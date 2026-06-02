"""Export SQLite -> 12-col v1.0 csv for the MTS project (FR-EXPORT).

State machine (INTERFACE_CONTRACT 搂5.6 v1.1 2026-05-26, see DATA_ARCHITECTURE 搂5):
  State A 鈥?no IB_Sync setup_tag annotation touches this trade_date
            鈫?all target-future legs for the date, category=PAPER_AUTO
            鈫?MTS routes 0-candidate to DC default-skip (scheme-E behavior)
  State B 鈥?at least one round-trip touching this date has a resolved
            setup_tag 鈭?MTS_RELEVANT_SETUPS (currently {Q_intraday})
            鈫?only legs from those MTS-confirmed round-trips, category=MTS_CONFIRMED
            鈫?MTS routes 0-candidate to FORCE_WRITTEN with alert

State is decided independently per trade_date. csv schema is still v1.0 frozen
(12 cols); only the content scope + category value change with state.

CLI: python -m src.exporter --date YYYY-MM-DD
     python -m src.exporter --lookback 90        (re-export recent dates)
     python -m src.exporter --lookback all       (re-export everything)
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from . import annotations as annotations_mod
from . import sqlite_store
from .annotations import resolve_setup_tag
from .constants import (
    CSV_CATEGORY_MTS_CONFIRMED,
    CSV_CATEGORY_PAPER_AUTO,
    CSV_COLUMNS,
    DEFAULT_EXPORT_LOOKBACK_DAYS,
    EXPORT_DIR,
    MTS_RELEVANT_SETUPS,
    SQLITE_PATH,
    TARGET_UNDERLYINGS,
)
from .parser import TradeRow
from .roundtrip import RoundTrip, coalesce_fills, pair_round_trips


@dataclass(frozen=True)
class ExportStats:
    """Per-date export result. `state` is 'A' or 'B'; `category` mirrors it."""

    date: str
    path: Path
    state: str                # 'A' or 'B'
    category: str             # PAPER_AUTO (A) or MTS_CONFIRMED (B)
    exported_rows: int
    open_legs: int
    close_legs: int
    stocks_skipped: int
    other_futures_skipped: int

    def summary(self) -> str:
        return (
            f"exported {self.exported_rows} rows "
            f"({self.open_legs} open + {self.close_legs} close legs, NQ/MNQ/ES/MES only; "
            f"State {self.state} / category={self.category}; "
            f"{self.stocks_skipped} stocks + {self.other_futures_skipped} other-futures skipped)"
        )


def to_csv_record(row: TradeRow, category: str) -> dict[str, str]:
    """Apply the v1.0 csv contract transforms to one SQLite row.

    `category` is decided per trade_date by the state machine (PAPER_AUTO for
    State A, MTS_CONFIRMED for State B). All rows in one csv file share the
    same category (state is a per-date attribute)."""
    if not row.expiry:
        # Guard: stocks must be filtered before export (col #5 is NOT NULL).
        raise ValueError(f"export row {row.trade_id} has empty expiry (not a future)")
    return {
        "trade_id": row.trade_id,
        "trade_date": row.trade_date,
        "trade_time": row.trade_time,
        "underlying": row.underlying,
        "expiry": row.expiry[:6],            # YYYYMMDD -> YYYYMM (csv v1.0 contract)
        "buy_sell": row.buy_sell,
        "quantity": str(abs(row.quantity)),  # unsigned per 搂2.3 #7
        "trade_price": f"{row.trade_price:.2f}",
        # NULL commission (rare IB omission) -> empty string; MTS treats as 0 (REQUIREMENTS 搂6)
        "ib_commission": "" if row.ib_commission is None else f"{row.ib_commission:.2f}",
        "open_close": row.open_close,
        "category": category,                 # state-machine driven (C7)
        # notes = USER notes only (搂5.6 C16). row.notes carries IB trade-CODES
        # (O/C/P) 鈥?never export those into the user-notes column. v1 has no
        # user-notes source (GSheet is v2), so this is empty. The `P` partial
        # code is used internally for verification (coalesce), not shipped.
        "notes": "",
    }


def write_csv(path: Path, records: list[dict[str, str]]) -> None:
    """Write 12-col csv: UTF-8 (no BOM), LF line endings, RFC 4180.

    Always writes the file (header always written), even with zero records 鈥?
    a header-only csv is valid per INTERFACE_CONTRACT 搂5.6 C10 (MTS silently
    exits 0 on it; State B with all RTs filtered out / non-trading day)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=list(CSV_COLUMNS), lineterminator="\n", quoting=csv.QUOTE_MINIMAL
        )
        writer.writeheader()
        writer.writerows(records)


def _confirmed_leg_ids(
    round_trips: list[RoundTrip],
    annotations: dict,
    tag_config,
) -> tuple[set[str], set[str]]:
    """Walk all round-trips, resolve setup_tag, collect leg trade_ids belonging
    to MTS-confirmed RTs. Returns (open_trade_ids, close_trade_ids).

    A single close leg can split across multiple opens (FIFO), so its key
    may appear in many RTs. We deduplicate by collecting into sets 鈥?if ANY
    RT containing that close leg is MTS-confirmed, the close leg goes to csv.

    Keys on the ORDER id (FR-PIVOT-2c / agent BUG-1), falling back to the
    representative trade_id when order_id is absent. The order id is stable
    across the full-set vs per-date coalescing paths (a cross-date order is
    refused by the full-set merge but merged per-date 鈥?keying on the
    representative trade_id would then mismatch and silently drop the order)."""
    open_ids: set[str] = set()
    close_ids: set[str] = set()
    for rt in round_trips:
        tag = resolve_setup_tag(rt.open_trade_id, rt.order_ref, annotations, tag_config)
        if tag in MTS_RELEVANT_SETUPS:
            open_ids.add(rt.open_order_id or rt.open_trade_id)
            close_ids.add(rt.close_order_id or rt.close_trade_id)
    return open_ids, close_ids


def _dates_touched_by_confirmed_rts(
    round_trips: list[RoundTrip],
    annotations: dict,
    tag_config,
) -> set[str]:
    """Trade dates that at least one MTS-confirmed round-trip touches (via
    open_date or close_date). These dates flip to State B."""
    dates: set[str] = set()
    for rt in round_trips:
        tag = resolve_setup_tag(rt.open_trade_id, rt.order_ref, annotations, tag_config)
        if tag in MTS_RELEVANT_SETUPS:
            dates.add(rt.open_date)
            dates.add(rt.close_date)
    return dates


def export_date(
    conn,
    date_str: str,
    export_dir: Path = EXPORT_DIR,
    underlyings: tuple[str, ...] = TARGET_UNDERLYINGS,
    round_trips: list[RoundTrip] | None = None,
    annotations: dict | None = None,
    tag_config=None,
) -> ExportStats:
    """Export one trade_date's csv per the state machine.

    Optional `round_trips` / `annotations` / `tag_config` let the caller pair
    once across the lookback window and pass the same objects to many
    export_date calls (avoids re-pairing the whole SQLite per date). If
    omitted, this function pairs/loads for itself (single-date convenience)."""
    if annotations is None:
        annotations = annotations_mod.load_annotations()
    if tag_config is None:
        tag_config = annotations_mod.load_tag_config()
    if round_trips is None:
        # Single-date convenience: pair across all target-future legs (paper-scale
        # SQLite is small enough that pairing-all is cheap).
        all_legs = [
            r for r in sqlite_store.query_all(conn)
            if r.asset_type == "FUT" and r.underlying in underlyings
        ]
        round_trips, _ = pair_round_trips(coalesce_fills(all_legs))  # FR-PIVOT-2c

    confirmed_dates = _dates_touched_by_confirmed_rts(round_trips, annotations, tag_config)
    state = "B" if date_str in confirmed_dates else "A"
    category = CSV_CATEGORY_MTS_CONFIRMED if state == "B" else CSV_CATEGORY_PAPER_AUTO

    # FR-PIVOT-2c: coalesce this date's fills into order-level legs (one row per
    # order). Deterministic min(tradeID) representative matches the ids in
    # round_trips (also coalesced), so the State-B filter aligns across paths.
    date_legs = coalesce_fills(sqlite_store.query_for_export(conn, date_str, underlyings))
    if state == "A":
        # State A 鈥?scheme-E behavior: all target-future ORDERS for this date.
        target_legs = date_legs
    else:
        # State B 鈥?only orders from MTS-confirmed RTs that fall on this date.
        open_ids, close_ids = _confirmed_leg_ids(round_trips, annotations, tag_config)
        # Match on the order id (fallback to representative trade_id), aligned
        # with _confirmed_leg_ids 鈥?see BUG-1 note there.
        target_legs = [
            r for r in date_legs
            if (r.open_close == "O" and (r.order_id or r.trade_id) in open_ids)
            or (r.open_close == "C" and (r.order_id or r.trade_id) in close_ids)
        ]

    records = [to_csv_record(r, category) for r in target_legs]
    path = export_dir / f"mts_trades_{date_str}.csv"
    write_csv(path, records)

    # Stats: partition the full date for skipped counts.
    all_for_date = sqlite_store.query_by_date(conn, date_str)
    stocks = sum(1 for r in all_for_date if r.asset_type == "STK")
    other_fut = sum(
        1 for r in all_for_date if r.asset_type == "FUT" and r.underlying not in underlyings
    )
    return ExportStats(
        date=date_str,
        path=path,
        state=state,
        category=category,
        exported_rows=len(records),
        open_legs=sum(1 for r in target_legs if r.open_close == "O"),
        close_legs=sum(1 for r in target_legs if r.open_close == "C"),
        stocks_skipped=stocks,
        other_futures_skipped=other_fut,
    )


def export_lookback(
    conn,
    lookback_days: int | None = DEFAULT_EXPORT_LOOKBACK_DAYS,
    export_dir: Path = EXPORT_DIR,
    underlyings: tuple[str, ...] = TARGET_UNDERLYINGS,
    today: date | None = None,
) -> list[ExportStats]:
    """Re-export csv for the last `lookback_days` trade_dates (C8/C9 contract).

    Pairs once across the full SQLite, then calls export_date per date sharing
    the pairing 鈥?avoids O(N虏) re-pairing per date. `lookback_days=None` is the
    'all' mode (every distinct trade_date in SQLite).

    Each date in the window gets a csv written, even if empty (header-only per
    C10) 鈥?wrapper.bat can blind-loop the same window when calling MTS."""
    annotations = annotations_mod.load_annotations()
    tag_config = annotations_mod.load_tag_config()

    all_legs = [
        r for r in sqlite_store.query_all(conn)
        if r.asset_type == "FUT" and r.underlying in underlyings
    ]
    round_trips, _ = pair_round_trips(coalesce_fills(all_legs))  # FR-PIVOT-2c

    # Decide which trade_dates to export.
    today = today or date.today()
    if lookback_days is None:                                 # 'all' mode
        # Every distinct trade_date that has any target future in SQLite.
        all_dates = sorted({r.trade_date for r in all_legs})
    else:
        cutoff = today - timedelta(days=lookback_days)
        # Every calendar day in the window (so wrapper sees a file per day,
        # header-only on non-trading days).
        all_dates = [(cutoff + timedelta(days=i)).isoformat()
                     for i in range(lookback_days + 1)]

    return [
        export_date(conn, d, export_dir, underlyings,
                    round_trips=round_trips, annotations=annotations, tag_config=tag_config)
        for d in all_dates
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.exporter")
    mx = parser.add_mutually_exclusive_group(required=True)
    mx.add_argument("--date", help="single trade date YYYY-MM-DD")
    mx.add_argument("--lookback", help="re-export last N trade_dates (or 'all')")
    parser.add_argument("--db", default=str(SQLITE_PATH), help="SQLite path")
    parser.add_argument("--export-dir", default=str(EXPORT_DIR), help="csv output dir")
    args = parser.parse_args(argv)

    conn = sqlite_store.connect(args.db)
    try:
        sqlite_store.init_schema(conn)
        if args.date:
            stats_list = [export_date(conn, args.date, Path(args.export_dir))]
        else:
            lb = None if args.lookback.lower() == "all" else int(args.lookback)
            stats_list = export_lookback(conn, lb, Path(args.export_dir))
    finally:
        conn.close()
    for s in stats_list:
        print(f"{s.summary()} -> {s.path}")
    print(f"[OK] {len(stats_list)} csv files written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
