"""Robustness against Flex XML field changes: order, extra, missing fields.

The parser reads attributes by NAME (xml attrib dict), so attribute order is
irrelevant and unknown/extra attributes are ignored. Missing OPTIONAL fields
default to None; missing CRITICAL fields skip that trade (not the batch).
"""

from __future__ import annotations

import logging

from src.parser import parse_trades

# Minimal valid EXECUTION Trade with the critical attrs.
_BASE_ATTRS = {
    "tradeID": "T1",
    "tradeDate": "20260422",
    "dateTime": "20260422;095605",
    "underlyingSymbol": "MES",
    "expiry": "20260618",
    "buySell": "BUY",
    "quantity": "1",
    "tradePrice": "7148.0",
    "multiplier": "5",
    "ibCommission": "-0.62",
    "openCloseIndicator": "O",
    "fifoPnlRealized": "0",
    "levelOfDetail": "EXECUTION",
}


def _xml(*trades: dict) -> bytes:
    def attrs(d: dict) -> str:
        return " ".join(f'{k}="{v}"' for k, v in d.items())
    body = "".join(f"<Trade {attrs(t)}/>" for t in trades)
    return (
        f'<FlexQueryResponse><FlexStatements count="1">'
        f'<FlexStatement accountId="U0" fromDate="20260401" toDate="20260430">'
        f"<Trades>{body}</Trades></FlexStatement></FlexStatements></FlexQueryResponse>"
    ).encode()


def test_attribute_order_irrelevant():
    # Same attrs, reversed insertion order -> identical parse result.
    normal = dict(_BASE_ATTRS)
    reordered = dict(reversed(list(_BASE_ATTRS.items())))
    kw = dict(run_id="R", now_utc="2026-05-20T00:00:00+00:00")
    r1 = parse_trades(_xml(normal), **kw)
    r2 = parse_trades(_xml(reordered), **kw)
    assert len(r1) == len(r2) == 1
    assert r1[0] == r2[0]


def test_extra_unknown_attributes_ignored():
    extended = dict(_BASE_ATTRS)
    extended["someNewIBField2027"] = "whatever"
    extended["anotherExtra"] = "123"
    rows = parse_trades(_xml(extended))
    assert len(rows) == 1
    assert rows[0].underlying == "MES"


def test_missing_optional_commission_defaults_none():
    t = dict(_BASE_ATTRS)
    del t["ibCommission"]
    rows = parse_trades(_xml(t))
    assert len(rows) == 1
    assert rows[0].ib_commission is None


def test_missing_optional_multiplier_defaults_none():
    t = dict(_BASE_ATTRS)
    del t["multiplier"]
    rows = parse_trades(_xml(t))
    assert len(rows) == 1
    assert rows[0].multiplier is None


def test_missing_optional_fifo_and_notes():
    t = dict(_BASE_ATTRS)
    del t["fifoPnlRealized"]
    rows = parse_trades(_xml(t))
    assert rows[0].fifo_pnl_realized is None
    assert rows[0].notes is None


def test_missing_critical_field_skips_only_that_trade(caplog):
    good = dict(_BASE_ATTRS, tradeID="GOOD")
    bad = dict(_BASE_ATTRS, tradeID="BAD")
    del bad["tradePrice"]  # critical
    with caplog.at_level(logging.WARNING):
        rows = parse_trades(_xml(good, bad))
    ids = {r.trade_id for r in rows}
    assert ids == {"GOOD"}  # bad skipped, good survived
    assert any("BAD" in rec.message for rec in caplog.records)


def test_unparseable_value_skips_trade(caplog):
    good = dict(_BASE_ATTRS, tradeID="GOOD")
    bad = dict(_BASE_ATTRS, tradeID="BAD", quantity="not-a-number")
    with caplog.at_level(logging.WARNING):
        rows = parse_trades(_xml(good, bad))
    assert {r.trade_id for r in rows} == {"GOOD"}


def test_empty_critical_treated_as_missing():
    t = dict(_BASE_ATTRS, tradeID="")  # present but empty -> critical missing
    rows = parse_trades(_xml(t))
    assert rows == []


def test_empty_xml_no_trades():
    # Valid statement, zero Trade elements -> empty list, no crash.
    xml = (
        b'<FlexQueryResponse><FlexStatements count="1">'
        b'<FlexStatement accountId="U0" fromDate="20260401" toDate="20260430">'
        b"<Trades></Trades></FlexStatement></FlexStatements></FlexQueryResponse>"
    )
    assert parse_trades(xml) == []


def test_multi_account_warns(caplog):
    a = dict(_BASE_ATTRS, tradeID="A1")
    b = dict(_BASE_ATTRS, tradeID="B1")
    two = (
        f'<FlexQueryResponse><FlexStatements count="2">'
        f'<FlexStatement accountId="U1" fromDate="20260401" toDate="20260430"><Trades>'
        f'<Trade {" ".join(f2 + chr(61) + chr(34) + str(v) + chr(34) for f2, v in a.items())}/>'
        f"</Trades></FlexStatement>"
        f'<FlexStatement accountId="U2" fromDate="20260401" toDate="20260430"><Trades>'
        f'<Trade {" ".join(f2 + chr(61) + chr(34) + str(v) + chr(34) for f2, v in b.items())}/>'
        f"</Trades></FlexStatement></FlexStatements></FlexQueryResponse>"
    ).encode()
    with caplog.at_level(logging.WARNING):
        rows = parse_trades(two)
    assert {r.trade_id for r in rows} == {"A1", "B1"}  # both accounts merged
    assert any("Multiple FlexStatements" in rec.message for rec in caplog.records)
