# Data Architecture — TraderLens

> **Status**: v1.0 (2026-05-26) — Three-layer model: fact / annotation / derived.
> **Audience**: developers + integrators ([MTS DevTest](https://github.com/arthurpcj/MTS-backtest), future `<broker>_sync` siblings).
> **Companion docs**: [REQUIREMENTS.md](REQUIREMENTS.md) (FR specs) · [INTERFACE_CONTRACT.md](INTERFACE_CONTRACT.md) (cross-project csv) · [SPEC_Code_Review.md](SPEC_Code_Review.md)

---

## 1. Why three layers

TraderLens splits trade data into three layers with **strict ownership and
mutability rules**. The split is not academic — it removes whole categories of
bugs (e.g. "did re-fetching IB overwrite my notes?") and lets the system evolve
(annotation UI, cloud backup, multi-device sync) without rewriting the fact
pipeline.

```
┌──────────────────────────────────────────────────────────────────┐
│ ① FACT LAYER (immutable, source of truth, broker-given)           │
│ data/trades.sqlite — 17 columns, all IB trades                    │
│ Writer:  ib_sync only (INSERT OR IGNORE, idempotent)              │
│ Reader:  exporter, pivot, ad-hoc SQL                              │
└─────────┬────────────────────────────────────────────────────────┘
          │
          ▼ (joined on open_trade_id)
┌──────────────────────────────────────────────────────────────────┐
│ ② ANNOTATION LAYER (persistent, source of truth, user-owned)      │
│ data/annotations.csv — 10 cols, keyed by open-leg trade_id        │
│ Writer:  review.bat (user fills setup_tag / score / notes)        │
│ Reader:  exporter, pivot                                          │
│ Backup:  data/annotations.bak/{timestamp}.csv (R1, last 20)       │
└─────────┬────────────────────────────────────────────────────────┘
          │
          │     join open_trade_id, apply state machine
          ▼
   ┌──────┴───────────────────────────────────┐
   ▼                                          ▼
┌─────────────────────────────┐  ┌────────────────────────────────┐
│ ③ DERIVED — INTERFACE LAYER │  │ ③ DERIVED — VIEW LAYER          │
│ data/exports/                │  │ reports/                       │
│   mts_trades_{date}.csv     │  │   pivot_latest.html            │
│ Writer:  exporter (auto on  │  │ Writer:  pivot.py (auto on     │
│   ib_sync, manual via       │  │   review-flow, manual)         │
│   --review-flow, CLI)       │  │ Consumer: user (browser)       │
│ Consumer: MTS DevTest       │  │ Self-contained, gitignored     │
│ Schema:  v1.0 frozen, 12 col│  │                                │
└─────────────────────────────┘  └────────────────────────────────┘
```

### Two ironclad rules

1. **Truth only flows ① + ② → ③**. The derived layer is recomputable; it never
   writes back into the fact or annotation layers.
2. **Each layer has exactly one writer**. ib_sync owns the fact layer.
   review.bat (via Excel) owns the annotation layer. Derived files have writers
   that read but never modify the upstream layers.

---

## 2. Layer 1 — Fact

| Property | Value |
|---|---|
| **Storage** | `data/trades.sqlite` (SQLite, WAL mode) |
| **Schema** | 17 columns (see `src/sqlite_store.py`); broker-faithful — multiplier, expiry, ib_commission preserved with original IB sign |
| **Key** | `trade_id` (IB-global unique tradeID) |
| **Granularity** | Leg-level (one row per fill leg) |
| **Writer** | `src/ib_sync.py` orchestrator → `sqlite_store.insert_trades_idempotent` |
| **Mutation** | `INSERT OR IGNORE` only. Never UPDATE, never DELETE in normal flow. |
| **Lifecycle** | Permanent. Stays through schema migrations (additive only). |
| **Backup** | None at app level. SQLite is the lowest layer; protect it via OS-level backup if needed. |
| **gitignored** | Yes (contains real trades, privacy + size). |

### Why immutable

Once IB has reported a fill, that fill is a historical fact. Re-fetching IB
should never overwrite or contradict existing rows. The `INSERT OR IGNORE`
discipline + `tradeID` as primary key give us:
- Safe to re-run `ib_sync` arbitrarily many times.
- Safe to extend the lookback window without dedup logic.
- Safe to recover from corrupted state.json by re-fetching last 30 days.

### What the fact layer does NOT store

- `setup_tag`, `score`, `notes` (user subjective) — these live in layer ②.
- Any computed projection (round-trips, PnL aggregates, by-setup totals) — these
  are derived on the fly by `roundtrip.py` and `pivot.py` from the leg data.
- MTS-side concepts like `signal_id`, `setup_id` — those belong to MTS's domain
  model and are derived in the MTS project.

---

## 3. Layer 2 — Annotation

| Property | Value |
|---|---|
| **Storage** | `data/annotations.csv` (UTF-8, RFC 4180) |
| **Schema** | 10 cols: 4 editable (`open_trade_id`, `setup_tag`, `score`, `notes`) + 6 read-only refs (`ref_open_date`, `ref_open_time`, `ref_underlying`, `ref_direction`, `ref_pnl_usd`, `ref_round_trips`) |
| **Key** | `open_trade_id` — the **opening leg's IB tradeID**, stable across re-fetch and across re-pairing |
| **Granularity** | Round-trip entry level. A single opening fill that splits into N closing fills shares one annotation across all N derived round-trips. |
| **Writer** | `review.bat` → user edits in Excel → `Ctrl+S` writes the csv |
| **Lifecycle** | Permanent. The file grows over time as new trades happen. Old annotations are preserved indefinitely (no archival, no rotation). |
| **Backup** | Automatic — every `write_template` run snapshots the current file to `data/annotations.bak/{YYYY-MM-DD-HHMMSS}.csv` (R1), keeping the last 20. |
| **Atomic write** | Yes — temp file + rename (R2). Crash mid-write leaves the previous file intact. |
| **Schema validation** | On read — missing required columns fail loud rather than silently dropping data (R3). |
| **gitignored** | Yes (real trade subjective notes, privacy). |

### Three-tier setup_tag resolution

When a round-trip needs to know its `setup_tag`, the resolver walks three tiers
(per FR-PIVOT-3c):

1. **Explicit** — `annotations.csv` has a non-empty `setup_tag` for that
   `open_trade_id`. User has manually committed.
2. **Order ref alias** — round-trip's `order_ref` (stamped by quant/Backtrader)
   maps to a `setup_tag` code via `config/pivot_tags.json`'s `order_ref_aliases`.
3. **`untagged`** — explicit fall-through, visible in reports (never hidden).

### Why CSV (not GSheet, not SQLite, not a database)

- **CSV is portable**: Excel opens it natively; any text editor works as
  fallback; can be backed up to OneDrive / Google Drive / a USB stick.
- **CSV is auditable**: line-by-line diff, easy to spot a wrong edit.
- **CSV is stdlib-only**: zero new dependency (Python's `csv` module is in the
  standard library).
- **GSheet was deferred** to v2 — too much setup cost (service account JSON,
  OAuth, network dependency) for the user-quantity savings.
- **SQLite would conflate** fact and subjective layers. Re-fetch from IB would
  need special-case logic to preserve annotations. Two writers (ib_sync writing
  fact, user writing subjective) creates lock contention and reasoning load.

### Capacity expectations (long-term)

| Horizon | Round-trips | File size | Excel responsiveness |
|---|---|---|---|
| 1 year paper (~2 trades/day) | ~500 | ~100 KB | instant |
| 5 years | ~2,500 | ~500 KB | instant |
| 10 years (paper → live) | ~5,000 | ~1 MB | instant |
| 20 years (worst case) | ~10,000 | ~2 MB | still instant |

Single-file storage is correct at this scale. Splitting by year / instrument /
setup would create cross-file join complexity for no measurable benefit. If we
ever hit > 50,000 rows (not realistic for a discretionary trader), a server-UI
upgrade — not a file split — would be the answer.

---

## 4. Layer 3 — Derived

The derived layer holds *projections* of `fact ⋈ annotation`. There are two
projections, each with a different consumer.

### 3a — Interface projection (for MTS)

| Property | Value |
|---|---|
| **Storage** | `data/exports/mts_trades_{YYYY-MM-DD}.csv` (one file per trade date) |
| **Schema** | v1.0 frozen, 12 columns (see [INTERFACE_CONTRACT.md §2.3](INTERFACE_CONTRACT.md)) |
| **Granularity** | Leg-level (matches fact layer) |
| **Writer** | `src/exporter.py` — called automatically by `ib_sync.py`, manually via CLI, or by `review-flow` after annotation edits |
| **Consumer** | MTS DevTest (`mts record --import-from-ib --date X`) |
| **Lifecycle** | Recomputable. Files may be overwritten any time by re-export. Safe to delete and regenerate. |
| **gitignored** | Yes (real trades) |
| **State machine** | See §5 — content varies based on annotation state for that trade_date |

### 3b — View projection (for user)

| Property | Value |
|---|---|
| **Storage** | `reports/pivot_latest.html` (single self-contained file) |
| **Schema** | Browser-renderable HTML, ~400 KB with inlined PivotTable.js + SVG charts |
| **Granularity** | Round-trip aggregations + KPI rollups + by-setup scoring |
| **Writer** | `src/pivot.py` — `python -m src.pivot` regenerates, opt-in browser auto-open via `--review-flow` |
| **Consumer** | User (browser). Self-contained: works offline, portable to phone / cloud drive. |
| **Lifecycle** | Recomputable. Stale freely; rerun any time. |
| **gitignored** | Yes |

### Why two projections, not one

The interface and view projections have very different consumers and update
cadences. The interface csv must be machine-parseable, schema-stable, and
contract-bound (MTS depends on it). The view html must be human-readable, can
embed JavaScript, and changes shape with every visual iteration. Coupling them
would force visual changes through a contract-review process — friction with
no benefit.

---

## 5. CSV state machine (interface projection)

The MTS interface csv content depends on what the user has annotated. There are
**two states per trade_date**, decided independently:

| State | When | csv content | `category` column |
|---|---|---|---|
| **A — Raw** | No round-trip for this date has any explicit annotation in `annotations.csv` (all `untagged` or unmatched) | All NQ/MNQ/ES/MES futures legs for the date (scheme-E behavior) | `PAPER_AUTO` |
| **B — Confirmed** | At least one round-trip for this date is annotated with a setup_tag ∈ `MTS_RELEVANT_SETUPS` (currently `{Q_intraday}`) | Only the legs of round-trips whose `setup_tag` ∈ `MTS_RELEVANT_SETUPS` | `MTS_CONFIRMED` |

### State decision is per trade_date (granular)

Two different dates can be in different states at the same time. If the user
has fully annotated Day 1 but not touched Day 2, Day 1's csv is in State B and
Day 2's csv is in State A. The user marks dates "MTS-audited" by labeling at
least one of that date's trades, not by setting a global flag.

### State transitions

- **A → B**: user adds a `Q_intraday` annotation that maps to a round-trip on
  that date → next re-export emits State B for that date.
- **B → A**: only if the user removes all `Q_intraday` annotations for that
  date — extremely rare (user changes their mind about every trade).
- **B → A' (smaller B)**: user removes one `Q_intraday` annotation but keeps
  others → date stays in State B, with one fewer row.

### Header-only csv is valid

A trade_date in the lookback window with no MTS-relevant round-trips (e.g.
user marked all trades as S1 manual; or it was a non-trading day) gets a
**header-only csv** (just the column header line, no data rows). This is the
correct State-B representation of "the user has audited this date; nothing here
belongs to MTS".

MTS's single-date import must accept header-only csv as `exit 0 + 0 rows
imported` (which is its natural behavior — reading 0 rows is not an error).

### MTS-side branching (~10 LOC)

MTS reads the `category` column to choose its 0-candidate behavior:

```python
# MTS pseudocode
if pair.csv_category == 'MTS_CONFIRMED':
    # IB_Sync has confirmed this is an MTS trade. A 0-candidate smart match
    # means the benchmark didn't capture this signal — alert and write
    # FORCE_WRITTEN so the user can investigate.
    on_zero_candidate = 'FORCE_WRITTEN_WITH_ALERT'
else:  # PAPER_AUTO (raw, possibly contains user manual trades)
    # Default to skipping unmatched trades into the review queue — assume
    # 0 candidates means "user manual trade, not ours" (DC behavior).
    on_zero_candidate = 'REVIEW_QUEUE_DEFAULT_SKIP'
```

This is the **only** MTS-side code change required by this architecture.

---

## 6. Trigger model — when each layer is written

```
Event                                    Layer written
────────────────────────────────────────────────────────────────
ib_sync scheduled run (Beijing 04..10)   ① (Flex → SQLite)
ib_sync scheduled run                    ③a (auto re-export per state)
review.bat opens Excel                   (no write yet)
user Ctrl+S in Excel                     ② (annotations.csv)
review.bat post-Enter                    ③a (re-export last N days)
review.bat post-Enter                    ③b (regenerate pivot html)
manual `python -m src.exporter --date X` ③a (one date)
manual `python -m src.pivot`             ③b
```

### review.bat full sequence (FR-PIVOT-3d + this architecture)

```
[1/4] Refresh annotations.csv template (preserves existing, appends new
      round-trips, refreshes ref_* columns).  ← layer ② structure refresh
      (also: backup current file to data/annotations.bak/  ← R1)

[2/4] Open csv in Excel (os.startfile). User fills setup_tag / score / notes.
      Ctrl+S to save.                          ← layer ② content write

[3/4] Wait for user to press Enter in terminal (Ctrl+C aborts cleanly).

[4/4] Re-export mts_trades_{date}.csv for the last N days (default N=90;
      `--lookback N` or `--lookback all` to override).
      Then regenerate reports/pivot_latest.html and open in browser.
                                               ← layer ③a + ③b refresh
```

### Lookback window contract

- **IB_Sync side default**: 90 days. Configurable via `--lookback N` /
  `--lookback all`.
- **MTS-side wrapper** (`daily_run.bat`, user-layer, [INTERFACE_CONTRACT §9](INTERFACE_CONTRACT.md)):
  must loop the **same N** days when calling MTS import. Asymmetric N causes
  silent drift (IB_Sync changed Day -60 csv, MTS only imported Day -7 → Day
  -60 D5 row is stale).
- **Recommendation**: both sides hard-code N=90 unless the user explicitly
  needs a longer window.

---

## 7. Robustness guarantees (R-series)

| ID | Guarantee | Implementation |
|---|---|---|
| **R1** | **Annotation backup** — every annotation write snapshots the previous file | Before `write_template`, copy current `annotations.csv` to `data/annotations.bak/{YYYY-MM-DD-HHMMSS}.csv`. Prune to last 20 to bound disk use (~2 MB total). |
| **R2** | **Atomic annotation write** — crash mid-write leaves previous file intact | Write to `annotations.csv.tmp`, then `Path.replace` (POSIX rename, atomic on same filesystem). |
| **R3** | **Schema validation on read** — Excel-corrupted schema fails loud | `load_annotations` checks for the required key column (`open_trade_id`). Missing → friendly error with "restore from data/annotations.bak/". |
| **R4** | **No orphan deletion** — round-trips that disappear from the fact layer (re-pairing edge cases) keep their annotation row | `write_tag_template` already preserves orphans with empty ref_* columns. |
| **R5** | **Doctor command** (backlog) | `python -m src.annotations --doctor` checks: duplicate keys / invalid setup_tag / score out of range / open_trade_id missing from SQLite. Not yet implemented. |

These are the minimum bar for treating annotations as a source-of-truth layer
(not a scratch file). All are stdlib-only.

---

## 8. Cross-layer invariants (regression guards)

These invariants should hold at all times. If they break, something is wrong.

| # | Invariant | How to check |
|---|---|---|
| I1 | Re-running ib_sync N times produces the same SQLite state (modulo new fills from IB) | `INSERT OR IGNORE` on `trade_id` PK |
| I2 | Re-running `--review-flow` with no Excel edits produces a byte-identical re-export | `write_template` preserves existing; csv export is deterministic |
| I3 | Deleting and regenerating any layer-③ file produces the same content | ③ is a pure function of ① + ② |
| I4 | Annotation never leaks into SQLite | code review — no `UPDATE trades SET setup_tag = ...` exists anywhere |
| I5 | Fact-layer `trade_id` and annotation-layer `open_trade_id` use the same vocabulary (IB tradeID) | type system + reviewer attention |
| I6 | csv schema columns and order match v1.0 contract regardless of state | `tests/test_exporter.py::test_csv_schema_v1_0_12_cols` |

---

## 9. Evolution paths (what this design supports)

### Adding a new MTS-relevant setup
Edit `config/pivot_tags.json` `mts_relevant: [...]` (or `constants.py`
`MTS_RELEVANT_SETUPS`) to include the new code. No code, no contract change,
no MTS-side action — next re-export picks up new mapping automatically.

### Adding a new broker (e.g. `td_sync`)
The new broker module ships its own `<broker>_sync` package writing its own
SQLite (or shares the one), exports its own csv to `data/exports/`. The
annotation layer is broker-agnostic (keyed by leg-level trade_id, which is
unique across brokers if they use different ID spaces, or namespaced if not).
The MTS contract stays per-broker until evidence calls for a unified schema.

### Cloud sync (v2 ADR)
Move `data/annotations.csv` into a directory that's auto-synced (OneDrive /
Drive / Dropbox). The application code doesn't need to change — the annotation
layer is just a file. Backup files (`annotations.bak/`) follow naturally.

### MTS-side "csv-row-disappeared → IGNORED" mechanism (D3b)
If paper-period audit shows the residual D5 pollution from State A misclassi-
fication is too high, MTS could add: on import, for any D5 row with
`recorded_by=cli_ib_sync` whose `(date, trade_id)` is no longer in the current
csv, mark `actual_status=IGNORED`. This is an MTS-project decision; it does
not require any IB_Sync-side change. The contract already supports it (writer
not modifying upstream layers).

### Per-trade verification UI (D, server mode)
A future stdlib `http.server` mode (`python -m src.pivot --review`) could
expose the annotation layer through a web UI — dropdowns for setup_tag,
slider for score, in-context view of equity / drawdown. The annotation layer
remains the same file; only the editor changes. Static-HTML fallback survives
unchanged.

---

## 10. What this architecture explicitly does NOT do

- **No transaction log / event sourcing** — annotations are last-write-wins.
  R1 backups give point-in-time recovery; that's enough.
- **No multi-user concurrent edit** — single-user assumption. Lock contention
  is avoided by R2 (atomic write); two simultaneous review.bats would race
  the backup-then-rewrite sequence, and one would clobber the other. Don't
  do that.
- **No real-time push to MTS** — interface is file-based, polled by MTS or
  triggered by wrapper.bat. Latency is "next wrapper run" (minutes to hours),
  which matches the V6 delayed-eventual-consistency model in MTS.
- **No cross-database joins at runtime** — pivot/exporter load SQLite + csv
  into Python, do the joins in-memory. Files small enough that this is
  cheap and avoids ORM weight.
- **No schema migration tooling for annotations.csv** — the schema is so
  stable (4 editable + 6 ref columns) that we'd rather hand-migrate in the
  unlikely event of a change.

---

## 11. References

- **Fact-layer schema**: `src/sqlite_store.py` `init_schema`
- **Annotation-layer schema**: `src/annotations.py` `ANNOTATION_COLUMNS`
- **Round-trip pairing**: `src/roundtrip.py` `pair_round_trips`
- **Interface csv v1.0**: [INTERFACE_CONTRACT.md §2.3](INTERFACE_CONTRACT.md)
- **State machine + lookback contract**: [INTERFACE_CONTRACT.md §5.6](INTERFACE_CONTRACT.md) v1.1 changelog (C6–C10)
- **MTS-side implementation checklist**: [INTERFACE_CONTRACT.md §5.5](INTERFACE_CONTRACT.md)
- **Annotation editor flow**: `src/pivot.py` `review_flow` (FR-PIVOT-3d)
- **Project requirements**: [REQUIREMENTS.md](REQUIREMENTS.md) FR-PIVOT / FR-EXPORT
- **Code review standard**: [SPEC_Code_Review.md](SPEC_Code_Review.md)

---

*v1.0 sediment — 2026-05-26. Updated as the three-layer model evolves.*
