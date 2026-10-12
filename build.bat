@echo off
rem Window Remote 1.0.0 build script
rem Output: dist\WindowRemote.exe
cd /d "%~dp0"

if not exist .venv (
    python -m venv .venv || goto :error
)
.venv\Scripts\python.exe -m pip install -r requirements.txt || goto :error

.venv\Scripts\pyinstaller.exe --noconfirm --clean --onefile --windowed ^
    --name WindowRemote ^
    --version-file version_info.txt ^
    --exclude-module tkinter ^
    main.py || goto :error

echo.
echo Build OK: dist\WindowRemote.exe
exit /b 0

:error
echo Build FAILED
exit /b 1
