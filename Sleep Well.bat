@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "Sleep Well" ".venv\Scripts\pythonw.exe" -m nightshift tray
) else (
    start "Sleep Well" pythonw -m nightshift tray
)
