@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Multi-Agent BI Web Test
uv run python scripts\start_web.py
pause
