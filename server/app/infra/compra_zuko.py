"""Los ZUKO de la factura del 16 de octubre, a la familia de Maruchan y a la bodega.

────────────────────────────────────────────────────────────────────────────
DE DÓNDE SALE
────────────────────────────────────────────────────────────────────────────
Pedido de la dirección (octubre 2026, ADR 0002 §125), con la foto de la factura:
«Se agregarán estos artículos a la familia de Maruchan. Se agregarán solo en el
almacén». Siete sabores de ZUKO 8 gr (12/8), por exhibidor (EXH): jamaica,
naranja («NJA»), piña, uva, horchata, fresa («FSA») y limón; 600 exhibidores a
$47.00 con impuestos, $28,200.00.

La factura pasada a limpio está en `db/semillas/compra_zuko_2026-10-16.csv`: un
renglón por artículo con su SKU (T-103 a T-109, después de las tres Maruchan),
su nombre, la familia, lo que llegó y lo que costó cada exhibidor.

────────────────────────────────────────────────────────────────────────────
QUÉ HACE
────────────────────────────────────────────────────────────────────────────
1. Da de alta los artículos que falten, en la familia de Maruchan, con el
   exhibidor como pieza. SIN precio de venta: la factura solo dice lo que
   costó, y un artículo sin precio en la lista general no le aparece al
   vendedor —que es lo que pidió «solo en el almacén»—. El precio se pone en el
   panel, en Productos, cuando se decida venderlos en la ruta.
2. Los recibe en la bodega principal como una COMPRA (folio EN-), con el costo
   de la factura: entran al libro mayor, a la existencia de la bodega y al costo
   promedio, igual que una compra capturada en Entradas. A los camiones no les
   llega nada: la mercancía sube al camión con una carga.

Correrlo dos veces no duplica nada: el artículo que ya existe no se vuelve a
crear, y la compra tiene una llave fija (`ENTRADA_ID`): si ya está confirmada,
no se suma otra vez.
"""

from __future__ import annotations

import csv
import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import text

from app.api.admin.entradas import abrir_entrada, agregar_renglon_a_entrada, confirmar_entrada
from app.infra.cierre_del_vendedor import bodega_principal

FACTURA = (
    Path(__file__).resolve().parents[2] / "db" / "semillas" / "compra_zuko_2026-10-16.csv"
)

PALABRA = "ZUKO"

# Lo que dice la factura. El proveedor es el del correo al pie («El Competidor»);
# va como texto, sin cuenta por pagar, igual que una compra a un proveedor que no
# está en el catálogo.
PROVEEDOR = "El Competidor"
REFERENCIA = "Factura del 16/10/2026: ZUKO 8 gr"
FECHA = "2026-10-16"
NOTA = "Factura de ZUKO 8 gr (12/8), por exhibidor, a la familia de Maruchan (ADR 0002 §125)."

# La llave de la compra: la misma cada vez que se corre el comando, para que la
# segunda vez se reconozca y no se sume otra vez.
ENTRADA_ID = uuid.uuid5(uuid.NAMESPACE_URL, "distribuciones-se/compra-zuko-2026-10-16")


class FacturaInvalida(Exception):
    """No se cargó nada, y el mensaje dice por qué."""


@dataclass(frozen=True)
class Renglon:
    sku: str
    nombre: str
    familia: str
    cantidad: Decimal
    costo: Decimal

    @property
    def importe(self) -> Decimal:
        return self.cantidad * self.costo


@dataclass
class Plan:
    """Lo que se haría, para decirlo antes de hacerlo."""

    familia: str | None = None
    bodega: str | None = None
    nuevos: list[Renglon] = field(default_factory=list)
    ya_estaban: list[Renglon] = field(default_factory=list)
    # La compra ya confirmada (su folio): no se vuelve a sumar.
    compra_hecha: str | None = None
    problemas: list[str] = field(default_factory=list)

    @property
    def se_puede(self) -> bool:
        return not self.problemas


@dataclass
class Resultado:
    articulos_nuevos: int = 0
    folio: str | None = None
    piezas: Decimal = Decimal(0)
    importe: Decimal = Decimal(0)
    ya_estaba: bool = False


def leer_factura(ruta: Path = FACTURA) -> list[Renglon]:
    """Los renglones del archivo, revisados antes de tocar nada."""
    renglones: list[Renglon] = []
    vistos: set[str] = set()
    with ruta.open(encoding="utf-8", newline="") as f:
        for n, fila in enumerate(csv.DictReader(f), start=2):
            sku = "".join((fila.get("sku") or "").split()).upper()
            nombre = " ".join((fila.get("nombre") or "").split())
            familia = " ".join((fila.get("familia") or "").split())
            if not sku or not nombre or not familia:
                raise FacturaInvalida(f"Renglón {n}: falta el SKU, el nombre o la familia.")
            if sku in vistos:
                raise FacturaInvalida(f"Renglón {n}: el SKU {sku} viene dos veces.")
            vistos.add(sku)
            try:
                cantidad = Decimal((fila.get("cantidad") or "").strip())
                costo = Decimal((fila.get("costo") or "").strip())
            except InvalidOperation as e:
                raise FacturaInvalida(f"Renglón {n} ({sku}): número mal escrito.") from e
            if cantidad <= 0 or cantidad != cantidad.to_integral_value():
                raise FacturaInvalida(f"Renglón {n} ({sku}): la cantidad va en piezas enteras.")
            if costo <= 0:
                raise FacturaInvalida(f"Renglón {n} ({sku}): falta el costo.")
            renglones.append(Renglon(sku, nombre, familia, cantidad, costo))
    if not renglones:
        raise FacturaInvalida("El archivo no trae ningún renglón.")
    return renglones


async def planear(sesion, renglones: list[Renglon]) -> Plan:
    plan = Plan()
    for nombre in dict.fromkeys(r.familia for r in renglones):
        try:
            plan.familia = (await _familia(sesion, nombre))["nombre"]
        except FacturaInvalida as e:
            plan.problemas.append(str(e))

    bodega = await bodega_principal(sesion)
    if bodega is None:
        plan.problemas.append("No hay ninguna bodega activa a donde recibir la compra.")
    else:
        plan.bodega = bodega["nombre"]

    for r in renglones:
        ya = (
            await sesion.execute(
                text("SELECT nombre, activo FROM productos WHERE upper(sku) = :s"), {"s": r.sku}
            )
        ).mappings().first()
        if ya is None:
            # El mismo artículo dado de alta a mano con otro SKU: se diría dos veces.
            otro = (
                await sesion.execute(
                    text("SELECT sku FROM productos WHERE lower(nombre) = lower(:n)"),
                    {"n": r.nombre},
                )
            ).scalar_one_or_none()
            if otro is not None:
                plan.problemas.append(f"«{r.nombre}» ya existe con el SKU {otro}, no {r.sku}.")
            else:
                plan.nuevos.append(r)
        elif ya["nombre"].lower() != r.nombre.lower():
            plan.problemas.append(f"el SKU {r.sku} ya es «{ya['nombre']}», no «{r.nombre}».")
        elif not ya["activo"]:
            plan.problemas.append(f"{r.sku} {r.nombre} está desactivado: actívalo en Productos.")
        else:
            plan.ya_estaban.append(r)

    compra = (
        await sesion.execute(
            text("SELECT estado, folio FROM entradas WHERE id = :e"), {"e": ENTRADA_ID}
        )
    ).mappings().first()
    if compra is not None and compra["estado"] == "confirmada":
        plan.compra_hecha = compra["folio"]
    elif compra is not None and compra["estado"] != "borrador":
        plan.problemas.append(
            f"la compra {compra['folio']} de esta factura está {compra['estado']}. Si fue "
            "un error, captúrala a mano en Entradas."
        )
    return plan


async def aplicar(sesion, renglones: list[Renglon], *, quien: uuid.UUID | None) -> Resultado:
    """Da de alta lo que falte y recibe la compra en la bodega."""
    if quien is None:
        raise FacturaInvalida(
            "No hay ningún usuario de oficina activo (admin o gerente) a cuyo nombre "
            "registrar la compra."
        )
    plan = await planear(sesion, renglones)
    if not plan.se_puede:
        raise FacturaInvalida("; ".join(plan.problemas))

    resultado = Resultado(
        piezas=sum((r.cantidad for r in renglones), Decimal(0)),
        importe=sum((r.importe for r in renglones), Decimal(0)),
    )
    for r in plan.nuevos:
        familia = (await _familia(sesion, r.familia))["id"]
        producto = uuid.uuid4()
        await sesion.execute(
            text("INSERT INTO productos (id, sku, nombre, categoria_id, unidad_base, tasa_iva) "
                 "VALUES (:id, :sku, :nombre, :familia, 'PZA', 0)"),
            {"id": producto, "sku": r.sku, "nombre": r.nombre[:200], "familia": familia},
        )
        await sesion.execute(
            text("INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, es_default) "
                 "VALUES (:p, 'PZA', 1, true)"),
            {"p": producto},
        )
        resultado.articulos_nuevos += 1
    await sesion.commit()

    if plan.compra_hecha:
        resultado.folio = plan.compra_hecha
        resultado.ya_estaba = True
        return resultado

    bodega = await bodega_principal(sesion)
    borrador = (
        await sesion.execute(text("SELECT 1 FROM entradas WHERE id = :e"), {"e": ENTRADA_ID})
    ).first()
    if borrador is None:
        await abrir_entrada(
            sesion,
            almacen_destino_id=str(bodega["id"]),
            motivo="compra",
            proveedor=PROVEEDOR,
            referencia=REFERENCIA,
            fecha=FECHA,
            nota=NOTA,
            quien=quien,
            entrada_id=ENTRADA_ID,
        )
    else:
        # Una vez anterior que se quedó a la mitad: se rehace con la factura.
        await sesion.execute(
            text("DELETE FROM entrada_detalle WHERE entrada_id = :e"), {"e": ENTRADA_ID}
        )
        await sesion.commit()
    for r in renglones:
        await agregar_renglon_a_entrada(
            sesion, ENTRADA_ID, producto=r.sku, unidad_codigo="PZA",
            cantidad=format(r.cantidad, "f"), costo=format(r.costo, "f"),
        )
    await confirmar_entrada(sesion, ENTRADA_ID, quien=quien)
    resultado.folio = (
        await sesion.execute(text("SELECT folio FROM entradas WHERE id = :e"), {"e": ENTRADA_ID})
    ).scalar_one()

    await sesion.execute(
        text("INSERT INTO auditoria (entidad, entidad_id, accion, usuario_id, datos_despues, "
             "                       motivo) "
             "VALUES ('entrada', :id, 'compra_zuko', :quien, CAST(:datos AS jsonb), :motivo)"),
        {
            "id": ENTRADA_ID,
            "quien": quien,
            "datos": json.dumps(
                {
                    "folio": resultado.folio,
                    "articulos_nuevos": [r.sku for r in plan.nuevos],
                    "piezas": str(resultado.piezas),
                    "importe": str(resultado.importe),
                },
                ensure_ascii=False,
            ),
            "motivo": NOTA,
        },
    )
    await sesion.commit()
    return resultado


async def _familia(sesion, nombre: str) -> dict:
    """La familia por su nombre exacto o, si no, la única que lo contiene: en el
    catálogo es «Sopa Maruchan», y pudo haberse renombrado en el panel (§55)."""
    filas = (
        await sesion.execute(
            text("SELECT id, nombre FROM categorias WHERE activo "
                 "   AND lower(nombre) LIKE '%' || lower(:n) || '%' ORDER BY nombre"),
            {"n": nombre},
        )
    ).mappings().all()
    exacta = [f for f in filas if f["nombre"].lower() == nombre.lower()]
    if exacta:
        return dict(exacta[0])
    if len(filas) == 1:
        return dict(filas[0])
    if not filas:
        raise FacturaInvalida(
            f"no hay ninguna familia «{nombre}» activa. Créala en Productos → Familias."
        )
    raise FacturaInvalida(
        f"hay {len(filas)} familias que dicen «{nombre}» ("
        + ", ".join(f["nombre"] for f in filas)
        + "): deja una sola."
    )
