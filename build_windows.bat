@echo off
REM Build a single-file Windows exe (run this on a Windows PC with Python).
REM Output: dist\HaremLinkBridge.exe  — ship that file + keep config beside it.
setlocal
cd /d "%~dp0\.."
python -m pip install -r link_bridge\requirements.txt
python -m PyInstaller --noconfirm link_bridge\harem_link_bridge.spec
echo.
echo Built: dist\HaremLinkBridge.exe
echo Config file (created on first save): harem_link_bridge.json next to the exe.
pause
