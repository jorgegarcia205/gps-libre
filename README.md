# GPS Libre

**Your location. Your choice.**

Cambia la ubicación GPS de un iPhone desde un **PC con Windows**, sin jailbreak: teletransporte, rutas por
calles reales, recorrido de zonas y joystick. La interfaz es una página web local (Leaflet + OpenStreetMap)
servida por un pequeño servidor FastAPI; la comunicación con el iPhone se hace con
[`pymobiledevice3`](https://github.com/doronz88/pymobiledevice3) (USB la primera vez y Wi‑Fi después).

> Este repositorio contiene la **app de escritorio (Windows)**. El cliente de Android (mock location, sin PC)
> es un proyecto aparte.

---

## Arquitectura

```
┌───────────────────────────┐        USB (1ª vez) / Wi‑Fi         ┌───────────────┐
│  PC Windows                │  ───────────────────────────────▶  │   iPhone      │
│                            │        pymobiledevice3              │  (iOS 17+)    │
│  servidor.py (FastAPI)     │                                     └───────────────┘
│   ├─ Iphone   → túnel USB / Wi‑Fi (RemotePairing + RSD)
│   ├─ Motor    → teletransporte, rutas (OSRM), zonas (Overpass), joystick
│   ├─ Licencia → 2 mov. gratis; claves firmadas (Ed25519) o Lemon Squeezy
│   └─ Acceso   → control desde el propio iPhone (PIN/QR, misma red)
│                            │
│  web/index.html  ◀── navegador local (127.0.0.1:8765): mapa Leaflet + 7 idiomas
└───────────────────────────┘
```

- **`servidor.py`** — todo el backend en un archivo: geometría, clase `Iphone` (USB + `TunelWifi` por Wi‑Fi),
  `Motor` (movimiento y streaming de zonas por sectores), `RedDeCalles` (Overpass), `Licencia` y `AccesoCelular`.
  La ubicación real del PC se lee con `System.Device.Location.GeoCoordinateWatcher` vía PowerShell.
- **`web/`** — la interfaz (una SPA en `index.html`), iconos y `manifest.webmanifest`.
- **`empaquetar/`** — empaquetado con **PyInstaller** (`gpslibre.spec`) + **Inno Setup** (`instalador.iss`),
  orquestado por `construir.ps1`. Iconos en `generar_icono.py`; generación de claves en `generar_licencia.py`.

### Detalle de la conexión con el iPhone
- **iOS < 17:** simulación por lockdown con `DtSimulateLocation`.
- **iOS 17+:** `LocationSimulation` vía **DVT** sobre un **túnel RSD**.
- **Wi‑Fi:** túnel **RemotePairing** (`create_core_device_tunnel_service_using_remotepairing`, Bonjour
  `_remotepairing._tcp`, puerto 49152) envuelto en `UserspaceRsdTunnel`. En Python < 3.13 ese túnel usa
  `sslpsk_pmd3`, cuyo `.pyd` busca `libssl-1_1-x64.dll`; Python 3.11 la trae como `libssl-1_1.dll` y **se copia
  renombrada automáticamente** (en dev por `servidor.py`, en el build por `gpslibre.spec`). No hay que subir esa DLL.

---

## Requisitos

### Para usar la app (usuario final)
| Requisito | Detalle |
|---|---|
| **Windows** | 10 u 11, 64 bits (x64). |
| **Driver de Apple** | **Apple Mobile Device Support** — viene con **iTunes** o con la app **«Dispositivos Apple»** de Microsoft Store. Sin ese servicio, Windows no habla con el iPhone por **cable**. El instalador lo detecta y ofrece abrir la tienda. |
| **iPhone** | iOS 17 o superior, con **Modo desarrollador** activado (Ajustes → Privacidad y seguridad). |
| **Red** | Para Wi‑Fi, PC e iPhone en la **misma red local**. |

### Para desarrollar / construir
| Requisito | Detalle |
|---|---|
| **Python** | **3.11 (x64)**. El Python de Windows 3.11 incluye `DLLs/libssl-1_1.dll` (OpenSSL 1.1.1), necesaria para el túnel Wi‑Fi. |
| **Dependencias** | `pip install -r requirements.txt` (ver lista). |
| **PyInstaller** | `pip install pyinstaller` (build de la app). |
| **Inno Setup 6** | Solo para generar el **instalador** (`.exe`). `winget install JRSoftware.InnoSetup` o `choco install innosetup`. |

---

## iOS probados y matriz de compatibilidad

**Probado en real:** iPhone 13 (`iPhone14,5`) con **iOS 26.6.1**, por **cable y por Wi‑Fi**.

| iOS del iPhone | Simulación de ubicación | Cable (USB) | Wi‑Fi |
|---|---|:---:|:---:|
| iOS ≤ 16 | `DtSimulateLocation` (lockdown) | ✅ | ➖ (Wi‑Fi requiere túnel RSD, solo iOS 17+) |
| iOS 17 / 18 / 26 | `LocationSimulation` (DVT sobre túnel RSD) | ✅ | ✅ (RemotePairing) |

- La **primera conexión siempre es por cable** (para «Confiar» y emparejar). Después funciona por Wi‑Fi.
- La imagen de desarrollador (DDI) se monta automáticamente; si la pantalla está bloqueada, se espera al desbloqueo.

---

## Ejecutar en desarrollo

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python servidor.py
```

Abre `http://127.0.0.1:8765`. Conecta el iPhone por cable la primera vez y pulsa **Conectar**.
`servidor.py` copia solo la DLL de OpenSSL que necesita; no hay que preparar nada más.

Archivos que la app crea en tiempo de ejecución (ignorados por git): `config.json`, `guardados.json`,
`iphone_recordado.json`, `licencia.json`. Cuando el `.exe` está congelado, viven en `%LOCALAPPDATA%\GPSLibre`.

---

## Build y release

### Construir el instalador (local)
```powershell
powershell -ExecutionPolicy Bypass -File empaquetar\construir.ps1
```
Hace, en orden:
1. `generar_icono.py` — verifica los iconos de marca (`web/icono*.png`, `empaquetar/gpslibre.ico`).
2. **PyInstaller** con `gpslibre.spec` → `empaquetar/dist/GPSLibre/GPSLibre.exe` (onedir, sin consola).
3. **Inno Setup** con `instalador.iss` → `empaquetar/salida/GPSLibre-Instalador-<versión>.exe`
   (instalador ultra‑sencillo, admin, abre el firewall para la Wi‑Fi del iPhone).

### Publicar un release (GitHub)
El instalador (≈48 MB) **no cabe** en un hosting estático típico; se publica como **GitHub Release**:
```bash
gh release create v1.0.0 "empaquetar/salida/GPSLibre-Instalador-1.0.0.exe" \
  --repo jorgegarcia205/gps-libre --title "GPS Libre 1.0.0" --notes "Your location. Your choice."
```
> El **CI** (`.github/workflows/ci.yml`) reconstruye la app y el instalador en cada push a `main` y sube el
> `.exe` como *artifact*, demostrando que el build es reproducible desde un checkout limpio.

---

## Sistema de licencias y **secretos**

- **2 teletransportes gratis**; rutas, zonas y joystick son de pago.
- Dos tipos de clave, ambos verificados en `servidor.py`:
  - **`GPSL-…`** — clave firmada **Ed25519**. La **llave pública** va incrustada en `servidor.py`
    (`CLAVE_PUBLICA_LICENCIA`); la **llave privada** vive **solo** en `empaquetar/licencia_privada.pem` y la usa
    `generar_licencia.py` para acuñar claves. **Esa llave privada NO está en el repositorio (ni debe estarlo).**
  - **Claves de Lemon Squeezy** (formato UUID) — se validan **en línea** contra la API de licencias de
    Lemon Squeezy (`/v1/licenses/activate` y `/validate`, sin token), de modo que una cancelación bloquea la app.

> **Nada sensible se publica.** El `.gitignore` excluye `*.pem`, claves, `.env`, artefactos de build y datos de
> runtime. El único secreto del proyecto es la llave privada de licencias, que **no** interviene en el build.

---

## Reproducibilidad

**Construir y ejecutar la app NO requiere ningún secreto.** Un desarrollador puede clonar este repositorio y,
siguiendo esta documentación, reproducir el build completo (PyInstaller + Inno Setup) en Windows con Python 3.11.
El CI lo verifica en cada push.

La **única** dependencia privada es `empaquetar/licencia_privada.pem`, y **solo** afecta a una cosa: acuñar
claves `GPSL-…` que validen contra la llave pública incrustada. Sin ella:
- La app **compila, se empaqueta y funciona** igual (los 2 movimientos gratis, la conexión con el iPhone, todo).
- Si necesitas emitir tus propias claves firmadas, ejecuta `python empaquetar/generar_licencia.py --plan vida`:
  crea una **nueva** pareja de llaves y te imprime la pública para reemplazar `CLAVE_PUBLICA_LICENCIA` en `servidor.py`.

---

## Estructura del repositorio

```
servidor.py                 # backend completo (FastAPI + pymobiledevice3 + licencias)
web/                        # interfaz web (index.html, iconos, manifest)
empaquetar/
  construir.ps1             # icono → PyInstaller → Inno Setup
  gpslibre.spec             # empaquetado PyInstaller (copia la DLL de OpenSSL desde Python)
  instalador.iss            # instalador Inno Setup (admin, firewall, accesos directos)
  generar_icono.py          # verifica iconos de marca
  generar_licencia.py       # acuña claves firmadas (necesita la llave privada, no incluida)
  gpslibre.ico              # icono del ejecutable/instalador
requirements.txt            # dependencias de Python
iniciar.bat / tunel_admin.bat  # lanzadores de desarrollo
.github/workflows/ci.yml    # build reproducible en Windows
```

---

No afiliado a Apple Inc. «iPhone» e «iOS» son marcas de Apple. El usuario es responsable del uso de la aplicación.
