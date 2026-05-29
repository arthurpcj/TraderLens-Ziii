#!/usr/bin/env bash
# TraderLens review-flow entry point — POSIX (macOS / Linux) mirror of
# review.bat.
#
# Runs the one-shot annotation flow:
#   1. Refresh data/annotations.csv template (preserves existing tags)
#   2. Open it in your default csv handler (Numbers / Excel for Mac /
#      LibreOffice Calc / etc.) so you can fill setup_tag / score / notes
#   3. Wait for you to press Enter in this terminal after you Ctrl+S in
#      the editor
#   4. Re-export data/exports/mts_trades_{date}.csv for the last 90
#      trade_dates and regenerate reports/pivot_latest.html, then auto-open
#      it in your default browser
#
# Args are forwarded to python -m src.pivot --review-flow, e.g.:
#   ./scripts/review.sh --lookback 180     (180-day re-export window)
#   ./scripts/review.sh --lookback all     (full history)

set -e
cd "$(dirname "$0")/.."

if [ -f "venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
else
    echo "ERROR: venv/bin/activate not found. Set up the venv first:"
    echo "    python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

python -m src.pivot --review-flow "$@"
