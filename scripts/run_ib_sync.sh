#!/usr/bin/env bash
# TraderLens project entry point — POSIX (macOS / Linux) mirror of
# run_ib_sync.bat.
#
# Runs the ib_sync main flow: fetch IBKR Flex -> SQLite archive -> CSV export.
#
# Exit code:
#   0 = OK / idle  (success, nothing to do, graceful backoff)
#   2 = RETRYABLE  (throttle / server-busy / network -- caller may retry later)
#   3 = HARD       (token / auth expired or unexpected -- caller must alert)
# See src/constants.py RC_* for the exit-code definitions.
#
# Args are forwarded to python, e.g.:
#   ./scripts/run_ib_sync.sh --mode auto          (scheduler: pick activity/confirmation)
#   ./scripts/run_ib_sync.sh --mode confirmation  (manual same-day pull)
#   ./scripts/run_ib_sync.sh --no-delay --mode confirmation  (debug: skip the boot wait)

set -e
cd "$(dirname "$0")/.."

# 30-second WiFi delay (FR-ENTRY-2): wait for network after boot / login.
# Skip with --no-delay (consumed here, not forwarded).
if [ "$1" = "--no-delay" ]; then
    shift
else
    sleep 30
fi

# Activate venv. macOS/Linux venv layout puts python under venv/bin/.
if [ -f "venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
else
    echo "ERROR: venv/bin/activate not found. Run try-demo first, or:"
    echo "    python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Logging goes to logs/ib_sync_YYYYMMDD.log (Python configures it) plus stdout.
python -m src.ib_sync "$@"
