@echo off
setlocal
cd /d "%~dp0"
title AI MEKDEP

echo ==========================================
echo AI MEKDEP STARTER
echo ==========================================
echo.

set "PYTHON_CMD="
where py >nul 2>&1
if %errorlevel%==0 set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
    where python >nul 2>&1
    if %errorlevel%==0 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo ERROR: Python was not found.
    echo Install Python 3 and select Add Python to PATH.
    echo.
    pause
    exit /b 1
)

echo Python found.

%PYTHON_CMD% -c "import flask, openai, pypdf, docx" >nul 2>&1
if errorlevel 1 (
    echo Installing required Python packages...
    %PYTHON_CMD% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo ERROR: Required packages could not be installed.
        echo Check the internet connection and try again.
        echo.
        pause
        exit /b 1
    )
)

if "%OPENAI_API_KEY%"=="" (
    echo.
    echo OpenAI API key is not set.
    echo You can press ENTER to start without AI,
    echo or paste your OpenAI API key now.
    set /p "OPENAI_API_KEY=API key: "
)

echo.
echo Mugallymlar ilkinji gezek platformada oz login we parolyny doredip biler.
echo Address: http://127.0.0.1:5000
echo Keep this window open while using AI MEKDEP.
echo.

start "" "http://127.0.0.1:5000"
%PYTHON_CMD% app.py

echo.
echo AI MEKDEP stopped.
pause
