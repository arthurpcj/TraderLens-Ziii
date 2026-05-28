# ADR-002: Flex Web Service rate-limit policy (10-min interval, 30-min penalty)

**Status**: Accepted
**Date**: 2026-05-20
**Context**: Spike 001 hit IBKR code 1018 in practice + official documentation review

---

## Context

REQUIREMENTS v1.0 FR-FETCH-5 only said "network retry 3-5 times, 30-sec interval", without distinguishing:
- **Transient server-busy** (after SendRequest the report is still generating) — wait and retry.
- **Client-side throttling** (call rate too high) — **never retry**; retries risk a permanent IP ban.

Spike 001 measurements:
- 08:34 first call succeeded (1.0s, 33 trades).
- 08:41 second call succeeded (~7 min gap, 46 KB).
- 08:41:44 third call failed (28-sec gap) — `ResponseCodeError Code=1001/1018: Statement could not be generated at this time`.
- Subsequent calls continued to be rejected for several minutes.

Community wisdom suggests "60 sec is fine" — **empirically it isn't**. The official wording is blunt:

> "Activity Statement" Flex Queries contain data that is only updated once daily at close of business, so **there is no benefit to generating and retrieving these reports more than once per day.**
>
> Violator IP addresses may be put in a **penalty box for 10 minutes**. **Repeat violator IP addresses may be permanently blocked** until the issue is resolved.

ibflex source (`client.py`) already distinguishes two error-code families:
- `SERVER_BUSY = ("1009", "1019")` — report still generating, safe to wait + retry.
- `CLIENT_THROTTLED = ("1018",)` — you've been throttled, do not hit again.

## Decision

`src/ib_sync.py` implements a **two-layer throttle gate + strict error-code classification**:

### Policy constants

```python
MIN_INTERVAL_SEC = 600    # min gap between calls to the same query — 10 min
PENALTY_BOX_SEC  = 1800   # after a 1018, freeze for 30 min (official 10 min + buffer)
SERVER_BUSY_RETRY_MAX = 3 # 1009 / 1019 — at most 3 retries
SERVER_BUSY_INTERVAL_SEC = 30  # 30-sec wait between busy retries
```

### Two new fields in `state.json`

```json
{
  "last_flex_call_ts": 1747728000,   // updated only on a successful call
  "throttled_until_ts": 0            // 0 = not throttled; otherwise epoch when freeze lifts
}
```

### Double gate before every call

```python
def gate_flex_call(state) -> str | None:
    now = time.time()
    # Gate 1: still in the penalty box?
    if now < state.get("throttled_until_ts", 0):
        return f"throttled, {(state['throttled_until_ts'] - now)/60:.1f} min remaining"
    # Gate 2: minimum interval satisfied?
    elapsed = now - state.get("last_flex_call_ts", 0)
    if elapsed < MIN_INTERVAL_SEC:
        return f"min interval not met, {(MIN_INTERVAL_SEC - elapsed):.0f}s remaining"
    return None
```

### Error-code handling

| Code | Name | Behaviour |
|---|---|---|
| 1009 / 1019 | SERVER_BUSY | Wait 30s, retry GetStatement, up to 3 times. This is the normal post-SendRequest wait. |
| **1018** | **CLIENT_THROTTLED** | **Exit immediately**, set `throttled_until_ts = now + 1800`. **Do not retry.** Do not update `last_success_trade_date`. The next trigger will retry naturally. |
| HTTP 429 | (same) | Same as 1018. |
| Token invalid / other | Record `last_error`, exit. Do not update `last_success_trade_date`, flag as red. |

### Scheduler-layer safety net

- Task Scheduler trigger frequency ≤ 4 times/day (early morning / midday / EOD / late evening).
- Each trigger runs the throttle gate first — most return within a second. Even with 4 triggers, the actual Flex call count is 1-2.
- Any user-side wrapper script does not bypass the gate — wrappers should invoke the project entrypoint `.bat` (which honours the gate), never the Flex Web Service directly.

## Consequences

### Upsides

- **Permanent-ban risk → zero** (assuming the gate is not bypassed).
- **Safe to run idempotently many times** — 2nd / 3rd / 4th invocation auto-exits in milliseconds.
- **Auto-recovery after a throttle** — 30 min later the next trigger works normally; no manual intervention.
- **Data integrity preserved** — a throttled run does not update `last_success_trade_date`, so the next successful run backfills.

### Costs

- **0-10 minute data latency** — but IBKR Flex is designed as a "once daily" feed (official statement), 10 min is fully acceptable.
- **Cannot "quickly try" during debugging** — after a code change you must wait 10 min before hitting production. Mitigation: use `tests/fixtures/sample_flex.xml` (a real 53 KB sample from spike) for parser unit tests, no Flex calls.

### Risks

- **`state.json` corrupted / hand-deleted** → throttle gate disarmed, risk of accidental violation.
  - Mitigation: atomic-rename + backup on write; corruption detected on startup → safe mode (default `throttled_until_ts = now + 30 min`, wait out the cooldown before resuming).
- **Multiple machines sharing one token** → each machine keeps its own `state.json`, effective frequency exceeds the limit.
  - Mitigation: in paper / live use we run on one laptop only — single-machine assumption holds. If we ever go multi-machine, move state to a cloud store (out of v1 scope).

## Debug Discipline (hard rules, codified in CONTRIBUTING)

| Anti-pattern | Outcome | Correct approach |
|---|---|---|
| Code edit + "quick re-test" within 30 sec | 1018 → penalty box | Edit + wait ≥ 10 min, OR test parser logic against cached XML. |
| Click "Run" in the Flex Queries web UI, then immediately run the script | Shared counter, instant 1018 | The web "Run" counts as a call too — wait 10 min. |
| `while True: retry()` loop on failure | Repeat violation → permanent ban | Exit on failure; let the next trigger retry. |
| "Just for debug" — change MIN_INTERVAL_SEC to 60s | Forget to revert → sustained violation | Always use 600s; debug parsing against cached XML instead. |
| Repeatedly hit the production query while adding logging | Repeated 1018 | Create a second query (`IB_Trade_Sync_Debug`, period = Last 7 Days) for debugging — still honor 10-min interval. |

## Alternatives Considered

1. **Use a 60-sec interval** (community wisdom): empirically hits the wall, rejected.
2. **Use a 5-minute interval**: borderline, risky. 10 minutes is the safer choice and the user only needs 1-2 calls per day.
3. **No throttle gate, just try-and-fail**: unacceptable — a permanent IP ban affects the entire IBKR account's API access.
4. **Have the user manually re-enter the token after each failure**: anti-automation, defeats the goal.

## References

- Spike 001 (the live 1018 hit): [001_flex_connectivity_spike_20260520](../studies/001_flex_connectivity_spike_20260520/README.md)
- Official docs: https://www.interactivebrokers.com/campus/ibkr-api-page/flex-web-service/
- ibflex error-code source (historical reference): https://github.com/csingley/ibflex/blob/master/ibflex/client.py
- Related: [ADR-001 Drop ibflex](001-drop-ibflex.md)
