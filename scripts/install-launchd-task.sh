#!/usr/bin/env bash
# Install a launchd agent for macOS scheduling —
# equivalent of register_ib_sync_task.ps1 (Windows Task Scheduler).
#
# Strategy: StartInterval every 4 hours + RunAtLoad. Python's
# _resolve_auto_mode and the 10-min Flex gate decide whether each fire
# actually hits Flex (real Flex calls remain <=2/day regardless of
# trigger frequency). This is timezone-agnostic — no need to hardcode
# Beijing / NY times like the Windows version.
#
# Idempotent: re-running unloads any previous version first.

set -e
cd "$(dirname "$0")/.."

LABEL="com.traderlens.ibsync"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST="$PLIST_DIR/${LABEL}.plist"
PROJECT_DIR="$(pwd -P)"

# macOS-only sanity check (works on Linux too, just warns).
if [ "$(uname)" != "Darwin" ]; then
    echo "WARNING: launchd is macOS-only. On Linux, use cron or systemd."
    echo "  cron example (every 4 hours):"
    echo "    0 */4 * * * ${PROJECT_DIR}/scripts/run_ib_sync.sh --no-delay --mode auto"
    exit 1
fi

# Verify the wrapper script exists and is executable.
if [ ! -x "scripts/run_ib_sync.sh" ]; then
    echo "ERROR: scripts/run_ib_sync.sh missing or not executable."
    echo "  Fix:  chmod +x scripts/run_ib_sync.sh"
    exit 1
fi

mkdir -p "$PLIST_DIR"
mkdir -p "logs"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
                       "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>

  <!-- Fire every 4 hours (14400 s). Python's _resolve_auto_mode +
       the 10-min Flex gate gate the actual call rate to <=2/day. -->
  <key>StartInterval</key><integer>14400</integer>

  <!-- Also fire shortly after the user logs in. -->
  <key>RunAtLoad</key><true/>

  <key>WorkingDirectory</key><string>${PROJECT_DIR}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PROJECT_DIR}/scripts/run_ib_sync.sh</string>
    <string>--no-delay</string>
    <string>--mode</string>
    <string>auto</string>
  </array>

  <!-- Capture stdout/stderr to project-local log files; Python ALSO logs
       to logs/ib_sync_YYYYMMDD.log via the application logger. -->
  <key>StandardOutPath</key><string>${PROJECT_DIR}/logs/launchd.out.log</string>
  <key>StandardErrorPath</key><string>${PROJECT_DIR}/logs/launchd.err.log</string>

  <!-- Re-launch on crash, but bounded (don't hammer Flex on bug loops). -->
  <key>KeepAlive</key><false/>
  <key>ThrottleInterval</key><integer>60</integer>
</dict>
</plist>
EOF

# Unload any existing version (ignore failure on first install).
launchctl unload -w "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"

echo "Installed launchd task '${LABEL}'."
echo "  Plist:    $PLIST"
echo "  Inspect:  launchctl list | grep ${LABEL}"
echo "  Logs:     logs/launchd.{out,err}.log + logs/ib_sync_*.log"
echo "  Remove:   launchctl unload -w '$PLIST' && rm '$PLIST'"
echo
echo "First run will fire shortly (RunAtLoad). After that, every 4 hours."
