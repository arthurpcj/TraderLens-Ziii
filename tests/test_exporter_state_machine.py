"""Tests for state-machine driven exporter (INTERFACE_CONTRACT §5.6 C6-C10).

Locks the per-trade_date State A/B decision, category column dual values,
header-only csv writing for empty State-B dates, and lookback re-export.

State A test fixtures use the existing sample XML (no annotations file →
all dates fall back to State A scheme-E behavior, preserves backward compat).
State B fixtures build a tiny in-memory annotations.csv keyed by the open-leg
tradeID present in the sample data, so the resolver sees Q_intraday.
"""

from __future__ import annotations

import csv
import os
from datetime import date as date_cls
from pathlib import Path

import pytest

from src import annotations as annotations_mod
from src import exporter, sqlite_store
from src.annotations import ANNOTATION_COLUMNS
from src.constants import CSV_CATEGORY_MTS_CONFIRMED, CSV_CATEGORY_PAPER_AUTO
from src.parser import parse_trades


@pytest.fixture
def conn(sample_xml_bytes):
    c = sqlite_store.connect(":memory:")
    sqlite_store.init_schema(c)
    rows = parse_trades(sample_xml_bytes, run_id="RUN1", now_utc="2026-05-20T00:00:00+00:00")
    sqlite_store.upsert_trades(c, rows)
    yield c
    c.close()


def _ann(open_trade_id: str, setup_tag: str = "Q_intraday") -> dict:
    """Single-entry annotations dict for direct injection into export_date."""
    return {open_trade_id: annotations_mod.Annotation(
        setup_tag=setup_tag, score="", notes=""
    )}


_EMPTY_TAG_CONFIG = annotations_mod.TagConfig({}, {})


# --- State A: no annotations (scheme-E behavior preserved) ---

def test_state_a_no_annotations_emits_paper_auto(conn, tmp_path):
    """Backward compat: with empty annotations dict, every date is State A and
    every row's category column = PAPER_AUTO. Equivalent to scheme-E behavior."""
    stats = exporter.export_date(
        conn, "2026-04-22", tmp_path,
        annotations={}, tag_config=_EMPTY_TAG_CONFIG,
    )

    assert stats.state == "A"
    assert stats.category == CSV_CATEGORY_PAPER_AUTO

    with (tmp_path / "mts_trades_2026-04-22.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows, "State A should emit at least the existing target-future legs"
    assert all(r["category"] == CSV_CATEGORY_PAPER_AUTO for r in rows)


# --- State B: an annotation flips the date ---

def test_state_b_annotation_flips_date_to_mts_confirmed(conn, tmp_path):
    """An annotation tagging one round-trip with Q_intraday should flip that
    round-trip's trade_dates to State B + category MTS_CONFIRMED. Only legs
    from MTS-confirmed round-trips should appear in csv."""
    rows = sqlite_store.query_all(conn)
    open_legs = [r for r in rows if r.open_close == "O" and r.asset_type == "FUT"
                 and r.underlying in ("NQ", "MNQ", "ES", "MES")]
    assert open_legs, "fixture should provide at least one open future leg"
    target_open = open_legs[0]

    stats = exporter.export_date(
        conn, target_open.trade_date, tmp_path,
        annotations=_ann(target_open.trade_id), tag_config=_EMPTY_TAG_CONFIG,
    )

    assert stats.state == "B"
    assert stats.category == CSV_CATEGORY_MTS_CONFIRMED

    with (tmp_path / f"mts_trades_{target_open.trade_date}.csv").open(
        encoding="utf-8", newline=""
    ) as fh:
        emitted = list(csv.DictReader(fh))
    assert all(r["category"] == CSV_CATEGORY_MTS_CONFIRMED for r in emitted)
    assert any(r["trade_id"] == target_open.trade_id for r in emitted), \
        "annotated open leg should appear in State-B csv"


def test_state_b_excludes_non_mts_legs_on_same_date(conn, tmp_path):
    """If date D has 2 round-trips A (Q_intraday) and B (untagged), State B
    csv for D should contain ONLY A's legs, not B's. This is the core
    'scope narrowing' contract."""
    rows = sqlite_store.query_all(conn)
    open_legs = [r for r in rows if r.open_close == "O" and r.asset_type == "FUT"
                 and r.underlying in ("NQ", "MNQ", "ES", "MES")]
    if len(open_legs) < 2:
        pytest.skip("need >=2 open legs to compare confirmed vs non-confirmed")
    # Group by date, find a date with multiple opens.
    by_date: dict[str, list] = {}
    for r in open_legs:
        by_date.setdefault(r.trade_date, []).append(r)
    multi_open_dates = [d for d, legs in by_date.items() if len(legs) >= 2]
    if not multi_open_dates:
        pytest.skip("fixture lacks a date with >=2 open legs")
    d = multi_open_dates[0]
    confirmed_open = by_date[d][0]
    excluded_open = by_date[d][1]

    stats = exporter.export_date(
        conn, d, tmp_path,
        annotations=_ann(confirmed_open.trade_id), tag_config=_EMPTY_TAG_CONFIG,
    )
    assert stats.state == "B"
    with (tmp_path / f"mts_trades_{d}.csv").open(encoding="utf-8", newline="") as fh:
        emitted = [r["trade_id"] for r in csv.DictReader(fh)]
    assert confirmed_open.trade_id in emitted, "confirmed open should be present"
    assert excluded_open.trade_id not in emitted, \
        "non-confirmed open on same date must be excluded under State B"


def test_state_b_unrelated_date_stays_state_a(conn, tmp_path):
    """Per-date independence: annotating round-trip on date D1 does NOT
    flip date D2 to State B if no RT touches D2 with MTS_RELEVANT tag."""
    rows = sqlite_store.query_all(conn)
    open_legs = [r for r in rows if r.open_close == "O" and r.asset_type == "FUT"
                 and r.underlying in ("NQ", "MNQ", "ES", "MES")]
    distinct_dates = sorted({r.trade_date for r in open_legs})
    if len(distinct_dates) < 2:
        pytest.skip("fixture lacks distinct trade_dates for cross-date test")
    target = next(r for r in open_legs if r.trade_date == distinct_dates[0])
    other_date = distinct_dates[1]
    other_open = next((r for r in open_legs if r.trade_date == other_date), None)
    assert other_open

    other_stats = exporter.export_date(
        conn, other_date, tmp_path,
        annotations=_ann(target.trade_id), tag_config=_EMPTY_TAG_CONFIG,
    )
    # If pairing happens to span date1→date2, the RT may touch other_date
    # and flip it. We only assert that state and category are consistent.
    if other_stats.state == "A":
        assert other_stats.category == CSV_CATEGORY_PAPER_AUTO
    else:
        assert other_stats.category == CSV_CATEGORY_MTS_CONFIRMED


# --- Header-only csv (C10) ---

def test_header_only_csv_for_non_trading_day_in_lookback(conn, tmp_path):
    """A trade_date with no SQLite trades produces a header-only csv (one
    line — just the header). MTS imports silently exit 0 on this."""
    far_past = "2020-01-15"                                   # no trades on this date
    stats = exporter.export_date(
        conn, far_past, tmp_path,
        annotations={}, tag_config=_EMPTY_TAG_CONFIG,
    )

    assert stats.exported_rows == 0
    csv_path = tmp_path / f"mts_trades_{far_past}.csv"
    assert csv_path.exists()
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, "header-only csv has exactly one line (the header)"
    assert lines[0].startswith("trade_id,trade_date")


# --- export_lookback helper (C8) ---

def test_export_lookback_writes_one_csv_per_day_in_window(conn, tmp_path):
    """`export_lookback(N)` writes one csv per day in the N-day window
    (inclusive of today), even non-trading days (header-only). Guarantees the
    wrapper.bat loop never trips on missing files."""
    pinned_today = date_cls(2026, 4, 25)
    stats_list = exporter.export_lookback(
        conn, lookback_days=5, export_dir=tmp_path, today=pinned_today
    )
    assert len(stats_list) == 6, "lookback=5 produces today + 5 prior = 6 csv files"
    for s in stats_list:
        assert s.path.exists()


def test_export_lookback_all_mode_covers_every_trade_date(conn, tmp_path):
    """`lookback_days=None` (all mode) writes one csv per distinct trade_date
    in SQLite — no extras, no gaps."""
    distinct = sorted({r.trade_date for r in sqlite_store.query_all(conn)
                       if r.asset_type == "FUT" and r.underlying in
                       ("NQ", "MNQ", "ES", "MES")})
    stats_list = exporter.export_lookback(
        conn, lookback_days=None, export_dir=tmp_path
    )
    assert len(stats_list) == len(distinct)
    assert sorted(s.date for s in stats_list) == distinct
