<#
.SYNOPSIS
  Register the TraderLens daily auto-run as a Windows Scheduled Task.

.DESCRIPTION
  Auto-runs the Activity-Flex pipeline (`python -m src.ib_sync` via
  scripts/run_ib_sync.bat). This covers the VERIFIED Activity path (T+1: fetches
  up to "yesterday" ET — stable settled data; see Step 8). The same-day Trade
  Confirmation evening run is NOT included yet (blocked on spike-002 C2).

  ⚠ RATE-LIMIT SAFETY (ADR-002 — permanent IP-ban risk). This task is the
  scheduling layer, so its frequency matters:
    - Few triggers/day only (logon + one daily time). NOT polling.
    - NO auto-retry on failure (RestartCount 0) — a failed run waits for the
      next natural trigger. Auto-retry loops can hammer Flex -> ban.
    - MultipleInstances=IgnoreNew — never run two copies at once (avoids two
      Flex calls racing the 10-min gate).
    - The real safety net is the Python gate (state.last_flex_call_ts): even if
      a trigger fires "too soon", ib_sync skips the Flex call. Extra triggers
      are harmless & idempotent by design.

  Idempotent: re-running this script replaces the existing task.
  Does NOT execute the pipeline now — it only registers; first run is at the
  next logon / daily time.

.NOTES
  Run from an elevated-or-normal PowerShell (registers under the current user):
    powershell -ExecutionPolicy Bypass -File scripts\register_ib_sync_task.ps1
  Remove with:
    Unregister-ScheduledTask -TaskName 'TraderLens IB Sync' -Confirm:$false
#>

# --- self-elevate: registering a scheduled task needs an elevated shell ------
# (VS Code's integrated terminal is NOT elevated -> Register fails 0x80070005.)
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Not elevated -> relaunching as administrator (approve the UAC prompt)." -ForegroundColor Yellow
    Write-Host "A new elevated PowerShell window will open, do the work, and stay open." -ForegroundColor Yellow
    try {
        Start-Process powershell.exe -Verb RunAs -ArgumentList @(
            '-NoExit', '-ExecutionPolicy', 'Bypass', '-NoProfile',
            '-File', "`"$($MyInvocation.MyCommand.Path)`""
        ) -ErrorAction Stop
    } catch {
        Write-Host "[FAILED] Elevation cancelled/blocked: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "Fallback: open an admin PowerShell manually and re-run this script." -ForegroundColor Yellow
        exit 1
    }
    exit 0
}

# --- config -----------------------------------------------------------------
$TaskName  = 'TraderLens IB Sync'
# ONE task drives both Flex queries via `--mode auto`; the Python picks mode by
# real NY time + state (so DST drift is harmless): past NY close & today's
# Confirmation not yet captured -> Confirmation (same-day, primary feed);
# at the Activity slot (NY >=20) & Confirmation already in -> Activity (T+1
# backup/reconcile); otherwise -> skip (no Flex call).
#
# Five explicit daily fires (vs hourly-repeat across the full window). Each
# Beijing time hits a real slot or a real retry under at least one DST regime;
# the middle NY 18:00 / 19:00 dead zone is dropped (no useful work there in
# either DST), and 11:00 Beijing = NY 23:00 is dropped (post-3-retry over-poll
# — if Activity still fails by then, next-day T+1 backfill recovers naturally).
# 5 daily fires + on-logon; Python gate caps ACTUAL Flex calls at <=2/day
# (ADR-002 safe).
$TriggerTimes = @(
    '04:00',  # NY 16:00 EDT  Confirmation slot (post-close)
    '05:00',  # NY 17:00 EDT retry / NY 16:00 EST  Confirmation (DST winter)
    # 06:00 / 07:00 DROPPED -- NY 18:00 / 19:00 dead zone (no useful work in either DST)
    '08:00',  # NY 20:00 EDT  Activity slot (post T+1 availability ~20:10)
    '09:00',  # NY 21:00 EDT retry / NY 20:00 EST  Activity (DST winter)
    '10:00'   # NY 22:00 EDT retry / NY 21:00 EST retry
    # 11:00 DROPPED -- over-retry; deeper failure rolls into next-day T+1 backfill
)

# --- resolve paths from this script's location -------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root      = Split-Path -Parent $ScriptDir
$Bat       = Join-Path $ScriptDir 'run_ib_sync.bat'

if (-not (Test-Path $Bat)) {
    Write-Error "Not found: $Bat"
    exit 1
}
Write-Host "Project root : $Root"
Write-Host "Entry bat    : $Bat"
Write-Host "Daily fires  : $($TriggerTimes -join ', ') (local Beijing) + at logon  [--mode auto]"

# --- action: run the project entry bat in auto mode (handles venv + wifi delay)
$action = New-ScheduledTaskAction -Execute $Bat -Argument '--mode auto' -WorkingDirectory $Root

# --- triggers: at logon + N explicit daily times (no mid-zone wasted spawns) -
$tLogon = New-ScheduledTaskTrigger -AtLogOn
$tDailies = $TriggerTimes | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ }

# --- settings: run-if-missed, battery-OK, NO auto-retry, single instance -----
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -RestartCount 0

# --- principal: only when this user is logged on (keeps venv + user env) -----
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

# --- (re)register -------------------------------------------------------------
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Existing task found -> replacing." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger (@($tLogon) + $tDailies) `
        -Settings $settings `
        -Principal $principal `
        -Description 'TraderLens IBKR Flex sync (--mode auto): same-day Trade Confirmation (primary) + T+1 Activity (backup/reconcile). Rate-limit-safe: Python gate caps Flex calls at <=2/day; no auto-retry.' `
        -ErrorAction Stop | Out-Null
} catch {
    Write-Host "`n[FAILED] Could not register task: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Most likely cause: this needs an ELEVATED shell." -ForegroundColor Yellow
    Write-Host "Fix: right-click PowerShell -> 'Run as administrator', then run:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`""
    exit 1
}

# Verify it actually exists (Register can emit a non-terminating error and still
# look like it 'ran' — confirm before claiming success).
if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
    Write-Host "`n[FAILED] Task not found after registration (likely access denied)." -ForegroundColor Red
    Write-Host "Re-run in an ADMIN PowerShell (right-click -> Run as administrator)." -ForegroundColor Yellow
    exit 1
}

Write-Host "`nRegistered '$TaskName'." -ForegroundColor Green
Write-Host "Triggers: at logon + daily $($TriggerTimes -join ' / ') (local Beijing)  [--mode auto]."
Write-Host "First run: next logon or next trigger time — NOT now (no Flex call on registration)."
Write-Host "`nInspect : Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Host "Run once: Start-ScheduledTask -TaskName '$TaskName'   (respects the 10-min gate)"
Write-Host "Remove  : Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
