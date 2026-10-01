"""El job que recalcula el esquema estrella de la Fase 8.

────────────────────────────────────────────────────────────────────────────
POR QUÉ UN REFRESH COMPLETO Y NO UNA CARGA INCREMENTAL
────────────────────────────────────────────────────────────────────────────
Es la decisión de diseño de todo el laboratorio, y es propia de un DSD.

Una carga incremental procesa "lo creado desde la última corrida". Aquí **los
datos llegan tarde por diseño**: una venta del lunes puede sincronizar el jueves
porque el teléfono no tuvo señal (§0.3). Un incremental por `creado_en` la
cargaría con fecha de jueves, y un incremental por `fecha_operativa` no la
cargaría nunca — el lunes quedaría subreportado para siempre y nada lo avisaría.

Un `REFRESH MATERIALIZED VIEW` recalcula desde la verdad transaccional, así que
es **imposible** que se desincronice. A miles de tickets diarios cuesta
segundos. Cuando el volumen lo pida, el camino es particionar por fecha, no
volverse incremental.

────────────────────────────────────────────────────────────────────────────
EL ORDEN NO ES ALFABÉTICO
────────────────────────────────────────────────────────────────────────────
Las dimensiones van antes que los hechos. Ninguna vista de hechos depende en SQL
de una dimensión —se unen al consultar, no al materializar— pero si alguien abre
el laboratorio justo en medio del refresh, con este orden ve dimensiones nuevas
con hechos viejos, que como mucho muestra un cliente de más. Al revés vería
hechos nuevos con dimensiones viejas, y eso sí pierde renglones: una venta a un
cliente que la dimensión todavía no tiene desaparece del reporte.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("dsd.analitica")

# Dimensiones primero. Ver el encabezado: el orden protege al que está mirando.
VISTAS: tuple[str, ...] = (
    "dim_cliente",
    "dim_producto",
    "dim_vendedor",
    "dim_ruta",
    "fact_ventas",
    "fact_visitas",
    "fact_movimientos",
)


async def refrescar_una(sesion: AsyncSession, vista: str) -> dict[str, Any]:
    """Refresca una vista y deja registrado cuándo y con qué resultado.

    Usa `CONCURRENTLY` salvo que la vista nunca se haya poblado. Las dos cosas
    importan:

    · **Con** `CONCURRENTLY`, el refresh no toma un ACCESS EXCLUSIVE y el
      laboratorio sigue respondiendo mientras corre. Sin él, cualquiera que esté
      consultando se congela.
    · **Sin** él en el primer refresh, porque PostgreSQL lo rechaza:
      «CONCURRENTLY cannot be used when the materialized view is not populated».
      La migración las crea CON DATOS, así que este caso solo aparece si alguien
      las recreó a mano — y entonces el job tiene que funcionar igual, no fallar.
    """
    if vista not in VISTAS:
        # La vista entra en una sentencia SQL por interpolación —no hay forma de
        # parametrizar un nombre de objeto— así que se valida contra la lista
        # blanca. Sin esto, el `payload` de un job sería inyección de SQL.
        raise ValueError(f"vista desconocida: {vista!r}")

    poblada = (
        await sesion.execute(
            text("SELECT relispopulated FROM pg_class WHERE relname = :v AND relkind = 'm'"),
            {"v": vista},
        )
    ).scalar_one_or_none()
    if poblada is None:
        raise RuntimeError(f"la vista materializada '{vista}' no existe")

    concurrente = "CONCURRENTLY " if poblada else ""
    inicio = time.monotonic()
    await sesion.execute(text(f"REFRESH MATERIALIZED VIEW {concurrente}{vista}"))  # noqa: S608
    duracion_ms = int((time.monotonic() - inicio) * 1000)

    renglones = (
        await sesion.execute(text(f"SELECT count(*) FROM {vista}"))  # noqa: S608
    ).scalar_one()

    # La foto del mundo en el momento del cálculo. No es adorno: si tres equipos
    # no habían sincronizado, las cifras del día están incompletas, y quien las
    # lea tiene que poder saberlo sin preguntar.
    salud = (
        await sesion.execute(
            text(
                """
                SELECT count(*) FILTER (
                           WHERE d.ultima_sync_push_en IS NULL
                              OR d.ultima_sync_push_en < CURRENT_DATE
                       ) AS rezagados,
                       (SELECT count(*) FROM sync_cuarentena
                         WHERE estado = 'pendiente') AS cuarentena
                  FROM dispositivos d
                 WHERE d.estado = 'activo'
                """
            )
        )
    ).mappings().one()

    await sesion.execute(
        text(
            """
            INSERT INTO analitica_refrescos
                (vista, refrescado_en, duracion_ms, renglones,
                 equipos_sin_sincronizar, ops_en_cuarentena)
            VALUES (:v, now(), :ms, :n, :rezagados, :cuarentena)
            ON CONFLICT (vista) DO UPDATE
               SET refrescado_en = now(),
                   duracion_ms = excluded.duracion_ms,
                   renglones = excluded.renglones,
                   equipos_sin_sincronizar = excluded.equipos_sin_sincronizar,
                   ops_en_cuarentena = excluded.ops_en_cuarentena
            """
        ),
        {
            "v": vista,
            "ms": duracion_ms,
            "n": renglones,
            "rezagados": salud["rezagados"],
            "cuarentena": salud["cuarentena"],
        },
    )
    return {
        "vista": vista,
        "concurrente": bool(poblada),
        "duracion_ms": duracion_ms,
        "renglones": renglones,
    }


async def refrescar_todo(sesion: AsyncSession, vistas: tuple[str, ...] = VISTAS) -> list[dict]:
    """Refresca las vistas en orden y **no se detiene en la primera que falle**.

    Si `fact_ventas` falla, `fact_visitas` todavía puede refrescarse, y media
    actualización sirve más que ninguna: lo que queda viejo lo dice su propio
    renglón en `analitica_refrescos`, y el laboratorio lo muestra.

    Un `raise` a la primera falla haría que un problema en una vista dejara todo
    el laboratorio congelado en la foto de ayer, sin decir cuál fue el problema.
    """
    resultados: list[dict] = []
    fallas: list[str] = []

    for vista in vistas:
        try:
            resultado = await refrescar_una(sesion, vista)
            # Commit por vista: cada una queda disponible en cuanto termina, y
            # una falla posterior no deshace el trabajo que ya salió bien.
            await sesion.commit()
            resultados.append(resultado)
            log.info(
                "refrescada %s: %d renglones en %d ms",
                vista, resultado["renglones"], resultado["duracion_ms"],
            )
        except Exception as e:  # noqa: BLE001 — se registra y se sigue, a propósito
            await sesion.rollback()
            fallas.append(f"{vista}: {e}")
            log.exception("falló el refresh de %s", vista)
            resultados.append({"vista": vista, "error": str(e)})

    if fallas:
        # Se informa DESPUÉS de intentarlo todo, para que el job quede marcado
        # como fallido y se reintente, pero con lo que sí se pudo ya actualizado.
        raise RuntimeError("refresh incompleto — " + "; ".join(fallas))

    return resultados
