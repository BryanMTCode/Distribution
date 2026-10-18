"""El mapa de Mazatlán, para que el teléfono lo guarde (ADR 0002 §130).

`GET /v1/mapa` dice si hay mapa, de qué fecha y cuánto pesa: con eso la app
decide si ofrecer «Descargar» o «Actualizar», y le dice al vendedor cuántos MB
son antes de bajarlos. `GET /v1/mapa/archivo` es el archivo.

Lo puede pedir cualquiera con credencial: el mapa es de OpenStreetMap, no tiene
nada de la operación. Las tiendas no van en el archivo; las pone la app con lo
que ya tiene en su base.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import ActorDep
from app.infra.mapa import ARCHIVO, directorio_de_mapas, leer_ficha

router = APIRouter(prefix="/mapa", tags=["mapa"])


class InfoDelMapa(BaseModel):
    disponible: bool
    version: str | None = None
    bytes: int | None = None
    sha256: str | None = None
    # Oeste, sur, este, norte.
    zona: tuple[float, float, float, float] | None = None
    zoom_maximo: int | None = None
    atribucion: str | None = None


@router.get("", response_model=InfoDelMapa)
async def info(_: ActorDep) -> InfoDelMapa:
    ficha = leer_ficha()
    if ficha is None:
        return InfoDelMapa(disponible=False)
    return InfoDelMapa(
        disponible=True,
        version=ficha.version,
        bytes=ficha.bytes,
        sha256=ficha.sha256,
        zona=ficha.zona,
        zoom_maximo=ficha.zoom_maximo,
        atribucion=ficha.atribucion,
    )


@router.get("/archivo")
async def archivo(_: ActorDep) -> FileResponse:
    ficha = leer_ficha()
    if ficha is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "todavía no hay mapa en el servidor: se baja con "
            "«python -m app.cli mapa-mazatlan»",
        )
    return FileResponse(
        directorio_de_mapas() / ARCHIVO,
        media_type="application/vnd.pmtiles",
        filename=ARCHIVO,
        headers={"ETag": f'"{ficha.sha256}"', "X-Mapa-Version": ficha.version},
    )
