"""SQLite persistence — 18-col full archive + idempotent upsert (FR-STORE).

trade_id PRIMARY KEY + INSERT OR IGNORE => running N times/day converges.
SQLite archives ALL trades (futures + stocks); export-stage filtering is in
exporter.py (FR-FETCH-4: filter moved FETCH -> EXPORT).
"""

from __future__ import annotations

import sqlite3
from dataclasses import astuple, dataclass, fields
from pathlib import Path
from typing import Iterable

from .parser import TradeRow

# Column order = TradeRow field order. Single source so insert/select stay aligned.
_COLUMNS: tuple[str, ...] = tuple(f.name for f in fields(TradeRow))

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id           TEXT PRIMARY KEY,
    trade_date         TEXT NOT NULL,
    trade_time         TEXT NOT NULL,
    underlying         TEXT NOT NULL,
    expiry             TEXT,
    buy_sell           TEXT NOT NULL,
    quantity           INTEGER NOT NULL,
    trade_price        REAL NOT NULL,
    multiplier         INTEGER,
    ib_commission      REAL,
    open_close         TEXT NOT NULL,
    fifo_pnl_realized  REAL,
    asset_type         TEXT NOT NULL,
    category           TEXT,
    notes              TEXT,
    category_set_at    TEXT,
    row_created_at     TEXT NOT NULL,
    source_run_id      TEXT NOT NULL,
    data_source        TEXT NOT NULL DEFAULT 'ACTIVITY',
    order_ref          TEXT
);
"""

_CREATE_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_trades_date_underlying "
    "ON trades (trade_date, underlying, asset_type);"
)


@dataclass(frozen=True)
class UpsertStats:
    attempted: int
    inserted: int
    ignored_dupes: int


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection (WAL, Row factory). Use ':memory:' for tests."""
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if db_path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_TABLE)
    conn.execute(_CREATE_INDEX)
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive, idempotent migrations for DBs created before a column existed.

    `data_source` (spike-002): pre-existing rows were all Activity-fed, so the
    NOT NULL DEFAULT 'ACTIVITY' backfills them correctly.

    `order_ref` (FR-PIVOT-2b): nullable, backfills to NULL on old rows (which
    have no orderReference recorded) — they fall through to the `untagged`
    setup_tag tier, which is correct.
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(trades)")}
    if "data_source" not in cols:
        conn.execute(
            "ALTER TABLE trades ADD COLUMN data_source TEXT NOT NULL DEFAULT 'ACTIVITY'"
        )
    if "order_ref" not in cols:
        conn.execute("ALTER TABLE trades ADD COLUMN order_ref TEXT")


def upsert_trades(conn: sqlite3.Connection, rows: Iterable[TradeRow]) -> UpsertStats:
    """INSERT OR IGNORE on trade_id. Returns insert/dedup stats."""
    rows = list(rows)
    placeholders = ", ".join("?" for _ in _COLUMNS)
    sql = f"INSERT OR IGNORE INTO trades ({', '.join(_COLUMNS)}) VALUES ({placeholders})"
    before = conn.total_changes
    conn.executemany(sql, [astuple(r) for r in rows])
    conn.commit()
    inserted = conn.total_changes - before
    attempted = len(rows)
    return UpsertStats(attempted=attempted, inserted=inserted, ignored_dupes=attempted - inserted)


def _row_to_traderow(row: sqlite3.Row) -> TradeRow:
    return TradeRow(**{col: row[col] for col in _COLUMNS})


def query_for_export(
    conn: sqlite3.Connection, date: str, underlyings: Iterable[str]
) -> list[TradeRow]:
    """Futures-only, target-underlying rows for a single trade date (export filter)."""
    unders = tuple(underlyings)
    if not unders:
        return []
    marks = ", ".join("?" for _ in unders)
    sql = (
        f"SELECT * FROM trades "
        f"WHERE trade_date = ? AND asset_type = 'FUT' AND underlying IN ({marks}) "
        f"ORDER BY trade_time, trade_id"
    )
    cur = conn.execute(sql, (date, *unders))
    return [_row_to_traderow(r) for r in cur.fetchall()]


def query_by_date(conn: sqlite3.Connection, date: str) -> list[TradeRow]:
    """All archived trades for one date (any asset type) — used for export stats."""
    cur = conn.execute(
        "SELECT * FROM trades WHERE trade_date = ? ORDER BY trade_time, trade_id", (date,)
    )
    return [_row_to_traderow(r) for r in cur.fetchall()]


def distinct_export_dates(
    conn: sqlite3.Connection, from_date: str, to_date: str, underlyings: Iterable[str]
) -> list[str]:
    """Trade dates in [from, to] that have >=1 target future (for auto-export)."""
    unders = tuple(underlyings)
    if not unders:
        return []
    marks = ", ".join("?" for _ in unders)
    sql = (
        f"SELECT DISTINCT trade_date FROM trades "
        f"WHERE asset_type = 'FUT' AND underlying IN ({marks}) "
        f"AND trade_date BETWEEN ? AND ? ORDER BY trade_date"
    )
    cur = conn.execute(sql, (*unders, from_date, to_date))
    return [r["trade_date"] for r in cur.fetchall()]


def query_all(conn: sqlite3.Connection) -> list[TradeRow]:
    """All archived trades (P2 pivot hook)."""
    cur = conn.execute("SELECT * FROM trades ORDER BY trade_date, trade_time, trade_id")
    return [_row_to_traderow(r) for r in cur.fetchall()]
