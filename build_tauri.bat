@echo off
setlocal
where pwsh.exe >nul 2>&1
if errorlevel 1 (
  echo PowerShell 7 is required. See desktop\README.md.
  exit /b 1
)
pwsh.exe -NoProfile -File "%~dp0desktop\scripts\build-tauri.ps1" %*
exit /b %errorlevel%
