"""Step 6 — full pipeline integration (HTTP mocked via injected download_fn).

Covers AC-1 (normal), AC-5 (catch-up window), AC-6 (idempotency: window + gate),
AC-7/7b (auth/throttle), AC-13 (state corrupt safe-mode). No live Flex.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from src import ib_sync, sqlite_store
from src import state as state_mod
from src.constants import MIN_INTERVAL_SEC, PENALTY_BOX_SEC, RC_HARD, RC_OK, RC_RETRYABLE
from src.errors import FlexAuthError, FlexServerBusyError, FlexThrottledError

from builders import tcf_trade, tcf_xml

_NY = ZoneInfo("America/New_York")


def _ny_epoch(y, m, d, hh) -> float:
    return datetime(y, m, d, hh, 0, tzinfo=_NY).timestamp()

TOKEN, QUERY = "tok", "123"


class Downloader:
    def __init__(self, payload: bytes | None = None, exc: Exception | None = None):
        self.payload = payload
        self.exc = exc
        self.calls = 0

    def __call__(self, token, query_id):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.payload


@pytest.fixture
def paths(tmp_path):
    return {
        "db_path": tmp_path / "trades.sqlite",
        "state_path": tmp_path / "state.json",
        "export_dir": tmp_path / "exports",
    }


def _count_rows(db_path) -> int:
    conn = sqlite_store.connect(str(db_path))
    try:
        return conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    finally:
        conn.close()


def test_ac1_normal_path(sample_xml_bytes, paths):
    dl = Downloader(sample_xml_bytes)
    rc = ib_sync.run(
        now=1_000_000.0, today=date(2026, 5, 20), token=TOKEN, query_id=QUERY,
        download_fn=dl, **paths,
    )
    assert rc == 0
    assert dl.calls == 1
    assert _count_rows(paths["db_path"]) == 33  # full archive incl stocks
    st = state_mod.load_state(paths["state_path"])
    assert st.last_success_trade_date == "2026-05-19"
    assert st.last_flex_call_ts == 1_000_000.0
    # csv generated for at least the 2026-04-22 MES pair
    assert (paths["export_dir"] / "mts_trades_2026-04-22.csv").exists()


def test_ac6_window_idempotency(sample_xml_bytes, paths):
    dl = Downloader(sample_xml_bytes)
    common = dict(today=date(2026, 5, 20), token=TOKEN, query_id=QUERY, download_fn=dl, **paths)
    ib_sync.run(now=1_000_000.0, **common)
    # 2nd run same day: last_success advanced to yesterday -> empty window -> skip
    rc2 = ib_sync.run(now=1_000_000.0 + 10 * MIN_INTERVAL_SEC, **common)
    assert rc2 == 0
    assert dl.calls == 1  # NOT called again


def test_ac6b_gate_blocks_when_recent_call(sample_xml_bytes, paths):
    # state: window non-empty (old last_success) but a Flex call happened 100s ago
    seed = state_mod.State(last_success_trade_date="2026-04-01", last_flex_call_ts=999_900.0)
    state_mod.save_state(seed, paths["state_path"])
    dl = Downloader(sample_xml_bytes)
    rc = ib_sync.run(
        now=1_000_000.0, today=date(2026, 5, 20), token=TOKEN, query_id=QUERY,
        download_fn=dl, **paths,
    )
    assert rc == 0
    assert dl.calls == 0  # gate blocked (100s < 600s)


def test_ac5_catchup_window(sample_xml_bytes, paths):
    seed = state_mod.State(last_success_trade_date="2026-05-14")  # gap, no recent call
    state_mod.save_state(seed, paths["state_path"])
    dl = Downloader(sample_xml_bytes)
    rc = ib_sync.run(
        now=1_000_000.0, today=date(2026, 5, 20), token=TOKEN, query_id=QUERY,
        download_fn=dl, **paths,
    )
    assert rc == 0
    assert dl.calls == 1  # single Flex call covers the gap (Last 30 Days)
    # 2026-05-15 trades fall in window (05-15..05-19) -> exported
    assert (paths["export_dir"] / "mts_trades_2026-05-15.csv").exists()


def test_ac7_token_expiry(paths):
    dl = Downloader(exc=FlexAuthError("Token has expired.", "1012"))
    rc = ib_sync.run(
        now=1_000_000.0, today=date(2026, 5, 20), token=TOKEN, query_id=QUERY,
        download_fn=dl, **paths,
    )
    assert rc == RC_HARD  # auth -> user must renew token (MTS P5: HARD)
    st = state_mod.load_state(paths["state_path"])
    assert st.last_success_trade_date is None  # untouched
    assert st.last_error and "1012" in st.last_error


def test_ac7_token_expiry_logs_staleness(paths, caplog):
    import logging
    # Prior success 6 days before today -> auth failure should surface staleness.
    seed = state_mod.State(last_success_trade_date="2026-05-14")
    state_mod.save_state(seed, paths["state_path"])
    dl = Downloader(exc=FlexAuthError("Token has expired.", "1012"))
    with caplog.at_level(logging.INFO, logger="tradelens"):
        rc = ib_sync.run(
            now=1_000_000.0, today=date(2026, 5, 20), token=TOKEN, query_id=QUERY,
            download_fn=dl, **paths,
        )
    assert rc == RC_HARD
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "[STALE]" in text and "6 days ago" in text  # multi-day outage visible


def test_ac7b_throttle_backoff(paths):
    dl = Downloader(exc=FlexThrottledError("Too many requests", "1018"))
    rc = ib_sync.run(
        now=1_000_000.0, today=date(2026, 5, 20), token=TOKEN, query_id=QUERY,
        download_fn=dl, **paths,
    )
    assert rc == RC_RETRYABLE  # throttle -> back off + retry next trigger (MTS P5: RETRYABLE)
    st = state_mod.load_state(paths["state_path"])
    assert st.throttled_until_ts == 1_000_000.0 + PENALTY_BOX_SEC
    assert st.last_success_trade_date is None  # untouched


def test_server_busy_exhausted_is_retryable(paths):
    # Server busy after internal retries exhausted -> transient -> RETRYABLE.
    dl = Downloader(exc=FlexServerBusyError("Statement generation in progress", "1009"))
    rc = ib_sync.run(
        now=1_000_000.0, today=date(2026, 5, 20), token=TOKEN, query_id=QUERY,
        download_fn=dl, **paths,
    )
    assert rc == RC_RETRYABLE
    st = state_mod.load_state(paths["state_path"])
    assert st.last_success_trade_date is None  # untouched


def test_run_summary_clean_on_success(sample_xml_bytes, paths, caplog):
    import logging
    dl = Downloader(sample_xml_bytes)
    with caplog.at_level(logging.INFO, logger="tradelens"):
        ib_sync.run(
            now=1_000_000.0, today=date(2026, 5, 20), token=TOKEN, query_id=QUERY,
            download_fn=dl, **paths,
        )
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "RUN SUMMARY" in text
    assert "warnings: 0 | errors: 0" in text


def test_run_summary_lists_malformed_skips(paths, caplog):
    import logging
    # XML with one good + one malformed (missing tradePrice) target trade.
    xml = (
        b'<FlexQueryResponse><FlexStatements count="1">'
        b'<FlexStatement accountId="U0" fromDate="20260401" toDate="20260519"><Trades>'
        b'<Trade tradeID="G" tradeDate="20260518" dateTime="20260518;100000" underlyingSymbol="MES"'
        b' expiry="20260618" buySell="BUY" quantity="1" tradePrice="7000" multiplier="5"'
        b' ibCommission="-0.62" openCloseIndicator="O" levelOfDetail="EXECUTION"/>'
        b'<Trade tradeID="BAD" tradeDate="20260518" dateTime="20260518;100500" underlyingSymbol="MES"'
        b' expiry="20260618" buySell="SELL" quantity="-1" multiplier="5"'
        b' ibCommission="-0.62" openCloseIndicator="C" levelOfDetail="EXECUTION"/>'
        b"</Trades></FlexStatement></FlexStatements></FlexQueryResponse>"
    )
    dl = Downloader(xml)
    with caplog.at_level(logging.INFO, logger="tradelens"):
        rc = ib_sync.run(
            now=1_000_000.0, today=date(2026, 5, 20), token=TOKEN, query_id=QUERY,
            download_fn=dl, **paths,
        )
    assert rc == 0
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "RUN SUMMARY" in text
    assert "BAD" in text  # malformed skip surfaced in summary
    # only the good trade stored
    assert _count_rows(paths["db_path"]) == 1


def test_ac13_state_corrupt_safe_mode(paths):
    paths["state_path"].write_text("{garbage", encoding="utf-8")
    dl = Downloader(b"unused")
    rc = ib_sync.run(
        now=1_000_000.0, today=date(2026, 5, 20), token=TOKEN, query_id=QUERY,
        download_fn=dl, **paths,
    )
    assert rc == 0
    assert dl.calls == 0  # no Flex call in safe-mode
    st = state_mod.load_state(paths["state_path"])
    assert st.throttled_until_ts == 1_000_000.0 + PENALTY_BOX_SEC


# --- spike-002: --mode auto resolver + confirmation export/skip --------------

def test_resolve_auto_mode_by_state_and_ny_hour():
    today = date(2026, 5, 21)                                    # Thursday (weekday=3)
    fresh = state_mod.State()                                    # confirmation not done
    done = state_mod.State(last_confirmation_date="2026-05-21")  # confirmation done today

    # pre-close -> skip (no premature/partial confirmation, no early activity)
    assert ib_sync._resolve_auto_mode(fresh, _ny_epoch(2026, 5, 21, 10), today) == "skip"
    # after close, not captured -> confirmation (even at a late boot, same NY day)
    assert ib_sync._resolve_auto_mode(fresh, _ny_epoch(2026, 5, 21, 16), today) == "confirmation"
    assert ib_sync._resolve_auto_mode(fresh, _ny_epoch(2026, 5, 21, 21), today) == "confirmation"
    # captured, before activity slot -> skip (NOT activity early)
    assert ib_sync._resolve_auto_mode(done, _ny_epoch(2026, 5, 21, 16), today) == "skip"
    # captured, at activity slot -> activity
    assert ib_sync._resolve_auto_mode(done, _ny_epoch(2026, 5, 21, 20), today) == "activity"


def test_resolve_auto_mode_skips_ny_weekend():
    """NY Sat/Sun: scheduler still fires (Task Scheduler can't tell weekdays),
    but auto-mode resolves to 'skip' so no Flex quota is burned on a closed
    market. Observed 2026-05-25/26: weekend fires were making 2-3 real Flex
    calls/day returning 0 rows. See LOG_20260526."""
    sat = date(2026, 5, 23)                                      # Saturday (weekday=5)
    sun = date(2026, 5, 24)                                      # Sunday (weekday=6)
    fresh = state_mod.State()
    done_sat = state_mod.State(last_confirmation_date="2026-05-23")
    done_sun = state_mod.State(last_confirmation_date="2026-05-24")

    # All NY hours, fresh and done, both Sat and Sun -> skip
    for hour in (10, 16, 17, 20, 21, 23):
        assert ib_sync._resolve_auto_mode(fresh, _ny_epoch(2026, 5, 23, hour), sat) == "skip"
        assert ib_sync._resolve_auto_mode(done_sat, _ny_epoch(2026, 5, 23, hour), sat) == "skip"
        assert ib_sync._resolve_auto_mode(fresh, _ny_epoch(2026, 5, 24, hour), sun) == "skip"
        assert ib_sync._resolve_auto_mode(done_sun, _ny_epoch(2026, 5, 24, hour), sun) == "skip"


def test_resolve_auto_mode_weekday_unchanged_after_weekend_gate():
    """Regression guard: adding the weekend gate must not affect Mon-Fri behavior.
    Spot-check Monday and Friday at the slot boundaries."""
    mon = date(2026, 5, 25)                                      # Monday (weekday=0)
    fri = date(2026, 5, 22)                                      # Friday (weekday=4)
    fresh = state_mod.State()
    done_mon = state_mod.State(last_confirmation_date="2026-05-25")
    done_fri = state_mod.State(last_confirmation_date="2026-05-22")

    # Monday post-close, not captured -> confirmation (weekday path lives)
    assert ib_sync._resolve_auto_mode(fresh, _ny_epoch(2026, 5, 25, 16), mon) == "confirmation"
    # Friday activity slot, captured -> activity (weekday path lives)
    assert ib_sync._resolve_auto_mode(done_fri, _ny_epoch(2026, 5, 22, 20), fri) == "activity"
    # Monday pre-close -> still skip (not weekend-skip, the existing pre-slot skip)
    assert ib_sync._resolve_auto_mode(fresh, _ny_epoch(2026, 5, 25, 10), mon) == "skip"


def test_confirmation_ingests_and_exports_csv(paths):
    # A target future (MES) confirmation -> SQLite + same-day MTS csv export.
    dl = Downloader(tcf_xml(tcf_trade(tradeID="C1", underlyingSymbol="MES", expiry="20260618")))
    rc = ib_sync.run(
        now=1_000_000.0, today=date(2026, 5, 18), token=TOKEN, query_id=QUERY,
        download_fn=dl, mode="confirmation", **paths,
    )
    assert rc == RC_OK and dl.calls == 1
    assert _count_rows(paths["db_path"]) == 1
    assert (paths["export_dir"] / "mts_trades_2026-05-18.csv").exists()  # primary feed
    st = state_mod.load_state(paths["state_path"])
    assert st.last_confirmation_date == "2026-05-18"
    assert st.last_success_trade_date is None  # confirmation does NOT advance this


def test_confirmation_skips_flex_if_already_captured_today(paths):
    # Manual early pull already grabbed today -> scheduled run must NOT re-query.
    seed = state_mod.State(last_confirmation_date="2026-05-18")
    state_mod.save_state(seed, paths["state_path"])
    dl = Downloader(tcf_xml(tcf_trade(tradeID="C1")))
    rc = ib_sync.run(
        now=1_000_000.0, today=date(2026, 5, 18), token=TOKEN, query_id=QUERY,
        download_fn=dl, mode="confirmation", **paths,
    )
    assert rc == RC_OK
    assert dl.calls == 0  # no Flex call — today already captured
