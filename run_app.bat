@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    py -3.12 -m venv .venv
    if errorlevel 1 (
        echo Install Python 3.12 and try again.
        pause
        exit /b 1
    )
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo Dependency installation failed. Check your internet connection and Python installation.
    pause
    exit /b 1
)
.venv\Scripts\python.exe -m streamlit run app.py
pause
