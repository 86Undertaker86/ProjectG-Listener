@echo off
rem Builds the capture window (listener_gui.py) with PyInstaller:
rem   dist\ForzaListener\ForzaListener.exe  and  dist\ForzaListener.zip (send this one).
rem PyInstaller works in %TEMP%: OneDrive locks folders inside the project while it syncs,
rem and PyInstaller can't delete them then. The result is copied here afterwards.
rem Needs: .venv with pyinstaller (".venv\Scripts\python.exe -m pip install pyinstaller").
setlocal
cd /d "%~dp0"
set "WORK=%TEMP%\ForzaListener-build"
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --distpath "%WORK%\dist" --workpath "%WORK%\work" packaging\ForzaListener.spec
if errorlevel 1 exit /b 1
copy /y packaging\README_UA.txt "%WORK%\dist\ForzaListener\README_UA.txt" >nul
rem Self-check of the bundle: Recorder built from the packet layout, UDP socket opened.
start "" /wait "%WORK%\dist\ForzaListener\ForzaListener.exe" --check "%WORK%\check.txt"
if errorlevel 1 (
    echo Self-check failed:
    type "%WORK%\check.txt"
    exit /b 1
)
type "%WORK%\check.txt"
robocopy "%WORK%\dist\ForzaListener" dist\ForzaListener /MIR /R:5 /W:1 /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
    echo Copy to dist\ForzaListener failed - is ForzaListener.exe still running?
    exit /b 1
)
powershell -NoProfile -Command "Compress-Archive -Path '%WORK%\dist\ForzaListener' -DestinationPath 'dist\ForzaListener.zip' -Force"
if errorlevel 1 exit /b 1
echo.
echo Done: dist\ForzaListener\ForzaListener.exe and dist\ForzaListener.zip
exit /b 0
