@echo off
setlocal
echo Cboard Windows installer r2
where pwsh.exe >nul 2>&1
if errorlevel 1 goto legacy
pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Prepare-Cboard.ps1"
goto result
:legacy
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Prepare-Cboard.ps1"
:result
set "CB_RESULT=%ERRORLEVEL%"
echo.
if "%CB_RESULT%"=="0" goto success
echo INSTALLATION FAILED - not running in the background.
echo Log: %LOCALAPPDATA%\Cboard\logs\install-last.log
if exist "%LOCALAPPDATA%\Cboard\logs\install-last.log" start "" notepad.exe "%LOCALAPPDATA%\Cboard\logs\install-last.log"
echo This window stays open so you can read or copy the error.
echo Type exit only when finished.
cmd /k
exit /b %CB_RESULT%
:success
echo CBOARD_INSTALL_OK - installation and command checks passed.
echo Use cusage in the newly opened PowerShell window.
pause
exit /b 0
