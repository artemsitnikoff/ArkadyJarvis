"""Tests for app.services.meeting_downloader URL resolution."""

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from app.services.meeting_downloader import (
    DownloadError,
    _extract_gdrive_id,
    _is_google_drive,
    _is_yandex_disk,
    _resolve_google_drive,
    download_meeting,
)


class TestIsGoogleDrive:
    def test_file_view_url(self):
        assert _is_google_drive(
            "https://drive.google.com/file/d/ABC123/view"
        )

    def test_open_url(self):
        assert _is_google_drive("https://drive.google.com/open?id=ABC123")

    def test_docs_host(self):
        assert _is_google_drive("https://docs.google.com/uc?id=ABC123")

    def test_other_host(self):
        assert not _is_google_drive("https://example.com/file/d/ABC/view")

    def test_yandex_not_google(self):
        url = "https://disk.yandex.ru/d/abc"
        assert _is_yandex_disk(url)
        assert not _is_google_drive(url)


class TestExtractGdriveId:
    def test_file_d_view(self):
        assert (
            _extract_gdrive_id(
                "https://drive.google.com/file/d/1NYQWyONMpndTC9GxHvq3XodjxjYq0kCf/view"
            )
            == "1NYQWyONMpndTC9GxHvq3XodjxjYq0kCf"
        )

    def test_file_d_view_with_query(self):
        assert (
            _extract_gdrive_id(
                "https://drive.google.com/file/d/ABC_123-x/view?usp=sharing"
            )
            == "ABC_123-x"
        )

    def test_open_id_query(self):
        assert (
            _extract_gdrive_id("https://drive.google.com/open?id=XYZ999")
            == "XYZ999"
        )

    def test_uc_id_query(self):
        assert (
            _extract_gdrive_id(
                "https://drive.google.com/uc?export=download&id=QWE-456"
            )
            == "QWE-456"
        )

    def test_no_id_returns_none(self):
        assert _extract_gdrive_id("https://drive.google.com/") is None

    def test_query_id_with_garbage_rejected(self):
        # Path-traversal-style payload in ?id= must not slip into the
        # outgoing URL — boundary validation at the resolver.
        assert (
            _extract_gdrive_id("https://drive.google.com/uc?id=../../etc/passwd")
            is None
        )

    def test_query_id_with_space_rejected(self):
        assert (
            _extract_gdrive_id("https://drive.google.com/uc?id=ABC%20XYZ")
            is None
        )


class TestResolveGoogleDrive:
    def test_rewrites_to_usercontent(self):
        out = _resolve_google_drive(
            "https://drive.google.com/file/d/1NYQWyONMpndTC9GxHvq3XodjxjYq0kCf/view"
        )
        assert out == (
            "https://drive.usercontent.google.com/download"
            "?id=1NYQWyONMpndTC9GxHvq3XodjxjYq0kCf&export=download&confirm=t"
        )

    def test_unparseable_raises(self):
        with pytest.raises(DownloadError):
            _resolve_google_drive("https://drive.google.com/some/random/path")


# ── download_meeting integration: HTML guard ──────────────────


def _patched_client_factory(handler):
    """Build an httpx.AsyncClient subclass that uses a MockTransport.

    Plugged in via `patch(..., new=...)` so `download_meeting`'s
    `async with httpx.AsyncClient(...)` picks up our fake transport
    instead of opening a real socket.
    """
    transport = httpx.MockTransport(handler)

    class _PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    return _PatchedClient


@pytest.mark.asyncio
async def test_download_meeting_aborts_on_html_response(tmp_path):
    """Drive sometimes returns an HTML 'request access' / warning page
    with 200 OK. Without the content-type guard the body would be
    streamed to disk and ffmpeg would emit a confusing 'Invalid data'
    error. The guard converts this into a readable DownloadError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html>access denied</html>",
        )

    dest = tmp_path / "out.bin"

    async def _public(_url: str) -> str:
        return "fake-host"

    with patch(
        "app.services.meeting_downloader._assert_public_url", side_effect=_public,
    ), patch(
        "app.services.meeting_downloader.httpx.AsyncClient",
        new=_patched_client_factory(handler),
    ):
        with pytest.raises(DownloadError, match="HTML"):
            await download_meeting("https://example.com/file.mp4", dest)

    # Partial file must be cleaned up.
    assert not dest.exists()
