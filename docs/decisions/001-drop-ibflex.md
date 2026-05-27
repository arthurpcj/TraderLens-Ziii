# ADR-001: Drop ibflex, use stdlib xml.etree + requests

**Status**: Accepted
**Date**: 2026-05-20
**Context**: Spike 001 ([README](../studies/001_flex_connectivity_spike_20260520/README.md))

---

## Context

REQUIREMENTS v1.0 recommended [`ibflex`](https://pypi.org/project/ibflex/) (0.15) as the Python library for the IBKR Flex Web Service:
- one-liner `client.download(token, query_id)` handles the two-step HTTP flow (SendRequest → poll → GetStatement)
- one-liner `parser.parse(xml)` converts XML into typed Python objects (Decimal / date / Enum)

Spike 001 used ibflex against a real 30-day paper account (33 trades) and exposed **two parser bugs**:

1. **SymbolSummary.reportDate="MULTI"** — ibflex's strict type validation throws `FlexParserError: Can't convert 'MULTI' to <class 'datetime.date'>`. Workaround: remove the SymbolSummary section in Client Portal.
2. **Order has no attribute 'tradePrice'** — the Orders section schema does not match ibflex 0.15's expectations. Working around it cleanly would require removing the Orders section as well.

State of the ibflex library on GitHub:
- Last substantive update was in **2018**; only trivial fixes since.
- IBKR has added several fields / sections since 2018 that ibflex never picked up.
- Maintainer response time is slow — quick fixes are not realistic.

Continuing with ibflex carries these risks:
- IBKR changes a field → ibflex breaks → we work around it again.
- We either fork + self-maintain, or repeatedly ask the user to mutate Flex Query configuration to dodge bugs.

## Decision

**From v1.1, `src/ib_sync.py` will not depend on ibflex.** Replacement:

| Layer | Replacement | LOC |
|---|---|---|
| HTTP two-step flow (SendRequest → poll → GetStatement) | `requests` + hand-written | ~30 |
| XML parsing (read all attributes off `<Trade>` elements) | stdlib `xml.etree.ElementTree` | ~10 |
| Type conversion (str → date / float / int) | hand-written helper (one dict mapping) | ~15 |

**Total**: ~55 lines of hand-written code, replacing one external dependency.

Re-evaluating the "benefits" ibflex was supposed to provide:
- ✅ Two-step flow encapsulation → ~30 lines ourselves (with retry / code 1009 wait).
- ✅ Type conversion → we control types at the SQLite / CSV boundary; ibflex's Decimal/Enum is not needed.
- ❌ Error code constants (SERVER_BUSY / CLIENT_THROTTLED) → trivial to define ourselves, see [ADR-002](002-flex-rate-limit-policy.md).
- ❌ ibflex field renames (e.g., `tradeID` → `trade_id`) → we want IBKR's raw field names for audit; ibflex's "helpful" renaming is undesired.

## Consequences

### Upsides

- **Zero ibflex dependency** — no exposure to library bugs / forced upgrades / abandonment.
- **Leaner `requirements.txt`** — only `requests` remains (plus future `gspread` + `google-auth` for v2).
- **Parsing is transparent** — `xml.etree` reads a Trade element's attribute dict directly; debugging is obvious.
- **Future IBKR fields** — just `trade.attrib.get('newField')`, zero blocker.
- **Simple test fixtures** — pytest uses a real XML sample (53KB from spike), no ibflex mocking.

### Costs

- **~55 extra lines of code** — one-time investment, won't recur.
- **HTTP retry / code 1009 wait implemented in-house** — but [ADR-002](002-flex-rate-limit-policy.md) requires us to own rate-limiting anyway; even with ibflex we would wrap it. Net delta is small.
- **Lose ibflex's type conversion** — written as a small helper, see the field-mapping table accompanying `src/parser.py`.

### Risks

- **ibflex ships a major fix one day** — even then, migrating back is not worth it (rewrite + new dependency). Stay on the in-house implementation.

## Alternatives Considered

1. **Fork ibflex + self-maintain**: work >> writing it ourselves; poor ROI.
2. **Submit PRs to ibflex for "MULTI" + Order schema**: upstream cadence is uncontrollable.
3. **Permanently require users to strip SymbolSummary + Orders sections**: fragile (IBKR Client Portal UI shifts can hide the toggles); we'd also lose the (admittedly small) audit value of the Orders section.
4. **Switch to ib_insync / TWS API instead of Flex**: out of scope per REQUIREMENTS §2.2 (Flex Web Service is the only supported channel).

## References

- ibflex source: https://github.com/csingley/ibflex
- IBKR Flex docs: https://www.interactivebrokers.com/campus/ibkr-api-page/flex-web-service/
- Related: [ADR-002 Flex rate-limit policy](002-flex-rate-limit-policy.md)
