@echo off
rem Launch the FAMarket UI in your browser.
cd /d "%~dp0"
".venv\Scripts\streamlit.exe" run app.py
