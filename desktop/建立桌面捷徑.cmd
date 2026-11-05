@echo off
cd /d "%~dp0"
set "DIARY_PYW=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
if exist "%DIARY_PYW%" (
  start "" "%DIARY_PYW%" "%~dp0launcher.py" --shortcut
) else (
  start "" pyw -3 "%~dp0launcher.py" --shortcut
)
