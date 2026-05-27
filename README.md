# TraderLens — Multi-Broker Trade Sync & Analytics

> **Status (v1)**: Live on a daily Windows scheduled task —
> - ✅ IBKR Activity Flex → SQLite full archive → 12-column CSV export (T+1, settled)
> - ✅ Local interactive HTML pivot (round-trip pairing, KPI / equity / calendar / by-setup / detail, all filter-linked) — self-contained single file, no server
> - ✅ Local CSV annotation layer (setup_tag / score / notes, keyed by opening trade_id) driving the PAPER_AUTO ↔ MTS_CONFIRMED state machine for the downstream backtester
> **In progress**: same-day Trade Confirmation capture (TCF spike #002).
> **Deferred to v2**: Google Sheet labeling layer (superseded by the local CSV annotation layer above).
> **Naming**: *TraderLens* is the broker-agnostic umbrella. v1 ships the IBKR adapter only (`src/ib_sync`); future siblings could be `coinbase_sync`, `td_sync`, etc.
> **License**: [AGPL-3.0](LICENSE) — see [ADR-003](docs/decisions/003-license-agpl-3.0.md) for rationale.

![TraderLens HTML pivot — overview](assets/screenshots/01-overview.png)

*Screenshot generated from the bundled [demo data](demo/) (50 anonymised paper trades with MES position size rescaled to make the equity curve more illustrative).*

---

## 1. What it does

Fetch your IBKR trades via the **Flex Web Service**, archive **everything** to SQLite, and:
- export the target futures (NQ/MNQ/ES/MES) as a **12-column CSV** for a downstream backtester (the [MTS DevTest](https://github.com/arthurpcj/MTS-backtest) project), and
- generate a **self-contained HTML pivot** for local review (round-trip pairing, derived metrics, calendar/by-setup/detail breakdowns).

Two consumers, one pipeline:
1. **MTS feed** — `data/exports/mts_trades_{date}.csv` (target futures only) for the MTS project's actual-trade log.
2. **Local analytics** — the full SQLite archive (incl. stocks & non-target futures) rendered into a single HTML file for personal review, no server required.

---

## 2. Architecture (current v1)

```
Windows Task Scheduler  (logon + daily 21:00 local = NY 08:00/09:00)
        │
        ▼
 scripts/run_ib_sync.bat ──▶ python -m src.ib_sync
        │
        ▼
 IBKR Flex (Activity, Last 30 days)
        │  two-step: SendRequest → GetStatement   (requests; ibflex dropped)
        ▼
 parse (stdlib xml.etree)
        │
        ▼
 SQLite  data/trades.sqlite   ← FULL archive (stocks + all futures), idempotent
        │
        ▼
 export  data/exports/mts_trades_{date}.csv   ← NQ/MNQ/ES/MES only, 12 cols v1.0
        │  (one-way file interface; ib_sync never reads MTS, never knows signal_id)
        ▼
 MTS DevTest project reads the CSV
```

- **Zero cross-project coupling**: ib_sync does not read any MTS file, call any MTS API, or know the `signal_id` concept.
- **Single interface**: the 12-column CSV (`INTERFACE_CONTRACT.md`, v1.0 frozen). Filtering (4 target underlyings) happens at **export**, not fetch — so SQLite stays a complete archive for local analysis.

---

## 3. Pipeline details

- **Fetch window**: up to **yesterday (US/Eastern)** — settled, stable data (T+1). Trade-day logic uses ET, never the local calendar date.
- **Idempotent**: `trade_id` primary key + `INSERT OR IGNORE`; running N times/day or re-fetching the rolling 30 days converges.
- **State** (`data/state.json`): backfill window (`last_success_trade_date`), rate-limit gate (`last_flex_call_ts`, `throttled_until_ts`), last error.
- **Exit codes** (consumed by the scheduler / a future MTS wrapper): `0` OK/idle · `2` RETRYABLE (throttle/network) · `3` HARD (auth/token expired).

### ⚠ Flex rate limiting (ADR-002 — permanent-ban risk)
A built-in gate enforces a **≥10-minute interval** between Flex calls; a `1018` throttle triggers a **30-minute penalty box**; failures **never blind-retry** (they wait for the next scheduled trigger). Treat Flex as a once-daily batch, not a pollable API.

---

## 4. Tech stack

| Layer | Choice | Note |
|---|---|---|
| HTTP | [`requests`](https://pypi.org/project/requests/) | self-implemented two-step Flex flow (~30 lines); ibflex dropped ([ADR-001](docs/decisions/001-drop-ibflex.md)) |
| XML parse | stdlib `xml.etree.ElementTree` | parses `<Trade>` (Activity) / `<TradeConfirm>` (Confirmation) |
| Storage | SQLite (`trade_id` PK, `INSERT OR IGNORE`) | 18-col full archive |
| State | `state.json` | gate + backfill window; updated only on success |
| Schedule | Windows Task Scheduler (logon + 1×/day) | the Python gate de-dups extra triggers ([ADR-002](docs/decisions/002-flex-rate-limit-policy.md)) |
| Tests | `pytest` | 171 passing |

---

## 5. Quick start

### Try it without an IBKR account (demo data)

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt

mkdir data 2>$null
copy demo\trades.sqlite      data\trades.sqlite
copy demo\annotations.csv    data\annotations.csv
venv\Scripts\python.exe -m src.exporter --date 2026-05-19
venv\Scripts\python.exe -m src.pivot
start reports\pivot.html
```

See [demo/README.md](demo/README.md) for what's in the bundle (50 anonymised trades from a paper account, with MES position size rescaled ×8 to make the equity curve more illustrative).

### Real run (live IBKR data)

```powershell
# 1. Configure credentials (never committed)
#    .env:  IBKR_FLEX_TOKEN, IBKR_FLEX_QUERY_ID  (see .env.example)

# 2. Run once (manual)
venv\Scripts\python.exe -m src.ib_sync

# 3. Enable automatic daily runs (Windows scheduled task)
powershell -ExecutionPolicy Bypass -File scripts\register_ib_sync_task.ps1

# 4. Re-export one day's CSV (reads SQLite only, no Flex call)
venv\Scripts\python.exe -m src.exporter --date 2026-05-19
```

Full operations guide: **[docs/guides/OPERATIONS.md](docs/guides/OPERATIONS.md)** (commands, logs, exit codes, troubleshooting, register/unregister).

---

## Screenshots

The HTML pivot exposes five filter-linked views, all rendered from the SQLite + annotations archive into one self-contained file.

<details>
<summary><strong>Click to expand — equity curve / calendar / by-setup / pivot / detail</strong></summary>

### Equity curve (with date ticks)

![equity curve](assets/screenshots/02-equity-curve.png)

### Calendar heatmap (6-col Mon-Fri+Sun)

![calendar heatmap](assets/screenshots/03-calendar.png)

### By-setup scoring

![by-setup scoring](assets/screenshots/04-by-setup.png)

### Pivot table (default = EntryHour × Result)

![pivot table](assets/screenshots/05-pivot.png)

### Filter-linked detail table

![detail table](assets/screenshots/06-detail.png)

</details>

> The interactive version: open [`demo/pivot.html`](demo/pivot.html) in any browser (no server, no installs).

---

## 6. Repository layout

```
traderlens/
├── README.md                       # this file
├── LICENSE                         # AGPL-3.0
├── .env.example                    # IBKR_FLEX_TOKEN / IBKR_FLEX_QUERY_ID template
├── .gitignore                      # secrets / runtime data / venv / private notes
├── docs/
│   ├── INDEX.md                    # documentation index
│   ├── specs/
│   │   ├── REQUIREMENTS.md         # FR / NFR / failure handling / acceptance
│   │   ├── INTERFACE_CONTRACT.md   # ★ cross-project CSV contract (12 cols)
│   │   ├── DATA_ARCHITECTURE.md    # 3-layer model: fact / annotation / derived
│   │   └── SPEC_Code_Review.md     # internal code-review process
│   ├── guides/OPERATIONS.md        # user operations manual
│   ├── decisions/                  # ADRs (001 drop-ibflex, 002 rate-limit, 003 license)
│   └── studies/                    # spikes / technical investigations
├── src/
│   ├── ib_sync.py                  # orchestrator (Flex → SQLite → auto-export)
│   ├── flex_client.py              # Flex two-step HTTP flow
│   ├── parser.py                   # XML → typed TradeRow (Activity + Confirmation)
│   ├── sqlite_store.py             # 20-col SQLite archive + idempotent upsert + migrations
│   ├── exporter.py                 # 12-col CSV export + state machine (PAPER_AUTO ↔ MTS_CONFIRMED)
│   ├── state.py                    # state.json + rate-limit gate
│   ├── annotations.py              # local annotation layer (setup_tag / score / notes)
│   ├── roundtrip.py                # round-trip pairing for the local pivot
│   ├── pivot.py                    # self-contained HTML pivot generator
│   ├── constants.py / errors.py    # config constants / typed errors
├── assets/vendor/                  # pinned 3rd-party JS/CSS for the local pivot (jQuery, pivottable)
├── config/pivot_tags.json          # local pivot setup_tag presets
├── scripts/
│   ├── run_ib_sync.bat             # project entry (venv + python -m src.ib_sync)
│   ├── review.bat                  # one-shot review flow (annotate → re-export → re-pivot)
│   └── register_ib_sync_task.ps1   # register the scheduled task (self-elevating)
├── tests/                          # pytest (171 passing)
└── data/                           # gitignored — real trades (SQLite, CSV, state, logs)
```

---

## 7. Boundary with the MTS project (zero coupling, single interface)

| Dimension | Design |
|---|---|
| ib_sync → MTS dependency | **none** (no MTS files / APIs / `signal_id` / command names) |
| MTS → ib_sync dependency | reads one CSV (path set in MTS-side config) |
| Sole interface | `mts_trades_{date}.csv` — 12 cols, v1.0 frozen ([§2](docs/specs/INTERFACE_CONTRACT.md)) |
| Filtering | export-stage (NQ/MNQ/ES/MES); MTS matches rows to its benchmark log (scheme E) |
| `ib_commission` | IB-native **signed** value (cost negative); MTS adds it (not subtracts) — [§5.6 C5](docs/specs/INTERFACE_CONTRACT.md) |
| Contract changes | dual-project review + version bump ([§5](docs/specs/INTERFACE_CONTRACT.md)) |

---

## 8. Roadmap

- ✅ **Local interactive HTML pivots** — shipped: round-trip pairing, KPI block, equity curve (with date ticks), calendar heatmap, by-setup scoring, full filter-linked detail table; single self-contained file.
- ✅ **Local CSV annotation layer** — shipped: `data/annotations.csv` (setup_tag / score / notes, keyed by opening trade_id) drives the PAPER_AUTO ↔ MTS_CONFIRMED state machine for csv export.
- 🔧 **Same-day capture** — Trade Confirmation Flex query (`<TradeConfirm>`) to fetch *today's* fills after close. Spike #002 verifies `tradeID` consistency vs the Activity archive, enabling a single-table merge (Activity remains the T+1 authoritative correction).
- 🔧 **Pivot Tier-2** — five enhancements pending design (CSV export of current filter, auto-regenerate after each scheduled sync, …).
- ⏳ **Cross-project scheduler** — user-level wrapper chaining ib_sync + MTS import (MTS paper W5+).
- ❌ **Google Sheet labeling** — deferred to v2 indefinitely (replaced by the local CSV annotation layer above).

---

## 9. Out of scope

- ❌ Real-time intraday monitoring / TWS API
- ❌ Automatic order placement / risk halts
- ❌ Options / stocks / FX **export** (futures NQ/MNQ/ES/MES only for MTS; stocks are archived for local analysis but not exported)
- ❌ MTS-internal concepts: `signal_id`, R-multiple, slippage, mode inference (computed on the MTS side)
- ❌ Reading any MTS file (zero cross-project read coupling)

---

## License

[AGPL-3.0](LICENSE). The author retains full copyright as the sole contributor; future dual-licensing remains an option pending a CLA. Network-use reciprocity (per AGPL §13) means a SaaS rehost would need to open-source its full stack. See [ADR-003](docs/decisions/003-license-agpl-3.0.md) for the full rationale.

**Third-party components** vendored in `assets/vendor/` (jQuery, jQuery UI, PivotTable.js) are MIT-licensed; full attribution in [assets/vendor/README.md](assets/vendor/README.md).

## See also

- [DISCLAIMER.md](DISCLAIMER.md) — not financial advice, use at your own risk
- [CONTRIBUTING.md](CONTRIBUTING.md) — how to file issues, branch/commit conventions, code review process
- [CHANGELOG.md](CHANGELOG.md) — release notes (per [Keep a Changelog](https://keepachangelog.com/en/1.1.0/))

*v1 — IBKR adapter. Updated 2026-05-28 (public release preparation).*
