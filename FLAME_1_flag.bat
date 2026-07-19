@echo off
cd /d "%~dp0"
echo [Flame fix - minimal: keep flame visual, add damage flag]
"C:\Users\seo\AppData\Local\Programs\Python\Python314\python.exe" fix_flame.py flag
echo.
echo Now launch the game and attack with the flame infantry to check damage.
pause
