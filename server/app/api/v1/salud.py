"""Endpoints de salud. Los consume el healthcheck de Docker y el panel."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import SesionDep

router = APIRouter(tags=["salud"])


class Salud(BaseModel):
    ok: bool
    base_de_datos: bool
    postgis: bool
    migraciones: int


@router.get("/salud", response_model=Salud)
async def salud(sesion: SesionDep) -> Salud:
    try:
        await sesion.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        return Salud(ok=False, base_de_datos=False, postgis=False, migraciones=0)

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
    return Salud(ok=db_ok and postgis, base_de_datos=db_ok, postgis=postgis, migraciones=tablas)
