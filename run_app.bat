@echo off
rem Launch the FAMarket UI in your browser.
rem Rebuilds frontend\dist only when it is older than the frontend sources,
rem then serves the built frontend + the API from one process, and exits when
rem the last tab closes.
cd /d "%~dp0"
".venv\Scripts\python.exe" scripts\launch_ui.py %*
