"""Step 3 tests — csv export contract (12 cols, encoding, filtering, stats).

Covers AC-2 (csv schema), AC-4 (scheme-E contents), AC-9 (leg-level).
"""

from __future__ import annotations

import csv

import pytest

from src import exporter, sqlite_store
from src.constants import CSV_COLUMNS
from src.parser import parse_trades


@pytest.fixture
def conn(sample_xml_bytes):
    c = sqlite_store.connect(":memory:")
    sqlite_store.init_schema(c)
    rows = parse_trades(sample_xml_bytes, run_id="RUN1", now_utc="2026-05-20T00:00:00+00:00")
    sqlite_store.upsert_trades(c, rows)
    yield c
    c.close()


def test_export_produces_file(conn, tmp_path):
    stats = exporter.export_date(conn, "2026-04-22", tmp_path)
    assert stats.path.exists()
    assert stats.exported_rows > 0


def test_csv_header_exact_12_columns(conn, tmp_path):
    exporter.export_date(conn, "2026-04-22", tmp_path)
    content = (tmp_path / "mts_trades_2026-04-22.csv").read_text(encoding="utf-8")
    header = content.splitlines()[0]
    assert header == ",".join(CSV_COLUMNS)
    assert len(CSV_COLUMNS) == 12


def test_csv_encoding_lf_no_bom(conn, tmp_path):
    exporter.export_date(conn, "2026-04-22", tmp_path)
    raw = (tmp_path / "mts_trades_2026-04-22.csv").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")  # no BOM
    assert b"\r\n" not in raw  # LF only, no CRLF
    assert b"\n" in raw


def test_csv_transforms(conn, tmp_path):
    exporter.export_date(conn, "2026-04-22", tmp_path)
    with open(tmp_path / "mts_trades_2026-04-22.csv", encoding="utf-8", newline="") as fh:
        records = list(csv.DictReader(fh))
    rec = next(r for r in records if r["trade_id"] == "1216416114")
    assert rec["expiry"] == "202606"          # YYYYMMDD -> YYYYMM
    assert rec["quantity"] == "1"             # unsigned
    assert rec["trade_price"] == "7148.00"    # 2 decimals
    assert rec["ib_commission"] == "-0.62"
    assert rec["category"] == "PAPER_AUTO"    # fixed value (scheme E)
    # SELL leg quantity also unsigned
    sell = next(r for r in records if r["trade_id"] == "1216419347")
    assert sell["quantity"] == "1"
    assert sell["buy_sell"] == "SELL"


def test_export_excludes_stocks_and_other_futures(conn, tmp_path):
    # Use a date that has a stock (FMCC) and/or other-future trade.
    # Aggregate across all dates: exported underlyings must be subset of targets.
    allrows = sqlite_store.query_all(conn)
    dates = {r.trade_date for r in allrows}
    exported_unders = set()
    total_stocks_skipped = 0
    total_other_skipped = 0
    for d in dates:
        stats = exporter.export_date(conn, d, tmp_path)
        total_stocks_skipped += stats.stocks_skipped
        total_other_skipped += stats.other_futures_skipped
        with open(tmp_path / f"mts_trades_{d}.csv", encoding="utf-8", newline="") as fh:
            for rec in csv.DictReader(fh):
                exported_unders.add(rec["underlying"])
    assert exported_unders <= {"NQ", "MNQ", "ES", "MES"}
    assert total_stocks_skipped == 2          # 2 FMCC
    assert total_other_skipped == 5           # 3 M6B + 2 MHG (EXECUTION level)


def test_leg_level_pairing_not_done(conn, tmp_path):
    # AC-9: open + close are separate rows (MTS pairs, not ib_sync).
    stats = exporter.export_date(conn, "2026-04-22", tmp_path)
    assert stats.open_legs >= 1
    assert stats.close_legs >= 1


def test_null_commission_exports_empty(tmp_path):
    # A target future with NULL ib_commission (IB omitted it) -> empty csv field.
    from dataclasses import replace
    from src.parser import TradeRow

    row = TradeRow(
        trade_id="X1", trade_date="2026-04-22", trade_time="09:00:00", underlying="MES",
        expiry="20260618", buy_sell="BUY", quantity=1, trade_price=7000.0,
        multiplier=None, ib_commission=None, open_close="O", fifo_pnl_realized=None,
        asset_type="FUT", category=None, notes=None, category_set_at=None,
        row_created_at="2026-05-20T00:00:00+00:00", source_run_id="R",
    )
    c = sqlite_store.connect(":memory:")
    sqlite_store.init_schema(c)
    sqlite_store.upsert_trades(c, [row])
    exporter.export_date(c, "2026-04-22", tmp_path)
    c.close()
    with open(tmp_path / "mts_trades_2026-04-22.csv", encoding="utf-8", newline="") as fh:
        rec = next(csv.DictReader(fh))
    assert rec["ib_commission"] == ""  # NULL -> empty string (RFC4180, MTS treats as 0)


def test_stats_summary_string(conn, tmp_path):
    stats = exporter.export_date(conn, "2026-04-22", tmp_path)
    s = stats.summary()
    assert "exported" in s and "skipped" in s


def test_no_target_trades_writes_header_only(conn, tmp_path):
    # A date with no target futures -> header-only csv (signals 'no trades' vs 'missing file').
    stats = exporter.export_date(conn, "1999-01-01", tmp_path)
    assert stats.exported_rows == 0
    content = (tmp_path / "mts_trades_1999-01-01.csv").read_text(encoding="utf-8")
    assert content.strip() == ",".join(CSV_COLUMNS)  # header only, no data rows
