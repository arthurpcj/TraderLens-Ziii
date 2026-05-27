"""Trade Confirmation (TCF) parsing — verified against real spike-002 data.

The TCF statement uses a different element (<TradeConfirm>) AND different
attribute names than Activity (price/commission/code vs tradePrice/
ibCommission/openCloseIndicator). parse_trades(profile=CONFIRMATION_PROFILE)
maps them to the same canonical TradeRow. See parser.SourceProfile.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src import sqlite_store
from src.parser import ACTIVITY_PROFILE, CONFIRMATION_PROFILE, parse_trades

FIXTURE = Path(__file__).parent / "fixtures" / "sample_trade_confirm.xml"


@pytest.fixture
def tcf_bytes() -> bytes:
    return FIXTURE.read_bytes()


def test_confirm_rows_parsed(tcf_bytes):
    rows = parse_trades(
        tcf_bytes, profile=CONFIRMATION_PROFILE, run_id="t", now_utc="2026-05-21T00:00:00+00:00"
    )
    # 4 EXECUTION rows; the interleaved <Order> (levelOfDetail=ORDER) rows are excluded.
    assert len(rows) == 4
    assert {r.underlying for r in rows} == {"MNQ"}
    assert {r.asset_type for r in rows} == {"FUT"}
    assert {r.data_source for r in rows} == {"CONFIRMATION"}


def test_confirm_activity_profile_finds_nothing(tcf_bytes):
    # A TCF statement has no <Trade> elements; the Activity default must parse
    # zero rows rather than silently mis-read — proves the profile matters.
    rows = parse_trades(tcf_bytes, profile=ACTIVITY_PROFILE)
    assert rows == []


def test_confirm_fields_and_signs(tcf_bytes):
    rows = parse_trades(tcf_bytes, profile=CONFIRMATION_PROFILE, run_id="t", now_utc="z")
    by_id = {r.trade_id: r for r in rows}

    open_leg = by_id["1216857749"]
    assert open_leg.trade_date == "2026-05-21"
    assert open_leg.trade_time == "09:50:42"      # from dateTime "20260521;095042"
    assert open_leg.buy_sell == "BUY"
    assert open_leg.open_close == "O"             # from code="O" (NOT openCloseIndicator)
    assert open_leg.quantity == 1                 # signed: BUY positive
    assert open_leg.trade_price == pytest.approx(27042.75)  # from price (NOT tradePrice)
    assert open_leg.expiry == "20260618"          # full YYYYMMDD kept (exporter truncates)
    assert open_leg.multiplier == 2
    assert open_leg.ib_commission == pytest.approx(-0.62)   # from commission, IB-native signed
    assert open_leg.fifo_pnl_realized is None     # TCF has no realized-PnL field
    assert open_leg.notes is None                 # TCF has no notes attr
    assert open_leg.data_source == "CONFIRMATION"

    close_leg = by_id["1216862213"]
    assert close_leg.buy_sell == "SELL"
    assert close_leg.open_close == "C"            # from code="C"
    assert close_leg.quantity == -1               # signed: SELL negative


def test_confirm_ingest_roundtrip(tcf_bytes):
    """End-to-end: parse TCF -> upsert SQLite (INSERT OR IGNORE, idempotent)."""
    rows = parse_trades(tcf_bytes, profile=CONFIRMATION_PROFILE, run_id="t", now_utc="z")
    conn = sqlite_store.connect(":memory:")
    sqlite_store.init_schema(conn)

    s1 = sqlite_store.upsert_trades(conn, rows)
    assert (s1.attempted, s1.inserted, s1.ignored_dupes) == (4, 4, 0)

    # data_source persisted through the round-trip
    stored = {r.trade_id: r for r in sqlite_store.query_all(conn)}
    assert all(r.data_source == "CONFIRMATION" for r in stored.values())
    assert stored["1216857749"].open_close == "O"

    # re-ingesting the same statement is a no-op (idempotent on trade_id)
    s2 = sqlite_store.upsert_trades(conn, rows)
    assert (s2.inserted, s2.ignored_dupes) == (0, 4)
    conn.close()
