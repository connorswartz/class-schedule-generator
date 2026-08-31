@echo off
REM Start the Class Schedule Generator on Windows.
REM Creates the virtual environment on first run, then launches the app.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo Could not create the virtual environment.
        echo Install Python 3.10+ from https://www.python.org/downloads/
        echo and make sure "Add python.exe to PATH" is ticked.
        pause
        exit /b 1
    )
    echo Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
)

echo.
echo Starting the schedule generator...
echo Open http://localhost:5001 in your browser. Press Ctrl+C to stop.
echo.
start "" "http://localhost:5001"
".venv\Scripts\python.exe" app.py
pause
