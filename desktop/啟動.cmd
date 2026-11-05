@echo off
cd /d "%~dp0"
set "DIARY_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%DIARY_PY%" (
  if "%~1"=="" (
    start "" "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe" "%~dp0launcher.py"
  ) else (
    "%DIARY_PY%" server.py %*
  )
) else (
  if "%~1"=="" (
    start "" pyw -3 "%~dp0launcher.py"
  ) else (
    py -3 server.py %*
  )
)
if not "%~1"=="" pause
