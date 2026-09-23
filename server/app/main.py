"""Ensamblado de la aplicación. Aquí no vive lógica de negocio."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1 import auth, catalogo, clientes, dispositivos, salud
from app.core.config import obtener_config
from app.core.db import motor


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI):
    yield
    await motor.dispose()


def crear_app() -> FastAPI:
    cfg = obtener_config()
    app = FastAPI(
        title="Sistema DSD",
        version="0.1.0",
        description=(
            "API del sistema DSD. El contrato con el dispositivo se genera de aquí; "
            "ver contracts/README.md §3 para el degradado a OpenAPI 3.0."
        ),
        debug=cfg.debug,
        lifespan=ciclo_de_vida,
    )
    app.include_router(salud.router)
    app.include_router(auth.router, prefix="/v1")
    app.include_router(dispositivos.router, prefix="/v1")
    app.include_router(catalogo.router, prefix="/v1")
    app.include_router(clientes.router, prefix="/v1")
    return app


app = crear_app()
