#!/usr/bin/env python3
"""Genera contracts/argon2_vectors.json.

El servidor calcula el hash de la contraseña y **lo replica al dispositivo**
para permitir el login sin señal. Los bindings de Argon2 en Dart son FFI y no
comparten valores por defecto con `argon2-cffi`: si los parámetros no coinciden,
el vendedor no puede entrar al empezar el día — el peor momento posible para
descubrirlo.

La prueba de Dart **verifica** estos hashes (no los regenera: la sal es
aleatoria) y comprueba que una contraseña equivocada falla.

Uso:  python3 contracts/generar_vectores_argon2.py
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "server"))

from app.core.seguridad import (  # noqa: E402
    PARAMETROS_ARGON2,
    hashear_password,
    verificar_password,
)

CASOS = [
    ("ascii_simple", "Contraseña ASCII corriente", "Vendedor2026"),
    ("pin_numerico", "PIN de 6 dígitos, el caso real en la calle", "481507"),
    ("con_acentos", "Acentos y eñe", "contraseña_mañana"),
    ("con_emoji", "Fuera del plano básico: pares suplentes en UTF-16", "clave🔒segura"),
    ("con_espacios", "Espacios al inicio y al final son significativos", "  dos espacios  "),
    ("larga", "Contraseña larga", "R" * 120),
    ("un_caracter", "Un solo carácter", "x"),
]


def main() -> int:
    vectores = []
    for nombre, descripcion, password in CASOS:
        phc = hashear_password(password)
        if not verificar_password(password, phc):
            raise SystemExit(f"el hash de {nombre} no se verifica contra su propia contraseña")
        if verificar_password(password + "x", phc):
            raise SystemExit(f"el hash de {nombre} valida una contraseña incorrecta")
        vectores.append(
            {
                "nombre": nombre,
                "descripcion": descripcion,
                "password": password,
                "hash_phc": phc,
                "password_incorrecta": password + "x",
            }
        )

    destino = pathlib.Path(__file__).with_name("argon2_vectors.json")
    destino.write_text(
        json.dumps(
            {
                "version": 1,
                "descripcion": (
                    "Vectores de Argon2id para el login offline. La prueba de Dart VERIFICA "
                    "estos hashes; no los regenera (la sal es aleatoria). "
                    "Reglas en contracts/README.md §2."
                ),
                "parametros": {
                    "variante": PARAMETROS_ARGON2.variante,
                    "memoria_kib": PARAMETROS_ARGON2.memoria_kib,
                    "iteraciones": PARAMETROS_ARGON2.iteraciones,
                    "paralelismo": PARAMETROS_ARGON2.paralelismo,
                    "sal_bytes": PARAMETROS_ARGON2.sal_bytes,
                    "hash_bytes": PARAMETROS_ARGON2.hash_bytes,
                },
                "vectores": vectores,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(vectores)} vectores de Argon2id escritos en {destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
