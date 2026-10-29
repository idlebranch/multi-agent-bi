@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%..\.."

echo [1/2] Syncing locked dependencies...
uv sync --locked
if errorlevel 1 goto :failed

echo [2/2] Building the Windows GUI launcher...
uv run pyinstaller --noconfirm --clean --onefile --windowed --name "MultiAgentBI-Launcher" --distpath "%SCRIPT_DIR%dist" --workpath "%SCRIPT_DIR%build\launcher" --specpath "%SCRIPT_DIR%build\launcher" "%SCRIPT_DIR%launcher.pyw"
if errorlevel 1 goto :failed

echo.
echo Build complete: %SCRIPT_DIR%dist\MultiAgentBI-Launcher.exe
exit /b 0

:failed
echo.
echo Launcher build failed. Review the output above.
exit /b 1
