@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo   DiceFrame WebUI
echo   Open http://localhost:18000 after startup.
echo ========================================
echo.

if exist ".venv314\Scripts\python.exe" goto run_venv314
if exist ".venv\Scripts\python.exe" goto run_venv
rem No virtualenv: prefer the py launcher, which picks a real interpreter.
rem Bare "python" on Windows may be the Store alias or a version whose
rem dependencies have no wheels, and then startup spends minutes in pip.
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 goto run_py
goto run_system_python

:run_venv314
".venv314\Scripts\python.exe" scripts\start_webui.py
set "WEBUI_EXIT_CODE=%ERRORLEVEL%"
goto finished

:run_venv
".venv\Scripts\python.exe" scripts\start_webui.py
set "WEBUI_EXIT_CODE=%ERRORLEVEL%"
goto finished

:run_py
py -3 scripts\start_webui.py
set "WEBUI_EXIT_CODE=%ERRORLEVEL%"
goto finished

:run_system_python
python scripts\start_webui.py
set "WEBUI_EXIT_CODE=%ERRORLEVEL%"

:finished
if not "%WEBUI_EXIT_CODE%"=="0" goto failed
echo.
echo DiceFrame WebUI stopped.
pause
exit /b 0

:failed
echo.
echo DiceFrame WebUI failed to start. Review the error above.
pause
exit /b %WEBUI_EXIT_CODE%
