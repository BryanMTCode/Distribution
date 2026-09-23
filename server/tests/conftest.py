"""Infraestructura de pruebas.

Las pruebas corren contra un PostgreSQL **real**, no contra SQLite ni mocks: la
mitad del diseño vive en constraints, triggers y columnas generadas que solo
existen en PostgreSQL. Una suite que no los ejerce no prueba este sistema.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# El puerto estándar de PostgreSQL. Si corres la base en otro, exporta
# DSD_TEST_DATABASE_URL en vez de editar esto.
URL_PRUEBAS = os.environ.get(
    "DSD_TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:dsd@127.0.0.1:5432/dsd_test",
)

# Datos transaccionales: se vacían entre pruebas. CASCADE arrastra a las
# tablas que dependen de ellas, así que el orden no importa.
#
# Los datos de REFERENCIA (roles, permisos, unidades, canales, motivos, la
# lista de precios por defecto) NO están aquí: los siembra la migración 0009 y
# el código depende de sus códigos literales. Vaciarlos entre pruebas sería
# probar contra un sistema que no existe en producción.
TABLAS_VOLATILES = [
    "sesiones",
    "folios_rangos",
    "jobs",
    "auditoria",
    "usuarios_rutas",
    "usuarios_permisos",
    "dispositivos",
    "clientes",
    "productos",
    "categorias",
    "marcas",
    "rutas",
    "usuarios",
    "almacenes",
    "sucursales",
]


def _preparar_esquema() -> None:
    base = URL_PRUEBAS.rsplit("/", 1)[0] + "/postgres"
    sync = base.replace("+psycopg", "")
    nombre = URL_PRUEBAS.rsplit("/", 1)[1]
    import psycopg

    with psycopg.connect(sync, autocommit=True) as con:
        existe = con.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (nombre,)
        ).fetchone()
        if not existe:
            con.execute(f'CREATE DATABASE "{nombre}"')
    with psycopg.connect(URL_PRUEBAS.replace("+psycopg", ""), autocommit=True) as con:
        con.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        ya = con.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='alembic_version'"
        ).fetchone()
    if not ya:
        # sys.executable -m alembic: el binario del venv no está en PATH
        # cuando pytest corre desde un entorno distinto.
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            env={**os.environ, "DSD_DATABASE_URL": URL_PRUEBAS},
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )


@pytest.fixture(scope="session", autouse=True)
def esquema() -> None:
    os.environ.setdefault(
        "DSD_JWT_SECRETO", "secreto-de-pruebas-de-32-bytes-o-mas-no-usar-en-produccion"
    )
    os.environ["DSD_DATABASE_URL"] = URL_PRUEBAS
    _preparar_esquema()


@pytest_asyncio.fixture
async def motor(esquema):  # noqa: ARG001
    m = create_async_engine(URL_PRUEBAS, poolclass=None)
    async with m.begin() as con:
        await con.execute(
            text(f"TRUNCATE {', '.join(TABLAS_VOLATILES)} RESTART IDENTITY CASCADE")
        )
    yield m
    await m.dispose()


@pytest_asyncio.fixture
async def sesion(motor) -> AsyncIterator[AsyncSession]:
    fabrica = async_sessionmaker(motor, expire_on_commit=False, class_=AsyncSession)
    async with fabrica() as s:
        yield s


@pytest_asyncio.fixture
async def cliente(motor) -> AsyncIterator[AsyncClient]:
    """Cliente HTTP contra la app, con la sesión apuntando a la BD de pruebas."""
    from app.core.db import obtener_sesion
    from app.main import crear_app

    fabrica = async_sessionmaker(motor, expire_on_commit=False, class_=AsyncSession)

    async def sesion_de_pruebas() -> AsyncIterator[AsyncSession]:
        async with fabrica() as s:
            yield s

    app = crear_app()
    app.dependency_overrides[obtener_sesion] = sesion_de_pruebas
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://pruebas"
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Semilla mínima
# ---------------------------------------------------------------------------

PASSWORD_VENDEDOR = "Vendedor2026"


@pytest_asyncio.fixture
async def semilla(sesion: AsyncSession) -> dict[str, uuid.UUID]:
    """Un vendedor con su camión y su ruta, y un admin."""
    from app.core.seguridad import hashear_password

    ahora = datetime.now(UTC)
    ids = {
        "sucursal": uuid.uuid4(),
        "vendedor": uuid.uuid4(),
        "admin": uuid.uuid4(),
        "camion": uuid.uuid4(),
        "bodega": uuid.uuid4(),
        "ruta": uuid.uuid4(),
    }
    hash_password = hashear_password(PASSWORD_VENDEDOR)

    # Roles y permisos ya vienen de la migración 0009.
    await sesion.execute(
        text("INSERT INTO sucursales(id, codigo, nombre) VALUES (:id,'MATRIZ','Matriz')"),
        {"id": ids["sucursal"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO usuarios(id, sucursal_id, codigo, nombre, password_hash, rol_codigo, "
            "creado_en, actualizado_en) VALUES "
            "(:v,:s,'VEND01','Juan Pérez',:h,'vendedor',:t,:t), "
            "(:a,:s,'ADMIN01','Bryan',:h,'admin',:t,:t)"
        ),
        {
            "v": ids["vendedor"],
            "a": ids["admin"],
            "s": ids["sucursal"],
            "h": hash_password,
            "t": ahora,
        },
    )
    await sesion.execute(
        text(
            "INSERT INTO almacenes(id, codigo, nombre, tipo, responsable_id) VALUES "
            "(:b,'BODEGA_PRINCIPAL','Bodega','bodega',NULL), "
            "(:c,'CAMION_01','Camión 01','camion',:v)"
        ),
        {"b": ids["bodega"], "c": ids["camion"], "v": ids["vendedor"]},
    )
    await sesion.execute(
        text("UPDATE usuarios SET almacen_id = :c WHERE id = :v"),
        {"c": ids["camion"], "v": ids["vendedor"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO rutas(id, codigo, nombre, vendedor_id) VALUES (:r,'R04','Ruta 4',:v)"
        ),
        {"r": ids["ruta"], "v": ids["vendedor"]},
    )
    await sesion.execute(
        text("INSERT INTO usuarios_rutas(usuario_id, ruta_id) VALUES (:v,:r)"),
        {"v": ids["vendedor"], "r": ids["ruta"]},
    )
    await sesion.commit()
    ids["lista_precios"] = (
        await sesion.execute(text("SELECT id FROM listas_precios WHERE codigo = 'GENERAL'"))
    ).scalar_one()
    return ids
