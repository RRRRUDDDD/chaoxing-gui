@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-WebView2.ps1" %*
set "chaoxing_exit=%ERRORLEVEL%"
pause
exit /b %chaoxing_exit%
