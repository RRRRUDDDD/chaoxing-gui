@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Chaoxing.ps1" %*
set "chaoxing_exit=%ERRORLEVEL%"
if not "%chaoxing_exit%"=="0" pause
exit /b %chaoxing_exit%
