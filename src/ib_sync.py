"""Main orchestrator — Flex -> SQLite -> auto-export csv (FR-ENTRY).

Entry: python -m src.ib_sync

Flow is rate-limit-safe (ADR-002): the gate runs before any Flex call, 1018
backs off without retry, and last_success_trade_date advances only on full
success. `now`/`today`/`download_fn` are injected so the whole pipeline is
integration-testable offline.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from . import exporter, flex_client, sqlite_store
from . import state as state_mod
from .constants import (
    ENV_PATH,
    ET_TZ,
    EXPORT_DIR,
    GAP_THRESHOLD_DAYS,
    LOGS_DIR,
    RC_HARD,
    RC_OK,
    RC_RETRYABLE,
    SQLITE_PATH,
    STATE_PATH,
    TARGET_UNDERLYINGS,
)
from .errors import FlexAuthError, FlexThrottledError, StateCorruptError
from .parser import CONFIRMATION_PROFILE, parse_trades

log = logging.getLogger("tradelens.ib_sync")


def load_env_file(path: Path) -> dict[str, str]:
    """Parse a flat .env (lifted from spike 001). No python-dotenv dependency."""
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def get_credentials(env_path: Path = ENV_PATH) -> tuple[str, str]:
    """Read IBKR_FLEX_TOKEN + IBKR_FLEX_QUERY_ID from .env or environment."""
    env = load_env_file(env_path)
    token = env.get("IBKR_FLEX_TOKEN") or os.environ.get("IBKR_FLEX_TOKEN")
    query = env.get("IBKR_FLEX_QUERY_ID") or os.environ.get("IBKR_FLEX_QUERY_ID")
    if not token or not query:
        raise SystemExit(
            "Missing IBKR_FLEX_TOKEN / IBKR_FLEX_QUERY_ID. "
            f"Edit {env_path} (see .env.example)."
        )
    return token, query


def get_confirmation_credentials(env_path: Path = ENV_PATH) -> tuple[str, str]:
    """Read IBKR_FLEX_TOKEN + IBKR_FLEX_QUERY_ID_CONFIRMATION (same token, TCF query)."""
    env = load_env_file(env_path)
    token = env.get("IBKR_FLEX_TOKEN") or os.environ.get("IBKR_FLEX_TOKEN")
    query = (
        env.get("IBKR_FLEX_QUERY_ID_CONFIRMATION")
        or os.environ.get("IBKR_FLEX_QUERY_ID_CONFIRMATION")
    )
    if not token or not query:
        raise SystemExit(
            "Missing IBKR_FLEX_TOKEN / IBKR_FLEX_QUERY_ID_CONFIRMATION. "
            f"Edit {env_path} (see .env.example)."
        )
    return token, query


class _SummaryHandler(logging.Handler):
    """Collects WARNING+ records emitted anywhere under the 'tradelens' logger
    during a run, so we can print a consolidated summary at the end."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if record.levelno >= logging.ERROR:
            self.errors.append(msg)
        else:
            self.warnings.append(msg)


def _log_run_summary(summary: _SummaryHandler, rc: int, elapsed: float) -> None:
    """Emit the end-of-run summary. Handler is already detached, so these lines
    are not re-collected."""
    label = {RC_OK: "OK", RC_RETRYABLE: "RETRYABLE", RC_HARD: "HARD"}.get(rc, f"FAIL({rc})")
    log.info("===== RUN SUMMARY =====")
    log.info("result: %s (rc=%d) | elapsed %.1fs", label, rc, elapsed)
    log.info("warnings: %d | errors: %d", len(summary.warnings), len(summary.errors))
    for w in summary.warnings:
        log.info("  WARN: %s", w)
    for e in summary.errors:
        log.info("  ERROR: %s", e)
    if not summary.warnings and not summary.errors:
        log.info("  (clean — no warnings or errors)")
    log.info("=======================")


def run(
    *,
    now: float,
    today: date,
    token: str,
    query_id: str,
    db_path: str | Path = SQLITE_PATH,
    state_path: str | Path = STATE_PATH,
    export_dir: Path = EXPORT_DIR,
    underlyings: tuple[str, ...] = TARGET_UNDERLYINGS,
    download_fn=flex_client.download_statement,
    mode: str = "activity",
    confirmation_query_id: str | None = None,
) -> int:
    """Run the pipeline once, with an end-of-run warning/error summary.

    `mode`:
      - "activity"     T+1 Flex Activity -> SQLite -> MTS csv (backup/reconcile)
      - "confirmation" same-day Trade Confirmation -> SQLite + MTS csv (primary)
      - "auto"         pick by state+time: today's Confirmation not yet captured
                       and past NY close -> confirmation; else activity
    Both share the global Flex gate.

    Returns process exit code (0/2/3, see constants.RC_*).
    """
    summary = _SummaryHandler()
    tl_logger = logging.getLogger("tradelens")
    tl_logger.addHandler(summary)
    started = time.time()
    rc = RC_HARD
    try:
        if mode == "auto":
            mode = _resolve_auto_mode_safe(state_path, now, today)
            reason = " (NY weekend, market closed)" if (mode == "skip" and today.weekday() >= 5) else ""
            log.info("[..] auto-mode resolved to: %s%s", mode, reason)
        if mode == "skip":
            if today.weekday() >= 5:
                log.info("[OK] auto: NY %s, market closed, skip", today.strftime("%A"))
            else:
                log.info("[OK] auto: nothing due at this NY hour (Confirmation in / pre-slot), skip")
            rc = RC_OK
        elif mode == "confirmation":
            rc = _run_confirmation_pipeline(
                now=now, today=today, token=token,
                query_id=confirmation_query_id or query_id,
                db_path=db_path, state_path=state_path,
                export_dir=export_dir, underlyings=underlyings, download_fn=download_fn,
            )
        else:
            rc = _run_pipeline(
                now=now, today=today, token=token, query_id=query_id,
                db_path=db_path, state_path=state_path, export_dir=export_dir,
                underlyings=underlyings, download_fn=download_fn,
            )
    except Exception as exc:  # last-resort guard so summary always prints
        log.exception("[FAIL] unexpected error: %s", exc)
        rc = RC_HARD  # unexpected -> needs investigation (code/config)
    finally:
        tl_logger.removeHandler(summary)  # detach BEFORE summary (avoid re-collect)
        _log_run_summary(summary, rc, time.time() - started)
    return rc


def _run_pipeline(
    *,
    now: float,
    today: date,
    token: str,
    query_id: str,
    db_path: str | Path = SQLITE_PATH,
    state_path: str | Path = STATE_PATH,
    export_dir: Path = EXPORT_DIR,
    underlyings: tuple[str, ...] = TARGET_UNDERLYINGS,
    download_fn=flex_client.download_statement,
) -> int:
    """The actual pipeline. Returns exit code; all logging flows to the summary."""
    # 1. Load state (corrupt -> safe-mode back-off, not a hard failure)
    try:
        state = state_mod.load_state(state_path)
    except StateCorruptError as exc:
        log.error("[FAIL] state.json corrupt (%s) -> safe-mode back-off 30 min", exc)
        state = state_mod.enter_safe_mode(now)
        state_mod.save_state(state, state_path)
        return RC_OK

    # 2. Backfill window (never includes today)
    window = state_mod.compute_backfill_window(state, today)
    if window is None:
        log.info("[OK] no new trade days, skip")
        state.last_run_at = datetime.now(timezone.utc).isoformat()
        state_mod.save_state(state, state_path)
        return RC_OK
    from_date, to_date = window
    log.info("[..] run start: backfill window %s .. %s", from_date, to_date)

    # 3. Rate-limit gate (ADR-002) — before any Flex call
    reason = state_mod.gate_flex_call(state, now)
    if reason is not None:
        log.info("[OK] skip Flex call: %s", reason)
        state.last_run_at = datetime.now(timezone.utc).isoformat()
        state_mod.save_state(state, state_path)
        return RC_OK

    # 4. Download (typed error handling; never retry a throttle)
    log.info("[..] downloading Flex statement (query_id=%s)...", query_id)
    try:
        xml_bytes = download_fn(token, query_id)
    except FlexThrottledError:
        log.warning("[WARN] Flex throttled (1018/429) -> back off 30 min, no retry")
        state_mod.mark_throttled(state, now)
        state_mod.save_state(state, state_path)
        return RC_RETRYABLE  # transient: penalty box clears, next trigger retries
    except FlexAuthError as exc:
        log.error("[FAIL] Flex auth error (%s) -> renew token", exc)
        _log_staleness(state, today)  # token expiry -> multi-day outage; show how stale
        state_mod.mark_error(state, f"AUTH:{exc.code}")
        state_mod.save_state(state, state_path)
        return RC_HARD  # token/account: retry won't help, user must act
    except Exception as exc:  # server-busy exhausted / network / malformed
        log.error("[FAIL] Flex fetch failed: %s", exc)
        _log_staleness(state, today)
        state_mod.mark_error(state, str(exc))
        state_mod.save_state(state, state_path)
        return RC_RETRYABLE  # transient fetch failure -> next trigger retries

    # 5. Record successful call timestamp (enforces 10-min interval)
    state_mod.mark_flex_call_success(state, now)
    state_mod.save_state(state, state_path)

    # 6-7. Parse ALL trades + idempotent upsert (full archive)
    run_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = parse_trades(xml_bytes, run_id=run_id, now_utc=now_iso)
    conn = sqlite_store.connect(db_path)
    try:
        sqlite_store.init_schema(conn)
        stats = sqlite_store.upsert_trades(conn, rows)
        log.info(
            "[OK] fetched %d trades: %d new, %d healed (order_id/fifo/etc backfill), %d dupes",
            stats.attempted, stats.inserted, stats.healed, stats.ignored_dupes,
        )

        # 8. Advance last_success (FR-STATE-2) only after a clean fetch+store
        state_mod.mark_trade_success(state, to_date.isoformat(), now)
        state_mod.save_state(state, state_path)

        # 9. Auto-export each date in window that has target futures
        dates = sqlite_store.distinct_export_dates(
            conn, from_date.isoformat(), to_date.isoformat(), underlyings
        )
        for d in dates:
            es = exporter.export_date(conn, d, export_dir, underlyings)
            log.info("[OK] %s", es.summary())
        if not dates:
            log.info("[OK] no target futures in window to export")
    finally:
        conn.close()

    # 10. Gap alert (NFR-RELIABILITY-4)
    if state_mod.is_gap_alert(state, today, GAP_THRESHOLD_DAYS):
        g = state_mod.gap_days(state, today)
        log.warning("[WARN] gap=%s days since last success (>%d)", g, GAP_THRESHOLD_DAYS)

    return RC_OK


def _resolve_auto_mode(state: state_mod.State, now: float, today: date) -> str:
    """Pick the mode for `--mode auto`, by NY weekday + state + time-of-day.

    Confirmation is the primary same-day feed; Activity is the time-anchored
    backup/reconcile. Returns "confirmation", "activity", or "skip":
      - NY weekend (Sat=5 / Sun=6): -> skip (market closed, fetching only burns
        Flex quota for 0 rows; see DATA_ARCHITECTURE / 2026-05-26 dev log)
      - past NY close (ET hour >= 16) and today's Confirmation NOT yet captured
        -> confirmation (primary; fires even on a late boot — it's a today-only
        query for the same NY day)
      - at the Activity slot (ET hour >= 20) AND Confirmation already captured
        -> activity (Activity NEVER runs before its own slot, even if there is
        nothing else to do — per user; its data is identical whenever it runs)
      - otherwise (pre-close, or Confirmation done but before the Activity slot)
        -> skip: do nothing, no Flex call
    """
    if today.weekday() >= 5:                                  # Sat=5, Sun=6
        return "skip"
    et_hour = datetime.fromtimestamp(now, tz=timezone.utc).astimezone(ET_TZ).hour
    confirmation_done = state.last_confirmation_date == today.isoformat()
    if et_hour >= 16 and not confirmation_done:
        return "confirmation"
    if et_hour >= 20 and confirmation_done:
        return "activity"
    return "skip"


def _resolve_auto_mode_safe(state_path: str | Path, now: float, today: date) -> str:
    """_resolve_auto_mode but tolerant of a corrupt state file (-> activity, the
    pipeline then detects corruption and enters safe-mode)."""
    try:
        st = state_mod.load_state(state_path)
    except StateCorruptError:
        return "activity"
    return _resolve_auto_mode(st, now, today)


def _run_confirmation_pipeline(
    *,
    now: float,
    today: date,
    token: str,
    query_id: str,
    db_path: str | Path = SQLITE_PATH,
    state_path: str | Path = STATE_PATH,
    export_dir: Path = EXPORT_DIR,
    underlyings: tuple[str, ...] = TARGET_UNDERLYINGS,
    download_fn=flex_client.download_statement,
) -> int:
    """Same-day Trade Confirmation (TCF) ingest -> SQLite + same-day csv export.

    Confirmation is the PRIMARY same-day MTS feed (design updated 2026-05-22 per
    user: was "Activity feeds MTS"; now Confirmation feeds same-day, Activity is
    the T+1 backup/reconcile). It is a today-only query, so:
      - if today's Confirmation was already captured (e.g. a manual early pull),
        SKIP the Flex call entirely — no redundant request, rate-limit-safe
      - no backfill window, does NOT advance last_success_trade_date
      - INSERT OR IGNORE: preliminary rows; next-day Activity reconciles them
      - shares the global Flex gate (last_flex_call_ts)
    """
    try:
        state = state_mod.load_state(state_path)
    except StateCorruptError as exc:
        log.error("[FAIL] state.json corrupt (%s) -> safe-mode back-off 30 min", exc)
        state = state_mod.enter_safe_mode(now)
        state_mod.save_state(state, state_path)
        return RC_OK

    # Already captured today (e.g. a manual early pull)? -> skip the Flex call.
    if state.last_confirmation_date == today.isoformat():
        log.info("[OK] today's Confirmation already captured (%s) -> skip, no Flex call", today)
        state.last_run_at = datetime.now(timezone.utc).isoformat()
        state_mod.save_state(state, state_path)
        return RC_OK

    # Rate-limit gate (ADR-002) — before any Flex call
    reason = state_mod.gate_flex_call(state, now)
    if reason is not None:
        log.info("[OK] skip Flex call: %s", reason)
        state.last_run_at = datetime.now(timezone.utc).isoformat()
        state_mod.save_state(state, state_path)
        return RC_OK

    log.info("[..] downloading Trade Confirmation (query_id=%s)...", query_id)
    try:
        xml_bytes = download_fn(token, query_id)
    except FlexThrottledError:
        log.warning("[WARN] Flex throttled (1018/429) -> back off 30 min, no retry")
        state_mod.mark_throttled(state, now)
        state_mod.save_state(state, state_path)
        return RC_RETRYABLE
    except FlexAuthError as exc:
        log.error("[FAIL] Flex auth error (%s) -> renew token", exc)
        _log_staleness(state, today)
        state_mod.mark_error(state, f"AUTH:{exc.code}")
        state_mod.save_state(state, state_path)
        return RC_HARD
    except Exception as exc:
        log.error("[FAIL] Flex fetch failed: %s", exc)
        state_mod.mark_error(state, str(exc))
        state_mod.save_state(state, state_path)
        return RC_RETRYABLE

    # Arm the shared-IP gate immediately on success.
    state_mod.mark_flex_call_success(state, now)
    state_mod.save_state(state, state_path)

    run_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = parse_trades(xml_bytes, run_id=run_id, now_utc=now_iso, profile=CONFIRMATION_PROFILE)
    conn = sqlite_store.connect(db_path)
    try:
        sqlite_store.init_schema(conn)
        stats = sqlite_store.upsert_trades(conn, rows)
        log.info(
            "[OK] confirmation ingest: %d rows, %d new, %d dupes ignored (preliminary, no clobber)",
            stats.attempted, stats.inserted, stats.ignored_dupes,
        )
        # Same-day csv export — Confirmation is the primary MTS feed.
        if rows:
            row_dates = [r.trade_date for r in rows]
            dates = sqlite_store.distinct_export_dates(
                conn, min(row_dates), max(row_dates), underlyings
            )
            for d in dates:
                es = exporter.export_date(conn, d, export_dir, underlyings)
                log.info("[OK] %s", es.summary())
            if not dates:
                log.info("[OK] no target futures in confirmation to export")
    finally:
        conn.close()

    # Mark today's Confirmation captured (does NOT advance last_success_trade_date).
    state_mod.mark_confirmation_success(state, today.isoformat(), now)
    state_mod.save_state(state, state_path)
    return RC_OK


def _log_staleness(state: state_mod.State, today: date) -> None:
    """On a failed/blocked fetch, surface how stale the archive is so a multi-day
    outage (e.g. an expired Flex token needing re-activation in IB) is obvious in
    the log + RUN SUMMARY, not only in the exit code. WARNING level so the
    summary handler collects it.
    """
    last = state.last_success_trade_date
    if not last:
        log.warning("[STALE] no successful Activity pull on record yet")
        return
    try:
        days = (today - date.fromisoformat(last)).days
    except ValueError:
        return
    log.warning("[STALE] last successful Activity pull: %s (%d days ago) — "
                "if this persists, check the Flex token (may need re-activation in IB).",
                last, days)


def _setup_logging(logs_dir: Path = LOGS_DIR) -> Path:
    """Log to BOTH console (stderr) and a dated file, regardless of how the
    process is launched (no longer relies on the .bat shell redirect)."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"ib_sync_{date.today():%Y%m%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[
            logging.StreamHandler(),                         # console
            logging.FileHandler(log_path, encoding="utf-8"),  # dated file (append)
        ],
    )
    return log_path


def today_et(now_dt: datetime | None = None) -> date:
    """The current trade date in US/Eastern (NOT the laptop's local date).

    Critical for the backfill window: 'yesterday' must be the last COMPLETE ET
    session. Using local date for a UTC+8 user can land 'yesterday' on the still-
    open ET session (REVIEW F1 / NFR-RELIABILITY-3). `now_dt` injected for tests.
    """
    if now_dt is None:
        now_dt = datetime.now(timezone.utc)
    return now_dt.astimezone(ET_TZ).date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.ib_sync")
    parser.add_argument("--env", default=str(ENV_PATH), help=".env path")
    parser.add_argument(
        "--mode", choices=["activity", "confirmation", "auto"], default="activity",
        help="activity = T+1 Flex Activity (backup/reconcile, default); "
             "confirmation = same-day Trade Confirmation -> SQLite + MTS csv (primary); "
             "auto = pick by state+time (scheduler uses this)",
    )
    args = parser.parse_args(argv)

    log_path = _setup_logging()
    et = today_et()
    # Same Flex token for both queries; activity query is always needed, the
    # confirmation query id is loaded too when auto might resolve to confirmation.
    token, activity_query = get_credentials(Path(args.env))
    confirmation_query = None
    if args.mode in ("confirmation", "auto"):
        _, confirmation_query = get_confirmation_credentials(Path(args.env))
    log.info("[..] logging to %s | mode=%s | trade-date(ET)=%s", log_path, args.mode, et)
    return run(
        now=time.time(), today=et, token=token, query_id=activity_query,
        confirmation_query_id=confirmation_query, mode=args.mode,
    )


if __name__ == "__main__":
    sys.exit(main())
