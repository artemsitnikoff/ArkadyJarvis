"""Download a meeting recording from a URL to a local file.

Supports:
- Yandex.Disk public links (`disk.yandex.ru/d/...`, `yadi.sk/d/...`) —
  resolved to a direct download URL via the public cloud-api.
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
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("arkadyjarvis")

YANDEX_DISK_API = "https://cloud-api.yandex.net/v1/disk/public/resources/download"
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024  # 1 GiB safety ceiling

YANDEX_DISK_HOSTS = {"disk.yandex.ru", "disk.yandex.com", "yadi.sk"}

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


def _is_blocked_address(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in _BLOCKED_NETS)


async def _assert_public_url(url: str) -> str:
    """Resolve the URL's host and reject internal / private addresses.

    Returns the sanitised host (for logging). Raises DownloadError on
    any blocked address, unresolvable host, or unsupported scheme.
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


async def download_meeting(url: str, dest: str | Path) -> int:
    """Download a recording to `dest`. Returns the final byte count.

    All URLs (original + Yandex-resolved + every redirect) are validated
    against the SSRF blocklist before any connection is made.
    """
    dest = Path(dest)
    await _assert_public_url(url)

    direct_url = await _resolve_yandex_disk(url) if _is_yandex_disk(url) else url
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
    stall the polling loop.
    """
    total = 0
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
