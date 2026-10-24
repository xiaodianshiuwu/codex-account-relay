@echo off
setlocal
set CODEX_RELAY_TEST_MODE=1
set PYTHONPATH=%~dp0..;%PYTHONPATH%
python "%~dp0run.py" %*
