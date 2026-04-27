"""Download a meeting recording from a URL to a local file.

Supports:
- Yandex.Disk public links (`disk.yandex.ru/d/...`, `yadi.sk/d/...`) —
  resolved to a direct download URL via the public cloud-api.
- Google Drive share links (`drive.google.com/file/d/{ID}/view`,
  `drive.google.com/open?id={ID}`, `drive.google.com/uc?id={ID}`) —
  rewritten to the `drive.usercontent.google.com/download` endpoint
  with `confirm=t` so the virus-scan warning page is bypassed for
  publicly shared files.
- Any direct HTTP(S) URL (e.g. S3 pre-signed links, Telemost exports
  exposed as direct downloads).

Telemost's own recording URL is typically served via Yandex.Disk, so
the Yandex.Disk branch covers the common case.

Security: every URL we touch — including redirect targets — is
validated against a private-IP blocklist to prevent SSRF into the
bot's Tailscale / internal network.
"""

import asyncio
import ipaddress
import logging
import re
import socket
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

logger = logging.getLogger("arkadyjarvis")

YANDEX_DISK_API = "https://cloud-api.yandex.net/v1/disk/public/resources/download"
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024  # 1 GiB safety ceiling

YANDEX_DISK_HOSTS = {"disk.yandex.ru", "disk.yandex.com", "yadi.sk"}
GOOGLE_DRIVE_HOSTS = {"drive.google.com", "docs.google.com"}

# Drive file IDs are base64url-style. Same charset is used to validate
# `?id=...` query values too — the URL goes back into our request, so we
# sanitise it at the boundary instead of trusting whatever shape the user
# pasted.
_GDRIVE_ID_CHARSET = re.compile(r"^[A-Za-z0-9_-]+$")
# `/file/d/{ID}/...` — most common share URL shape. Intentionally NOT
# anchored at start: also matches domain-aware links like
# `/a/{domain}/file/d/{ID}/view`.
_GDRIVE_FILE_PATH_RE = re.compile(r"/file/d/([A-Za-z0-9_-]+)")

# Anything resolving into these ranges is considered internal and blocked.
# Covers loopback, link-local, RFC1918, Tailscale CGNAT, IPv6 unique-local & link-local.
_BLOCKED_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local
    ipaddress.ip_network("100.64.0.0/10"),    # CGNAT (Tailscale)
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("0.0.0.0/8"),
]


class DownloadError(RuntimeError):
    pass


def _is_yandex_disk(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host.lower() in YANDEX_DISK_HOSTS


def _is_google_drive(url: str) -> bool:
    # We check only the user-facing share hosts. The post-resolve host
    # `drive.usercontent.google.com` is intentionally NOT here — we only
    # ever reach it after `_resolve_google_drive` has already rewritten
    # the URL, so re-detecting it would loop the resolver.
    host = (urlparse(url).hostname or "").lower()
    return host in GOOGLE_DRIVE_HOSTS


def _extract_gdrive_id(url: str) -> str | None:
    """Pull the Drive file ID out of common share-URL shapes.

    Returns None for unrecognised shapes or values that don't match the
    base64url charset — better to fail loudly upstream than to splice
    arbitrary user input into an outgoing URL.
    """
    parsed = urlparse(url)
    m = _GDRIVE_FILE_PATH_RE.search(parsed.path)
    if m:
        return m.group(1)
    qs = parse_qs(parsed.query)
    val = qs.get("id")
    if val and val[0] and _GDRIVE_ID_CHARSET.match(val[0]):
        return val[0]
    return None


def _is_blocked_address(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    # Normalise IPv4-mapped IPv6 (`::ffff:10.0.0.1`) so it's tested against
    # the IPv4 blocklist instead of slipping through as "public IPv6".
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if any(ip in net for net in _BLOCKED_NETS):
        return True
    # Belt + braces: stdlib flags catch extra reserved / multicast /
    # unspecified ranges that aren't in the explicit blocklist.
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def _assert_public_url(url: str) -> str:
    """Resolve the URL's host and reject internal / private addresses.

    Returns the sanitised host (for logging). Raises DownloadError on
    any blocked address, unresolvable host, or unsupported scheme.

    We intentionally do NOT cache getaddrinfo results here: a DNS
    rebinding attacker could poison the cache with a public address
    at validation time and serve a private one at connect time. Each
    call gets a fresh resolution.

    Note: there is still a small TOCTOU window between this function
    and httpx's own getaddrinfo inside `client.stream`. Full mitigation
    would require a pinned-IP transport (custom httpcore connection
    pool). Compensating controls for the current deployment:
    Bitrix-tied /start auth (only authorised users can reach this
    code) + internal Tailscale-only infra. Revisit before opening
    the bot to untrusted users.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise DownloadError(f"Неподдерживаемая схема URL: {parsed.scheme or 'none'}")
    host = parsed.hostname
    if not host:
        raise DownloadError("URL без host")

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise DownloadError(f"Не смог резолвить host {host}: {e}") from e

    for _, _, _, _, sockaddr in infos:
        addr = sockaddr[0]
        if _is_blocked_address(addr):
            raise DownloadError(
                f"Host {host} указывает на приватный адрес {addr} — запрещено"
            )
    return host


async def _resolve_yandex_disk(public_url: str) -> str:
    """Resolve a public Yandex.Disk URL to a direct download URL."""
    # Re-validate the API endpoint itself — defence in depth even though the
    # constant is ours. Catches a future typo / DNS poisoning of cloud-api.
    await _assert_public_url(YANDEX_DISK_API)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(YANDEX_DISK_API, params={"public_key": public_url})
        if resp.status_code >= 400:
            raise DownloadError(
                f"Yandex.Disk resolve {resp.status_code}: {resp.text[:200]}"
            )
        href = resp.json().get("href")
        if not href:
            raise DownloadError("Yandex.Disk returned no download href")
        return href


def _resolve_google_drive(public_url: str) -> str:
    """Rewrite a Google Drive share URL into a direct-download URL.

    Uses `drive.usercontent.google.com/download?...&confirm=t` — the
    modern endpoint that bypasses the virus-scan warning HTML page for
    publicly accessible files. The host is later re-validated by the
    SSRF guard like every other URL.
    """
    file_id = _extract_gdrive_id(public_url)
    if not file_id:
        raise DownloadError(
            "Не смог извлечь Google Drive file ID из URL. "
            "Поддерживаются ссылки вида drive.google.com/file/d/{ID}/view, "
            "drive.google.com/open?id={ID}, drive.google.com/uc?id={ID}."
        )
    return (
        f"https://drive.usercontent.google.com/download"
        f"?id={file_id}&export=download&confirm=t"
    )


async def download_meeting(url: str, dest: str | Path) -> int:
    """Download a recording to `dest`. Returns the final byte count.

    All URLs (original + Yandex-resolved + every redirect) are validated
    against the SSRF blocklist before any connection is made.
    """
    dest = Path(dest)
    await _assert_public_url(url)

    if _is_yandex_disk(url):
        direct_url = await _resolve_yandex_disk(url)
    elif _is_google_drive(url):
        direct_url = _resolve_google_drive(url)
    else:
        direct_url = url
    await _assert_public_url(direct_url)

    # Log only the host — pre-signed URLs carry secrets in the query string.
    log_host = urlparse(direct_url).hostname or "?"
    logger.info("download_meeting: start host=%s -> %s", log_host, dest)

    bytes_written = 0
    # Long timeout — some of these downloads are hundreds of MB. No read timeout.
    timeout = httpx.Timeout(connect=20.0, read=None, write=60.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        current_url = direct_url
        for _hop in range(5):  # at most 5 redirect hops
            await _assert_public_url(current_url)
            async with client.stream("GET", current_url) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location")
                    if not location:
                        raise DownloadError("Redirect без Location header")
                    current_url = str(resp.url.join(location))
                    continue
                if resp.status_code >= 400:
                    raise DownloadError(
                        f"Download failed {resp.status_code} на host={log_host}"
                    )
                # Sanity check: if we ended up with an HTML page (Drive
                # warning page, viewer page, "access denied" stub), fail
                # fast with a readable error instead of feeding garbage
                # to ffmpeg.
                content_type = (resp.headers.get("content-type") or "").lower()
                if content_type.startswith(("text/html", "application/xhtml")):
                    raise DownloadError(
                        "Сервер вернул HTML-страницу вместо файла. "
                        "Проверь, что ссылка ведёт на сам файл и доступ "
                        "открыт по ссылке (а не «по запросу»)."
                    )
                # Optional pre-check on declared size.
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > MAX_DOWNLOAD_BYTES:
                    raise DownloadError(
                        f"Файл слишком большой: {int(declared) // 1024 // 1024} МБ "
                        f"(потолок {MAX_DOWNLOAD_BYTES // 1024 // 1024} МБ)"
                    )
                # Stream the body to disk off-loop.
                bytes_written = await _stream_to_file(resp, dest)
                break
        else:
            raise DownloadError("Слишком много редиректов (>5)")

    logger.info("download_meeting: wrote %d bytes to %s", bytes_written, dest)
    return bytes_written


async def _stream_to_file(resp: httpx.Response, dest: Path) -> int:
    """Write the streamed response body to `dest` without blocking the event loop.

    Uses `asyncio.to_thread` for each write so a 500 MB download doesn't
    stall the polling loop. On any failure the partial file is removed
    so we never leave junk behind if the caller doesn't run its own
    cleanup.
    """
    total = 0
    try:
        with open(dest, "wb") as fh:
            async for chunk in resp.aiter_bytes(chunk_size=1024 * 256):
                await asyncio.to_thread(fh.write, chunk)
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise DownloadError(
                        f"Download exceeded safety ceiling "
                        f"({MAX_DOWNLOAD_BYTES // 1024 // 1024} МБ)"
                    )
        return total
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
