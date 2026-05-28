# Code Review Standard — TraderLens

> Single authoritative document. Any change to `src/*.py` must follow this workflow.
> TraderLens domain adjustments: CSV export schema stability / Flex rate limit (permanent-ban risk) / idempotency / field robustness.

---

## Quick Reference

| Trigger File | Risk | Required Steps |
|---|---|---|
| `constants.py` (non-logic constants) / `errors.py` / docs / comments | Green Low | Step 1 (written Review) -> Step 2 (relevant pytest cases) -> Commit |
| `parser.py` / `exporter.py` / `sqlite_store.py` | Yellow Medium | Step 1 -> Step 2 -> Step 3 (full pytest + offline vertical-slice demo) -> Commit |
| `flex_client.py` / `state.py` (rate-limit gate) / **csv schema** / `ib_sync.py` orchestration | Red High | Step 1 -> Step 2 -> Step 3 (full pytest + mock verification, **never call real Flex**) -> Commit |

**Mandatory order: Review must come before Test. You may not modify code and jump straight to running tests without producing a written Review.**

**TraderLens iron rule: any change touching Flex calls / retries / scheduling frequency is automatically Red** (permanent IP ban risk, see [ADR-002](../decisions/002-flex-rate-limit-policy.md)). During development, verify only with mocks + cached XML; real Flex calls must respect the 10 min interval and remain single-shot.

---

## Step 1 — Written Review Report (Blocking Gate)

After completing the code change, answer each item before running any tests. Every item **must have a conclusion** ("not affected, N/A" is acceptable; blanks are not).

### A. Change Summary
- Which functions/logic in which files were modified?
- What is the intent of the change?

### B. Default Behavior (Backward Compatibility)
- [ ] B1 Do existing callers (not passing new parameters) behave exactly as before?
- [ ] B2 Have new parameter defaults been validated (not guessed)?
- [ ] B3 Are implicit dependencies changed (module-level constants / state.json schema / SQLite schema)?

### C. Corner Cases (TraderLens Domain)

| # | Scenario | Conclusion (required) |
|---|---|---|
| C1 | Empty input (empty XML / 0 trades / empty SQLite / empty catch-up window) | |
| C2 | None / missing fields (optional field missing -> NULL; key field missing -> skip that record) | |
| C3 | File/directory does not exist (.env / state.json / data dir) | |
| C4 | **Flex rate limit 1018 -> no retry + 30 min penalty box** (permanent-ban protection) | |
| C5 | Idempotency: duplicate trade_id / running N times a day -> converges (INSERT OR IGNORE + gate) | |
| C6 | Cross-platform paths (Windows `\` vs `/`; use pathlib) | |
| C7 | Time zone / DST (trade_time preserves IB ET original value without conversion; audit timestamps in UTC) | |
| C8 | Type/format anomalies (quantity not numeric / dateTime missing separator / price not float) | |
| C9 | Field order changes / extra unknown fields appear (attrib lookup by name, naturally immune) | |
| C10 | state.json corrupted/missing -> safe-mode backoff | |
| C11 | Multiple FlexStatement (multi-account) / single open leg not closed | |
| C12 | csv contract: 12-column order / expiry YYYYMM truncation / qty unsigned / NULL -> empty string / UTF-8 LF | |

### D. User Interface Impact

> User interface = CLI (`python -m src.ib_sync` / `src.exporter`) + **csv output contract** + logs + state.json

| # | Check Item | Conclusion |
|---|---|---|
| U1 | Have CLI parameters changed (added/removed/default values)? | |
| U2 | Under the same parameters, has implicit behavior changed (execution path/conditions/ordering)? | |
| U3 | Have output file names/locations/formats changed (csv / SQLite / log paths)? | |
| U4 | **Has the csv 12-column export schema changed** (column names/order/types/encoding)? If yes -> version bump + CHANGELOG entry + notify any downstream consumers | |
| U5 | Is log observability affected (process prints / RUN SUMMARY)? Has state.json schema changed? | |

### E. Cross-Module + Downstream Impact

- Which modules/callers depend on the modified function? (confirm with `grep`)
- **Downstream CSV consumers**: did the csv schema or semantics change? -> Bump the CSV schema version, document in CHANGELOG, notify any known consumers.
- Did state.json / SQLite schema change? -> Compatibility with old files?

### F. Risk Rating
- Green Low: constants/comments/docs, no change to computation logic
- Yellow Medium: parsing/export/storage logic, csv content generation
- Red High: Flex calls/rate limit/retries, state gate, csv schema contract, orchestration main flow

**Conclusion: APPROVED / NEEDS_FIX** — only APPROVED proceeds to Step 2.

---

## Step 2 — Automated Quality Checks

```bash
venv\Scripts\python.exe -m pytest tests/ -q          # full
venv\Scripts\python.exe -m pytest tests/test_<changed_module>.py -q   # focused
venv\Scripts\python.exe -m py_compile src/<file>.py  # Green: syntax only
```
Required: all green. New logic must have a corresponding test.

---

## Step 3 — Testing Recommendations

### 3a. By Risk Level
| Risk | Command |
|---|---|
| Green | `py_compile` + relevant pytest files |
| Yellow | Full pytest + offline vertical-slice demo (cached XML -> csv eyeball check) |
| Red | Full pytest + **mock verification** (flex_client / gate with injected now/session, **never call real Flex**) |

### 3b. Existing Verification Assets
| Asset | What It Verifies | Applicable Changes |
|---|---|---|
| `tests/test_parser*.py` | Parsing + type conversion + field robustness | parser.py |
| `tests/test_sqlite_store.py` | Schema / idempotency / filtering | sqlite_store.py |
| `tests/test_exporter.py` | 12-column contract / encoding / filtering | exporter.py |
| `tests/test_state.py` | Gate (injected now) / window / safe-mode | state.py |
| `tests/test_flex_client.py` | Two-step flow / 1018 no retry / 1009 retry (mock HTTP) | flex_client.py |
| `tests/test_integration.py` | End-to-end AC-1/5/6/7/13 + RUN SUMMARY | ib_sync.py |
| `tests/fixtures/sample_flex.xml` | Real (sanitized) Flex sample | Any parsing/export change |

> **Gap log**: if a class of change has no corresponding test -> note "suggest adding a test" in the Review.

### 3c. Test Recommendation Format
```
[Test Recommendation]
Scope of impact: [module / interface / contract]
Recommended tests: 1. [pytest file/command] — verifies [what]
Corner case verification: [items from C1-C12 that need to be run]
Negative impact: [possible regression points]
Real Flex call?: No (dev uses mock) / Yes (state why + respect 10 min interval)
Requires user action?: Yes/No (reason)
```

---

## Step 4 — Commit Message Template

```
[type]: [description]

Code Review: APPROVED
- Default behavior: [unchanged / changed: ...]
- Corner cases: C1[conclusion] C4[conclusion] C5[conclusion] ... (omit N/A)
- User interface: U4[csv schema unchanged / changed -> bumped + CHANGELOG] U5[...]
- Cross-module / downstream impact: [None / consumers must update ...]
Tests: PASSED ([N] passed) / SKIPPED (Green: syntax only)
Risk: Green / Yellow / Red

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

---

*Version: 1.0 | Created: 2026-05-20*
*TraderLens adjustments: CSV export schema stability (U4/E), Flex rate-limit iron rule (C4/Red), field robustness (C2/C9), pytest in place of backtest, RUN SUMMARY observability (U5)*
