@echo off
REM ============================================================================
REM  Phenom Trade Worker - run it LOCALLY on your own PC (no Railway needed).
REM  Turn ON  : double-click this file when the market opens.
REM  Turn OFF : just close this window (or press Ctrl+C). It also auto-sleeps
REM             outside NSE hours (9:15am - 3:30pm IST), so it does nothing
REM             until the market is actually open.
REM
REM  Then in the website's Trade tab: click "Connect worker" and enter
REM       http://localhost:8000
REM  (you only type that once - the site remembers it; after that, Connect /
REM   Disconnect is your on/off switch, and this window is the engine).
REM
REM  Phone alerts (ntfy + Telegram) work from here because this PC makes the
REM  outbound calls. Optional: set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID below.
REM ============================================================================
cd /d "%~dp0"

REM --- optional phone alerts: uncomment + fill in to enable Telegram ---
REM set TELEGRAM_BOT_TOKEN=123456:ABC...
REM set TELEGRAM_CHAT_ID=987654321

echo.
echo [1/2] Installing / updating dependencies (first run only, ~1 min)...
py -3 -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo.
  echo Could not install dependencies. Make sure Python 3 is installed: py -3 --version
  pause
  exit /b 1
)

echo.
echo [2/2] Starting Phenom trade worker at  http://localhost:8000
echo       In the website Trade tab: Connect worker  ^>  http://localhost:8000
echo       Close this window to stop the worker.
echo.
REM Real market-hours gating (do NOT force-open). The monitor sleeps off-hours.
py -3 main.py
pause
