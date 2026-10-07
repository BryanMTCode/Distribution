"""Salud y métricas. Los consume el healthcheck de Docker, el panel y el monitor."""

from __future__ import annotations

import hmac
from functools import cache
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import SesionDep
from app.core.config import obtener_config
from app.core.metricas import exponer

router = APIRouter(tags=["salud"])


class Salud(BaseModel):
    ok: bool
    base_de_datos: bool
    postgis: bool
    migraciones: int
    version: str

    # Si las políticas por renglón se están aplicando de verdad (Fase 9).
    #
    # Se reporta porque la diferencia entre «RLS escrito en una migración» y
    # «RLS en uso» es una variable de entorno sin poner, y eso no se nota
    # mirando la base: las políticas están ahí, simplemente el rol dueño las
    # salta. Que lo diga un endpoint permite comprobarlo después de desplegar.
    rls: bool

    # La revisión de Alembic en la base y la que trae este código. Si no son la
    # misma, el código nuevo corre contra una base vieja: las pantallas que leen
    # una columna nueva responden «Internal Server Error» y nada más lo explica.
    # Pasa cuando se reconstruye `api` sin reconstruir `migraciones`.
    esquema: str | None = None
    esquema_esperado: str | None = None


@cache
def _revision_del_codigo() -> str | None:
    """La última revisión de `db/alembic/versions`, leída una vez."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        raiz = Path(__file__).resolve().parents[3]
        cfg = Config(str(raiz / "alembic.ini"))
        cfg.set_main_option("script_location", str(raiz / "db" / "alembic"))
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:
        return None


@router.get("/salud", response_model=Salud)
async def salud(sesion: SesionDep) -> Salud:
    cfg = obtener_config()
    try:
        await sesion.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        return Salud(
            ok=False,
            base_de_datos=False,
            postgis=False,
            migraciones=0,
            version=cfg.version,
            rls=cfg.rls_activa,
        )

    postgis = bool(
        (
            await sesion.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'postgis')")
            )
        ).scalar_one()
    )
    tablas = (
        await sesion.execute(
            text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
        )
    ).scalar_one()
    try:
        async with sesion.begin_nested():
            esquema = (
                await sesion.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
    except Exception:
        esquema = None
    esperado = _revision_del_codigo()
    al_dia = esquema is None or esperado is None or esquema == esperado
    return Salud(
        ok=db_ok and postgis and al_dia,
        base_de_datos=db_ok,
        postgis=postgis,
        migraciones=tablas,
        version=cfg.version,
        rls=cfg.rls_activa,
        esquema=esquema,
        esquema_esperado=esperado,
    )


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------
# Fuera del esquema público: no es un contrato con el dispositivo, es un
# endpoint de operación, y dejarlo en el OpenAPI haría que el generador de Dart
# intentara crearle un cliente.
@router.get("/metrics", include_in_schema=False)
async def metricas(peticion: Request, sesion: SesionDep) -> Response:
    """Formato de texto de Prometheus. Apagado si no hay token configurado.

    ────────────────────────────────────────────────────────────────────────
    POR QUÉ 404 Y NO 401 CUANDO ESTÁ APAGADO
    ────────────────────────────────────────────────────────────────────────
    Un 401 confirma que hay un endpoint de métricas ahí y que solo falta la
    credencial — y eso es información que no hace falta regalar a quien escanea.
    Un 404 es indistinguible de «esta ruta no existe en este sistema».

    Por la misma razón, un token equivocado también devuelve 404: si devolviera
    401, el endpoint sería enumerable probando tokens.

    ────────────────────────────────────────────────────────────────────────
    POR QUÉ UN TOKEN Y NO UN PERMISO
    ────────────────────────────────────────────────────────────────────────
    Lo consume un agente, no una persona: Prometheus manda una cabecera fija y
    no sabe renovar un JWT de 30 minutos. El token va en la configuración del
    agente y se compara en tiempo constante.
    """
    cfg = obtener_config()
    if not cfg.metricas_token:
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    presentado = peticion.headers.get("authorization", "")
    esperado = f"Bearer {cfg.metricas_token}"
    # `compare_digest`: una comparación con `==` se corta en el primer byte
    # distinto y filtra el prefijo del token por tiempo de respuesta.
    if not hmac.compare_digest(presentado, esperado):
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    cuerpo = await exponer(sesion)
    return Response(
        content=cuerpo,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
