"""Round-trip pairing tests (Priority 2 / FR-PIVOT)."""

from __future__ import annotations

import pytest

from src.parser import TradeRow
from src.roundtrip import pair_round_trips


def _leg(tid, date, time, bs, qty_signed, price, oc, *, underlying="MNQ",
         expiry="20260618", mult=2, comm=-0.62, asset="FUT", order_ref=None):
    return TradeRow(
        trade_id=tid, trade_date=date, trade_time=time, underlying=underlying,
        expiry=expiry, buy_sell=bs, quantity=qty_signed, trade_price=price,
        multiplier=mult, ib_commission=comm, open_close=oc,
        fifo_pnl_realized=None, asset_type=asset, category=None, notes=None,
        category_set_at=None, row_created_at="z", source_run_id="r",
        order_ref=order_ref,
    )


def test_simple_long_intraday():
    rows = [
        _leg("1", "2026-05-20", "09:50:00", "BUY", 1, 100.0, "O"),
        _leg("2", "2026-05-20", "10:20:00", "SELL", -1, 110.0, "C"),
    ]
    rts, stats = pair_round_trips(rows)
    assert len(rts) == 1
    rt = rts[0]
    assert rt.direction == "LONG"
    assert rt.pnl_pts == 10.0
    assert rt.pnl_usd == pytest.approx(10 * 2 * 1 + (-0.62 - 0.62))  # 18.76
    assert rt.is_win is True
    assert rt.is_intraday is True
    assert rt.trade_class == "Futures-Intraday"
    assert rt.hold_minutes == 30
    assert stats["still_open_qty"] == 0 and stats["unmatched_close_qty"] == 0


def test_short_round_trip():
    rows = [
        _leg("1", "2026-05-20", "09:50:00", "SELL", -1, 110.0, "O"),
        _leg("2", "2026-05-20", "10:00:00", "BUY", 1, 100.0, "C"),
    ]
    rts, _ = pair_round_trips(rows)
    assert rts[0].direction == "SHORT"
    assert rts[0].pnl_pts == 10.0          # open - close for short
    assert rts[0].pnl_usd == pytest.approx(18.76)


def test_cross_day_swing():
    rows = [
        _leg("1", "2026-05-18", "15:00:00", "BUY", 1, 100.0, "O"),
        _leg("2", "2026-05-20", "11:00:00", "SELL", -1, 90.0, "C"),
    ]
    rt = pair_round_trips(rows)[0][0]
    assert rt.is_intraday is False
    assert rt.trade_class == "Futures-Swing"
    assert rt.pnl_pts == -10.0
    assert rt.is_win is False
    assert rt.hold_minutes == 44 * 60  # 05-18 15:00 -> 05-20 11:00 = 44h = 2640 min


def test_fifo_partial_split():
    # two 1-lot opens, then one 2-lot close -> two round-trips (FIFO).
    rows = [
        _leg("1", "2026-05-20", "09:00:00", "BUY", 1, 100.0, "O"),
        _leg("2", "2026-05-20", "09:30:00", "BUY", 1, 102.0, "O"),
        _leg("3", "2026-05-20", "10:00:00", "SELL", -2, 110.0, "C", comm=-1.24),
    ]
    rts, stats = pair_round_trips(rows)
    assert len(rts) == 2
    opens = sorted(rt.open_price for rt in rts)
    assert opens == [100.0, 102.0]            # FIFO: oldest first
    # close commission (-1.24 over qty 2 = -0.62/unit) allocated per round-trip
    for rt in rts:
        assert rt.commission == pytest.approx(-0.62 - 0.62)  # open -0.62 + close -0.62
    assert stats["still_open_qty"] == 0


def test_stock_multiplier_defaults_one():
    rows = [
        _leg("1", "2026-05-20", "09:50:00", "BUY", 10, 5.00, "O",
             underlying="FMCC", expiry=None, mult=None, comm=-1.0, asset="STK"),
        _leg("2", "2026-05-20", "14:00:00", "SELL", -10, 5.50, "C",
             underlying="FMCC", expiry=None, mult=None, comm=-1.0, asset="STK"),
    ]
    rt = pair_round_trips(rows)[0][0]
    assert rt.trade_class == "Stock"
    assert rt.multiplier == 1
    assert rt.pnl_usd == pytest.approx(0.5 * 1 * 10 + (-1.0 - 1.0))  # 3.0


def test_derived_dims_from_entry_leg():
    """FR-PIVOT-2: session / entry_hour / entry_dow / hold_bucket / open_trade_id /
    order_ref are all keyed off the ENTRY leg (ET wall-clock)."""
    rows = [
        # 2026-05-20 is a Wednesday; 10:00 ET is RTH; held 30 min.
        _leg("OPEN", "2026-05-20", "10:00:00", "BUY", 1, 100.0, "O", order_ref="bt_orb_v3"),
        _leg("CLOSE", "2026-05-20", "10:30:00", "SELL", -1, 110.0, "C"),
    ]
    rt = pair_round_trips(rows)[0][0]
    assert rt.session == "RTH"
    assert rt.entry_hour == 10
    assert rt.entry_dow == "3-Wed"
    assert rt.hold_bucket == "15-60m"
    assert rt.open_trade_id == "OPEN"        # entry leg id, NOT the close
    assert rt.order_ref == "bt_orb_v3"       # carried from entry leg only


@pytest.mark.parametrize("open_time,expected", [
    ("09:29:00", "ETH"), ("09:30:00", "RTH"), ("15:59:00", "RTH"), ("16:00:00", "ETH"),
    ("03:00:00", "ETH"), ("23:00:00", "ETH"),
])
def test_session_boundaries(open_time, expected):
    rows = [
        _leg("O", "2026-05-20", open_time, "BUY", 1, 100.0, "O"),
        _leg("C", "2026-05-20", "23:59:00", "SELL", -1, 101.0, "C"),
    ]
    assert pair_round_trips(rows)[0][0].session == expected


@pytest.mark.parametrize("minutes,bucket", [
    (5, "<15m"), (14, "<15m"), (15, "15-60m"), (59, "15-60m"),
    (60, "1-4h"), (239, "1-4h"), (240, ">4h"), (600, ">4h"),
])
def test_hold_buckets(minutes, bucket):
    from datetime import datetime, timedelta
    open_dt = datetime(2026, 5, 20, 8, 0, 0)
    close_dt = open_dt + timedelta(minutes=minutes)
    rows = [
        _leg("O", open_dt.strftime("%Y-%m-%d"), open_dt.strftime("%H:%M:%S"),
             "BUY", 1, 100.0, "O"),
        _leg("C", close_dt.strftime("%Y-%m-%d"), close_dt.strftime("%H:%M:%S"),
             "SELL", -1, 101.0, "C"),
    ]
    assert pair_round_trips(rows)[0][0].hold_bucket == bucket


def test_fifo_split_each_rt_carries_its_own_open_id():
    """A 2-lot close split across two opens -> each round-trip keeps its OWN
    entry id + order_ref (annotation join must not bleed across lots)."""
    rows = [
        _leg("A", "2026-05-20", "09:00:00", "BUY", 1, 100.0, "O", order_ref="setupA"),
        _leg("B", "2026-05-20", "09:30:00", "BUY", 1, 102.0, "O", order_ref="setupB"),
        _leg("C", "2026-05-20", "10:00:00", "SELL", -2, 110.0, "C", comm=-1.24),
    ]
    rts = pair_round_trips(rows)[0]
    by_open = {rt.open_trade_id: rt for rt in rts}
    assert by_open["A"].order_ref == "setupA"
    assert by_open["B"].order_ref == "setupB"


def test_unmatched_and_still_open_counted():
    rows = [
        _leg("1", "2026-05-20", "09:00:00", "SELL", -1, 100.0, "C"),  # close w/o open
        _leg("2", "2026-05-20", "10:00:00", "BUY", 1, 100.0, "O"),    # open never closed
    ]
    rts, stats = pair_round_trips(rows)
    assert rts == []
    assert stats["unmatched_close_qty"] == 1
    assert stats["still_open_qty"] == 1
