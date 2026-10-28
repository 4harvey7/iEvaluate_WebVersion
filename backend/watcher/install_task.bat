@echo off
REM OPTIONAL, for IT: makes the Scan Watcher start by itself (minimized) every
REM time this Windows user logs on. Since watcher 2.5.0 this is OFF by default:
REM people start it by hand with the "Start Scan Watcher" shortcut and stop it
REM with "Stop Scan Watcher". INSTALL.bat no longer calls this - it REMOVES any
REM auto-start, so run this again after every INSTALL if you want auto-start.
REM Turn it off again with uninstall_task.bat.
REM   install_task.bat          set up auto-start, start the watcher now, wait for a key
REM   install_task.bat /quiet   only set up auto-start, no pause
REM Uses a shortcut in this user's Start-up folder (no administrator rights needed).
REM If that fails it falls back to a scheduled task. Old auto-starts are removed
REM first, so running this twice is safe. Exit code 0 = OK, 1 = failed.
setlocal
set "TASKNAME=iEvaluate Scan Watcher"
set "SW_STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SW_LNK=%SW_STARTUP%\iEvaluate Scan Watcher.lnk"
set "SW_TARGET=%~dp0start_watcher.bat"
for %%I in ("%~dp0.") do set "SW_WORKDIR=%%~fI"

if not exist "%SW_TARGET%" (
  echo start_watcher.bat is missing next to install_task.bat.
  goto failed
)

REM Remove any older auto-start (old scheduled task / old shortcut).
call "%~dp0uninstall_task.bat" /quiet

if not exist "%SW_STARTUP%\" mkdir "%SW_STARTUP%" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:SW_LNK); $s.TargetPath = $env:SW_TARGET; $s.WorkingDirectory = $env:SW_WORKDIR; $s.WindowStyle = 7; $s.Description = 'iEvaluate Scan Watcher'; $s.Save()" >nul 2>&1
if exist "%SW_LNK%" goto created

REM Fallback: scheduled task at log-on (may need "Run as administrator").
schtasks /create /tn "%TASKNAME%" /tr "\"%SW_TARGET%\"" /sc ONLOGON /rl LIMITED /f >nul 2>&1
if errorlevel 1 goto failed

:created
if /i "%~1"=="/quiet" exit /b 0
echo Auto-start is set up. The watcher will start by itself at every log-on.
echo Starting it now...
start "iEvaluate Scan Watcher" /min /d "%SW_WORKDIR%" "%SW_TARGET%"
pause
exit /b 0

:failed
if /i "%~1"=="/quiet" exit /b 1
echo Could not set up auto-start. Try right-clicking this file and choosing "Run as administrator".
pause
exit /b 1
