# INTERFACE CONTRACT — IB_Trade_Sync ↔ [MTS DevTest](https://github.com/arthurpcj/MTS-backtest)

> **Status**: 🎯 **csv schema v1.0 unchanged** (12 columns). **Behavioral semantics v1.1 adjusted** (2026-05-20, scheme E): ib_sync exports all NQ/MNQ/ES/MES trades, the `category` column carries the fixed value `PAPER_AUTO`, and the MTS side relies on the §4.2 smart matcher for filtering (no longer depends on `category`). The GSheet annotation layer is deferred to v2.
> **✅ Both projects confirmed (2026-05-21, see §5.6)**: (1) **C5 `ib_commission` sign** — csv carries IB's native **signed** value (costs negative / rebates positive); the MTS PnL formula uses `+`, not `−`; **do NOT take abs**. (2) **DC/DD MTS-side behavior** — 0-candidate trades enter the review queue (default skip, recoverable) / dual-source conflicts use field-level merge. **Neither the csv schema nor ib_sync code changes; version is not bumped.**
> **Scope**: This document defines the **single cross-project interface** = one csv file (12 columns). Cross-project scheduling is performed by **a user-layer wrapper.bat that chains the two project entry .bat files** (see §9); the wrapper does not belong to either project (it is a user-level business automation script).
> **Responsibility split**:
> - **IB_Trade_Sync project**: implements the §2 output csv schema + §6 user-classification UX + the project entry point `scripts/run_ib_sync.bat`
> - **MTS DevTest project**: implements the §3 csv reader + §4 enrichment (including the signal_id smart matcher) + adds a 4th value `cli_ib_sync` to D5 `recorded_by` + the project entry point `scripts/import_from_ib.bat`
> - **User layer**: writes `daily_run.bat` (wrapper, §9) chaining the two project entry points, dual-mode (manual double-click / Task Scheduler `--auto`)
> - **Any csv schema change**: must be reviewed in sync by both projects (see §5)

---

## 1. Upstream/Downstream Diagram (single csv interface + user-layer wrapper.bat scheduling)

```
┌──────────────────────────────────────────────────────────────────────────┐
│  User layer: daily_run.bat (wrapper, §9) — dual-mode: double-click / Task Scheduler --auto│
└──────┬───────────────────────────────────────────────────────────┬────────┘
       │                                                            │
       │ Step 1: call run_ib_sync.bat            Step 3: call import_from_ib.bat
       │                                                            │
       ▼                                                            │
┌─────────────────────────────────────────────────┐                 │
│  IB_Trade_Sync project (D:/02.Projects/13.IB_*/)│                 │
│                                                  │                 │
│  scripts/run_ib_sync.bat (project entry)         │                 │
│   └─ activate venv + python -m src.ib_sync       │                 │
│                                                  │                 │
│  ib_sync.py main flow:                           │                 │
│   ├─ IBKR Flex Query → SQLite (17 cols)         │                 │
│   ├─ SQLite ↔ Google Sheet (user tags category) │                 │
│   └─ auto-export csv                             │                 │
│                                                  │                 │
│        ↓                                         │                 │
│  data/exports/mts_trades_{date}.csv             │                 │
│  (12 cols, ★ sole cross-project interface, §2)  │                 │
└──────────────┬───────────────────────────────────┘                 │
               │ (one-way file interface, MTS reads)                 │
               │  ★ IB_Trade_Sync does not know MTS exists           │
               │  ★ does not call / does not know wrapper.bat exists │
               ▼                                                      │
┌─────────────────────────────────────────────────────────────────────┘
│  MTS DevTest project (<mts-dir>/)                ◄────
│                                                                       │
│  scripts/import_from_ib.bat (project entry)                            │
│   └─ activate venv + mts record --import-from-ib --date %TODAY%        │
│                                                                       │
│  mts record --import-from-ib:                                          │
│   ├─ read csv (§2) → validate schema v1.0                              │
│   ├─ filter category=MTS                                               │
│   ├─ pair open+close legs by FIFO                                      │
│   ├─ smart match signal_id (§4.2)                                      │
│   ├─ enrich derived (§3.1)                                             │
│   └─ write D5 actual_log.csv (REPLACE)                                 │
│                                                                       │
│        ↓                                                              │
│  D5 actual_log.csv (recorded_by=cli_ib_sync)                           │
└────────────────────────────────────────────────────────────────────────┘

Intermediate user action (wrapper Step 2): open Google Sheet, tag category via the L-column dropdown (~15 sec)
```

**Core boundary guarantees**:
- IB_Trade_Sync does not read any MTS file / does not call any MTS API / does not know any MTS command name
- The IB_Trade_Sync project's internal `scripts/run_ib_sync.bat` only runs itself; it does not call MTS
- The MTS project's internal `scripts/import_from_ib.bat` only runs its own mts record; it does not call ib_sync
- Cross-project scheduling is handled by **the user-layer wrapper.bat** that chains them (the wrapper does not belong to any project; the user writes/maintains it)
- signal_id is an MTS-internal concept that IB_Trade_Sync never knows
- Derivable information such as multiplier / fifo_pnl does not enter the csv (the MTS side computes it)
- Daily user actions: double-click the wrapper (1 click) + tag category in GSheet (~15 sec) + press Enter (interactive mode) — 0 commands in auto mode

---

## 2. 🎯 Output CSV Schema v1.0 (sole cross-project contract)

### 2.1 Filename Convention
- Single day: `mts_trades_{YYYY-MM-DD}.csv`
- Range: `mts_trades_{from}_to_{to}.csv` (v1.1 backlog; v1.0 supports single day only)
- Default location: `data/exports/` (relative to the ib_sync project root)
- The MTS-side yaml `ib_sync_export_dir` points to this path (e.g. `<tradelens-dir>/data/exports/`)

### 2.2 Encoding / Timezone / Number Format
- **Encoding**: UTF-8 (no BOM)
- **Line ending**: LF (Unix)
- **CSV dialect**: RFC 4180 (strings double-quoted, comma-separated, header on line 1)
- **Date**: `YYYY-MM-DD` (ISO 8601 date)
- **Time**: `HH:MM:SS` (24h, **US/Eastern timezone**, IB Flex `tradeTime` raw value)
- **Float**: prices to 2 decimal places, commission to 2 decimal places
- **NULL**: empty string (`,,`); do not write a literal `"NULL"`

### 2.3 Complete Field Table (12 columns, v1.0 frozen)

| # | Column | Type | NULL | IB Flex source field | Notes |
|---|--------|------|------|----------------|-------|
| 1 | `trade_id` | str | NO | `tradeID` | Primary key (IB globally unique); MTS-side audit reverse-lookup against IB raw |
| 2 | `trade_date` | str (YYYY-MM-DD) | NO | `tradeDate` | Trade date (US/Eastern) |
| 3 | `trade_time` | str (HH:MM:SS) | NO | `tradeTime` | Trade time (US/Eastern) |
| 4 | `underlying` | str | NO | `underlyingSymbol` | `NQ` / `MNQ` / `ES` / `MES` (only these 4 remain after ib_sync filtering) |
| 5 | `expiry` | str (YYYYMM) | NO | `expiry` | e.g. `202609` (M6); used by MTS-side audit (to identify cross-contract rolls) |
| 6 | `buy_sell` | str | NO | `buySell` | `BUY` / `SELL` (leg-level direction; MTS computes LONG/SHORT after pairing) |
| 7 | `quantity` | int | NO | `quantity` | Number of contracts (no sign; direction comes from buy_sell) |
| 8 | `trade_price` | float | NO | `tradePrice` | Actual fill price |
| 9 | `ib_commission` | float | NO* | `ibCommission` | Per-leg commission (including exchange fees), **IB's native signed value passed through unchanged**: **cost is negative** (e.g. `-0.62`, cash outflow), **rebate/credit is positive** (maker-rebate scenario). ib_sync **does NOT flip the sign or take absolute value**. When MTS computes actual_pnl_usd, it **adds** this signed value (not subtracts) (see §3.1 G6). **\*v1.1 exception**: when IB rarely omits commission, ib_sync exports an **empty string**, and the MTS side must handle it **NULL-safe as 0** (sign-neutral) (per REQUIREMENTS §6 field-missing fallback) |
| 10 | `open_close` | str | NO | `openCloseIndicator` | `O` (open) / `C` (close); used by MTS pairing |
| 11 | `category` | str | NO | (v1: state-machine binary value) | **v1.1 (2026-05-26 state machine)**: the IB_Sync side annotates state with setup_tag, **decided independently per trade_date** — **State A**: `PAPER_AUTO` (no IB_Sync setup_tag annotation for that day; csv contains all NQ/MNQ/ES/MES, behavior same as scheme E); **State B**: `MTS_CONFIRMED` (≥1 round-trip tagged within `MTS_RELEVANT_SETUPS` for that day; csv only contains the matched round-trips). The MTS side **branches on value** (~10 LOC): `MTS_CONFIRMED` → 0-candidate FORCE_WRITTEN + alert (a real MTS trade was lost); `PAPER_AUTO` → current DC review-queue behavior, default skip. **See §5.6 v1.1 (2026-05-26) C6/C7 for details.** |
| 12 | `notes` | str | YES | (tagged by user in GSheet, optional) | Free text (e.g. "network lag, late close by 30 sec") |

### 2.4 CSV Examples

```csv
# State A example (user did not annotate that day, category=PAPER_AUTO, MTS goes through DC default-skip for 0 candidates)
trade_id,trade_date,trade_time,underlying,expiry,buy_sell,quantity,trade_price,ib_commission,open_close,category,notes
12345678,2026-05-23,09:46:32,MNQ,202609,BUY,1,21521.50,-1.25,O,PAPER_AUTO,
12345679,2026-05-23,11:30:15,MNQ,202609,SELL,1,21640.00,-1.25,C,PAPER_AUTO,

# State B example (user tagged Q_intraday that day, category=MTS_CONFIRMED, MTS 0-candidate FORCE_WRITTEN+alert)
trade_id,trade_date,trade_time,underlying,expiry,buy_sell,quantity,trade_price,ib_commission,open_close,category,notes
12345680,2026-05-24,10:15:00,MNQ,202609,BUY,1,21580.00,-1.25,O,MTS_CONFIRMED,
12345681,2026-05-24,14:45:00,MNQ,202609,SELL,1,21650.00,-1.25,C,MTS_CONFIRMED,

# Header-only example (State B that day but 0 round-trips matched MTS_RELEVANT, or non-trading day; MTS silent exit 0)
trade_id,trade_date,trade_time,underlying,expiry,buy_sell,quantity,trade_price,ib_commission,open_close,category,notes
```

Note: 2 rows = 1 round-trip (open + close legs). The MTS side pairs them up on import. Discretionary user trades (where setup_tag is not in `MTS_RELEVANT_SETUPS`) **do not appear** in csv under State B (they remain in the internal ib_sync SQLite + local HTML pivot for the user).
Note: `ib_commission` is the **IB native signed value** — cost negative (`-1.25`), rebate positive. The MTS-side PnL formula **adds** it (§3.1 G6); do not take absolute value or flip sign.
Note: State A vs State B is judged **independently per trade_date** (the `category` column is the same for all rows within a csv, because State is a per-date decision). See §5.6 v1.1 (2026-05-26) C6 for details.

### 2.5 Field Order Locked
**Column order strictly follows §2.3 #1–#12.** The MTS side parses by column name (header), not by position, but when bumping the schema new columns may only be appended at the end (#13+).

### 2.6 Dropped Fields (design-decision record)

| Dropped field | Reason | How MTS obtains it |
|---|---|---|
| `multiplier` | IB native but derivable; multipliers are fixed for the 4 underlying contract types | MTS internally hardcodes the dict `{'MNQ': 2, 'NQ': 20, 'MES': 5, 'ES': 50}` |
| `fifo_pnl_realized` | IB's FIFO algorithm ≠ MTS R-multiple algorithm; including it causes more confusion than clarity | MTS computes `(close.price - open.price) × multiplier × qty × dir_sign + sum(ib_commission)` itself (commission is already signed: cost negative, adding = subtracting cost) |
| `signal_id` | MTS-internal concept; ib_sync should not know it (to avoid cross-project coupling) | MTS-side §4.2 smart matcher (reverse-lookup benchmark_log by entry_time + price + direction) |
| `asset_category` | ib_sync already filters for FUT, redundant in csv | Implicit (all rows in csv are FUT) |
| `currency` | All USD during paper | v1.0 assumes USD; v1.1 may add a column if multi-currency support is needed |

---

## 3. MTS-side Derived Field Enrichment (csv 12 cols → D5 33 fields)

After reading the csv, the MTS side `mts record --import-from-ib` computes the 33 D5 actual_log fields (per the MTS SPEC §9.4).

### 3.1 csv → D5 Mapping Table

| D5 field (# in SPEC G group) | Source | Algorithm |
|---|---|---|
| **G1 Identity** | | |
| `date` | csv `trade_date` (open leg) | copy directly |
| `mode` | MTS yaml | `get_mode_for_date(yaml, date)` per FR-RES-6 |
| `signal_id` | **MTS smart matcher** (§4.2) | benchmark_log candidates → auto / prompt / fallback |
| **G2 Setup Mapping** | | |
| `setup_id` | MTS benchmark_log reverse lookup | matched signal_id → benchmark_log.setup_id |
| `setup_id_backfilled` | derived | `true` (auto reverse-lookup) |
| **G3 Actual Entry** | | |
| `actual_entry_time` | csv `trade_time` (open leg, `open_close=O`) | open leg time |
| `actual_entry_price` | csv `trade_price` (open leg) | open leg price |
| `actual_entry_type` | inferred from dispatch_path | per benchmark.vix_dispatch_path: `default`→`STOP` / `alternate`→`LIMIT` / else `MARKET` |
| `actual_order_type` | inferred from dispatch_path + direction | per benchmark.entry_method + direction (e.g. `BUY_STOP_ORH` / `REBREAK_TOUCH_PB`) |
| `actual_size` | csv `quantity` (open leg) | sum if split across multiple legs |
| **G4 Actual Exit** | | |
| `actual_exit_time` | csv `trade_time` (close leg, `open_close=C`) | close leg time |
| `actual_exit_price` | csv `trade_price` (close leg) | close leg price |
| `actual_exit_type` | inferred from time + pnl + benchmark | (`exit_time ≥ 15:55` → `CLOSE_4PM`) elif (`hold_min ≥ 120` and setup=T2D120 → `D120`) elif (alternate + entry ≥ 2h + reverse-break OR → `REVERSE` #9 resolution) elif pnl > 0 → `TP` else `SL` |
| `actual_exit_method` | inferred | (`exit_type=CLOSE_4PM` → `4PM_FORCE`) else `OCO_AUTO` (paper assumes OCO bracket auto-exit) |
| `actual_hold_minutes` | derived | `(close_time - open_time).minutes` |
| **G5 Slippage** | | |
| `slippage_entry_pts` | derived vs benchmark | `actual_entry_price - benchmark.entry_price` (LONG: + bad; SHORT: sign-flipped to keep + bad) |
| `slippage_exit_pts` | derived vs benchmark | same |
| `slippage_usd` | derived | `(slip_entry + slip_exit) × multiplier_lookup(underlying) × actual_size` |
| **G6 Actual P&L** | | |
| `actual_pnl_pts` | derived | `(close.price - open.price) × dir_sign` (LONG=+1 / SHORT=-1) |
| `actual_pnl_usd` | derived | `actual_pnl_pts × multiplier_lookup(underlying) × actual_size + (open.ib_commission + close.ib_commission)` ⚠ **plus sign** — csv `ib_commission` is IB's native signed value (cost negative), so adding deducts the cost; rebates (positive) add as gains. **Do NOT subtract, do NOT take absolute value** (see §5.6 v1.1 semantic clarification 2026-05-21) |
| `actual_r_multiple` | derived | `actual_pnl_pts / benchmark.sl_dist_from_entry` |
| `actual_r_size` | derived | `actual_size / benchmark_planned_size` (SPLIT_0.5 = 0.5, FULL = 1.0) |
| `is_win` | derived | `actual_pnl_pts > 0` |
| **G7 V6 Audit Flags** | | |
| `actual_status` | default `FILLED` (trades fetched = filled) | per MTS FR-RES-3 |
| `BACKFILLED` | derived | `true` if `import_date != trade_date` (post-hoc backfill) |
| `FORCE_WRITTEN` | derived | `true` if signal_id matcher fallback (0 candidates) |
| `EXIT_AUTO_FROM_BENCHMARK` | always `false` | ib_sync has real exit data; no benchmark substitute needed |
| `filled_reason` | NULL by default | user may fill later via `mts record --edit` |
| **G8 User Notes** | | |
| `arbitration_observed_note` | NULL | csv `notes` from ib_sync does not map here (different semantics); user fills separately via `mts record --edit` |
| `notes` | csv `notes` | copy directly |
| **G9 Provenance** | | |
| `row_created_at` | MTS import timestamp | `now()` UTC |
| `recorded_by` | `cli_ib_sync` | **add 4th value to D5 schema** (per §5.5 MTS-side implementation checklist #1) |
| `client_version` | MTS git hash | MTS side |

### 3.2 Inferred-Field Accuracy Risk (MTS-side paper W1–W4 validation)

| Field | Inference algorithm | Accuracy risk | When inaccurate |
|---|---|---|---|
| `actual_entry_type` / `actual_order_type` | per benchmark.dispatch_path | 🟡 60% — accuracy of inferring LIMIT for the alternate path is uncertain | v1.1 adds a GSheet column (csv #13) for users to tag order type |
| `actual_exit_type` | fall-through 5-value decision | 🟡 boundary cases for D120/REVERSE are error-prone | correct via mts record --edit |
| `actual_exit_method` | default OCO_AUTO | 🟡 inaccurate when user closes manually | correct via mts record --edit |
| `actual_size` | sum csv `quantity` | 🟢 100% accurate (IB ground truth) | n/a |
| `signal_id` smart match | entry_time ±60s + price ±5pt | 🟢 in paper with dual setups ≤2 trades/day, ~85% unique-candidate auto-match | ~10% prompt user to choose, ~5% fallback FORCE_WRITTEN |

---

## 4. MTS-side `mts record --import-from-ib` Subcommand Behavior Contract

### 4.1 CLI Signature (MTS project implementation)
```
$ mts record --import-from-ib --date <YYYY-MM-DD> [--backfill] [--csv <path>] [--dry-run]
```

| Argument | Required/Optional | Default | Description |
|---|---|---|---|
| `--import-from-ib` | required (flag) | — | trigger this branch |
| `--date` | required | — | target trade date (YYYY-MM-DD) |
| `--backfill` | optional | inferred from `--date < today` | explicitly tag BACKFILLED=true |
| `--csv` | optional | `<ib_sync_export_dir>/mts_trades_{date}.csv` (yaml config) | csv file path |
| `--dry-run` | optional | false | validate + print only; do not write D5 |

### 4.2 Behavior Flow (including signal_id smart matcher algorithm)

```python
def import_from_ib(date, csv_path):
    # Step 1: Read + validate
    rows = read_csv(csv_path)
    validate_schema_v1_0(rows)  # 12 cols, header match
    
    # Step 2: (v1.1 scheme E) no longer pre-filter by category — csv is already
    #         the full NQ/MNQ/ES/MES set with category fixed to PAPER_AUTO. Filtering
    #         is delegated to Step 4 smart matcher (benchmark reverse-lookup).
    #         When GSheet is reinstated in v2: restore `mts_rows = [r for r in rows if r.category == 'MTS']`.
    candidate_rows = rows
    
    # Step 3: Pair open + close legs (FIFO within trade_date by underlying+expiry+opposite direction)
    trade_pairs = pair_open_close_fifo(candidate_rows)
    
    # Step 4: Smart match signal_id
    benchmark_rows = read_benchmark_log(date)
    for pair in trade_pairs:
        direction = 'LONG' if pair.open.buy_sell == 'BUY' else 'SHORT'
        candidates = [
            b for b in benchmark_rows
            if b.direction == direction
            and abs(time_diff(b.entry_time, pair.open.trade_time)) <= 60  # ±60 sec
            and abs(b.entry_price - pair.open.trade_price) <= 5.0           # ±5pt MNQ; per-underlying tolerance v1.1
        ]
        if len(candidates) == 1:
            pair.signal_id = candidates[0].signal_id
            pair.force_written = False
        elif len(candidates) > 1:
            pair.signal_id = prompt_user_disambiguate(candidates, pair)  # CLI prompt
            pair.force_written = False
        else:  # 0 candidates
            # 0 matches could mean either (a) an MTS trade where benchmark did not match (rare)
            # or (b) a discretionary user trade. Concrete handling is an MTS-side implementation
            # decision (now refined as: enter the MTS review queue, default skip but recoverable;
            # mechanism detailed in MTS project docs / §5.6 "DC"). ib_sync export is unaffected
            # — it still exports the full set.
            pair.signal_id = None
            pair.skip_reason = "no_benchmark_candidate (MTS-side review queue: default skip, recoverable)"
            continue  # do NOT write D5 here; MTS routes to its review queue
    
    # Step 5: Enrich derived (per §3.1)
    d5_rows = [enrich_derived(pair, benchmark_log, yaml) for pair in trade_pairs]
    
    # Step 6: Write D5 (REPLACE by (date, signal_id))
    for row in d5_rows:
        d5_actual_log.replace(row)
    
    # Stats (v1.1 scheme E: categorize by matched / skipped; no more category=MTS pre-filter)
    print(f"Imported {len([p for p in trade_pairs if p.signal_id])} trades:")
    print(f"  - {sum(1 for p in trade_pairs if p.signal_id and not p.force_written)} matched (benchmark hit)")
    print(f"  - {sum(1 for p in trade_pairs if getattr(p, 'skip_reason', None))} skipped (no benchmark candidate, assumed non-MTS per scheme E)")
    print(f"  - {sum(1 for p in trade_pairs if p.incomplete)} incomplete pairs skipped (pending close)")
```

### 4.3 Error Handling
| Error | Behavior |
|---|---|
| csv file does not exist (file absent entirely) | exit 1 + message "Run ib_sync first: cd <ib_sync_project> && python -m src.exporter --date {date}" (reasonable when called manually for a single day) |
| **csv file exists but contains only the header row (header-only)** | **silent exit 0 + 0 rows imported** (State B empty / non-trading day / all discretionary trades filtered out; see §5.6 v1.1 (2026-05-26) C10) |
| csv schema does not match v1.0 (column names / column count) | exit 1 + clear error "Schema mismatch, expected v1.0 (12 cols), got <N>. Check INTERFACE_CONTRACT.md §2.3" |
| A trade with only 1 leg (open without close) | warning + skip (pending close; handled on next import) |
| signal_id smart match >1 candidate | CLI prompt for user to choose (per §4.4) |
| signal_id smart match 0 candidates | **Branch on csv `category` value** (§5.6 v1.1 (2026-05-26) C7): `MTS_CONFIRMED` → FORCE_WRITTEN + alert (a real MTS trade was lost, recover it); `PAPER_AUTO` → review queue, default skip (current DC behavior, §5.6 v1.1 (2026-05-21)) |
| D5 already has a row with same (date, signal_id) | MTS-side field-level merge: IB numeric values overwrite + user notes preserved (not a blunt REPLACE; see §5.6 "DD" + MTS docs) |

### 4.4 Ambiguous-Match CLI Prompt Example

```
$ mts record --import-from-ib --date 2026-05-23
Reading csv: <tradelens-dir>/data/exports/mts_trades_2026-05-23.csv
Found 2 MTS trade pairs, 1 MANUAL skipped.

[Ambiguous match] Trade @ 09:46:33 MNQ BUY @ 21521.50 (close @ 11:30:15 @ 21640.00):
  1. signal_id=20260523_0946_LONG_1 (setup=S1_T2D120_15m, predicted entry=21520, diff=+1.5pt)
  2. signal_id=20260523_0946_LONG_2 (setup=S2_ORPCT40_15m, predicted entry=21521, diff=+0.5pt)
Select [1/2]: > 2

Trade @ 14:20:45 MNQ BUY @ 21580.00 → matched signal_id=20260523_1420_LONG_1 (S1, auto)

Wrote 2 D5 rows. 0 FORCE_WRITTEN. 0 incomplete pairs skipped.
```

---

## 5. Version Management + Schema Change SOP

### 5.1 Version Number
- **csv schema version**: v1.0 (defined in §2 of this document)
- Identifier: documentation + cross-project commit message (the csv file does **not** embed a magic line, to keep pandas reading simple)
- Breaking change → major bump (v2.0); additive column (backward compatible) → minor bump (v1.1)

### 5.2 Adding Columns (minor bump, backward compatible)
- **Process**: IB_Trade_Sync raises a proposal → MTS-side Claude reviews → both sides cross-link via commit messages → IB_Trade_Sync appends the column at the end of §2.3 (#13+) → MTS-side reader adds optional parsing
- **MTS-side strategy**: old csv (without the new column) defaults the new field to NULL/default → backward compatible
- **Example v1.1**: add #13 `order_type_user_labeled` (user tags order type details in GSheet)

### 5.3 Changing/Removing Columns (major bump, incompatible)
- **Process**: raise a proposal → MTS-side Claude **must approve** → both sides release in sync → MTS-side reader adds a `--schema-version v2.0` flag for strict checking
- **Extremely rare** (v1.0 is designed for long-term stability; 5+ years of paper → live_100 should not need a major bump)

### 5.4 Cross-project Commit Message Template

**IB_Trade_Sync project commit**:
```
feat(csv-schema): bump v1.0 → v1.1, add order_type_user_labeled column

Schema change: add column #13 order_type_user_labeled (str, NULL allowed).
Backward compatible: MTS side reads NULL if column missing.

Coordinated with MTS DevTest project: see commit <MTS-side-commit-hash>.
Updated: INTERFACE_CONTRACT.md §2.3 (column #13 added).
```

**MTS DevTest project commit**:
```
feat(ib-import): support csv schema v1.1 from IB_Trade_Sync

IB_Trade_Sync bumped csv schema to v1.1 (added order_type_user_labeled column).
This commit:
  - reader handles column #13 if present (graceful NULL if v1.0)
  - enrich actual_order_type from column #13 instead of dispatch_path-based inference

Coordinated with: <tradelens-dir> commit <IB-side-commit-hash>.
Reference: <tradelens-dir>/INTERFACE_CONTRACT.md §2.3 v1.1.
```

### 5.5 MTS-side Implementation Checklist (this contract's requirements on the MTS project)

| # | MTS-side action | File / Location | Timing |
|---|---|---|---|
| 1 | Add 4th value `cli_ib_sync` to the D5 `recorded_by` enum | SPEC §9.4 G9 #32 | **Now (before paper launch)**, ~10 min |
| 2 | Add backlog entries (W5+ enablement + W4 accuracy validation + wrapper.bat scheduling design) | MEMORY.md `paper W4+ backlog` section | **Now**, ~10 min |
| 3 | Annotate Launch Checklist §3.1 (mts record paper W5+ invoked by wrapper.bat) | LAUNCH_CHECKLIST §3.1 | **Now**, ~10 min |
| 4 | yaml `ib_sync_export_dir` field (config pointing to ib_sync data/exports/) | live_config.yaml schema | paper W4 EOD (before enablement) |
| 5 | Implement `mts record --import-from-ib` subcommand | `tools/live/actual_record.py` | paper W5+ |
| 6 | csv reader (strict v1.0 schema validation) | `tools/live/ib_csv_reader.py` | paper W5+ |
| 7 | signal_id smart matcher algorithm (§4.2) | same as above | paper W5+ |
| 8 | derived-field enrichment (per §3.1 mapping table) | same as above | paper W5+ |
| 9 | Ambiguous CLI prompt UX (§4.4) | same as above | paper W5+ |
| 10 | **Project entry `scripts/import_from_ib.bat`** (activate venv + `mts record --import-from-ib --date %TODAY%`) | new file under the MTS project `scripts/` | paper W5+ |
| **11** | **`category` value branching** (~10 LOC): `MTS_CONFIRMED` → 0-candidate FORCE_WRITTEN + alert; `PAPER_AUTO` → current DC review queue (per §5.6 v1.1 (2026-05-26) C7) | `tools/live/ib_csv_reader.py` | paper W5+ |
| **12** | **Header-only csv unit test** (confirm silent exit 0 + 0 rows, per C10); likely 0 LOC changes (csv reader probably already defaults this way) | tests | paper W5+ |
| **13** | **wrapper.bat last 90-day loop** (user-layer, not within the MTS project; per C9, paired with IB_Sync `--lookback 90`) | user's own `daily_run.bat` | paper W5+ |

→ Do #1 + #2 + #3 now (~30 min); #4–#13 are implemented in paper W5+ following §3 + §4 + §9 + §5.6.

> ⚠ **When implementing #5–#13, you must first read all of §5.6's v1.1 Changelogs** (read in reverse chronological order):
> - **2026-05-26 (state machine + binary category)**: csv content scoped per trade_date State A/B; `category` is binary (`PAPER_AUTO`/`MTS_CONFIRMED`); MTS branches on category for 0-candidate handling (C7); header-only csv → silent exit 0 (C10); wrapper 90-day loop (C9)
> - **2026-05-21 (DC/DD)**: 0-candidate default-skip (current PAPER_AUTO path still holds); D5 dual-source field-level merge
> - **2026-05-21 (commission sign)**: IB native signed value; MTS formula uses `+`, not `−`
> - **2026-05-20 (scheme E)**: largely superseded by 2026-05-26; C1/C2 behaviors have been replaced by the state machine (State A still follows the scheme E path)

**User-layer wrapper.bat (§9)**: does not belong to the MTS project; the user creates it themselves from the §9 template (desktop / `D:/Scripts/` / any location). The MTS project only provides the `scripts/import_from_ib.bat` entry point for the wrapper to call.

### 5.6 Changelog — Contract Behavior Change Log

> When the csv schema column structure is unchanged (this case), record only **behavioral/semantic changes**. The schema version is bumped only for column-structure changes.

#### v1.1 Behavioral Adjustment (2026-05-20, scheme E + scope reduction)

**Background**: The IB_Trade_Sync project scope was reduced — the GSheet annotation layer is deferred to v2, replaced by "export everything + MTS-side matcher filtering" (scheme E). See IB-side [REQUIREMENTS v1.1 rev6](REQUIREMENTS.md).

**csv schema**: ✅ **unchanged** (still 12 columns v1.0). MTS reader needs no parse changes.

**🎯 MTS-side handoff checklist (new/changed this round, to be implemented under §5.5 #5–#9)**:

| # | Change | Original behavior (v1.0) | New behavior (v1.1 scheme E) | MTS-side action |
|---|---|---|---|---|
| C1 | csv content scope | Only `category=MTS` trades | **Full NQ/MNQ/ES/MES set** (including discretionary non-MTS trades) | Remove the `category=='MTS'` pre-filter in import Step 2 (§4.2) |
| C2 | `category` column (#11) semantics | User-tagged `MTS`/`MANUAL` | Fixed value `PAPER_AUTO` | MTS **ignores this column**, does not branch on it |
| C3 | smart matcher "0 candidates" | `FORCE_WRITTEN` strong-write to D5 | Default no-write to D5 (assume discretionary trade) | **Refined on 2026-05-21 by DC**: enter the MTS review queue (default skip, recoverable); see DC in this section |
| C4 | Filter location | ib_sync side (category) | MTS side (benchmark reverse-lookup) | The matcher becomes the single source of truth for filtering |

**C3 trade-off** (MTS-side decision): matcher 0-match has two possibilities — (a) a real MTS trade outside tolerance (SKIP causes a missed write, requiring manual `mts record` backfill) / (b) a discretionary user trade (SKIP is correct). The data cannot distinguish → default SKIP, trading "occasional missed writes" for "no D5 pollution". During paper, with dual setups ≤2 trades/day + matcher ~85% hit rate, the miss probability is low and acceptable.

**Not affected**: signal_id concept / expiry format (still YYYYMM, fixed on the IB side) / filename / path / encoding / D5 enrichment mapping (§3.1) — all unchanged.

**v2 rollback point**: after the GSheet annotation layer is reinstated, restore C1 (`category=='MTS'` pre-filter) + C2 (real MTS/MANUAL values) + C3 (FORCE_WRITTEN). Zero schema changes.

#### v1.1 Semantic Clarification (2026-05-21): `ib_commission` sign

**Background**: When the MTS side read the csv, the sign was inconsistent — the contract example originally used the positive value `1.25` and §3.1's formula used a minus sign (assuming commission is a positive cost), but **the actual csv and IB's native XML both carry negative values** (`-0.62`). Investigation showed the ib_sync parser/exporter **performs no sign transformation; this is IB's native convention** (commission as cash outflow → negative). The original positive-value example was a **guess made before real data was obtained**, and it was the opposite of reality.

**csv schema**: ✅ **unchanged** (still 12 columns v1.0; `ib_commission` still float). Only the **sign semantics are clarified**; the version is not bumped.

**Decision (option B — retain IB's native sign, update the contract formula)**: ib_sync **passes through the signed commission value unchanged** (cost negative / rebate positive), **does not flip the sign or take absolute value**.

| # | Change | Original (incorrect) | New (2026-05-21) | MTS-side action |
|---|---|---|---|---|
| C5 | `ib_commission` sign + PnL formula | Example `1.25` positive; formula `− (open.comm + close.comm)` | IB native **signed** (cost negative such as `-0.62` / rebate positive); formula changed to `+ (open.comm + close.comm)` (§2.3 #9 / §2.4 / §2.6 / §3.1 G6 all updated) | Change enrichment formula operator from `−` to `+`; **do NOT** `abs()` or flip the sign |

**Why option B instead of ib_sync flipping to positive (option A)**: ① **Rebate self-consistency** — for maker rebates, IB commission is positive; option A's `abs()` would treat rebates as costs (reversed accounting); option B adding the signed value **handles both cost (negative) and rebate (positive) automatically and correctly**. ② **Audit reconciliation** — the csv value matches IB raw XML byte-for-byte (trade_id audit reverse-lookup needs no translation). ③ **Multi-broker pass-through** — flipping the sign would force every future `<broker>_sync` to replicate the flip logic; broker conventions may differ; pass-through is cleanest. ④ commission's **sign is its semantics** (cost vs rebate); no other column carries this, so flipping the sign = information loss (different from `quantity`'s abs — where direction is carried separately by `buy_sell`).

**ib_sync-side action**: ✅ **zero code changes** (parser/exporter already passes the raw value through; behavior is correct). Documentation revision only.

**Not affected**: NULL commission fallback still defaults to 0 (sign-neutral) / all other fields unchanged.

#### v1.1 MTS-side Behavior Refinement (2026-05-21): DC review / DD conflict

> ⚠ The following are **MTS-side implementation behaviors**, listed here only for contract completeness. **csv schema / file format / encoding / ib_sync code all unchanged** (no version bump). Mechanism details evolve with MTS iteration; **the MTS project docs are authoritative**, and this contract does not duplicate its internal design (zero coupling).

- **DC (0-candidate review)**: smart-matcher 0 matches no longer trigger `FORCE_WRITTEN` strong-write to D5 (which would pollute) and is no longer just silently skipped — instead, they enter the **MTS-side review queue** (default skip, **recoverable**). This supersedes the old SKIP / FORCE_WRITTEN descriptions in §4.2 / §4.3 / §7 (those two locations had become mutually contradictory and are now aligned to point to this entry).
- **DD (dual-source conflict)**: when ib_sync writes and the user's `mts record` writes the same `(date, signal_id)`, change from blunt "REPLACE latest wins" to **field-level merge**: IB numeric values overwrite + user notes preserved.
- **IB-side action**: ❌ none (neither csv nor ib_sync code changes).

#### v1.1 Behavioral Adjustment (2026-05-26, setup_tag-driven state machine + binary category)

> **Background**: IB_Sync has now launched a local annotation layer (annotations.csv; the user tags setup_tag/score/notes in review.bat, see [DATA_ARCHITECTURE.md](DATA_ARCHITECTURE.md)). With this, IB_Sync can now **pre-scope the quantitative trade set for MTS**; MTS no longer needs the smart matcher to reverse-lookup "is this trade an MTS trade?".

**csv schema**: ✅ **unchanged** (still 12 columns v1.0). What changes is the **csv content scope** + **`category` column (#11) binary semantics** + a new **re-export trigger protocol**.

**Core idea**: csv content **tightens progressively** with IB_Sync's annotation state; the MTS side reads the `category` column and branches (~10 LOC) to explicitly handle both states. MTS does not need to decide "is this an MTS trade?" — `category=MTS_CONFIRMED` means IB_Sync has asserted this trade belongs to the MTS scope.

##### 🎯 State Machine + MTS-side Action Checklist

| # | Change | Original (scheme E v1.1) | New (state machine v1.1) | MTS-side action |
|---|---|---|---|---|
| **C6** | csv content scope (judged independently per trade_date State) | Full NQ/MNQ/ES/MES (including discretionary trades) | **State A** (no IB_Sync setup_tag annotations that day): full set (same as scheme E)<br>**State B** (≥1 round-trip tagged within `MTS_RELEVANT_SETUPS`, currently = `{Q_intraday}` for that day): only MTS_RELEVANT-matched round-trips (including their open + close legs) | No code changes (IB_Sync determines per-day csv content; MTS reads as-is) |
| **C7** | `category` column (#11) binary | Fixed `PAPER_AUTO` (MTS ignores) | State A: `PAPER_AUTO` (current)<br>State B: `MTS_CONFIRMED` (new value, indicating IB_Sync has confirmed this is an MTS trade) | **Add ~10 LOC branch**: `MTS_CONFIRMED` → 0-candidate → **FORCE_WRITTEN + alert** (a real MTS trade was lost, must be recovered); `PAPER_AUTO` → current DC review queue (default skip) |
| **C8** | re-export trigger protocol | ib_sync main flow auto-exports the single-day csv | **Dual trigger**: ① ib_sync main flow auto-exports the current day's csv (state machine takes effect automatically); ② review.bat appends a re-export step at the end, by default scanning the **last 90 trade_dates**, propagating user tag changes immediately | wrapper-side paired loop (see C9) |
| **C9** | lookback window mutual convention | Single-day import (`--date X`) | **wrapper.bat must loop the same lookback window as IB_Sync** (default 90). If IB_Sync uses `--lookback 365` and modifies ancient csv files while the wrapper only loops 7 days → silent drift. **Recommendation: both sides hard-code N=90; in extreme cases override explicitly on both sides in sync.** | wrapper.bat (user layer) implements `for d in last 90 days; do import --date $d done`; single-day import is idempotent (REPLACE by date,signal_id); re-importing the same day is harmless |
| **C10** | header-only csv handling | (unspecified) | When IB_Sync re-exports, **every trade_date within the lookback window writes a csv**, even if that day has no MTS_CONFIRMED round-trips (header-only). Possible scenarios: (a) user tagged everything as discretionary, State B is empty for the day; (b) non-trading day; (c) State A filters everything out | MTS single-date import on a header-only csv must **silent exit 0 + 0 rows imported** (the current csv reader probably defaults to this; only requires testing to confirm / possibly +5 LOC) |

##### 💡 Why this design — explicit expression of "user-vetted vs not vetted"

scheme E pain point: when MTS receives the csv, it does not know whether a trade is (a) a real MTS trade IB_Sync failed to tag / (b) a discretionary user trade. For 0 candidates, it can only conservatively skip → occasional missed writes of real MTS trades.

state-machine solution: the `category` column directly tells MTS "has IB_Sync vetted this day":
- `PAPER_AUTO` → "not vetted, judge conservatively yourself" (current)
- `MTS_CONFIRMED` → "I have vetted, these are all MTS trades; on 0 candidates please alert and recover"

Cost: ~30–40 LOC on the IB_Sync side (state machine + filter), ~10 LOC on the MTS side (category branch). In return on the D5 side: 0 misjudgments in State B, occasional in State A (tolerable during paper, will be upgraded in D3b).

##### 🧱 Other clauses in this contract revised

| Section | Old statement | New statement |
|---|---|---|
| §2.3 #11 `category` | Fixed `PAPER_AUTO` | **Binary**: `PAPER_AUTO` (State A) / `MTS_CONFIRMED` (State B), decided by the IB_Sync-side state machine |
| §2.4 csv example | category=MTS / category=MANUAL | Example updated to include both `PAPER_AUTO` and `MTS_CONFIRMED` examples |
| §4.2 Step 4 0-candidate handling | Enter MTS review queue, default skip (DC) | Branch on csv `category` column (C7); MTS_CONFIRMED → 0-candidate → FORCE_WRITTEN + alert; PAPER_AUTO → still DC |
| §4.3 error handling "csv not exists → exit 1" | csv not exists → exit 1 | **header-only csv (file exists, no data rows) → silent exit 0 + 0 rows imported** (new behavior, see C10); csv completely missing still → exit 1 (reasonable for single-day manual calls) |
| §5.5 MTS-side implementation checklist | #5–#9 paper W5+ | Add **#11 binary category branch** + **#12 header-only csv test** (~15 LOC + tests), same timing |
| §7 boundary cases | Discretionary trades may be missed (scheme E) | State A same as scheme E (occasional); State B 0-error (IB_Sync vetted); residual D3a pollution backlog is upgraded by the MTS D5 IGNORED mechanism (D3b) |
| §9.6 wrapper backlog | wrapper loops last 7 days | wrapper loops last **90** days (C9 pairing); kept consistent with IB_Sync `--lookback` on both sides |

##### 🔒 Not affected

- csv schema (column names/positions/types/count/encoding) — all v1.0 frozen
- Filename convention `mts_trades_{YYYY-MM-DD}.csv` — unchanged
- Single-day csv self-containment + leg-level granularity — unchanged
- §3.1 csv → D5 enrichment mapping — unchanged (except 0-candidate handling branches per C7)
- §5.6 v1.1 (2026-05-20) scheme E's C1–C4 — mostly still hold; only C3 (0-candidate behavior) is superseded by C7's category-based branch

##### 📅 Implementation Timing

- **IB_Sync side**: now (implemented together with this changelog commit, feat/mts-confirmed-export)
- **MTS side**: in paper W5+, done together with the original §5.5 #5–#9 (one-shot, no need to pre-implement)
- **wrapper.bat**: user layer; the user writes the 90-day loop per C9 before paper W5+ enablement
- **Transition period (before paper W4)**: if MTS does not consume csv = 0 impact; if MTS consumes the old csv early = current behavior (all PAPER_AUTO, scheme E path), fully backward compatible

---

## 6. User GSheet UX (legacy v1.0 design, **v2 backlog** — superseded by §5.6)

> ⚠ **Status note**: this section captures the original GSheet-based labeling
> workflow (MTS/MANUAL dropdown, manual EOD tagging). In v1.1 (§5.6) the
> category column is driven by the local **PAPER_AUTO ↔ MTS_CONFIRMED state
> machine** (annotations.csv + setup_tag), and the GSheet layer is **deferred
> to v2 indefinitely**. The 17-column table below reflects the legacy v1.0
> schema before spike-002 added `data_source` / `order_ref` (now 20 columns
> per REQUIREMENTS FR-STORE-2). Retained for design-history reference only.

### 6.1 GSheet Layout (worksheet `trades`)

Display 17 columns (all SQLite fields); user edits only 2 columns:

| Column | Name | Purpose | User-editable? | Displayed? |
|---|---|---|---|---|
| A | trade_date | Trade date | ❌ | ✅ |
| B | trade_time | Trade time | ❌ | ✅ |
| C | underlying | NQ/MNQ/ES/MES | ❌ | ✅ |
| D | expiry | Contract month | ❌ | ✅ |
| E | buy_sell | BUY/SELL | ❌ | ✅ |
| F | quantity | Quantity | ❌ | ✅ |
| G | trade_price | Trade price | ❌ | ✅ |
| H | multiplier | MNQ=2 etc. | ❌ | ✅ (used by internal pivot) |
| I | ib_commission | Per-leg commission | ❌ | ✅ |
| J | open_close | O/C | ❌ | ✅ |
| K | fifo_pnl_realized | IB FIFO PnL | ❌ | ✅ (used by user's internal pivot) |
| **L** | **category** | **A/B classification** | ✅ **dropdown** (MTS / MANUAL) | ✅ |
| **M** | **notes** | **Notes** | ✅ free text | ✅ |
| N | trade_id | IB unique ID | ❌ | (hidden) |
| O | category_set_at | User tag timestamp | ❌ | (hidden, audit) |
| P | row_created_at | ib_sync fetch timestamp | ❌ | (hidden, audit) |
| Q | source_run_id | ib_sync run ID | ❌ | (hidden, audit) |

### 6.2 User Daily Operations

```
After close (T+1 morning):
  1. User opens GSheet → sees previous day's new trades (L column category empty)
  2. Select MTS or MANUAL from the L-column dropdown (~3 sec/trade)
  3. Optionally fill notes in column M (~10 sec, only for special cases)
  4. Close GSheet (next ib_sync run syncs back to SQLite)

(Optional) User exports class-B csv:
  $ cd <tradelens-dir>
  $ python -m src.exporter --date 2026-05-23
  → writes data/exports/mts_trades_2026-05-23.csv

MTS-side import:
  $ cd <mts-dir>
  $ mts record --import-from-ib --date 2026-05-23
  → smart-match signal_id + enrich + write D5
```

### 6.3 GSheet Enhancements (within this project, does not affect the interface)

- Trade-level merged display: use GSheet QUERY/formulas on another sheet `trades_view` to show 1 trade per row (merging open+close), more user-friendly (underlying data remains leg-level)
- Top banner cell (A1 or dedicated): ib_sync writes alerts (`[OK] last_run` / `[WARN] gap=8 days` / `[FAIL] Token expired`)
- Pivot sheet: pivot discretionary vs MTS PnL by underlying / category / month

---

## 7. Failure / Boundary-case Consistency (cross-project compatibility)

| Boundary case | IB_Trade_Sync behavior | MTS-side appearance |
|---|---|---|
| **Complete fetch failure** (Flex query not generated for the day / token invalid) | state.json not updated → retried next run | 0 rows in D5 for that day → `actual_status=MISSED` (V6 FR-RES-3) |
| **Partial fetch** (Flex Last 30 Days misses 1–2 days) | tradeID primary key + next 30-day fetch backfills the missed trades | Same MISSED placeholder → subsequent import REPLACE |
| **User did not tag category** | GSheet leaves it pending review (`category=NULL`); exporter does not output it | Same MISSED placeholder → REPLACE after user tags + imports |
| **User tagged incorrectly (B → A)** | csv does not contain this trade; when user discovers later → change to MTS in GSheet → re-export → MTS REPLACE | Consistent |
| **signal_id smart match 0 candidates** (discretionary trade mis-tagged as B / entry severely deviates) | n/a (ib_sync does not care) | MTS-side review queue: default skip, recoverable (see §5.6 "DC" + MTS docs) |
| **signal_id smart match >1 candidates** (dual-trigger dual-setup on same bar) | n/a | CLI prompt for user to choose (§4.4) |
| **Cross-day hold** (open Mon 23:00 / close Tue 01:00) | 2 trades land in csv files for different trade_dates | MTS single-day import only sees 1 leg → warning skip (pending close). v1.1 backlog: `--from --to` range import to pair cross-day legs |
| **IB auto 4PM force close** | csv still has 2 rows (open + close; both are fills from IB's perspective) | MTS pairs correctly; exit_type inferred as `CLOSE_4PM` (per §3.1) |
| **Dual-source conflict** (ib_sync writes + user `mts record --backfill` writes the same signal_id) | n/a | MTS-side field-level merge: IB numeric values overwrite + user notes preserved (not a blunt REPLACE; see §5.6 "DD" + MTS docs); BACKFILLED flag audit |
| **Stage 3+ add instrument (NQ active)** | csv schema unchanged (underlying column already includes 4 types) | Add NQ to yaml `instruments_active` → enrichment auto-covers |
| **Instruments outside NQ/MNQ/ES/MES** (e.g. ZN / CL / options) | ib_sync filters them out (FR-FETCH-4), they do not enter SQLite | n/a |
| **IB Flex field missing** (e.g. some trade has NULL commission) | csv writes NULL (empty string) | MTS reader is NULL-safe; commission NULL → defaults to 0 |

---

## 8. Testing / Verification Hooks

### 8.1 IB_Trade_Sync Project-side Tests
- `tests/test_exporter.py::test_csv_schema_v1_0_12_cols` — assert exported csv has 12 columns, order strictly per §2.3
- `tests/test_exporter.py::test_only_mts_category_exported` — class-A + untagged trades do not appear in csv
- `tests/test_exporter.py::test_csv_encoding_utf8_lf` — strict encoding / line ending

### 8.2 MTS Project-side Tests (implemented at paper W5+)
- `tests/live/test_ib_csv_reader.py::test_schema_v1_0_strict` — mock 12-col csv → assert parse OK; 11 cols → exit 1 with clear error
- `tests/live/test_ib_csv_reader.py::test_pair_open_close_fifo` — mock open + close legs same date+underlying+expiry → 1 D5 row
- `tests/live/test_ib_csv_reader.py::test_signal_id_match_unique` — mock 1 candidate → auto match
- `tests/live/test_ib_csv_reader.py::test_signal_id_match_ambiguous` — mock 2 candidates → CLI prompt mock
- `tests/live/test_ib_csv_reader.py::test_signal_id_match_zero` — mock 0 candidates → IMPORTED_<id> + FORCE_WRITTEN=true
- `tests/live/test_ib_csv_reader.py::test_d5_replace_idempotent` — mock D5 already has a row + import csv with same signal_id → REPLACE

### 8.3 Cross-project Integration Test (must pass before paper W5+ enablement)
- End-to-end: mock IB Flex response → full ib_sync flow → generate csv → MTS import → assert D5 row correct
- Run by MTS-side Claude/developer at paper W4 EOD

---

## 9. User-layer wrapper.bat Scheduling Design (informational — does not belong to any project)

### 9.1 Purpose
wrapper.bat is a **user-level business automation script** that chains the IB_Trade_Sync project entry + GSheet user tagging + MTS project entry. It does not belong to any project (put it on the desktop / `D:/Scripts/` / any location). The same .bat is **dual-mode**: manual double-click (interactive + debug-friendly) / Task Scheduler `--auto` (background + async GSheet tagging).

### 9.2 Design Principles
- **Separation of concerns**: IB_Trade_Sync provides its own project entry bat (runs itself); MTS provides its own project entry bat (runs itself); the wrapper only chains them
- **Dual-mode**: one .bat, `--auto` argument switches between interactive / background
- **Failures surfaced**: in interactive mode, the terminal window shows them directly; in auto mode, log to file + GSheet A1 banner
- **Zero cross-process complexity**: no subprocess.run hook callbacks, no cross-project Python imports, no mutual venv activation

### 9.3 Each Project's Entry .bat (maintained separately, no mutual calls)

**IB_Trade_Sync provides** `scripts/run_ib_sync.bat`:
```batch
@echo off
REM Project entry: runs the ib_sync main flow (fetch Flex + sync GSheet + auto-export csv)
cd /d <tradelens-dir>
call venv\Scripts\activate.bat
python -m src.ib_sync
exit /b %errorlevel%
```

**MTS project provides** `scripts/import_from_ib.bat`:
```batch
@echo off
REM Project entry: runs mts record --import-from-ib --date <today>
cd /d <mts-dir>
call venv\Scripts\activate.bat
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set "dt=%%a"
set "TODAY=%dt:~0,4%-%dt:~4,2%-%dt:~6,2%"
mts record --import-from-ib --date %TODAY%
exit /b %errorlevel%
```

### 9.3b Exit-code Contract (run_ib_sync.bat → wrapper / MTS S3 failure classification)

`run_ib_sync.bat` passes through the exit code of `python -m src.ib_sync` (`%ERRORLEVEL%`). Maps to the MTS P5 three-class taxonomy (SPEC_Paper_P2_Monitoring §3.0):

| Exit code | Class | Meaning | wrapper / MTS response |
|---|---|---|---|
| **0** | OK / idle | Success / no new data / gate skipped / state corruption safe-exit | Continue (S4 import) or stay silent |
| **2** | RETRYABLE | Transient: rate limit 1018/429 / server-busy exhausted / network | **Do not fix, wait for the next scheduled trigger to retry automatically** (ib_sync already backs off; do not loop-retry) |
| **3** | HARD | token/auth expired / unexpected error | **Halt + alert user** (retrying is useless; needs token renewal or troubleshooting) |

> Implementation: see `src/constants.py` `RC_OK/RC_RETRYABLE/RC_HARD`. `1` is intentionally left unused (to avoid legacy `exit 1` being misclassified). ib_sync itself handles rate limit/backoff idempotently; the wrapper **should only route by exit code, not add its own retry loop** (ADR-002 ban risk).

### 9.4 User-layer wrapper.bat Template (put at user's own location)

```batch
@echo off
REM ====================================================================
REM daily_run.bat — user-layer wrapper, dual-mode
REM Usage:
REM   Manual: double-click daily_run.bat (interactive, pauses to wait for GSheet tagging)
REM   Auto: Task Scheduler runs "daily_run.bat --auto" (background, async GSheet)
REM Location: user-defined (desktop / D:/Scripts/ / etc.)
REM ====================================================================

set AUTO_MODE=0
if "%~1"=="--auto" set AUTO_MODE=1

set LOG=<tradelens-dir>\logs\wrapper.log
echo [%date% %time%] start (auto=%AUTO_MODE%) >> %LOG%

REM ===== Step 1: IB Sync (run project entry bat) =====
call <tradelens-dir>\scripts\run_ib_sync.bat
if errorlevel 1 (
    echo [%date% %time%] ib_sync FAIL exit=%errorlevel% >> %LOG%
    if %AUTO_MODE%==0 (
        echo [FAIL] ib_sync. Check <tradelens-dir>\logs\
        pause
    )
    exit /b 1
)

REM ===== Step 2: User tags GSheet (interactive mode only) =====
if %AUTO_MODE%==0 (
    echo.
    echo [Step 2/3] Label new trades in Google Sheet (L column dropdown).
    set /p READY="Press [Enter] when done..."
)
REM ★ Auto mode skips this step — async: after user tags GSheet, the next trigger imports automatically

REM ===== Step 3: MTS Import (run project entry bat) =====
call <mts-dir>\scripts\import_from_ib.bat
if errorlevel 1 (
    echo [%date% %time%] mts import FAIL exit=%errorlevel% >> %LOG%
    if %AUTO_MODE%==0 (
        echo [FAIL] MTS import
        pause
    )
    exit /b 1
)

echo [%date% %time%] OK >> %LOG%
if %AUTO_MODE%==0 (echo [OK] All done! & pause)
exit /b 0
```

### 9.5 User's Actual Usage Patterns

| Stage | Usage | Trigger |
|---|---|---|
| **paper W1–W4 (validation period)** | User double-clicks `daily_run.bat` daily (interactive) | User |
| **paper W5+ (stable period)** | Task Scheduler runs `daily_run.bat --auto` (on logon + Daily 13/19 ET) | OS auto |
| **Any time debugging** | User double-clicks `daily_run.bat` any time (interactive mode to inspect the flow) | User |

### 9.6 Async "GSheet Tagging" Flow in Auto Mode

```
Day 1 19:00 Task Scheduler triggers daily_run.bat --auto
  ├─ Step 1: ib_sync → fetches Day 1 trades → GSheet displays them (category empty)
  └─ Step 3: mts record --import-from-ib --date Day1 → 0 rows imported (Day 1 not tagged yet)

Day 2 morning: user opens GSheet and tags Day 1 trades (~15 sec)

Day 2 13:00 Task Scheduler triggers daily_run.bat --auto
  ├─ Step 1: ib_sync → syncs Day 1's tagged category back to SQLite + auto-exports csv
  └─ Step 3: mts record --import-from-ib --date Day2 → 0 rows (Day 2 not tagged)
             (but csv contains tagged Day 1; need to adjust import to support --date Day1)

→ wrapper should actually import "all dates whose csv contains tagged trades", not only --date today.
   **v1.1 (2026-05-26 state machine)**: wrapper loops **last 90 days** (paired with IB_Sync `--lookback 90`, per §5.6 C9). Single-day import is idempotent (REPLACE by date,signal_id); re-importing the same day is harmless.
```

**v1.0 simplification** (old, superseded by v1.1 state machine): wrapper Step 3 imports only today's date — in auto mode the user sees Day 1 trades land in D5 on D+1.
**v1.1 (currently implemented, 2026-05-26): wrapper Step 3 loops the last 90 days** — paired with IB_Sync re-export's lookback window, so any user revisions made via review.bat for any of the last N days propagate to D5 on the next wrapper run.

### 9.7 Task Scheduler Configuration (one-time setup, paper W5+)

```
Open Task Scheduler (taskschd.msc) → create task:
  Name: IB Trade Sync Daily
  Trigger:
    - At log on (when user logs in)
    - Daily 13:00 ET
    - Daily 19:00 ET
  Action: Start a program
    Program: <user path>\daily_run.bat
    Arguments: --auto
  Settings:
    ☑ Allow task to be run on demand
    ☑ Run task ASAP after a scheduled start is missed (catch up after laptop wakes from sleep)
    ☐ Start task only if computer is on AC power (off, also run on battery)
    ☑ Wake computer to run this task (optional)
    ☑ Run only when user is logged on (important: preserves GSheet auth + venv PATH)
```

### 9.8 Debug Paths

| Issue | How to investigate |
|---|---|
| No new D5 row | **Double-click daily_run.bat to run interactive mode** → terminal window shows which step failed |
| Want to know why a day failed | `<tradelens-dir>/logs/wrapper.log` (OK/FAIL history) |
| Task Scheduler did not fire | Windows Task Scheduler → task history (built-in) |
| GSheet has no new data | GSheet A1 banner (status written by ib_sync) |
| ib_sync ran but csv was not generated | ib_sync logs/ib_sync_{date}.log (project-internal log) |
| mts record ran but D5 not written | mts terminal output (visible in interactive mode) / mts log |

### 9.9 wrapper-independent Boundary Guarantees (still zero coupling)
- The IB_Trade_Sync project does not know the wrapper exists (`run_ib_sync.bat` is a project-internal entry; the wrapper calling it is no different from the user calling it)
- The MTS project does not know the wrapper exists (`import_from_ib.bat` same)
- Schema/command changes on either side: user updates the wrapper; both projects are unaffected
- wrapper failure → IB_Trade_Sync / MTS project data states do not become dirty (each side is idempotent)

---

## 10. References / Context

- **MTS SPEC v13 D5 complete fields**: MTS project SPEC_Paper_P2_Monitoring.md §9.4
- **MTS V6 error handling (eventual consistency)**: same as above §4.9 FR-RES-1 ~ FR-RES-10
- **MTS Launch Checklist (paper W5+ cutover timing)**: same path, LAUNCH_CHECKLIST_paper_Stage_2.md §3.1
- **This project's requirements**: [REQUIREMENTS.md](REQUIREMENTS.md)
- **This project's overview**: [README.md](../../README.md)

---

*v1.0 frozen — 2026-05-20 (rev4, single csv interface + user-level wrapper.bat scheduler).*
*Future csv schema changes require both-project sync review (per §5.3) and a version bump. wrapper.bat is a user-layer script and falls outside the contract scope.*
