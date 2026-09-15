"""Cola de trabajos sobre PostgreSQL.

`FOR UPDATE SKIP LOCKED` da una cola multi-consumidor sin duplicar trabajo. La
ventaja que ningún broker externo ofrece: **encolar queda en la misma
transacción que la escritura de negocio**. O se guardó la venta y se encoló su
procesamiento, o no ocurrió ninguna de las dos cosas.
"""

from __future__ import annotations

import json
import socket
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Un job 'ejecutando' cuyo worker murió (apagón, OOM) se recupera por tiempo.
# Sin heartbeat: una pieza móvil menos.
TIEMPO_MAXIMO_EJECUCION = timedelta(minutes=15)

_IDENTIDAD_WORKER = f"{socket.gethostname()}"


async def encolar(
    sesion: AsyncSession,
    tipo: str,
    payload: dict[str, Any] | None = None,
    *,
    clave_unica: str | None = None,
    prioridad: int = 100,
    retraso: timedelta | None = None,
) -> int | None:
    """Encola un job. NO hace commit: el llamador decide la transacción.

    Devuelve el id, o None si `clave_unica` ya tenía un job activo — encolar
    dos veces 'refrescar_vistas:2026-09-15' es un no-op, no un error.
    """
    ejecutar_en = datetime.now(UTC) + (retraso or timedelta())
    fila = (
        await sesion.execute(
            text(
                """
                INSERT INTO jobs (tipo, payload, clave_unica, prioridad, ejecutar_en)
                VALUES (:tipo, CAST(:payload AS jsonb), :clave, :prioridad, :ejecutar_en)
                ON CONFLICT DO NOTHING
                RETURNING id
                """
            ),
            {
                "tipo": tipo,
                "payload": json.dumps(payload or {}),
                "clave": clave_unica,
                "prioridad": prioridad,
                "ejecutar_en": ejecutar_en,
            },
        )
    ).scalar_one_or_none()
    return fila


async def tomar_lote(sesion: AsyncSession, limite: int = 10) -> list[dict[str, Any]]:
    """Toma hasta `limite` jobs listos y los marca 'ejecutando'.

    `SKIP LOCKED` hace que dos workers concurrentes nunca tomen el mismo job:
    el segundo simplemente salta las filas bloqueadas en vez de esperarlas.
    """
    filas = (
        await sesion.execute(
            text(
                """
                WITH listos AS (
                    SELECT id FROM jobs
                     WHERE estado = 'pendiente' AND ejecutar_en <= now()
                     ORDER BY prioridad, ejecutar_en, id
                     FOR UPDATE SKIP LOCKED
                     LIMIT :limite
                )
                UPDATE jobs j
                   SET estado = 'ejecutando',
                       tomado_por = :worker,
                       tomado_en = now(),
                       intentos = j.intentos + 1
                  FROM listos
                 WHERE j.id = listos.id
             RETURNING j.id, j.tipo, j.payload, j.intentos, j.max_intentos
                """
            ),
            {"limite": limite, "worker": _IDENTIDAD_WORKER},
        )
    ).mappings().all()
    return [dict(f) for f in filas]


async def marcar_hecho(sesion: AsyncSession, job_id: int) -> None:
    await sesion.execute(
        text("UPDATE jobs SET estado='hecho', terminado_en=now() WHERE id=:id"),
        {"id": job_id},
    )


async def marcar_fallido(sesion: AsyncSession, job_id: int, error: str) -> None:
    """Reintenta con backoff exponencial; al agotar intentos, queda 'fallido'.

    Un job fallido NO se borra: queda visible en el panel de operación. Un
    trabajo que desaparece en silencio es un descuadre esperando a suceder.
    """
    await sesion.execute(
        text(
            """
            UPDATE jobs
               SET estado = CASE WHEN intentos >= max_intentos THEN 'fallido' ELSE 'pendiente' END,
                   ejecutar_en = now() + (interval '10 seconds' * power(2, LEAST(intentos, 8))),
                   ultimo_error = :error,
                   terminado_en = CASE WHEN intentos >= max_intentos THEN now() END,
                   tomado_por = NULL,
                   tomado_en = NULL
             WHERE id = :id
            """
        ),
        {"id": job_id, "error": error[:2000]},
    )


async def recuperar_huerfanos(sesion: AsyncSession) -> int:
    """Devuelve a 'pendiente' los jobs cuyo worker murió a media ejecución."""
    resultado = await sesion.execute(
        text(
            """
            UPDATE jobs
               SET estado='pendiente', tomado_por=NULL, tomado_en=NULL,
                   ultimo_error='worker perdido; recuperado por tiempo'
             WHERE estado='ejecutando' AND tomado_en < now() - CAST(:limite AS interval)
            """
        ),
        {"limite": f"{int(TIEMPO_MAXIMO_EJECUCION.total_seconds())} seconds"},
    )
    return resultado.rowcount or 0
