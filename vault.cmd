@echo off
REM vault up ^| status ^| reset ^| seed
cd /d "%~dp0"
set PYTHONPATH=backend;%PYTHONPATH%
python -m vault %*
