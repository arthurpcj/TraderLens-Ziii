# TraderLens Demo Data

A small anonymised SQLite + annotations bundle that lets you explore the
end-to-end pipeline (CSV export + HTML pivot) **without an Interactive
Brokers account**.

---

## What's in the box

| File | Rows | Description |
|---|---|---|
| `trades.sqlite` | 50 | 20-column trade archive (FMCC stocks + MNQ / MES / M6B / MHG / MZC futures) |
| `annotations.csv` | 22 | Local annotation layer (one row per closed round-trip on a target underlying) |

The data was captured from a paper account during the 2026-04 → 2026-05
window, then mechanically transformed:

- **MES position size ×8** (`quantity`, `ib_commission`,
  `fifo_pnl_realized` for trades; `ref_pnl_usd` in annotations).
  This makes the equity curve more illustrative; the other underlyings
  carry their original 1-lot paper sizes.
- **`setup_tag` renamed** to generic identifiers (`setup_a`, `setup_b`)
  — strategy naming is private.
- No `account` field exists in the SQLite schema (Flex XML drops the
  account ID at parse time), so no further scrubbing was needed.

Everything else is verbatim: timestamps, prices, `orderReference`,
commissions per lot, round-trip outcomes. The pipeline's behaviour on
this bundle is representative of how it will behave on your own data.

---

## How to view the demo

From the project root (Windows; adapt slashes on macOS / Linux):

```powershell
# 1. Set up the env once
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. Stage the demo data into where the code expects it
#    (data/ is gitignored — these files don't ship in your fork until you copy them in)
mkdir data 2>$null
copy demo\trades.sqlite      data\trades.sqlite
copy demo\annotations.csv    data\annotations.csv

# 3. Export the CSV for the latest demo date
venv\Scripts\python.exe -m src.exporter --date 2026-05-19

# 4. Generate the self-contained HTML pivot
venv\Scripts\python.exe -m src.pivot

# 5. Open it in your browser
start reports\pivot.html
```

The HTML is a single file — open it offline, send it as an attachment,
diff it against another run. No server, no dependencies.

---

## How this differs from a real run

| | Demo | Real |
|---|---|---|
| Data source | This directory (offline) | IBKR Flex Web Service (live) |
| Account credentials needed | No | Yes (`.env`: token + query ID) |
| Number of trades | 50 (fixed) | Whatever your account has |
| Re-fetching adds data | No | Yes — `INSERT OR IGNORE` idempotent |
| Rate limits apply | No (no network) | Yes — see [ADR-002](../docs/decisions/002-flex-rate-limit-policy.md) |

---

## How the demo was generated

The bundle is produced by an author-only one-shot script that reads the
real (gitignored) SQLite + annotations and applies the transformations
above. The script itself is not part of the public repo — public users
consume the output here.

If you want to swap in your **own** data instead of the demo, just run
the real pipeline (`python -m src.ib_sync` after configuring `.env`);
the resulting `data/trades.sqlite` becomes the input to steps 3 + 4
above.
