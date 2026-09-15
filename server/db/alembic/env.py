"""Alembic sobre SQL escrito a mano.

`autogenerate` NO detecta triggers, constraints EXCLUDE, columnas generadas ni
extensiones — y este esquema usa las cuatro cosas. Los archivos de
`db/migrations/*.sql` son la fuente de verdad; cada revisión los aplica con
`op.execute()`.

Por eso `target_metadata` es None a propósito: si algún día alguien ejecuta
`alembic revision --autogenerate`, no obtendrá una migración que proponga
borrar el trigger de inmutabilidad del libro mayor.
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

target_metadata = None  # deliberado: ver docstring


def _url() -> str:
    url = os.environ.get("DSD_DATABASE_URL")
    if not url:
        raise RuntimeError("falta DSD_DATABASE_URL")
    # Alembic corre en modo síncrono; psycopg 3 sirve para ambos.
    return url.replace("+asyncpg", "+psycopg")


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    motor = create_engine(_url(), poolclass=pool.NullPool)
    with motor.connect() as conexion:
        context.configure(connection=conexion, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
