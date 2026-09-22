@echo off
REM Solo hace falta si tu iPhone tiene iOS 17.0 a 17.3 (desde 17.4 no se necesita).
REM Clic derecho -> Ejecutar como administrador. Deja la ventana abierta y pulsa Conectar en GPS Libre.
python -m pymobiledevice3 remote tunneld
pause
