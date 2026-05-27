# TraderLens — Documentation Index

> Navigation hub for all public docs. Loaded on demand by new contributors.

---

## 📂 Layout

```
docs/
├── INDEX.md       # this file (navigation)
├── specs/         # frozen specs — changes require review
├── guides/        # end-user operations manual
├── decisions/     # ADRs (Architecture Decision Records)
└── studies/       # spikes / technical investigations (NNN_topic_YYYYMMDD)
```

---

## 📋 specs — specifications

| Document | Status | Purpose |
|---|---|---|
| [REQUIREMENTS.md](specs/REQUIREMENTS.md) | v1.1 frozen | Full requirements — scope / FR / NFR / failure handling / acceptance |
| [INTERFACE_CONTRACT.md](specs/INTERFACE_CONTRACT.md) | 🎯 v1.1 frozen | 12-column CSV schema for the downstream MTS importer + cross-project commit SOP |
| [DATA_ARCHITECTURE.md](specs/DATA_ARCHITECTURE.md) | v1.0 | Three-layer model (fact / annotation / derived) — ownership & mutability rules |
| [SPEC_Code_Review.md](specs/SPEC_Code_Review.md) | v1.0 | Pre-implementation review template (acceptance criteria + risk gates) |

---

## 📘 guides — operations manual

| Document | Status | Purpose |
|---|---|---|
| [OPERATIONS.md](guides/OPERATIONS.md) | ✅ 2026-05-21 | End-user operations: commands / interfaces / log inspection / exit codes / scheduled-task install |
| `QUICKSTART.md` | ⏳ TODO | 5-minute onboarding: request Flex token → configure `.env` → first sync |

---

## 🔬 studies — spikes & investigations

Pre-implementation verifications / proof-of-concept / field surveys. Naming convention `NNN_topic_YYYYMMDD/`, each study self-contained (README + script).

| Study | Topic | Status |
|---|---|---|
| [001_flex_connectivity_spike_20260520](studies/001_flex_connectivity_spike_20260520/README.md) | IBKR Flex Query end-to-end connectivity verification | ✅ Passed |
| 002_trade_confirmation_spike_20260521 | Trade Confirmation query (same-day capture) | 🔧 In progress |

---

## 🧭 decisions — ADRs

> Major architectural decisions, one per file. Naming: `NNN-<slug>.md`.

| ADR | Status | Subject |
|---|---|---|
| [001-drop-ibflex.md](decisions/001-drop-ibflex.md) | ✅ Accepted | Drop `ibflex` 0.15 (parser bugs + unmaintained) → stdlib `xml.etree` + `requests` |
| [002-flex-rate-limit-policy.md](decisions/002-flex-rate-limit-policy.md) | ✅ Accepted | 🚨 Flex rate-limit policy: 10-min interval + 30-min penalty box + no blind retries |
| [003-license-agpl-3.0.md](decisions/003-license-agpl-3.0.md) | ✅ Accepted | License = AGPL-3.0 — network-use copyleft + author retains dual-licensing flexibility |

**Planned**:
- `004-csv-as-only-interface.md` (background already in INTERFACE_CONTRACT, to extract)
- `005-signal-id-stays-in-mts.md` (same)
- `006-multi-broker-naming.md` (TraderLens umbrella + per-broker adapter pattern)

---

## 🔗 External references

- **IBKR Flex Web Service** — https://www.interactivebrokers.com/campus/ibkr-api-page/flex-web-service/
- **ibflex** (Python lib, historical reference only) — https://pypi.org/project/ibflex/

---

*Last updated: 2026-05-28 (public release preparation)*
