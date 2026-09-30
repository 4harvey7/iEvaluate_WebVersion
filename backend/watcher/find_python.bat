@echo off
REM Helper (do not double-click). Used by INSTALL.bat, start_watcher.bat, CHECK.bat.
REM Sets PY to a command that runs Python 3.10 or newer, for example
REM   py -3      or      "C:\Users\me\AppData\Local\Programs\Python\Python312\python.exe"
REM PY stays empty when no usable Python is found.
REM Works right after a fresh (winget / python.org) install, even when Python is
REM not on PATH yet in this window.
set "PY="
call :probe py -3
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if not defined PY if exist "%%~D\python.exe" call :probe "%%~D\python.exe"
for /d %%D in ("%ProgramFiles%\Python3*") do if not defined PY if exist "%%~D\python.exe" call :probe "%%~D\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" call :probe "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" -3
if not defined PY call :probe python
if not defined PY call :probe python3
goto :eof

:probe
if defined PY goto :eof
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set PY=%*
goto :eof
