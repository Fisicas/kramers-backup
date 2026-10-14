@echo off
rem Installs Kramers' one dependency (sv-ttk) and creates Desktop + Start Menu shortcuts.
rem Double-click this in Explorer (not from a terminal inside the Claude app, whose
rem AppData writes are redirected). Set KRAMERS_PYTHON to choose an interpreter.
cd /d "%~dp0.."
set "PY=%KRAMERS_PYTHON%"
if "%PY%"=="" ( py -3.12 -c "import tkinter" >nul 2>nul && set "PY=py -3.12" )
if "%PY%"=="" ( py -3 -c "import tkinter" >nul 2>nul && set "PY=py -3" )
if "%PY%"=="" set "PY=python"
echo Using %PY%
%PY% -m pip install --disable-pip-version-check -q -r requirements.txt
%PY% "%~dp0..\Kramers.pyw" --install-shortcuts
pause
