"""Unit tests for R-multiple derivation (FR-PIVOT-10 / SPEC_R_multiple_v1 §5,§6).

Covers the corner-case surface C2,C3,C4,C5,C6,C7,C8,C15,C16 at the pure-math
layer. The adapter (r_for_round_trip) is exercised with a duck-typed stub so we
don't construct full RoundTrips here (the FIFO pairing that produces splits is
roundtrip.py's job, tested separately; here we prove each split's R is computed
independently from its own qty/pnl).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src import r_multiple as rm


def _rt(direction="LONG", open_price=100.0, quantity=2, multiplier=5, pnl_usd=None):
    """Minimal duck-typed RoundTrip stub (only the fields r_for_round_trip reads)."""
    return SimpleNamespace(
        direction=direction, open_price=open_price, quantity=quantity,
        multiplier=multiplier, pnl_usd=pnl_usd,
    )


# --- realized_risk -----------------------------------------------------------

def test_realized_risk_long_basic():
    # |100 - 90| * 2 * 5 = 100
    assert rm.realized_risk(100.0, 90.0, 2, 5) == 100.0


def test_realized_risk_short_symmetric():
    # SHORT stop above entry: |100 - 110| * 2 * 5 = 100 (sign lives in pnl, C15)
    assert rm.realized_risk(100.0, 110.0, 2, 5) == 100.0


def test_realized_risk_scales_with_qty():
    # C6: a split carrying more contracts has proportionally more risk
    assert rm.realized_risk(100.0, 90.0, 1, 5) == 50.0
    assert rm.realized_risk(100.0, 90.0, 3, 5) == 150.0


def test_realized_risk_none_inputs_return_none():
    assert rm.realized_risk(None, 90.0, 2, 5) is None
    assert rm.realized_risk(100.0, None, 2, 5) is None
    assert rm.realized_risk(100.0, 90.0, None, 5) is None
    assert rm.realized_risk(100.0, 90.0, 2, None) is None   # C5 multiplier unknown


def test_realized_risk_zero_distance_is_none():
    # C3: stop == entry → no division-by-zero downstream
    assert rm.realized_risk(100.0, 100.0, 2, 5) is None


# --- r_multiple --------------------------------------------------------------

def test_r_multiple_basic_win():
    assert rm.r_multiple(300.0, 100.0) == 3.0


def test_r_multiple_clean_stop_is_minus_one():
    # C16: a loss equal to planned risk = exactly -1R (the floor)
    assert rm.r_multiple(-100.0, 100.0) == -1.0


def test_r_multiple_blown_stop_worse_than_minus_one():
    # C16: loss bigger than planned risk → below the floor, NOT clamped
    assert rm.r_multiple(-180.0, 100.0) == pytest.approx(-1.8)


def test_r_multiple_outlier_not_capped():
    # C8: tiny risk + big win → large R, kept honest (no cap, unlike the demo)
    assert rm.r_multiple(2000.0, 50.0) == 40.0


def test_r_multiple_none_or_zero_risk_is_none():
    assert rm.r_multiple(None, 100.0) is None
    assert rm.r_multiple(300.0, None) is None
    assert rm.r_multiple(300.0, 0) is None


def test_r_multiple_keeps_full_precision():
    # caller rounds for display; the math layer must not pre-round
    assert rm.r_multiple(100.0, 30.0) == pytest.approx(3.3333333, rel=1e-6)


# --- classify_stop -----------------------------------------------------------

def test_classify_none():
    assert rm.classify_stop("LONG", 100.0, None) == "none"
    assert rm.classify_stop("LONG", None, 90.0) == "none"


def test_classify_zero_distance():
    assert rm.classify_stop("LONG", 100.0, 100.0) == "zero"


def test_classify_long_ok_and_wrong_side():
    assert rm.classify_stop("LONG", 100.0, 90.0) == "ok"          # stop below entry
    assert rm.classify_stop("LONG", 100.0, 110.0) == "wrong_side"  # C4: stop above


def test_classify_short_ok_and_wrong_side():
    assert rm.classify_stop("SHORT", 100.0, 110.0) == "ok"         # stop above entry
    assert rm.classify_stop("SHORT", 100.0, 90.0) == "wrong_side"  # C4: stop below


def test_classify_non_positive_price_is_wrong_side():
    # C7: a ≤0 stop price is nonsensical
    assert rm.classify_stop("LONG", 100.0, 0.0) == "wrong_side"
    assert rm.classify_stop("LONG", 100.0, -5.0) == "wrong_side"


# --- r_for_round_trip (adapter) ----------------------------------------------

def test_r_for_round_trip_ok():
    info = rm.r_for_round_trip(_rt(pnl_usd=300.0), planned_stop=90.0)
    assert info.status == "ok"
    assert info.realized_risk == 100.0
    assert info.r == 3.0
    assert info.has_r is True
    assert info.is_invalid_stop is False


def test_r_for_round_trip_no_stop():
    info = rm.r_for_round_trip(_rt(pnl_usd=300.0), planned_stop=None)
    assert info.status == "none"
    assert info.r is None and info.realized_risk is None
    assert info.has_r is False
    assert info.is_invalid_stop is False    # 'none' is normal partial coverage


@pytest.mark.parametrize("stop,expect", [(100.0, "zero"), (110.0, "wrong_side")])
def test_r_for_round_trip_invalid_stops_flagged(stop, expect):
    info = rm.r_for_round_trip(_rt(pnl_usd=-50.0), planned_stop=stop)
    assert info.status == expect
    assert info.r is None
    assert info.is_invalid_stop is True     # tallied into the warning


def test_r_for_round_trip_ok_stop_but_no_multiplier():
    # C5: valid stop, but multiplier unknown → r None, status stays 'ok' (falls
    # into the no-R subset, NOT the invalid-stop warning)
    info = rm.r_for_round_trip(_rt(multiplier=None, pnl_usd=None), planned_stop=90.0)
    assert info.status == "ok"
    assert info.r is None
    assert info.is_invalid_stop is False


def test_r_for_round_trip_short_winner_positive_r():
    # C15: SHORT that made money → positive R
    info = rm.r_for_round_trip(_rt(direction="SHORT", pnl_usd=200.0), planned_stop=110.0)
    assert info.r == 2.0


def test_fifo_splits_have_independent_r():
    # C6: same entry/stop, two splits with different qty + different exits →
    # each split's R derives from its own qty (risk) and its own pnl.
    split_a = rm.r_for_round_trip(_rt(quantity=1, pnl_usd=150.0), planned_stop=90.0)
    split_b = rm.r_for_round_trip(_rt(quantity=3, pnl_usd=-150.0), planned_stop=90.0)
    assert split_a.realized_risk == 50.0 and split_a.r == 3.0     # +3R scale-out
    assert split_b.realized_risk == 150.0 and split_b.r == -1.0   # clean stop on the rest
