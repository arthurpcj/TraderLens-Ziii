"""Step 1 tests — parser against the real (sanitized) spike fixture."""

from __future__ import annotations

import pytest

from src.errors import FlexResponseError
from src.parser import (
    derive_asset_type,
    parse_statements,
    parse_trade_date,
    parse_trade_time,
    parse_trades,
)

TARGET = {"NQ", "MNQ", "ES", "MES"}


def test_parse_statements_single_account(sample_xml_bytes):
    metas = parse_statements(sample_xml_bytes)
    assert len(metas) == 1
    assert metas[0].account_id == "U0000000"
    assert metas[0].from_date and metas[0].to_date


def test_parse_trades_count(sample_xml_bytes):
    rows = parse_trades(sample_xml_bytes, run_id="RUN1", now_utc="2026-05-20T00:00:00+00:00")
    assert len(rows) == 33


def test_fut_stk_split(sample_xml_bytes):
    rows = parse_trades(sample_xml_bytes)
    fut = [r for r in rows if r.asset_type == "FUT"]
    stk = [r for r in rows if r.asset_type == "STK"]
    assert len(fut) == 31
    assert len(stk) == 2
    # stocks have no expiry
    assert all(r.expiry is None for r in stk)
    assert all(r.expiry for r in fut)


def test_target_underlying_count(sample_xml_bytes):
    rows = parse_trades(sample_xml_bytes)
    target = [r for r in rows if r.asset_type == "FUT" and r.underlying in TARGET]
    assert len(target) == 26  # 20 MES + 6 MNQ (EXECUTION level)


def test_audit_fields_propagated(sample_xml_bytes):
    rows = parse_trades(sample_xml_bytes, run_id="RUN42", now_utc="2026-05-20T12:00:00+00:00")
    assert all(r.source_run_id == "RUN42" for r in rows)
    assert all(r.row_created_at == "2026-05-20T12:00:00+00:00" for r in rows)
    # v1 leaves category NULL in SQLite (exporter fills csv fixed value)
    assert all(r.category is None for r in rows)


def test_known_trade_conversion(sample_xml_bytes):
    rows = parse_trades(sample_xml_bytes)
    by_id = {r.trade_id: r for r in rows}
    t = by_id["1216416114"]  # MES BUY open from RESULTS.md sample
    assert t.trade_date == "2026-04-22"
    assert t.trade_time == "09:56:05"
    assert t.underlying == "MES"
    assert t.expiry == "20260618"
    assert t.asset_type == "FUT"
    assert t.buy_sell == "BUY"
    assert t.quantity == 1
    assert t.trade_price == pytest.approx(7148.0)
    assert t.open_close == "O"
    assert t.fifo_pnl_realized == pytest.approx(0.0)  # open leg: "0" -> 0.0
    assert t.ib_commission == pytest.approx(-0.62)


def test_signed_quantity_on_sell(sample_xml_bytes):
    rows = parse_trades(sample_xml_bytes)
    by_id = {r.trade_id: r for r in rows}
    sell = by_id["1216419347"]  # MES SELL close
    assert sell.buy_sell == "SELL"
    assert sell.quantity == -1  # signed
    assert sell.fifo_pnl_realized == pytest.approx(37.51)


# --- pure conversion helpers (AC-15 quirks) ---

@pytest.mark.parametrize("raw,expected", [("20260422", "2026-04-22"), ("20251231", "2025-12-31")])
def test_parse_trade_date(raw, expected):
    assert parse_trade_date(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("20260422;095605", "09:56:05"), ("20260101;000000", "00:00:00"), ("20260101;235959", "23:59:59")],
)
def test_parse_trade_time(raw, expected):
    assert parse_trade_time(raw) == expected


@pytest.mark.parametrize("expiry,expected", [("20260618", "FUT"), ("", "STK"), (None, "STK")])
def test_derive_asset_type(expiry, expected):
    assert derive_asset_type(expiry) == expected


def test_malformed_xml_raises():
    with pytest.raises(FlexResponseError):
        parse_trades(b"<not valid xml<<<")


def test_bad_date_format_raises():
    with pytest.raises(FlexResponseError):
        parse_trade_date("2026-04-22")  # already formatted, not 8-digit


def test_order_ref_parsed_from_orderreference():
    """FR-PIVOT-2b: orderReference (shared AF/TCF attr) -> order_ref; absent -> None."""
    from src.parser import CONFIRMATION_PROFILE
    from builders import af_trade, af_xml, tcf_trade, tcf_xml

    af = parse_trades(af_xml(af_trade(orderReference="bt_orb_v3")))
    assert af[0].order_ref == "bt_orb_v3"
    tcf = parse_trades(
        tcf_xml(tcf_trade(orderReference="bt_orb_v3")), profile=CONFIRMATION_PROFILE
    )
    assert tcf[0].order_ref == "bt_orb_v3"
    # absent attribute -> None (manual orders carry no reference)
    none_ref = parse_trades(af_xml(af_trade(orderReference=None)))
    assert none_ref[0].order_ref is None
