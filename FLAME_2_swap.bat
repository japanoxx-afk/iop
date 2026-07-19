@echo off
cd /d "%~dp0"
echo [Flame fix - reliable: swap to working rifle-type weapon]
"C:\Users\seo\AppData\Local\Programs\Python\Python314\python.exe" fix_flame.py swap
echo.
echo Now launch the game and attack with the flame infantry to check damage.
pause
