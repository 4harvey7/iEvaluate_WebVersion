@echo off
REM Removes the Scan Watcher auto-start (does NOT stop a running watcher,
REM does NOT delete any scans). UNINSTALL.bat is the friendly version.
REM   uninstall_task.bat          remove auto-start, then wait for a key
REM   uninstall_task.bat /quiet   same, no pause (used by INSTALL / UNINSTALL)
REM Exit code 0 = no auto-start left, 1 = an old scheduled task could not be removed.
setlocal
set "TASKNAME=iEvaluate Scan Watcher"
set "SW_LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\iEvaluate Scan Watcher.lnk"
set "RESULT=0"

REM 1) Start-up shortcut (current installs).
if exist "%SW_LNK%" del /f /q "%SW_LNK%" >nul 2>&1

REM 2) Scheduled task (older installs).
schtasks /query /tn "%TASKNAME%" >nul 2>&1
if errorlevel 1 goto done
schtasks /delete /tn "%TASKNAME%" /f >nul 2>&1
schtasks /query /tn "%TASKNAME%" >nul 2>&1
if errorlevel 1 goto done

echo.
echo   An older auto-start needs permission to be removed.
echo   If Windows asks "Do you want to allow this app to make changes to your device?"
echo   click "Yes".
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Start-Process -FilePath schtasks.exe -ArgumentList '/delete /tn \"iEvaluate Scan Watcher\" /f' -Verb RunAs -Wait -WindowStyle Hidden } catch { exit 1 }" >nul 2>&1
schtasks /query /tn "%TASKNAME%" >nul 2>&1
if errorlevel 1 goto done
echo   [!] The old auto-start "%TASKNAME%" could not be removed.
echo       Ask your IT contact to delete it in Task Scheduler.
set "RESULT=1"

:done
if exist "%SW_LNK%" set "RESULT=1"
if /i "%~1"=="/quiet" exit /b %RESULT%
if "%RESULT%"=="0" echo Auto-start removed. The watcher will no longer start by itself. Your scans were not touched.
pause
exit /b %RESULT%
