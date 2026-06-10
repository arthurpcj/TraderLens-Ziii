"""Pivot analytics + HTML build tests (Priority 2 / FR-PIVOT-4/5/7).

Browser-side JS is exercised separately; here we lock the Python analytics
(KPIs, drawdown, streaks, by-setup scoring) and that build_html threads the
annotation layer through into the report.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from src import pivot
from src.annotations import Annotation, TagConfig
from src.parser import TradeRow
from src.roundtrip import pair_round_trips


def _leg(tid, date, time, bs, qty_signed, price, oc, *, order_ref=None):
    return TradeRow(
        trade_id=tid, trade_date=date, trade_time=time, underlying="MNQ",
        expiry="20260618", buy_sell=bs, quantity=qty_signed, trade_price=price,
        multiplier=2, ib_commission=0.0, open_close=oc, fifo_pnl_realized=None,
        asset_type="FUT", category=None, notes=None, category_set_at=None,
        row_created_at="z", source_run_id="r", order_ref=order_ref,
    )


def _rt(open_date, close_date, pnl_pts, *, order_ref=None, tid="O"):
    """One LONG round-trip with a chosen point move (mult=2, no commission ->
    pnl_usd = pnl_pts * 2). Open 10:00, close 10:30 same/other day."""
    rows = [
        _leg(tid, open_date, "10:00:00", "BUY", 1, 100.0, "O", order_ref=order_ref),
        _leg(tid + "c", close_date, "10:30:00", "SELL", -1, 100.0 + pnl_pts, "C"),
    ]
    return pair_round_trips(rows)[0][0]


# --- streaks ---

def test_streaks():
    # W W L W L L L W  -> max win 2, max loss 3
    rts = [_rt("2026-05-20", "2026-05-20", p, tid=f"T{i}")
           for i, p in enumerate([1, 1, -1, 1, -1, -1, -1, 1])]
    assert pivot._streaks(rts) == (2, 3)


# --- max drawdown ---

def test_max_drawdown_amount_pct_days():
    # equity path: +100, +100 (peak 200 @ day2), then -50, -90 (trough 60 @ day4)
    rts = [
        _rt("2026-05-18", "2026-05-18", 50, tid="A"),    # +100 -> cum 100
        _rt("2026-05-19", "2026-05-19", 50, tid="B"),    # +100 -> cum 200 (peak)
        _rt("2026-05-20", "2026-05-20", -25, tid="C"),   # -50  -> cum 150
        _rt("2026-05-22", "2026-05-22", -45, tid="D"),   # -90  -> cum 60 (trough)
    ]
    k = pivot._kpis(rts)
    dd = k["dd"]
    assert dd["amount"] == pytest.approx(140.0)          # 200 -> 60
    assert dd["pct"] == pytest.approx(70.0)              # 140/200
    assert dd["days"] == 3                                # 05-19 peak -> 05-22 trough
    assert dd["peak_i"] == 1 and dd["trough_i"] == 3


# --- KPIs ---

def test_kpis_profit_factor_expectancy():
    # 3 wins +200 each (+600), 2 losses -100 each (-200). net=400, n=5.
    rts = [_rt("2026-05-20", "2026-05-20", v / 2, tid=f"T{i}")
           for i, v in enumerate([200, 200, 200, -100, -100])]
    k = pivot._kpis(rts)
    assert k["net"] == pytest.approx(400.0)
    assert k["commission"] == 0.0 and k["gross"] == pytest.approx(400.0)
    assert k["win_rate"] == pytest.approx(60.0)
    assert k["profit_factor"] == pytest.approx(600 / 200)  # 3.0
    assert k["expectancy"] == pytest.approx(80.0)          # 400/5
    assert k["avg_win"] == pytest.approx(200.0)
    assert k["avg_loss"] == pytest.approx(-100.0)


def test_profit_factor_infinite_when_no_losses():
    rts = [_rt("2026-05-20", "2026-05-20", 10, tid=f"T{i}") for i in range(3)]
    assert pivot._kpis(rts)["profit_factor"] is None  # rendered as ∞


# --- by-setup scoring (FR-PIVOT-5) ---

def test_scoring_rows_perf_and_execution():
    cfg = TagConfig({"ORB": "Opening Range Breakout"}, {})
    # ORB: one win (held 30m) + one loss (held 30m). Both same day -> intraday.
    rts = [_rt("2026-05-20", "2026-05-20", 50, tid="A"),   # +100 win
           _rt("2026-05-20", "2026-05-20", -25, tid="B")]  # -50 loss
    rows = pivot._scoring_rows(rts, ["ORB", "ORB"], cfg)
    assert len(rows) == 1
    r = rows[0]
    assert r["name"] == "Opening Range Breakout"
    assert r["n"] == 2 and r["net"] == pytest.approx(50.0)
    assert r["win_rate"] == pytest.approx(50.0)
    assert r["pf"] == pytest.approx(100 / 50)              # 2.0
    assert r["avg_win"] == pytest.approx(100.0) and r["avg_loss"] == pytest.approx(-50.0)
    assert r["hold_win"] == pytest.approx(30.0) and r["hold_loss"] == pytest.approx(30.0)
    assert r["intraday_pct"] == pytest.approx(100.0)


# --- R-multiple (FR-PIVOT-10) ---
# _rt builds LONG entry=100.0, mult=2, qty=1. With planned_stop=90 the risk is
# |100-90|*1*2 = 20, so R = pnl_usd/20 = pnl_pts/10 — clean integers below.

def _rec(pnl_pts, stop, *, tid="E1", code="ORB"):
    rt = _rt("2026-05-20", "2026-05-20", pnl_pts, tid=tid)
    ann = Annotation(setup_tag=code, score="", notes="", planned_stop=(stop or ""))
    return pivot._record(rt, code, code, ann)


def test_record_emits_r_fields():
    r = _rec(30, "90")                                  # +60 pnl / 20 risk = +3R
    assert r["R"] == pytest.approx(3.0)
    assert r["RealizedRisk"] == pytest.approx(20.0)
    assert r["HasR"] is True and r["StopStatus"] == "ok"
    assert r["PlannedStop"] == pytest.approx(90.0)


def test_record_no_stop_is_null_r():
    r = _rec(30, None)
    assert r["R"] is None and r["RealizedRisk"] is None
    assert r["HasR"] is False and r["StopStatus"] == "none"
    assert r["PlannedStop"] is None


def test_record_wrong_side_stop_is_invalid():
    # LONG with stop ABOVE entry (110 > 100) -> not a stop-loss (C4)
    r = _rec(-10, "110")
    assert r["R"] is None and r["StopStatus"] == "wrong_side"


# --- entry/exit fill price (detail table + calendar drill + CSV) ---

def test_record_carries_entry_exit_price():
    # _rt: LONG entry 100.0, close 100.0+pnl_pts. Prices surfaced for display.
    r = _rec(30, "90")
    assert r["OpenPx"] == pytest.approx(100.0)
    assert r["ClosePx"] == pytest.approx(130.0)


def test_record_caps_vwap_price_noise_at_4dp():
    # Multi-fill VWAP carries float-division noise (roundtrip._merge_group keeps
    # full precision; _record rounds at output). 4dp is a ceiling, not padding.
    rt = _rt("2026-05-20", "2026-05-20", 30, tid="V")
    rt = replace(rt, open_price=5230.333333333335, close_price=18000.5)
    r = pivot._record(rt, "ORB", "ORB", None)
    assert r["OpenPx"] == 5230.3333          # noise capped to 4dp
    assert r["ClosePx"] == 18000.5           # already clean -> unchanged (no padding)


def test_record_none_price_is_none():
    rt = _rt("2026-05-20", "2026-05-20", 30, tid="N")
    rt = replace(rt, open_price=None, close_price=None)
    r = pivot._record(rt, "ORB", "ORB", None)
    assert r["OpenPx"] is None and r["ClosePx"] is None


def test_detail_cols_price_columns_sit_before_notes():
    keys = [k for k, _ in pivot._DETAIL_COLS]
    assert keys.index("OpenPx") == keys.index("Notes") - 2
    assert keys.index("ClosePx") == keys.index("Notes") - 1
    labels = dict(pivot._DETAIL_COLS)
    assert labels["OpenPx"] == "Entry px" and labels["ClosePx"] == "Exit px"


def test_detail_csv_emits_raw_price_values():
    rec = {"OpenPx": 5230.25, "ClosePx": 5236.5}
    lines = pivot._detail_csv([rec]).replace("﻿", "").split("\r\n")
    header = lines[0].split(",")
    cells = lines[1].split(",")
    assert "Entry px" in header and "Exit px" in header
    assert cells[header.index("Entry px")] == "5230.25"   # raw number, no $
    assert cells[header.index("Exit px")] == "5236.5"


def test_r_kpis_aggregates_over_with_stop_subset():
    recs = [
        _rec(30, "90", tid="A"),    # +3R
        _rec(10, "90", tid="B"),    # +1R
        _rec(-10, "90", tid="C"),   # -1R clean
        _rec(-15, "90", tid="D"),   # -1.5R blown
        _rec(5, None, tid="E"),     # no stop -> excluded
        _rec(-10, "110", tid="F"),  # wrong-side -> invalid, excluded
    ]
    k = pivot._r_kpis(recs)
    assert k["r_n"] == 4 and k["n_closed"] == 6
    assert k["expectancy_r"] == pytest.approx((3 + 1 - 1 - 1.5) / 4)   # 0.375
    assert k["total_r"] == pytest.approx(3 + 1 - 1 - 1.5)   # 1.5 (headline chip)
    assert k["avg_win_r"] == pytest.approx(2.0)        # (3+1)/2
    assert k["avg_loss_r"] == pytest.approx(-1.25)     # (-1-1.5)/2
    assert k["blown"] == 1                              # only D < -1R
    assert k["invalid_stops"] == 1                      # F


def test_r_kpis_zero_coverage_is_null():
    recs = [_rec(30, None, tid="A"), _rec(-10, None, tid="B")]
    k = pivot._r_kpis(recs)
    assert k["r_n"] == 0 and k["n_closed"] == 2
    assert k["expectancy_r"] is None
    assert k["total_r"] is None                         # mirrors JS `length ? ... : null`
    assert k["avg_win_r"] is None and k["avg_loss_r"] is None
    assert k["blown"] == 0 and k["invalid_stops"] == 0


def test_r_scoring_per_setup_coverage():
    recs = [
        _rec(30, "90", tid="A", code="ORB"),    # ORB +3R
        _rec(10, "90", tid="B", code="ORB"),    # ORB +1R
        _rec(-10, "90", tid="C", code="PB"),    # PB  -1R
        _rec(5, None, tid="G", code="PB"),      # PB  no stop
    ]
    s = pivot._r_scoring(recs)
    assert s["ORB"] == {"r_n": 2, "n": 2, "expectancy_r": pytest.approx(2.0)}
    assert s["PB"]["r_n"] == 1 and s["PB"]["n"] == 2
    assert s["PB"]["expectancy_r"] == pytest.approx(-1.0)


def test_detail_cols_adds_r_only_when_present():
    with_r = _rec(30, "90", tid="A")        # R = +3.0
    no_r = _rec(5, None, tid="B")           # R = None
    cols = [k for k, _ in pivot._detail_cols([with_r, no_r])]
    assert "R" in cols and cols.index("R") == cols.index("PnL_USD") + 1
    assert "R" not in [k for k, _ in pivot._detail_cols([no_r])]   # zero coverage
    # CSV surfaces the R column + raw value when any record has one
    text = pivot._detail_csv([with_r, no_r])
    assert "R" in text.splitlines()[0].split(",")
    assert "3.0" in text


def test_build_html_embeds_r_when_stop_present():
    rt = _rt("2026-05-20", "2026-05-20", 30, tid="E1")
    stats = {"round_trips": 1, "unmatched_close_qty": 0, "still_open_qty": 0}
    anns = {"E1": Annotation(setup_tag="ORB", score="", notes="", planned_stop="90")}
    cfg = TagConfig({"ORB": "ORB"}, {})
    html = pivot.build_html([rt], stats, anns, cfg)
    assert '"R": 3.0' in html and '"HasR": true' in html


# --- build_html threads annotation layer through ---

def test_build_html_smoke_and_setup_resolution():
    rt = _rt("2026-05-20", "2026-05-20", 50, order_ref="bt_orb_v3", tid="E1")
    stats = {"round_trips": 1, "unmatched_close_qty": 0, "still_open_qty": 0}
    cfg = TagConfig({"ORB": "Opening Range Breakout"}, {"bt_orb_v3": "ORB"})
    anns = {"E1": Annotation(setup_tag="", score="8", notes="clean")}
    html = pivot.build_html([rt], stats, anns, cfg)
    # tier-2 alias resolved the display name into the report data
    assert "Opening Range Breakout" in html
    assert '"Score": 8.0' in html and "clean" in html
    # neutral-color legend + key sections present
    for token in ['id="calendar"', 'id="detail"', 'id="pivot"', "Profit factor",
                  "Max drawdown", "By setup", "var DATA", "var CFG"]:
        assert token in html


def test_build_html_empty_is_safe():
    stats = {"round_trips": 0, "unmatched_close_qty": 0, "still_open_qty": 0}
    html = pivot.build_html([], stats)
    assert "var DATA = []" in html
    assert "No closed round-trips to plot." in html  # equity-curve empty guard


def test_build_html_header_two_column_no_notices():
    """Header is a two-column bar (filters left, brand right). The old notices
    aside — FIFO Pairing-edges note + small-sample warning — was removed, so it
    must not render even when there ARE unmatched/still-open legs (which used to
    trigger the Pairing-edges note)."""
    rt = _rt("2026-05-20", "2026-05-20", 50, tid="E1")
    stats = {"round_trips": 1, "unmatched_close_qty": 3, "still_open_qty": 2}
    html = pivot.build_html([rt], stats)
    for token in ['class="header-bar"', 'class="filters"', 'class="brand"']:
        assert token in html, f"missing new header token: {token}"
    for gone in ['class="page-header"', 'class="topbar"', 'id="sampleWarn"',
                 "Pairing edges", "sample-warn", "small sample"]:
        assert gone not in html, f"removed structure leaked back: {gone}"


# --- read-only generation (demo never mutates its snapshot DB) ---

def test_connect_read_only_blocks_writes(tmp_path):
    """A read_only connection must reject writes — the guarantee behind 'demo
    generation never mutates the snapshot DB'."""
    import sqlite3

    from src import sqlite_store
    db = tmp_path / "t.sqlite"
    c = sqlite_store.connect(str(db))
    sqlite_store.init_schema(c)
    c.close()
    ro = sqlite_store.connect(str(db), read_only=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("INSERT INTO trades(trade_id) VALUES('x')")
            ro.commit()
    finally:
        ro.close()


def test_generate_read_only_uses_ann_path_and_leaves_db_untouched(tmp_path):
    """generate(read_only=True, ann_path=...) builds the report from a fixed
    snapshot without mutating the .sqlite, and honours the given annotations
    path (the --annotations flag used to be ignored for HTML generation)."""
    from src import sqlite_store
    db = tmp_path / "t.sqlite"
    conn = sqlite_store.connect(str(db))
    sqlite_store.init_schema(conn)
    sqlite_store.upsert_trades(conn, [
        _leg("O", "2026-05-20", "10:00:00", "BUY", 1, 100.0, "O"),
        _leg("Oc", "2026-05-20", "10:30:00", "SELL", -1, 110.0, "C"),
    ])
    conn.commit()
    conn.close()
    snapshot = db.read_bytes()
    out = tmp_path / "out.html"
    ann = tmp_path / "missing.csv"   # absent -> load_annotations returns {}
    out_path, stats = pivot.generate(db_path=db, out=out, ann_path=ann, read_only=True)
    assert out_path.exists() and "var DATA" in out.read_text(encoding="utf-8")
    assert stats["round_trips"] == 1
    assert db.read_bytes() == snapshot   # read-only build did not mutate the DB


# --- detail CSV export (Tier-2): Python source-of-truth for the browser
# "Download CSV". The JS toCSV mirror is verified visually; the escaping /
# column-order / BOM contract is locked here. ---

def _csv_lines(text):
    """Strip the BOM and split into CSV records on the CRLF terminator. An
    embedded LF inside a quoted field is NOT a record boundary (csv uses \\r\\n),
    so this split keeps such a field intact. Drops the trailing empty element
    left by the final CRLF."""
    assert text[0] == "﻿", "must start with a UTF-8 BOM (Excel CJK)"
    return text[1:].split("\r\n")[:-1]


def test_detail_csv_header_is_the_column_labels():
    text = pivot._detail_csv([])
    lines = _csv_lines(text)
    assert len(lines) == 1   # header only when no records
    assert lines[0] == ",".join(label for _, label in pivot._DETAIL_COLS)


def test_detail_csv_uses_crlf_terminators():
    # RFC-4180 + Excel friendliness: records end with CRLF, and the bare data
    # contains no lone LF (the only LF allowed is one quoted inside a field).
    text = pivot._detail_csv([{"Setup": "ORB"}])
    assert text.endswith("\r\n")
    assert "\n" not in text.replace("\r\n", "")   # no lone LF for plain records


def test_detail_csv_emits_raw_values_in_column_order():
    rec = {
        "CloseDate": "2026-05-20", "CloseTime": "10:30:00",
        "OpenDate": "2026-05-20", "OpenTime": "10:00:00",
        "Setup": "ORB", "Class": "FUT", "Underlying": "MNQ",
        "Direction": "LONG", "Result": "Win", "Session": "RTH",
        "EntryDOW": "Wed", "HoldBucket": "0-30m", "Qty": 2,
        "PnL_USD": 1234.5, "Hold_min": 30.0, "Score": 8.0, "Notes": "clean",
        "open_trade_id": "IGNORE", "SetupCode": "IGNORE",   # extras dropped
    }
    row = _csv_lines(pivot._detail_csv([rec]))[1]
    cells = row.split(",")
    assert len(cells) == len(pivot._DETAIL_COLS)
    # raw numeric P&L, not the display-formatted "+$1,234.50"
    assert "1234.5" in cells and "+$" not in row
    # column order follows _DETAIL_COLS, not dict insertion / alphabetical
    assert cells[0] == "2026-05-20" and cells[4] == "ORB" and cells[-1] == "clean"


def test_detail_csv_rfc4180_quoting():
    # comma, embedded double-quote, and newline must all be quoted/escaped
    recs = [
        {"Notes": "a,b"},          # comma -> field quoted
        {"Notes": 'say "hi"'},     # quote -> doubled + field quoted
        {"Notes": "line1\nline2"}, # newline -> field quoted
    ]
    rows = _csv_lines(pivot._detail_csv(recs))[1:]
    notes_idx = [k for k, _ in pivot._DETAIL_COLS].index("Notes")
    # comma case: the quoted field keeps the comma inside one cell
    assert rows[0].endswith('"a,b"')
    # quote case: " -> ""
    assert '"say ""hi"""' in rows[1]
    # newline case: the record spans two physical lines but is one CSV record;
    # _csv_lines split on CRLF, so the embedded LF keeps both halves together
    joined = "\r\n".join(_csv_lines(pivot._detail_csv([recs[2]]))[1:])
    assert '"line1\nline2"' in joined


def test_detail_csv_none_and_missing_become_empty():
    # Score=None (unscored) and a wholly absent key both render as empty cells
    text = pivot._detail_csv([{"Setup": "ORB", "Score": None}])
    cells = _csv_lines(text)[1].split(",")
    cols = [k for k, _ in pivot._DETAIL_COLS]
    assert cells[cols.index("Score")] == ""      # explicit None -> empty
    assert cells[cols.index("Qty")] == ""        # missing key -> empty


def test_detail_csv_preserves_cjk_notes():
    text = pivot._detail_csv([{"Notes": "突破回踩，干净"}])
    assert "突破回踩，干净" in text   # full-width comma is inside the note, not a delimiter


def test_build_html_wires_csv_export_button():
    rt = _rt("2026-05-20", "2026-05-20", 50, tid="E1")
    stats = {"round_trips": 1, "unmatched_close_qty": 0, "still_open_qty": 0}
    html = pivot.build_html([rt], stats)
    assert 'id="detailExport"' in html          # the button exists
    assert "exportDetailCsv" in html and "function toCSV" in html   # wired + mirror present


# --- calendar windowed viewport (Tier-2, FR-PIVOT-8): Python source of truth
# for the bounded N-month viewport + gentle re-anchor rule. JS mirror
# (calWindow/resolveAnchor) verified visually. ---

def test_calendar_window_recent_n_months():
    w = pivot._calendar_window("2026-06", 3, "2025-01", "2026-06")
    assert w["months"] == ["2026-04", "2026-05", "2026-06"]
    assert w["has_next"] is False        # anchor at the data's max month
    assert w["has_prev"] is True         # older data exists


def test_calendar_window_crosses_year_boundary():
    w = pivot._calendar_window("2026-01", 3, "2024-01", "2026-06")
    assert w["months"] == ["2025-11", "2025-12", "2026-01"]


def test_calendar_window_clamps_at_oldest_edge():
    # anchor at min: prev arrow dead, window shows leading empty pre-data months
    w = pivot._calendar_window("2025-01", 3, "2025-01", "2026-06")
    assert w["months"] == ["2024-11", "2024-12", "2025-01"]
    assert w["has_prev"] is False and w["has_next"] is True


def test_calendar_window_single_column_and_short_history():
    assert pivot._calendar_window("2026-03", 1, "2026-01", "2026-06")["months"] == ["2026-03"]
    # data shorter than cols -> window padded with leading empty months
    w = pivot._calendar_window("2026-03", 4, "2026-02", "2026-03")
    assert w["months"] == ["2025-12", "2026-01", "2026-02", "2026-03"]


def test_calendar_window_empty_extent_is_safe():
    w = pivot._calendar_window(None, 3, None, None)
    assert w == {"months": [], "has_prev": False, "has_next": False}


def test_resolve_anchor_unbounded_all_stays_put():
    # switching to "All" (no `to`) must NOT move the viewport
    assert pivot._resolve_anchor("2026-03", None, 3, "2025-01", "2026-06") == "2026-03"
    # ...but with no current anchor yet (initial load) fall back to most-recent
    assert pivot._resolve_anchor(None, None, 3, "2025-01", "2026-06") == "2026-06"


def test_resolve_anchor_gentle_when_filter_month_already_visible():
    # filter.to month is inside the current viewport -> don't move (gentle rule)
    assert pivot._resolve_anchor("2026-06", "2026-05-10", 3, "2025-01", "2026-06") == "2026-06"


def test_resolve_anchor_jumps_when_filter_month_offscreen():
    # filter.to month not visible -> re-anchor right edge to it
    assert pivot._resolve_anchor("2026-06", "2026-01-10", 3, "2025-01", "2026-06") == "2026-01"


def test_resolve_anchor_clamps_filter_outside_data_extent():
    # filter far in the future -> clamp target into [min,max]; here that target
    # (max month) is already visible, so the viewport stays put
    assert pivot._resolve_anchor("2026-06", "2030-01-10", 3, "2025-01", "2026-06") == "2026-06"


def test_resolve_anchor_no_data_returns_none():
    assert pivot._resolve_anchor(None, "2026-01-10", 3, None, None) is None


def test_build_html_wires_calendar_viewport():
    rt = _rt("2026-05-20", "2026-05-20", 50, tid="E1")
    stats = {"round_trips": 1, "unmatched_close_qty": 0, "still_open_qty": 0}
    html = pivot.build_html([rt], stats)
    # viewport arrows present + wired view-only (pageCal), and the JS mirrors exist
    assert 'id="calPrev"' in html and 'id="calNext"' in html
    assert "function pageCal" in html and "function calWindow" in html and "function resolveAnchor" in html
    assert "filteredNoDate" in html        # calendar decoupled from the date filter
