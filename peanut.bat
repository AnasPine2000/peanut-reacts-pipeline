@echo off
REM peanut — pipeline operations CLI (Windows wrapper)
REM Lets you run `peanut <command>` from the repo root instead of
REM `python scripts\peanut.py <command>`.
setlocal
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
python "%~dp0scripts\peanut.py" %*
endlocal
