"""Proceso worker: drena la cola y recupera huérfanos.

Se ejecuta como servicio aparte en Docker Compose:
    python -m app.workers.principal
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.db import CrearSesion
from app.workers.cola import (
    marcar_fallido,
    marcar_hecho,
    recuperar_huerfanos,
    tomar_lote,
)

log = logging.getLogger("dsd.worker")

INTERVALO_VACIO = 2.0        # segundos de espera cuando no hay trabajo
INTERVALO_HUERFANOS = 60.0   # cada cuánto se buscan jobs de workers muertos

# Registro de manejadores. Cada fase agrega los suyos aquí.
MANEJADORES: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {}


def manejador(tipo: str):
    def decorador(fn):
        MANEJADORES[tipo] = fn
        return fn

    return decorador


@manejador("ping")
async def _ping(payload: dict[str, Any]) -> None:
    """Job de humo: confirma que el worker está vivo y procesando."""
    log.info("ping %s", payload)


async def procesar_uno(job: dict[str, Any]) -> None:
    manejador_fn = MANEJADORES.get(job["tipo"])
    if manejador_fn is None:
        raise RuntimeError(f"sin manejador para el tipo '{job['tipo']}'")
    await manejador_fn(job["payload"])


async def ciclo(detener: asyncio.Event) -> None:
    ultimo_barrido = 0.0
    while not detener.is_set():
        ahora = asyncio.get_running_loop().time()
        if ahora - ultimo_barrido > INTERVALO_HUERFANOS:
            async with CrearSesion() as sesion:
                recuperados = await recuperar_huerfanos(sesion)
                await sesion.commit()
            if recuperados:
                log.warning("se recuperaron %s jobs de workers perdidos", recuperados)
            ultimo_barrido = ahora

        async with CrearSesion() as sesion:
            lote = await tomar_lote(sesion, limite=10)
            await sesion.commit()

        if not lote:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(detener.wait(), timeout=INTERVALO_VACIO)
            continue

        for job in lote:
            # Cada job en su propia sesión: uno que falla no arrastra al resto.
            async with CrearSesion() as sesion:
                try:
                    await procesar_uno(job)
                    await marcar_hecho(sesion, job["id"])
                except Exception as e:  # noqa: BLE001 — el error se persiste, no se traga
                    log.exception("job %s (%s) falló", job["id"], job["tipo"])
                    await marcar_fallido(sesion, job["id"], f"{type(e).__name__}: {e}")
                await sesion.commit()


async def principal() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    detener = asyncio.Event()
    bucle = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        bucle.add_signal_handler(sig, detener.set)

    log.info("worker iniciado; %s manejadores registrados", len(MANEJADORES))
    await ciclo(detener)
    log.info("worker detenido limpiamente")


if __name__ == "__main__":
    asyncio.run(principal())
