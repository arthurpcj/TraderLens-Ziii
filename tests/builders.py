"""Synthetic Flex XML builders for scenario tests.

Lets a test compose arbitrary Activity (AF) and Trade Confirmation (TCF)
statements without hitting Flex. The attribute names mirror the REAL field
sets verified in spike-001 (AF) and spike-002 (TCF), so parsing these matches
production. Use the overrides to model edge cases (stocks, NULL commission,
non-target underlyings, missing/extra fields, mixed-source overlap).
"""

from __future__ import annotations

# --- Activity (AF): <Trades>/<Trade>, attrs tradePrice/ibCommission/openCloseIndicator/fifoPnlRealized
_AF_BASE = {
    "tradeID": "AF1",
    "tradeDate": "20260518",
    "dateTime": "20260518;095605",
    "underlyingSymbol": "MES",
    "expiry": "20260618",
    "buySell": "BUY",
    "quantity": "1",
    "tradePrice": "7148.00",
    "multiplier": "5",
    "ibCommission": "-0.62",
    "openCloseIndicator": "O",
    "fifoPnlRealized": "0",
    "levelOfDetail": "EXECUTION",
}

# --- Confirmation (TCF): <TradeConfirms>/<TradeConfirm>, attrs price/commission/code (no fifo)
_TCF_BASE = {
    "tradeID": "TCF1",
    "tradeDate": "20260518",
    "dateTime": "20260518;095605",
    "underlyingSymbol": "MES",
    "expiry": "20260618",
    "buySell": "BUY",
    "quantity": "1",
    "price": "7148.00",
    "multiplier": "5",
    "commission": "-0.62",
    "code": "O",
    "levelOfDetail": "EXECUTION",
}


def af_trade(**over) -> dict:
    """One Activity trade attr dict. Pass field=None to DELETE a base field."""
    return _merge(_AF_BASE, over)


def tcf_trade(**over) -> dict:
    """One Confirmation trade attr dict. Pass field=None to DELETE a base field."""
    return _merge(_TCF_BASE, over)


def _merge(base: dict, over: dict) -> dict:
    d = dict(base)
    for k, v in over.items():
        if v is None:
            d.pop(k, None)        # explicit delete (model a field IB stopped sending)
        else:
            d[k] = str(v)
    return d


def _attrs(d: dict) -> str:
    return " ".join(f'{k}="{v}"' for k, v in d.items())


def af_xml(*trades: dict, account: str = "U0", count: int = 1) -> bytes:
    """Wrap Activity <Trade> rows in a type=AF FlexQueryResponse."""
    body = "".join(f"<Trade {_attrs(t)}/>" for t in trades)
    return (
        f'<FlexQueryResponse type="AF"><FlexStatements count="{count}">'
        f'<FlexStatement accountId="{account}" fromDate="20260401" toDate="20260519">'
        f"<Trades>{body}</Trades></FlexStatement></FlexStatements></FlexQueryResponse>"
    ).encode()


def tcf_xml(*trades: dict, account: str = "U0", with_orders: bool = True) -> bytes:
    """Wrap Confirmation <TradeConfirm> rows in a type=TCF FlexQueryResponse.

    If `with_orders`, prepend a sibling <Order levelOfDetail="ORDER"> per row to
    mirror the real interleaved structure (parser must skip these).
    """
    parts = []
    for t in trades:
        if with_orders:
            order = dict(t, levelOfDetail="ORDER", tradeID="", code="")
            parts.append(f"<Order {_attrs(order)}/>")
        parts.append(f"<TradeConfirm {_attrs(t)}/>")
    body = "".join(parts)
    return (
        f'<FlexQueryResponse type="TCF"><FlexStatements count="1">'
        f'<FlexStatement accountId="{account}" fromDate="20260518" toDate="20260518">'
        f"<TradeConfirms>{body}</TradeConfirms></FlexStatement></FlexStatements></FlexQueryResponse>"
    ).encode()
