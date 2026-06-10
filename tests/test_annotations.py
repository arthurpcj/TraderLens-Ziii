"""Annotation-layer tests (Priority 2 / FR-PIVOT-3): three-tier setup_tag
resolution + --tag-template generation (preserve / append / refresh)."""

from __future__ import annotations

import csv
import json

import pytest

from src import annotations as A
from src.annotations import Annotation, TagConfig
from src.constants import UNTAGGED
from src.parser import TradeRow
from src.roundtrip import pair_round_trips


def _leg(tid, date, time, bs, qty_signed, price, oc, *, order_ref=None):
    return TradeRow(
        trade_id=tid, trade_date=date, trade_time=time, underlying="MES",
        expiry="20260618", buy_sell=bs, quantity=qty_signed, trade_price=price,
        multiplier=5, ib_commission=-0.62, open_close=oc, fifo_pnl_realized=None,
        asset_type="FUT", category=None, notes=None, category_set_at=None,
        row_created_at="z", source_run_id="r", order_ref=order_ref,
    )


# --- config loading ---

def test_load_tag_config(tmp_path):
    p = tmp_path / "pivot_tags.json"
    p.write_text(json.dumps({
        "_comment": ["ignored"],
        "setup_tags": {"ORB": "Opening Range Breakout"},
        "order_ref_aliases": {"bt_orb_v3": "ORB"},
    }), encoding="utf-8")
    cfg = A.load_tag_config(p)
    assert cfg.setup_tags == {"ORB": "Opening Range Breakout"}
    assert cfg.aliases == {"bt_orb_v3": "ORB"}
    assert cfg.display("ORB") == "Opening Range Breakout"
    assert cfg.display("ZZ") == "ZZ"          # unknown code -> itself
    assert cfg.display(UNTAGGED) == "Untagged"


def test_load_tag_config_missing_is_empty(tmp_path):
    cfg = A.load_tag_config(tmp_path / "nope.json")
    assert cfg == TagConfig({}, {})


# --- three-tier resolution (FR-PIVOT-3c) ---

def test_resolve_tier1_explicit_wins_over_alias():
    cfg = TagConfig({}, {"bt_orb_v3": "ORB"})
    anns = {"E1": Annotation(setup_tag="PB", score="7", notes="")}
    # explicit "PB" beats the alias "ORB" even though order_ref matches
    assert A.resolve_setup_tag("E1", "bt_orb_v3", anns, cfg) == "PB"


def test_resolve_tier2_alias_when_no_explicit():
    cfg = TagConfig({}, {"bt_orb_v3": "ORB"})
    assert A.resolve_setup_tag("E1", "bt_orb_v3", {}, cfg) == "ORB"


def test_resolve_empty_explicit_falls_through_to_alias():
    cfg = TagConfig({}, {"bt_orb_v3": "ORB"})
    anns = {"E1": Annotation(setup_tag="", score="", notes="")}  # row exists, tag blank
    assert A.resolve_setup_tag("E1", "bt_orb_v3", anns, cfg) == "ORB"


def test_resolve_tier3_untagged():
    cfg = TagConfig({}, {})
    assert A.resolve_setup_tag("E1", None, {}, cfg) == UNTAGGED
    assert A.resolve_setup_tag("E1", "unknown_ref", {}, cfg) == UNTAGGED


# --- score parsing ---

@pytest.mark.parametrize("raw,val", [("7", 7.0), ("8.5", 8.5), ("", None), ("n/a", None)])
def test_score_value(raw, val):
    assert Annotation(setup_tag="", score=raw, notes="").score_value == val


# --- planned_stop parsing (FR-PIVOT-10) ---

@pytest.mark.parametrize("raw,val", [
    ("90.5", 90.5), ("4280", 4280.0), ("  102.25 ", 102.25),   # numeric (whitespace ok)
    ("", None), ("n/a", None), ("stop?", None),                # blank / text -> None (C2)
    ("-5", -5.0),                                              # parses; validity is classify_stop's job
])
def test_planned_stop_value(raw, val):
    assert Annotation(setup_tag="", score="", notes="", planned_stop=raw).planned_stop_value == val


def test_planned_stop_defaults_blank():
    # Annotation built without planned_stop (existing call sites) -> blank, None.
    a = Annotation(setup_tag="ORB", score="7", notes="x")
    assert a.planned_stop == "" and a.planned_stop_value is None


def test_load_old_file_without_planned_stop_column(tmp_path):
    # C12: a pre-FR-PIVOT-10 annotations.csv (no planned_stop column) must load
    # unchanged, with planned_stop -> "" / None, never a schema error.
    out = tmp_path / "annotations.csv"
    old_cols = ["open_trade_id", "setup_tag", "score", "notes",
                "ref_open_date", "ref_open_time", "ref_underlying",
                "ref_direction", "ref_pnl_usd", "ref_round_trips"]
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=old_cols)
        w.writeheader()
        w.writerow({"open_trade_id": "E1", "setup_tag": "ORB", "score": "8",
                    "notes": "x", "ref_open_date": "", "ref_open_time": "",
                    "ref_underlying": "", "ref_direction": "", "ref_pnl_usd": "",
                    "ref_round_trips": ""})
    anns = A.load_annotations(out)
    assert anns["E1"].setup_tag == "ORB"
    assert anns["E1"].planned_stop == "" and anns["E1"].planned_stop_value is None


# --- --tag-template generation (FR-PIVOT-3d) ---

def _two_round_trips():
    rows = [
        _leg("E1", "2026-05-20", "09:50:00", "BUY", 1, 100.0, "O", order_ref="bt_orb_v3"),
        _leg("X1", "2026-05-20", "10:20:00", "SELL", -1, 110.0, "C"),
        _leg("E2", "2026-05-21", "10:00:00", "SELL", -1, 200.0, "O"),
        _leg("X2", "2026-05-21", "10:30:00", "BUY", 1, 195.0, "C"),
    ]
    return pair_round_trips(rows)[0]


def test_tag_template_creates_with_ref_columns(tmp_path):
    out = tmp_path / "annotations.csv"
    st = A.write_tag_template(_two_round_trips(), out)
    assert st["total"] == 2 and st["new"] == 2 and st["preserved"] == 0
    with out.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["open_trade_id"] == "E1"        # chronological
    assert rows[0]["setup_tag"] == "" and rows[0]["score"] == ""
    assert rows[0]["ref_underlying"] == "MES"
    assert rows[0]["ref_direction"] == "LONG"
    assert rows[1]["open_trade_id"] == "E2"
    assert rows[1]["ref_direction"] == "SHORT"


def test_tag_template_preserves_filled_and_appends_new(tmp_path):
    out = tmp_path / "annotations.csv"
    # pre-fill E1 only
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=A.ANNOTATION_COLUMNS)
        w.writeheader()
        w.writerow({"open_trade_id": "E1", "setup_tag": "ORB", "score": "8",
                    "notes": "clean break", "ref_open_date": "", "ref_open_time": "",
                    "ref_underlying": "", "ref_direction": "", "ref_pnl_usd": "",
                    "ref_round_trips": ""})
    st = A.write_tag_template(_two_round_trips(), out)
    assert st["new"] == 1 and st["preserved"] == 1
    anns = A.load_annotations(out)
    assert anns["E1"].setup_tag == "ORB" and anns["E1"].score == "8"   # preserved
    assert anns["E1"].notes == "clean break"
    assert anns["E2"].setup_tag == ""                                  # appended blank


def test_tag_template_keeps_orphaned_annotation(tmp_path):
    out = tmp_path / "annotations.csv"
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=A.ANNOTATION_COLUMNS)
        w.writeheader()
        w.writerow({"open_trade_id": "GONE", "setup_tag": "PB", "score": "5",
                    "notes": "old", "ref_open_date": "", "ref_open_time": "",
                    "ref_underlying": "", "ref_direction": "", "ref_pnl_usd": "",
                    "ref_round_trips": ""})
    st = A.write_tag_template(_two_round_trips(), out)
    assert st["orphaned"] == 1
    anns = A.load_annotations(out)
    assert "GONE" in anns and anns["GONE"].setup_tag == "PB"   # user work not lost


def test_tag_template_preserves_planned_stop(tmp_path):
    # C13: an entered planned_stop survives a --tag-template re-run (R4).
    out = tmp_path / "annotations.csv"
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=A.ANNOTATION_COLUMNS)
        w.writeheader()
        w.writerow({"open_trade_id": "E1", "setup_tag": "ORB", "score": "8",
                    "notes": "clean", "planned_stop": "95.5",
                    "ref_open_date": "", "ref_open_time": "", "ref_underlying": "",
                    "ref_direction": "", "ref_pnl_usd": "", "ref_round_trips": ""})
    A.write_tag_template(_two_round_trips(), out)
    anns = A.load_annotations(out)
    assert anns["E1"].planned_stop == "95.5"            # preserved on active row
    assert anns["E1"].planned_stop_value == 95.5
    assert anns["E2"].planned_stop == ""                # new row -> blank


def test_tag_template_preserves_planned_stop_on_orphan(tmp_path):
    # orphaned annotation (entry no longer in data) keeps its planned_stop too.
    out = tmp_path / "annotations.csv"
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=A.ANNOTATION_COLUMNS)
        w.writeheader()
        w.writerow({"open_trade_id": "GONE", "setup_tag": "PB", "score": "5",
                    "notes": "old", "planned_stop": "203.0",
                    "ref_open_date": "", "ref_open_time": "", "ref_underlying": "",
                    "ref_direction": "", "ref_pnl_usd": "", "ref_round_trips": ""})
    A.write_tag_template(_two_round_trips(), out)
    anns = A.load_annotations(out)
    assert anns["GONE"].planned_stop == "203.0"         # user work not lost


def test_tag_template_split_close_one_row_per_entry(tmp_path):
    # one 2-lot entry, two 1-lot closes -> 2 round-trips, but ONE annotation row.
    rows = [
        _leg("E", "2026-05-20", "09:00:00", "BUY", 2, 100.0, "O"),
        _leg("C1", "2026-05-20", "09:30:00", "SELL", -1, 110.0, "C"),
        _leg("C2", "2026-05-20", "10:00:00", "SELL", -1, 120.0, "C"),
    ]
    rts = pair_round_trips(rows)[0]
    assert len(rts) == 2
    out = tmp_path / "annotations.csv"
    A.write_tag_template(rts, out)
    with out.open(encoding="utf-8", newline="") as fh:
        data = list(csv.DictReader(fh))
    assert len(data) == 1                          # collapsed to the entry
    assert data[0]["open_trade_id"] == "E"
    assert data[0]["ref_round_trips"] == "2"
