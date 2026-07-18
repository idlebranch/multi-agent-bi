@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Multi-Agent BI Manual Test
uv run python scripts\manual_test.py
if errorlevel 1 pause
