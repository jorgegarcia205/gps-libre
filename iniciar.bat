@echo off
REM Abre GPS Libre: servidor local + mapa en el navegador. Deja esta ventana abierta.
cd /d "%~dp0"
python -c "import pymobiledevice3" 2>nul || python -m pip install -r requirements.txt
python servidor.py
pause
