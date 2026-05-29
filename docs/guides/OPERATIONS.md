# TraderLens (ib_sync) — User Operations Manual

> Day-to-day user guide: commands / interfaces / viewing results / logs / fixing problems / human-intervention points / examples.
> Scope: Priority 1 (Confirmation same-day + Activity T+1 → SQLite full archive → CSV export) + `--mode auto` auto-scheduling + Priority 2 local HTML pivot.
> Project root: wherever you cloned this repo (all commands below run from there).

> **Cross-platform note.** TraderLens supports Windows, macOS, and Linux. Where commands differ, this guide shows both. Substitution rule of thumb:
> - PowerShell `venv\Scripts\python.exe -m src.X` ≡ bash `venv/bin/python -m src.X`
> - PowerShell `venv\Scripts\python.exe` ≡ bash `venv/bin/python`
> - Path separator `\` ≡ `/` (Python accepts both internally, but the shell glob differs).

---

## 🚨 0. Rule #1: Flex rate limiting (read before doing anything)

The IBKR Flex Web Service enforces a **minimum interval of ≥ 10 minutes between calls of the same Query**. Violations get you throttled (error 1018); **repeat violations can permanently ban your IP / token**.

- ✅ Built-in **gate**: re-running within 10 minutes **automatically skips** the Flex call (safe).
- ❌ Do **not** manually re-run rapidly, and do **not** click "Run" on the Flex Query web page right before running the script (they share one rate-limit counter).
- ❌ On failure, do **not** loop-retry — just wait for the next scheduled trigger (the tool already backs off).
- 💡 To test parsing logic, use cached XML / pytest — **never hit live Flex**.

> See `memory/knowledge/flex_rate_limiting.md` (internal) / `docs/decisions/002-flex-rate-limit-policy.md`.

---

## 1. One-minute overview

```
Trade Confirmation (same-day, post-NY-close) ─┐
                                              ├─▶ SQLite full archive ─▶ CSV export (NQ/MNQ/ES/MES)
Activity (T+1, next-day reconcile/backup) ────┘    data/trades.sqlite      data/exports/*.csv

       Driven by a single scheduled task running `--mode auto`; Python picks the mode by NY time + state.
```

- **Fetch cadence (dual track)**:
  - **Confirmation same-day** (after NY 16:00 close): **primary** — captures today's fills today, then writes CSV.
  - **Activity T+1** (after NY 20:10): **backup / reconcile** — settled data for yesterday, catches anything Confirmation missed.
- **Full archive**: SQLite stores **all** trades (incl. stocks, non-target futures) for local analysis; **only** NQ/MNQ/ES/MES futures land in the CSV export.
- **Idempotent**: running N times/day or re-fetching 30 days is safe; de-duplicated by `trade_id`.

---

## 2. Prerequisites (`.env`)

`.env` in the project root (**never committed**), three keys:

```
IBKR_FLEX_TOKEN=<your Flex token>
IBKR_FLEX_QUERY_ID=<Activity Query ID>                          # T+1 backup/reconcile (in production)
IBKR_FLEX_QUERY_ID_CONFIRMATION=<Trade Confirmation Query ID>   # same-day primary (in production)
```

- Get token / query IDs from IBKR Client Portal → **Settings → Flex Web Service**.
- The token **expires**; on expiry, auth fails (exit code 3) and you must regenerate it in IBKR and update `.env` (see §7).

---

## 3. Automatic runs (register / unregister the scheduled task)

All three platforms share the same gate logic: `--mode auto` + Python's `_resolve_auto_mode` + the 10-minute Flex gate cap real Flex calls at **≤ 2 / day** regardless of trigger frequency. The OS-level scheduler can fire often; Python decides whether to actually hit Flex.

### 3.1 Windows — Task Scheduler

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_ib_sync_task.ps1
```

The script **self-elevates via UAC** — approve the prompt; a new elevated PowerShell window opens, registers the task, and stays open (`-NoExit`) so you can read the output.

After registering (times are **Beijing local**):
- Triggers: **at logon** + **5 daily times: 04:00 / 05:00 / 08:00 / 09:00 / 10:00**
  - 04/05 Beijing ≈ **NY 16:00** (Confirmation slot, one per DST regime)
  - 08/09/10 Beijing ≈ **NY 20:00–22:00** (Activity slot + retries)
  - Dropped: Beijing 06/07 (NY 18/19 dead zone) and 11 (over-retry)
- **Registration itself makes no Flex call**; the first real run is at the next logon or trigger time
- To change trigger times: edit the `$TriggerTimes` array in `scripts\register_ib_sync_task.ps1` and re-run (idempotent)

**Inspect / trigger / unregister:**

```powershell
# Inspect task (last result, next run time)
Get-ScheduledTask -TaskName 'TraderLens IB Sync' | Get-ScheduledTaskInfo

# List all triggers (should be 1 LogonTrigger + 5 DailyTriggers)
Get-ScheduledTask -TaskName 'TraderLens IB Sync' | Select-Object -ExpandProperty Triggers | Format-List

# Trigger once now (DOES hit Flex, but still honors the 10-min gate)
Start-ScheduledTask -TaskName 'TraderLens IB Sync'

# Unregister (stop auto-run)
Unregister-ScheduledTask -TaskName 'TraderLens IB Sync' -Confirm:$false
```

### 3.2 macOS — launchd agent

```bash
bash scripts/install-launchd-task.sh
```

Generates `~/Library/LaunchAgents/com.traderlens.ibsync.plist` and `launchctl load -w` it. Idempotent (re-running unloads any previous version first).

Trigger model is **timezone-agnostic**: `StartInterval` every 4 hours + `RunAtLoad` (fires after each login). Python's `--mode auto` decides whether each fire actually hits Flex — same `_resolve_auto_mode` + 10-minute gate as Windows. No Beijing/NY hardcoding.

**Inspect / trigger / unregister:**

```bash
# Confirm it's loaded
launchctl list | grep com.traderlens.ibsync

# Trigger once now (DOES hit Flex, honors the 10-min gate)
launchctl start com.traderlens.ibsync

# Unregister (stop auto-run) — also removes the plist
PLIST="$HOME/Library/LaunchAgents/com.traderlens.ibsync.plist"
launchctl unload -w "$PLIST" && rm "$PLIST"

# Logs (launchd captures stdout/stderr to project-local files;
# the Python application logger also writes logs/ib_sync_YYYYMMDD.log)
tail -f logs/launchd.out.log logs/launchd.err.log
```

### 3.3 Linux — cron (or systemd timer)

No installer ships for Linux because Linux scheduling is fragmented across distros. Pick whichever you prefer:

**cron**:
```bash
# Edit your user crontab
crontab -e
# Add (fires every 4 hours, like the macOS launchd plist):
0 */4 * * * /absolute/path/to/scripts/run_ib_sync.sh --no-delay --mode auto
```

**systemd timer** — write `~/.config/systemd/user/traderlens.{service,timer}`, then:
```bash
systemctl --user enable --now traderlens.timer
```

The wrapper script `scripts/run_ib_sync.sh` does the same work as the Windows / macOS entries; trigger it on whatever schedule you like.

> All three platforms: scheduler fires often, Python gate decides reality. **single instance** behaviour (no concurrent runs) is handled by Python's state file, not the scheduler.

---

## 4. Manual commands (the complete list)

> Commands below assume **venv activated** (`source venv/bin/activate` on macOS/Linux, `venv\Scripts\Activate.ps1` on Windows). `--status` / `--include-today` flags are **not implemented** (`--mode auto` covers the common cases; see §10).

| Purpose | Command |
|---|---|
| **Auto-pick mode and run once** (same as the scheduler) | `python -m src.ib_sync --mode auto` |
| **Force same-day Confirmation** (primary, captures same-day fills) | `python -m src.ib_sync --mode confirmation` |
| **Force Activity** backup/reconcile (T+1, default) | `python -m src.ib_sync` or `--mode activity` |
| Via the project-entry script (incl. 30s boot network wait) | Windows: `scripts\run_ib_sync.bat --mode auto`<br>macOS / Linux: `bash scripts/run_ib_sync.sh --mode auto`<br>(both accept `--no-delay` to skip the wait when debugging) |
| **Re-export one day's csv** (reads SQLite only, **no Flex call**, safe) | `python -m src.exporter --date 2026-05-19` |
| Use a custom .env path | `python -m src.ib_sync --env /path/to/.env` |

> `python -m src.ib_sync` honors the 10-minute gate: if less than 10 minutes since the last successful call, it skips Flex (log shows `skip Flex call: ...`).

---

## 5. Viewing results (where things are, how to read them)

Everything lives under `data\` (gitignored — contains real trades):

| Artifact | Path | Notes |
|---|---|---|
| **CSV export** | `data\exports\mts_trades_{YYYY-MM-DD}.csv` | 12 cols v1.0, one file per trade date, NQ/MNQ/ES/MES only |
| **Full archive** (local analysis) | `data\trades.sqlite` | incl. stocks + all futures |
| **Run state** | `data\state.json` | last run/success time, throttle state, last error |
| **Logs** | `logs\ib_sync_YYYYMMDD.log` | appended per run + RUN SUMMARY |

### 5.1 The csv

Open in Excel / a text editor. 12 columns: `trade_id, trade_date, trade_time, underlying, expiry, buy_sell, quantity, trade_price, ib_commission, open_close, category, notes`.
> `ib_commission` is the **IB-native signed value** (cost is negative, e.g. `-0.62`). `category` defaults to `PAPER_AUTO`.

### 5.2 The SQLite archive (no extra tools — use the venv python)

```powershell
# Last 20 trades
venv\Scripts\python.exe -c "import sqlite3;c=sqlite3.connect('data/trades.sqlite');[print(r) for r in c.execute('SELECT trade_date,trade_time,underlying,buy_sell,quantity,trade_price,open_close FROM trades ORDER BY trade_date DESC,trade_time LIMIT 20').fetchall()]"

# Count by underlying
venv\Scripts\python.exe -c "import sqlite3;c=sqlite3.connect('data/trades.sqlite');[print(r) for r in c.execute('SELECT underlying,COUNT(*) FROM trades GROUP BY underlying ORDER BY 2 DESC').fetchall()]"
```

### 5.3 Run state

```powershell
venv\Scripts\python.exe -c "import json;print(json.dumps(json.load(open('data/state.json')),indent=2))"
```
Key fields: `last_success_trade_date` (last trade day fetched OK), `last_flex_call_ts` (last Flex call, drives the 10-min gate), `throttled_until_ts` (>0 means currently backing off), `last_error`.

---

## 6. Logs + RUN SUMMARY

Every run prints a **RUN SUMMARY** at the end (also written to `logs\ib_sync_YYYYMMDD.log`):

```
===== RUN SUMMARY =====
result: OK (rc=0) | elapsed 1.1s
warnings: 0 | errors: 0
  (clean — no warnings or errors)
=======================
```

- `result`: `OK` (rc=0) / `RETRYABLE` (rc=2) / `HARD` (rc=3).
- `warnings/errors`: listed one per line, for quick triage.

View today's full log:
```powershell
# Windows PowerShell:
Get-Content logs\ib_sync_(Get-Date -Format yyyyMMdd).log -Tail 40
# macOS / Linux:
#   tail -n 40 "logs/ib_sync_$(date +%Y%m%d).log"
```

---

## 7. Exit codes + how to fix problems

Process exit code (applies to both the scheduled task and the command line):

| Exit code | Meaning | What to do |
|---|---|---|
| **0** | OK / nothing to do (success / no new data / gate skip / safe-mode backoff) | nothing |
| **2** | RETRYABLE transient failure (throttle 1018/429, server busy, network) | **do nothing** — the tool backed off; the next scheduled trigger retries. **Do not hammer-retry manually** |
| **3** | HARD error (token/auth expired, unexpected error) | **needs you**: usually an expired token → see below |

### 7.1 Token expired (exit code 3, log shows `auth error` / `1012/1015`)

1. Regenerate the token in IBKR Client Portal → Settings → Flex Web Service.
2. Update `IBKR_FLEX_TOKEN` in `.env`.
3. Run once to verify: `venv\Scripts\python.exe -m src.ib_sync` (confirm ≥10 min since the last Flex call).

### 7.2 Currently backing off (`state.json` `throttled_until_ts` > now)

- Leave it. It recovers automatically at the next trigger after the window. **Do not** manually re-run to "test" (that extends the ban).

### 7.3 Gap alert (log `gap=N days`, N>7)

- Means no successful fetch for over 7 days. Check: is the scheduled task running (§3.2), is the token expired, was the machine off for a long time.
- After fixing, run `python -m src.ib_sync` once — it auto-backfills the last 30-day window.

### 7.4 A day's csv is missing / you want to re-export

- Data is in SQLite but the csv is missing → re-export directly (**no Flex call**): `venv\Scripts\python.exe -m src.exporter --date 2026-05-19`
- No NQ/MNQ/ES/MES futures that day → no csv is generated (normal).

### 7.5 state.json corrupt

- The tool enters **safe mode** automatically (backs off 30 minutes, then recovers); no manual action needed.
- As a last resort to reset: delete `data\state.json`; the next run treats it as a "first run" and backfills the last 30 days (note: the first run also honors the 10-min gate).

---

## 8. Human-intervention points (near-zero in normal operation)

| Scenario | Human needed? | Action |
|---|---|---|
| Normal daily fetch | ❌ fully automatic | occasionally glance at RUN SUMMARY |
| Exit code 2 (throttle/network) | ❌ | auto-retries, ignore |
| Exit code 3 (token expired) | ✅ | renew token + update `.env` (§7.1) |
| Gap alert > 7 days | ✅ | investigate + run a backfill (§7.3) |
| Change trigger times | ✅ (one-off) | edit the `$TriggerTimes` array, re-register (§3.1) |
| Stop auto-run | ✅ | unregister (§3.2) |

---

## 9. Usage examples (typical scenarios)

**Scenario A — first-time activation**

Windows (PowerShell):
```powershell
# 1. Make sure .env has token + query id
# 2. Register the scheduled task
powershell -ExecutionPolicy Bypass -File scripts\register_ib_sync_task.ps1
# 3. Verify the whole chain now (confirm >=10 min since last Flex)
Start-ScheduledTask -TaskName 'TraderLens IB Sync'
# 4. Check the result
Get-Content logs\ib_sync_(Get-Date -Format yyyyMMdd).log -Tail 40
```

macOS (bash):
```bash
# 1. Make sure .env has token + query id
# 2. Install the launchd agent
bash scripts/install-launchd-task.sh
# 3. Verify the whole chain now (confirm >=10 min since last Flex)
launchctl start com.traderlens.ibsync
# 4. Check the result
tail -n 40 "logs/ib_sync_$(date +%Y%m%d).log"
```

Linux (bash + cron): see §3.3 — add a cron entry, then trigger the wrapper directly to verify:
```bash
bash scripts/run_ib_sync.sh --no-delay --mode auto
tail -n 40 "logs/ib_sync_$(date +%Y%m%d).log"
```

**Scenario B — the daily norm**: do nothing. To confirm:
- Windows: `Get-ScheduledTask -TaskName 'TraderLens IB Sync' | Get-ScheduledTaskInfo` (check `LastTaskResult` = 0)
- macOS: `launchctl list | grep com.traderlens.ibsync` (third column = last exit code, 0 = OK)

**Scenario C — yesterday's CSV is missing**

```bash
# Check whether SQLite has target futures for that day (POSIX; Windows: substitute venv\Scripts\python.exe)
python -c "import sqlite3;c=sqlite3.connect('data/trades.sqlite');print(c.execute(\"SELECT COUNT(*) FROM trades WHERE trade_date='2026-05-19' AND underlying IN ('NQ','MNQ','ES','MES')\").fetchone())"
# If yes -> re-export (no Flex call)
python -m src.exporter --date 2026-05-19
```

**Scenario D — token expired**: see §7.1.

---

## 10. Not yet available / planned (don't misuse)

- ❌ **GSheet labeling / sync**: deferred to v2 (replaced by local CSV annotation layer + HTML pivot — see §11).
- ❌ `--status` / `--include-today` CLI flags: not implemented (`--mode auto` covers the common cases).
- ❌ **Pivot HTML auto-refresh**: scheduler doesn't regenerate the HTML after capture; run `python -m src.pivot` manually (automation pending in backlog).

---

## 11. Local pivot report (Priority 2)

After daily trades land, generate a **self-contained** offline HTML pivot (SQLite-only, no Flex):

```bash
# All commands assume venv activated. Substitute venv\Scripts\python.exe on Windows.

# Generate / refresh reports/pivot_latest.html
python -m src.pivot

# Pre-generate / refresh the annotation template (fill setup_tag / score / notes in your csv editor, then re-run pivot)
python -m src.pivot --tag-template

# Glued one-shot review loop (refresh template -> open csv editor -> wait Enter -> rebuild html -> open browser)
python -m src.pivot --review-flow
#   or use the project-entry script:
#     Windows:        scripts\review.bat
#     macOS / Linux:  bash scripts/review.sh
```

- Output: `reports\pivot_latest.html` (gitignored — contains real trades, ~400KB self-contained)
- Five views: KPI headline / calendar heatmap with click-drilldown / equity curve + DD band / drag-pivot (PivotTable.js) / sortable + filterable trade detail
- Annotation layer: `data\annotations.csv` (fill setup_tag / score / notes in Excel; key = entry-leg tradeID, stable across re-fetch + re-pairing)
- Colors: **neutral / cross-cultural** (blue = profit / amber = loss + ▲▼), no red/green
- Linked filter (sticky top bar): date presets / from-to / ← → / calendar month-click + drag-select — any change refreshes KPI / equity / by-setup / detail in lockstep
- The scheduler **does NOT auto-refresh the HTML** — re-run `python -m src.pivot` when you want fresh numbers

### 11.1 Review-flow (one-shot annotation loop)

`--review-flow` (or `scripts\review.bat`) is a wrapper that chains the manual steps into one **4-step flow**:

1. Refresh `data\annotations.csv` (preserves your existing tags, appends new round-trips). Also: snapshots the previous file to `data\annotations.bak\{timestamp}.csv` (R1, last 20 kept).
2. Hand the csv to Excel (`os.startfile`, opens in your default csv handler).
3. **Wait for you to press Enter** in the terminal — fill in Excel, **Ctrl+S** to save, then return to the terminal and hit Enter.
4. **Re-export `data\exports\mts_trades_{date}.csv` for the last 90 trade_dates** (re-applies the per-date state machine to recompute CSV contents from the latest annotations). Then rebuild `reports\pivot_latest.html` and auto-open in browser.

Override the lookback window:
```bash
python -m src.pivot --review-flow --lookback 180   # 180 days
python -m src.pivot --review-flow --lookback all   # full history
```

Notes:
- Your csv editor (Excel / Numbers / LibreOffice Calc) only needs **Ctrl+S** (Cmd+S on macOS) to save; you don't have to close it before pressing Enter. The pivot reader doesn't require an exclusive lock.
- **Don't run `--review-flow` twice while the csv editor still has the file open** — the second template refresh would fail to write. The script catches this and prints a clear message (`[FAIL] ... is locked`); close the editor and re-run.
- **Ctrl+C** during the Enter wait aborts cleanly (rc=130). Your annotations stay saved as-is, no regen happens.
- If you only want to **look at** the report (no annotation changes), skip `--review-flow` — just run `python -m src.pivot`.
- **macOS heads-up**: `os.startfile` (Windows) becomes `open <csv>` on macOS, which uses your default `.csv` handler. If that's Numbers, beware Numbers may silently reformat numeric strings on save. To force a different handler, set it via Finder → Get Info → Open with → Change All. LibreOffice Calc and Excel for Mac don't have this issue.

---

## Related docs
- Rate-limit policy (decision): `docs/decisions/002-flex-rate-limit-policy.md`
- License & dual-licensing rationale: `docs/decisions/003-license-agpl-3.0.md`
- Full documentation index: `docs/INDEX.md`

*Last updated: 2026-05-26 (state machine A/B + category dual + R1 backup + --lookback flag + 4-step review-flow)*
