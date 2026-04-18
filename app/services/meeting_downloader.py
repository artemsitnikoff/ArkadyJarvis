"""Download a meeting recording from a URL to a local file.

Supports:
- Yandex.Disk public links (`disk.yandex.ru/d/...`, `yadi.sk/d/...`) —
  resolved to a direct download URL via the public cloud-api.
- Any direct HTTP(S) URL (e.g. S3 pre-signed links, Telemost exports
  exposed as direct downloads).

Telemost's own recording URL is typically served via Yandex.Disk, so
the Yandex.Disk branch covers the common case.
"""

import logging
from pathlib import Path

import httpx

logger = logging.getLogger("arkadyjarvis")

YANDEX_DISK_API = "https://cloud-api.yandex.net/v1/disk/public/resources/download"
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024  # 1 GiB safety ceiling

YANDEX_DISK_HOSTS = {"disk.yandex.ru", "disk.yandex.com", "yadi.sk"}


class DownloadError(RuntimeError):
    pass


def _is_yandex_disk(url: str) -> bool:
    return any(host in url for host in YANDEX_DISK_HOSTS)


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

    Streams the response so we don't hold 500 MB in RAM.
    """
    dest = Path(dest)

    direct_url = await _resolve_yandex_disk(url) if _is_yandex_disk(url) else url

    logger.info("download_meeting: start %s -> %s", direct_url[:80], dest)
    bytes_written = 0
    # Long timeout — some of these downloads are hundreds of MB. No read timeout.
    timeout = httpx.Timeout(connect=20.0, read=None, write=60.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", direct_url) as resp:
            if resp.status_code >= 400:
                raise DownloadError(
                    f"Download failed {resp.status_code}: {direct_url[:120]}"
                )
            with open(dest, "wb") as fh:
                async for chunk in resp.aiter_bytes(chunk_size=1024 * 256):
                    fh.write(chunk)
                    bytes_written += len(chunk)
                    if bytes_written > MAX_DOWNLOAD_BYTES:
                        raise DownloadError(
                            f"Download exceeded safety ceiling "
                            f"({MAX_DOWNLOAD_BYTES // 1024 // 1024} MiB)"
                        )
    logger.info("download_meeting: wrote %d bytes to %s", bytes_written, dest)
    return bytes_written
