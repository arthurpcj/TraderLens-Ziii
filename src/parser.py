"""Parse IBKR Flex XML -> typed TradeRow records (stdlib xml.etree).

Pure functions, no I/O / no network — directly testable against cached XML.
Handles every real-XML field quirk found in spike 001
(docs/studies/001_flex_connectivity_spike_20260520/RESULTS.md):
  - no `assetCategory` attr -> derive asset_type from `expiry` emptiness
  - `expiry` is YYYYMMDD (kept full in SQLite; exporter truncates to YYYYMM)
  - no `tradeTime` attr -> split `dateTime="YYYYMMDD;HHMMSS"`
  - `quantity` is signed (BUY=+, SELL=-)
  - `fifoPnlRealized` is "0" string on open legs (not absent)
"""

from __future__ import annotations

import logging
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone

from .errors import FlexResponseError

log = logging.getLogger("tradelens.parser")

# --- source profiles: Activity (AF) vs Trade Confirmation (TCF) --------------
# The two Flex statement types carry the SAME data under DIFFERENT element +
# attribute names. VERIFIED against real XML (spike-002, 2026-05-21) — the
# earlier "attribute names are shared" assumption was WRONG:
#     price       tradePrice (AF)         vs  price (TCF)
#     commission  ibCommission (AF)       vs  commission (TCF)
#     open/close  openCloseIndicator (AF) vs  code (TCF, e.g. "O"/"C")
#     realized    fifoPnlRealized (AF)    vs  (absent in TCF)
# Genuinely shared (identical names): tradeID, tradeDate, dateTime,
# underlyingSymbol, buySell, quantity (signed), expiry, multiplier.
@dataclass(frozen=True)
class SourceProfile:
    name: str               # data_source tag: "ACTIVITY" / "CONFIRMATION"
    row_tag: str            # XML element name: "Trade" / "TradeConfirm"
    price_attr: str
    commission_attr: str
    openclose_attr: str
    fifo_attr: str | None   # None when the statement type has no realized-PnL field


ACTIVITY_PROFILE = SourceProfile(
    name="ACTIVITY", row_tag="Trade",
    price_attr="tradePrice", commission_attr="ibCommission",
    openclose_attr="openCloseIndicator", fifo_attr="fifoPnlRealized",
)
CONFIRMATION_PROFILE = SourceProfile(
    name="CONFIRMATION", row_tag="TradeConfirm",
    price_attr="price", commission_attr="commission",
    openclose_attr="code", fifo_attr=None,
)

# Critical attributes — a row without these is unusable (skip the trade).
# Source-specific names (price/openClose) come from the profile; the rest are
# shared. Everything NOT critical is optional -> None when absent (REQUIREMENTS
# §6: "missing IB field -> SQLite NULL, never block write"). Attribute ORDER is irrelevant.
_BASE_CRITICAL_ATTRS = (
    "tradeID",
    "tradeDate",
    "dateTime",
    "underlyingSymbol",
    "buySell",
    "quantity",
)


@dataclass(frozen=True)
class StmtMeta:
    """Per-FlexStatement metadata (one per account)."""

    account_id: str
    from_date: str
    to_date: str
    when_generated: str


@dataclass(frozen=True)
class TradeRow:
    """20-col SQLite row (matches REQUIREMENTS FR-STORE-2 + data_source + order_ref)."""

    # IB native (12)
    trade_id: str
    trade_date: str          # YYYY-MM-DD
    trade_time: str          # HH:MM:SS (US/Eastern, IB wall-clock)
    underlying: str          # NQ/MNQ/ES/MES/... or stock ticker
    expiry: str | None       # YYYYMMDD for futures; None for stocks
    buy_sell: str            # BUY/SELL
    quantity: int            # signed (BUY=+, SELL=-)
    trade_price: float
    multiplier: int | None      # optional: NULL if IB omits (REQUIREMENTS §6)
    ib_commission: float | None  # optional: NULL if IB omits (MTS treats NULL as 0)
    open_close: str          # O/C
    fifo_pnl_realized: float | None  # 0.0 on open legs, value on close; None if absent
    # Derived (1)
    asset_type: str          # FUT (expiry present) / STK (expiry empty)
    # User-labeled (2) — NULL in v1 (no GSheet); exporter fills csv category fixed
    category: str | None
    notes: str | None
    # Audit (3)
    category_set_at: str | None
    row_created_at: str      # ISO 8601 UTC
    source_run_id: str
    # Provenance (1) — which Flex statement type produced this row.
    # Default keeps existing TradeRow constructors (tests) working; the parser
    # always sets it explicitly from the SourceProfile.
    data_source: str = "ACTIVITY"  # "ACTIVITY" (AF, T+1) / "CONFIRMATION" (TCF, same-day)
    # Order reference (1) — Flex `orderReference`, shared by AF + TCF (FR-PIVOT-2b).
    # Backtrader stamps the quant strategy id here; used as the tier-2 setup_tag
    # alias source. Optional (None when absent / manual orders). Default keeps
    # pre-FR-PIVOT TradeRow constructors working.
    order_ref: str | None = None


# --- field conversion helpers (each unit-tested) ---

def parse_trade_date(raw: str) -> str:
    """20260422 -> 2026-04-22."""
    if len(raw) != 8 or not raw.isdigit():
        raise FlexResponseError(f"Unexpected tradeDate format: {raw!r}")
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"


def parse_trade_time(date_time: str) -> str:
    """'20260422;095605' -> '09:56:05' (split ';', format HHMMSS)."""
    if ";" not in date_time:
        raise FlexResponseError(f"Unexpected dateTime format (no ';'): {date_time!r}")
    hhmmss = date_time.split(";", 1)[1]
    if len(hhmmss) != 6 or not hhmmss.isdigit():
        raise FlexResponseError(f"Unexpected time part: {hhmmss!r}")
    return f"{hhmmss[0:2]}:{hhmmss[2:4]}:{hhmmss[4:6]}"


def derive_asset_type(expiry: str | None) -> str:
    """Futures have a contract expiry; stocks do not."""
    return "FUT" if expiry else "STK"


def parse_open_close(raw: str) -> str:
    """Normalize an open/close field to 'O' or 'C'.

    Activity gives a clean 'O'/'C' in openCloseIndicator; Confirmation puts it in
    `code`, which can carry several ';'/space-separated codes (e.g. 'O;P'). We
    pick the open/close token (O wins if both somehow appear). Raises if neither
    is present so the row is skipped rather than stored with a bogus indicator.
    """
    tokens = {t for t in re.split(r"[;,\s]+", raw.strip()) if t}
    if "O" in tokens:
        return "O"
    if "C" in tokens:
        return "C"
    raise FlexResponseError(f"no Open/Close token in {raw!r}")


def _to_signed_int(raw: str) -> int:
    return int(raw)


def _to_float(raw: str) -> float:
    return float(raw)


def _to_opt_int(raw: str | None) -> int | None:
    # multiplier may arrive as "5" or "5.0"; None/"" -> None
    if raw is None or raw == "":
        return None
    return int(float(raw))


def _to_opt_float(raw: str | None) -> float | None:
    if raw is None or raw == "":
        return None
    return float(raw)


def _parse_fifo(raw: str | None) -> float | None:
    if raw is None or raw == "":
        return None
    return float(raw)


def _norm_optional(raw: str | None) -> str | None:
    if raw is None or raw == "":
        return None
    return raw


# --- core parsing ---

def parse_statements(xml_bytes: bytes) -> list[StmtMeta]:
    """Extract per-account FlexStatement metadata (handles count > 1)."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise FlexResponseError(f"Malformed Flex XML: {exc}") from exc
    metas: list[StmtMeta] = []
    for fs in root.iter("FlexStatement"):
        metas.append(
            StmtMeta(
                account_id=fs.get("accountId", ""),
                from_date=fs.get("fromDate", ""),
                to_date=fs.get("toDate", ""),
                when_generated=fs.get("whenGenerated", ""),
            )
        )
    return metas


def parse_trades(
    xml_bytes: bytes,
    *,
    run_id: str | None = None,
    now_utc: str | None = None,
    profile: SourceProfile = ACTIVITY_PROFILE,
) -> list[TradeRow]:
    """Parse all EXECUTION-level rows into typed TradeRows.

    `profile` selects the statement type: ACTIVITY_PROFILE (type=AF,
    <Trades>/<Trade>) or CONFIRMATION_PROFILE (type=TCF, <TradeConfirms>/
    <TradeConfirm>). The two types differ in BOTH element name and attribute
    names (see SourceProfile), so the profile drives row selection *and* field
    extraction. Each row's `data_source` is stamped from the profile.

    Returns ALL trades (futures + stocks) — filtering to NQ/MNQ/ES/MES happens
    at export, not here (FR-FETCH-4: SQLite is a full archive).
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise FlexResponseError(f"Malformed Flex XML: {exc}") from exc

    if run_id is None:
        run_id = str(uuid.uuid4())
    if now_utc is None:
        now_utc = datetime.now(timezone.utc).isoformat()

    n_stmts = sum(1 for _ in root.iter("FlexStatement"))
    if n_stmts > 1:
        # v1 assumes single account; merging is harmless (trade_id globally unique)
        # but flag it so a misconfigured multi-account token is noticed.
        log.warning("Multiple FlexStatements (%d) — merging all accounts' trades", n_stmts)

    rows: list[TradeRow] = []
    skipped = 0
    for el in root.iter(profile.row_tag):
        a = el.attrib
        # Defensive: only execution-level rows (skip any Order/Summary leakage).
        # TCF interleaves <Order> (levelOfDetail=ORDER) with <TradeConfirm>
        # (EXECUTION); iterating row_tag already excludes <Order>, this guards
        # any other aggregate level.
        if a.get("levelOfDetail") not in (None, "EXECUTION"):
            continue
        try:
            rows.append(_build_row(a, profile=profile, run_id=run_id, now_utc=now_utc))
        except FlexResponseError as exc:
            # One bad trade must not lose the whole batch (REQUIREMENTS §6).
            # Skip + warn; Last-30-Days re-fetch picks it up if IB later fixes it.
            skipped += 1
            log.warning("Skipping malformed %s (tradeID=%s): %s",
                        profile.row_tag, a.get("tradeID"), exc)
    if skipped:
        log.warning("Parsed %d trades, skipped %d malformed", len(rows), skipped)
    return rows


def _build_row(a: dict, *, profile: SourceProfile, run_id: str, now_utc: str) -> TradeRow:
    """Build one TradeRow using `profile` for source-specific attribute names.
    Raises FlexResponseError if a critical attr is missing or unparseable
    (caller skips that trade)."""
    critical = (*_BASE_CRITICAL_ATTRS, profile.price_attr, profile.openclose_attr)
    missing = [k for k in critical if not a.get(k)]
    if missing:
        raise FlexResponseError(f"missing critical attrs {missing}")
    try:
        expiry = _norm_optional(a.get("expiry"))
        fifo_raw = a.get(profile.fifo_attr) if profile.fifo_attr else None
        return TradeRow(
            trade_id=a["tradeID"],
            trade_date=parse_trade_date(a["tradeDate"]),
            trade_time=parse_trade_time(a["dateTime"]),
            underlying=a["underlyingSymbol"],
            expiry=expiry,
            buy_sell=a["buySell"],
            quantity=_to_signed_int(a["quantity"]),
            trade_price=_to_float(a[profile.price_attr]),
            multiplier=_to_opt_int(a.get("multiplier")),               # optional -> None
            ib_commission=_to_opt_float(a.get(profile.commission_attr)),  # optional -> None
            open_close=parse_open_close(a[profile.openclose_attr]),
            fifo_pnl_realized=_parse_fifo(fifo_raw),  # TCF has none -> None
            asset_type=derive_asset_type(expiry),
            category=None,
            notes=_norm_optional(a.get("notes")),
            category_set_at=None,
            row_created_at=now_utc,
            source_run_id=run_id,
            data_source=profile.name,
            order_ref=_norm_optional(a.get("orderReference")),  # shared AF/TCF attr
        )
    except (ValueError, TypeError) as exc:
        # e.g. non-numeric quantity/price/commission
        raise FlexResponseError(f"unparseable value: {exc}") from exc
