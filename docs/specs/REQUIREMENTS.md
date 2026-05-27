# REQUIREMENTS — TraderLens (v1: IBKR adapter)

> **Status**: 📋 v1.1 (2026-05-26, rev8 — FR-EXPORT-3 upgraded from scheme E (full dump) to a state machine (per-trade_date two-state A/B): on the IB_Sync side, the `setup_tag` field in `annotations.csv` helps MTS scope the quantitative range; the `category` column becomes binary; re-export uses dual triggers + 90-day lookback. csv schema remains v1.0 frozen. Earlier: rev7 FR-PIVOT phase-1 locked: 5 views + decoupled CSV annotation layer (setup_tag/score/notes, key = opening tradeID) + per-system scoring; dropped MFE/MAE/R-multiple/roll. Earlier: rev6 scope reduction + priority inversion: P1 MTS csv export + P2 local HTML pivot; GSheet deferred to v2; full SQLite archive includes stocks)
> **Audience**: developers / Claude bootstrapping this project
> **Related docs**: [README.md](../../README.md) (project overview) + [INTERFACE_CONTRACT.md](INTERFACE_CONTRACT.md) (interface contract with the MTS project 🎯) + [DATA_ARCHITECTURE.md](DATA_ARCHITECTURE.md) (three-layer data architecture, English canonical) + [Spike 001 README](../studies/001_flex_connectivity_spike_20260520/README.md) (basis for v1.1 changes)

---

## 1. Background and Positioning

### 1.1 Origin
The [MTS DevTest project](https://github.com/arthurpcj/MTS-backtest) is a systematic futures backtesting platform, about to start paper Stage 2 (2026-05-23). In the original design, D5 `actual_log.csv` was filled in by the user manually typing (`mts record`). Pain points:
- Daily EOD manual entry creates friction for the user; long-term, it's easy to slack off
- The user also has discretionary trades outside MTS strategies (MNQ/MES), and wants them aggregated and pivoted uniformly

**Output of this tool**: have IBKR push data automatically → user labels A (discretionary) / B (MTS) → only category B is exported as csv to MTS; category A is pivoted locally. This achieves a "zero manual entry" main path.

### 1.2 Relationship with the MTS project (core: zero coupling)
- **Independent project** (not inside the MTS git repo)
- **Only cross-project interface**: one csv file (12 columns, [INTERFACE_CONTRACT.md §2](INTERFACE_CONTRACT.md))
- **Zero cross-project read/write**: ib_sync does not read any MTS file / does not call MTS APIs / does not know the signal_id concept / does not invoke MTS commands
- **The MTS-side reverse read path is configured on the MTS side** (yaml `ib_sync_export_dir` points to ib_sync `data/exports/`)
- **Cross-project scheduling** is wired by a **user-level wrapper.bat** (INTERFACE_CONTRACT §9); both projects provide `scripts/*.bat` entry points for the wrapper to call
- **Activation timing**: paper W1-W4 still uses `mts record` manual entry (to verify the accuracy of this tool); from paper W5+, wrapper.bat runs in dual mode (manual double-click / Task Scheduler `--auto`)

## 2. Scope

### 2.1 In scope (v1.1 rev6, with priority labels)

**Priority 1 — MTS csv export (delivered first)**:
- Fetch **all IBKR fills** (futures + stocks) into SQLite as a complete archive
- Export `mts_trades_{date}.csv` for MTS import: only NQ/MNQ/ES/MES futures (12 columns v1.0, [INTERFACE_CONTRACT.md §2.3](INTERFACE_CONTRACT.md)). Scheme E: full dump, MTS-side smart matcher filters.
- "Check on boot" scheduling (state.json) + rate limit policy (10 min, [ADR-002](../decisions/002-flex-rate-limit-policy.md))
- Token expiration / network down / report not generated — all failures have a safety net

**Priority 2 — Local interactive HTML pivot**:
- Generate HTML pivot reports from full SQLite data (FR-PIVOT)
- Pivot by trade type separately: stock investing / discretionary intraday / discretionary swing / MTS strategy

### 2.2 Out of scope
- ❌ Real-time intra-session monitoring (TWS API; left to the MTS project in the future)
- ❌ Automated order entry / risk halt / broker integration
- ❌ **GSheet annotation layer (FR-CLASSIFY/FR-SYNC) — deferred to v2, design retained**
- ❌ Options / FX fills (stocks are **included in the archive + pivot** starting v1.1, but not exported to MTS)
- ❌ MTS-internal derived fields (signal_id / setup_id / R-multiple / slippage / mode derivation) — computed at MTS import time
- ❌ **signal_id labeling** (an MTS-internal concept, computed by the MTS-side smart matcher; ib_sync never knows about it)
- ❌ Multi-account / multiple IB accounts (v1 is single account)
- ❌ Web UI / server (the HTML pivot is a self-contained single file, not a web app)

## 3. User Stories

### Story 1: Daily auto-fetch
> As a paper Stage 2 user, I want my laptop to automatically fetch yesterday's IB fills after boot, without manually running a command every day.

→ Windows Task Scheduler "At user logon" + multiple daily triggers; ib_sync self-checks state.json to decide whether to fetch.

### Story 2: Backfill after taking the laptop away
> As a user on the road, if I take my laptop away for 5 days and come back online, I want the tool to automatically backfill those 5 days.

→ state.json `last_run` + compute the backfill range (Flex Query configured Last 30 Days, tradeID dedup as fallback).

### Story 3: A/B classification and pivot
> I manually opened a few discretionary MNQ trades (unrelated to MTS strategies); I want them to stay in GSheet for my own pivoting, not mixed into the MTS database.

→ One dropdown per trade in GSheet: user picks `MTS` (category B) / `MANUAL` (category A). Only MTS is exported.

### Story 4: Fallback when fetch fails
> Occasionally the IB Flex report isn't generated for the day / the token expires; I don't want to lose data, nor block MTS's EOD.

→ state.json is not updated → retry next time. On the MTS side, when the user runs `mts eod` that day with 0 rows in actual_log → automatically write an `actual_status=MISSED` placeholder + WARN (V6 FR-RES-3). After ib_sync backfills → user runs `mts record --import-from-ib --backfill` to REPLACE.

### Story 5: Mislabel / change of mind
> I mislabeled an MTS trade as MANUAL; I want to be able to fix it afterwards without affecting data integrity.

→ Change category in GSheet → next export → MTS REPLACE (latest row_created_at wins).

## 4. Functional Requirements (FR)

### FR-FETCH: Flex Query retrieval
- **FR-FETCH-1**: Configure the Activity Flex Query range = **Last 30 Days**, section = Trades (level = Executions), disable the SymbolSummary section (ibflex parser compatibility; we have deprecated ibflex, but keep the XML simple)
- **FR-FETCH-2**: Flex Query fields must include (v1.1 revised, per [spike 001](../studies/001_flex_connectivity_spike_20260520/README.md)):
  - **Actually exist**: `tradeID` / `symbol` / `underlyingSymbol` / `tradeDate` / `dateTime` / `quantity` / `tradePrice` / `ibCommission` / `multiplier` / `fifoPnlRealized` / `buySell` / `expiry` / `openCloseIndicator` / `orderReference` / `orderType` / `conid` / `exchange`
  - ⚠ **`assetCategory` does not exist in Flex XML** (incorrectly listed in v1.0, removed in v1.1); filtering by `underlyingSymbol` whitelist is sufficient (FR-FETCH-4)
  - ⚠ **`tradeTime` is not a standalone field**; it is actually part of the composite `dateTime="YYYYMMDD;HHMMSS"`; the source side splits on `;` and takes the latter half → `HH:MM:SS`
  - ⚠ **`expiry` is in `YYYYMMDD` format** (contract last trading day), not the `YYYYMM` mistakenly noted in v1.0; SQLite stores YYYYMMDD, and csv export truncates with `[:6]` → YYYYMM (csv v1.0 contract is not broken)
- **FR-FETCH-3**: The two-step HTTP flow (SendRequest → poll → GetStatement) is implemented in-house using the `requests` library; XML parsing uses stdlib `xml.etree.ElementTree` (the `ibflex` library recommended in v1.0 is deprecated, because the 0.15-version parser has schema bugs on SymbolSummary/Orders and maintenance has stalled; see [ADR-001](../decisions/001-drop-ibflex.md))
- **FR-FETCH-4** (v1.1 rev6 revised): **Full-volume ingestion into SQLite, no filtering at fetch time**. Stocks (e.g., FMCC) + all futures (NQ/MNQ/ES/MES + micro contracts like M6B/MHG, etc.) are all stored in SQLite as a complete archive for local pivots (Priority 2). Filtering to NQ/MNQ/ES/MES moves to the **export stage** (FR-EXPORT-3, only these 4 underlyings go to MTS). Rationale: the user needs to pivot stocks/swing/intraday separately, and the data must be archived first.
- **FR-FETCH-5**: **Flex rate limit policy (hard, permanent-ban risk)** — see [ADR-002](../decisions/002-flex-rate-limit-policy.md):
  - **Minimum interval per Query ≥ 10 minutes** (`MIN_INTERVAL_SEC = 600`), not the 60s commonly circulated in the community
  - On **error code 1018** (CLIENT_THROTTLED) → set `state.throttled_until_ts = now + 30 min` → exit immediately, **never blindly retry** (continued violations can result in a permanent IP ban)
  - On **1009/1019** (SERVER_BUSY, report still being generated) → retry at 30 sec intervals up to 3 times (this is normal waiting between SendRequest → GetStatement, not rate limiting)
  - HTTP 429 → handled the same as 1018
  - Token invalid / other errors → do not update state.json `last_success_trade_date`, mark `last_error`, retry on the next trigger
- **FR-FETCH-6**: Token / Query ID read from `.env` (env var, not hardcoded, not committed). config.yaml only contains non-sensitive configuration.
- **FR-FETCH-8 (v1.1, timezone rule)**: All times are US/Eastern:
  - **The Flex Query must be configured in the Eastern time zone** — the Flex XML `dateTime`/`tradeTime` has no timezone suffix; the timezone is determined by the Query configuration; the code keeps the original values without conversion (no suffix means no reliable conversion), so "it is ET" is guaranteed by the Query configuration. ⚠ The user must select Eastern when configuring the Query; otherwise all trade times are shifted, and the MTS signal_id matcher won't match (MTS ORB is also in the ET market session).
  - **The "today/yesterday" backfill window logic uses ET dates** (`datetime.now(ET).date()`), **never the local calendar day** (the user is UTC+8; `date.today()` would mis-identify yesterday during the ET session → incomplete fetch, violating NFR-RELIABILITY-3).
  - Audit timestamps (`row_created_at`, etc.) use **UTC** (explicit +00:00, unambiguous).
  - Deployment verification (§8.3): after fetching the first trade, cross-check a fill time against TWS to confirm consistency with Flex (verifying the Query timezone is configured correctly).
- **FR-FETCH-7 (v1.1, field-change robustness)**: parsing is robust to Flex XML field changes:
  - **Field order changes** → immune (values fetched by attribute name, not position)
  - **Extra unknown fields** → ignored (only required ones are read)
  - **Missing optional fields** (multiplier / ibCommission / expiry / fifoPnlRealized / notes) → default NULL, non-blocking (per §6)
  - **Missing critical fields** (tradeID / tradeDate / dateTime / underlyingSymbol / buySell / quantity / tradePrice / openCloseIndicator) or unparseable values → **skip that record + WARN log**, do not kill the whole batch (the next Last 30 Days fetch will re-pull; consistent with delayed eventual consistency)

### FR-STORE: SQLite persistence (20 internal columns)
- **FR-STORE-1**: Primary key `trade_id` (IB native), `INSERT OR IGNORE` for dedup
- **FR-STORE-2**: Table schema 20 columns (IB native 12 + derived 1 `asset_type` + user 2 + audit 3 + spike-002 follow-up 2):

```sql
CREATE TABLE trades (
    -- IB native (12)
    trade_id              TEXT PRIMARY KEY,
    trade_date            TEXT NOT NULL,         -- YYYY-MM-DD (converted from Flex tradeDate: 20260422 → 2026-04-22)
    trade_time            TEXT NOT NULL,         -- HH:MM:SS ET (from Flex dateTime '20260422;095605' split on ';', take latter half + format)
    underlying            TEXT NOT NULL,         -- futures: NQ/MNQ/ES/MES/M6B/MHG...; stocks: ticker (e.g., FMCC)
    asset_type            TEXT NOT NULL,         -- v1.1: 'FUT' (expiry non-empty) / 'STK' (expiry empty). Derived, used for pivot slicing
    expiry                TEXT,                  -- v1.1: nullable. Futures YYYYMMDD (contract last trading day); stocks NULL. csv export truncates [:6] → YYYYMM to preserve csv v1.0 contract
    buy_sell              TEXT NOT NULL,         -- BUY/SELL
    quantity              INTEGER NOT NULL,
    trade_price           REAL NOT NULL,
    multiplier            INTEGER,               -- MNQ=2/NQ=20/MES=5/ES=50 (used for internal pivots); v1.1 nullable (IB occasionally omits the field)
    ib_commission         REAL,                  -- v1.1 nullable (IB omits → NULL; MTS side treats NULL as 0, see §6)
    open_close            TEXT NOT NULL,         -- O/C
    fifo_pnl_realized     REAL,                  -- opening leg NULL/0, closing leg has value (used for internal pivots)
    -- User-labeled (2) — driven by the local annotation layer (see DATA_ARCHITECTURE)
    category              TEXT,                  -- PAPER_AUTO / MTS_CONFIRMED (state-machine output, v1.1 §5.6)
    notes                 TEXT,                  -- free-form user input
    -- Audit (3)
    category_set_at       TEXT,                  -- ISO 8601 UTC, moment of user labeling
    row_created_at        TEXT NOT NULL,         -- ISO 8601 UTC, moment ib_sync fetched
    source_run_id         TEXT NOT NULL,         -- ib_sync run ID (UUID, troubleshooting trace)
    -- Spike-002 follow-up (2) — added by additive migration
    data_source           TEXT NOT NULL DEFAULT 'ACTIVITY',  -- 'ACTIVITY' (T+1 Flex) / 'CONFIRMATION' (same-day TCF)
    order_ref             TEXT                   -- IB `orderReference` strategy tag (open leg only; close leg empty)
);
```

- **FR-STORE-3**: Retained permanently (5-year paper → live_100 entire span, same table)
- **FR-STORE-4**: `multiplier` and `fifo_pnl_realized` are retained internally (convenient for user GSheet pivots), but **do not enter the export csv** (the MTS side computes them itself, keeping the interface minimal)

### FR-CLASSIFY: User classification (GSheet operations) — ⏸ **v2 BACKLOG (deferred, design retained)**
> v1 does not implement the GSheet annotation layer. csv export uses scheme E (full dump to MTS, MTS-side smart matcher filters). This section's design is retained for v2 if GSheet is revived.
- **FR-CLASSIFY-1** (v2): GSheet `category` column dropdown: `MTS` / `MANUAL` (blank = pending review)
- **FR-CLASSIFY-2** (v2): GSheet `notes` column free-form (optional)
- **FR-CLASSIFY-3** (v2): User labels → on the next ib_sync run, gspread READ → SQLite UPDATE (`category` + `notes` + `category_set_at`)
- **FR-CLASSIFY-4** (v2): Only `category=MTS` trades are exported to MTS; `MANUAL` + blank remain internal to ib_sync
- **FR-CLASSIFY-5**: ⚠ **No signal_id column** — signal_id is an MTS-internal concept, computed by the MTS-side smart matcher (per [INTERFACE_CONTRACT.md §4.2](INTERFACE_CONTRACT.md)) — **holds for both v1 and v2**

### FR-SYNC: Google Sheet sync — ⏸ **v2 BACKLOG (deferred, design retained)**
> v1 does not implement GSheet sync. Local pivots are replaced by HTML reports (FR-PIVOT). This section's design is retained for v2.
- **FR-SYNC-1** (v2): Use the `gspread` library; service account authentication (config reads json key path)
- **FR-SYNC-2** (v2): Each ib_sync run flow: SQLite → GSheet append new trades + sync user edits (category/notes) back to SQLite
- **FR-SYNC-3** (v2): Dedup in GSheet by `trade_id` (read existing trade_id set, only append new ones)
- **FR-SYNC-4** (v2): Fixed GSheet column order (human-friendly, per [INTERFACE_CONTRACT.md §6.1](INTERFACE_CONTRACT.md))
- **FR-SYNC-5** (v2): Top banner cell (A1) of GSheet is written by ib_sync as an alert (`[OK] last_run` / `[WARN] gap=N` / `[FAIL] reason`)

### FR-PIVOT: Local interactive HTML pivot (v1 Priority 2) — phase 1 specification (locked 2026-05-22)

> **Design principles**: small but accurate, in service of trading, no feature piling. Every view/metric must be able to change "the next trade's decision". Five views form a closed loop — calendar → drill-down → slice → equity curve → score by system; anything heavier than that is **deliberately cut** (see FR-PIVOT-9).
> User profile anchor: **micro-futures intraday scalper** (MES/MNQ, mostly 1-lot intraday open/close) → highest signal dimensions = hour-of-day / day-of-week / hold-time.

**Fact layer (immutable, already built)**
- **FR-PIVOT-1**: Read all trades from SQLite (including stocks / non-target futures), generate a **self-contained single HTML file** (no server, offline, portable / can be dropped into Drive). Tech: inline PivotTable.js (drag-and-drop slicing) + Python-generated SVG charts. **Not rewriting as Streamlit** (phase 1; PivotTable.js already covers interactive slicing, static HTML is zero-friction).
- **FR-PIVOT-2**: leg→round-trip FIFO pairing (already built in `roundtrip.py`, by underlying+expiry, cross-day). Derived fields: `direction` / `pnl_usd` / `pnl_pts` / `hold_minutes` / `is_win` / `is_intraday` / `trade_class` (Stock/Futures-Intraday/Futures-Swing) / `week` / `month` / `session` (RTH/ETH, determined by ET time) / `entry_hour` / `entry_dow` / `hold_bucket` (<15m/15-60m/1-4h/>4h) / **`open_trade_id`** (opening leg's IB tradeID, the join key for the annotation layer).
- **FR-PIVOT-2b**: The fact-layer `trades` table **adds an `order_ref` column** (sourced from Flex `orderReference`, present on both AF/TCF; identifies the source of quant auto-tagging + provides traceability). Small migration (same as `data_source`).

**Annotation layer (subjective, editable, decoupled from the fact layer — industry standard practice)**
- **FR-PIVOT-3**: Annotation layer = `data/annotations.csv` (gitignored, contains real trades), key = **opening leg's IB `trade_id`** (immutable, stable across re-fetches / re-pairing). A round-trip inherits its opening leg's annotation; a single open split into multiple closes → multiple round-trips share the same annotation.
- **FR-PIVOT-3b** (annotation columns):
  - `setup_tag` — trade system (free string). Config reserves **about 8 system slots** (code → display name mapping, names filled in by the user later; quant orders with the same orderRef share one code).
  - `score` — **10-point** quality score (semantics: ≥~6-7 is worth trading; can diagnose in reverse "do high-score trades really perform better").
  - `notes` — free-form post-trade review (optional; the user may not always fill).
- **FR-PIVOT-3c** (three-tier `setup_tag` priority): ① explicit annotation in `annotations.csv` (post-hoc fill for discretionary trades) > ② opening-leg `order_ref` via config alias table (quant orders, Backtrader auto) > ③ `untagged` (explicitly visible).
- **FR-PIVOT-3d** (re-tagging flow, fitted to static HTML): the `--tag-template` command pre-generates/updates `annotations.csv` — lists all round-trips, **preserves existing annotations and appends untagged ones**, with read-only reference columns (opening date/time/symbol/direction/net P&L) for easy identification. The user fills in `setup_tag`/`score`/`notes` in Excel → re-run the pivot → the report slices by system. **Click-in-UI tagging (which requires write-back) is not done** — that is the one thing that would force a move to Streamlit.
- **FR-PIVOT-3e**: The local CSV annotation layer **replaces** the deferred GSheet annotation (FR-CLASSIFY/FR-SYNC) for the local pivot scenario — simpler, no Google service account required.

**Views (phase 1, only these 5)**
- **FR-PIVOT-4**:
  1. **KPI headline**: Net/Gross P&L, Commissions, trade count, win rate, **Profit Factor**, **Expectancy**, **Max Drawdown** (amount + % + duration in days), max win/loss streak.
  2. **Calendar heatmap** (front page): each trading day colored by net P&L, with cell showing trade count/P&L/win rate; **click date → drill into that day's detail**. Top toggle filters by setup_tag / trade_class.
  3. **Equity curve**: cumulative net P&L (already built as SVG) + **max drawdown interval highlighted**.
  4. **Dimension slice** (PivotTable.js drag-and-drop): dimensions include `setup_tag` / `entry_dow` / `entry_hour` / `hold_bucket` / `underlying` / `direction` / `trade_class`.
  5. **Trade detail table**: sortable/filterable; shows round-trip key fields + setup_tag/score/notes.
- **FR-PIVOT-5** (per-system scoring — core user requirement): one row per `setup_tag` comparing **performance** (trade count / Net P&L / win rate / Profit Factor / Expectancy / Avg Win / Avg Loss) + **execution** (**average hold time for winning vs losing trades** = "can't hold winners / death-grip losers" diagnostic; intraday vs swing ratio). No minute bars required.

**Output + scope boundary**
- **FR-PIVOT-6**: Output `reports/pivot_latest.html`, gitignored (contains real trades).
- **FR-PIVOT-7** (UI palette — neutral / cross-cultural / non-emotive; **recorded for now, to be applied during HTML visual design**): **Avoid red/green binary** — red-up-green-down (some Asian regions: red = up/profit) and green-up-red-down (US) have opposite semantics, and red/green easily triggers trader emotions. Use **a set of neutral contrasting colors**: one neutral color for "up/profit" and a contrasting neutral color for "down/loss", applied uniformly to the equity curve / PnL / calendar heatmap coloring / some text and background colors. **Do not rely on color alone** — pair with `+/−`, `▲/▼`, or win/loss text (colorblind-friendly + cross-culturally unambiguous). Exact color values to be decided at the HTML design stage.
- **FR-PIVOT-9** (explicit **non-goals** — small but accurate): ❌ **MFE/MAE** (requires a minute-bar pipeline to reconstruct paths, breaks "zero MTS dependency"; user-judged too heavy — not done in phase 1/2) ❌ **R-multiple** (requires initial_stop, no such field in IB fills) ❌ **Continuous-contract roll merging** (mostly intraday, very few cross-quarter rolls; revisit when data actually appears) ❌ Monte Carlo / tilt psychology tags / multi-account comparison / AI summary ❌ Options multi-leg combos.

### FR-EXPORT: export csv to MTS (v1 Priority 1 — delivered first)
- **FR-EXPORT-1**: CLI `python -m src.exporter --date <YYYY-MM-DD>`
- **FR-EXPORT-2**: Output `data/exports/mts_trades_{date}.csv` (single day)
- **FR-EXPORT-3** (v1.1 rev8 revised, 2026-05-26 — state machine): export judges **State A/B independently per trade_date**:
  - **State A** (all NQ/MNQ/ES/MES round-trips in SQLite for that day have no IB_Sync setup_tag annotation): csv contains the **full day's** NQ/MNQ/ES/MES futures legs (same as the current scheme E)
  - **State B** (≥1 round-trip on that day is tagged ∈ `MTS_RELEVANT_SETUPS`, currently = `{Q_intraday}`, extensible): csv contains **only the open + close pair of legs for the matched round-trips**
  - Stocks + other micro contracts are **never** exported (filtered out in both states, kept in SQLite for local pivots)
  - See [INTERFACE_CONTRACT.md §5.6 v1.1 (2026-05-26) C6](INTERFACE_CONTRACT.md) + [DATA_ARCHITECTURE.md §5](DATA_ARCHITECTURE.md)
- **FR-EXPORT-3b** (v1.1 rev8 revised): csv `category` column (#11) takes **two values**:
  - **State A → `PAPER_AUTO`** (current behavior, MTS's existing DC review queue skips by default)
  - **State B → `MTS_CONFIRMED`** (IB_Sync has adjudicated; MTS 0-candidate path goes via FORCE_WRITTEN + alert)
  - See [INTERFACE_CONTRACT.md §5.6 v1.1 (2026-05-26) C7](INTERFACE_CONTRACT.md)
- **FR-EXPORT-3c** (v1.1 rev8 added, 2026-05-26 — re-export trigger protocol):
  - **Dual trigger**: ① ib_sync main flow auto-exports the day's csv (state machine takes effect automatically); ② `python -m src.pivot --review-flow` appends a re-export step at the end, scanning **last 90 trade_dates** by default (`--lookback N` / `--lookback all` to change)
  - On re-export, **every trade_date within the lookback window writes a csv**, including header-only csv (that day's State B fully filtered out / non-trading day)
  - See [INTERFACE_CONTRACT.md §5.6 v1.1 (2026-05-26) C8/C9/C10](INTERFACE_CONTRACT.md)
- **FR-EXPORT-4**: csv schema strictly follows [INTERFACE_CONTRACT.md §2 v1.0](INTERFACE_CONTRACT.md) (12 columns, UTF-8 LF RFC 4180). schema is **unchanged** (still v1.0).
- **FR-EXPORT-5**: At export time, print stats: "exported N rows (M trade pairs, NQ/MNQ/ES/MES only; K stocks + L other-futures skipped)"
- **FR-EXPORT-6**: v1.x backlog: range `--from --to` support for cross-day hold scenarios

### FR-STATE: state.json state management
- **FR-STATE-1**: Fields (v1.1 adds 2 throttle-related entries, per FR-FETCH-5):
  ```json
  {
    "last_run_at": "2026-05-23T22:35:00Z",
    "last_success_trade_date": "2026-05-22",
    "last_flex_call_ts": 1747728000,
    "throttled_until_ts": 0,
    "last_error": null,
    "last_error_at": null
  }
  ```
  - `last_flex_call_ts`: epoch (Unix timestamp) of the last actual Flex call. Used to enforce MIN_INTERVAL_SEC (10 min). Only updated on a successful call.
  - `throttled_until_ts`: epoch when the penalty box expires. `0` = not throttled. Set to `now + 1800` after hitting 1018.
- **FR-STATE-2**: **Only update last_success_trade_date after successfully writing to SQLite + GSheet** (on failure, only update last_error)
- **FR-STATE-3**: On startup, read → compute backfill range `from = last_success_trade_date + 1`, `to = yesterday` (per NFR-RELIABILITY-3)
- **FR-STATE-4**: Empty range → quick exit + log "no new trade days, skip"

### FR-ENTRY: project entry bat
- **FR-ENTRY-1**: Provide `scripts/run_ib_sync.bat` as the project entry point (activate venv + `python -m src.ib_sync` + log redirection)
- **FR-ENTRY-2**: The `.bat` includes a 30 sec WiFi delay (wait for the network to be ready after boot)
- **FR-ENTRY-3**: The entry bat only runs this project's main flow, **does not invoke other projects** (zero cross-project coupling; cross-project scheduling is handled by the user-level wrapper.bat, see INTERFACE_CONTRACT §9)
- **FR-ENTRY-4**: The entry bat exit codes are strict (0=OK, 1=fail); the wrapper side short-circuits based on this

### FR-SCHEDULE: scheduling (informational — decided by the user layer; this project does not hook Task Scheduler directly)
- **FR-SCHEDULE-1**: This project's entry `scripts/run_ib_sync.bat` can be hooked into Task Scheduler standalone (runs the ib_sync main flow, does not chain MTS)
- **FR-SCHEDULE-2**: Recommended user-layer wrapper.bat (INTERFACE_CONTRACT §9.4 template) hooked into Task Scheduler, chaining the ib_sync + MTS entry points
- **FR-SCHEDULE-3**: Recommended Task Scheduler triggers: "At log on" + Daily 13:00 ET + Daily 19:00 ET (per INTERFACE_CONTRACT §9.7)
- **FR-SCHEDULE-4**: Precise timing is not required (state.json is the safety net; running multiple times is harmless)

## 5. Non-Functional Requirements (NFR)

### NFR-PERF
- A single ib_sync run (Flex fetch + SQLite + GSheet sync) ≤ 60 sec (paper-period data is small)
- Backfilling 30 days of data ≤ 5 min

### NFR-RELIABILITY
- **NFR-RELIABILITY-1 Idempotent**: running N times a day → consistent results (trade_id primary key + state.json double safeguard + throttle gate)
- **NFR-RELIABILITY-2 Retry (per scenario, see FR-FETCH-5)**: transient network failure / 1009 SERVER_BUSY → 30s × 3 retries; HTTP 429 / 1018 CLIENT_THROTTLED → **no retry**, exit immediately + 30 min penalty box; token invalid → no retry, red flag
- **NFR-RELIABILITY-3 Only fetch yesterday**: `to = yesterday` never fetches today (the IB Flex report has settlement delay; fetching today may yield incomplete data). v1 does not implement "grace period for today" logic.
- **NFR-RELIABILITY-4 Gap alert**: `today - last_success_trade_date > 7 trading days` → red-flag log + GSheet A1 banner red

### NFR-USABILITY
- User GSheet annotation: **1 dropdown column** (category) + optional notes — minimal
- Plain-text error logs (the user is not required to read stack traces)
- **Run log observability (v1.1)**: print steps in real time during the run (run start / downloading / fetch stats / export stats) + WARN immediately on field anomalies (which record skipped / why); **print a RUN SUMMARY at the end of the run**: result (OK/FAIL) + elapsed + warnings/errors counts + enumerate all warnings/errors. Every exit path (including exceptions) prints the summary.

### NFR-IDEMP (cross-project contract)
- ib_sync rerun → export csv → MTS import N times → D5 has no duplicate rows (MTS side `(date, signal_id)` REPLACE)

### NFR-MAINTAIN
- Configuration-driven (Token / Query ID / GSheet ID all in config.yaml, zero hardcoding)
- **Zero MTS path dependency** (config contains no MTS project paths — fixes the rev1 leak)
- pytest coverage of core logic (state management / field mapping / dedup / GSheet sync / export)

## 6. Failure Handling (mapped to MTS V6 delayed eventual consistency)

| Scenario | This tool's behavior | MTS side behavior (managed by MTS) |
|---|---|---|
| **Total fetch failure** (Flex report not generated / token expired / network completely down) | state.json not updated → retry next time | D5 has 0 rows that day → `actual_status=MISSED` placeholder + WARN (V6 FR-RES-3) |
| **Partial fetch** (Flex Last 30 Days misses 1-2 days) | trade_id dedup + next pull of 30 days covers it | Same as above MISSED placeholder → subsequent import REPLACE |
| **Fetched but user hasn't labeled category** | trades remain in GSheet pending review (`category=NULL`); export does not include them | Same MISSED placeholder as above → after labeling + export + import, REPLACE |
| **User mislabel** (B → A) | csv does not include that record; user changes back in GSheet → re-export → MTS REPLACE | Consistent |
| **Token expires after 30 days** | Fetch fails → state.json `last_error` updated, `last_success_trade_date` not updated + red-flag log | Same MISSED safety net; user reads the log and manually renews the token |
| **Large gap beyond Flex Query range** (laptop off > 30 days) | Gap detected → red-flag log + GSheet A1 banner; user manually runs a large range to backfill | Same as above |
| **Missing IB field** (e.g., ib_commission NULL for some record) | The field is NULL in SQLite; write is not blocked | MTS NULL-safe arithmetic (commission NULL → 0) |
| **Instruments outside NQ/MNQ/ES/MES** (e.g., ZN / CL / options) | Filtered out (FR-FETCH-4), not stored in SQLite | n/a |

**Core safeguard**: every "fetch failed / fetch incomplete / labeled late" scenario → consistent MTS-side behavior (MISSED placeholder → REPLACE upon backfill). No additional error handling beyond V6 is needed.

## 7. Acceptance Criteria (AC)

### AC-NORMAL: normal path
- **AC-1** One laptop boot, ib_sync runs through: fetches yesterday's NQ/MNQ/ES/MES futures fills, SQLite + GSheet synced, state.json updated
- **AC-2** User labels MTS in GSheet → export csv 12 columns strictly match [INTERFACE_CONTRACT.md §2.3](INTERFACE_CONTRACT.md)
- **AC-3** MTS side `mts record --import-from-ib` → D5 row written correctly (`recorded_by=cli_ib_sync`, signal_id computed by MTS smart matcher)
- **AC-4** Category A trades (`category=MANUAL`) stay in GSheet, do not appear in the export csv

### AC-RESILIENCE: safety-net path
- **AC-5** Laptop taken away 5 days, back online → auto-backfill 5 days (Flex Query Last 30 Days covers)
- **AC-6** Boot 3 times in one day, 2nd/3rd time quick-exit (state.json prevents re-run)
- **AC-7** Token expired, fetch fails → state.json `last_success_trade_date` not updated, log red flag; after renewing the token, the next run succeeds
- **AC-8** Mislabeled category fixed in GSheet → next export overwrites csv → after MTS REPLACE, D5 reflects the latest
- **AC-9** ib_sync writes + user `mts record --backfill` manually writes the same signal_id → MTS REPLACE (latest wins)
- **AC-10** Beyond Flex Query coverage (35 days off) → log detects the gap + GSheet A1 banner alerts

### AC-INTEGRATION (cross-project)
- **AC-11** csv schema changes → cross-project commit messages synced on both sides + version bump (see [INTERFACE_CONTRACT.md §5](INTERFACE_CONTRACT.md))
- **AC-12** MTS side yaml without `ib_sync_export_dir` configured → `mts record --import-from-ib` exits 1 with a clear error (does not assume a default path)

## 8. Implementation Conventions

### 8.1 Configuration (`config/config.yaml`)
```yaml
ibkr:
  flex_token: ${IBKR_FLEX_TOKEN}            # env var, do not commit
  flex_query_id: ${IBKR_FLEX_QUERY_ID}
  retry_count: 5
  retry_interval_sec: 30

storage:
  sqlite_path: ./data/trades.sqlite
  state_path: ./data/state.json
  export_dir: ./data/exports/

gsheet:
  spreadsheet_id: <google_sheet_id>
  worksheet_name: trades
  service_account_key: ./config/gsheet_key.json   # .gitignore'd

filter:
  underlying_symbols: [NQ, MNQ, ES, MES]
  asset_categories: [FUT]

alert:
  gap_threshold_days: 7        # last_success_trade_date lagging beyond this value → red flag
```

**Note**: contains no MTS paths / no hook configuration. Cross-project scheduling is handled by the user-level wrapper.bat (INTERFACE_CONTRACT §9); this project only provides an entry bat (FR-ENTRY).

**Note**: config **contains no MTS project paths** (fixes the rev1 leak). ib_sync runs entirely standalone; the MTS-side yaml `ib_sync_export_dir` reverse-configures the ib_sync export path.

### 8.2 Testing
- `tests/test_state.py` — state.json read/write + backfill range computation + rate-limit gate
- `tests/test_flex_client.py` — mock IB Flex XML download (Activity + Confirmation envelopes)
- `tests/test_parser.py` / `test_parser_confirmation.py` (+ `*_robustness.py`) — XML → TradeRow, both Activity and Confirmation profiles
- `tests/test_sqlite_store.py` — dedup / 20-column schema / additive migrations
- `tests/test_exporter.py` / `test_exporter_state_machine.py` — csv schema (12 cols, v1.0) + PAPER_AUTO ↔ MTS_CONFIRMED state machine
- `tests/test_annotations.py` (+ `*_robustness.py`) — local annotation layer (setup_tag / score / notes)
- `tests/test_pivot.py` / `test_pivot_review_flow.py` — local HTML pivot + one-shot review flow
- `tests/test_roundtrip.py` / `test_overlap.py` — round-trip pairing + windowed re-fetch idempotency
- `tests/test_timezone.py` — trade-day logic uses ET, never local calendar date
- `tests/test_integration.py` — end-to-end mock pipeline
- Entry point `scripts/run_ib_sync.bat` integration: manually run once to verify exit code + log output (one-off, not under pytest)

### 8.3 Deployment / startup (user actions)
1. `pip install -r requirements.txt` (after creating a venv, see `scripts/run_ib_sync.bat`)
2. Copy `.env.example` → `.env`, fill in `IBKR_FLEX_TOKEN` + `IBKR_FLEX_QUERY_ID`
3. Copy `config.example.yaml` → `config.yaml`, fill in gsheet_spreadsheet_id and other non-sensitive configuration
4. Download the Google service account JSON key and place at `config/gsheet_key.json`
5. Create the Windows Task Scheduler entry (per FR-SCHEDULE-1)
6. Manually run `python -m src.ib_sync` once to verify

## 9. References

- **This project's interface contract**: [INTERFACE_CONTRACT.md](INTERFACE_CONTRACT.md)
- **This project's ADRs**: [001-drop-ibflex.md](../decisions/001-drop-ibflex.md) + [002-flex-rate-limit-policy.md](../decisions/002-flex-rate-limit-policy.md)
- **Spike 001 verification**: [docs/studies/001_flex_connectivity_spike_20260520/README.md](../studies/001_flex_connectivity_spike_20260520/README.md)
- **IBKR Flex Web Service official documentation**: https://www.interactivebrokers.com/campus/ibkr-api-page/flex-web-service/
- **`gspread` library**: https://pypi.org/project/gspread/

---

*v1.1 — 2026-05-20 (rev6, scope reduction + priority inversion: P1 MTS csv export scheme E, P2 local HTML pivot, GSheet deferred to v2, full SQLite archive includes stocks).*
*v1.1 — 2026-05-20 (rev5, post-spike-001: drop ibflex, real Flex schema, 10-min rate-limit policy).*
*v1.0 history: rev4 frozen 2026-05-20 — single csv interface + user-level wrapper.bat scheduler.*
*Any FR / NFR / AC change requires review (internal to this project) + a check for impact on [INTERFACE_CONTRACT.md](INTERFACE_CONTRACT.md).*
