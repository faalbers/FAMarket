@echo off
rem Launch the FAMarket UI in your browser.
rem Serves the built frontend + the API from one process, and exits when the
rem last tab closes. If it reports frontend\dist missing, build it once:
rem     cd frontend ^&^& npm run build
cd /d "%~dp0"
".venv\Scripts\python.exe" scripts\serve_ui.py %*
