"""Step 4 tests — state persistence, rate-limit gate, backfill window, safe-mode.

The gate is tested purely with injected `now` (no network). This is the
IP-ban safety net (ADR-002).
"""

from __future__ import annotations

from datetime import date

import pytest

from src import state as st
from src.constants import MIN_INTERVAL_SEC, PENALTY_BOX_SEC
from src.errors import StateCorruptError


# --- gate (injected now, zero network) ---

def test_gate_allows_when_fresh():
    s = st.State()  # last_flex_call_ts=0, throttled=0
    assert st.gate_flex_call(s, now=1_000_000.0) is None


def test_gate_blocks_within_min_interval():
    s = st.State(last_flex_call_ts=1000.0)
    reason = st.gate_flex_call(s, now=1000.0 + MIN_INTERVAL_SEC - 1)
    assert reason and "min interval" in reason


def test_gate_allows_after_min_interval():
    s = st.State(last_flex_call_ts=1000.0)
    assert st.gate_flex_call(s, now=1000.0 + MIN_INTERVAL_SEC) is None


def test_gate_blocks_in_penalty_box():
    s = st.State(throttled_until_ts=5000.0)
    reason = st.gate_flex_call(s, now=4000.0)
    assert reason and "throttled" in reason


def test_gate_allows_after_penalty_box():
    s = st.State(throttled_until_ts=5000.0, last_flex_call_ts=0.0)
    assert st.gate_flex_call(s, now=5001.0) is None


def test_gate_clock_skew_negative_elapsed_blocks():
    # system clock jumped backward -> elapsed negative -> conservative block
    s = st.State(last_flex_call_ts=10_000.0)
    reason = st.gate_flex_call(s, now=9000.0)
    assert reason and "min interval" in reason


# --- backfill window ---

def test_window_first_run_last_30_days():
    s = st.State()  # no last_success
    w = st.compute_backfill_window(s, today=date(2026, 5, 20))
    assert w == (date(2026, 4, 20), date(2026, 5, 19))


def test_window_incremental():
    s = st.State(last_success_trade_date="2026-05-15")
    w = st.compute_backfill_window(s, today=date(2026, 5, 20))
    assert w == (date(2026, 5, 16), date(2026, 5, 19))


def test_window_empty_when_caught_up():
    s = st.State(last_success_trade_date="2026-05-19")
    assert st.compute_backfill_window(s, today=date(2026, 5, 20)) is None


def test_window_never_includes_today():
    s = st.State(last_success_trade_date="2026-05-18")
    w = st.compute_backfill_window(s, today=date(2026, 5, 20))
    assert w[1] == date(2026, 5, 19)  # yesterday, not today


# --- gap alert ---

def test_gap_alert_triggers():
    s = st.State(last_success_trade_date="2026-05-01")
    assert st.is_gap_alert(s, today=date(2026, 5, 20), threshold_days=7) is True


def test_gap_alert_quiet_when_recent():
    s = st.State(last_success_trade_date="2026-05-18")
    assert st.is_gap_alert(s, today=date(2026, 5, 20), threshold_days=7) is False


# --- mutations ---

def test_mark_throttled_sets_penalty_no_success_change():
    s = st.State(last_success_trade_date="2026-05-15")
    st.mark_throttled(s, now=1000.0)
    assert s.throttled_until_ts == 1000.0 + PENALTY_BOX_SEC
    assert s.last_success_trade_date == "2026-05-15"  # untouched
    assert s.last_error == "FLEX_THROTTLED_1018"


def test_mark_trade_success_advances_and_clears_error():
    s = st.State(last_error="boom", last_error_at="x")
    st.mark_trade_success(s, "2026-05-19", now=1000.0)
    assert s.last_success_trade_date == "2026-05-19"
    assert s.last_error is None


def test_mark_flex_call_success_only_sets_ts():
    s = st.State(last_success_trade_date="2026-05-10")
    st.mark_flex_call_success(s, now=2000.0)
    assert s.last_flex_call_ts == 2000.0
    assert s.last_success_trade_date == "2026-05-10"  # not advanced by flex-call alone


# --- persistence ---

def test_load_missing_returns_defaults(tmp_path):
    s = st.load_state(tmp_path / "nope.json")
    assert s == st.State()


def test_save_load_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    s = st.State(last_success_trade_date="2026-05-19", last_flex_call_ts=12345.0)
    st.save_state(s, p)
    assert st.load_state(p) == s


def test_save_creates_backup(tmp_path):
    p = tmp_path / "state.json"
    st.save_state(st.State(last_error="v1"), p)
    st.save_state(st.State(last_error="v2"), p)
    assert (tmp_path / "state.json.bak").exists()


def test_load_corrupt_raises(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(StateCorruptError):
        st.load_state(p)


def test_enter_safe_mode_backs_off(tmp_path):
    s = st.enter_safe_mode(now=1000.0)
    assert s.throttled_until_ts == 1000.0 + PENALTY_BOX_SEC
    assert st.gate_flex_call(s, now=1500.0) is not None  # blocked
