@echo off
rem Removes Kramers' Desktop + Start Menu shortcuts and its 'Kramers' scheduled task,
rem and asks before deleting its settings (%APPDATA%\Kramers). Your backups and this
rem program folder are never touched. Double-click this in Explorer.
cd /d "%~dp0.."
set "PY=%KRAMERS_PYTHON%"
if "%PY%"=="" ( py -3.12 -c "import tkinter" >nul 2>nul && set "PY=py -3.12" )
if "%PY%"=="" ( py -3 -c "import tkinter" >nul 2>nul && set "PY=py -3" )
if "%PY%"=="" set "PY=python"
%PY% "%~dp0..\Kramers.pyw" --uninstall
pause
