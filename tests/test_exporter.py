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


# --- order-id fill aggregation (FR-PIVOT-2c / INTERFACE_CONTRACT §5.6 C13-C16) ---

def _frow(tid, date, time, bs, qty, price, oc, *, oid, comm=-0.62, notes=None):
    from src.parser import TradeRow
    return TradeRow(
        trade_id=tid, trade_date=date, trade_time=time, underlying="MES",
        expiry="20260618", buy_sell=bs, quantity=qty, trade_price=price,
        multiplier=5, ib_commission=comm, open_close=oc, fifo_pnl_realized=None,
        asset_type="FUT", category=None, notes=notes, category_set_at=None,
        row_created_at="2026-05-20T00:00:00+00:00", source_run_id="R",
        order_id=oid,
    )


def _csv_rows(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_export_order_level_aggregation_state_a(tmp_path):
    # A 2-lot entry that partial-filled as 1+1 (same order_id, cross-minute) +
    # a 2-lot exit -> ONE open row + ONE close row, qty=2, VWAP, summed comm.
    rows = [
        _frow("A2", "2026-05-25", "09:31:18", "BUY", 1, 20100.50, "O", oid="OE", comm=-0.62),
        _frow("A1", "2026-05-25", "09:31:15", "BUY", 1, 20100.25, "O", oid="OE", comm=-0.62),
        _frow("C1", "2026-05-25", "15:02:40", "SELL", -2, 20180.00, "C", oid="OX", comm=-1.24),
    ]
    c = sqlite_store.connect(":memory:"); sqlite_store.init_schema(c)
    sqlite_store.upsert_trades(c, rows)
    exporter.export_date(c, "2026-05-25", tmp_path); c.close()
    recs = _csv_rows(tmp_path / "mts_trades_2026-05-25.csv")
    assert len(recs) == 2                          # 2 fills collapsed -> 1 open + 1 close
    op = next(r for r in recs if r["open_close"] == "O")
    assert op["quantity"] == "2"                   # order total
    assert op["trade_price"] == "20100.38"         # qty-weighted VWAP (rounded 2dp)
    assert op["ib_commission"] == "-1.24"          # summed
    assert op["trade_time"] == "09:31:15"          # open -> first fill
    assert op["trade_id"] == "A1"                  # representative = min(tradeID)


def test_export_single_fill_unchanged(tmp_path):
    # The ~99% case: single-fill order -> byte-identical to pre-change (qty 1).
    rows = [_frow("S1", "2026-05-25", "10:00:00", "BUY", 1, 7000.0, "O", oid="O1")]
    c = sqlite_store.connect(":memory:"); sqlite_store.init_schema(c)
    sqlite_store.upsert_trades(c, rows)
    exporter.export_date(c, "2026-05-25", tmp_path); c.close()
    rec = _csv_rows(tmp_path / "mts_trades_2026-05-25.csv")[0]
    assert rec["quantity"] == "1" and rec["trade_id"] == "S1"


def test_export_notes_never_carries_ib_codes(tmp_path):
    # row.notes carries IB trade-codes ('P'); the csv user-notes column must be empty (C16).
    rows = [_frow("P1", "2026-05-25", "10:00:00", "BUY", 1, 7000.0, "O", oid="O1", notes="P")]
    c = sqlite_store.connect(":memory:"); sqlite_store.init_schema(c)
    sqlite_store.upsert_trades(c, rows)
    exporter.export_date(c, "2026-05-25", tmp_path); c.close()
    assert _csv_rows(tmp_path / "mts_trades_2026-05-25.csv")[0]["notes"] == ""


def test_export_state_b_multifill_keeps_full_order(tmp_path):
    # AGENT C-1/C-2: a confirmed (Q_intraday) multi-fill order must export the
    # WHOLE order (qty=total), no fill dropped, via representative-id matching.
    from src.annotations import Annotation, TagConfig
    rows = [
        _frow("A2", "2026-05-25", "09:31:18", "BUY", 1, 20100.50, "O", oid="OE"),
        _frow("A1", "2026-05-25", "09:31:15", "BUY", 1, 20100.25, "O", oid="OE"),
        _frow("C9", "2026-05-25", "15:02:40", "SELL", -2, 20180.00, "C", oid="OX"),
    ]
    c = sqlite_store.connect(":memory:"); sqlite_store.init_schema(c)
    sqlite_store.upsert_trades(c, rows)
    # tag the representative open id (min tradeID = "A1") as Q_intraday
    annots = {"A1": Annotation(setup_tag="Q_intraday", score=None, notes=None)}
    stats = exporter.export_date(c, "2026-05-25", tmp_path,
                                 annotations=annots, tag_config=TagConfig({}, {}))
    c.close()
    assert stats.category == "MTS_CONFIRMED"       # State B
    recs = _csv_rows(tmp_path / "mts_trades_2026-05-25.csv")
    op = next(r for r in recs if r["open_close"] == "O")
    assert op["quantity"] == "2"                   # full order qty — NO fill dropped
    assert op["trade_id"] == "A1"


def test_planned_stop_never_leaks_to_mts_csv(tmp_path):
    # T-EXP-1 (FR-PIVOT-10 boundary): planned_stop is an internal annotation — it
    # must NEVER reach the frozen 12-col MTS csv. The exporter reads only
    # setup_tag, so the contract is structurally safe; this locks it as a
    # regression guard against a future "dump the whole annotation" mistake.
    from src.annotations import Annotation, TagConfig
    rows = [
        _frow("A1", "2026-05-25", "09:31:15", "BUY", 1, 20100.25, "O", oid="OE"),
        _frow("C9", "2026-05-25", "15:02:40", "SELL", -1, 20180.00, "C", oid="OX"),
    ]
    c = sqlite_store.connect(":memory:"); sqlite_store.init_schema(c)
    sqlite_store.upsert_trades(c, rows)
    annots = {"A1": Annotation(setup_tag="Q_intraday", score="8", notes="x",
                               planned_stop="20050.0")}
    exporter.export_date(c, "2026-05-25", tmp_path,
                         annotations=annots, tag_config=TagConfig({}, {}))
    c.close()
    content = (tmp_path / "mts_trades_2026-05-25.csv").read_text(encoding="utf-8")
    assert content.splitlines()[0] == ",".join(CSV_COLUMNS) and len(CSV_COLUMNS) == 12
    assert "planned_stop" not in content
    assert "20050" not in content                  # the stop value never appears


def test_export_state_b_cross_date_order_not_dropped(tmp_path):
    # AGENT BUG-1: an order whose fills span two trade_dates (GTC across ET
    # midnight) is REFUSED by the full-set coalesce but MERGED per-date. Keying
    # State-B on order_id (not the representative trade_id) keeps it. Here the
    # confirmed RT's open leg is B9 (earliest time) but the per-date merged
    # representative is min(B1,B9)=B1 -> trade_id keying would DROP it.
    from src.annotations import Annotation, TagConfig
    rows = [
        _frow("B9", "2026-05-20", "10:00:00", "BUY", 1, 100.0, "O", oid="OE"),
        _frow("B1", "2026-05-20", "10:00:05", "BUY", 1, 102.0, "O", oid="OE"),
        _frow("B5", "2026-05-21", "09:00:00", "BUY", 1, 104.0, "O", oid="OE"),
        _frow("X1", "2026-05-22", "11:00:00", "SELL", -3, 110.0, "C", oid="OX"),
    ]
    c = sqlite_store.connect(":memory:"); sqlite_store.init_schema(c)
    sqlite_store.upsert_trades(c, rows)
    annots = {"B9": Annotation(setup_tag="Q_intraday", score=None, notes=None)}
    stats = exporter.export_date(c, "2026-05-20", tmp_path,
                                 annotations=annots, tag_config=TagConfig({}, {}))
    c.close()
    assert stats.state == "B"
    recs = _csv_rows(tmp_path / "mts_trades_2026-05-20.csv")
    op = [r for r in recs if r["open_close"] == "O"]
    assert len(op) == 1 and op[0]["quantity"] == "2"   # 05-20 portion kept, not dropped


def test_export_date_matches_lookback(tmp_path):
    # AGENT R2.1.2: same date via export_date(--date) and export_lookback(--lookback 1)
    # must produce byte-identical csv (catches coalesce-path divergence like BUG-1).
    rows = [
        _frow("A1", "2026-05-25", "09:31:15", "BUY", 1, 20100.25, "O", oid="OE"),
        _frow("A2", "2026-05-25", "09:31:18", "BUY", 1, 20100.50, "O", oid="OE"),
        _frow("C1", "2026-05-25", "15:02:40", "SELL", -2, 20180.0, "C", oid="OX"),
    ]
    c = sqlite_store.connect(":memory:"); sqlite_store.init_schema(c)
    sqlite_store.upsert_trades(c, rows)
    d1 = tmp_path / "single"; d2 = tmp_path / "look"
    exporter.export_date(c, "2026-05-25", d1)
    exporter.export_lookback(c, lookback_days=None, export_dir=d2)   # 'all' includes 05-25
    c.close()
    a = (d1 / "mts_trades_2026-05-25.csv").read_bytes()
    b = (d2 / "mts_trades_2026-05-25.csv").read_bytes()
    assert a == b
