# TraderLens demo bundle (advanced reference)

The headline demo is the self-contained HTML file at the project
root: **[`demo.html`](../demo.html)** — double-click to open. No
setup, no Python, no broker account.

This directory keeps the **raw bundle** the demo HTML was generated
from, in case you want to inspect the data layer or re-run the
pipeline yourself.

---

## What's in here

| File | Rows | Description |
|---|---|---|
| `trades.sqlite` | 50 | 20-column trade archive (FMCC stocks + MNQ / MES / M6B / MHG / MZC futures) |
| `annotations.csv` | 22 | Local annotation layer (one row per closed round-trip on a target underlying) |

The data was captured from a paper account during the 2026-04 → 2026-05
window, then mechanically transformed:

- **MES position size ×8** (`quantity`, `ib_commission`,
  `fifo_pnl_realized` for trades; `ref_pnl_usd` in annotations).
  This makes the equity curve more illustrative; the other
  underlyings carry their original 1-lot paper sizes.
- **`setup_tag` renamed** to generic identifiers (`setup_a`,
  `setup_b`) — strategy naming is private.
- No `account` field exists in the SQLite schema (Flex XML drops
  the account ID at parse time), so no further scrubbing was needed.

Everything else is verbatim: timestamps, prices, `orderReference`,
commissions per lot, round-trip outcomes. The pipeline's behaviour
on this bundle is representative of how it behaves on real data.

---

## Re-running the pipeline against this bundle

If you want to walk through `data/trades.sqlite + data/annotations.csv`
→ `reports/pivot_latest.html` step by step:

> ⚠ **Heads-up**: the steps below copy demo files into `data/`,
> which is where the real pipeline writes too. If you've already
> configured TraderLens with your broker token and have real trades
> archived, **move your real data aside first**:
>
> - Windows: `move data data.real-backup`
> - macOS / Linux: `mv data data.real-backup`
>
> Restore later by moving it back.

From the project root:

```powershell
# 1. Set up venv + deps (only needed once)
python -m venv venv
venv\Scripts\Activate.ps1            # macOS / Linux: source venv/bin/activate
pip install -r requirements.txt

# 2. Stage the demo bundle into data/
mkdir data 2>$null                   # macOS / Linux: mkdir -p data
copy demo\trades.sqlite      data\trades.sqlite       # macOS / Linux: cp demo/trades.sqlite data/
copy demo\annotations.csv    data\annotations.csv     # macOS / Linux: cp demo/annotations.csv data/

# 3. Regenerate the CSV export and HTML pivot
venv\Scripts\python.exe -m src.exporter --date 2026-05-19
venv\Scripts\python.exe -m src.pivot

# 4. Open the result
start reports\pivot_latest.html      # macOS: open; Linux: xdg-open
```

The HTML in `reports/pivot_latest.html` should match the headline
[`demo.html`](../demo.html) modulo timestamps in the footer.
