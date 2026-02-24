"""Tests for app.utils pure functions."""

from datetime import datetime

import pytest


# ── parse_meeting_time ────────────────────────────────────────

class TestParseMeetingTime:
    @pytest.fixture(autouse=True)
    def _patch_now(self, monkeypatch):
        """Fix 'now' to 2026-02-20 12:00 so tests are deterministic."""
        from unittest.mock import patch
        from zoneinfo import ZoneInfo
        fixed = datetime(2026, 2, 20, 12, 0, tzinfo=ZoneInfo("Asia/Novosibirsk"))
        with patch("app.utils.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.strptime = datetime.strptime
            mock_dt.combine = datetime.combine
            mock_dt.min = datetime.min
            yield

    def test_colon_time(self):
        from app.utils import parse_meeting_time
        dt, err = parse_meeting_time("сделай встречу 16:00 27 февраля")
        assert err is None
        assert dt == datetime(2026, 2, 27, 16, 0)

    def test_compact_time(self):
        from app.utils import parse_meeting_time
        dt, err = parse_meeting_time("создай встречу 1630")
        assert err is None
        assert dt.hour == 16
        assert dt.minute == 30

    def test_numeric_date(self):
        from app.utils import parse_meeting_time
        dt, err = parse_meeting_time("сделай встречу 09:00 15.03")
        assert err is None
        assert dt.month == 3
        assert dt.day == 15

    def test_no_time_returns_error(self):
        from app.utils import parse_meeting_time
        dt, err = parse_meeting_time("сделай встречу завтра")
        assert dt is None
        assert err is not None

    def test_invalid_time(self):
        from app.utils import parse_meeting_time
        dt, err = parse_meeting_time("сделай встречу 25:00")
        assert dt is None
        assert "Некорректное время" in err


# ── merge_intervals ──────────────────────────────────────────

class TestMergeIntervals:
    def test_empty(self):
        from app.utils import merge_intervals
        assert merge_intervals([]) == []

    def test_no_overlap(self):
        from app.utils import merge_intervals
        a = (datetime(2026, 1, 1, 9, 0), datetime(2026, 1, 1, 10, 0))
        b = (datetime(2026, 1, 1, 11, 0), datetime(2026, 1, 1, 12, 0))
        assert merge_intervals([a, b]) == [a, b]

    def test_overlap(self):
        from app.utils import merge_intervals
        a = (datetime(2026, 1, 1, 9, 0), datetime(2026, 1, 1, 11, 0))
        b = (datetime(2026, 1, 1, 10, 0), datetime(2026, 1, 1, 12, 0))
        merged = merge_intervals([a, b])
        assert len(merged) == 1
        assert merged[0] == (datetime(2026, 1, 1, 9, 0), datetime(2026, 1, 1, 12, 0))

    def test_adjacent(self):
        from app.utils import merge_intervals
        a = (datetime(2026, 1, 1, 9, 0), datetime(2026, 1, 1, 10, 0))
        b = (datetime(2026, 1, 1, 10, 0), datetime(2026, 1, 1, 11, 0))
        merged = merge_intervals([a, b])
        assert len(merged) == 1

    def test_unsorted_input(self):
        from app.utils import merge_intervals
        a = (datetime(2026, 1, 1, 11, 0), datetime(2026, 1, 1, 12, 0))
        b = (datetime(2026, 1, 1, 9, 0), datetime(2026, 1, 1, 10, 0))
        merged = merge_intervals([b, a])
        assert len(merged) == 2
        assert merged[0][0] < merged[1][0]


# ── parse_attendees ──────────────────────────────────────────

class TestParseAttendees:
    def test_nicknames_only(self):
        from app.utils import parse_attendees
        nicks, emails = parse_attendees("встречу @alice @bob")
        assert nicks == ["alice", "bob"]
        assert emails == []

    def test_emails_only(self):
        from app.utils import parse_attendees
        nicks, emails = parse_attendees("встречу test@example.com")
        assert emails == ["test@example.com"]
        assert nicks == []

    def test_mixed(self):
        from app.utils import parse_attendees
        nicks, emails = parse_attendees("встречу @alice test@example.com @bob")
        assert "alice" in nicks
        assert "bob" in nicks
        assert "test@example.com" in emails

    def test_email_not_as_nick(self):
        from app.utils import parse_attendees
        nicks, emails = parse_attendees("user@domain.com")
        assert emails == ["user@domain.com"]
        # email's local part should not appear as a nick
        assert "domain" not in nicks


# ── strip_numbered_item ──────────────────────────────────────

class TestStripNumberedItem:
    def test_dot_prefix(self):
        from app.utils import strip_numbered_item
        assert strip_numbered_item("1. First item") == "First item"

    def test_paren_prefix(self):
        from app.utils import strip_numbered_item
        assert strip_numbered_item("2) Second item") == "Second item"

    def test_no_prefix(self):
        from app.utils import strip_numbered_item
        assert strip_numbered_item("No number here") == "No number here"



# ── md_to_telegram_html ──────────────────────────────────────

class TestMdToTelegramHtml:
    def test_bold(self):
        from app.utils import md_to_telegram_html
        assert "<b>text</b>" in md_to_telegram_html("**text**")

    def test_code_block(self):
        from app.utils import md_to_telegram_html
        result = md_to_telegram_html("```\ncode here\n```")
        assert "<pre>" in result
        assert "code here" in result

    def test_inline_code(self):
        from app.utils import md_to_telegram_html
        assert "<code>x</code>" in md_to_telegram_html("`x`")

    def test_header_becomes_bold(self):
        from app.utils import md_to_telegram_html
        result = md_to_telegram_html("## Title")
        assert "<b>" in result
        assert "Title" in result
