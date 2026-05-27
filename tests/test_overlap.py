"""AF + TCF coexistence in one SQLite table: dedup, mutual fill, and the
current first-writer-wins behavior (NO self-heal yet — see data_schema.md).

Also an end-to-end scenario: mixed-source archive -> csv export, proving the
export filter is source-agnostic (it keys on underlying/asset_type, not
data_source) and applies the v1.0 contract transforms.
"""

from __future__ import annotations

import csv

from src import exporter, sqlite_store
from src.parser import ACTIVITY_PROFILE, CONFIRMATION_PROFILE, parse_trades

from builders import af_trade, af_xml, tcf_trade, tcf_xml


def _conn():
    c = sqlite_store.connect(":memory:")
    sqlite_store.init_schema(c)
    return c


def _ingest(conn, xml, profile):
    rows = parse_trades(xml, run_id="r", now_utc="z", profile=profile)
    return sqlite_store.upsert_trades(conn, rows)


# --- dedup / first-writer-wins (the C2-overlap behavior) --------------------

def test_tcf_then_af_same_id_dedups_keeps_confirmation():
    """Confirmation arrives first (evening); next-day Activity has the SAME
    tradeID -> IGNORED. Row stays CONFIRMATION (no self-heal yet)."""
    conn = _conn()
    _ingest(conn, tcf_xml(tcf_trade(tradeID="X", price="100.00", commission="-0.62")),
            CONFIRMATION_PROFILE)
    # Activity later reports the same trade with a (hypothetically) corrected price.
    s = _ingest(conn, af_xml(af_trade(tradeID="X", tradePrice="999.99")), ACTIVITY_PROFILE)

    assert (s.inserted, s.ignored_dupes) == (0, 1)          # AF row ignored as dupe
    rows = sqlite_store.query_all(conn)
    assert len(rows) == 1
    assert rows[0].data_source == "CONFIRMATION"            # first writer wins
    assert rows[0].trade_price == 100.00                    # preliminary value retained
    conn.close()


def test_af_then_tcf_same_id_dedups_keeps_activity():
    """Reverse order: Activity first, Confirmation re-pull same id -> ignored."""
    conn = _conn()
    _ingest(conn, af_xml(af_trade(tradeID="X")), ACTIVITY_PROFILE)
    s = _ingest(conn, tcf_xml(tcf_trade(tradeID="X")), CONFIRMATION_PROFILE)
    assert (s.inserted, s.ignored_dupes) == (0, 1)
    rows = sqlite_store.query_all(conn)
    assert len(rows) == 1 and rows[0].data_source == "ACTIVITY"
    conn.close()


def test_disjoint_ids_union_both_sources():
    """Different trades from each source coexist (mutual fill)."""
    conn = _conn()
    _ingest(conn, af_xml(af_trade(tradeID="A"), af_trade(tradeID="B")), ACTIVITY_PROFILE)
    _ingest(conn, tcf_xml(tcf_trade(tradeID="C")), CONFIRMATION_PROFILE)
    rows = {r.trade_id: r.data_source for r in sqlite_store.query_all(conn)}
    assert rows == {"A": "ACTIVITY", "B": "ACTIVITY", "C": "CONFIRMATION"}
    conn.close()


def test_partial_overlap_inserts_only_new():
    """Confirmation has {X,Y}; Activity has {X,Z} -> table = {X(conf),Y,Z}."""
    conn = _conn()
    _ingest(conn, tcf_xml(tcf_trade(tradeID="X"), tcf_trade(tradeID="Y")), CONFIRMATION_PROFILE)
    s = _ingest(conn, af_xml(af_trade(tradeID="X"), af_trade(tradeID="Z")), ACTIVITY_PROFILE)
    assert (s.attempted, s.inserted, s.ignored_dupes) == (2, 1, 1)  # only Z is new
    by = {r.trade_id: r.data_source for r in sqlite_store.query_all(conn)}
    assert by == {"X": "CONFIRMATION", "Y": "CONFIRMATION", "Z": "ACTIVITY"}
    conn.close()


# --- end-to-end: mixed-source archive -> csv export -------------------------

def test_mixed_source_export_is_source_agnostic(tmp_path):
    """Export pulls target futures from BOTH sources; stocks + non-target
    futures are filtered; v1.0 transforms applied."""
    conn = _conn()
    # Activity: one target future (MES), one non-target future (M6B), one stock (AAPL)
    _ingest(conn, af_xml(
        af_trade(tradeID="MES1", underlyingSymbol="MES", expiry="20260618",
                 buySell="BUY", quantity="1", openCloseIndicator="O"),
        af_trade(tradeID="M6B1", underlyingSymbol="M6B", expiry="20260616"),
        af_trade(tradeID="AAPL1", underlyingSymbol="AAPL", expiry=None,
                 tradePrice="210.50"),
    ), ACTIVITY_PROFILE)
    # Confirmation: a target future (NQ) on the same date
    _ingest(conn, tcf_xml(
        tcf_trade(tradeID="NQ1", underlyingSymbol="NQ", expiry="20260618",
                  buySell="SELL", quantity="-1", price="20100.25", code="C"),
    ), CONFIRMATION_PROFILE)

    stats = exporter.export_date(conn, "2026-05-18", tmp_path)
    assert stats.exported_rows == 2          # MES (AF) + NQ (TCF)
    assert stats.stocks_skipped == 1         # AAPL
    assert stats.other_futures_skipped == 1  # M6B

    with open(stats.path, encoding="utf-8") as fh:
        recs = list(csv.DictReader(fh))
    assert {r["underlying"] for r in recs} == {"MES", "NQ"}
    by = {r["underlying"]: r for r in recs}
    assert by["NQ"]["quantity"] == "1"             # unsigned in csv (was -1)
    assert by["NQ"]["open_close"] == "C"
    assert by["MES"]["expiry"] == "202606"         # YYYYMMDD -> YYYYMM
    assert all(r["category"] == "PAPER_AUTO" for r in recs)
    conn.close()
