"""TCF parser robustness: the Confirmation path must tolerate a user editing
the Flex query field set (extra/reordered/missing fields) exactly like the
Activity path does — skip a bad row + warn, never crash, never lose the batch.
"""

from __future__ import annotations

import logging

from src.parser import CONFIRMATION_PROFILE, parse_trades

from builders import tcf_trade, tcf_xml

_KW = dict(profile=CONFIRMATION_PROFILE, run_id="r", now_utc="z")


def test_extra_unknown_attr_ignored():
    rows = parse_trades(tcf_xml(tcf_trade(someNewIBField2027="x")), **_KW)
    assert len(rows) == 1 and rows[0].underlying == "MES"


def test_reordered_attrs_same_result():
    # Builder dict order vs reversed -> identical parse (read by name).
    t = tcf_trade(tradeID="Z")
    r1 = parse_trades(tcf_xml(t), **_KW)
    r2 = parse_trades(tcf_xml(dict(reversed(list(t.items())))), **_KW)
    assert r1 == r2 and len(r1) == 1


def test_missing_optional_commission_defaults_none():
    rows = parse_trades(tcf_xml(tcf_trade(commission=None)), **_KW)
    assert len(rows) == 1 and rows[0].ib_commission is None


def test_missing_optional_multiplier_defaults_none():
    rows = parse_trades(tcf_xml(tcf_trade(multiplier=None)), **_KW)
    assert len(rows) == 1 and rows[0].multiplier is None


def test_missing_critical_price_skips_only_that_row(caplog):
    good = tcf_trade(tradeID="GOOD")
    bad = tcf_trade(tradeID="BAD", price=None)   # price is critical for TCF
    with caplog.at_level(logging.WARNING):
        rows = parse_trades(tcf_xml(good, bad), **_KW)
    assert {r.trade_id for r in rows} == {"GOOD"}
    assert any("BAD" in rec.message for rec in caplog.records)


def test_missing_critical_code_skips_row():
    # `code` carries open/close for TCF; empty -> treated as missing -> skip.
    rows = parse_trades(tcf_xml(tcf_trade(tradeID="NC", code="")), **_KW)
    assert rows == []


def test_unparseable_quantity_skips_row(caplog):
    good = tcf_trade(tradeID="GOOD")
    bad = tcf_trade(tradeID="BAD", quantity="not-a-number")
    with caplog.at_level(logging.WARNING):
        rows = parse_trades(tcf_xml(good, bad), **_KW)
    assert {r.trade_id for r in rows} == {"GOOD"}


def test_code_with_multiple_tokens_extracts_open_close():
    # IB `code` can carry several ';'-separated codes (e.g. "O;P"); we extract O/C.
    o = parse_trades(tcf_xml(tcf_trade(tradeID="O1", code="O;P")), **_KW)[0]
    c = parse_trades(tcf_xml(tcf_trade(tradeID="C1", code="C;Ep")), **_KW)[0]
    assert o.open_close == "O" and c.open_close == "C"


def test_order_rows_never_counted():
    # with_orders=True prepends <Order levelOfDetail="ORDER"> per row; only the
    # <TradeConfirm> EXECUTION rows must be parsed.
    rows = parse_trades(tcf_xml(tcf_trade(tradeID="A"), tcf_trade(tradeID="B")), **_KW)
    assert {r.trade_id for r in rows} == {"A", "B"}  # 2, not 4
