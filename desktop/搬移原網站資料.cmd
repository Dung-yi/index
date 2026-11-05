@echo off
cd /d "%~dp0"
set "DIARY_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%DIARY_PY%" (
  "%DIARY_PY%" migrate_cloud.py
) else (
  py -3 migrate_cloud.py
)
pause
