# PyInstaller: empaqueta GPS Libre como carpeta dist\GPSLibre\GPSLibre.exe (la usa instalador.iss).
# Se ejecuta desde construir.ps1:  python -m PyInstaller --distpath dist --workpath build gpslibre.spec
import os
import shutil
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

AQUI = Path(SPECPATH)
PROYECTO = AQUI.parent

# sslpsk_pmd3 (túnel por Wi‑Fi) busca `libssl-1_1-x64.dll`; Python 3.11 la trae como `libssl-1_1.dll`.
# Se mete renombrada en la raíz del ejecutable, que es donde Windows busca las DLL del programa.
dll_renombrada = AQUI / "build" / "dll" / "libssl-1_1-x64.dll"
dll_renombrada.parent.mkdir(parents=True, exist_ok=True)
_candidatos_ssl = [
    Path(sys.base_prefix) / "DLLs" / "libssl-1_1.dll",
    Path(sys.base_prefix) / "libssl-1_1.dll",
    Path(sys.prefix) / "DLLs" / "libssl-1_1.dll",
]
_origen_ssl = next((c for c in _candidatos_ssl if c.is_file()), None)
if _origen_ssl is None:
    raise SystemExit(
        "No encuentro libssl-1_1.dll (OpenSSL 1.1.1) en este Python.\n"
        "GPS Libre necesita un Python 3.11 compilado con OpenSSL 1.1.1 (p. ej. 3.11.0-3.11.4 en Windows).\n"
        "Los Python 3.11 nuevos traen OpenSSL 3 (libssl-3.dll), incompatible con sslpsk_pmd3 (túnel Wi-Fi)."
    )
shutil.copy2(_origen_ssl, dll_renombrada)
# PyInstaller importa sslpsk_pmd3 para listar sus módulos: necesita encontrar la DLL también aquí.
_directorios_dll = [os.add_dll_directory(str(c)) for c in (dll_renombrada.parent, Path(sys.base_prefix) / "DLLs")]

# Módulos que se cargan por nombre en tiempo de ejecución y el análisis estático no ve.
ocultos = (
    collect_submodules("pymobiledevice3", filter=lambda nombre: ".cli" not in nombre and not nombre.endswith("__main__"))
    + collect_submodules("uvicorn")
    + collect_submodules("pmd_pytcp")
    + collect_submodules("pmd_net_addr")
    + collect_submodules("pmd_net_proto")
    + collect_submodules("qh3")
    + collect_submodules("sslpsk_pmd3")
    + collect_submodules("developer_disk_image")
)

datos = [
    (str(PROYECTO / "web" / "index.html"), "web"),
    (str(PROYECTO / "web" / "icono.png"), "web"),
    (str(PROYECTO / "web" / "icono-512.png"), "web"),
    (str(PROYECTO / "web" / "manifest.webmanifest"), "web"),
] + collect_data_files("pymobiledevice3") + collect_data_files("pytun_pmd3", includes=["**/*.dll"])
# pyimg4 y otras dependencias leen su propia versión instalada (importlib.metadata) al importarse.
datos += copy_metadata("pymobiledevice3", recursive=True)

a = Analysis(
    [str(PROYECTO / "servidor.py")],
    pathex=[str(PROYECTO)],
    binaries=[(str(dll_renombrada), ".")],
    datas=datos,
    hiddenimports=ocultos,
    # Dependencias de la línea de comandos de pymobiledevice3 que GPS Libre no usa y pesan mucho.
    excludes=[
        "IPython", "xonsh", "jedi", "matplotlib", "tkinter", "pytest", "av",
        # Paquetes científicos que hay en este Python por otros proyectos; ni GPS Libre ni pymobiledevice3 los usan.
        "numba", "llvmlite", "scipy", "pandas", "sklearn", "torch", "cv2", "sympy", "notebook", "jupyter",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GPSLibre",
    icon=str(AQUI / "gpslibre.ico"),
    console=False,  # sin ventana negra: la interfaz es la página web (y el botón «Cerrar GPS Libre»)
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="GPSLibre")
