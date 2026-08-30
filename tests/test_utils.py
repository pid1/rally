"""Tests for static_version, timezone helpers, and the UNSET schema sentinel."""

import hashlib
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import rally.utils.timezone as tz
from rally.schemas import UNSET, TodoUpdate
from rally.utils import static_version

# --- static_version ------------------------------------------------------------


def test_compute_version_missing_file_returns_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(static_version, "STATIC_DIR", tmp_path)
    assert static_version._compute_version() == "0"


def test_compute_version_hashes_stylesheet(tmp_path, monkeypatch):
    (tmp_path / "styles.css").write_bytes(b"body{color:red}")
    monkeypatch.setattr(static_version, "STATIC_DIR", tmp_path)

    expected = hashlib.md5(b"body{color:red}").hexdigest()[:12]
    assert static_version._compute_version() == expected


# --- timezone ------------------------------------------------------------------


def test_ensure_utc_treats_naive_as_utc():
    assert tz.ensure_utc(datetime(2026, 1, 1, 12, 0)) == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_ensure_utc_converts_aware_to_utc():
    aware = datetime(2026, 1, 1, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))  # +05:30
    assert tz.ensure_utc(aware) == datetime(2026, 1, 1, 6, 30, tzinfo=UTC)


def test_now_utc_and_today_utc_follow_frozen_clock(frozen_now):
    instant = frozen_now(datetime(2026, 3, 15, 9, 30, tzinfo=UTC))
    assert tz.now_utc() == instant
    assert tz.today_utc() == date(2026, 3, 15)


def test_today_local_uses_configured_timezone(frozen_now):
    frozen_now(datetime(2026, 3, 15, 2, 0, tzinfo=UTC))
    # 02:00Z is next-day-morning in Kolkata but still the previous evening on the US west coast.
    assert tz.today_local("Asia/Kolkata") == date(2026, 3, 15)
    assert tz.today_local("America/Los_Angeles") == date(2026, 3, 14)


# --- UNSET sentinel ------------------------------------------------------------


def test_unset_distinguishes_omitted_from_explicit_null():
    assert TodoUpdate().due_date is UNSET
    assert TodoUpdate(due_date=None).due_date is None
    assert TodoUpdate(due_date="2026-01-01").due_date == "2026-01-01"
