"""El middleware de observabilidad (Fase 9).

Hace tres cosas por petición, y las tres existen para contestar preguntas en un
incidente:

1. **Asigna un `peticion_id`** y lo devuelve en `X-Peticion-Id`. Cuando el
   vendedor dice «no me llegó», ese identificador —que la app guarda en su
   bitácora de sync— es lo que permite encontrar la petición entre las de ocho
   camiones sincronizando a la vez.
2. **Registra una línea por petición** con método, ruta, estado y duración. Sin
   el payload (ver `core/registro.py`).
3. **Cuenta y cronometra** para `/metrics`.

────────────────────────────────────────────────────────────────────────────
LA RUTA SE ETIQUETA CON LA PLANTILLA, NO CON LA URL
────────────────────────────────────────────────────────────────────────────
`/v1/clientes/{cliente_id}/cartera`, no `/v1/clientes/8f3a…/cartera`. Dos
razones, y la segunda importa más:

· Con la URL real, cada cliente sería una serie temporal distinta y Prometheus
  explotaría en cardinalidad.
· Un UUID de cliente en una etiqueta de métrica es un dato de negocio metido en
  un sistema de monitoreo, que es justo lo que este módulo no hace.

────────────────────────────────────────────────────────────────────────────
UNA EXCEPCIÓN NO MANEJADA SE REGISTRA Y SE VUELVE A LANZAR
────────────────────────────────────────────────────────────────────────────
No se traga: FastAPI tiene que seguir devolviendo su 500. Lo que se hace es
dejarla en el log CON su `peticion_id` y contarla, para que un error en
producción no sea solo un 500 en la pantalla de alguien.
"""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from app.core.metricas import registro
from app.core.registro import contexto, fijar_contexto, nuevo_id_de_peticion

log = logging.getLogger("dsd.http")

CABECERA_ID = "X-Peticion-Id"

# Rutas que no se registran una por una.
#
# El healthcheck de Docker pega cada 30 segundos y el scrape de métricas cada
# 15: entre los dos son ~7,000 líneas al día que no dicen nada y que entierran
# las que sí. Sus contadores de métricas SÍ se llevan, así que si el healthcheck
# empieza a fallar se ve en `dsd_peticiones_total`.
SIN_BITACORA = frozenset({"/salud", "/metrics"})


class Observabilidad(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, peticion: Request, siguiente):
        # Se respeta el identificador que venga de fuera: Caddy o el túnel
        # pueden poner uno, y conservarlo permite cruzar sus logs con los de
        # aquí. Si no viene, se genera.
        entrante = peticion.headers.get(CABECERA_ID)
        peticion_id = entrante[:64] if entrante else nuevo_id_de_peticion()
        fijar_contexto({"peticion_id": peticion_id})

        arranque = time.perf_counter()
        try:
            respuesta = await siguiente(peticion)
        except Exception:
            duracion = time.perf_counter() - arranque
            ruta = _plantilla(peticion)
            registro.anotar(peticion.method, ruta, 500, duracion)
            registro.anotar_error(ruta)
            log.exception(
                "excepción no manejada",
                extra={
                    "metodo": peticion.method,
                    "ruta": ruta,
                    "duracion_ms": round(duracion * 1000, 1),
                },
            )
            raise

        duracion = time.perf_counter() - arranque
        ruta = _plantilla(peticion)
        registro.anotar(peticion.method, ruta, respuesta.status_code, duracion)
        respuesta.headers[CABECERA_ID] = peticion_id

        if peticion.url.path not in SIN_BITACORA:
            # El nivel depende del resultado: un 5xx tiene que destacar en un
            # archivo con miles de líneas de 200.
            nivel = (
                logging.ERROR
                if respuesta.status_code >= 500
                else logging.WARNING
                if respuesta.status_code >= 400
                else logging.INFO
            )
            log.log(
                nivel,
                "%s %s → %s",
                peticion.method,
                ruta,
                respuesta.status_code,
                extra={
                    "metodo": peticion.method,
                    "ruta": ruta,
                    "estado": respuesta.status_code,
                    "duracion_ms": round(duracion * 1000, 1),
                    **{
                        k: v for k, v in contexto().items()
                        if k in ("usuario_id", "dispositivo_id", "rol")
                    },
                },
            )
        return respuesta


def _plantilla(peticion: Request) -> str:
    """La plantilla de la ruta, o la URL cruda si no hubo coincidencia.

    `route` lo pone Starlette al resolver; en un 404 no existe, y entonces se
    devuelve un literal en vez de la URL pedida: un escaneo automatizado
    probando `/wp-admin/…` crearía una serie temporal por cada intento.
    """
    ruta = peticion.scope.get("route")
    if ruta is not None and getattr(ruta, "path", None):
        return str(ruta.path)
    return "«sin ruta»"
