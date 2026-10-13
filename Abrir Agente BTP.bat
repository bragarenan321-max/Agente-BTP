@echo off
chcp 65001 >nul
title Agente BTP
cd /d "%~dp0"
if not exist .venv (
  echo Preparando ambiente pela primeira vez...
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt
python -m agente_btp
pause
