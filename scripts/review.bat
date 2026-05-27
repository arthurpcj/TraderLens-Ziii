@echo off
REM TraderLens pivot review-flow entry (FR-PIVOT-3d glue).
REM One-shot loop: refresh annotations.csv -> open in Excel -> wait Enter ->
REM rebuild reports\pivot_latest.html -> auto-open in browser.
REM
REM Double-click to start; the terminal stays open so you can read the prompts
REM and press Enter to advance. Ctrl+C aborts cleanly (no regen, annotations
REM stay as-is).

setlocal
cd /d "%~dp0\.."
call "venv\Scripts\activate.bat"
python -m src.pivot --review-flow
set RC=%ERRORLEVEL%
echo.
pause
endlocal & exit /b %RC%
