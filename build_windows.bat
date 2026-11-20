@echo off
setlocal
cd /d "%~dp0"

py -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1

py -m PyInstaller --noconfirm --clean --onefile --windowed --name VideoDownloader --collect-all yt_dlp --collect-all yt_dlp_ejs main.py
if errorlevel 1 exit /b 1

echo.
echo Executable created: dist\VideoDownloader.exe
endlocal
