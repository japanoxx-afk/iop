@echo off
cd /d "%~dp0"
echo Applying balance.csv to the game (original auto-backed up once) ...
"C:\Users\seo\AppData\Local\Programs\Python\Python314\python.exe" iop_balance.py import balance.csv
echo.
echo Done. Launch the game to check.
pause
