"""Pivot analytics + HTML build tests (Priority 2 / FR-PIVOT-4/5/7).

Browser-side JS is exercised separately; here we lock the Python analytics
(KPIs, drawdown, streaks, by-setup scoring) and that build_html threads the
annotation layer through into the report.
"""

from __future__ import annotations

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
