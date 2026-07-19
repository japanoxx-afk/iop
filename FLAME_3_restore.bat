@echo off
cd /d "%~dp0"
echo [Restore flame infantry weapon to original]
"C:\Users\seo\AppData\Local\Programs\Python\Python314\python.exe" fix_flame.py restore
echo.
pause
