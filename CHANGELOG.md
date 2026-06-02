# Changelog

All notable changes to TraderLens are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project (loosely) follows [Semantic Versioning](https://semver.org/).
The CSV export schema carries its own independent version (v1.0,
12 columns, frozen).

---

## [Unreleased]

_No changes yet._

## [1.2.0] - 2026-06-03

Local pivot analytics gains two user-facing features — a windowed
calendar and one-click detail CSV export — plus multi-fill order
coalescing across the pivot and the MTS export.

### Added — Pivot analytics

- **Calendar windowed viewport** (FR-PIVOT-8) — the calendar now shows
  one month at a time with a constant column width and stable layout,
  instead of stretching across the whole history. `←` / `→` step through
  months as a pure view change (no data recompute); changing a filter
  gently re-anchors to a month that has data rather than landing on a
  blank window. Driven by pure functions `_calendar_window` /
  `_resolve_anchor` (unit-tested).
- **Detail CSV export** (FR-PIVOT-4.5) — a `⬇ CSV` button on the detail
  table downloads the *currently filtered* rows as CSV (client-side
  Blob, no server, respects the active filter state).

### Added — Pipeline

- **Multi-fill order coalescing** (FR-PIVOT-2c) — multiple fills of a
  single order now collapse into one trade (quantity-weighted VWAP,
  keyed on `order_id` with a same-second fallback; mixed
  side / open-close / date are refused). Applied before round-trip
  pairing in both the pivot and the exporter. The MTS CSV export is now
  order-level (one row per order, total quantity) — the 12-column v1.0
  schema is unchanged, so downstream consumers need no changes.
- **Activity self-heal upsert** — the T+1 Activity feed now backfills
  `order_id` and unifies native IB fields on existing rows via
  `ON CONFLICT DO UPDATE`; same-day Confirmation rows stay
  `INSERT OR IGNORE` and never overwrite. User/audit columns
  (annotations, category, timestamps) are never touched.

### Added — Documentation

- Bilingual README — [`README_cn.md`](README_cn.md) (中文) cross-linked
  with the English README as a dual-language front door.

### Changed

- Pivot header revamp — two-column header layout and a wider equity
  curve (860 → 1180 px). `pivot.generate()` gains a read-only mode so the
  demo renders with zero side effects on the source database.

### Fixed

- Calendar rendered on a single squashed row in some reports
  (`.cal-wrap` now `nowrap` + `overflow-x`).

## [1.1.0] - 2026-05-29

Humanises the public face of TraderLens and adds first-class
macOS + Linux support. 100% backward compatible — no `src/` changes;
every existing Windows entry point is unchanged.

### Added

- macOS + Linux first-class support: bash wrappers
  ([`scripts/run_ib_sync.sh`](scripts/run_ib_sync.sh),
  [`scripts/review.sh`](scripts/review.sh)), a macOS launchd installer
  ([`scripts/install-launchd-task.sh`](scripts/install-launchd-task.sh),
  idempotent + timezone-agnostic), and documented `cron` / `systemd
  timer` templates. Executable bit (`100755`) set in the git index.
- Zero-setup [`demo.html`](demo.html) — a top-level, ~440 KB
  self-contained file (jQuery + PivotTable.js inlined). Double-click to
  open; no Python, no broker token, and it never touches `data/`.

### Changed

- README user-value rewrite — "Who is this for?", a "Privacy and data
  ownership" section listing every network call, and a broker-agnostic
  roadmap (Interactive Brokers reframed as the first adapter).
- Stronger [DISCLAIMER](DISCLAIMER.md) — a "Data integrity and other
  software" section, plus an explicit acceptance line in the README.
- MTS cross-project plumbing relocated from public `docs/specs/` to
  private local storage (off-GitHub, zero loss).

### CI

- New `lint-line-endings` job preventing CRLF/LF mismatch from breaking
  `.bat` / `.ps1` / `.sh` scripts.

## [1.0.0] - 2026-05-28

Initial public release.

### Added — Pipeline

- IBKR Activity Flex Web Service ingestion (Token + Query ID, two-step
  `SendRequest → poll → GetStatement` envelope, stdlib `requests` only).
- Same-day Trade Confirmation ingestion (`<TradeConfirm>`) for fills that
  haven't yet appeared in the Activity feed. `data_source` column
  distinguishes the two; `tradeID` consistency makes them merge into one
  table.
- XML parsing via stdlib `xml.etree.ElementTree` — robust to extra fields,
  field reorder, missing optional fields. Critical-field absence skips that
  row with a WARN, never aborts the batch.
- SQLite archive (20 columns including `asset_type`, `data_source`,
  `order_ref`), `INSERT OR IGNORE` for idempotency. Additive migrations
  handle pre-spike-002 databases.
- 12-column CSV export (v1.0, frozen) covering NQ/MNQ/ES/MES futures.
  Schema-stable for machine consumption by downstream tools.
- Per-trade-date state machine for the `category` column
  (`PAPER_AUTO` ↔ `MTS_CONFIRMED`), driven by the local annotation
  layer (setup_tag / score / notes on the opening trade_id).
- `--lookback N` re-export window so re-annotated dates flow through
  to the CSV without re-fetching from the broker.

### Added — Local analytics

- Self-contained HTML pivot generator (`src/pivot.py`), one file, no
  server. Includes KPI block, equity curve (with date ticks), calendar
  heatmap (6-col Mon-Fri+Sun), by-setup scoring, fully filter-linked
  detail table.
- Round-trip pairing (`src/roundtrip.py`) connects open/close legs for
  derived metrics (PnL, hold time, intraday vs swing).
- Tier-1 pivot UX: shared filter state across all five views, sticky top
  bar, responsive CSS Grid layout, date filter (chips + from/to + arrow
  nav + calendar drag-select), streak metric filter-aware, default
  pivot = EntryHour × Result.
- One-shot review flow (`scripts/review.bat`, `--review-flow`): four
  manual steps collapsed into one entry point.
- Local CSV annotation layer (`data/annotations.csv`, 10 cols, keyed by
  opening trade_id). Decoupled from the immutable IB-fact SQLite layer
  so re-fetching never overwrites user notes.

### Added — Reliability

- Two-layer Flex throttle gate (`MIN_INTERVAL_SEC=600`,
  `PENALTY_BOX_SEC=1800`). `1018` → exit immediately + arm the gate; never
  blind-retry. See [ADR-002](docs/decisions/002-flex-rate-limit-policy.md).
- Granular exit codes for schedulers and wrappers: `0` OK / idle,
  `2` RETRYABLE (throttle / network), `3` HARD (auth / token).
- NY-weekend skip in the scheduler — avoids wasted Flex calls when no
  trading session is active.
- State persistence (`data/state.json`): backfill window, last successful
  trade date, throttle gate timestamps, last error.

### Added — Tooling

- Windows Task Scheduler self-elevating registration script
  (`scripts/register_ib_sync_task.ps1`). Single `--mode auto` task,
  five trigger times mapped to NY 04:00 / 05:00 / 08:00 / 09:00 / 10:00.
- Test suite covering state, Flex client, parser (Activity +
  Confirmation, plus robustness suites), SQLite store, exporter and
  state machine, annotations, pivot, round-trip, overlap, timezone,
  and end-to-end integration.

### Added — Documentation

- [SPEC_Code_Review.md](docs/specs/SPEC_Code_Review.md) — pre-implementation
  review checklist used in the project.
- [OPERATIONS.md](docs/guides/OPERATIONS.md) — end-user operations manual.
- ADRs:
  [001 drop ibflex](docs/decisions/001-drop-ibflex.md),
  [002 Flex rate-limit policy](docs/decisions/002-flex-rate-limit-policy.md),
  [003 license = AGPL-3.0](docs/decisions/003-license-agpl-3.0.md).
- Spike 001 (Flex connectivity verification, passed).
- [CONTRIBUTING.md](CONTRIBUTING.md), [DISCLAIMER.md](DISCLAIMER.md),
  this CHANGELOG.

### Project setup

- License: [AGPL-3.0](LICENSE).
- stdlib-first ethos — `requests` is the only runtime dependency.

---

*Pre-1.0 development history (M0 → M5, 2026-05-20 → 2026-05-28) is
preserved in the git log of the `release/v1-public-prep` branch; the
public `main` branch starts cleanly from v1.0.0.*
