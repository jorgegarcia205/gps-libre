"""Fabrica claves de licencia de GPS Libre (firmadas, para que no se puedan falsificar).

La clave se verifica dentro del programa con la llave pública que va incrustada en servidor.py; la llave
privada vive SOLO aquí (`licencia_privada.pem`) y nunca se mete en el ejecutable. Así, cuando montes el cobro,
tu pasarela (Lemon Squeezy/Paddle) llama a este script en su webhook de compra y le entrega la clave al cliente.

Uso:
    python generar_licencia.py --publica              # imprime la llave pública para pegar en servidor.py
    python generar_licencia.py --plan anual            # una clave anual (365 días)
    python generar_licencia.py --plan vida             # una clave de por vida (sin caducidad)
    python generar_licencia.py --plan mensual --dias 30
"""

import argparse
import base64
import json
import secrets
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

AQUI = Path(__file__).resolve().parent
LLAVE_PRIVADA = AQUI / "licencia_privada.pem"
DIAS_POR_PLAN = {"mensual": 30, "trimestral": 92, "anual": 365, "vida": None}


def _cargar_o_crear_llave() -> Ed25519PrivateKey:
    if LLAVE_PRIVADA.exists():
        return serialization.load_pem_private_key(LLAVE_PRIVADA.read_bytes(), password=None)
    llave = Ed25519PrivateKey.generate()
    LLAVE_PRIVADA.write_bytes(
        llave.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    print(f"(llave privada nueva creada en {LLAVE_PRIVADA.name}; NO la subas a internet)")
    return llave


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode()


def llave_publica_b64(llave: Ed25519PrivateKey) -> str:
    cruda = llave.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return _b64(cruda)


def fabricar_clave(llave: Ed25519PrivateKey, plan: str, dias) -> str:
    # Clave = GPSL-<carga>.<firma>. La carga y la firma van en base64url (sin '.'), así el programa parte por '.'.
    carga = {"p": plan, "id": secrets.token_hex(4)}
    if dias:
        carga["exp"] = int(time.time()) + dias * 86400
    carga_bytes = json.dumps(carga, separators=(",", ":"), sort_keys=True).encode()
    firma = llave.sign(carga_bytes)
    return f"GPSL-{_b64(carga_bytes)}.{_b64(firma)}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Genera claves de licencia de GPS Libre")
    parser.add_argument("--publica", action="store_true", help="imprime la llave pública para servidor.py")
    parser.add_argument("--plan", choices=list(DIAS_POR_PLAN), help="plan de la clave a fabricar")
    parser.add_argument("--dias", type=int, help="días de validez (anula el valor por defecto del plan)")
    parser.add_argument("--cantidad", type=int, default=1, help="cuántas claves fabricar")
    args = parser.parse_args()

    llave = _cargar_o_crear_llave()
    if args.publica or not args.plan:
        print("CLAVE_PUBLICA_LICENCIA =", repr(llave_publica_b64(llave)))
    if args.plan:
        dias = args.dias if args.dias is not None else DIAS_POR_PLAN[args.plan]
        for _ in range(args.cantidad):
            print(fabricar_clave(llave, args.plan, dias))
