@echo off
REM TraderLens project entry point (FR-ENTRY).
REM Runs the ib_sync main flow ONLY -- does not call any other project.
REM Cross-project scheduling is the user-level wrapper.bat's job (INTERFACE_CONTRACT 9).
REM Exit code (propagated as %ERRORLEVEL%) maps to MTS P5 failure classes:
REM   0 = OK/idle (success, nothing to do, graceful backoff)
REM   2 = RETRYABLE (throttle/server-busy/network -> wrapper retries next trigger)
REM   3 = HARD (token/auth expired or unexpected -> wrapper halts + alerts)
REM See INTERFACE_CONTRACT.md 9 + src/constants.py RC_*.
REM
REM Args are forwarded to python, e.g.:
REM   run_ib_sync.bat --mode auto          (scheduler: pick activity/confirmation)
REM   run_ib_sync.bat --mode confirmation  (manual same-day pull)
REM   run_ib_sync.bat --no-delay --mode confirmation  (debug: skip the boot wait)

setlocal
cd /d "%~dp0\.."

REM Forward all args to python; consume a leading --no-delay (skips the boot wait).
set "PYARGS=%*"

REM 30-second WiFi delay (FR-ENTRY-2): wait for network to come up after boot.
if /i "%~1"=="--no-delay" (
    set "PYARGS=%PYARGS:*--no-delay=%"
    goto :run
)
timeout /t 30 /nobreak >nul

:run
REM Logging goes to logs\ib_sync_YYYYMMDD.log (written by Python, see _setup_logging)
REM plus the console. No shell redirect needed.
call "venv\Scripts\activate.bat"
python -m src.ib_sync %PYARGS%
set RC=%ERRORLEVEL%
endlocal & exit /b %RC%
