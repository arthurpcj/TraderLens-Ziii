"""Zero-config constants for TraderLens v1 (P1).

Config is .env (token/query) + these constants. config.yaml is deferred to v2
(GSheet). See plan: .env + constants decision.
"""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

# Trade-day logic timezone. IBKR futures + the MTS ORB strategies are US/Eastern;
# NEVER use the laptop's local date (user is UTC+8 -> off by a calendar day).
# Requires the `tzdata` package on Windows (no built-in IANA db).
ET_TZ = ZoneInfo("America/New_York")

# --- Project paths (relative to repo root = parent of src/) ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SQLITE_PATH = DATA_DIR / "trades.sqlite"
STATE_PATH = DATA_DIR / "state.json"
EXPORT_DIR = DATA_DIR / "exports"
ENV_PATH = PROJECT_ROOT / ".env"
LOGS_DIR = PROJECT_ROOT / "logs"
CONFIG_DIR = PROJECT_ROOT / "config"

# --- FR-PIVOT annotation layer (P2) ---
# pivot_tags.json: setup-tag taxonomy + order_ref alias map (committed template,
# user edits display names in place — NOT secret, no trades). stdlib json, zero dep.
PIVOT_TAGS_PATH = CONFIG_DIR / "pivot_tags.json"
# annotations.csv: subjective layer keyed by entry-leg tradeID (gitignored under
# data/ — contains real trades). User fills setup_tag / score / notes in Excel.
ANNOTATIONS_PATH = DATA_DIR / "annotations.csv"
# R1 backup dir: each write_template run snapshots the previous annotations.csv
# here before overwriting. Keeps the last ANNOTATIONS_BAK_KEEP files (FIFO prune).
ANNOTATIONS_BAK_DIR = DATA_DIR / "annotations.bak"
ANNOTATIONS_BAK_KEEP = 20
UNTAGGED = "untagged"  # explicit tier-3 setup_tag (visible, not hidden)

# --- Trade filtering (EXPORT stage only; SQLite archives everything) ---
TARGET_UNDERLYINGS: tuple[str, ...] = ("NQ", "MNQ", "ES", "MES")

# --- State machine (INTERFACE_CONTRACT §5.6 v1.1 2026-05-26, C6-C10) ---
# MTS_RELEVANT_SETUPS: round-trips with these setup_tag codes (resolved via the
# three-tier resolver) trigger State B for their trade_date — csv content
# narrows to only these round-trips + category column flips to MTS_CONFIRMED.
# Currently a single code; extend the frozenset to broaden the State-B scope.
MTS_RELEVANT_SETUPS: frozenset[str] = frozenset({"Q_intraday"})

# --- IBKR Flex Web Service endpoints (from ibflex client.py) ---
FLEX_BASE_URL = "https://gdcdyn.interactivebrokers.com/Universal/servlet/"
FLEX_SEND_REQUEST_URL = FLEX_BASE_URL + "FlexStatementService.SendRequest"
FLEX_GET_STATEMENT_URL = FLEX_BASE_URL + "FlexStatementService.GetStatement"
FLEX_API_VERSION = "3"
# IBKR rejects requests without a recognizable user-agent.
FLEX_USER_AGENT = "Java"

# --- Rate-limit policy (ADR-002; permanent IP-ban risk) ---
MIN_INTERVAL_SEC = 600      # 10 min minimum between Flex calls for same query
PENALTY_BOX_SEC = 1800      # 30 min back-off after a 1018 throttle (no retry)
SERVER_BUSY_RETRY_MAX = 3   # 1009/1019 retries
SERVER_BUSY_INTERVAL_SEC = 30
HTTP_TIMEOUT_SEC = 30

# --- Gap alert (NFR-RELIABILITY-4) ---
GAP_THRESHOLD_DAYS = 7

# --- CSV interface (INTERFACE_CONTRACT §2.3, v1.0 frozen, 12 cols, ORDER LOCKED) ---
CSV_COLUMNS: tuple[str, ...] = (
    "trade_id",
    "trade_date",
    "trade_time",
    "underlying",
    "expiry",
    "buy_sell",
    "quantity",
    "trade_price",
    "ib_commission",
    "open_close",
    "category",
    "notes",
)

# csv `category` column (#11) — INTERFACE_CONTRACT §5.6 v1.1 2026-05-26 C7.
# State A (no annotation for the date): PAPER_AUTO — MTS routes 0-candidate to
#   DC default-skip (treats it as possible user manual trade).
# State B (≥1 round-trip for the date matches MTS_RELEVANT_SETUPS): MTS_CONFIRMED
#   — MTS routes 0-candidate to FORCE_WRITTEN with alert (real MTS trade missed
#   by smart matcher, recoverable). IB_Sync has confirmed the date is audited.
CSV_CATEGORY_PAPER_AUTO = "PAPER_AUTO"
CSV_CATEGORY_MTS_CONFIRMED = "MTS_CONFIRMED"
# Legacy alias for the few callers that imported the old constant; remove once
# downstream is migrated. Maps to State-A value (PAPER_AUTO) so existing tests
# without state machine still pass.
CSV_CATEGORY_FIXED = CSV_CATEGORY_PAPER_AUTO

# --- Re-export lookback (INTERFACE_CONTRACT §5.6 v1.1 2026-05-26, C8/C9) ---
# review-flow re-exports the last DEFAULT_EXPORT_LOOKBACK_DAYS trade_dates by
# default (override via --lookback N or --lookback all). Wrapper.bat on the
# user layer MUST loop the same N when calling MTS import (C9 contract).
DEFAULT_EXPORT_LOOKBACK_DAYS = 90

# --- Process exit codes (entry contract: run_ib_sync.bat -> MTS S3 stage) ---
# Maps to MTS P5 failure classification (SPEC_Paper_P2_Monitoring §3.0). The
# wrapper / MTS reads %ERRORLEVEL% to decide how to continue:
#   0 OK         success / nothing to do / graceful safe-mode backoff -> no action
#   2 RETRYABLE  transient (throttle 1018/429, server busy, network) -> retry next trigger
#   3 HARD       needs user/code fix (token expired/auth, unexpected error) -> halt + alert
# (1 is intentionally unused so a legacy "exit 1" is never mistaken for a class.)
RC_OK = 0
RC_RETRYABLE = 2
RC_HARD = 3
