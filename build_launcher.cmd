@echo off
setlocal
cd /d "%~dp0"

echo [1/2] Syncing locked dependencies...
uv sync --locked
if errorlevel 1 goto :failed

echo [2/2] Building the Windows GUI launcher...
uv run pyinstaller --noconfirm --clean --onefile --windowed --name "MultiAgentBI-Launcher" --distpath "dist" --workpath "build\launcher" --specpath "build\launcher" "launcher.pyw"
if errorlevel 1 goto :failed

echo.
echo Build complete: %~dp0dist\MultiAgentBI-Launcher.exe
exit /b 0

:failed
echo.
echo Launcher build failed. Review the output above.
exit /b 1
