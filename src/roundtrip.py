"""Round-trip reconstruction from leg-level trades (Priority 2 / FR-PIVOT).

Pairs opening + closing legs (FIFO, per underlying+expiry, ACROSS days) into
round-trips with derived metrics for the local HTML pivot. Pure functions, no
I/O — the pairing logic is unit-tested against fixtures.

PnL convention: ib_commission is IB-native signed (cost negative), so it is
ADDED, never subtracted (matches INTERFACE_CONTRACT §3.1 G6 / §5.6 C5).
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Iterable

from .parser import TradeRow

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoundTrip:
    """One matched open->close round-trip with derived analytics fields."""

    underlying: str
    expiry: str | None
    asset_type: str            # FUT / STK
    direction: str             # LONG / SHORT
    quantity: int              # matched contracts/shares
    open_date: str
    open_time: str
    open_price: float
    close_date: str
    close_time: str
    close_price: float
    multiplier: int | None
    commission: float          # allocated open+close commission (signed, IB-native)
    pnl_pts: float
    pnl_usd: float | None      # None only if multiplier unknown (rare)
    hold_minutes: int
    is_intraday: bool          # opened and closed same trade date
    is_win: bool
    trade_class: str           # Stock / Futures-Intraday / Futures-Swing (pivot dim)
    week: str                  # ISO year-week of close, e.g. 2026-W21
    month: str                 # close month YYYY-MM
    # FR-PIVOT-2 derived dims (all keyed off the ENTRY leg, ET wall-clock) ------
    session: str               # RTH / ETH (index-future regular hours 09:30-16:00 ET)
    entry_hour: int            # hour-of-day of entry (0-23 ET) — top scalper signal
    entry_dow: str             # day-of-week of entry, sortable "1-Mon".."7-Sun"
    hold_bucket: str           # <15m / 15-60m / 1-4h / >4h
    open_trade_id: str         # entry leg's IB tradeID — annotation-layer join key
    close_trade_id: str        # closing leg's IB tradeID — needed to find close legs
                               #   of MTS-confirmed RTs during state-machine export
                               #   (a close leg can split across multiple opens, so
                               #   one close tradeID can map to multiple RTs)
    order_ref: str | None      # entry leg's orderReference — tier-2 setup_tag source
    # FR-PIVOT-2c: the entry/exit ORDER id (ibOrderID/orderID). The MTS State-B
    # export keys on this (falling back to the representative trade_id when None)
    # so leg selection is stable across the full-set vs per-date coalescing paths
    # (a cross-date order is refused by the full-set merge but merged per-date —
    # order_id keys agree where representative trade_ids would not).
    open_order_id: str | None = None
    close_order_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _OpenLot:
    qty: int                   # remaining unmatched contracts
    price: float
    date: str
    time: str
    comm_per_unit: float       # signed commission allocated per contract
    direction: str
    trade_id: str              # entry leg tradeID (-> RoundTrip.open_trade_id)
    order_ref: str | None      # entry leg orderReference (-> RoundTrip.order_ref)
    order_id: str | None       # entry leg order_id (-> RoundTrip.open_order_id)


def _dt(date_str: str, time_str: str) -> datetime:
    return datetime.fromisoformat(f"{date_str}T{time_str}")


def _trade_class(asset_type: str, is_intraday: bool) -> str:
    if asset_type != "FUT":
        return "Stock"
    return "Futures-Intraday" if is_intraday else "Futures-Swing"


# Index-future regular trading hours (ET wall-clock). trade_time is already
# US/Eastern (parser keeps IB wall-clock), so no tz conversion here.
_RTH_START = (9, 30)
_RTH_END = (16, 0)
_DOW_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _session(open_dt: datetime) -> str:
    """RTH if 09:30 <= entry < 16:00 ET, else ETH (overnight/extended)."""
    hm = (open_dt.hour, open_dt.minute)
    return "RTH" if _RTH_START <= hm < _RTH_END else "ETH"


def _entry_dow(open_dt: datetime) -> str:
    """Sortable day-of-week label, '1-Mon'..'7-Sun' (Mon=1, ISO)."""
    iso = open_dt.isoweekday()  # Mon=1..Sun=7
    return f"{iso}-{_DOW_ABBR[iso - 1]}"


def _hold_bucket(hold_minutes: int) -> str:
    if hold_minutes < 15:
        return "<15m"
    if hold_minutes < 60:
        return "15-60m"
    if hold_minutes < 240:
        return "1-4h"
    return ">4h"


def _build_round_trip(
    underlying: str,
    expiry: str | None,
    asset_type: str,
    lot: _OpenLot,
    close_leg: TradeRow,
    qty: int,
    close_comm_per_unit: float,
) -> RoundTrip:
    open_price = lot.price
    close_price = close_leg.trade_price
    pnl_pts = close_price - open_price if lot.direction == "LONG" else open_price - close_price

    mult = close_leg.multiplier
    if mult is None and asset_type == "STK":
        mult = 1  # stocks: 1 unit = 1 share
    commission = (lot.comm_per_unit + close_comm_per_unit) * qty  # signed (cost<0)
    pnl_usd = pnl_pts * mult * qty + commission if mult is not None else None

    open_dt = _dt(lot.date, lot.time)
    close_dt = _dt(close_leg.trade_date, close_leg.trade_time)
    hold_minutes = int((close_dt - open_dt).total_seconds() // 60)
    hold_minutes = max(hold_minutes, 0)
    is_intraday = lot.date == close_leg.trade_date
    if pnl_usd is not None:
        is_win = pnl_usd > 0
    else:
        is_win = pnl_pts > 0
    iso = close_dt.isocalendar()

    return RoundTrip(
        underlying=underlying,
        expiry=expiry,
        asset_type=asset_type,
        direction=lot.direction,
        quantity=qty,
        open_date=lot.date,
        open_time=lot.time,
        open_price=open_price,
        close_date=close_leg.trade_date,
        close_time=close_leg.trade_time,
        close_price=close_price,
        multiplier=mult,
        commission=round(commission, 6),
        pnl_pts=round(pnl_pts, 6),
        pnl_usd=round(pnl_usd, 2) if pnl_usd is not None else None,
        hold_minutes=hold_minutes,
        is_intraday=is_intraday,
        is_win=is_win,
        trade_class=_trade_class(asset_type, is_intraday),
        close_trade_id=close_leg.trade_id,
        week=f"{iso[0]}-W{iso[1]:02d}",
        month=close_leg.trade_date[:7],
        session=_session(open_dt),
        entry_hour=open_dt.hour,
        entry_dow=_entry_dow(open_dt),
        hold_bucket=_hold_bucket(hold_minutes),
        open_trade_id=lot.trade_id,
        order_ref=lot.order_ref,
        open_order_id=lot.order_id,
        close_order_id=close_leg.order_id,
    )


# --- order-id fill coalescing (FR-PIVOT-2c) ---
# IB reports executions (fills); one order can partial-fill into several. A
# trader's "one trade" is one ORDER, not one fill. coalesce_fills merges an
# order's fills into one synthetic leg BEFORE pairing/export, so a multi-fill
# order counts as one round-trip (pivot) and exports as one order-level row
# (MTS, INTERFACE_CONTRACT §5.6 2026-06-02). The fact layer stays raw; this is a
# pure derived-layer projection. Broker-/asset-agnostic: see _instrument_key.

def _instrument_key(row: TradeRow) -> tuple:
    """Asset-class identity for the same-second fallback key (when order_id is
    absent). Futures -> (underlying, expiry); stocks -> (underlying, None)
    (expiry already None). Options later append (strike, put_call) here — the
    single extension point. order_id grouping itself is asset-agnostic."""
    return (row.underlying, row.expiry)


def _coalesce_key(row: TradeRow):
    """Primary key = order_id; fallback (order_id absent) = same-second identity.
    Returns (kind, ...) so OID and heuristic keys never collide."""
    if row.order_id:
        return ("OID", row.order_id)
    return ("HEU", _instrument_key(row), row.buy_sell, row.open_close,
            row.trade_date, row.trade_time)


def _merge_group(group: list[TradeRow]) -> TradeRow:
    """Merge an order's fills into one leg. qty=Σ, price=qty-weighted VWAP,
    commission/fifo=Σ (NULL-safe), time=first(open)/last(close), representative
    trade_id=min(tradeID) (sort-independent → stable annotation key). Full
    precision kept; rounding happens only at output. notes cleared (an aggregated
    order is not a 'partial' fill — drops IB's `P` code)."""
    rep = min(group, key=lambda r: r.trade_id)        # deterministic, order-independent
    total_qty = sum(r.quantity for r in group)        # signed (all same side)
    abs_qty = sum(abs(r.quantity) for r in group)
    if abs_qty == 0:                                   # degenerate (all qty-0) — don't divide
        return rep
    vwap = sum(abs(r.quantity) * r.trade_price for r in group) / abs_qty
    comms = [r.ib_commission for r in group if r.ib_commission is not None]
    commission = sum(comms) if comms else None         # None iff ALL fills None
    fifos = [r.fifo_pnl_realized for r in group if r.fifo_pnl_realized is not None]
    fifo = sum(fifos) if fifos else None
    # open order -> first fill; close order -> last fill (full position lifetime).
    times = sorted(r.trade_time for r in group)
    trade_time = times[0] if rep.open_close == "O" else times[-1]
    return replace(
        rep, quantity=total_qty, trade_price=vwap, ib_commission=commission,
        fifo_pnl_realized=fifo, trade_time=trade_time, notes=None,
    )


def coalesce_fills(rows: Iterable[TradeRow]) -> list[TradeRow]:
    """Group executions of one order into a single leg (FR-PIVOT-2c).

    Pure. Single-fill orders pass through unchanged (object identity), so the
    common case is byte-identical. Groups with mixed (buy_sell, open_close) or
    trade_date are NOT merged (defensive: the 'one order = one side, one day'
    invariant is not guaranteed — flip/GTC-rollover orders) -> emitted per-fill
    with a WARN. A WARN also fires when order_id is absent (heuristic fallback)."""
    buckets: dict = defaultdict(list)
    order = []                                          # preserve first-seen bucket order
    for r in rows:
        k = _coalesce_key(r)
        if k not in buckets:
            order.append(k)
        buckets[k].append(r)

    out: list[TradeRow] = []
    n_heuristic = 0
    n_refused = 0
    for k in order:
        group = buckets[k]
        if len(group) == 1:
            out.append(group[0])
            continue
        if k[0] == "HEU":
            n_heuristic += len(group)
        # Defensive: refuse to merge a group that is not one-side / one-day.
        if len({(r.buy_sell, r.open_close) for r in group}) > 1 or \
           len({r.trade_date for r in group}) > 1:
            n_refused += 1
            out.extend(group)
            continue
        out.append(_merge_group(group))

    if n_heuristic:
        log.warning(
            "[WARN] %d fills had no order_id -> same-second heuristic coalescing; "
            "add ibOrderID/orderID to your Flex Query for accurate cross-minute fills",
            n_heuristic,
        )
    if n_refused:
        log.warning(
            "[WARN] %d order-id group(s) had mixed side/open-close/date -> left un-merged "
            "(unexpected; check the data)", n_refused,
        )
    return out


def pair_round_trips(rows: Iterable[TradeRow]) -> tuple[list[RoundTrip], dict]:
    """FIFO-pair legs into round-trips. Returns (round_trips, stats).

    Grouped by (underlying, expiry). Within a group legs run in chronological
    order; a closing leg consumes the OLDEST open lots first (FIFO), possibly
    splitting across several opens (each split is its own round-trip, carrying
    that lot's open time/price + proportional commission). Unmatched closes
    (position opened before the data window) and still-open lots are counted in
    stats, not emitted.
    """
    by_key: dict[tuple[str, str | None], list[TradeRow]] = defaultdict(list)
    for r in rows:
        by_key[(r.underlying, r.expiry)].append(r)

    round_trips: list[RoundTrip] = []
    unmatched_close_qty = 0
    still_open_qty = 0

    for (underlying, expiry), legs in by_key.items():
        legs.sort(key=lambda r: (r.trade_date, r.trade_time, r.trade_id))
        open_lots: deque[_OpenLot] = deque()
        asset_type = legs[0].asset_type
        for leg in legs:
            qty = abs(leg.quantity)
            if qty == 0:
                continue
            comm_per_unit = (leg.ib_commission or 0.0) / qty
            if leg.open_close == "O":
                direction = "LONG" if leg.buy_sell == "BUY" else "SHORT"
                open_lots.append(
                    _OpenLot(qty, leg.trade_price, leg.trade_date, leg.trade_time,
                             comm_per_unit, direction, leg.trade_id, leg.order_ref,
                             leg.order_id)
                )
            else:  # closing leg
                remaining = qty
                while remaining > 0 and open_lots:
                    lot = open_lots[0]
                    matched = min(remaining, lot.qty)
                    round_trips.append(
                        _build_round_trip(underlying, expiry, asset_type, lot,
                                          leg, matched, comm_per_unit)
                    )
                    lot.qty -= matched
                    remaining -= matched
                    if lot.qty == 0:
                        open_lots.popleft()
                if remaining > 0:
                    unmatched_close_qty += remaining
        still_open_qty += sum(lot.qty for lot in open_lots)

    round_trips.sort(key=lambda rt: (rt.close_date, rt.close_time))
    stats = {
        "round_trips": len(round_trips),
        "unmatched_close_qty": unmatched_close_qty,
        "still_open_qty": still_open_qty,
    }
    return round_trips, stats
