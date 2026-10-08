"""La cuenta del vendedor: los cargos que nacen solos al cerrar el Corte del día.

Regla de la dirección (octubre 2026): **la mercancía se cobra a costo**, no a
precio de venta. Se recupera la pérdida real del inventario; no se gana margen
con el error de un empleado. El costo es el promedio ponderado de
`producto_costos` (ADR 0002 §41), leído en el momento del cierre y congelado en
el `detalle` del cargo: si el costo cambia mañana, lo que se cobró hoy no cambia.

Tres cargos posibles por Corte, cada uno una vez (índice único en la base):

    faltante_mercancia  lo contado quedó abajo de lo esperado
    merma_atribuible    las mermas del día con motivo `afecta_vendedor`
    faltante_efectivo   entregó menos efectivo del esperado — solo si se hizo el
                        arqueo: `efectivo_entregado` nace en 0, y «nadie contó»
                        no es «entregó $0»

Un producto SIN costo capturado no se cobra en cero a escondidas: queda en el
detalle con `costo: null` y el cierre lo dice, para que la oficina capture el
costo y cargue el resto a mano si corresponde.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CENTAVO = Decimal("0.01")

# Cómo se le dice a una persona cada origen. Lo usan la pantalla de la cuenta y
# el Corte del día.
ORIGENES = {
    "faltante_mercancia": "Faltante de mercancía",
    "merma_atribuible": "Merma a cargo del vendedor",
    "faltante_efectivo": "Faltante de efectivo",
    "cobro_no_entregado": "Cobro que no llegó a la empresa",
    "transferencia_no_llego": "Transferencia que no llegó",
    "cargo_manual": "Cargo",
    "descuento_nomina": "Descuento de nómina",
    "pago": "Pago",
    "condonacion": "Condonado",
}


@dataclass
class CargosDelCorte:
    """Lo que el cierre le cargó al vendedor, para decírselo a quien cerró."""

    mercancia: Decimal = Decimal(0)
    merma: Decimal = Decimal(0)
    efectivo: Decimal = Decimal(0)
    sin_costo: list[str] = field(default_factory=list)
    sin_arqueo: bool = False

    @property
    def total(self) -> Decimal:
        return self.mercancia + self.merma + self.efectivo


def _a_costo(renglones) -> tuple[Decimal, list[dict], list[str]]:
    """Valúa cada renglón a costo. Devuelve el total, el detalle y los sin costo."""
    total = Decimal(0)
    detalle: list[dict] = []
    sin_costo: list[str] = []
    for r in renglones:
        cantidad = Decimal(r["cantidad"])
        costo = r["costo"]
        importe = None
        if costo is not None:
            # Por renglón y luego se suma: así el detalle que ve el vendedor suma
            # exactamente el cargo, sin un centavo de diferencia que explicar.
            importe = (cantidad * Decimal(costo)).quantize(CENTAVO, ROUND_HALF_UP)
            total += importe
        else:
            sin_costo.append(r["nombre"])
        detalle.append(
            {
                "producto_id": str(r["producto_id"]),
                "sku": r["sku"],
                "nombre": r["nombre"],
                "cantidad": str(cantidad),
                "costo": None if costo is None else str(costo),
                "importe": None if importe is None else str(importe),
                **({"motivo": r["motivo"]} if r.get("motivo") else {}),
            }
        )
    return total, detalle, sin_costo


async def _insertar_cargo(
    sesion: AsyncSession,
    *,
    vendedor_id: uuid.UUID,
    origen: str,
    importe: Decimal,
    fecha: date,
    concepto: str,
    detalle: list[dict] | dict | None,
    liquidacion_id: uuid.UUID,
    quien: uuid.UUID | None,
    ahora: datetime,
) -> None:
    await sesion.execute(
        text(
            """
            INSERT INTO cuenta_vendedor
              (vendedor_id, tipo, origen, importe, fecha, concepto, detalle,
               liquidacion_id, registrado_por, registrado_en)
            VALUES (:v, 'cargo', :origen, :importe, :fecha, :concepto,
                    CAST(:detalle AS jsonb), :l, :quien, :ahora)
            ON CONFLICT (liquidacion_id, origen) WHERE liquidacion_id IS NOT NULL
              DO NOTHING
            """
        ),
        {
            "v": vendedor_id,
            "origen": origen,
            "importe": importe,
            "fecha": fecha,
            "concepto": concepto,
            "detalle": json.dumps(detalle, ensure_ascii=False) if detalle is not None else None,
            "l": liquidacion_id,
            "quien": quien,
            "ahora": ahora,
        },
    )


async def cargar_el_corte(
    sesion: AsyncSession,
    *,
    liquidacion_id: uuid.UUID,
    quien: uuid.UUID,
    ahora: datetime,
) -> CargosDelCorte:
    """Escribe los cargos del Corte en la cuenta del vendedor.

    Se llama DENTRO de la transacción del cierre, después de recalcular las
    cifras: lo que se carga es exactamente lo que la pantalla del Corte dice.
    """
    corte = (
        await sesion.execute(
            text(
                """
                SELECT l.folio, l.vendedor_id, l.fecha_operativa, l.arqueo_en,
                       l.efectivo_esperado, l.efectivo_entregado,
                       l.diferencia_efectivo, c.almacen_destino_id AS camion
                  FROM liquidaciones l JOIN cargas c ON c.id = l.carga_id
                 WHERE l.id = :l
                """
            ),
            {"l": liquidacion_id},
        )
    ).mappings().one()
    resumen = CargosDelCorte()
    comunes = {
        "vendedor_id": corte["vendedor_id"],
        "fecha": corte["fecha_operativa"],
        "liquidacion_id": liquidacion_id,
        "quien": quien,
        "ahora": ahora,
    }

    # ------------------------------------------------------------------
    # Mercancía: lo que el conteo encontró de menos.
    # ------------------------------------------------------------------
    faltantes = (
        await sesion.execute(
            text(
                """
                SELECT d.producto_id, p.sku, p.nombre, -d.diferencia AS cantidad,
                       pc.costo_promedio AS costo
                  FROM liquidacion_detalle d
                  JOIN productos p ON p.id = d.producto_id
                  LEFT JOIN producto_costos pc ON pc.producto_id = d.producto_id
                 WHERE d.liquidacion_id = :l AND d.diferencia < 0
                 ORDER BY p.nombre
                """
            ),
            {"l": liquidacion_id},
        )
    ).mappings().all()
    if faltantes:
        total, detalle, sin_costo = _a_costo(faltantes)
        resumen.sin_costo += sin_costo
        if total > 0:
            await _insertar_cargo(
                sesion,
                origen="faltante_mercancia",
                importe=total,
                concepto=f"Faltante de mercancía en el Corte {corte['folio']}, a costo",
                detalle=detalle,
                **comunes,
            )
            resumen.mercancia = total

    # ------------------------------------------------------------------
    # Mermas que el catálogo de motivos dice que son del vendedor.
    # ------------------------------------------------------------------
    # La misma atadura que el Corte usa para la columna «merma»: vendedor,
    # camión y día operativo. Una caja rota que el vendedor declaró no es un
    # faltante —el conteo la explica—, pero si el motivo dice `afecta_vendedor`,
    # la pérdida es suya.
    mermas = (
        await sesion.execute(
            text(
                """
                SELECT md.producto_id, p.sku, p.nombre, mm.nombre AS motivo,
                       sum(md.cantidad_base) AS cantidad,
                       pc.costo_promedio AS costo
                  FROM merma_detalle md
                  JOIN mermas me ON me.id = md.merma_id
                  JOIN motivos_merma mm ON mm.codigo = me.motivo_codigo
                  JOIN productos p ON p.id = md.producto_id
                  LEFT JOIN producto_costos pc ON pc.producto_id = md.producto_id
                 WHERE me.tipo = 'merma'
                   AND mm.afecta_vendedor
                   AND me.estado = 'confirmada'
                   AND me.vendedor_id = :v
                   AND me.almacen_id = :camion
                   AND me.fecha_operativa = :f
                 GROUP BY md.producto_id, p.sku, p.nombre, mm.nombre, pc.costo_promedio
                 ORDER BY p.nombre
                """
            ),
            {
                "v": corte["vendedor_id"],
                "camion": corte["camion"],
                "f": corte["fecha_operativa"],
            },
        )
    ).mappings().all()
    if mermas:
        total, detalle, sin_costo = _a_costo(mermas)
        resumen.sin_costo += [n for n in sin_costo if n not in resumen.sin_costo]
        if total > 0:
            await _insertar_cargo(
                sesion,
                origen="merma_atribuible",
                importe=total,
                concepto=f"Mermas a su cargo en el Corte {corte['folio']}, a costo",
                detalle=detalle,
                **comunes,
            )
            resumen.merma = total

    # ------------------------------------------------------------------
    # Efectivo: solo si alguien contó.
    # ------------------------------------------------------------------
    diferencia = Decimal(corte["diferencia_efectivo"] or 0)
    if corte["arqueo_en"] is None:
        resumen.sin_arqueo = Decimal(corte["efectivo_esperado"] or 0) > 0
    elif diferencia < 0:
        await _insertar_cargo(
            sesion,
            origen="faltante_efectivo",
            importe=-diferencia,
            concepto=f"Faltante de efectivo en el Corte {corte['folio']}",
            detalle={
                "esperado": str(corte["efectivo_esperado"]),
                "entregado": str(corte["efectivo_entregado"]),
            },
            **comunes,
        )
        resumen.efectivo = -diferencia

    return resumen


async def saldo_de(sesion: AsyncSession, vendedor_id: uuid.UUID) -> Decimal:
    fila = (
        await sesion.execute(
            text("SELECT saldo FROM v_cuenta_vendedor WHERE vendedor_id = :v"),
            {"v": vendedor_id},
        )
    ).scalar_one_or_none()
    return Decimal(fila or 0)
