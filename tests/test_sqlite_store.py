"""Step 2 tests — SQLite schema, idempotent upsert, export query."""

from __future__ import annotations

import pytest

from src import sqlite_store
from src.constants import TARGET_UNDERLYINGS
from src.parser import parse_trades


@pytest.fixture
def conn():
    c = sqlite_store.connect(":memory:")
    sqlite_store.init_schema(c)
    yield c
    c.close()


@pytest.fixture
def rows(sample_xml_bytes):
    return parse_trades(sample_xml_bytes, run_id="RUN1", now_utc="2026-05-20T00:00:00+00:00")


def test_schema_has_20_columns(conn):
    cols = conn.execute("PRAGMA table_info(trades)").fetchall()
    assert len(cols) == 20  # 18 + data_source (spike-002) + order_ref (FR-PIVOT-2b)
    names = {c["name"] for c in cols}
    assert "asset_type" in names
    assert "data_source" in names
    assert "order_ref" in names
    # expiry nullable (stocks), fifo_pnl_realized nullable
    by_name = {c["name"]: c for c in cols}
    assert by_name["expiry"]["notnull"] == 0
    assert by_name["trade_id"]["pk"] == 1


def test_upsert_inserts_all(conn, rows):
    stats = sqlite_store.upsert_trades(conn, rows)
    assert stats.attempted == 33
    assert stats.inserted == 33
    assert stats.ignored_dupes == 0
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 33


def test_upsert_idempotent(conn, rows):
    sqlite_store.upsert_trades(conn, rows)
    stats2 = sqlite_store.upsert_trades(conn, rows)  # re-run same data
    assert stats2.inserted == 0
    assert stats2.ignored_dupes == 33
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 33


def test_query_for_export_filters_futures_and_targets(conn, rows):
    sqlite_store.upsert_trades(conn, rows)
    # All target trades in fixture are dated across the window; pick one known date.
    exported = sqlite_store.query_for_export(conn, "2026-04-22", TARGET_UNDERLYINGS)
    assert exported  # at least the MES pair on 2026-04-22
    assert all(r.asset_type == "FUT" for r in exported)
    assert all(r.underlying in TARGET_UNDERLYINGS for r in exported)
    assert all(r.trade_date == "2026-04-22" for r in exported)


def test_query_for_export_excludes_stocks_and_other_futures(conn, rows):
    sqlite_store.upsert_trades(conn, rows)
    # gather all exported across every date present
    dates = {r.trade_date for r in rows}
    exported_underlyings = set()
    for d in dates:
        for r in sqlite_store.query_for_export(conn, d, TARGET_UNDERLYINGS):
            exported_underlyings.add(r.underlying)
    assert exported_underlyings <= set(TARGET_UNDERLYINGS)
    assert "FMCC" not in exported_underlyings  # stock excluded
    assert "M6B" not in exported_underlyings    # other future excluded
    assert "MHG" not in exported_underlyings


def test_query_all_returns_full_archive(conn, rows):
    sqlite_store.upsert_trades(conn, rows)
    allrows = sqlite_store.query_all(conn)
    assert len(allrows) == 33  # includes stocks + non-target futures


def test_roundtrip_preserves_fields(conn, rows):
    sqlite_store.upsert_trades(conn, rows)
    allrows = {r.trade_id: r for r in sqlite_store.query_all(conn)}
    src = {r.trade_id: r for r in rows}
    t = allrows["1216416114"]
    assert t == src["1216416114"]  # full dataclass equality after round-trip


def test_migrate_adds_order_ref_to_old_db():
    """A DB created before order_ref existed gets the column back-filled to NULL,
    and re-running init_schema is idempotent (FR-PIVOT-2b migration)."""
    c = sqlite_store.connect(":memory:")
    # pre-FR-PIVOT schema: same table minus order_ref.
    c.execute(
        "CREATE TABLE trades (trade_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, "
        "trade_time TEXT NOT NULL, underlying TEXT NOT NULL, expiry TEXT, "
        "buy_sell TEXT NOT NULL, quantity INTEGER NOT NULL, trade_price REAL NOT NULL, "
        "multiplier INTEGER, ib_commission REAL, open_close TEXT NOT NULL, "
        "fifo_pnl_realized REAL, asset_type TEXT NOT NULL, category TEXT, notes TEXT, "
        "category_set_at TEXT, row_created_at TEXT NOT NULL, source_run_id TEXT NOT NULL, "
        "data_source TEXT NOT NULL DEFAULT 'ACTIVITY')"
    )
    c.execute(
        "INSERT INTO trades (trade_id, trade_date, trade_time, underlying, buy_sell, "
        "quantity, trade_price, open_close, asset_type, row_created_at, source_run_id) "
        "VALUES ('OLD1','2026-05-01','09:30:00','MES','BUY',1,7000.0,'O','FUT','z','r')"
    )
    c.commit()
    sqlite_store.init_schema(c)            # runs _migrate
    sqlite_store.init_schema(c)            # idempotent — must not raise
    cols = {r["name"] for r in c.execute("PRAGMA table_info(trades)")}
    assert "order_ref" in cols
    assert c.execute("SELECT order_ref FROM trades WHERE trade_id='OLD1'").fetchone()[0] is None
    c.close()
