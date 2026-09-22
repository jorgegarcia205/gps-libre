# PyInstaller (macOS): empaqueta GPS Libre como «GPS Libre.app».
# Se ejecuta:  python -m PyInstaller --distpath dist --workpath build gpslibre-mac.spec
# No copia libssl (eso es solo Windows); en macOS pymobiledevice3 usa el OpenSSL del sistema/Python.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

AQUI = Path(SPECPATH)
PROYECTO = AQUI.parent


def _sub(nombre, filtro=None):
    # Colecciona submódulos de forma resiliente (si un paquete opcional no está, no rompe el build).
    try:
        return collect_submodules(nombre, filter=filtro) if filtro else collect_submodules(nombre)
    except Exception:
        return []


ocultos = (
    _sub("pymobiledevice3", lambda n: ".cli" not in n and not n.endswith("__main__"))
    + _sub("uvicorn")
    + _sub("pmd_pytcp")
    + _sub("pmd_net_addr")
    + _sub("pmd_net_proto")
    + _sub("qh3")
    + _sub("sslpsk_pmd3")
    + _sub("developer_disk_image")
)

datos = [
    (str(PROYECTO / "web" / "index.html"), "web"),
    (str(PROYECTO / "web" / "icono.png"), "web"),
    (str(PROYECTO / "web" / "icono-512.png"), "web"),
    (str(PROYECTO / "web" / "manifest.webmanifest"), "web"),
] + collect_data_files("pymobiledevice3")
datos += copy_metadata("pymobiledevice3", recursive=True)

_icns = AQUI / "gpslibre.icns"
icono = str(_icns) if _icns.is_file() else None

a = Analysis(
    [str(PROYECTO / "servidor.py")],
    pathex=[str(PROYECTO)],
    binaries=[],
    datas=datos,
    hiddenimports=ocultos,
    excludes=[
        "IPython", "xonsh", "jedi", "matplotlib", "tkinter", "pytest", "av",
        "numba", "llvmlite", "scipy", "pandas", "sklearn", "torch", "cv2", "sympy", "notebook", "jupyter",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="GPSLibre", console=False, icon=icono)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="GPSLibre")
app = BUNDLE(
    coll,
    name="GPS Libre.app",
    icon=icono,
    bundle_identifier="com.gpslibre.app",
    info_plist={
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
        "NSHighResolutionCapable": True,
        # macOS 13+ pide describir el uso de la ubicación aunque la app no la lea directamente.
        "NSLocationWhenInUseUsageDescription": "GPS Libre gestiona la ubicación simulada de tu iPhone.",
    },
)
