"""Recálculo de los modelos de lectura del tablero (Fase 7).

Lo encola la ingesta de sincronización y el cierre de liquidación, con
`clave_unica = 'recalcular_tablero'`: mientras haya uno pendiente, ocho camiones
subiendo a la vez encolan **uno**. No hace falta que nadie acierte la fecha,
porque el job averigua por sí mismo qué días quedaron rancios — ver
`SQL_DIAS_SUCIOS` en `app/domain/tablero.py`.

Esa propiedad es la que lo hace autorreparable: si el worker estuvo caído dos
horas, la siguiente corrida recupera todo sin intervención.
"""

from __future__ import annotations

import logging
import time
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.tablero import (
    CATEGORIAS_NUESTRAS,
    MAXIMO_DIAS_POR_CORRIDA,
    SQL_BORRAR_DIA,
    SQL_BORRAR_MES_RUTA,
    SQL_DIA_VENDEDOR,
    SQL_DIAS_SUCIOS,
    SQL_ESTADO_DEL_MUNDO,
    SQL_MES_RUTA,
    SQL_SELLAR_DIA,
    inicio_de_mes,
)

log = logging.getLogger("dsd.tablero")

__all__ = [
    "dias_a_recalcular",
    "recalcular_dia",
    "recalcular_mes",
    "asegurar_fresco",
    "recalcular_todo",
]


async def dias_a_recalcular(sesion: AsyncSession, hoy: date | None = None) -> list[date]:
    """Los días rancios, más hoy.

    Hoy entra **siempre**, incluso si nada cambió. Dos razones:

    · El renglón de hoy tiene que existir para que el tablero pueda decir
      "nadie ha vendido" en vez de "sin datos" (ver `SQL_SELLAR_DIA`).
    · La antigüedad que muestra la tarjeta es `calculado_en`. Si hoy no se
      recalculara por no haber cambios, el tablero diría "hace 3 h" cuando la
      cifra es correcta — y quien lo lea va a creer que el sistema está caído.
    """
    hoy = hoy or date.today()
    filas = (
        await sesion.execute(text(SQL_DIAS_SUCIOS), {"limite": MAXIMO_DIAS_POR_CORRIDA})
    ).scalars()
    dias = set(filas)
    dias.add(hoy)
    return sorted(dias, reverse=True)


async def recalcular_dia(sesion: AsyncSession, fecha: date) -> int:
    """Recalcula un día completo. Devuelve cuántos renglones quedaron.

    Borrar e insertar, no `ON CONFLICT DO UPDATE`: si la única venta de un
    vendedor se canceló, su renglón tiene que DESAPARECER. Con un update
    quedaría ahí con la cifra anterior, y un tablero que muestra la venta de
    ayer como la de hoy es peor que uno vacío.
    """
    await sesion.execute(text(SQL_BORRAR_DIA), {"fecha": fecha})
    await sesion.execute(
        text(SQL_DIA_VENDEDOR), {"fecha": fecha, "nuestras": list(CATEGORIAS_NUESTRAS)}
    )
    # Solo el día de hoy se sella con los vendedores activos: sellar un día
    # pasado inventaría renglones en cero para vendedores que entraron después.
    if fecha == date.today():
        await sesion.execute(text(SQL_SELLAR_DIA), {"fecha": fecha})
    return (
        await sesion.execute(
            text("SELECT count(*) FROM tablero_dia WHERE fecha = :fecha"), {"fecha": fecha}
        )
    ).scalar_one()


async def recalcular_mes(sesion: AsyncSession, periodo: date) -> int:
    """Recalcula el avance del mes por ruta. `periodo` se normaliza al día 1."""
    periodo = inicio_de_mes(periodo)
    await sesion.execute(text(SQL_BORRAR_MES_RUTA), {"periodo": periodo})
    await sesion.execute(text(SQL_MES_RUTA), {"periodo": periodo})
    return (
        await sesion.execute(
            text("SELECT count(*) FROM tablero_mes_ruta WHERE periodo = :p"), {"p": periodo}
        )
    ).scalar_one()


async def recalcular_todo(sesion: AsyncSession, hoy: date | None = None) -> dict:
    """Una corrida completa: días rancios, sus meses y el sello.

    Hace **commit al final**, en una sola transacción. Al contrario que el
    refresh de la analítica —que commitea por vista porque cada una tarda
    segundos y sirve por separado— aquí las cifras se leen juntas en una sola
    pantalla: un commit parcial dejaría la venta del día ya actualizada y el
    mes de hace media hora, y nadie podría explicar la diferencia.
    """
    arranque = time.monotonic()
    # El worker y `asegurar_fresco` (la pantalla que lo pide) no pueden borrar e
    # insertar los mismos días a la vez. Se suelta con el commit.
    await sesion.execute(text("SELECT pg_advisory_xact_lock(hashtext('recalcular_tablero'))"))
    dias = await dias_a_recalcular(sesion, hoy)

    for dia in dias:
        await recalcular_dia(sesion, dia)

    # Los meses que tocaron esos días, una vez cada uno. Un recálculo al subir
    # treinta días rancios del mismo mes repetiría el mismo mes treinta veces.
    for periodo in sorted({inicio_de_mes(d) for d in dias}, reverse=True):
        await recalcular_mes(sesion, periodo)

    mundo = (await sesion.execute(text(SQL_ESTADO_DEL_MUNDO))).mappings().one()
    duracion_ms = int((time.monotonic() - arranque) * 1000)
    await sesion.execute(
        text(
            """
            INSERT INTO tablero_refrescos
                (id, calculado_en, duracion_ms, dias_recalculados,
                 equipos_sin_sincronizar, cola_reportada, ops_en_cuarentena)
            VALUES (true, now(), :ms, :dias, :equipos, :cola, :cuarentena)
            ON CONFLICT (id) DO UPDATE SET
                calculado_en = now(),
                duracion_ms = excluded.duracion_ms,
                dias_recalculados = excluded.dias_recalculados,
                equipos_sin_sincronizar = excluded.equipos_sin_sincronizar,
                cola_reportada = excluded.cola_reportada,
                ops_en_cuarentena = excluded.ops_en_cuarentena
            """
        ),
        {
            "ms": duracion_ms,
            "dias": len(dias),
            "equipos": mundo["equipos_sin_sincronizar"],
            "cola": mundo["cola_reportada"],
            "cuarentena": mundo["ops_en_cuarentena"],
        },
    )
    await sesion.commit()

    log.info("tablero recalculado: %s día(s) en %s ms", len(dias), duracion_ms)
    return {
        "dias": [d.isoformat() for d in dias],
        "duracion_ms": duracion_ms,
        "equipos_sin_sincronizar": mundo["equipos_sin_sincronizar"],
        "cola_reportada": mundo["cola_reportada"],
        "ops_en_cuarentena": mundo["ops_en_cuarentena"],
    }


# Cuánto puede tener el tablero antes de recalcularlo al pedirlo.
FRESCURA_MAXIMA_SEG = 120


async def asegurar_fresco() -> bool:
    """Si el tablero nunca se calculó, o tiene más de dos minutos, lo calcula aquí.

    Reportado en operación (octubre 2026): la gerencia entraba en el teléfono y
    leía «el servidor no ha calculado el tablero todavía». El recálculo lo hace el
    worker cuando entra una sincronización; si el worker no ha corrido —recién
    desplegado, caído, o sin ventas que lo disparen— nadie lo pedía nunca.

    Corre con el rol dueño, como el worker (son agregados de toda la operación),
    y con un candado que no espera: si otro ya está recalculando, se lee lo que
    haya. Nunca tumba la pantalla: si algo falla, se registra y se sigue.
    """
    from app.core.db import CrearSesion

    try:
        async with CrearSesion() as sesion:
            fresco = (
                await sesion.execute(
                    text(
                        "SELECT calculado_en > now() - make_interval(secs => :s) "
                        "  FROM tablero_refrescos WHERE id"
                    ),
                    {"s": FRESCURA_MAXIMA_SEG},
                )
            ).scalar_one_or_none()
            if fresco:
                return False
            libre = (
                await sesion.execute(
                    text("SELECT pg_try_advisory_xact_lock(hashtext('recalcular_tablero'))")
                )
            ).scalar_one()
            if not libre:
                return False
            await recalcular_todo(sesion)
            return True
    except Exception:  # noqa: BLE001 — una cifra vieja es mejor que una pantalla caída
        log.exception("no se pudo recalcular el tablero al pedirlo")
        return False
