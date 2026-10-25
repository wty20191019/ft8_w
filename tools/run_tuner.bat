@echo off
rem Launch FT8 tuning UI (ASCII only: cmd.exe may use a non-UTF8 codepage)
setlocal
set "PY=python"
where python >nul 2>nul || set "PY=py -3"
%PY% "%~dp0ft8_tuner.py" %*
if errorlevel 1 pause
