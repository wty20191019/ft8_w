@echo off
rem 双击启动 FT8 参数调优台
cd /d "%~dp0.."
python tools\ft8_tuner.py
if errorlevel 1 pause
