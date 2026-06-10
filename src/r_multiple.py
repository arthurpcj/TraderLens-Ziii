"""R-multiple derivation (FR-PIVOT-10, v1).

R = realized_pnl ÷ planned_risk, where planned_risk is reconstructed from the
ENTRY price, the matched quantity, the instrument multiplier, and the user's
optional `planned_stop` annotation (the *initial* planned stop, not a trailed
one). Pure functions, no I/O — the whole corner-case surface is unit-tested here
(see SPEC_R_multiple_v1.md §5).

Design (SPEC §3):
- D1: this module is the single home of the math + the stop-validity classifier.
- D9: invalid stops (zero distance, wrong-side, non-positive price) yield R=None
  and a status the caller tallies into an `invalid_stops` warning — they are NOT
  silently folded into the −1R floor.

R is computed only for round-trips whose entry carries a *valid* planned_stop;
everything downstream aggregates over that subset with a coverage badge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:                       # avoid runtime coupling to roundtrip
    from .roundtrip import RoundTrip

StopStatus = Literal["ok", "none", "zero", "wrong_side"]


@dataclass(frozen=True)
class RInfo:
    """Per-round-trip R result. `r`/`realized_risk` are None unless status=='ok'."""

    r: float | None
    realized_risk: float | None
    status: StopStatus

    @property
    def has_r(self) -> bool:
        return self.r is not None

    @property
    def is_invalid_stop(self) -> bool:
        """A stop was entered but is unusable (zero distance / wrong side / ≤0).

        Distinct from 'none' (no stop entered) — only these warrant the
        `invalid_stops` warning, since 'none' is the normal partial-coverage case.
        """
        return self.status in ("zero", "wrong_side")


def classify_stop(
    direction: str, open_price: float | None, planned_stop: float | None
) -> StopStatus:
    """Validate a planned_stop against the entry (SPEC C3/C4/C7).

    - 'none'       : no stop entered (planned_stop is None)
    - 'zero'       : stop == entry → zero risk distance (division by zero guard)
    - 'wrong_side' : non-positive price, OR a stop on the wrong side of entry
                     (LONG stop ≥ entry / SHORT stop ≤ entry — not a stop-loss)
    - 'ok'         : a usable initial stop
    """
    if planned_stop is None or open_price is None:
        return "none"
    if planned_stop <= 0:
        return "wrong_side"
    if planned_stop == open_price:
        return "zero"
    if direction == "LONG":
        return "ok" if planned_stop < open_price else "wrong_side"
    # SHORT (or any non-LONG): a valid stop sits ABOVE entry
    return "ok" if planned_stop > open_price else "wrong_side"


def realized_risk(
    open_price: float | None,
    planned_stop: float | None,
    qty: int | None,
    multiplier: int | None,
) -> float | None:
    """planned dollar risk = |entry − stop| × |qty| × multiplier (SPEC §1.1).

    None if any input is missing or the distance is zero (no division-by-zero
    risk downstream). Uses absolute distance/qty so LONG and SHORT are symmetric
    (sign lives in pnl, not in risk). Caller is expected to have validated the
    side via classify_stop; this stays defensive regardless.
    """
    if open_price is None or planned_stop is None or qty is None or multiplier is None:
        return None
    distance = abs(open_price - planned_stop)
    if distance == 0:
        return None
    risk = distance * abs(qty) * multiplier
    return risk if risk > 0 else None


def r_multiple(pnl_usd: float | None, risk: float | None) -> float | None:
    """R = pnl ÷ planned_risk. None if either input is missing or risk is 0.

    No clamping: a blown stop reads worse than −1R, a small-risk outlier reads
    high — both are honest (the demo's +6R cap was synthetic-only, SPEC D10).
    """
    if pnl_usd is None or risk is None or risk == 0:
        return None
    return pnl_usd / risk


def r_for_round_trip(rt: "RoundTrip", planned_stop: float | None) -> RInfo:
    """Compose classify_stop + realized_risk + r_multiple for one round-trip.

    Duck-typed on rt.{direction, open_price, quantity, multiplier, pnl_usd} so
    the module stays decoupled from the RoundTrip class. For a FIFO split (one
    entry → several round-trips, SPEC C6) each split carries its own quantity, so
    its realized_risk scales to that slice and its R is independent.
    """
    status = classify_stop(rt.direction, rt.open_price, planned_stop)
    if status != "ok":
        return RInfo(r=None, realized_risk=None, status=status)
    risk = realized_risk(rt.open_price, planned_stop, rt.quantity, rt.multiplier)
    r = r_multiple(rt.pnl_usd, risk)
    # multiplier/pnl unknown (open or un-priced) → risk or r None; keep status
    # 'ok' (the stop itself is valid) but has_r will be False, so it just falls
    # into the no-R subset rather than the invalid-stop warning.
    return RInfo(r=r, realized_risk=risk, status=status)
