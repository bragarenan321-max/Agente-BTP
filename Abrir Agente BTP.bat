@echo off
chcp 65001 >NUL
title Agente BTP
cd /d "%~dp0"

rem Encerra qualquer Agente BTP que ainda esteja aberto (versao antiga na porta 8767).
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8767" ^| findstr "LISTENING"') do taskkill /PID %%p /F >NUL 2>&1

if not exist .venv (
  echo Preparando ambiente pela primeira vez...
  py -3 -m venv .venv 2>NUL || python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt
python -m agente_btp
pause
