# ADR-003: License — AGPL-3.0

**Status**: Accepted
**Date**: 2026-05-27

---

## Context

TraderLens is preparing for public release on GitHub. The project is small and
single-author today, but a sustainable open-source posture needs a license
chosen before — not after — the first public push, because:

- Once code is pushed under a given license, **that version stays under that
  license forever**. Future versions can be relicensed, but existing checkouts
  cannot.
- The author is the **sole copyright holder**, which preserves maximum
  flexibility (dual licensing, future commercial offerings) — but only if the
  license is picked deliberately, not by default.
- The project belongs to a problem space (trading tooling, broker integrations)
  where commodified SaaS rehosting is a realistic risk: a permissive license
  invites a third party to wrap the code and sell hosted access without
  contributing back.

## Decision

**License: [AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0.html)**
(GNU Affero General Public License, version 3, OSI-approved).

The license text is committed verbatim from the canonical
`https://www.gnu.org/licenses/agpl-3.0.txt` to [LICENSE](../../LICENSE).

## Why AGPL-3.0

### 1. AGPL's network-use clause closes the SaaS rehost loophole
GPL-3.0 requires source disclosure when binaries are distributed, but says
nothing about software run as a service. AGPL §13 extends the reciprocity to
"users interacting with the modified work over a network" — anyone hosting a
modified TraderLens as a service must also publish their full source. In
practice, this:
- preserves the open-source contract (improvements come back),
- without preventing self-hosting, internal corporate use, or personal use.

### 2. The author retains commercial flexibility
A copyright holder is not bound by their own license. Concretely:
- Closed-source commercial editions or hosted SaaS by the author remain
  feasible without any contradiction with the public AGPL grant.
- Dual licensing is possible later (sell a non-AGPL commercial license to
  parties that cannot comply with AGPL §13). This requires a CLA before
  accepting external contributions — see Risks below.

### 3. AGPL is OSI-approved
Unlike newer "source-available" licenses (BSL, SSPL, PolyForm Shield,
Elastic v2), AGPL is recognised by the OSI as a real open-source license. This
matters for:
- Discoverability (GitHub trending / HN / package indexes treat OSI licenses
  differently from source-available ones),
- Adoption (organisations with open-source-only procurement policies),
- Community trust (no perception of "fauxpen-source").

## Consequences

### For users
- **You may use, modify, and self-host TraderLens freely.** Personal use,
  internal company use, modifying the code for your own deployment — all
  unrestricted, including for commercial activity.
- **If you offer TraderLens (or a modified version) as a network service to
  others**, you must make your full source code available to those users under
  AGPL-3.0 (§13). For most users this never applies, because they run it
  locally for themselves.
- **You may not sublicense under a more permissive license.** Forks remain
  AGPL.

### For contributors
- All contributions are licensed under AGPL-3.0 by default (per the inbound =
  outbound convention).
- Until a CLA is in place, the project cannot retroactively relicense
  contributed code. This is acceptable for now (no external contributors yet)
  but should be revisited before merging the first external PR — see Risks.

### For the author
- Future closed-source commercial editions are not blocked, since the author
  holds full copyright.
- The decision is **irreversible for the versions actually published**: any
  version pushed under AGPL stays under AGPL forever, even if a future v2.0
  relicenses.

## Risks

| Risk | Mitigation |
|---|---|
| **License choice is one-way at publish time** — once a tagged release exists publicly under AGPL, that version is permanently AGPL. | Verify the license choice in the working tree one final time before the first `git push` to the public remote. |
| **No CLA yet** — external contributions accepted before a CLA exists mean those copyrights stay with the contributors. The project then cannot dual-license that code without each contributor's permission. | Treat the first external PR as the trigger to add a CLA (recommended: [cla-assistant.io](https://cla-assistant.io/)). Until then, no PR is accepted from someone other than the copyright holder. |
| **AGPL deters adoption** by organisations that fear §13. | This is partly the point (it discourages SaaS rehosts). The README explicitly notes self-hosting and internal corporate use are unaffected, to reduce false fear. |
| **Permission ambiguity around running TraderLens against IBKR's network** | TraderLens calls IBKR's Flex Web Service; it does not redistribute IBKR's service. AGPL §13 applies to TraderLens itself running as a service to *other users*, not to TraderLens consuming a third-party API. |

## Alternatives Considered

1. **MIT / Apache-2.0** — maximal permissiveness, but anyone can rehost a
   modified version as a closed-source SaaS without contributing back. Acceptable
   for a true library, not for a useful end-user application in a commodifiable
   space. Rejected.
2. **GPL-3.0** — same copyleft as AGPL but only triggers on binary
   distribution, not on network use. Same SaaS-rehost loophole as MIT in
   practice. Rejected.
3. **BSL (Business Source License)** — time-bombed proprietary that converts
   to a real OSI license after a delay. Pragmatic for VC-backed companies, but
   not OSI-approved and signals "fauxpen-source" to communities. Rejected.
4. **SSPL (Server Side Public License)** — MongoDB's response to AWS
   DocumentDB. Rejected by OSI as not genuinely open. Same trust issue as BSL.
   Rejected.
5. **PolyForm Shield / Elastic License v2** — source-available with use
   restrictions. Same OSI-status issue. Rejected.

## References

- AGPL-3.0 canonical text: https://www.gnu.org/licenses/agpl-3.0.html
- OSI license list: https://opensource.org/licenses
- Choosealicense.com on AGPL: https://choosealicense.com/licenses/agpl-3.0/
- Background on MongoDB's SSPL trajectory (cautionary tale on relicensing
  later): https://www.mongodb.com/licensing/server-side-public-license
- Project [LICENSE](../../LICENSE)
