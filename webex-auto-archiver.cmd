@echo off
setlocal
set "SCRIPT_DIR=%~dp0"

if /I "%~1"=="generate" (
    shift
    py -3 "%SCRIPT_DIR%generate_space_batch.py" %*
    exit /b %ERRORLEVEL%
)

if /I "%~1"=="download" (
    shift
    py -3 "%SCRIPT_DIR%download_everything.py" %*
    exit /b %ERRORLEVEL%
)

echo Usage: %~nx0 generate [options]
echo        %~nx0 download [options]
exit /b 1
