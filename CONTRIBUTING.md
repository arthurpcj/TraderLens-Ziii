# Contributing to TraderLens

Thanks for considering a contribution. TraderLens is a small, opinionated
project; the contribution process is intentionally lightweight, but a few
rules keep the codebase healthy.

> **Before opening a PR**: please open or comment on an issue first, so we
> can agree on direction. Surprise PRs — even good ones — are likely to be
> closed if they don't fit the v1 scope (see [README §9 — Out of scope](README.md#9-out-of-scope)).

---

## Code of Conduct

Be civil. Engage with code and ideas, not with people. Disagreements about
design choices should produce shared understanding, not point-scoring.

## Licensing

By contributing code, documentation, or other content to this repository,
you agree that your contributions are licensed under the
[AGPL-3.0](LICENSE), the same license as the project itself ("inbound =
outbound").

Because the project may later pursue dual licensing for a hypothetical
commercial edition, a Contributor License Agreement (CLA) will likely be
introduced before any external PR is merged. If your PR is the first
external contribution, expect a CLA-signing step. The CLA does not change
the AGPL grant — it only authorizes the project owner to also grant
non-AGPL licenses to third parties.

## Reporting bugs / requesting features

Please open a GitHub issue with:

- **What you did** (commands, inputs, environment),
- **What you expected**,
- **What happened instead** (full traceback if applicable, redacted of any
  IBKR token / account number / real trade data — see
  [DISCLAIMER §Broker terms of service](DISCLAIMER.md)),
- **Why it matters** (use case, frequency).

For Flex Web Service rate-limit issues specifically, please read
[ADR-002](docs/decisions/002-flex-rate-limit-policy.md) first — most
"it broke" reports turn out to be intentional throttle behavior.

## Development setup

Requires Python 3.10+. On Windows:

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# edit .env to fill IBKR_FLEX_TOKEN + IBKR_FLEX_QUERY_ID (optional for tests)
pytest -q
```

Tests should pass without an IBKR token — XML fixtures under
`tests/fixtures/` are anonymized samples (account `U0000000`).

## Branch and commit conventions

- **Branch naming**: `feat/<short-slug>`, `fix/<short-slug>`,
  `docs/<short-slug>`, `chore/<short-slug>`.
- **Commit subject**: `<type>(<scope>): <subject>` (Conventional Commits-ish
  but lightweight). Examples in `git log` are the source of truth.
- **Commit body**: explain *why*, not *what*. The diff already shows what.
- **One logical change per commit.** Don't bundle a feature with an
  unrelated refactor.
- **No `--no-verify` or hook bypasses** unless explicitly requested in
  review.

## Code review process

The project follows the workflow in
[docs/specs/SPEC_Code_Review.md](docs/specs/SPEC_Code_Review.md). For any
change to `src/*.py`, the PR description should include a brief Step-1
review (sections A-F as applicable) — the more invasive the change, the
more thorough the review. For pure docs / typo fixes, the description can
be a one-liner.

Risk classification (per SPEC §1):

- **Green (low risk)** — docs, typos, comments, isolated config tweaks.
  Goes to `main` directly.
- **Yellow (medium risk)** — non-critical code changes with tests.
  Recommend a feature branch + PR.
- **Red (high risk)** — anything touching the Flex client, rate-limit
  gate, SQLite schema, CSV exporter, or state machine. **Required**: feature
  branch + PR + tests + a rollback tag on `main`.

## Testing

- **Every PR must include passing tests.** Run the full suite (`pytest -q`)
  before pushing.
- **New features add tests.** Bug fixes add a regression test that fails
  without the fix.
- **No mocking the SQLite layer.** Integration tests use a real
  `:memory:` SQLite (see `tests/conftest.py`).
- **No live Flex calls in tests.** All Flex tests run against captured
  XML fixtures under `tests/fixtures/`. The 10-minute Flex rate limit is
  not negotiable; see [ADR-002](docs/decisions/002-flex-rate-limit-policy.md).

If you add a new XML fixture from your own Flex output, **anonymize the
account ID** to `U0000000` and remove any `orderReference` strings that
could leak strategy names.

## What is in scope

See [README §9 — Out of scope](README.md#9-out-of-scope) for the
not-list. In addition:

- **Yes**: bug fixes, additional broker adapters as siblings
  (`coinbase_sync`, `td_sync`, …), pivot view improvements, doc fixes,
  test improvements, CI improvements.
- **Probably yes**: same-day capture refinements, alternative output
  formats (JSON, Parquet) as additive features, additional asset-type
  support in SQLite (not CSV export).
- **No**: real-time / TWS API integration, automatic order placement,
  introducing heavy framework dependencies (the project is stdlib-first;
  see [ADR-001](docs/decisions/001-drop-ibflex.md)), modifying the
  v1.0 CSV schema without coordinated cross-project review (see
  [INTERFACE_CONTRACT §5](docs/specs/INTERFACE_CONTRACT.md)).

## Documentation conventions

- English is canonical. The codebase, docs, comments, and commit messages
  are in English.
- One spec, one file. Avoid spawning parallel docs covering the same
  ground — extend the existing spec or write an ADR if the change is a
  decision.
- Link generously, duplicate sparingly.

---

*Last updated: 2026-05-28.*
