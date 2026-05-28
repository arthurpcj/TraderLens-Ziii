# Spike 001 — IBKR Flex Query Connectivity Verification

> **Date**: 2026-05-20
> **Goal**: End-to-end verification of the IBKR Flex Query pipeline (Token + Query ID → download → parse → field coverage check) before implementing `src/ib_sync.py`. Removes technical risk up front.
> **Status**: ✅ Passed — see [ADR-001](../../decisions/001-drop-ibflex.md) and [ADR-002](../../decisions/002-flex-rate-limit-policy.md) for decisions derived from the spike.

---

## 1. Verification targets

| # | Item | Pass criterion |
|---|---|---|
| V1 | `ibflex` library usable | `pip install ibflex` succeeds, import clean |
| V2 | Token + Query ID configured correctly | `client.download()` returns non-empty XML |
| V3 | Two-step flow (SendRequest → GetStatement) handled automatically | ibflex internal, caller-transparent |
| V4 | Field coverage complete (14 core + 4 enhanced fields per parser spec) | all present |
| V5 | At least one NQ/MNQ/ES/MES futures trade captured | (precondition: account has futures fills in the last 30 days) |
| V6 | `orderReference` field round-tripped (strategy-tag carrier) | attribute present on sample trade |

---

## 2. Prerequisites — one-time IBKR-side configuration (user)

Walk through the external Flex Query setup guide once and complete these:

- [ ] Sign into [IBKR Client Portal](https://www.interactivebrokers.com) (not TWS).
- [ ] Settings → Account Settings → enable **Flex Web Service**, retrieve the **Token** (copy and save).
- [ ] Performance & Reports → Flex Queries → create an **Activity Flex Query** named `IB_Trade_Sync_Daily`:
  - Format = **XML**
  - Period = **Last 30 Days**
  - Section: **Trades** (level = **Executions**)
  - Tick all fields, especially `orderReference` / `expiry` / `multiplier` / `fifoPnlRealized` / `openCloseIndicator`.
  - (Optional) Section: **Cash Transactions**.
- [ ] Save, record the **Query ID** (a numeric string).
- [ ] Click **Run** in the web Flex Queries list to manually trigger once, then download the XML and eyeball it for field completeness.

---

## 3. Running the spike

### 3.1 Credentials

Populate `.env` at the project root:

```bash
IBKR_FLEX_TOKEN=<your_token_here>
IBKR_FLEX_QUERY_ID=<your_query_id_here>
```

`.env` is in `.gitignore` and will not leak.

### 3.2 Dependencies

```bash
pip install ibflex
```

(Note: the spike used `ibflex` to surface the library's bugs. The production code in `src/ib_sync.py` does not — see [ADR-001](../../decisions/001-drop-ibflex.md).)

### 3.3 Execute

```bash
python docs/studies/001_flex_connectivity_spike_20260520/spike.py
```

Expected output (illustrative):
```
=== TraderLens Spike 001: Flex Query Connectivity ===
Time: 2026-05-20 22:30:00
Token: ***ab12  Query ID: 12345678

[1/3] Downloading Flex statement (may take 10-30 sec)...
  OK: 152340 bytes in 8.3s
  Saved raw XML: raw_response_20260520_223000.xml

[2/3] Parsing XML...
  OK: 1 FlexStatement(s)
    Account: U1234567, period: 2026-04-20 → 2026-05-19, Trades: 47

[3/3] Field coverage check ...
  Field availability (sample = first trade, ID=12345678):
    ✓  tradeID                  = 12345678
    ✓  symbol                   = MNQM6
    ✓  underlyingSymbol         = MNQ
    ...
  Asset breakdown:
    Total       : 47
    Futures     : 47
    NQ/MNQ/ES/MES: 47
=== Spike complete ===
```

### 3.4 Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: ibflex` | Not installed | `pip install ibflex` |
| `IBKR_FLEX_TOKEN must be set` | `.env` not populated / wrong path | Check `.env` at project root |
| `Statement generation in progress` | Server-side report not ready | Wait 30 sec and retry (ibflex retries automatically in most cases) |
| Token error | Token expired or mistyped | Regenerate (note: shown only once) |
| `0 Trades` | No fills in the last 30 days / wrong period in the query | Compare against the web "Run" output, or widen the period |

---

## 4. Outputs

| File | Purpose | git tracked |
|---|---|---|
| `spike.py` | Spike script | ✅ |
| `raw_response_*.xml` | Actual XML retrieved (contains real trade data) | ❌ (`.gitignore` in this directory) |

---

## 5. After passing

V1-V6 all green → kick off `src/ib_sync.py`.

Any ✗ or surprise → record in a follow-up note and decide whether it affects the v1.0 CSV schema. If it does → bump the schema version + document in [CHANGELOG.md](../../../CHANGELOG.md).
