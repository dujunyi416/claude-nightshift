@echo off
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    start "Sleep Well" ".venv\Scripts\pythonw.exe" -m nightshift tray
    exit /b
)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" (
    start "Sleep Well" "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" -m nightshift tray
    exit /b
)
if exist "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" (
    start "Sleep Well" "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" -m nightshift tray
    exit /b
)
if exist "%LOCALAPPDATA%\Programs\Python\Python310\pythonw.exe" (
    start "Sleep Well" "%LOCALAPPDATA%\Programs\Python\Python310\pythonw.exe" -m nightshift tray
    exit /b
)
start "Sleep Well" python -m nightshift tray
