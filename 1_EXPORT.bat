@echo off
cd /d "%~dp0"
echo Exporting current game data to balance.csv ...
"C:\Users\seo\AppData\Local\Programs\Python\Python314\python.exe" iop_balance.py export balance.csv
echo.
echo Done. Edit balance.csv in Excel or Notepad, then run 2_APPLY.bat
pause
