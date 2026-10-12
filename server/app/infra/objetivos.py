"""Objetivos de cualquier periodo, con su avance (ADR 0002 §104).

«Quiero poder agregar los objetivos en la app; los objetivos pueden ser por el
periodo que yo decida, incluso por día.»

Dos clases de objetivo, una sola lista:

  · **Del mes, por ruta** (`objetivos_ruta`, 0021): el que dibuja la barra del
    tablero. Si en la app o en el panel se escoge un mes completo para una
    ruta, se guarda aquí, para que la barra lo tome.
  · **De cualquier periodo** (`objetivos`, 0055): un día, una semana, del 3 al
    17; de una ruta o de toda la empresa.

El avance se calcula al pedirlo con las ventas del rango, y se lee igual que
la barra del tablero (`domain/tablero.Avance`): lo logrado contra lo esperado
a la fecha, prorrateado por días naturales.

Lo usan el panel (`api/admin/objetivos.py`) y la app (`api/v1/objetivos.py`).
"""

from __future__ import annotations

import calendar
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text

from app.domain.tablero import Avance

# Un error de dedo, no una regla: igual que el del objetivo mensual.
OBJETIVO_MAXIMO = Decimal("50000000")
DIAS_MAXIMOS = 366

# Los terminados hace más de esto ya no se listan: la lista es para operar.
DIAS_DE_HISTORIA = 60


class ObjetivoInvalido(Exception):
    """El objetivo no se guardó, y el mensaje dice por qué."""


@dataclass(frozen=True)
class ObjetivoConAvance:
    id: str
    # 'periodo' (tabla `objetivos`) o 'mes' (el del mes por ruta).
    clase: str
    ruta_id: uuid.UUID | None
    ruta: str | None
    desde: date
    hasta: date
    objetivo_venta: Decimal
    objetivo_visitas: int | None
    nota: str | None
    venta: Decimal
    visitas: int
    avance: Avance

    @property
    def estado(self) -> str:
        """'por_empezar' | 'en_curso' | 'terminado'."""
        hoy = date.today()
        if hoy < self.desde:
            return "por_empezar"
        if hoy > self.hasta:
            return "terminado"
        return "en_curso"

    @property
    def dias(self) -> int:
        return (self.hasta - self.desde).days + 1


def fin_de_mes(dia: date) -> date:
    return dia.replace(day=calendar.monthrange(dia.year, dia.month)[1])


def es_mes_completo(desde: date, hasta: date) -> bool:
    return desde.day == 1 and hasta == fin_de_mes(desde)


def _avance(venta: Decimal, objetivo: Decimal, desde: date, hasta: date) -> Avance:
    """El esperado a la fecha: nada antes de empezar, todo después de acabar."""
    hoy = date.today()
    dias = (hasta - desde).days + 1
    if hoy < desde:
        transcurridos = 0
    elif hoy > hasta:
        transcurridos = dias
    else:
        transcurridos = (hoy - desde).days + 1
    return Avance(venta=venta, objetivo=objetivo, dia_del_mes=transcurridos, dias_del_mes=dias)


async def _vendido(sesion, ruta_id, desde: date, hasta: date) -> tuple[Decimal, int]:
    """Lo vendido y las visitas (cliente-día con venta o no-drop) del rango.

    Las ventas de mayoreo cuentan en los objetivos de toda la empresa, no en
    los de una ruta: no las hizo ningún vendedor.
    """
    fila = (
        await sesion.execute(
            text(
                """
                SELECT
                  COALESCE((SELECT sum(v.total) FROM ventas v
                             WHERE v.estado = 'confirmada'
                               AND v.fecha_operativa BETWEEN :desde AND :hasta
                               AND (CAST(:ruta AS uuid) IS NULL OR v.ruta_id = :ruta)), 0)
                  + CASE WHEN CAST(:ruta AS uuid) IS NULL THEN COALESCE((
                        SELECT sum(m.total) FROM ventas_mayoreo m
                         WHERE m.estado = 'confirmada'
                           AND m.fecha BETWEEN :desde AND :hasta), 0)
                    ELSE 0 END AS venta,
                  (SELECT count(*) FROM (
                       SELECT cliente_id, fecha_operativa FROM ventas
                        WHERE fecha_operativa BETWEEN :desde AND :hasta
                          AND (CAST(:ruta AS uuid) IS NULL OR ruta_id = :ruta)
                       UNION
                       SELECT cliente_id, fecha_operativa FROM no_drops
                        WHERE fecha_operativa BETWEEN :desde AND :hasta
                          AND (CAST(:ruta AS uuid) IS NULL OR ruta_id = :ruta)
                  ) x) AS visitas
                """
            ),
            {"ruta": ruta_id, "desde": desde, "hasta": hasta},
        )
    ).mappings().one()
    return Decimal(fila["venta"]), int(fila["visitas"])


async def objetivos_con_avance(
    sesion, *, incluir_terminados: bool = True
) -> list[ObjetivoConAvance]:
    """Los vigentes y los que vienen, y los terminados hace poco; de las dos
    clases, del más próximo al más lejano."""
    hoy = date.today()
    corte = hoy - timedelta(days=DIAS_DE_HISTORIA if incluir_terminados else 0)
    filas = (
        await sesion.execute(
            text(
                """
                SELECT CAST(o.id AS text) AS id, 'periodo' AS clase, o.ruta_id,
                       r.codigo || ' · ' || r.nombre AS ruta,
                       o.desde, o.hasta, o.objetivo_venta, o.objetivo_visitas, o.nota
                  FROM objetivos o
                  LEFT JOIN rutas r ON r.id = o.ruta_id
                 WHERE o.hasta >= :corte
                UNION ALL
                SELECT 'mes-' || o.ruta_id || '-' || to_char(o.periodo, 'YYYY-MM'),
                       'mes', o.ruta_id, r.codigo || ' · ' || r.nombre,
                       o.periodo,
                       (o.periodo + interval '1 month' - interval '1 day')::date,
                       o.objetivo_venta, o.objetivo_visitas, o.nota
                  FROM objetivos_ruta o
                  JOIN rutas r ON r.id = o.ruta_id
                 WHERE (o.periodo + interval '1 month' - interval '1 day')::date >= :corte
                 ORDER BY desde, hasta, ruta NULLS FIRST
                """
            ),
            {"corte": corte},
        )
    ).mappings().all()
    resultado = []
    for f in filas:
        hasta_conteo = min(f["hasta"], hoy)
        if f["desde"] > hoy:
            venta, visitas = Decimal("0"), 0
        else:
            venta, visitas = await _vendido(sesion, f["ruta_id"], f["desde"], hasta_conteo)
        objetivo = Decimal(f["objetivo_venta"])
        resultado.append(
            ObjetivoConAvance(
                id=f["id"],
                clase=f["clase"],
                ruta_id=f["ruta_id"],
                ruta=f["ruta"],
                desde=f["desde"],
                hasta=f["hasta"],
                objetivo_venta=objetivo,
                objetivo_visitas=f["objetivo_visitas"],
                nota=f["nota"],
                venta=venta,
                visitas=visitas,
                avance=_avance(venta, objetivo, f["desde"], f["hasta"]),
            )
        )
    return resultado


async def guardar_objetivo(
    sesion,
    *,
    ruta_id: uuid.UUID | None,
    desde: date,
    hasta: date,
    objetivo_venta: Decimal,
    objetivo_visitas: int | None,
    nota: str | None,
    quien: uuid.UUID | None,
) -> str:
    """Guarda un objetivo y devuelve su id. No hace commit.

    Un mes completo de una ruta es el objetivo del mes: va a `objetivos_ruta`
    —si ya había uno, lo reemplaza— para que la barra del tablero lo tome.
    """
    if hasta < desde:
        desde, hasta = hasta, desde
    if (hasta - desde).days + 1 > DIAS_MAXIMOS:
        raise ObjetivoInvalido("Un objetivo abarca a lo más un año.")
    if objetivo_venta <= 0:
        raise ObjetivoInvalido("Escribe cuánto se tiene que vender.")
    if objetivo_venta > OBJETIVO_MAXIMO:
        raise ObjetivoInvalido(
            f"Un objetivo de ${objetivo_venta:,.2f} parece un error de dedo."
        )
    if objetivo_visitas is not None and objetivo_visitas <= 0:
        objetivo_visitas = None
    if ruta_id is not None:
        existe = (
            await sesion.execute(text("SELECT 1 FROM rutas WHERE id = :r"), {"r": ruta_id})
        ).first()
        if existe is None:
            raise ObjetivoInvalido("Esa ruta ya no existe.")

    if ruta_id is not None and es_mes_completo(desde, hasta):
        await sesion.execute(
            text(
                """
                INSERT INTO objetivos_ruta
                    (ruta_id, periodo, objetivo_venta, objetivo_visitas, nota, fijado_por)
                VALUES (:r, :p, :monto, :visitas, :nota, :quien)
                ON CONFLICT (ruta_id, periodo) DO UPDATE SET
                    objetivo_venta   = excluded.objetivo_venta,
                    objetivo_visitas = excluded.objetivo_visitas,
                    nota             = excluded.nota,
                    fijado_por       = excluded.fijado_por,
                    actualizado_en   = now()
                """
            ),
            {"r": ruta_id, "p": desde, "monto": objetivo_venta, "visitas": objetivo_visitas,
             "nota": nota, "quien": quien},
        )
        return f"mes-{ruta_id}-{desde:%Y-%m}"

    nuevo = uuid.uuid4()
    await sesion.execute(
        text(
            """
            INSERT INTO objetivos (id, ruta_id, desde, hasta, objetivo_venta,
                                   objetivo_visitas, nota, fijado_por)
            VALUES (:id, :r, :desde, :hasta, :monto, :visitas, :nota, :quien)
            """
        ),
        {"id": nuevo, "r": ruta_id, "desde": desde, "hasta": hasta, "monto": objetivo_venta,
         "visitas": objetivo_visitas, "nota": nota, "quien": quien},
    )
    return str(nuevo)


async def borrar_objetivo(sesion, objetivo_id: str) -> bool:
    """Quita un objetivo de cualquiera de las dos clases. No hace commit."""
    if objetivo_id.startswith("mes-"):
        try:
            ruta = uuid.UUID(objetivo_id[4:40])
            periodo = date.fromisoformat(objetivo_id[41:] + "-01")
        except ValueError:
            return False
        r = await sesion.execute(
            text("DELETE FROM objetivos_ruta WHERE ruta_id = :r AND periodo = :p"),
            {"r": ruta, "p": periodo},
        )
        return r.rowcount > 0
    try:
        uid = uuid.UUID(objetivo_id)
    except ValueError:
        return False
    r = await sesion.execute(text("DELETE FROM objetivos WHERE id = :id"), {"id": uid})
    return r.rowcount > 0


async def rutas_para_objetivos(sesion) -> list[dict]:
    return [
        dict(f)
        for f in (
            await sesion.execute(
                text("SELECT id, codigo, nombre FROM rutas WHERE activo ORDER BY codigo")
            )
        ).mappings().all()
    ]
