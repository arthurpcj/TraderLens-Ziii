"""Spike 002 — verify the Trade Confirmation Flex Query (NQ/MNQ same-day capture).

Goal: decide if we can add a Trade Confirmation query alongside the Activity
query for same-day fetch + analysis, with Activity as next-day verification.

Acceptance criteria (see chat / MEMORY):
  C1  two-step envelope works -> FlexQueryResponse
  C2  🔑 Confirmation tradeID matches the EXISTING SQLite tradeIDs (from the
      Activity-fed Step-8 run) on overlapping dates  -> enables single-table merge
  C3  today's (ET) NQ/MNQ trades are present shortly after close
  C4  required fields present: price/qty/buySell/openClose/commission(sign)/
      expiry/underlying/orderReference/notes

Rate-limit safety: ONE Flex call. Re-checks the 10-min gate, and on success
ARMS the global gate (state.last_flex_call_ts) so a following auto-run won't
double-call the shared-IP penalty box.

Run:  venv\\Scripts\\python.exe docs\\studies\\002_trade_confirmation_spike_20260521\\spike_confirmation.py
"""

from __future__ import annotations

import sqlite3
import sys

# Windows consoles default to GBK; force UTF-8 so emoji/CJK in output don't crash.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:
    pass
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# --- make `src` importable when run as a script ---
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src import flex_client                     # noqa: E402
from src import state as state_mod              # noqa: E402
from src.constants import ET_TZ, SQLITE_PATH, STATE_PATH, TARGET_UNDERLYINGS  # noqa: E402
from src.ib_sync import load_env_file           # noqa: E402

ENV = ROOT / ".env"
OUT_DIR = Path(__file__).resolve().parent
# Use the real export target set (NQ/MNQ/ES/MES), not just NQ/MNQ — otherwise
# MES/ES same-day trades are wrongly reported as "no target trades".
TARGET = TARGET_UNDERLYINGS


def banner(s: str) -> None:
    print(f"\n{'='*60}\n{s}\n{'='*60}")


def main() -> int:
    env = load_env_file(ENV)
    token = env.get("IBKR_FLEX_TOKEN")
    conf_q = env.get("IBKR_FLEX_QUERY_ID_CONFIRMATION")
    if not token or not conf_q:
        print("MISSING token or IBKR_FLEX_QUERY_ID_CONFIRMATION in .env")
        return 2

    # --- gate re-check (defensive) ---
    state = state_mod.load_state(STATE_PATH)
    now = time.time()
    reason = state_mod.gate_flex_call(state, now)
    if reason is not None:
        print(f"GATE BLOCKED: {reason} — aborting (no Flex call).")
        return 3

    banner("C1 — download Trade Confirmation statement (two-step envelope)")
    t0 = time.time()
    try:
        xml_bytes = flex_client.download_statement(token, conf_q)
    except Exception as exc:  # noqa: BLE001 — spike: show whatever broke
        print(f"[FAIL] download raised {type(exc).__name__}: {exc}")
        return 1
    dt = time.time() - t0

    # ARM the shared-IP gate immediately on success.
    state_mod.mark_flex_call_success(state, time.time())
    state_mod.save_state(state, STATE_PATH)

    raw_path = OUT_DIR / f"raw_confirmation_{datetime.now():%Y%m%d_%H%M%S}.xml"
    raw_path.write_bytes(xml_bytes)
    print(f"[OK] {len(xml_bytes)} bytes in {dt:.1f}s -> {raw_path.name} (gate armed)")

    root = ET.fromstring(xml_bytes)
    print(f"root tag   : {root.tag}")
    fq = root.find(".//FlexStatements/FlexStatement")
    if fq is not None:
        print(f"queryName  : {root.get('queryName')}  type={root.get('type')}")
        print(f"period     : {fq.get('period')}  from={fq.get('fromDate')} to={fq.get('toDate')}")
        print(f"whenGen    : {fq.get('whenGenerated')}")
    c1_ok = root.tag == "FlexQueryResponse"
    print(f"C1: {'PASS' if c1_ok else 'FAIL'}")

    # --- collect rows. Confirmation uses <TradeConfirm> (not Activity's <Trade>). ---
    confirms = list(root.iter("TradeConfirm"))
    trades = confirms or list(root.iter("Trade"))
    row_tag = "TradeConfirm" if confirms else "Trade"
    levels = Counter(t.get("levelOfDetail") for t in trades)
    print(f"\n<{row_tag}> elements: {len(trades)}  levelOfDetail dist: {dict(levels)}")

    # Prefer EXECUTION rows; fall back to whatever level exists.
    exec_rows = [t for t in trades if t.get("levelOfDetail") in (None, "EXECUTION")]
    if not exec_rows:
        exec_rows = trades
        print("(no EXECUTION-level rows; using all Trade rows)")

    conf_ids = {t.get("tradeID") for t in exec_rows if t.get("tradeID")}
    n_empty_id = sum(1 for t in exec_rows if not t.get("tradeID"))

    banner("C2 🔑 — tradeID consistency vs existing SQLite (Activity-fed)")
    print(f"Confirmation EXECUTION rows: {len(exec_rows)}  (empty tradeID: {n_empty_id})")
    print(f"distinct Confirmation tradeIDs: {len(conf_ids)}")
    sql_ids: set[str] = set()
    sql_dates: dict[str, str] = {}
    if Path(SQLITE_PATH).exists():
        conn = sqlite3.connect(SQLITE_PATH)
        for tid, td in conn.execute("SELECT trade_id, trade_date FROM trades"):
            sql_ids.add(str(tid))
            sql_dates[str(tid)] = td
        conn.close()
    print(f"SQLite trades (Activity): {len(sql_ids)} ids")

    overlap = conf_ids & sql_ids
    conf_only = conf_ids - sql_ids
    print(f"  matched (in both)     : {len(overlap)}")
    print(f"  confirmation-only      : {len(conf_only)} (newer than last Activity pull -> expected)")
    if overlap:
        print(f"  sample matched ids     : {sorted(overlap)[:5]}")
    # The decisive check: among ids on dates the Activity pull covered, do they match?
    sql_max_date = max(sql_dates.values()) if sql_dates else None
    print(f"  Activity max trade_date: {sql_max_date}")
    c2_ok = len(overlap) > 0
    print(f"C2: {'PASS (tradeID is consistent across query types)' if c2_ok else 'INCONCLUSIVE — see below'}")
    if not c2_ok:
        print("  -> 0 overlap. Either no overlapping dates, or tradeID differs.")
        print("     Compare formats manually before concluding.")

    banner("C3 — today's (ET) NQ/MNQ trades present?")
    today_et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).date()
    print(f"today (ET): {today_et}")
    dates = Counter(t.get("tradeDate") for t in exec_rows)
    print(f"tradeDate dist: {dict(sorted(dates.items()))}")
    nqmnq = [t for t in exec_rows if t.get("underlyingSymbol") in TARGET]
    today_str = today_et.strftime("%Y%m%d")
    today_nqmnq = [t for t in nqmnq if t.get("tradeDate") == today_str]
    print(f"NQ/MNQ rows total: {len(nqmnq)}   of which dated today({today_str}): {len(today_nqmnq)}")
    print(f"C3: {'PASS (today NQ/MNQ present)' if today_nqmnq else 'NO today NQ/MNQ (maybe no trades today, or not yet posted)'}")

    banner("C4 — field inventory (sample target rows)")
    # TCF (Confirmation) attribute names DIFFER from Activity (AF): price (not
    # tradePrice), commission (not ibCommission), code O/C (not
    # openCloseIndicator), and no fifoPnlRealized. (Verified spike-002.)
    fields = ["tradeID", "tradeDate", "dateTime", "underlyingSymbol",
              "expiry", "buySell", "quantity", "price", "commission",
              "code", "orderReference", "multiplier", "assetCategory",
              "execID", "orderID", "transactionType"]
    sample = (today_nqmnq or nqmnq or exec_rows)[:3]
    for i, t in enumerate(sample, 1):
        print(f"\n-- sample {i} --")
        for f in fields:
            v = t.get(f)
            print(f"  {f:20s} = {v!r}")
    # commission sign check (TCF uses `commission`, not `ibCommission`)
    comms = [float(t.get("commission")) for t in nqmnq
             if t.get("commission") not in (None, "")]
    if comms:
        print(f"\nibCommission: n={len(comms)} min={min(comms)} max={max(comms)} "
              f"(expect negative=cost, same as Activity)")
    # orderReference population
    orefs = [t.get("orderReference") for t in nqmnq]
    n_oref = sum(1 for o in orefs if o)
    print(f"orderReference populated: {n_oref}/{len(orefs)} NQ/MNQ rows")

    banner("VERDICT")
    print(f"C1 envelope     : {'PASS' if c1_ok else 'FAIL'}")
    print(f"C2 tradeID match: {'PASS' if c2_ok else 'INCONCLUSIVE'}")
    print(f"C3 today NQ/MNQ : {'PASS' if today_nqmnq else 'N/A (no today trades?)'}")
    print(f"C4 fields       : inspect samples above")
    return 0 if c1_ok else 1


if __name__ == "__main__":
    sys.exit(main())
