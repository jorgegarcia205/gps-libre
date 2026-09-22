"""GPS Libre — simulador de ubicación para iPhone (alternativa gratuita a iMyFone AnyTo).

Levanta un mapa en http://127.0.0.1:8765 y mueve la ubicación del iPhone, por cable o por Wi‑Fi:
teletransporte, rutas por puntos o siguiendo calles a velocidad constante, y joystick.
La misma página se puede abrir desde el iPhone (misma Wi‑Fi) con un PIN o el código QR del PC.

Cómo habla con el iPhone (pymobiledevice3):
- iOS < 17: servicio de desarrollador `com.apple.dt.simulatelocation` sobre lockdown (cable).
- iOS 17+ por cable: canal DVT `LocationSimulation` a través de un túnel RSD sin administrador
  (desde iOS 17.4; para 17.0–17.3 hay que tener abierto `tunel_admin.bat`).
- iOS 17+ por Wi‑Fi: el mismo canal DVT, pero el túnel va sobre RemotePairing (lo que usa Xcode para
  depurar sin cable). El emparejamiento RemotePairing se crea solo la primera vez que se conecta por cable.
En iOS 17+ la ubicación simulada vive mientras el canal esté abierto, por eso este servidor
mantiene la conexión abierta todo el tiempo (y reconecta solo si se cae).

Uso:  python servidor.py            (abre el navegador solo)
      python servidor.py --sin-navegador --puerto 8765 [--solo-pc]
"""

import argparse
import asyncio
import base64
import bisect
import hashlib
import heapq
import hmac
import inspect
import ipaddress
import json
import logging
import math
import os
import random
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
import zlib
from collections import Counter, defaultdict
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

RAIZ = Path(__file__).resolve().parent
CONGELADO = getattr(sys, "frozen", False)  # ejecutable generado con PyInstaller
# web/ va junto al código (dentro del ejecutable si está empaquetado); los datos del usuario, en
# %LOCALAPPDATA%\GPSLibre cuando está instalado, porque Archivos de programa no se puede escribir.
RECURSOS = Path(getattr(sys, "_MEIPASS", RAIZ))
if not CONGELADO:
    DATOS = RAIZ
elif sys.platform == "darwin":
    DATOS = Path.home() / "Library" / "Application Support" / "GPSLibre"
elif sys.platform == "win32":
    DATOS = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "GPSLibre"
else:
    DATOS = Path.home() / ".gpslibre"
DATOS.mkdir(parents=True, exist_ok=True)

for _flujo in (sys.stdout, sys.stderr):
    if _flujo is not None and hasattr(_flujo, "reconfigure"):
        _flujo.reconfigure(errors="replace")  # una consola sin UTF-8 no debe tumbar el programa


def _preparar_openssl_para_wifi() -> list:
    """El túnel por Wi‑Fi (TCP con PSK) usa sslpsk_pmd3 en Python < 3.13, y su módulo nativo busca
    `libssl-1_1-x64.dll`. Python 3.11 trae esa misma DLL como `libssl-1_1.dll`: se copia con el nombre
    esperado a ./dll y se registran ambas carpetas. Tiene que ocurrir antes de importar pymobiledevice3.
    El ejecutable ya lleva la DLL renombrada junto a las demás (ver empaquetar/gpslibre.spec)."""
    if sys.platform != "win32" or sys.version_info >= (3, 13) or CONGELADO:
        return []
    carpeta_python = Path(sys.base_prefix) / "DLLs"
    carpeta_propia = RAIZ / "dll"
    destino = carpeta_propia / "libssl-1_1-x64.dll"
    origen = carpeta_python / "libssl-1_1.dll"
    if not destino.exists() and origen.exists():
        carpeta_propia.mkdir(exist_ok=True)
        shutil.copy2(origen, destino)
    return [os.add_dll_directory(str(c)) for c in (carpeta_propia, carpeta_python) if c.is_dir()]


_DIRECTORIOS_DLL = _preparar_openssl_para_wifi()  # se conservan: cerrarlos quitaría las carpetas

import ifaddr  # noqa: E402
import requests  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from packaging.version import Version  # noqa: E402
from pydantic import BaseModel, Field, field_validator  # noqa: E402

from pymobiledevice3.exceptions import (  # noqa: E402
    AlreadyMountedError,
    DeveloperModeIsNotEnabledError,
    DeviceNotFoundError,
    NoDeviceConnectedError,
    PairingDialogResponsePendingError,
    PasswordRequiredError,
    PyMobileDevice3Exception,
    RemotePairingCompletedError,
)
from pymobiledevice3.lockdown import create_using_usbmux  # noqa: E402
from pymobiledevice3.pair_records import iter_remote_paired_identifiers  # noqa: E402
from pymobiledevice3.remote import userspace_tunnel  # noqa: E402
from pymobiledevice3.remote.rsd_tunnel import PreferredRsdTunnel  # noqa: E402
from pymobiledevice3.remote.tunnel_service import (  # noqa: E402
    RemotePairingLockdownService,
    create_core_device_tunnel_service_using_remotepairing,
    get_remote_pairing_tunnel_services,
)
from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel  # noqa: E402
from pymobiledevice3.services.amfi import AmfiService  # noqa: E402
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider  # noqa: E402
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation  # noqa: E402
from pymobiledevice3.services.mobile_image_mounter import auto_mount  # noqa: E402
from pymobiledevice3.services.simulate_location import DtSimulateLocation  # noqa: E402
from pymobiledevice3.tunneld.api import get_tunneld_devices  # noqa: E402

PUERTO = 8765
TICK_RUTA_S = 1.0
TICK_JOYSTICK_S = 0.5
JOYSTICK_SIN_SENAL_S = 1.5
ESPERA_DESBLOQUEO_S = 40
ESPERA_BONJOUR_S = 5
PUERTO_REMOTEPAIRING_HABITUAL = 49152
PUERTO_USBMUXD_WINDOWS = 27015
UBICACION_REAL_CACHE_S = 300
RADIO_TIERRA_M = 6_371_000.0
AGENTE_HTTP = "GPSLibre/1.0 (simulador personal)"
ARCHIVO_RECORDADO = DATOS / "iphone_recordado.json"
ARCHIVO_GUARDADOS = DATOS / "guardados.json"
ARCHIVO_CONFIG = DATOS / "config.json"
ARCHIVO_LICENCIA = DATOS / "licencia.json"
COOKIE_SESION = "gpslibre_sesion"
# Movimientos gratis antes de pedir la licencia (un movimiento = un teletransporte, una ruta o una sesión de joystick).
LIMITE_MOVIMIENTOS_GRATIS = 2
# Llave pública para verificar las claves de licencia (la privada solo está en empaquetar/licencia_privada.pem).
CLAVE_PUBLICA_LICENCIA = "NClfWrRdw5YFZ5AvyEi9Vd4znf-B3-ojU3rRWoK-lMM"
# Checkouts de Lemon Squeezy (tienda gpslibre.lemonsqueezy.com).
CHECKOUT_MENSUAL = "https://gpslibre.lemonsqueezy.com/checkout/buy/936174fe-7d32-4a27-b715-26741f958de5"
CHECKOUT_TRIMESTRAL = "https://gpslibre.lemonsqueezy.com/checkout/buy/8c0f67db-c39d-4598-a681-8514140e49d1"
CHECKOUT_ANUAL = "https://gpslibre.lemonsqueezy.com/checkout/buy/8450f2a7-615a-4464-a355-477a77bf454b"
# Enlace del botón «Comprar ahora» (por defecto, el plan destacado).
URL_COMPRA = CHECKOUT_ANUAL
PRECIOS = [
    {"plan": "mensual", "nombre": "Mensual", "precio": "$9,99", "url": CHECKOUT_MENSUAL},
    {"plan": "trimestral", "nombre": "Trimestral", "precio": "$19,99", "url": CHECKOUT_TRIMESTRAL},
    {"plan": "anual", "nombre": "Anual", "precio": "$29,99", "destacado": True, "url": CHECKOUT_ANUAL},
]
# Descuento de bienvenida (se crea como cupón en Lemon Squeezy y se asocia al correo del cliente).
DESCUENTO_PRIMERA = "20% de descuento en tu primera compra"
SERVIDOR_EN_RED = True  # False con --solo-pc
servidor_uvicorn = None  # se asigna al arrancar; lo usa el botón «Cerrar GPS Libre»

_manejadores_log: list[logging.Handler] = []
if sys.stderr is not None:  # el programa instalado se abre sin consola: no hay dónde escribir
    _manejadores_log.append(logging.StreamHandler())
if CONGELADO:
    _manejadores_log.append(logging.FileHandler(DATOS / "gpslibre.log", encoding="utf-8"))
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=_manejadores_log
)
logger = logging.getLogger("gps-libre")


class ErrorGuiado(Exception):
    """Error con un mensaje pensado para mostrarse tal cual en la pantalla."""


# ---------------------------------------------------------------- geometría


def distancia_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * RADIO_TIERRA_M * math.asin(math.sqrt(h))


def rumbo_grados(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    x = math.sin(lon2 - lon1) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def desplazar(p: tuple[float, float], rumbo: float, metros: float) -> tuple[float, float]:
    lat1, lon1 = math.radians(p[0]), math.radians(p[1])
    theta, d = math.radians(rumbo), metros / RADIO_TIERRA_M
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(theta))
    lon2 = lon1 + math.atan2(
        math.sin(theta) * math.sin(d) * math.cos(lat1), math.cos(d) - math.sin(lat1) * math.sin(lat2)
    )
    return math.degrees(lat2), (math.degrees(lon2) + 540) % 360 - 180


class Ruta:
    """Polilínea recorrible por distancia: `punto_en(d)` da la posición a d metros del inicio."""

    def __init__(self, puntos: list[tuple[float, float]]):
        if len(puntos) < 2:
            raise ErrorGuiado("La ruta necesita al menos 2 puntos.")
        for lat, lon in puntos:
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ErrorGuiado(f"Coordenada fuera de rango: {lat}, {lon}")
        self.puntos = [tuple(p) for p in puntos]
        self.acumulado = [0.0]
        for a, b in zip(self.puntos, self.puntos[1:]):
            self.acumulado.append(self.acumulado[-1] + distancia_m(a, b))

    @property
    def total_m(self) -> float:
        return self.acumulado[-1]

    def extender(self, puntos) -> None:
        """Alarga la ruta (recorridos de zona que se siguen calculando mientras el iPhone avanza)."""
        for p in puntos:
            p = tuple(p)
            self.acumulado.append(self.acumulado[-1] + distancia_m(self.puntos[-1], p))
            self.puntos.append(p)

    def punto_en(self, d: float) -> tuple[tuple[float, float], float]:
        d = max(0.0, min(d, self.total_m))
        i = min(bisect.bisect_right(self.acumulado, d) - 1, len(self.puntos) - 2)
        tramo = self.acumulado[i + 1] - self.acumulado[i]
        f = 0.0 if tramo == 0 else (d - self.acumulado[i]) / tramo
        a, b = self.puntos[i], self.puntos[i + 1]
        return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f), rumbo_grados(a, b)


# ---------------------------------------------------------------- recorrer las calles de una zona

# Servidores públicos de Overpass (calles de OpenStreetMap). El principal a veces se satura (504/429).
SERVIDORES_OVERPASS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
_VIAS_CARRO = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary",
    "secondary_link", "tertiary", "tertiary_link", "unclassified", "residential", "living_street",
}
_VIAS_SIN_AUTOPISTA = _VIAS_CARRO - {"motorway", "motorway_link", "trunk", "trunk_link"}
TIPOS_DE_VIA = {
    "car": _VIAS_CARRO,
    "bike": _VIAS_SIN_AUTOPISTA | {"cycleway", "pedestrian"},
    "foot": _VIAS_SIN_AUTOPISTA | {"pedestrian", "footway", "steps"},
}
TAM_SECTOR_M = 1500          # una zona grande se descarga y calcula por sectores de este lado
MARGEN_SECTOR_M = 150        # calles de alrededor del sector, para conectar tramos a través del borde
MAX_SECTORES = 800           # unos 1.800 km²: cabe una ciudad grande entera, no un país
DESCARGAS_ADELANTADAS = 2    # sectores que se descargan por adelantado mientras se calcula el actual
REINTENTOS_SECTOR = 3
MAX_PUNTOS_POR_CONSULTA = 30_000


def punto_en_poligono(p: tuple[float, float], vertices: list[tuple[float, float]]) -> bool:
    dentro = False
    j = len(vertices) - 1
    for i in range(len(vertices)):
        (lat_i, lon_i), (lat_j, lon_j) = vertices[i], vertices[j]
        if (lat_i > p[0]) != (lat_j > p[0]) and p[1] < (lon_j - lon_i) * (p[0] - lat_i) / (lat_j - lat_i) + lon_i:
            dentro = not dentro
        j = i
    return dentro


def recortar_poligono(poligono, sur: float, oeste: float, norte: float, este: float) -> list[tuple[float, float]]:
    """Parte del polígono dentro del rectángulo (Sutherland–Hodgman; vale también para polígonos cóncavos)."""

    def cruce_lat(limite):
        return lambda a, b: (limite, a[1] + (b[1] - a[1]) * (limite - a[0]) / (b[0] - a[0]))

    def cruce_lon(limite):
        return lambda a, b: (a[0] + (b[0] - a[0]) * (limite - a[1]) / (b[1] - a[1]), limite)

    bordes = (
        (lambda p: p[0] >= sur, cruce_lat(sur)),
        (lambda p: p[0] <= norte, cruce_lat(norte)),
        (lambda p: p[1] >= oeste, cruce_lon(oeste)),
        (lambda p: p[1] <= este, cruce_lon(este)),
    )
    puntos = list(poligono)
    for dentro, cruce in bordes:
        entrada, puntos = puntos, []
        for i, actual in enumerate(entrada):
            previo = entrada[i - 1]
            if dentro(actual):
                if not dentro(previo):
                    puntos.append(cruce(previo, actual))
                puntos.append(actual)
            elif dentro(previo):
                puntos.append(cruce(previo, actual))
        if not puntos:
            break
    return puntos


def dividir_en_sectores(poligono, inicio) -> list[dict]:
    """Sectores cuadrados que cubren la zona, en zigzag y empezando por la esquina más cercana al iPhone."""
    lats, lons = [p[0] for p in poligono], [p[1] for p in poligono]
    sur, norte, oeste, este = min(lats), max(lats), min(lons), max(lons)
    paso_lat = TAM_SECTOR_M / 110_574
    paso_lon = TAM_SECTOR_M / (111_320 * math.cos(math.radians((sur + norte) / 2)))
    filas = max(1, math.ceil((norte - sur) / paso_lat))
    columnas = max(1, math.ceil((este - oeste) / paso_lon))
    if filas * columnas > MAX_SECTORES * 3:
        raise ErrorGuiado("La zona es demasiado grande: marca como mucho una ciudad entera.")
    desde_el_norte = inicio is not None and abs(inicio[0] - norte) < abs(inicio[0] - sur)
    desde_el_este = inicio is not None and abs(inicio[1] - este) < abs(inicio[1] - oeste)
    sectores = []
    for f in range(filas):
        fila = filas - 1 - f if desde_el_norte else f
        columnas_en_orden = range(columnas - 1, -1, -1) if (f % 2 == 0) == desde_el_este else range(columnas)
        for c in columnas_en_orden:
            caja = (sur + fila * paso_lat, oeste + c * paso_lon, sur + (fila + 1) * paso_lat, oeste + (c + 1) * paso_lon)
            recorte = recortar_poligono(poligono, *caja)
            if len(recorte) >= 3:
                sectores.append({"caja": caja, "poligono": recorte})
    if len(sectores) > MAX_SECTORES:
        raise ErrorGuiado(f"La zona es demasiado grande ({len(sectores)} sectores): marca como mucho una ciudad entera.")
    return sectores


def _descargar_calles(consulta: str) -> list[dict]:
    """Pide las calles a Overpass; si un servidor está saturado o no responde, prueba el siguiente."""
    ultimo_error: Optional[Exception] = None
    for url in SERVIDORES_OVERPASS:
        try:
            respuesta = requests.post(url, data={"data": consulta}, headers={"User-Agent": AGENTE_HTTP}, timeout=150)
            respuesta.raise_for_status()
            return respuesta.json()["elements"]
        except (requests.RequestException, ValueError, KeyError) as e:
            logger.warning("Overpass %s falló: %s", url, e)
            ultimo_error = e
    raise ErrorGuiado(
        "No se pudieron descargar las calles de la zona: los servidores de OpenStreetMap están saturados. "
        f"Prueba de nuevo en un minuto. ({ultimo_error})"
    ) from ultimo_error


class RedDeCalles:
    """Grafo de las calles de OpenStreetMap que tocan la zona: un tramo por cada par de nodos seguidos de una vía."""

    def __init__(self, poligono: list[tuple[float, float]], perfil: str):
        # Se piden las calles del rectángulo del sector con un margen, para poder conectar tramos a través del
        # borde; solo cuentan como «del sector» las que tienen el punto medio dentro de su polígono.
        lats, lons = [p[0] for p in poligono], [p[1] for p in poligono]
        margen_lat = MARGEN_SECTOR_M / 110_574
        margen_lon = MARGEN_SECTOR_M / (111_320 * math.cos(math.radians(sum(lats) / len(lats))))
        caja = f"{min(lats) - margen_lat:.6f},{min(lons) - margen_lon:.6f},{max(lats) + margen_lat:.6f},{max(lons) + margen_lon:.6f}"
        tipos = "|".join(sorted(TIPOS_DE_VIA[perfil]))
        consulta = (
            f'[out:json][timeout:90];way({caja})["highway"~"^({tipos})$"]'
            '["area"!="yes"]["access"!~"^(private|no)$"];out body qt;>;out skel qt;'
        )
        elementos = _descargar_calles(consulta)

        self.coord = {e["id"]: (e["lat"], e["lon"]) for e in elementos if e["type"] == "node"}
        self.aristas: list[tuple[int, int, float, int]] = []  # (nodo, nodo, metros, id de la vía)
        self.dentro: list[bool] = []  # tramo dentro de la zona; los de fuera solo sirven para conectar
        self.ady: dict[int, list[tuple[int, int]]] = defaultdict(list)  # nodo -> [(vecino, índice del tramo)]
        for via in elementos:
            if via["type"] != "way" or via.get("tags", {}).get("footway") in ("sidewalk", "crossing"):
                continue  # las aceras y cruces mapeados aparte duplicarían cada calle
            refs = [n for n in via.get("nodes", []) if n in self.coord]
            for u, v in zip(refs, refs[1:]):
                if u == v:
                    continue
                a, b = self.coord[u], self.coord[v]
                indice = len(self.aristas)
                self.aristas.append((u, v, distancia_m(a, b), via["id"]))
                self.dentro.append(punto_en_poligono(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), poligono))
                self.ady[u].append((v, indice))
                self.ady[v].append((u, indice))
        self.vacia = not any(self.dentro)  # un parque, un cerro o un embalse: sector sin calles

    def elegir_calles(self, porcentaje: int, semilla: int) -> tuple[set[int], float, float]:
        """Tramos a recorrer: todos los de la zona, o calles completas al azar hasta sumar el porcentaje."""
        por_via: dict[int, list[int]] = defaultdict(list)
        for i, (_, _, _, via) in enumerate(self.aristas):
            if self.dentro[i]:
                por_via[via].append(i)
        largo = {via: sum(self.aristas[i][2] for i in tramos) for via, tramos in por_via.items()}
        total = sum(largo.values())
        vias = list(por_via)
        if porcentaje < 100:
            random.Random(semilla).shuffle(vias)  # la misma zona y porcentaje dan siempre las mismas calles
        elegidas: set[int] = set()
        cubierto = 0.0
        for via in vias:
            if cubierto >= total * porcentaje / 100:
                break
            elegidas.update(por_via[via])
            cubierto += largo[via]
        return elegidas, total, cubierto

    def planificar(self, pendientes: set[int], inicio: Optional[tuple[float, float]]) -> list[tuple[float, float]]:
        """Recorrido que pasa por todos los tramos pendientes: sigue por un tramo pendiente pegado mientras haya y,
        si no, va por el camino más corto hasta el nodo más cercano que aún tenga tramos pendientes."""
        pendientes = set(pendientes)
        pendientes_por_nodo: Counter = Counter()
        for i in pendientes:
            pendientes_por_nodo[self.aristas[i][0]] += 1
            pendientes_por_nodo[self.aristas[i][1]] += 1

        def nodos_con_pendientes():
            return (n for n, cuantos in pendientes_por_nodo.items() if cuantos > 0)

        if inicio is None:
            actual = self.aristas[next(iter(pendientes))][0]
        else:
            # La calle más cercana a donde terminó el sector anterior: desde ahí se llega por calles a lo pendiente.
            actual = min(self.ady, key=lambda n: distancia_m(self.coord[n], inicio))
        recorrido = [actual]
        while pendientes:
            pegado = next(((v, i) for v, i in self.ady[actual] if i in pendientes), None)
            if pegado is not None:
                actual, tramo = pegado
                pendientes.discard(tramo)
                pendientes_por_nodo[self.aristas[tramo][0]] -= 1
                pendientes_por_nodo[self.aristas[tramo][1]] -= 1
                recorrido.append(actual)
                continue
            camino = self._camino_a_pendiente_mas_cercano(actual, pendientes_por_nodo)
            if camino is None:
                # Calles sueltas, sin conexión con esta parte en los datos: salto en línea recta a la más cercana.
                origen = self.coord[actual]
                actual = min(nodos_con_pendientes(), key=lambda n: distancia_m(self.coord[n], origen))
                recorrido.append(actual)
            else:
                recorrido.extend(camino[1:])
                actual = camino[-1]
        return [self.coord[n] for n in recorrido]

    def _camino_a_pendiente_mas_cercano(self, origen: int, pendientes_por_nodo: Counter) -> Optional[list[int]]:
        """Dijkstra que se detiene en el primer nodo con tramos pendientes."""
        distancia = {origen: 0.0}
        previo: dict[int, int] = {}
        cola = [(0.0, origen)]
        while cola:
            d, nodo = heapq.heappop(cola)
            if d > distancia[nodo]:
                continue
            if nodo != origen and pendientes_por_nodo[nodo] > 0:
                camino = [nodo]
                while camino[-1] != origen:
                    camino.append(previo[camino[-1]])
                return camino[::-1]
            for vecino, tramo in self.ady[nodo]:
                nueva = d + self.aristas[tramo][2]
                if nueva < distancia.get(vecino, math.inf):
                    distancia[vecino] = nueva
                    previo[vecino] = nodo
                    heapq.heappush(cola, (nueva, vecino))
        return None


class TrabajoZona:
    """Recorrido por las calles de una zona (hasta una ciudad entera), calculado sector a sector en segundo plano.

    Cada sector se descarga (los siguientes, por adelantado) y se planifica empezando donde terminó el anterior.
    Los puntos se van añadiendo a `puntos`: el mapa los dibuja según llegan y la ruta puede arrancar ya.
    """

    def __init__(self, poligono, porcentaje: int, perfil: str, inicio) -> None:
        self.id = secrets.token_hex(6)
        self.porcentaje, self.perfil = porcentaje, perfil
        self.inicio = tuple(inicio) if inicio else None
        self.sectores = dividir_en_sectores([tuple(p) for p in poligono], self.inicio)
        self.estado_sectores = ["pendiente"] * len(self.sectores)  # pendiente, descargando, lista, vacia o error
        self.puntos: list[tuple[float, float]] = []
        self.calles_m = self.cubiertas_m = self.recorrido_m = 0.0
        self.terminado = False
        self.error: Optional[str] = None
        self._tarea = asyncio.create_task(self._calcular())

    def cancelar(self) -> None:
        self._tarea.cancel()

    async def _calcular(self) -> None:
        inicio_t = time.monotonic()
        ultimo = self.inicio
        descargas: dict[int, asyncio.Task] = {}
        try:
            for i in range(len(self.sectores)):
                for j in range(i, min(i + DESCARGAS_ADELANTADAS + 1, len(self.sectores))):
                    if j not in descargas:
                        descargas[j] = asyncio.create_task(self._descargar_sector(j))
                self.estado_sectores[i] = "descargando"
                red = await descargas.pop(i)
                if red is None or red.vacia:
                    self.estado_sectores[i] = "error" if red is None else "vacia"
                    continue
                semilla = zlib.crc32(json.dumps([self.sectores[i]["caja"], self.porcentaje, self.perfil]).encode())
                elegidas, total, cubierto = red.elegir_calles(self.porcentaje, semilla)
                self.calles_m += total
                if elegidas:
                    puntos = await asyncio.to_thread(red.planificar, elegidas, ultimo)
                    if self.puntos:
                        self.recorrido_m += distancia_m(self.puntos[-1], puntos[0])
                    self.recorrido_m += sum(distancia_m(a, b) for a, b in zip(puntos, puntos[1:]))
                    self.puntos.extend(puntos)
                    self.cubiertas_m += cubierto
                    ultimo = puntos[-1]
                self.estado_sectores[i] = "lista"
            logger.info(
                "zona: %d sectores, %.1f km de calles, %.1f km elegidos (%d %%), recorrido de %.1f km, %.0f s",
                len(self.sectores), self.calles_m / 1000, self.cubiertas_m / 1000, self.porcentaje,
                self.recorrido_m / 1000, time.monotonic() - inicio_t,
            )
        except asyncio.CancelledError:
            for tarea in descargas.values():
                tarea.cancel()
            raise
        except Exception as e:
            logger.exception("falló el cálculo de la zona")
            self.error = str(e)
        finally:
            self.terminado = True

    async def _descargar_sector(self, i: int) -> Optional["RedDeCalles"]:
        for intento in range(1, REINTENTOS_SECTOR + 1):
            try:
                return await asyncio.to_thread(RedDeCalles, self.sectores[i]["poligono"], self.perfil)
            except ErrorGuiado as e:
                logger.warning("sector %d: intento %d de %d falló (%s)", i, intento, REINTENTOS_SECTOR, e)
                if intento < REINTENTOS_SECTOR:
                    await asyncio.sleep(10 * intento)
        return None

    def resumen(self, desde: int = 0) -> dict:
        return {
            "id": self.id,
            "terminado": self.terminado,
            "error": self.error,
            "celdas_total": len(self.sectores),
            "celdas_listas": sum(1 for e in self.estado_sectores if e not in ("pendiente", "descargando")),
            "estado_celdas": self.estado_sectores,
            "calles_m": self.calles_m,
            "cubiertas_m": self.cubiertas_m,
            "recorrido_m": self.recorrido_m,
            "total_puntos": len(self.puntos),
            "puntos": self.puntos[desde : desde + MAX_PUNTOS_POR_CONSULTA],
        }


# ---------------------------------------------------------------- memoria local, red y ubicación del PC


def _b64d(texto: str) -> bytes:
    """Decodifica base64url tolerando que falte el relleno '=' (así viajan la llave y las claves de licencia)."""
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def _leer_json(archivo: Path) -> dict:
    try:
        datos = json.loads(archivo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


def leer_recordado() -> dict:
    """Último iPhone conectado (UDID, nombre, IP y puerto de Wi‑Fi) para reconectar sin preguntar."""
    return _leer_json(ARCHIVO_RECORDADO)


def guardar_recordado(**cambios) -> None:
    datos = {**leer_recordado(), **{k: v for k, v in cambios.items() if v is not None}}
    try:
        ARCHIVO_RECORDADO.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        logger.warning("no se pudo guardar %s", ARCHIVO_RECORDADO, exc_info=True)


def leer_guardados() -> dict:
    """Rutas y favoritos guardados por el usuario (en disco, así sirven desde cualquier navegador)."""
    datos = _leer_json(ARCHIVO_GUARDADOS)
    return {"rutas": datos.get("rutas", []), "favoritos": datos.get("favoritos", [])}


_ADAPTADORES_VIRTUALES = ("vethernet", "virtualbox", "vmware", "tap-windows", "hyper-v", "loopback", "bluetooth", "wsl")
_cache_ips: dict = {}


def _adaptadores_ipv4() -> list[tuple[str, str]]:
    """(nombre del adaptador, IPv4) de este PC, cacheado 30 s porque se consulta en cada petición."""
    if not _cache_ips or time.monotonic() - _cache_ips["ts"] > 30:
        pares = [
            ((adaptador.nice_name or "").lower(), ip.ip)
            for adaptador in ifaddr.get_adapters()
            for ip in adaptador.ips
            if isinstance(ip.ip, str) and not ip.ip.startswith(("127.", "169.254."))
        ]
        _cache_ips.update(pares=pares, ts=time.monotonic())
    return _cache_ips["pares"]


def ips_locales() -> list[str]:
    return [ip for _, ip in _adaptadores_ipv4()]


def ips_para_celular() -> list[str]:
    """IPs privadas en adaptadores reales (Wi‑Fi o Ethernet): por donde entra el celular."""
    return [
        ip
        for nombre, ip in _adaptadores_ipv4()
        if ipaddress.ip_address(ip).is_private and not any(v in nombre for v in _ADAPTADORES_VIRTUALES)
    ]


def _usbmux_disponible() -> bool:
    """En Windows el controlador de Apple (iTunes o «Dispositivos Apple») escucha en 127.0.0.1:27015."""
    if sys.platform != "win32":
        return True
    try:
        with socket.create_connection(("127.0.0.1", PUERTO_USBMUXD_WINDOWS), timeout=0.5):
            return True
    except OSError:
        return False


_SCRIPT_UBICACION_WINDOWS = (
    "Add-Type -AssemblyName System.Device;"
    "$w = New-Object System.Device.Location.GeoCoordinateWatcher([System.Device.Location.GeoPositionAccuracy]::High);"
    "$null = $w.TryStart($false, [TimeSpan]::FromSeconds(10));"
    "$t = 0; while ($w.Status -ne 'Ready' -and $t -lt 100) { Start-Sleep -Milliseconds 100; $t++ };"
    "$c = $w.Position.Location; $w.Stop();"
    "@{ lat = $c.Latitude; lon = $c.Longitude; precision = $c.HorizontalAccuracy } | ConvertTo-Json -Compress"
)
_cache_ubicacion_real: dict = {}


def _leer_ubicacion_windows() -> Optional[dict]:
    """Ubicación del PC según el servicio de ubicación de Windows (Wi‑Fi cercanas), sin pedir permiso al navegador."""
    if sys.platform != "win32":
        return None
    try:
        salida = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT_UBICACION_WINDOWS],
            capture_output=True,
            text=True,
            timeout=25,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout.strip()
        datos = json.loads(salida.splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        logger.debug("no se pudo leer la ubicación de Windows", exc_info=True)
        return None
    lat, lon = datos.get("lat"), datos.get("lon")
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (lat, lon)):
        return None
    return {"lat": lat, "lon": lon, "precision_m": datos.get("precision"), "fuente": "Windows"}


# ---------------------------------------------------------------- conexión con el iPhone


class SimuladorDemo:
    """Sustituto del iPhone para probar la interfaz sin teléfono: solo registra lo que enviaría."""

    async def set(self, latitude: float, longitude: float) -> None:
        logger.info("[demo] ubicación -> %.6f, %.6f", latitude, longitude)

    async def clear(self) -> None:
        logger.info("[demo] GPS real restaurado")


class TunelWifi(UserspaceRsdTunnel):
    """El túnel sin administrador de pymobiledevice3, pero sobre un servicio RemotePairing por Wi‑Fi.

    pymobiledevice3 solo recurre a RemotePairing en este túnel como respaldo para iOS 17.0–17.3; aquí se
    le entrega directamente el servicio ya conectado. Depende de un detalle interno
    (`_create_no_root_tunnel_provider`), por eso requirements.txt fija pymobiledevice3 < 12.
    """

    def __init__(self, servicio) -> None:
        super().__init__(serial=servicio.remote_identifier, autopair=False)
        self._servicio = servicio

    async def _aopen_locked(self):
        original = userspace_tunnel._create_no_root_tunnel_provider

        async def proveedor_wifi(*_args, **_kwargs):
            return self._servicio, None

        userspace_tunnel._create_no_root_tunnel_provider = proveedor_wifi
        try:
            return await super()._aopen_locked()
        finally:
            userspace_tunnel._create_no_root_tunnel_provider = original


async def _cerrar_seguro(objeto) -> None:
    try:
        resultado = objeto.close()
        if inspect.isawaitable(resultado):
            await resultado
    except Exception:
        logger.debug("fallo al cerrar %r", objeto, exc_info=True)


class Iphone:
    def __init__(self) -> None:
        self._pila: Optional[AsyncExitStack] = None
        self._simulador = None
        self._candado = asyncio.Lock()
        self._ultima_conexion: Optional[dict] = None  # cómo reconectar si se cae
        self._ip_wifi: Optional[str] = None
        self._puerto_wifi: Optional[int] = None
        self.conectando = False
        self.info: dict = {}

    @property
    def conectado(self) -> bool:
        return self._simulador is not None

    async def conectar(self, demo: bool = False, via: str = "usb", ip: Optional[str] = None) -> dict:
        if self.conectando:
            raise ErrorGuiado("Ya estoy conectando, espera un momento.")
        self.conectando = True
        try:
            await self.desconectar()
            if demo:
                self._simulador = SimuladorDemo()
                self._ultima_conexion = None
                self.info = {"nombre": "iPhone de prueba", "modelo": "demo", "ios": "-", "via": "modo demo"}
                return self.info
            pila = AsyncExitStack()
            try:
                if via == "wifi":
                    await self._abrir_wifi(pila, ip)
                else:
                    await self._abrir_usb(pila)
            except BaseException:
                self._simulador = None
                await pila.aclose()
                raise
            self._pila = pila
            self._ultima_conexion = {"via": via, "ip": self._ip_wifi if via == "wifi" else None}
            return self.info
        except ErrorGuiado:
            raise
        except (NoDeviceConnectedError, DeviceNotFoundError) as e:
            raise ErrorGuiado(
                "No veo ningún iPhone por cable. Conéctalo por USB, desbloquéalo y vuelve a pulsar Conectar."
            ) from e
        except (PairingDialogResponsePendingError, PasswordRequiredError) as e:
            raise ErrorGuiado(
                "Desbloquea el iPhone y pulsa «Confiar» en el aviso «¿Confiar en este ordenador?». Luego pulsa Conectar."
            ) from e
        except DeveloperModeIsNotEnabledError as e:
            raise ErrorGuiado(_MENSAJE_MODO_DESARROLLADOR) from e
        except Exception as e:
            logger.exception("fallo al conectar")
            raise ErrorGuiado(f"No se pudo conectar con el iPhone: {type(e).__name__}: {e}") from e
        finally:
            self.conectando = False

    # ---- por cable

    async def _abrir_usb(self, pila: AsyncExitStack) -> None:
        if not await asyncio.to_thread(_usbmux_disponible):
            raise ErrorGuiado(_MENSAJE_FALTA_CONTROLADOR_APPLE)
        lockdown = await create_using_usbmux(autopair=True, pair_timeout=90, connection_type="USB")
        pila.push_async_callback(_cerrar_seguro, lockdown)
        valores = lockdown.all_values
        version = Version(lockdown.product_version)
        udid = valores.get("UniqueDeviceID")

        await self._comprobar_modo_desarrollador(lockdown, version, revelar=True)
        await self._montar_imagen(lockdown)

        if version < Version("17.0"):
            self._simulador = DtSimulateLocation(lockdown)
            via = "cable"
        else:
            await self._emparejar_para_wifi(lockdown, udid)
            rsd, via = await self._abrir_rsd(pila, udid, version)
            self._simulador = await self._abrir_simulacion_dvt(pila, rsd)

        self.info = {
            "nombre": valores.get("DeviceName", "iPhone"),
            "modelo": valores.get("ProductType", ""),
            "ios": str(lockdown.product_version),
            "via": via,
            "ip": None,
        }
        guardar_recordado(udid=udid, nombre=self.info["nombre"], modelo=self.info["modelo"])
        logger.info("conectado: %s", self.info)

    async def _emparejar_para_wifi(self, lockdown, udid: Optional[str]) -> None:
        """Crea una sola vez, sin avisos en el iPhone, el emparejamiento RemotePairing que usa el Wi‑Fi."""
        if udid in set(iter_remote_paired_identifiers()):
            return
        servicio = None
        try:
            servicio = await RemotePairingLockdownService.create(lockdown)
            try:
                await servicio.connect(autopair=True)
            except RemotePairingCompletedError:
                pass  # el iPhone cierra la conexión al terminar de emparejar
            logger.info("iPhone emparejado para Wi‑Fi")
        except Exception:
            logger.warning("no se pudo preparar la conexión por Wi‑Fi", exc_info=True)
        finally:
            if servicio is not None:
                await _cerrar_seguro(servicio)

    async def _abrir_rsd(self, pila: AsyncExitStack, udid: Optional[str], version: Version):
        try:
            rsd = await pila.enter_async_context(PreferredRsdTunnel(serial=udid))
            return rsd, "cable"
        except Exception as error_tunel:
            logger.warning("túnel sin administrador falló (%s); pruebo con tunneld", error_tunel)
            try:
                rsds = await get_tunneld_devices()
            except Exception:
                rsds = []
            elegido = next((r for r in rsds if udid is None or r.udid == udid), None)
            for r in rsds:
                if r is not elegido:
                    await _cerrar_seguro(r)
            if elegido is not None:
                pila.push_async_callback(_cerrar_seguro, elegido)
                return elegido, "cable (tunneld)"
            consejo = (
                "Tu iOS es 17.0–17.3: abre «tunel_admin.bat» con clic derecho → Ejecutar como administrador, "
                "déjalo abierto y pulsa Conectar otra vez (o actualiza el iPhone)."
                if version < Version("17.4")
                else "Mantén el iPhone desbloqueado y conectado por cable, y vuelve a intentarlo."
            )
            raise ErrorGuiado(f"No se pudo abrir el túnel con el iPhone ({error_tunel}). {consejo}") from error_tunel

    # ---- por Wi‑Fi

    async def _abrir_wifi(self, pila: AsyncExitStack, ip: Optional[str]) -> None:
        emparejados = sorted(set(iter_remote_paired_identifiers()))
        if not emparejados:
            raise ErrorGuiado(
                "Primero conecta el iPhone una vez por cable: así queda emparejado para Wi‑Fi "
                "(hace falta iOS 17 o superior)."
            )
        recordado = leer_recordado()
        udid = recordado.get("udid") if recordado.get("udid") in emparejados else None
        inicio = time.monotonic()
        servicio = await self._buscar_iphone_wifi(ip, udid, emparejados, recordado)
        logger.info("Wi‑Fi: iPhone localizado en %s:%s (%.1f s)", self._ip_wifi, self._puerto_wifi, time.monotonic() - inicio)
        try:
            rsd = await pila.enter_async_context(TunelWifi(servicio))
        except BaseException:
            await _cerrar_seguro(servicio)
            raise
        logger.info("Wi‑Fi: túnel abierto (%.1f s)", time.monotonic() - inicio)

        try:
            self._simulador = await self._abrir_simulacion_dvt(pila, rsd)
        except Exception as e:
            # La imagen de desarrollador sigue montada desde la última conexión hasta que el iPhone se
            # reinicia, y comprobarla por Wi‑Fi cuesta hasta 30 s; solo se revisa y monta si falta.
            logger.info("Wi‑Fi: simulación no disponible (%r); reviso la imagen de desarrollador", e)
            await self._comprobar_modo_desarrollador(rsd, Version(rsd.product_version), revelar=False)
            await self._montar_imagen(rsd)
            self._simulador = await self._abrir_simulacion_dvt(pila, rsd)
        logger.info("Wi‑Fi: simulación lista (%.1f s)", time.monotonic() - inicio)

        nombre = await self._leer_valor(rsd, "DeviceName") or recordado.get("nombre") or "iPhone"
        self.info = {
            "nombre": nombre,
            "modelo": recordado.get("modelo", ""),
            "ios": str(rsd.product_version),
            "via": "Wi‑Fi",
            "ip": self._ip_wifi,
        }
        guardar_recordado(
            udid=servicio.remote_identifier, nombre=nombre, ip_wifi=self._ip_wifi, puerto_wifi=self._puerto_wifi
        )
        logger.info("conectado: %s", self.info)

    async def _buscar_iphone_wifi(self, ip: Optional[str], udid: Optional[str], emparejados: list[str], recordado: dict):
        identificadores = [udid] if udid else emparejados

        # 1) Directo a la IP escrita o a la última conocida: evita esperar a Bonjour.
        directa = ip or recordado.get("ip_wifi")
        if directa:
            puerto = recordado.get("puerto_wifi") or PUERTO_REMOTEPAIRING_HABITUAL
            for identificador in identificadores:
                try:
                    servicio = await asyncio.wait_for(
                        create_core_device_tunnel_service_using_remotepairing(
                            identificador, directa, puerto, autopair=False
                        ),
                        8,
                    )
                except Exception as e:
                    logger.debug("RemotePairing directo a %s:%s falló: %r", directa, puerto, e)
                    continue
                self._ip_wifi, self._puerto_wifi = directa, puerto
                return servicio

        # 2) Bonjour: el iPhone anuncia su servicio RemotePairing en la red local.
        logger.info("busco el iPhone en la red Wi‑Fi…")
        servicios = await get_remote_pairing_tunnel_services(bonjour_timeout=ESPERA_BONJOUR_S, udid=udid)
        elegido = next((s for s in servicios if ":" not in s.hostname), servicios[0] if servicios else None)
        for s in servicios:
            if s is not elegido:
                await _cerrar_seguro(s)
        if elegido is not None:
            self._ip_wifi, self._puerto_wifi = elegido.hostname, elegido.port
            return elegido
        raise ErrorGuiado(
            "No encontré el iPhone por Wi‑Fi. Tiene que estar en la misma red que el PC "
            f"(el PC tiene {', '.join(ips_locales()) or 'ninguna red'}), desbloqueado y con la pantalla encendida."
        )

    # ---- común

    @staticmethod
    async def _leer_valor(proveedor, clave: str):
        try:
            return await proveedor.get_value(key=clave)
        except Exception:
            return None

    async def _comprobar_modo_desarrollador(self, proveedor, version: Version, revelar: bool) -> None:
        if version < Version("16.0") or await proveedor.get_developer_mode_status():
            return
        if revelar:
            try:
                await AmfiService(proveedor).reveal_developer_mode_option_in_ui()
            except Exception:
                logger.debug("no se pudo mostrar la opción de Modo desarrollador", exc_info=True)
        raise ErrorGuiado(_MENSAJE_MODO_DESARROLLADOR)

    async def _montar_imagen(self, proveedor) -> None:
        """Monta la imagen de desarrollador. Apple la rechaza con el iPhone bloqueado, así que espera al desbloqueo."""
        limite = time.monotonic() + ESPERA_DESBLOQUEO_S
        while True:
            try:
                await auto_mount(proveedor)
                logger.info("imagen de desarrollador montada")
                return
            except AlreadyMountedError:
                return
            except PyMobileDevice3Exception as e:
                if "DeviceLocked" not in str(e):
                    raise
                if time.monotonic() > limite:
                    raise ErrorGuiado(
                        "El iPhone está bloqueado. Desbloquéalo, déjalo con la pantalla encendida y pulsa Conectar otra vez."
                    ) from e
                logger.info("el iPhone está bloqueado: espero a que lo desbloqueen")
                await asyncio.sleep(2)

    @staticmethod
    async def _abrir_simulacion_dvt(pila: AsyncExitStack, rsd) -> LocationSimulation:
        dvt = await pila.enter_async_context(DvtProvider(rsd))
        return await pila.enter_async_context(LocationSimulation(dvt))

    async def desconectar(self, olvidar: bool = False) -> None:
        self._simulador = None
        self.info = {}
        if olvidar:
            self._ultima_conexion = None
        if self._pila is not None:
            pila, self._pila = self._pila, None
            inicio = time.monotonic()
            try:
                await pila.aclose()
            except Exception:
                logger.debug("fallo al cerrar la conexión", exc_info=True)
            logger.info("conexión cerrada (%.1f s)", time.monotonic() - inicio)

    async def poner(self, lat: float, lon: float) -> None:
        async with self._candado:
            if self._simulador is None:
                raise ErrorGuiado("El programa aún no está conectado al iPhone: pulsa «Conectar iPhone» arriba.")
            try:
                await self._simulador.set(lat, lon)
                return
            except Exception as e:
                logger.warning("fallo al enviar la ubicación (%r); intento reconectar", e)
                ultima = self._ultima_conexion
            if ultima is None:
                await self.desconectar()
                raise ErrorGuiado("Se perdió la conexión con el iPhone. Pulsa Conectar.")
            try:
                await self.conectar(**ultima)
                await self._simulador.set(lat, lon)
            except Exception as e:
                logger.exception("no se pudo reconectar")
                await self.desconectar()
                detalle = str(e) if isinstance(e, ErrorGuiado) else type(e).__name__
                raise ErrorGuiado(f"Se perdió la conexión con el iPhone y no pude reconectar. {detalle}") from e

    async def restaurar(self) -> None:
        async with self._candado:
            if self._simulador is None:
                raise ErrorGuiado("El programa aún no está conectado al iPhone: pulsa «Conectar iPhone» arriba.")
            await self._simulador.clear()


_MENSAJE_MODO_DESARROLLADOR = (
    "Activa el Modo desarrollador en el iPhone: Ajustes → Privacidad y seguridad → Modo desarrollador → "
    "Activar. El iPhone se reinicia; al volver confirma «Activar» y pulsa Conectar otra vez."
)
_MENSAJE_FALTA_CONTROLADOR_APPLE = (
    "Falta el controlador de Apple para el cable: instala «Dispositivos Apple» desde Microsoft Store "
    "(o iTunes), ábrelo una vez y vuelve a pulsar Conectar."
)


# ---------------------------------------------------------------- movimiento


class Motor:
    """Decide dónde está el iPhone en cada instante: quieto, siguiendo una ruta o con joystick."""

    def __init__(self, iphone: Iphone) -> None:
        self.iphone = iphone
        self.posicion: Optional[tuple[float, float]] = None
        self.modo = "quieto"
        self.pausado = False
        self.velocidad_kmh = 5.0
        self.ruta: Optional[Ruta] = None
        self.recorrido_m = 0.0
        self.vueltas = 0
        self.bucle = False
        self.ida_y_vuelta = False
        self.natural = True
        self.joy_rumbo: Optional[float] = None
        self.joy_ultima_senal = 0.0
        self.fin_ruta: Optional[str] = None  # "terminada" o "detenida": cómo acabó la última ruta
        self.trabajo: Optional[TrabajoZona] = None  # recorrido de zona que se sigue calculando mientras avanza
        self._puntos_del_trabajo = 0
        self.esperando_calles = False
        self.error: Optional[dict] = None
        self._tarea: Optional[asyncio.Task] = None

    async def detener(self) -> None:
        tarea, self._tarea = self._tarea, None
        if tarea is not None and not tarea.done() and tarea is not asyncio.current_task():
            tarea.cancel()
            try:
                await tarea
            except (asyncio.CancelledError, Exception):
                pass
        if self.modo == "ruta":
            self.fin_ruta = "detenida"
        self.modo, self.pausado, self.ruta, self.joy_rumbo = "quieto", False, None, None
        self.trabajo, self.esperando_calles = None, False

    async def _enviar(self, pos: tuple[float, float]) -> None:
        await self.iphone.poner(*pos)
        self.posicion = pos

    async def teletransportar(self, lat: float, lon: float) -> None:
        await self.detener()
        await self._enviar((lat, lon))

    async def iniciar_ruta(self, puntos, velocidad_kmh, bucle, ida_y_vuelta, natural, trabajo=None) -> None:
        await self.detener()
        if trabajo is not None:
            # Recorrido de zona que se sigue calculando: arranca con lo que hay y crece con cada sector nuevo.
            if not trabajo.puntos:
                raise ErrorGuiado("Espera a que aparezca el primer tramo de la zona (unos segundos).")
            salida = [tuple(puntos[0])] if puntos and distancia_m(puntos[0], trabajo.puntos[0]) > 1 else []
            puntos = salida + list(trabajo.puntos)
        ruta = Ruta(puntos)
        if ruta.total_m < 1:
            raise ErrorGuiado("La ruta mide menos de 1 metro.")
        await self._enviar(ruta.puntos[0])
        self.ruta, self.recorrido_m, self.vueltas = ruta, 0.0, 0
        self.trabajo, self._puntos_del_trabajo = trabajo, len(trabajo.puntos) if trabajo else 0
        self.velocidad_kmh, self.bucle, self.ida_y_vuelta, self.natural = velocidad_kmh, bucle, ida_y_vuelta, natural
        self.modo, self.error, self.fin_ruta = "ruta", None, None
        self._tarea = asyncio.create_task(self._vigilar(self._bucle_ruta()))

    async def joystick(self, rumbo: Optional[float], velocidad_kmh: Optional[float]) -> None:
        if self.posicion is None:
            raise ErrorGuiado("Primero teletransporta el iPhone a un punto.")
        if velocidad_kmh:
            self.velocidad_kmh = velocidad_kmh
        if self.modo != "joystick":
            await self.detener()
            self.modo, self.error = "joystick", None
            self._tarea = asyncio.create_task(self._vigilar(self._bucle_joystick()))
        self.joy_rumbo = None if rumbo is None else rumbo % 360
        self.joy_ultima_senal = time.monotonic()

    async def restaurar(self) -> None:
        await self.detener()
        await self.iphone.restaurar()
        self.posicion = None

    async def _vigilar(self, bucle) -> None:
        try:
            await bucle
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("el movimiento se detuvo")
            self.error = {"mensaje": str(e), "ts": time.time()}
            self.modo, self.ruta, self.joy_rumbo, self.pausado = "quieto", None, None, False

    async def _bucle_ruta(self) -> None:
        while True:
            await asyncio.sleep(TICK_RUTA_S)
            if self.pausado or self.ruta is None:
                continue
            if self.trabajo is not None and len(self.trabajo.puntos) > self._puntos_del_trabajo:
                self.ruta.extender(self.trabajo.puntos[self._puntos_del_trabajo :])
                self._puntos_del_trabajo = len(self.trabajo.puntos)
            if self.trabajo is not None and not self.trabajo.terminado and self.recorrido_m >= self.ruta.total_m:
                self.esperando_calles = True  # llegó al final de lo calculado: espera al siguiente sector
                continue
            self.esperando_calles = False
            velocidad_ms = self.velocidad_kmh / 3.6
            if self.natural:
                velocidad_ms *= random.uniform(0.85, 1.15)
            self.recorrido_m += velocidad_ms * TICK_RUTA_S

            if self.recorrido_m >= self.ruta.total_m:
                await self._enviar(self.ruta.puntos[-1])
                if self.trabajo is not None and not self.trabajo.terminado:
                    self.recorrido_m = self.ruta.total_m
                    continue
                if not self.bucle:
                    self.modo, self.ruta, self.fin_ruta = "quieto", None, "terminada"
                    return
                if self.ida_y_vuelta:
                    self.ruta = Ruta(list(reversed(self.ruta.puntos)))
                self.recorrido_m, self.vueltas = 0.0, self.vueltas + 1
                continue

            pos, rumbo = self.ruta.punto_en(self.recorrido_m)
            if self.natural:
                pos = desplazar(pos, rumbo + 90, random.uniform(-1.5, 1.5))
            await self._enviar(pos)

    async def _bucle_joystick(self) -> None:
        while True:
            await asyncio.sleep(TICK_JOYSTICK_S)
            sin_senal = time.monotonic() - self.joy_ultima_senal > JOYSTICK_SIN_SENAL_S
            if self.joy_rumbo is None or sin_senal or self.posicion is None:
                continue
            metros = self.velocidad_kmh / 3.6 * TICK_JOYSTICK_S
            await self._enviar(desplazar(self.posicion, self.joy_rumbo, metros))


# ---------------------------------------------------------------- acceso desde el celular


class AccesoCelular:
    """Control desde el celular: la página se abre por la red local con el PIN del PC o con su código QR.

    El propio PC (127.0.0.1) no necesita PIN. Las sesiones se guardan como hash en config.json para que
    el celular siga dentro tras reiniciar el programa; cambiar el PIN las invalida todas.
    """

    INTENTOS_MAX = 5
    BLOQUEO_S = 60
    VIGENCIA_CODIGO_S = 600
    SESIONES_MAX = 20

    def __init__(self) -> None:
        self._datos = _leer_json(ARCHIVO_CONFIG)
        self._codigos: dict[str, float] = {}  # código de un solo uso del QR → cuándo caduca
        self._fallos: dict[str, list[float]] = {}  # IP → momentos de los intentos fallidos recientes
        if not self._datos.get("pin"):
            self._datos.update(pin=self._pin_nuevo(), sesiones=[])
            self._guardar()

    @staticmethod
    def _pin_nuevo() -> str:
        return f"{secrets.randbelow(10**6):06d}"

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def _guardar(self) -> None:
        try:
            ARCHIVO_CONFIG.write_text(json.dumps(self._datos, indent=2), encoding="utf-8")
        except OSError:
            logger.warning("no se pudo guardar %s", ARCHIVO_CONFIG, exc_info=True)

    @property
    def pin(self) -> str:
        return self._datos["pin"]

    def cambiar_pin(self) -> str:
        self._datos.update(pin=self._pin_nuevo(), sesiones=[])
        self._guardar()
        return self.pin

    def codigo_qr(self) -> str:
        ahora = time.monotonic()
        self._codigos = {c: vence for c, vence in self._codigos.items() if vence > ahora}
        codigo = secrets.token_urlsafe(18)
        self._codigos[codigo] = ahora + self.VIGENCIA_CODIGO_S
        return codigo

    def entrar(self, ip: str, pin: Optional[str], codigo: Optional[str]) -> str:
        ahora = time.monotonic()
        fallos = [t for t in self._fallos.get(ip, []) if ahora - t < self.BLOQUEO_S]
        if len(fallos) >= self.INTENTOS_MAX:
            raise ErrorGuiado("Demasiados intentos fallidos. Espera un minuto.")
        valido = bool(codigo) and self._codigos.pop(codigo, 0.0) > ahora
        if not valido and pin:
            valido = hmac.compare_digest(pin.strip().encode(), self.pin.encode())
        if not valido:
            self._fallos[ip] = fallos + [ahora]
            raise ErrorGuiado(
                "PIN incorrecto: míralo en GPS Libre en el PC." if pin else "El código QR caducó: vuelve a escanearlo."
            )
        token = secrets.token_urlsafe(32)
        self._datos["sesiones"] = (self._datos.get("sesiones", []) + [self._hash(token)])[-self.SESIONES_MAX :]
        self._guardar()
        return token

    def sesion_valida(self, token: Optional[str]) -> bool:
        return bool(token) and self._hash(token) in self._datos.get("sesiones", [])


# ---------------------------------------------------------------- licencia (movimientos gratis y luego cobro)


class LicenciaRequerida(ErrorGuiado):
    """Se acabaron los movimientos gratis: la interfaz abre el muro de compra."""


class Licencia:
    """Cuenta los movimientos gratis y, pasado el límite, exige una clave de licencia firmada.

    La clave la fabrica empaquetar/generar_licencia.py y se verifica aquí con la llave pública incrustada
    (firma Ed25519), así nadie puede inventarse una clave válida. Los planes con caducidad dejan de valer
    en su fecha; el plan «vida» no caduca. El conteo y la clave activa se guardan en licencia.json.
    """

    def __init__(self) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        self._verificador = Ed25519PublicKey.from_public_bytes(_b64d(CLAVE_PUBLICA_LICENCIA))
        self._datos = _leer_json(ARCHIVO_LICENCIA)
        self.movimientos = int(self._datos.get("movimientos", 0))
        # Si la clave activa es de Lemon Squeezy, revalida en segundo plano (una cancelación bloquea la app).
        if self._datos.get("tipo") == "ls" and self._datos.get("clave"):
            import threading
            threading.Thread(target=self._bucle_ls, daemon=True).start()

    def _guardar(self) -> None:
        try:
            ARCHIVO_LICENCIA.write_text(json.dumps(self._datos, indent=2), encoding="utf-8")
        except OSError:
            logger.warning("no se pudo guardar %s", ARCHIVO_LICENCIA, exc_info=True)

    def _validar(self, clave: Optional[str]) -> Optional[dict]:
        """Devuelve la carga {p, exp?, id} si la clave está bien firmada y no ha caducado; si no, None."""
        if not clave:
            return None
        from cryptography.exceptions import InvalidSignature

        try:
            cuerpo = clave.strip()
            if cuerpo.startswith("GPSL-"):
                cuerpo = cuerpo[5:]
            carga_b64, firma_b64 = cuerpo.split(".", 1)
            carga_bytes = _b64d(carga_b64)
            self._verificador.verify(_b64d(firma_b64), carga_bytes)
            carga = json.loads(carga_bytes)
        except (ValueError, InvalidSignature, KeyError):
            return None
        if "exp" in carga and time.time() > carga["exp"]:
            return None
        return carga

    # ---- Lemon Squeezy: valida las claves de compra contra su API de licencias ----
    @staticmethod
    def _ls_peticion(endpoint: str, params: dict) -> Optional[dict]:
        """Llama a api.lemonsqueezy.com/v1/licenses/<endpoint>. No requiere token (solo la clave)."""
        import urllib.request, urllib.parse, urllib.error

        try:
            datos = urllib.parse.urlencode(params).encode()
            req = urllib.request.Request(
                f"https://api.lemonsqueezy.com/v1/licenses/{endpoint}",
                data=datos, headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode())  # LS responde JSON incluso en 400 (clave inválida)
            except Exception:
                return None
        except Exception:
            return None

    @staticmethod
    def _interpretar_ls(r: dict, instance_id: Optional[str]) -> dict:
        lk = (r or {}).get("license_key") or {}
        meta = (r or {}).get("meta") or {}
        activo = bool(r.get("valid") or r.get("activated")) and lk.get("status") == "active"
        exp_ts = None
        expira = lk.get("expires_at")
        if expira:
            try:
                from datetime import datetime
                exp_ts = int(datetime.fromisoformat(str(expira).replace("Z", "+00:00")).timestamp())
            except Exception:
                exp_ts = None
        inst = ((r or {}).get("instance") or {}).get("id") or instance_id
        return {"activo": activo, "expira": exp_ts, "plan": meta.get("product_name") or "premium",
                "instance_id": inst, "status": lk.get("status")}

    def _revalidar_ls(self) -> None:
        clave = self._datos.get("clave")
        if self._datos.get("tipo") != "ls" or not clave:
            return
        r = self._ls_peticion("validate", {"license_key": clave, "instance_id": self._datos.get("instance_id") or ""})
        if not r:
            return  # sin internet: conserva el último estado conocido (gracia)
        info = self._interpretar_ls(r, self._datos.get("instance_id"))
        self._datos["estado_ls"] = "active" if info["activo"] else (info["status"] or "inactive")
        self._datos["expira_ls"] = info["expira"]
        self._datos["validado_ls"] = int(time.time())
        self._guardar()

    def _bucle_ls(self) -> None:
        while True:
            try:
                self._revalidar_ls()
            except Exception:
                logger.debug("revalidación Lemon Squeezy falló", exc_info=True)
            time.sleep(6 * 3600)  # cada 6 h; y una vez al arrancar

    @property
    def licencia_activa(self) -> Optional[dict]:
        # 1) Nuestra clave firmada (GPSL-)
        carga = self._validar(self._datos.get("clave"))
        if carga is not None:
            return carga
        # 2) Clave de Lemon Squeezy (estado en caché; se refresca en segundo plano)
        if self._datos.get("tipo") == "ls" and self._datos.get("estado_ls") == "active":
            exp = self._datos.get("expira_ls")
            if exp and time.time() > exp:
                return None
            return {"p": self._datos.get("plan_ls", "premium"), "exp": exp, "ls": True}
        return None

    def activar(self, clave: str) -> dict:
        clave = clave.strip()
        # 1) Clave firmada nuestra (dueño / accesos manuales)
        carga = self._validar(clave)
        if carga is not None:
            self._datos["clave"] = clave
            self._datos["tipo"] = "gpsl"
            for k in ("instance_id", "estado_ls", "expira_ls", "plan_ls", "validado_ls"):
                self._datos.pop(k, None)
            self._guardar()
            logger.info("licencia activada (GPSL): plan %s", carga.get("p"))
            return self.estado()
        # 2) Clave de Lemon Squeezy: activa/valida en línea
        r = self._ls_peticion("activate", {"license_key": clave, "instance_name": "GPS Libre PC"})
        if r is None:
            raise ErrorGuiado("No pude conectar con el servidor de licencias. Revisa tu internet e inténtalo de nuevo.")
        info = self._interpretar_ls(r, None)
        if not info["activo"]:
            # p. ej. ya alcanzó el límite de activaciones: intenta solo validar
            r2 = self._ls_peticion("validate", {"license_key": clave})
            info = self._interpretar_ls(r2 or {}, None)
        if not info["activo"]:
            raise ErrorGuiado("Esa clave no es válida o está vencida. Revisa que la copiaste completa.")
        self._datos["clave"] = clave
        self._datos["tipo"] = "ls"
        self._datos["instance_id"] = info["instance_id"]
        self._datos["estado_ls"] = "active"
        self._datos["expira_ls"] = info["expira"]
        self._datos["plan_ls"] = info["plan"]
        self._datos["validado_ls"] = int(time.time())
        self._guardar()
        logger.info("licencia activada (Lemon Squeezy): %s", info["plan"])
        # arranca la revalidación periódica si no estaba
        import threading
        threading.Thread(target=self._bucle_ls, daemon=True).start()
        return self.estado()

    def cobrar_movimiento(self, es_demo: bool, tipo: str = "teletransporte") -> None:
        """Cobra un movimiento. La prueba gratis solo cubre los teletransportes; las rutas y el joystick
        son de la versión completa, así que para un usuario gratis muestran el muro de compra directamente."""
        if es_demo or self.licencia_activa is not None:
            return  # el modo demo y los licenciados no gastan ni tienen límite
        if tipo != "teletransporte":
            mensajes = {
                "ruta": "Las rutas son parte de GPS Libre completo. Actívalo para recorrerlas.",
                "joystick": "El joystick es parte de GPS Libre completo. Actívalo para usarlo.",
            }
            raise LicenciaRequerida(mensajes.get(tipo, "Esta función es de GPS Libre completo. Actívalo para usarla."))
        if self.movimientos >= LIMITE_MOVIMIENTOS_GRATIS:
            raise LicenciaRequerida(
                f"Usaste tus {LIMITE_MOVIMIENTOS_GRATIS} teletransportes gratis. Activa GPS Libre para seguir."
            )
        self.movimientos += 1
        self._datos["movimientos"] = self.movimientos
        self._guardar()

    def estado(self) -> dict:
        activa = self.licencia_activa
        return {
            "licenciado": activa is not None,
            "plan": activa.get("p") if activa else None,
            "expira": activa.get("exp") if activa else None,
            "movimientos_usados": self.movimientos,
            "limite_gratis": LIMITE_MOVIMIENTOS_GRATIS,
            "restantes": None if activa else max(0, LIMITE_MOVIMIENTOS_GRATIS - self.movimientos),
        }


# ---------------------------------------------------------------- API web

iphone = Iphone()
motor = Motor(iphone)
acceso = AccesoCelular()
licencia = Licencia()


def _ignorar_conexiones_cortadas(loop: asyncio.AbstractEventLoop, contexto: dict) -> None:
    # En Windows el bucle Proactor registra como error cada vez que el navegador cierra una conexión.
    if isinstance(contexto.get("exception"), ConnectionResetError):
        return
    loop.default_exception_handler(contexto)


@asynccontextmanager
async def ciclo_de_vida(_app: FastAPI):
    asyncio.get_running_loop().set_exception_handler(_ignorar_conexiones_cortadas)
    yield
    await motor.detener()
    await iphone.desconectar(olvidar=True)


app = FastAPI(title="GPS Libre", lifespan=ciclo_de_vida)

RUTAS_SIN_SESION = {"/", "/api/sesion", "/api/entrar", "/manifest.webmanifest", "/icono.png", "/icono-512.png"}


def _es_local(request: Request) -> bool:
    return request.client is not None and request.client.host in ("127.0.0.1", "::1")


def _nombre_host(cabecera: str) -> str:
    if cabecera.startswith("["):
        return cabecera[1 : cabecera.find("]")].lower()
    return cabecera.rsplit(":", 1)[0].lower()


@app.middleware("http")
async def _proteger(request: Request, call_next):
    # Solo se atiende por los nombres propios del PC: así una web que apunte su dominio a esta IP
    # (DNS rebinding) no puede usar el navegador del usuario para mover su iPhone.
    cliente = request.client.host if request.client else None
    host = request.headers.get("host", "")
    if _nombre_host(host) not in {"localhost", "127.0.0.1", "::1", *ips_locales()}:
        logger.warning("rechazada por dirección: host=%r cliente=%s %s", host, cliente, request.url.path)
        return JSONResponse(status_code=400, content={"detail": "Dirección no permitida."})
    if (
        not _es_local(request)
        and request.url.path not in RUTAS_SIN_SESION
        and not acceso.sesion_valida(request.cookies.get(COOKIE_SESION))
    ):
        logger.warning("rechazada sin sesión: cliente=%s host=%r %s", cliente, host, request.url.path)
        return JSONResponse(status_code=401, content={"detail": "Escribe el PIN que muestra GPS Libre en el PC."})
    return await call_next(request)


@app.exception_handler(ErrorGuiado)
async def _error_guiado(_request: Request, exc: ErrorGuiado):
    # 402 (pago requerido) marca el muro de compra; el resto son errores normales.
    if isinstance(exc, LicenciaRequerida):
        return JSONResponse(status_code=402, content={"detail": str(exc), "necesita_licencia": True})
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def _solo_desde_el_pc(request: Request) -> None:
    if not _es_local(request):
        raise ErrorGuiado("Esto solo se puede hacer desde el PC.")


class ConectarIn(BaseModel):
    demo: bool = False
    via: Literal["usb", "wifi"] = "usb"
    ip: Optional[str] = None

    @field_validator("ip")
    @classmethod
    def _ip_valida(cls, valor: Optional[str]) -> Optional[str]:
        if valor is None or not valor.strip():
            return None
        try:
            return str(ipaddress.ip_address(valor.strip()))
        except ValueError:
            raise ValueError("la IP del iPhone no es válida (ejemplo: 192.168.20.34)") from None


class PuntoIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class RutaIn(BaseModel):
    puntos: list[tuple[float, float]] = Field(min_length=1, max_length=100_000)
    trabajo_zona: Optional[str] = None  # si viene, la ruta es ese recorrido de zona y crece mientras se calcula
    velocidad_kmh: float = Field(gt=0, le=300)
    bucle: bool = False
    ida_y_vuelta: bool = False
    natural: bool = True


class JoystickIn(BaseModel):
    rumbo: Optional[float] = None
    velocidad_kmh: Optional[float] = Field(default=None, gt=0, le=300)


class PausaIn(BaseModel):
    pausado: bool


class VelocidadIn(BaseModel):
    velocidad_kmh: float = Field(gt=0, le=300)


class CallesIn(BaseModel):
    puntos: list[tuple[float, float]] = Field(min_length=2, max_length=60)
    perfil: str = Field(default="foot", pattern="^(foot|bike|car)$")


class ListaGuardadaIn(BaseModel):
    elementos: list[dict] = Field(max_length=1000)


class EntrarIn(BaseModel):
    pin: Optional[str] = Field(default=None, max_length=12)
    codigo: Optional[str] = Field(default=None, max_length=64)


@app.get("/")
async def pagina():
    return FileResponse(RECURSOS / "web" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/icono.png")
async def icono():
    return FileResponse(RECURSOS / "web" / "icono.png")


@app.get("/icono-512.png")
async def icono_grande():
    return FileResponse(RECURSOS / "web" / "icono-512.png")


@app.get("/manifest.webmanifest")
async def manifiesto():
    return FileResponse(RECURSOS / "web" / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/api/sesion")
async def sesion(request: Request):
    local = _es_local(request)
    return {
        "local": local,
        "autorizado": local or acceso.sesion_valida(request.cookies.get(COOKIE_SESION)),
        "ip": request.client.host if request.client else None,
    }


@app.post("/api/entrar")
async def entrar(datos: EntrarIn, request: Request):
    token = acceso.entrar(request.client.host if request.client else "?", datos.pin, datos.codigo)
    respuesta = JSONResponse({"ok": True})
    respuesta.set_cookie(COOKIE_SESION, token, max_age=180 * 24 * 3600, httponly=True, samesite="strict")
    return respuesta


@app.get("/api/celular")
async def celular(request: Request):
    """Datos para abrir GPS Libre desde el iPhone: direcciones, PIN y enlace del QR (con código de un solo uso)."""
    _solo_desde_el_pc(request)
    puerto = request.url.port or PUERTO
    direcciones = [f"http://{ip}:{puerto}" for ip in ips_para_celular()] if SERVIDOR_EN_RED else []
    return {
        "en_red": SERVIDOR_EN_RED,
        "pin": acceso.pin,
        "direcciones": direcciones,
        "enlace_qr": f"{direcciones[0]}/#codigo={acceso.codigo_qr()}" if direcciones else None,
    }


@app.post("/api/celular/nuevo-pin")
async def celular_nuevo_pin(request: Request):
    _solo_desde_el_pc(request)
    return {"pin": acceso.cambiar_pin()}


@app.post("/api/salir")
async def salir(request: Request):
    """Cierra GPS Libre: el programa instalado no tiene ventana que cerrar."""
    _solo_desde_el_pc(request)
    if servidor_uvicorn is not None:
        servidor_uvicorn.should_exit = True  # apaga ordenadamente: el iPhone vuelve a su GPS real
    return {"ok": True}


class ActivarLicenciaIn(BaseModel):
    clave: str = Field(min_length=1, max_length=400)


@app.get("/api/licencia")
async def licencia_estado():
    return {**licencia.estado(), "precios": PRECIOS, "descuento": DESCUENTO_PRIMERA, "url_compra": URL_COMPRA}


@app.post("/api/licencia/activar")
async def licencia_activar(datos: ActivarLicenciaIn):
    return licencia.activar(datos.clave)


@app.get("/api/estado")
async def estado():
    ruta = motor.ruta
    return {
        "conectado": iphone.conectado,
        "conectando": iphone.conectando,
        "dispositivo": iphone.info,
        "recordado": leer_recordado(),
        "posicion": motor.posicion,
        "modo": motor.modo,
        "pausado": motor.pausado,
        "velocidad_kmh": motor.velocidad_kmh,
        "ruta": None
        if ruta is None
        else {"total_m": ruta.total_m, "recorrido_m": min(motor.recorrido_m, ruta.total_m), "vueltas": motor.vueltas},
        "fin_ruta": motor.fin_ruta,
        "esperando_calles": motor.esperando_calles,
        "licencia": licencia.estado(),
        "error": motor.error,
    }


@app.post("/api/conectar")
async def conectar(datos: ConectarIn):
    await motor.detener()
    return await iphone.conectar(demo=datos.demo, via=datos.via, ip=datos.ip)


@app.post("/api/desconectar")
async def desconectar():
    await motor.detener()
    await iphone.desconectar(olvidar=True)
    return {"ok": True}


def _es_demo() -> bool:
    return isinstance(iphone._simulador, SimuladorDemo)


@app.post("/api/teletransportar")
async def teletransportar(datos: PuntoIn):
    licencia.cobrar_movimiento(_es_demo(), "teletransporte")
    await motor.teletransportar(datos.lat, datos.lon)
    return {"ok": True}


@app.post("/api/ruta")
async def ruta(datos: RutaIn):
    licencia.cobrar_movimiento(_es_demo(), "ruta")
    trabajo = None
    if datos.trabajo_zona:
        if trabajo_zona is None or trabajo_zona.id != datos.trabajo_zona:
            raise ErrorGuiado("Ese recorrido de zona ya no está en el programa: vuelve a calcularlo.")
        trabajo = trabajo_zona
    await motor.iniciar_ruta(
        datos.puntos, datos.velocidad_kmh, datos.bucle, datos.ida_y_vuelta, datos.natural, trabajo
    )
    return {"ok": True, "total_m": motor.ruta.total_m if motor.ruta else 0}


@app.post("/api/joystick")
async def joystick(datos: JoystickIn):
    # El joystick manda muchas órdenes; solo se cobra al empezar la sesión.
    if motor.modo != "joystick":
        licencia.cobrar_movimiento(_es_demo(), "joystick")
    await motor.joystick(datos.rumbo, datos.velocidad_kmh)
    return {"ok": True}


@app.post("/api/pausa")
async def pausa(datos: PausaIn):
    motor.pausado = datos.pausado
    return {"ok": True}


@app.post("/api/velocidad")
async def velocidad(datos: VelocidadIn):
    motor.velocidad_kmh = datos.velocidad_kmh
    return {"ok": True}


@app.post("/api/detener")
async def detener():
    await motor.detener()
    return {"ok": True}


@app.post("/api/restaurar")
async def restaurar():
    await motor.restaurar()
    return {"ok": True}


@app.get("/api/ubicacion-real")
async def ubicacion_real():
    """Ubicación real del PC (servicio de ubicación de Windows); el iPhone está a su lado."""
    cache = _cache_ubicacion_real
    if cache.get("datos") and time.monotonic() - cache["ts"] < UBICACION_REAL_CACHE_S:
        return cache["datos"]
    datos = await asyncio.to_thread(_leer_ubicacion_windows)
    if datos is None:
        raise ErrorGuiado("Activa la ubicación de Windows: Configuración → Privacidad y seguridad → Ubicación.")
    cache.update(datos=datos, ts=time.monotonic())
    return datos


@app.get("/api/guardados")
async def guardados():
    return leer_guardados()


@app.put("/api/guardados/{tipo}")
async def guardar_lista(tipo: Literal["rutas", "favoritos"], datos: ListaGuardadaIn):
    todo = leer_guardados()
    todo[tipo] = datos.elementos
    try:
        ARCHIVO_GUARDADOS.write_text(json.dumps(todo, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        raise ErrorGuiado(f"No se pudo guardar en {ARCHIVO_GUARDADOS.name}: {e}") from e
    return {"ok": True}


class ZonaCallesIn(BaseModel):
    poligono: list[tuple[float, float]] = Field(min_length=3, max_length=200)
    porcentaje: int = Field(ge=5, le=100)
    perfil: Literal["foot", "bike", "car"] = "foot"
    inicio: Optional[tuple[float, float]] = None


trabajo_zona: Optional[TrabajoZona] = None  # el último recorrido de zona (se calcula uno a la vez)


@app.post("/api/zona/iniciar")
async def zona_iniciar(datos: ZonaCallesIn):
    """Empieza a calcular, sector a sector, el recorrido por todas las calles de la zona o un porcentaje de ellas."""
    global trabajo_zona
    if trabajo_zona is not None:
        trabajo_zona.cancelar()
    trabajo_zona = TrabajoZona(datos.poligono, datos.porcentaje, datos.perfil, datos.inicio)
    return {**trabajo_zona.resumen(), "cajas": [s["caja"] for s in trabajo_zona.sectores]}


@app.get("/api/zona/estado")
async def zona_estado(id: str, desde: int = 0):
    """Progreso del cálculo y los puntos nuevos desde el índice `desde`, para ir dibujando."""
    if trabajo_zona is None or trabajo_zona.id != id:
        raise ErrorGuiado("Ese cálculo de zona ya no existe: vuelve a calcularlo.")
    return trabajo_zona.resumen(max(0, desde))


@app.post("/api/zona/cancelar")
async def zona_cancelar():
    global trabajo_zona
    if trabajo_zona is not None:
        trabajo_zona.cancelar()
        trabajo_zona = None
    return {"ok": True}


@app.get("/api/buscar")
async def buscar(q: str):
    """Busca lugares en OpenStreetMap (Nominatim)."""
    try:
        respuesta = await asyncio.to_thread(
            requests.get,
            "https://nominatim.openstreetmap.org/search",
            params={"format": "jsonv2", "limit": 6, "q": q, "accept-language": "es"},
            headers={"User-Agent": AGENTE_HTTP},
            timeout=15,
        )
        respuesta.raise_for_status()
    except requests.RequestException as e:
        raise ErrorGuiado(f"No se pudo buscar el lugar: {e}") from e
    return [
        {"nombre": r.get("display_name", ""), "lat": float(r["lat"]), "lon": float(r["lon"])}
        for r in respuesta.json()
    ]


@app.post("/api/calles")
async def calles(datos: CallesIn):
    """Traza la ruta por calles reales entre los puntos (OSRM de openstreetmap.de)."""
    coordenadas = ";".join(f"{lon},{lat}" for lat, lon in datos.puntos)
    url = f"https://routing.openstreetmap.de/routed-{datos.perfil}/route/v1/driving/{coordenadas}"
    try:
        respuesta = await asyncio.to_thread(
            requests.get,
            url,
            params={"overview": "full", "geometries": "geojson"},
            headers={"User-Agent": AGENTE_HTTP},
            timeout=20,
        )
        respuesta.raise_for_status()
        cuerpo = respuesta.json()
    except (requests.RequestException, ValueError) as e:
        raise ErrorGuiado(f"No se pudo trazar la ruta por calles: {e}") from e
    if cuerpo.get("code") != "Ok" or not cuerpo.get("routes"):
        raise ErrorGuiado("No encontré un camino por calles entre esos puntos.")
    mejor = cuerpo["routes"][0]
    return {
        "puntos": [(lat, lon) for lon, lat in mejor["geometry"]["coordinates"]],
        "distancia_m": mejor.get("distance", 0),
    }


def _puerto_ocupado(puerto: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", puerto)) == 0


def _ya_hay_un_gps_libre(puerto: int) -> bool:
    """Distingue otra copia de GPS Libre de cualquier otro programa que tenga el puerto."""
    try:
        return "autorizado" in requests.get(f"http://127.0.0.1:{puerto}/api/sesion", timeout=2).json()
    except Exception:
        return False


def _primer_puerto_libre(desde: int) -> int:
    for puerto in range(desde, desde + 20):
        if not _puerto_ocupado(puerto):
            return puerto
    raise ErrorGuiado("No encontré ningún puerto libre para GPS Libre.")


def _avisar_en_pantalla(titulo: str, mensaje: str) -> None:
    """Sin consola (el programa instalado) el único sitio donde avisar es una ventana de Windows."""
    if sys.stdout is not None:
        print(f"{titulo}: {mensaje}")
    elif sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, mensaje, titulo, 0x40)
    elif sys.platform == "darwin":
        # Sin consola (la .app de macOS): un diálogo nativo con osascript.
        guion = f"display dialog {json.dumps(mensaje)} with title {json.dumps(titulo)} buttons {{\"OK\"}} default button 1"
        try:
            subprocess.run(["osascript", "-e", guion], check=False)
        except Exception:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GPS Libre — simulador de ubicación para iPhone")
    parser.add_argument("--puerto", type=int, default=PUERTO)
    parser.add_argument("--sin-navegador", action="store_true", help="no abrir el navegador al arrancar")
    parser.add_argument("--solo-pc", action="store_true", help="no aceptar conexiones desde el celular")
    args = parser.parse_args()
    SERVIDOR_EN_RED = not args.solo_pc

    try:
        puerto = args.puerto
        if _puerto_ocupado(puerto):
            if _ya_hay_un_gps_libre(puerto):
                _avisar_en_pantalla("GPS Libre", "GPS Libre ya estaba abierto: abro su página en el navegador.")
                webbrowser.open(f"http://127.0.0.1:{puerto}")
                sys.exit(0)
            puerto = _primer_puerto_libre(puerto + 1)  # otro programa ocupa el puerto de siempre

        direccion = f"http://127.0.0.1:{puerto}"
        if sys.stdout is not None:
            print(f"\n  GPS Libre abierto en {direccion}")
            if SERVIDOR_EN_RED:
                for ip in ips_para_celular():
                    print(f"  Desde el iPhone (misma Wi‑Fi): http://{ip}:{puerto}   PIN: {acceso.pin}")
            print("  Deja esta ventana abierta mientras uses la ubicación simulada.\n")
        if not args.sin_navegador:
            threading.Timer(1.5, webbrowser.open, [direccion]).start()

        servidor_uvicorn = uvicorn.Server(
            uvicorn.Config(
                app,
                host="0.0.0.0" if SERVIDOR_EN_RED else "127.0.0.1",
                port=puerto,
                log_level="warning",
                log_config=None,  # sin consola, la configuración de registro de uvicorn no sirve
            )
        )
        servidor_uvicorn.run()
    except SystemExit:
        raise
    except Exception as error_arranque:
        logger.exception("GPS Libre no pudo arrancar")
        _avisar_en_pantalla("GPS Libre no pudo arrancar", f"{type(error_arranque).__name__}: {error_arranque}")
        sys.exit(1)
