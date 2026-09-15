"""Lectura de los archivos SQL que son la fuente de verdad del esquema.

Módulo aparte a propósito: `env.py` ejecuta las migraciones al importarse, así
que una revisión que importe de ahí las dispararía de nuevo en cascada.
"""

from __future__ import annotations

import pathlib

DIRECTORIO_SQL = pathlib.Path(__file__).resolve().parent / "migrations"


def leer_sql(nombre: str) -> str:
    ruta = DIRECTORIO_SQL / nombre
    if not ruta.exists():
        raise FileNotFoundError(f"no existe {ruta}")
    return ruta.read_text(encoding="utf-8")
