@echo off
REM Launch Conjure Finder GUI (Windows shortcut target).
cd /d "%~dp0\.."
if exist "venv\Scripts\pythonw.exe" (
  start "" "venv\Scripts\pythonw.exe" -m conjure_finder
) else if exist "venv\Scripts\python.exe" (
  start "" "venv\Scripts\python.exe" -m conjure_finder
) else (
  start "" pythonw -m conjure_finder
)
