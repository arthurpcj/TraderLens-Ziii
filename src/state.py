"""state.json management + the Flex rate-limit gate (ADR-002).

The gate is the IP-ban safety net. It is a pure function of (state, now) so it
is exhaustively testable WITHOUT touching IBKR. ib_sync.run always calls it
before flex_client.download_statement.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .constants import MIN_INTERVAL_SEC, PENALTY_BOX_SEC
from .errors import StateCorruptError


@dataclass
class State:
    last_run_at: str | None = None
    last_success_trade_date: str | None = None   # YYYY-MM-DD; advanced by Activity only
    last_confirmation_date: str | None = None     # YYYY-MM-DD (ET); last day Confirmation captured
    last_flex_call_ts: float = 0.0                # epoch; only set on success
    throttled_until_ts: float = 0.0               # epoch; 0 = not throttled
    last_error: str | None = None
    last_error_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "State":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- persistence (atomic) ---

def load_state(path: str | Path) -> State:
    """Load state. Missing file -> defaults. Corrupt JSON -> StateCorruptError."""
    p = Path(path)
    if not p.exists():
        return State()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError) as exc:
        raise StateCorruptError(f"state.json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise StateCorruptError("state.json is not a JSON object")
    return State.from_dict(data)


def save_state(state: State, path: str | Path) -> None:
    """Atomic write: temp file + os.replace; keep a .bak of the prior file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
    if p.exists():
        bak = p.with_suffix(p.suffix + ".bak")
        try:
            bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass  # backup is best-effort, never block the save
    os.replace(tmp, p)


# --- rate-limit gate (ADR-002) ---

def gate_flex_call(state: State, now: float) -> str | None:
    """Return None if OK to call Flex, else a human reason why it's blocked.

    Gate 1: still inside the 30-min penalty box after a 1018 throttle.
    Gate 2: less than MIN_INTERVAL_SEC (10 min) since the last successful call.
    Clock skew (negative elapsed) is treated as 'interval not met' (conservative).
    """
    if now < state.throttled_until_ts:
        remaining_min = (state.throttled_until_ts - now) / 60
        return f"throttled, {remaining_min:.1f} min remaining"
    elapsed = now - state.last_flex_call_ts
    if elapsed < MIN_INTERVAL_SEC:
        return f"min interval not met, {MIN_INTERVAL_SEC - elapsed:.0f}s remaining"
    return None


# --- backfill window (FR-STATE-3, NFR-RELIABILITY-3) ---

def compute_backfill_window(state: State, today: date) -> tuple[date, date] | None:
    """[from, to] where to = yesterday (never today). None if empty.

    First run (no last_success): default to last 30 days (Flex coverage).
    """
    to_date = today - timedelta(days=1)  # never fetch today (settlement delay)
    if state.last_success_trade_date:
        last = date.fromisoformat(state.last_success_trade_date)
        from_date = last + timedelta(days=1)
    else:
        from_date = today - timedelta(days=30)
    if from_date > to_date:
        return None
    return from_date, to_date


# --- gap detection (NFR-RELIABILITY-4) ---

def gap_days(state: State, today: date) -> int | None:
    """Calendar days since last success (approx; we dropped the market calendar)."""
    if not state.last_success_trade_date:
        return None
    last = date.fromisoformat(state.last_success_trade_date)
    return (today - last).days


def is_gap_alert(state: State, today: date, threshold_days: int) -> bool:
    g = gap_days(state, today)
    return g is not None and g > threshold_days


# --- state mutations (caller saves) ---

def mark_flex_call_success(state: State, now: float) -> None:
    """Record a successful Flex call timestamp (enforces the 10-min interval)."""
    state.last_flex_call_ts = now
    state.last_run_at = _utc_now_iso()


def mark_trade_success(state: State, last_trade_date: str, now: float) -> None:
    """Only this advances last_success_trade_date (FR-STATE-2). Clears error."""
    state.last_success_trade_date = last_trade_date
    state.last_error = None
    state.last_error_at = None
    state.last_run_at = _utc_now_iso()


def mark_confirmation_success(state: State, confirmation_date: str, now: float) -> None:
    """Record that today's (ET) Trade Confirmation was captured. Does NOT touch
    last_success_trade_date (that is Activity's, the T+1 authoritative source)."""
    state.last_confirmation_date = confirmation_date
    state.last_error = None
    state.last_error_at = None
    state.last_run_at = _utc_now_iso()


def mark_throttled(state: State, now: float) -> None:
    """1018/429 hit: enter 30-min penalty box. Do NOT touch last_success."""
    state.throttled_until_ts = now + PENALTY_BOX_SEC
    state.last_error = "FLEX_THROTTLED_1018"
    state.last_error_at = _utc_now_iso()
    state.last_run_at = _utc_now_iso()


def mark_error(state: State, message: str) -> None:
    """Record a non-throttle failure. Does NOT touch last_success_trade_date."""
    state.last_error = message
    state.last_error_at = _utc_now_iso()
    state.last_run_at = _utc_now_iso()


def enter_safe_mode(now: float) -> State:
    """Fresh state after corruption: back off a full penalty box before any call."""
    s = State()
    s.throttled_until_ts = now + PENALTY_BOX_SEC
    s.last_error = "STATE_CORRUPT_SAFE_MODE"
    s.last_error_at = _utc_now_iso()
    return s
