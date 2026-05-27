"""
TraderLens Spike 001 — IBKR Flex Query Connectivity Verification.

Goal: Confirm end-to-end pipeline (Token + Query ID → ibflex download → parse →
field presence) works before implementing src/ib_sync.py.

Usage:
    pip install ibflex
    # Fill IBKR_FLEX_TOKEN and IBKR_FLEX_QUERY_ID in <project_root>/.env
    python docs/studies/001_flex_connectivity_spike_20260520/spike.py

Exit codes:
    0 = full success
    1 = missing dependency / config
    2 = download failed
    3 = parse failed
    4 = field coverage incomplete (warning, not blocking)
"""
from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

# Force UTF-8 stdout on Windows (default GBK chokes on ✓/✗/○ etc).
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


# ---- 1. Locate project root + load .env (no python-dotenv dependency) ----

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent  # docs/studies/NNN_*/ → project root


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


env = load_env_file(PROJECT_ROOT / ".env")
TOKEN = env.get("IBKR_FLEX_TOKEN") or os.environ.get("IBKR_FLEX_TOKEN")
QUERY_ID = env.get("IBKR_FLEX_QUERY_ID") or os.environ.get("IBKR_FLEX_QUERY_ID")

# ---- 2. Banner ----

print("=" * 60)
print("TraderLens Spike 001: IBKR Flex Query Connectivity")
print("=" * 60)
print(f"Time       : {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
print(f"Project    : {PROJECT_ROOT}")
print(f"Token      : {'***' + TOKEN[-4:] if TOKEN else '(missing)'}")
print(f"Query ID   : {QUERY_ID or '(missing)'}")
print()

if not TOKEN or not QUERY_ID:
    print("FAIL: IBKR_FLEX_TOKEN and IBKR_FLEX_QUERY_ID must be set.")
    print(f"  Edit: {PROJECT_ROOT / '.env'}")
    print("  Template: see .env.example")
    sys.exit(1)

# ---- 3. Import ibflex ----

try:
    from ibflex import client, parser
except ImportError:
    print("FAIL: ibflex not installed.")
    print("  Run: pip install ibflex")
    sys.exit(1)

# ---- 4. Download (Step 1+2 internally: SendRequest → poll → GetStatement) ----

print("[1/3] Downloading Flex statement (auto two-step + retry, may take 10-30s)...")
t0 = time.time()
try:
    response = client.download(TOKEN, QUERY_ID)
except Exception as exc:
    elapsed = time.time() - t0
    print(f"  FAIL after {elapsed:.1f}s: {type(exc).__name__}: {exc}")
    print("  Hint: check token expiry, query ID, network. ibflex retries internally.")
    sys.exit(2)
elapsed = time.time() - t0
print(f"  OK : {len(response):,} bytes in {elapsed:.1f}s")

raw_xml_path = SCRIPT_DIR / f"raw_response_{time.strftime('%Y%m%d_%H%M%S')}.xml"
raw_xml_path.write_bytes(response)
print(f"  Saved raw XML → {raw_xml_path.name} (gitignored)")
print()

# ---- 5. Parse ----
#
# Primary path: ibflex.parser (rich types: Enum, Decimal, date)
# Fallback   : xml.etree (raw strings) — used when ibflex chokes on
#              edge cases like SymbolSummary.reportDate="MULTI" (known
#              ibflex bug, not our data fault).

import xml.etree.ElementTree as ET

def parse_via_xml(xml_bytes: bytes) -> list[dict]:
    """Direct XML parse, returns Trade elements as list[dict[str, str]]."""
    root = ET.fromstring(xml_bytes)
    return [dict(trade.attrib) for trade in root.iter("Trade")]

print("[2/3] Parsing XML...")
parse_path = None
all_trades: list = []
flex_meta = []

try:
    statement = parser.parse(response)
    parse_path = "ibflex"
    print(f"  OK (ibflex): {len(statement.FlexStatements)} FlexStatement(s)")
    for fs in statement.FlexStatements:
        n = len(fs.Trades) if fs.Trades else 0
        print(f"    Account={fs.accountId}  period={fs.fromDate}~{fs.toDate}  Trades={n}")
        flex_meta.append((str(fs.accountId), str(fs.fromDate), str(fs.toDate)))
        if fs.Trades:
            all_trades.extend(fs.Trades)
except Exception as exc:
    print(f"  ibflex FAILED: {type(exc).__name__}: {exc}")
    print(f"  → Falling back to direct XML parse (xml.etree, raw strings)")
    try:
        all_trades = parse_via_xml(response)
        parse_path = "xml.etree"
        root = ET.fromstring(response)
        for fs in root.iter("FlexStatement"):
            acc = fs.get("accountId", "?")
            fr = fs.get("fromDate", "?")
            to = fs.get("toDate", "?")
            flex_meta.append((acc, fr, to))
            print(f"    Account={acc}  period={fr}~{to}")
        print(f"  OK (xml.etree): {len(all_trades)} Trade elements")
    except Exception as exc2:
        print(f"  xml.etree also FAILED: {type(exc2).__name__}: {exc2}")
        sys.exit(3)
print()

# ---- 6. Field coverage check ----

print("[3/3] Field coverage check (per REQUIREMENTS FR-FETCH-2 + spike extras)...")

# Spike findings 2026-05-20:
#   - assetCategory NOT present in Flex XML; filter by underlyingSymbol instead
#   - expiry is YYYYMMDD (not YYYYMM as REQUIREMENTS assumed)
REQUIRED_FIELDS = [
    # FR-FETCH-2 (REQUIREMENTS v1.0 — to be corrected post-spike)
    "tradeID", "symbol", "underlyingSymbol",
    "tradeDate",  # "tradeTime" replaced by "dateTime" in actual XML
    "dateTime", "quantity", "tradePrice",
    "ibCommission", "multiplier", "fifoPnlRealized", "buySell",
    "expiry", "openCloseIndicator",
    # Spike-extra
    "orderReference", "orderType", "conid", "exchange",
    "tradeMoney", "netCash",
]

print(f"  Parse path           : {parse_path}")
print(f"  Total Trade elements : {len(all_trades)}")

if not all_trades:
    print("  WARN: No trades found. Field check skipped.")
    sys.exit(0)

def get_attr(t, name):
    """Unified accessor: works for ibflex objects (attr) and dicts (key)."""
    if isinstance(t, dict):
        return t.get(name)
    return getattr(t, name, None)

sample = all_trades[0]
sample_id = get_attr(sample, "tradeID")
print(f"\n  Sample = first trade (tradeID={sample_id})")
print("  " + "-" * 55)
missing = []
for field in REQUIRED_FIELDS:
    val = get_attr(sample, field)
    if val is None:
        # in xml.etree path, missing == None; check if KEY exists
        if isinstance(sample, dict) and field not in sample:
            print(f"    [X]  {field:25s} (attribute missing in XML)")
            missing.append(field)
            continue
    marker = "[+]" if val not in (None, "") else "[ ]"
    val_repr = repr(val)[:40]
    print(f"    {marker}  {field:25s} = {val_repr}")
    if val in (None, "") and field not in {"fifoPnlRealized", "orderReference", "notes", "expiry"}:
        # NULL-OK fields:
        #   fifoPnlRealized — open legs == 0/empty, close legs filled
        #   orderReference  — empty if no strategy tag
        #   notes           — user-optional
        #   expiry          — empty for stocks (we filter futures only later)
        missing.append(f"{field}=None")

# ---- 7. Asset / underlying breakdown ----
# Note: assetCategory NOT in XML. Filter by underlyingSymbol whitelist.

target_underlyings = {"NQ", "MNQ", "ES", "MES"}
all_underlyings = sorted({(get_attr(t, "underlyingSymbol") or "?") for t in all_trades})

# Futures heuristic: expiry attr non-empty (stocks have empty expiry)
fut = [t for t in all_trades if (get_attr(t, "expiry") or "")]
target = [t for t in fut if get_attr(t, "underlyingSymbol") in target_underlyings]
stocks = [t for t in all_trades if not (get_attr(t, "expiry") or "")]

print(f"\n  Asset breakdown:")
print(f"    All trades       : {len(all_trades)}")
print(f"    Futures (expiry!='')   : {len(fut)}")
print(f"    Stocks (expiry=='')    : {len(stocks)}")
print(f"    NQ/MNQ/ES/MES target   : {len(target)}")
print(f"  All underlyings seen   : {all_underlyings}")

other_fut_underlyings = sorted({get_attr(t, "underlyingSymbol") for t in fut if get_attr(t, "underlyingSymbol") not in target_underlyings})
if other_fut_underlyings:
    print(f"    Other futures (will be filtered) : {other_fut_underlyings}")

# ---- 8. Print first 3 target trades ----

if target:
    print(f"\n  First {min(3, len(target))} target trades:")
    print("  " + "-" * 55)
    for t in target[:3]:
        bs = get_attr(t, "buySell")
        if hasattr(bs, "name"):
            bs = bs.name
        oc = get_attr(t, "openCloseIndicator")
        if hasattr(oc, "name"):
            oc = oc.name
        order_ref = str(get_attr(t, "orderReference") or "").strip() or "(empty)"
        print(
            f"    {get_attr(t, 'tradeID')}  {get_attr(t, 'tradeDate')} {get_attr(t, 'dateTime')}  "
            f"{get_attr(t, 'underlyingSymbol')}{get_attr(t, 'expiry')}  "
            f"{bs} {get_attr(t, 'quantity')}@{get_attr(t, 'tradePrice')}  "
            f"comm={get_attr(t, 'ibCommission')}  pnl={get_attr(t, 'fifoPnlRealized')}  "
            f"oc={oc}  ref={order_ref}"
        )

# ---- 9. Final verdict ----

print("\n" + "=" * 60)
if missing:
    print(f"PARTIAL ✓: pipeline works but {len(missing)} field(s) need attention:")
    for f in missing:
        print(f"  - {f}")
    print("  → fix Flex Query field selection or update REQUIREMENTS")
    sys.exit(4)
else:
    print("ALL CHECKS PASSED ✓")
    print(f"  - download OK ({len(response):,} bytes, {elapsed:.1f}s)")
    print(f"  - parse OK ({len(all_trades)} trades)")
    print(f"  - {len(target)} target trades (NQ/MNQ/ES/MES) with full field coverage")
    print("\n  Next: write RESULTS.md, then proceed to src/ib_sync.py implementation.")
    sys.exit(0)
