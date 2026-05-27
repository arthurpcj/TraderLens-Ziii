# Changelog

All notable changes to TraderLens are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project (loosely) follows [Semantic Versioning](https://semver.org/).
The cross-project CSV interface contract carries its own independent
version, see [INTERFACE_CONTRACT.md](docs/specs/INTERFACE_CONTRACT.md) §5.

---

## [Unreleased]

_No changes yet._

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
- 12-column CSV export (v1.0, frozen) for the downstream MTS DevTest
  backtester: NQ/MNQ/ES/MES futures only, full-volume "scheme E"
  (downstream smart matcher does the filtering).
- PAPER_AUTO ↔ MTS_CONFIRMED state machine for the `category` column,
  driven by the local annotation layer (setup_tag / score / notes on the
  opening trade_id).
- `--lookback N` re-export window for cross-project consistency under the
  state machine.

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
  (see [DATA_ARCHITECTURE](docs/specs/DATA_ARCHITECTURE.md)).

### Added — Reliability

- Two-layer Flex throttle gate (`MIN_INTERVAL_SEC=600`,
  `PENALTY_BOX_SEC=1800`). `1018` → exit immediately + arm the gate; never
  blind-retry. See [ADR-002](docs/decisions/002-flex-rate-limit-policy.md).
- Granular exit codes for the downstream MTS scheduler: `0` OK / idle,
  `2` RETRYABLE (throttle / network), `3` HARD (auth / token).
- NY-weekend skip in the scheduler — avoids wasted Flex calls when no
  trading session is active.
- State persistence (`data/state.json`): backfill window, last successful
  trade date, throttle gate timestamps, last error.

### Added — Tooling

- Windows Task Scheduler self-elevating registration script
  (`scripts/register_ib_sync_task.ps1`). Single `--mode auto` task,
  five trigger times mapped to NY 04:00 / 05:00 / 08:00 / 09:00 / 10:00.
- 171 pytest tests covering state, Flex client, parser (Activity +
  Confirmation, plus robustness suites), SQLite store, exporter and
  state machine, annotations, pivot, round-trip, overlap, timezone, and
  end-to-end integration.

### Added — Documentation

- [REQUIREMENTS.md](docs/specs/REQUIREMENTS.md) — full functional /
  non-functional / failure-handling / acceptance spec.
- [INTERFACE_CONTRACT.md](docs/specs/INTERFACE_CONTRACT.md) — frozen
  12-column CSV contract with the MTS DevTest project + cross-project
  commit SOP.
- [DATA_ARCHITECTURE.md](docs/specs/DATA_ARCHITECTURE.md) — three-layer
  model (fact / annotation / derived), ownership and mutability rules.
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
