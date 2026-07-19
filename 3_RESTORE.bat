@echo off
cd /d "%~dp0"
echo Restoring original balance ...
"C:\Users\seo\AppData\Local\Programs\Python\Python314\python.exe" iop_balance.py restore
echo.
echo Done. Original restored.
pause
