"""Iconos de GPS Libre (marca nueva 2026-09).

El logo se rediseñó como SVG glossy (pin origen -> estela -> pin destino + onda), en
`gps-web/brand/app-icon.svg`. De ahí se rasterizaron a mano:
  - web/icono.png (256) y web/icono-512.png : pantalla de inicio / apple-touch-icon.
  - empaquetar/gpslibre.ico : ejecutable e instalador de Windows.

Estos archivos YA están en el repo, así que este script NO los regenera (antes dibujaba el
logo viejo con PIL y lo sobrescribía). Se deja como paso no destructivo para no romper
`construir.ps1`. Si hay que regenerar el .ico/.png desde el SVG, rasterizar app-icon.svg
(p. ej. con Edge headless --screenshot) y volver a guardar con Pillow.
"""

from pathlib import Path

AQUI = Path(__file__).resolve().parent
WEB = AQUI.parent / "web"

REQUERIDOS = [WEB / "icono.png", WEB / "icono-512.png", AQUI / "gpslibre.ico"]

if __name__ == "__main__":
    faltan = [str(p.name) for p in REQUERIDOS if not p.exists()]
    if faltan:
        print("AVISO: faltan iconos de marca:", ", ".join(faltan))
    else:
        print("iconos de marca nueva OK (no se regeneran)")
