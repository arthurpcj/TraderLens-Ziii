# Disclaimer

**TraderLens is software for personal record-keeping and analytics of your own
brokerage activity. It is not a trading system, a broker, or a financial
service.**

---

## Not financial advice

Nothing in this repository (code, documentation, examples, screenshots,
issue / PR discussion) constitutes investment, trading, tax, legal, or
financial advice. The author is not a registered financial adviser, broker,
or fiduciary in any jurisdiction. Any patterns, metrics, classifications,
or visualizations TraderLens produces from your data are mechanical
summaries, not recommendations.

If you trade based on what TraderLens shows you, the decisions and outcomes
are entirely yours.

## Use at your own risk

The software is provided **"AS IS", without warranty of any kind**, as
stated in the [AGPL-3.0 license](LICENSE) §15-16. In particular, no
guarantee is made that TraderLens will:

- correctly download every trade from Interactive Brokers,
- correctly parse every field returned by the Flex Web Service,
- correctly preserve numerical precision through the SQLite / CSV
  round-trip,
- correctly classify trades for the downstream backtester,
- recover gracefully from rate limits, network outages, IBKR side errors,
  or IBKR-side schema changes.

You are responsible for verifying that the data TraderLens shows you (and the
CSV it exports for downstream consumers) matches the truth on your
broker's statements. **Reconcile periodically against IBKR's own
statements** — particularly P&L, commission, and trade counts. Do not
treat TraderLens output as the authoritative record.

## Data integrity and other software

TraderLens reads from your broker over the network, writes local
files (SQLite, CSV, HTML, logs), and you typically open those files
in other software. The author makes **no warranty** about behaviour
at any of those boundaries:

- **Storage failures** — disk / filesystem corruption, accidental
  deletion or overwrite, crashes during write (despite atomic-rename
  safeguards), simultaneous TraderLens runs racing the same files.
- **Excel and CSV tools** — silent type coercion of numeric strings,
  locale-specific decimal separators, date reinterpretation. The CSV
  may look "fixed" after Excel saves it but no longer match what
  TraderLens wrote.
- **File-sync clients** (OneDrive, Dropbox, iCloud, Google Drive)
  may interact badly with the atomic-write pattern or version files
  in ways that confuse the next run.
- **Browsers** may render the HTML pivot incorrectly under unusual
  zoom, theme, or extension settings.
- **Antivirus / DLP software** may quarantine the venv or the
  generated HTML.
- **OS-level events** — power loss, sleep / hibernate races,
  filesystem journal quirks.

If anything downstream of TraderLens mangles your data, that is
**not something this project can fix or be held liable for**. Treat
`data/` like any other valuable folder: periodic backups, versioning,
off-site copies. **Reconcile against your broker's own statements
regularly.**

## Broker terms of service

TraderLens uses **your own** IBKR Flex Web Service credentials (Token +
Query ID) to fetch **your own** account activity. Your use of the IBKR
Flex Web Service is governed by Interactive Brokers' terms of service,
not by TraderLens. The author does not endorse, partner with, or represent
Interactive Brokers in any way.

In particular:

- **Rate limits.** IBKR's Flex Web Service has rate limits, and abusive
  use can lead to a permanent IP-level ban affecting *all* of your IBKR
  API access. TraderLens enforces a 10-minute interval + 30-minute
  penalty-box gate (see [ADR-002](docs/decisions/002-flex-rate-limit-policy.md)),
  but the responsibility for compliance is yours. Do **not** disable or
  bypass the gate.
- **Token security.** Your Flex Token authenticates Read-only Flex queries
  against your account. Keep it in `.env` (already in `.gitignore`); never
  commit it; rotate it if exposed.

## No automated trading

TraderLens **does not place orders, modify positions, or interact with TWS,
the Client Portal API, or any order-routing system**. It only consumes
read-only Flex Web Service responses. If you wire TraderLens output into a
system that does place orders, that integration is yours to build, test,
and own.

## Stocks / options / FX

TraderLens v1 is scoped to futures (NQ / MNQ / ES / MES) for CSV
export, with stocks and other instruments archived to SQLite but not
exported. Behavior on assets outside this scope (options, FX, crypto,
non-US futures) is **not tested** and may produce incorrect results.
The field-coverage assumptions in `src/parser.py` reflect what was
observed in a US futures paper account — other instrument classes
may carry different field sets.

## Jurisdiction

You are responsible for ensuring your use of TraderLens complies with the
laws and regulations of your jurisdiction, including but not limited to
tax reporting, record-keeping requirements, and any restrictions on
automated processing of financial data.

---

*This disclaimer is informational and does not modify the [AGPL-3.0
license](LICENSE). In case of conflict, the license terms govern.*
