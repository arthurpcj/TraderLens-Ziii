"""F1 — trade-date must be US/Eastern, never the laptop's local date."""

from __future__ import annotations

from datetime import datetime, timezone

from src.ib_sync import today_et


def test_china_midnight_maps_to_prior_et_day():
    # 2026-05-19 16:30 UTC == 2026-05-20 00:30 CST (China) == 2026-05-19 12:30 ET
    # Local(China) date would be 05-20 (WRONG); ET date is 05-19 (correct).
    u = datetime(2026, 5, 19, 16, 30, tzinfo=timezone.utc)
    assert today_et(u) == datetime(2026, 5, 19).date()


def test_et_noon_same_day():
    # 2026-05-20 16:00 UTC == 12:00 ET
    u = datetime(2026, 5, 20, 16, 0, tzinfo=timezone.utc)
    assert today_et(u) == datetime(2026, 5, 20).date()


def test_utc_early_morning_is_prior_et_day():
    # 2026-05-20 02:00 UTC == 2026-05-19 22:00 ET (prior day)
    u = datetime(2026, 5, 20, 2, 0, tzinfo=timezone.utc)
    assert today_et(u) == datetime(2026, 5, 19).date()
