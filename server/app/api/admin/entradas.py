"""Entradas de mercancía a la bodega: la puerta por la que el inventario existe.

────────────────────────────────────────────────────────────────────────────
EL HUECO QUE CIERRA, Y POR QUÉ NINGUNA PRUEBA SE PONÍA ROJA
────────────────────────────────────────────────────────────────────────────
El libro mayor contemplaba 'compra' y 'ajuste' desde la migración 0004, y el
permiso `inventario.ajustar` está definido desde la 0009 — sin que ningún rol lo
tuviera y sin que ningún código lo pidiera. Un permiso que nadie tiene y nada
consulta: la función se planeó hasta el nombre y no se construyó. El motor
estaba y la puerta no.

La consecuencia no era un error visible, y eso es lo peor: la bodega nacía vacía
y se quedaba vacía. Cargar un camión desde una bodega sin existencia FUNCIONA
—`existencias` no lleva CHECK de signo a propósito (§0.1: la mercancía ya
salió)— así que el sistema no se quejaba: dejaba la bodega en negativo y nadie
lo notaba hasta abrir el filtro de negativos de la pantalla de inventario.

Dicho de otra forma: se podía operar el sistema entero inyectando inventario con
SQL, y el día que alguien tecleara un `UPDATE existencias` sin `WHERE`, el libro
mayor no tendría nada que decir al respecto.

────────────────────────────────────────────────────────────────────────────
UN DOCUMENTO, NO UN «SUMAR N PIEZAS»
────────────────────────────────────────────────────────────────────────────
La pantalla rápida habría sido un formulario de un renglón: producto, cantidad,
guardar. Rompe la propiedad que sostiene todo el inventario de este sistema:
cada movimiento del libro mayor apunta a un documento que lo explica.

El día que esta pantalla importa es el día de una auditoría de inventario, y
«¿de dónde salieron estas 240 piezas?» tiene que contestarse con una remisión de
proveedor, no con «alguien lo capturó».

De ahí el mismo ciclo que la carga, que ya está probado:

    BORRADOR  →  (renglones)  →  CONFIRMADA  →  libro mayor + existencias

En borrador no mueve nada. Capturar quince renglones de una remisión toma veinte
minutos, y un sistema que mueve inventario al primer renglón obliga a terminar
sin interrupciones o deja la bodega a medio recibir.

────────────────────────────────────────────────────────────────────────────
EL COSTO, QUE ESTA PANTALLA NO TENÍA
────────────────────────────────────────────────────────────────────────────
La primera versión de esta pantalla no capturaba costo, y la razón era buena:
`productos` no tenía columna de costo, no había módulo de compras, y un campo
que se captura y nadie lee es peor que su ausencia porque **parece** que el
sistema sabe el costo.

La migración 0027 puso dónde: `producto_costos`, con promedio ponderado. Así que
el costo se captura aquí —es el único lugar donde existe la factura— y al
confirmar mueve tres cosas: el promedio del producto, el importe del documento y
la cuenta por pagar.

**Obligatorio en una compra.** Sin él no hay promedio, no hay cuenta por pagar y
el módulo entero no sirve; y si la factura todavía no llega, la remisión con la
que llegó la mercancía trae los precios. Opcional en un inventario inicial o un
ajuste: ahí nadie compró nada, y el renglón sin costo se valúa al promedio
vigente sin moverlo.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.api.admin.comun import (
    CapturaInvalida,
    SesionDep,
    render,
    sin_decimales,
    texto_o_nulo,
)
from app.api.admin.sesion_web import ActorWeb, exigir_csrf
from app.domain.compras import (
    COSTO_MAXIMO,
    SQL_COSTO_ACTUAL,
    SQL_GUARDAR_COSTO,
    SQL_UNIDADES_EN_MANO,
    estado_de_cuenta,
    importe_de_renglon,
    ponderar,
)
from app.domain.entradas import (
    EXIGEN_NOTA,
    MOTIVOS,
    TIPOS_DE_DESTINO,
    etiqueta,
    tipo_de_movimiento,
)
from app.domain.importes import CantidadInvalida, cantidad_base

router = APIRouter(prefix="/panel/entradas", tags=["panel"], include_in_schema=False)

# El permiso que la 0009 definió, nadie tenía y nada consultaba. No se crea uno
# nuevo —una entrada de mercancía ES el ajuste de inventario autorizado— y la
# migración 0025 se lo otorga al supervisor, que es quien está en la bodega.
#
# Durante meses gerencia NO lo tuvo, a propósito: es la operación con la que se
# puede tapar un faltante, y quien mide no es quien ajusta (ADR 0002 §0). La
# migración 0031 se lo concedió por decisión de la dirección —en un negocio de una
# sola plaza, la gerencia ES quien corrige el descuadre—, así que hoy lo tienen
# supervisor, gerente y admin. La separación sigue viva donde más importa: el
# cierre de la liquidación, que gerencia no puede firmar.
#
# Si un día esa concentración estorba, se revoca por persona en
# `usuarios_permisos` sin tocar el rol: `otorgado = false`.
PERMISO = "inventario.ajustar"

# Lo único editable es el borrador. Lo ya confirmado se corrige con otro
# documento —un ajuste en sentido contrario—, nunca editando el historial.
EDITABLE = ("borrador",)

# Tope de error de dedo, no regla de negocio. Un renglón de 100,000 piezas en
# una bodega de abarrotes es un cero de más, y confirmado deja la existencia
# inservible hasta que alguien capture el ajuste contrario.
MAXIMO_BULTOS = Decimal("100000")


# ---------------------------------------------------------------------------
# La entrada, aparte de la pantalla
# ---------------------------------------------------------------------------
# `abrir_entrada`, `datos_de_la_entrada`, `agregar_renglon_a_entrada`,
# `quitar_renglon_de_entrada`, `confirmar_entrada` y `cancelar_entrada` son la
# entrada de mercancía: las usan el panel y la app (`/v1/almacen/entradas`).
# Viven aquí, una vez, para que la que se recibe desde el teléfono mueva el libro
# mayor, el costo promedio y la cuenta por pagar EXACTAMENTE igual que la del
# panel. Las rutas del panel solo traducen a HTML.


class EntradaNoExiste(Exception):
    """La entrada que se pidió no existe."""


class EntradaRechazada(Exception):
    """No se puede así; el mensaje dice por qué y qué hacer."""


def _a_lista(*, error: str = "", guardado: str = "") -> RedirectResponse:
    cola = []
    if error:
        cola.append(f"error={quote(error)}")
    if guardado:
        cola.append(f"guardado={quote(guardado)}")
    destino = "/panel/entradas" + (f"?{'&'.join(cola)}" if cola else "")
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


def _volver(entrada_id, *, error: str = "", guardado: str = "") -> RedirectResponse:
    cola = []
    if error:
        cola.append(f"error={quote(error)}")
    if guardado:
        cola.append(f"guardado={quote(guardado)}")
    destino = f"/panel/entradas/{entrada_id}" + (f"?{'&'.join(cola)}" if cola else "")
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


def _leer_bultos(texto: str | None) -> Decimal:
    """Cuántos bultos de esa presentación llegaron.

    Entero a propósito, igual que en la carga: ningún producto se vende a granel
    (ADR 0002 §2), así que un `2.5` es un dedazo — y si se aceptara,
    `cantidad_base` lo convertiría en 60 piezas con cara de dato bueno.
    """
    crudo = (texto or "").strip().replace(",", "")
    if not crudo:
        raise CapturaInvalida("Falta cuántos bultos llegaron.")
    try:
        valor = Decimal(crudo)
    except ArithmeticError as e:
        raise CapturaInvalida(f"«{texto}» no es una cantidad.") from e
    if not valor.is_finite():
        raise CapturaInvalida(f"«{texto}» no es una cantidad.")
    if valor <= 0:
        raise CapturaInvalida("La cantidad tiene que ser mayor que cero.")
    if valor != valor.to_integral_value():
        raise CapturaInvalida(
            f"Llegan bultos completos, no {valor}. Si la remisión trae una "
            "fracción, captúrala en la unidad más chica."
        )
    if valor > MAXIMO_BULTOS:
        raise CapturaInvalida(
            f"{sin_decimales(valor)} bultos en un renglón parece un cero de más."
        )
    return valor


def _leer_costo(texto: str | None, *, obligatorio: bool) -> Decimal | None:
    """El costo unitario de un renglón, a cuatro decimales.

    Nunca `float`: `float("296.10")` ya no vale 296.10 y de ahí al centavo
    perdido hay un paso (ver `comun.py`). Se acepta lo que la gente escribe
    —`$1,296.50` es lo que sale de copiar una celda de Excel— y el vacío
    significa «al promedio vigente», no cero.
    """
    crudo = (texto or "").strip().replace("$", "").replace(",", "")
    if not crudo:
        if obligatorio:
            raise CapturaInvalida(
                "Falta el costo unitario. En una compra es obligatorio: sin él no "
                "hay promedio ni cuenta por pagar. Si la factura no llegó, usa el "
                "precio de la remisión."
            )
        return None
    try:
        valor = Decimal(crudo)
    except ArithmeticError as e:
        raise CapturaInvalida(f"«{texto}» no es un costo.") from e
    if not valor.is_finite():
        raise CapturaInvalida(f"«{texto}» no es un costo.")
    if valor < 0:
        raise CapturaInvalida("El costo no puede ser negativo.")
    if valor == 0:
        # Un cero no es «gratis», es un costo que falta capturar — y guardado
        # arrastra el promedio del producto a la baja con un costo que nadie
        # pagó. Lo gratis de verdad (una muestra del proveedor) se recibe como
        # inventario inicial o ajuste, sin costo.
        raise CapturaInvalida(
            "Un costo de cero arrastraría el promedio del producto. Si de verdad "
            "llegó sin costo, recíbelo como ajuste en vez de como compra."
        )
    if valor > COSTO_MAXIMO:
        raise CapturaInvalida(f"Un costo de {valor} parece un error de dedo.")
    return valor.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


async def _proveedores(sesion):
    return (
        await sesion.execute(
            text(
                "SELECT id, codigo, nombre, dias_credito FROM proveedores "
                " WHERE activo ORDER BY nombre"
            )
        )
    ).mappings().all()


async def _bodegas(sesion):
    """Solo bodegas. Un camión no recibe mercancía desde el panel: ver §0.2."""
    return (
        await sesion.execute(
            text(
                "SELECT id, codigo, nombre FROM almacenes "
                " WHERE activo AND tipo = ANY(:tipos) ORDER BY codigo"
            ),
            {"tipos": list(TIPOS_DE_DESTINO)},
        )
    ).mappings().all()


# ---------------------------------------------------------------------------
# Lista
# ---------------------------------------------------------------------------
@router.get("", response_class=HTMLResponse)
async def listar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    error: str = "",
    guardado: str = "",
) -> HTMLResponse:
    actor.exigir("inventario.ver")

    entradas = await entradas_recientes(sesion)

    return render(
        peticion,
        "entradas.html",
        {
            "entradas": entradas,
            "motivos": MOTIVOS,
            "bodegas": await _bodegas(sesion),
            "proveedores": await _proveedores(sesion),
            "hoy": date.today().isoformat(),
            "puede_editar": actor.puede(PERMISO),
            "borradores": sum(1 for e in entradas if e["estado"] == "borrador"),
            "devoluciones": await _devoluciones_por_recibir(sesion),
            "error": error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Entradas",
    )


async def entradas_recientes(sesion) -> list:
    """Las últimas cien entradas, las más nuevas primero."""
    return list(
        (
        await sesion.execute(
            text(
                """
                SELECT e.id, e.folio, e.motivo, e.estado, e.proveedor,
                       e.referencia, e.fecha_operativa, e.confirmada_en,
                       e.importe_total, e.proveedor_id,
                       a.codigo AS bodega, a.nombre AS bodega_nombre,
                       u.nombre AS creado_por,
                       c.saldo AS saldo_por_pagar, c.estado AS estado_pago,
                       c.fecha_vencimiento,
                       COALESCE(d.renglones, 0) AS renglones,
                       COALESCE(d.piezas, 0)    AS piezas
                  FROM entradas e
                  JOIN almacenes a ON a.id = e.almacen_destino_id
                  LEFT JOIN usuarios u ON u.id = e.creado_por
                  LEFT JOIN cuentas_por_pagar c ON c.entrada_id = e.id
                  LEFT JOIN LATERAL (
                        SELECT count(*) AS renglones, sum(cantidad) AS piezas
                          FROM entrada_detalle WHERE entrada_id = e.id
                  ) d ON true
                 ORDER BY e.fecha_operativa DESC, e.folio DESC
                 LIMIT 100
                """
            )
        )
        ).mappings().all()
    )


@router.post("/nueva")
async def crear(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    almacen_destino_id: Annotated[str, Form()] = "",
    motivo: Annotated[str, Form()] = "",
    proveedor_id: Annotated[str, Form()] = "",
    proveedor: Annotated[str, Form()] = "",
    referencia: Annotated[str, Form()] = "",
    fecha: Annotated[str, Form()] = "",
    nota: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Abre el borrador. No mueve nada todavía."""
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    try:
        nueva = await abrir_entrada(
            sesion,
            almacen_destino_id=almacen_destino_id,
            motivo=motivo,
            proveedor_id=proveedor_id,
            proveedor=proveedor,
            referencia=referencia,
            fecha=fecha,
            nota=nota,
            quien=actor.usuario_id,
        )
    except EntradaRechazada as e:
        return _a_lista(error=str(e))
    return _volver(nueva, guardado="Borrador abierto. Captura los renglones.")


async def abrir_entrada(
    sesion,
    *,
    almacen_destino_id: str,
    motivo: str,
    proveedor_id: str = "",
    proveedor: str = "",
    referencia: str = "",
    fecha: str = "",
    nota: str = "",
    quien: uuid.UUID,
    entrada_id: uuid.UUID | None = None,
    costo_opcional: bool = False,
) -> uuid.UUID:
    """Abre el borrador y devuelve su id. Ver `crear` para cada decisión.

    `entrada_id` lo pone la compra que el gerente capturó sin señal: es su llave
    de idempotencia. `costo_opcional` es la regla de esa compra (ADR 0002 §83):
    el renglón con costo mueve el promedio y entra a la cuenta por pagar; el que
    no lo trae solo suma inventario.
    """
    if motivo not in MOTIVOS:
        raise EntradaRechazada("Falta elegir por qué entra la mercancía.")

    try:
        destino = uuid.UUID(str(almacen_destino_id))
    except ValueError as e:
        raise EntradaRechazada("Falta elegir a qué bodega entra.") from e

    bodega = (
        await sesion.execute(
            text("SELECT id, codigo, nombre, tipo FROM almacenes WHERE id = :a AND activo"),
            {"a": destino},
        )
    ).mappings().first()
    if bodega is None:
        raise EntradaRechazada("Esa bodega no existe o está inactiva.")
    if bodega["tipo"] not in TIPOS_DE_DESTINO:
        # §0.2. El mensaje dice el camino correcto, no solo que no se puede.
        raise EntradaRechazada(
            f"{bodega['nombre']} es un {bodega['tipo']}, no una bodega. La "
            "mercancía nueva entra a la bodega y de ahí sube al camión con una "
            "carga, que es lo que el teléfono del vendedor sabe recibir."
        )

    hoy = date.today()
    try:
        operativa = date.fromisoformat(fecha) if fecha else hoy
    except ValueError:
        operativa = hoy
    if operativa > hoy:
        # Una entrada con fecha futura es mercancía que todavía no llegó, y
        # entraría al libro mayor con una fecha en la que no existía.
        raise EntradaRechazada(
            "La fecha no puede ser futura: una entrada con fecha de mañana "
            "es mercancía que todavía no llegó."
        )

    texto_nota = texto_o_nulo(nota, maximo=500)
    if motivo in EXIGEN_NOTA and not texto_nota:
        raise EntradaRechazada(
            "El inventario inicial necesita una nota: es el documento que "
            "explica de dónde salió todo el inventario del arranque, y se lee el "
            "día que algo no cuadra."
        )

    # El proveedor del catálogo, si se eligió uno. Su nombre se COPIA al
    # documento: si mañana el proveedor se renombra o se da de baja, la entrada
    # de hace dos años sigue diciendo a quién se le compró — el mismo
    # razonamiento por el que la venta congela `lista_precios_version`.
    #
    # Y se permite comprar SIN proveedor del catálogo, porque pasa: una compra
    # de contado en la central de abastos, sin factura. Lo que no hay entonces
    # es cuenta por pagar, y la pantalla lo dice en vez de dejarlo suponer.
    del_catalogo = None
    if (proveedor_id or "").strip():
        try:
            del_catalogo = (
                await sesion.execute(
                    text(
                        "SELECT id, nombre, dias_credito FROM proveedores "
                        " WHERE id = :p AND activo"
                    ),
                    {"p": uuid.UUID(proveedor_id)},
                )
            ).mappings().first()
        except ValueError:
            del_catalogo = None
        if del_catalogo is None:
            raise EntradaRechazada("Ese proveedor no existe o está inactivo.")

    nombre_proveedor = (
        del_catalogo["nombre"] if del_catalogo
        else texto_o_nulo(proveedor, maximo=160)
    )

    consecutivo = (
        await sesion.execute(text("SELECT nextval('seq_folio_entrada')"))
    ).scalar_one()
    nueva = entrada_id or uuid.uuid4()
    await sesion.execute(
        text(
            """
            INSERT INTO entradas
                (id, folio, almacen_destino_id, motivo, proveedor_id, proveedor,
                 referencia, fecha_operativa, nota, estado, creado_por,
                 costo_opcional)
            VALUES (:id, :folio, :a, :motivo, :prov_id, :prov, :ref, :fecha,
                    :nota, 'borrador', :quien, :costo_opcional)
            """
        ),
        {
            "costo_opcional": costo_opcional,
            "id": nueva,
            "folio": f"EN-{consecutivo:06d}",
            "a": destino,
            "motivo": motivo,
            "prov_id": del_catalogo["id"] if del_catalogo else None,
            "prov": nombre_proveedor,
            "ref": texto_o_nulo(referencia, maximo=80),
            "fecha": operativa,
            "nota": texto_nota,
            "quien": quien,
        },
    )
    await sesion.commit()
    return nueva


# ---------------------------------------------------------------------------
# Detalle y captura
# ---------------------------------------------------------------------------
@router.get("/{entrada_id}", response_class=HTMLResponse)
async def detalle(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    entrada_id: uuid.UUID,
    error: str = "",
    guardado: str = "",
) -> HTMLResponse:
    actor.exigir("inventario.ver")

    datos = await datos_de_la_entrada(sesion, entrada_id)
    if datos is None:
        return _a_lista(error="Esa entrada no existe.")
    entrada = datos["entrada"]

    presentaciones = (
        await sesion.execute(
            text(
                "SELECT DISTINCT unidad_codigo FROM producto_unidades "
                " WHERE activo ORDER BY unidad_codigo"
            )
        )
    ).scalars().all()

    return render(
        peticion,
        "entrada_detalle.html",
        {
            "entrada": entrada,
            "motivo_etiqueta": etiqueta(entrada["motivo"]),
            "importes": datos["importes"],
            "importe_capturado": datos["importe_capturado"],
            "sin_costo": datos["sin_costo"],
            "cuenta": datos["cuenta"],
            "exige_costo": entrada["motivo"] == "compra",
            "renglones": datos["renglones"],
            "proyectado": datos["proyectado"],
            "presentaciones": presentaciones,
            "editable": entrada["estado"] in EDITABLE,
            "puede_editar": actor.puede(PERMISO),
            "total_piezas": datos["total_piezas"],
            "error": error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Entradas",
    )


async def datos_de_la_entrada(sesion, entrada_id: uuid.UUID) -> dict | None:
    """Todo lo que se ve de una entrada."""
    entrada = (
        await sesion.execute(
            text(
                """
                SELECT e.*, a.codigo AS bodega, a.nombre AS bodega_nombre,
                       u.nombre AS creado_por_nombre,
                       c.nombre AS confirmada_por_nombre
                  FROM entradas e
                  JOIN almacenes a ON a.id = e.almacen_destino_id
                  LEFT JOIN usuarios u ON u.id = e.creado_por
                  LEFT JOIN usuarios c ON c.id = e.confirmada_por
                 WHERE e.id = :id
                """
            ),
            {"id": entrada_id},
        )
    ).mappings().first()
    if entrada is None:
        return None

    # Cada renglón con la existencia ACTUAL del producto en esa bodega al lado.
    # No es adorno: quien recibe tiene que poder ver si lo que está capturando
    # deja una cifra con sentido, y es donde se nota un cero de más antes de
    # confirmar — después ya es un asiento que no se borra.
    renglones = (
        await sesion.execute(
            text(
                """
                SELECT d.id, d.producto_id, d.cantidad, d.costo_unitario,
                       d.importe, d.unidad_codigo, d.unidades_capturadas, d.lote,
                       d.caducidad, p.nombre, p.sku, p.unidad_base,
                       COALESCE(x.cantidad, 0) AS existencia,
                       c.costo_promedio
                  FROM entrada_detalle d
                  JOIN productos p ON p.id = d.producto_id
                  LEFT JOIN existencias x ON x.producto_id = d.producto_id
                                         AND x.almacen_id = :a
                  LEFT JOIN producto_costos c ON c.producto_id = d.producto_id
                 WHERE d.entrada_id = :e
                 ORDER BY p.nombre, d.lote NULLS FIRST
                """
            ),
            {"e": entrada_id, "a": entrada["almacen_destino_id"]},
        )
    ).mappings().all()

    # La proyección se calcula POR PRODUCTO, sumando todos sus renglones del
    # documento, y no renglón por renglón.
    #
    # Con dos lotes del mismo producto —el caso normal de una remisión— la cuenta
    # por renglón miente en los dos: cada uno partiría de la misma existencia y
    # sumaría solo lo suyo, así que ninguna de las dos flechas daría el número en
    # el que de verdad queda la bodega. Y la columna existe justamente para ver
    # un cero de más antes de confirmar; una cifra que miente ahí es peor que no
    # tener la columna.
    proyectado: dict = {}
    for r in renglones:
        proyectado[r["producto_id"]] = (
            Decimal(r["existencia"])
            + sum(
                Decimal(x["cantidad"])
                for x in renglones
                if x["producto_id"] == r["producto_id"]
            )
        )

    # El importe de cada renglón y el del documento, calculados aquí: la
    # plantilla no multiplica dinero. Un `Decimal * Decimal` en Jinja es
    # correcto, pero un filtro que devuelva `float` en medio de la cadena
    # convertiría centavos en flotantes sin que nada avise.
    # Los importes vienen GUARDADOS, no se recalculan: el renglón congeló lo que
    # dice la factura, y recalcularlo sobre piezas volvería a perder el centavo.
    importes = {
        r["id"]: Decimal(r["importe"])
        for r in renglones
        if r["importe"] is not None
    }
    cuenta = (
        await sesion.execute(
            text(
                "SELECT c.importe_original, c.importe_pagado, c.saldo, c.estado, "
                "       c.fecha_vencimiento, p.nombre AS proveedor "
                "  FROM cuentas_por_pagar c "
                "  JOIN proveedores p ON p.id = c.proveedor_id "
                " WHERE c.entrada_id = :e"
            ),
            {"e": entrada_id},
        )
    ).mappings().first()

    return {
        "entrada": dict(entrada),
        "renglones": renglones,
        "proyectado": proyectado,
        "importes": importes,
        "importe_capturado": sum(importes.values(), Decimal("0.00")),
        "sin_costo": [r for r in renglones if r["costo_unitario"] is None],
        "cuenta": cuenta,
        "total_piezas": sum((Decimal(r["cantidad"]) for r in renglones), Decimal(0)),
    }


@router.post("/{entrada_id}/renglon")
async def agregar_renglon(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    entrada_id: uuid.UUID,
    producto: Annotated[str, Form()] = "",
    unidad_codigo: Annotated[str, Form()] = "",
    cantidad: Annotated[str, Form()] = "",
    costo: Annotated[str, Form()] = "",
    lote: Annotated[str, Form()] = "",
    caducidad: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Agrega un renglón. La conversión a unidad base ocurre aquí, una vez.

    `producto` acepta el SKU o el código de barras: quien recibe en la bodega
    tiene el lector en la mano o el SKU en la caja, no un UUID.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    try:
        aviso = await agregar_renglon_a_entrada(
            sesion,
            entrada_id,
            producto=producto,
            unidad_codigo=unidad_codigo,
            cantidad=cantidad,
            costo=costo,
            lote=lote,
            caducidad=caducidad,
        )
    except EntradaNoExiste as e:
        return _a_lista(error=str(e))
    except (EntradaRechazada, CapturaInvalida, CantidadInvalida) as e:
        return _volver(entrada_id, error=str(e))
    return _volver(entrada_id, guardado=aviso)


async def agregar_renglon_a_entrada(
    sesion,
    entrada_id: uuid.UUID,
    *,
    producto: str,
    unidad_codigo: str,
    cantidad: str,
    costo: str = "",
    lote: str = "",
    caducidad: str = "",
) -> str:
    """Agrega (o suma) un renglón y devuelve el aviso. Ver `agregar_renglon`."""
    # `FOR UPDATE` y no un SELECT simple, por una carrera estrecha y silenciosa:
    # si alguien confirma el documento mientras esto se ejecuta, un SELECT
    # normal leería 'borrador' de la versión anterior de la fila y el renglón se
    # insertaría DESPUÉS de que `confirmar` ya leyó el detalle. Resultado: una
    # línea que está en el documento y no en el libro mayor — el descuadre que
    # esta pantalla existe para no producir.
    #
    # Con el candado, esto espera a que la confirmación termine y entonces lee
    # 'confirmada', que es la respuesta correcta.
    entrada = (
        await sesion.execute(
            text(
                "SELECT estado, motivo, almacen_destino_id, costo_opcional FROM entradas "
                " WHERE id = :id FOR UPDATE"
            ),
            {"id": entrada_id},
        )
    ).mappings().first()
    if entrada is None:
        raise EntradaNoExiste("Esa entrada no existe.")
    if entrada["estado"] not in EDITABLE:
        raise EntradaRechazada(
            f"Esta entrada ya está {entrada['estado']}. Lo confirmado se "
            "corrige con otro documento, no editando el historial."
        )

    clave = producto.strip()
    fila = (
        await sesion.execute(
            text(
                "SELECT id, nombre, unidad_base, maneja_lote FROM productos "
                " WHERE activo AND (upper(sku) = upper(:c) OR codigo_barras = :c)"
            ),
            {"c": clave},
        )
    ).mappings().first()
    if fila is None:
        raise EntradaRechazada(f"No hay producto activo con clave «{clave}».")

    presentacion = (
        await sesion.execute(
            text(
                "SELECT factor FROM producto_unidades "
                " WHERE producto_id = :p AND unidad_codigo = :u AND activo"
            ),
            {"p": fila["id"], "u": unidad_codigo},
        )
    ).mappings().first()
    if presentacion is None:
        raise EntradaRechazada(f"{fila['nombre']} no tiene la presentación {unidad_codigo}.")

    bultos = _leer_bultos(cantidad)
    # La multiplicación, una sola vez y con la misma función que usa el
    # teléfono al armar una partida: la conversión caja→pieza está escrita
    # en un solo lugar del sistema.
    en_base = cantidad_base(bultos, Decimal(presentacion["factor"]))
    # El costo se captura POR BULTO —como viene en la factura— y se guarda
    # POR UNIDAD BASE, igual que la cantidad. Dividir aquí y no al confirmar
    # es lo que mantiene la regla del sistema: el libro mayor y todo lo que
    # cuelga de él hablan en unidad base.
    por_bulto = _leer_costo(
        costo, obligatorio=entrada["motivo"] == "compra" and not entrada["costo_opcional"]
    )
    costo_base = (
        (por_bulto / Decimal(presentacion["factor"])).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        if por_bulto is not None
        else None
    )

    vence = None
    if (caducidad or "").strip():
        try:
            vence = date.fromisoformat(caducidad.strip())
        except ValueError as e:
            raise EntradaRechazada(f"«{caducidad}» no es una fecha de caducidad.") from e

    numero_lote = texto_o_nulo(lote, maximo=40)
    if fila["maneja_lote"] and not numero_lote:
        # El producto se marcó como de lote en el catálogo: recibirlo sin lote
        # deja una merma por caducidad imposible de rastrear hasta su tarima.
        raise EntradaRechazada(
            f"{fila['nombre']} maneja lote: captura el de la tarima que estás recibiendo."
        )

    await sesion.execute(
        text(
            """
            INSERT INTO entrada_detalle
                (id, entrada_id, producto_id, cantidad, costo_unitario, importe,
                 unidad_codigo, unidades_capturadas, lote, caducidad)
            VALUES (:id, :e, :p, :cant, :costo, :importe, :u, :bultos, :lote, :cad)
            ON CONFLICT (entrada_id, producto_id, COALESCE(lote, ''))
              DO UPDATE SET
                 cantidad = entrada_detalle.cantidad + excluded.cantidad,
                 -- Al sumar dos capturas del mismo renglón, el costo se vuelve
                 -- a PONDERAR entre las dos. Quedarse con el último sería
                 -- aplicarle a las 240 piezas de la primera tarima el precio de
                 -- la segunda, que es justo el error que el promedio ponderado
                 -- existe para no cometer.
                 costo_unitario = CASE
                     WHEN excluded.costo_unitario IS NULL
                          THEN entrada_detalle.costo_unitario
                     WHEN entrada_detalle.costo_unitario IS NULL
                          THEN excluded.costo_unitario
                     ELSE (entrada_detalle.cantidad * entrada_detalle.costo_unitario
                           + excluded.cantidad * excluded.costo_unitario)
                          / (entrada_detalle.cantidad + excluded.cantidad)
                     END,
                 -- El importe SE SUMA, no se pondera: son dos renglones de la
                 -- misma factura y lo que se le debe al proveedor es la suma de
                 -- los dos. Ponderarlo perdería dinero.
                 importe = COALESCE(entrada_detalle.importe, 0)
                           + COALESCE(excluded.importe, 0),
                 -- Las unidades capturadas se suman SOLO si la presentación es
                 -- la misma; si no, el renglón ya no se puede leer como una
                 -- sola línea de la remisión y se deja en nulo en vez de
                 -- inventar una suma de cajas con piezas.
                 unidades_capturadas = CASE
                     WHEN entrada_detalle.unidad_codigo = excluded.unidad_codigo
                     THEN entrada_detalle.unidades_capturadas
                          + excluded.unidades_capturadas
                     ELSE NULL END,
                 unidad_codigo = CASE
                     WHEN entrada_detalle.unidad_codigo = excluded.unidad_codigo
                     THEN entrada_detalle.unidad_codigo
                     ELSE NULL END,
                 caducidad = COALESCE(entrada_detalle.caducidad, excluded.caducidad)
            """
        ),
        {
            "id": uuid.uuid4(),
            "e": entrada_id,
            "p": fila["id"],
            "cant": en_base,
            "costo": costo_base,
            # Sobre lo CAPTURADO: 10 cajas × $296.00 = $2,960.00 exactos. La
            # cuenta sobre piezas daría $2,959.99 y el pago de la factura se
            # rechazaría por un centavo.
            "importe": (
                importe_de_renglon(bultos, por_bulto)
                if por_bulto is not None
                else None
            ),
            "u": unidad_codigo,
            "bultos": bultos,
            "lote": numero_lote,
            "cad": vence,
        },
    )
    await sesion.commit()
    aviso = (
        f"{fila['nombre']}: {sin_decimales(bultos)} {unidad_codigo} "
        f"= {sin_decimales(en_base)} {fila['unidad_base']}."
    )
    if por_bulto is not None:
        aviso += (
            f" A ${por_bulto:,.2f} el {unidad_codigo} = "
            f"${costo_base:,.4f} la unidad; importe "
            f"${importe_de_renglon(bultos, por_bulto):,.2f}."
        )
    return aviso


@router.post("/{entrada_id}/renglon/{renglon_id}/quitar")
async def quitar_renglon(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    entrada_id: uuid.UUID,
    renglon_id: uuid.UUID,
    csrf: Annotated[str, Form()] = "",
):
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    try:
        await quitar_renglon_de_entrada(sesion, entrada_id, renglon_id)
    except EntradaNoExiste as e:
        return _a_lista(error=str(e))
    except EntradaRechazada as e:
        return _volver(entrada_id, error=str(e))
    return _volver(entrada_id, guardado="Renglón quitado.")


async def quitar_renglon_de_entrada(
    sesion, entrada_id: uuid.UUID, renglon_id: uuid.UUID
) -> None:
    # `FOR UPDATE` por lo mismo que al agregar: quitar un renglón que la
    # confirmación acaba de escribir en el libro mayor dejaría el asiento sin su
    # línea de documento.
    estado = (
        await sesion.execute(
            text("SELECT estado FROM entradas WHERE id = :id FOR UPDATE"),
            {"id": entrada_id},
        )
    ).scalar()
    if estado is None:
        raise EntradaNoExiste("Esa entrada no existe.")
    if estado not in EDITABLE:
        raise EntradaRechazada(
            "Esta entrada ya se confirmó: el renglón ya es un asiento del "
            "libro mayor y no se quita, se compensa."
        )

    await sesion.execute(
        text("DELETE FROM entrada_detalle WHERE id = :r AND entrada_id = :e"),
        {"r": renglon_id, "e": entrada_id},
    )
    await sesion.commit()


# ---------------------------------------------------------------------------
# Confirmar
# ---------------------------------------------------------------------------
@router.post("/{entrada_id}/confirmar")
async def confirmar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    entrada_id: uuid.UUID,
    csrf: Annotated[str, Form()] = "",
):
    """Escribe el libro mayor y las existencias. Todo o nada.

    Es **idempotente por estado**, con `FOR UPDATE`: confirmar dos veces no mete
    la mercancía dos veces. Sin eso, un doble clic en una pantalla lenta
    duplicaría una remisión completa, y el sobrante solo aparecería en el
    siguiente conteo físico — semanas después, sin forma de saber qué pasó.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    try:
        aviso = await confirmar_entrada(sesion, entrada_id, quien=actor.usuario_id)
    except EntradaNoExiste as e:
        return _a_lista(error=str(e))
    except EntradaRechazada as e:
        return _volver(entrada_id, error=str(e))
    return _volver(entrada_id, guardado=aviso)


async def confirmar_entrada(sesion, entrada_id: uuid.UUID, *, quien: uuid.UUID) -> str:
    """Escribe el libro mayor, existencias, costo y cuenta por pagar. Ver `confirmar`."""
    entrada = (
        await sesion.execute(
            text(
                "SELECT id, folio, estado, motivo, almacen_destino_id, "
                "       fecha_operativa, proveedor_id, referencia, costo_opcional "
                "  FROM entradas WHERE id = :id FOR UPDATE"
            ),
            {"id": entrada_id},
        )
    ).mappings().first()
    if entrada is None:
        raise EntradaNoExiste("Esa entrada no existe.")
    if entrada["estado"] != "borrador":
        raise EntradaRechazada(f"Esta entrada ya está {entrada['estado']}.")

    renglones = (
        await sesion.execute(
            text(
                "SELECT d.producto_id, d.cantidad, d.costo_unitario, d.importe, d.lote, "
                "       d.caducidad, p.nombre "
                "  FROM entrada_detalle d "
                "  JOIN productos p ON p.id = d.producto_id "
                " WHERE d.entrada_id = :e ORDER BY p.nombre"
            ),
            {"e": entrada_id},
        )
    ).mappings().all()
    if not renglones:
        raise EntradaRechazada("No puedes confirmar una entrada sin un solo renglón.")

    # En una compra, todos los renglones llevan costo. Se revisa aquí otra vez
    # y no solo al capturar porque un renglón puede haberse agregado antes de
    # que alguien cambiara el motivo con SQL, y confirmar una compra sin costo
    # dejaría una cuenta por pagar de cero — una deuda invisible.
    if entrada["motivo"] == "compra" and not entrada["costo_opcional"]:
        sin_costo = [r["nombre"] for r in renglones if r["costo_unitario"] is None]
        if sin_costo:
            raise EntradaRechazada(
                "Estos renglones no tienen costo y es una compra: "
                + ", ".join(sin_costo[:5])
                + (f" y {len(sin_costo) - 5} más" if len(sin_costo) > 5 else "")
                + ". Sin costo no hay cuenta por pagar ni promedio."
            )

    tipo = tipo_de_movimiento(entrada["motivo"])
    ahora = datetime.now(UTC)

    for r in renglones:
        # El asiento. `almacen_origen_id` queda en NULL: la mercancía entra al
        # sistema desde fuera, y el CHECK `movimiento_tiene_almacen` solo exige
        # que haya uno de los dos.
        await sesion.execute(
            text(
                """
                INSERT INTO movimientos_inventario
                  (tipo, almacen_origen_id, almacen_destino_id, producto_id,
                   cantidad, lote, caducidad, documento_tipo, documento_id,
                   usuario_id, fecha_servidor)
                VALUES (:tipo, NULL, :destino, :p, :cant, :lote, :cad,
                        'entrada', :doc, :quien, :ahora)
                """
            ),
            {
                "tipo": tipo,
                "destino": entrada["almacen_destino_id"],
                "p": r["producto_id"],
                "cant": r["cantidad"],
                "lote": r["lote"],
                "cad": r["caducidad"],
                "doc": entrada_id,
                "quien": quien,
                "ahora": ahora,
            },
        )
        # La caché, en la MISMA transacción que el asiento. Si divergieran, el
        # job de reconciliación nocturno estaría arreglando un error evitable.
        await sesion.execute(
            text(
                """
                INSERT INTO existencias (almacen_id, producto_id, cantidad, actualizado_en)
                VALUES (:a, :p, :cant, :ahora)
                ON CONFLICT (almacen_id, producto_id) DO UPDATE
                   SET cantidad = existencias.cantidad + :cant,
                       actualizado_en = :ahora
                """
            ),
            {
                "a": entrada["almacen_destino_id"],
                "p": r["producto_id"],
                "cant": r["cantidad"],
                "ahora": ahora,
            },
        )

    # ------------------------------------------------------------------
    # El costo: el promedio ponderado de cada producto que entró
    # ------------------------------------------------------------------
    # Va DESPUÉS de mover las existencias y en la misma transacción, y el orden
    # importa: se pondera contra las unidades que había ANTES de esta entrada,
    # así que la consulta resta lo que acabamos de sumar. Ponderar contra la
    # existencia ya actualizada contaría las unidades que entran dos veces y el
    # promedio se quedaría a medio camino del costo nuevo.
    importe_total = Decimal("0.00")
    for r in renglones:
        cantidad = Decimal(r["cantidad"])
        costo = (
            Decimal(r["costo_unitario"]) if r["costo_unitario"] is not None else None
        )

        actual = (
            await sesion.execute(
                text(SQL_COSTO_ACTUAL), {"producto": r["producto_id"]}
            )
        ).mappings().first()
        costo_actual = (
            Decimal(actual["costo_promedio"]) if actual is not None else None
        )

        en_mano_ahora = Decimal(
            (
                await sesion.execute(
                    text(SQL_UNIDADES_EN_MANO), {"producto": r["producto_id"]}
                )
            ).scalar()
            or 0
        )
        en_mano_antes = en_mano_ahora - cantidad

        # El importe del documento: el de la FACTURA si se capturó, y solo si
        # no, la valuación al promedio.
        #
        # El orden importa y es la corrección de un centavo que rompía el pago:
        # `d.importe` se calculó sobre los bultos capturados (10 cajas x $296 =
        # $2,960.00), mientras que cantidad x costo_unitario da $2,959.99 porque
        # 296/24 no es exacto. La cuenta por pagar tiene que decir lo que dice
        # la factura, o el pago se rechaza por un centavo.
        if r["importe"] is not None:
            importe_total += Decimal(r["importe"])
        elif entrada["motivo"] == "compra":
            # La compra del teléfono sin costo (§83): el renglón suma inventario y
            # NO entra a la cuenta por pagar. Valuarlo al promedio aquí metería a
            # la deuda con el proveedor un importe que nadie le facturó.
            pass
        elif costo_actual is not None:
            # Sin costo capturado, el renglón se valúa al promedio vigente y el
            # promedio NO se mueve: es el tratamiento estándar de lo que aparece
            # en un conteo, y guardarlo en cero arrastraría el promedio a la
            # baja con un costo que nadie pagó.
            importe_total += importe_de_renglon(cantidad, costo_actual)

        if costo is None and costo_actual is None:
            # Ni costo capturado ni promedio previo: no hay con qué valuar, y
            # NO se inventa un cero. El producto queda sin costo y la pantalla
            # de valor de inventario lo cuenta aparte en vez de valuarlo en
            # cero, que haría que el total se viera bajo sin decir por qué.
            continue

        resultado = ponderar(
            unidades_en_mano=en_mano_antes,
            costo_actual=costo_actual,
            unidades_que_entran=cantidad if costo is not None else Decimal(0),
            costo_que_entra=costo if costo is not None else Decimal(0),
        )
        await sesion.execute(
            text(SQL_GUARDAR_COSTO),
            {
                "producto": r["producto_id"],
                "promedio": resultado.costo_promedio,
                # El último costo y su fecha solo se mueven en una COMPRA: un
                # inventario inicial valuado al promedio no es una cotización
                # del proveedor y no debe parecerlo.
                "ultimo": costo if entrada["motivo"] == "compra" else None,
                "fecha": (
                    entrada["fecha_operativa"]
                    if entrada["motivo"] == "compra" and costo is not None
                    else None
                ),
                "unidades": resultado.unidades_al_ponderar,
            },
        )

    # ------------------------------------------------------------------
    # La cuenta por pagar, solo en una compra con proveedor del catálogo
    # ------------------------------------------------------------------
    # Sin proveedor del catálogo no hay deuda que rastrear: es la compra de
    # contado en la central de abastos. El importe es el del documento, y el
    # vencimiento sale de los días de crédito del proveedor — cero días es
    # contado, y vence el mismo día, que es verdad.
    if entrada["motivo"] == "compra" and entrada["proveedor_id"] and importe_total > 0:
        dias = (
            await sesion.execute(
                text("SELECT dias_credito FROM proveedores WHERE id = :p"),
                {"p": entrada["proveedor_id"]},
            )
        ).scalar() or 0
        await sesion.execute(
            text(
                """
                INSERT INTO cuentas_por_pagar
                    (entrada_id, proveedor_id, importe_original, fecha_emision,
                     fecha_vencimiento, referencia, estado)
                VALUES (:e, :p, :importe, :emision,
                        :emision + CAST(:dias AS integer), :ref, :estado)
                ON CONFLICT (entrada_id) DO NOTHING
                """
            ),
            {
                "e": entrada_id,
                "p": entrada["proveedor_id"],
                "importe": importe_total,
                "emision": entrada["fecha_operativa"],
                "dias": int(dias),
                "ref": entrada["referencia"],
                "estado": estado_de_cuenta(importe_total, Decimal("0.00")),
            },
        )

    await sesion.execute(
        text(
            """
            UPDATE entradas
               SET estado = 'confirmada', confirmada_en = :ahora,
                   confirmada_por = :quien, importe_total = :importe
             WHERE id = :id
            """
        ),
        {
            "id": entrada_id,
            "ahora": ahora,
            "quien": quien,
            "importe": importe_total,
        },
    )
    await sesion.commit()

    piezas = sum(Decimal(r["cantidad"]) for r in renglones)
    sin_costo = sum(1 for r in renglones if r["costo_unitario"] is None)
    return (
        f"{entrada['folio']} confirmada: {len(renglones)} renglón(es), "
        f"{sin_decimales(piezas)} piezas en la bodega"
        + (f" por ${importe_total:,.2f}" if importe_total else "")
        + ". Ya se puede cargar a un camión."
        + (
            f" {sin_costo} renglón(es) sin costo: sumaron al inventario, pero no "
            "movieron el costo promedio ni entraron a la cuenta por pagar."
            if entrada["motivo"] == "compra" and sin_costo
            else ""
        )
    )


@router.post("/{entrada_id}/cancelar")
async def cancelar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    entrada_id: uuid.UUID,
    motivo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Cancela un borrador. Lo confirmado NO se cancela: se compensa.

    Dejar cancelar una entrada confirmada obligaría a restar existencias por un
    camino que no es un documento, y el libro mayor quedaría con un asiento de
    entrada sin su contraparte. La corrección de una entrada equivocada es otra
    entrada —o un ajuste en contra— con su propio folio.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    try:
        await cancelar_entrada(sesion, entrada_id, motivo=motivo, quien=actor.usuario_id)
    except EntradaNoExiste as e:
        return _a_lista(error=str(e))
    except EntradaRechazada as e:
        return _volver(entrada_id, error=str(e))
    return _a_lista(guardado="Entrada cancelada.")


async def cancelar_entrada(
    sesion, entrada_id: uuid.UUID, *, motivo: str, quien: uuid.UUID
) -> None:
    """Cancela un borrador, con motivo. Lo confirmado no: se compensa."""
    estado = (
        await sesion.execute(
            text("SELECT estado FROM entradas WHERE id = :id FOR UPDATE"),
            {"id": entrada_id},
        )
    ).scalar()
    if estado is None:
        raise EntradaNoExiste("Esa entrada no existe.")
    if estado != "borrador":
        raise EntradaRechazada(
            f"Esta entrada está {estado}. Lo que ya entró al libro mayor se "
            "corrige con un documento en contra, no cancelando."
        )

    razon = texto_o_nulo(motivo, maximo=300)
    if not razon:
        raise EntradaRechazada("Escribe por qué se cancela.")

    await sesion.execute(
        text(
            """
            UPDATE entradas
               SET estado = 'cancelada', cancelada_en = now(),
                   cancelada_por = :quien, cancelacion_motivo = :motivo
             WHERE id = :id
            """
        ),
        {"id": entrada_id, "quien": quien, "motivo": razon},
    )
    await sesion.commit()


# ---------------------------------------------------------------------------
# Las devoluciones que bajan de un camión
# ---------------------------------------------------------------------------
#
# Viven en esta pantalla y no en una sección propia porque es la misma acción
# física: alguien está parado en la bodega con mercancía enfrente y la cuenta.
# Lo único distinto es de dónde viene —un camión en vez de un proveedor— y que el
# documento ya existe: lo abrió el vendedor desde su teléfono.
#
# ─────────────────────────────────────────────────────────────────────────────
# POR QUÉ ESTA PANTALLA NO TIENE BOTÓN DE «RECHAZAR»
# ─────────────────────────────────────────────────────────────────────────────
# `traspasos` admite el estado 'rechazado' desde la 0004, y para el sentido
# bodega → camión tiene sentido: el vendedor se niega a recibir. Aquí no.
#
# Si el vendedor declaró 18 cajas y no llegó ninguna, la respuesta correcta no es
# rechazar el documento —eso le devolvería 18 cajas a un camión que ya no las
# trae— sino **contar cero**. La mercancía se queda en tránsito, con el nombre de
# quien contó y la fecha, y es una diferencia que alguien tiene que explicar.
# Contar es la única respuesta que esta pantalla necesita.


def _a_devolucion(traspaso_id, *, error: str = "", guardado: str = "") -> RedirectResponse:
    cola = []
    if error:
        cola.append(f"error={quote(error)}")
    if guardado:
        cola.append(f"guardado={quote(guardado)}")
    destino = f"/panel/entradas/devolucion/{traspaso_id}" + (
        f"?{'&'.join(cola)}" if cola else ""
    )
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


def _leer_contado(texto: str | None, *, producto: str) -> Decimal:
    """Cuántas piezas se contaron de un renglón.

    **El cero es un dato, no un campo vacío.** Es la respuesta a «el vendedor dijo
    18 y no llegó nada», y por eso se acepta; lo que no se acepta es dejarlo en
    blanco, porque entonces no se sabría si nadie contó ese renglón o si contó cero.

    Se permite contar MÁS de lo declarado: el vendedor pudo bajar una caja que no
    anotó, y eso es un hecho físico (§0.1). Queda como diferencia, igual que si
    faltara.
    """
    crudo = (texto or "").strip().replace(",", "")
    if not crudo:
        raise CapturaInvalida(
            f"Falta cuántas piezas se contaron de {producto}. Si no llegó ninguna, "
            "escribe 0: no es lo mismo que dejarlo en blanco."
        )
    try:
        valor = Decimal(crudo)
    except ArithmeticError as e:
        raise CapturaInvalida(f"«{texto}» no es una cantidad.") from e
    if not valor.is_finite():
        raise CapturaInvalida(f"«{texto}» no es una cantidad.")
    if valor < 0:
        raise CapturaInvalida("No se pueden contar piezas negativas.")
    if valor > MAXIMO_BULTOS:
        raise CapturaInvalida(
            f"{sin_decimales(valor)} piezas de {producto} parece un cero de más."
        )
    return valor.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


async def _devoluciones_por_recibir(sesion) -> list[dict]:
    """Lo que bajó de los camiones y nadie ha contado todavía.

    Es lo primero que esta pantalla tiene que decir, arriba y con número: una
    devolución sin recibir es mercancía que está en la bodega física y no en el
    inventario de la bodega, y una carga hecha con esa cifra deja el almacén en
    negativo. El mismo defecto que un borrador sin confirmar, con otra cara.
    """
    return [
        dict(f)
        for f in (
            await sesion.execute(
                text(
                    """
                    SELECT t.id, t.folio, t.fecha_operativa, t.observaciones,
                           t.creado_en,
                           u.nombre AS vendedor, u.codigo AS vendedor_codigo,
                           o.codigo AS camion, o.nombre AS camion_nombre,
                           COALESCE(d.renglones, 0) AS renglones,
                           COALESCE(d.piezas, 0)    AS piezas
                      FROM traspasos t
                      JOIN almacenes o ON o.id = t.almacen_origen_id
                      LEFT JOIN usuarios u ON u.id = t.solicitado_por
                      LEFT JOIN LATERAL (
                            SELECT count(*) AS renglones, sum(cantidad) AS piezas
                              FROM traspaso_detalle WHERE traspaso_id = t.id
                      ) d ON true
                     WHERE t.estado = 'propuesto' AND o.tipo = 'camion'
                     ORDER BY t.creado_en
                    """
                )
            )
        ).mappings().all()
    ]


@router.get("/devolucion/{traspaso_id}", response_class=HTMLResponse)
async def devolucion(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    traspaso_id: uuid.UUID,
    error: str = "",
    guardado: str = "",
) -> HTMLResponse:
    """Lo que el vendedor declaró, para contarlo renglón por renglón."""
    actor.exigir("inventario.ver")

    traspaso = (
        await sesion.execute(
            text(
                """
                SELECT t.*, u.nombre AS vendedor, u.codigo AS vendedor_codigo,
                       o.codigo AS camion, o.nombre AS camion_nombre,
                       o.sucursal_id,
                       tr.codigo AS transito, tr.nombre AS transito_nombre,
                       q.nombre AS recibio
                  FROM traspasos t
                  JOIN almacenes o ON o.id = t.almacen_origen_id
                  JOIN almacenes tr ON tr.id = t.almacen_destino_id
                  LEFT JOIN usuarios u ON u.id = t.solicitado_por
                  LEFT JOIN usuarios q ON q.id = t.resuelto_por
                 WHERE t.id = :id
                """
            ),
            {"id": traspaso_id},
        )
    ).mappings().first()
    if traspaso is None:
        return _a_lista(error="Esa devolución no existe.")

    renglones = (
        await sesion.execute(
            text(
                """
                SELECT d.id, d.producto_id, d.cantidad, d.cantidad_recibida,
                       p.sku, p.nombre, p.unidad_base,
                       COALESCE(x.cantidad, 0) AS en_transito
                  FROM traspaso_detalle d
                  JOIN productos p ON p.id = d.producto_id
                  LEFT JOIN existencias x ON x.producto_id = d.producto_id
                                         AND x.almacen_id = :transito
                 WHERE d.traspaso_id = :t
                 ORDER BY p.nombre
                """
            ),
            {"t": traspaso_id, "transito": traspaso["almacen_destino_id"]},
        )
    ).mappings().all()

    # Las bodegas de la sucursal del camión primero, pero sin esconder las demás:
    # una ruta puede acabar su día en otra plaza, y la mercancía se baja donde se
    # bajó, no donde el organigrama dice.
    bodegas = await _bodegas(sesion)

    return render(
        peticion,
        "devolucion_detalle.html",
        {
            "traspaso": traspaso,
            "renglones": renglones,
            "bodegas": bodegas,
            "por_recibir": traspaso["estado"] == "propuesto",
            "puede_editar": actor.puede(PERMISO),
            "declarado": sum(Decimal(r["cantidad"]) for r in renglones),
            "contado": sum(
                Decimal(r["cantidad_recibida"])
                for r in renglones
                if r["cantidad_recibida"] is not None
            ),
            "diferencias": [
                r
                for r in renglones
                if r["cantidad_recibida"] is not None
                and Decimal(r["cantidad_recibida"]) != Decimal(r["cantidad"])
            ],
            "error": error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Entradas",
    )


@router.post("/devolucion/{traspaso_id}/recibir")
async def recibir_devolucion(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    traspaso_id: uuid.UUID,
    almacen_destino_id: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Mueve TRÁNSITO → bodega por lo que se contó, y cierra el documento.

    Lo que se contó es lo que entra, no lo que el vendedor declaró. Si declaró 18 y
    llegaron 16, entran 16 y **las otras 2 se quedan en tránsito**: son una
    diferencia con nombre y con fecha, no una cifra que alguien ajustó.

    Idempotente por estado y con `FOR UPDATE`: un doble clic en una pantalla lenta
    no puede meter la mercancía dos veces.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    traspaso = (
        await sesion.execute(
            text(
                "SELECT id, folio, estado, almacen_origen_id, almacen_destino_id "
                "  FROM traspasos WHERE id = :id FOR UPDATE"
            ),
            {"id": traspaso_id},
        )
    ).mappings().first()
    if traspaso is None:
        return _a_lista(error="Esa devolución no existe.")
    if traspaso["estado"] != "propuesto":
        return _a_devolucion(
            traspaso_id, error=f"Esta devolución ya está {traspaso['estado']}."
        )

    try:
        bodega = uuid.UUID(almacen_destino_id)
    except ValueError:
        return _a_devolucion(traspaso_id, error="Elige a qué bodega entra.")

    destino = (
        await sesion.execute(
            text("SELECT tipo, activo, codigo FROM almacenes WHERE id = :a"),
            {"a": bodega},
        )
    ).mappings().first()
    if destino is None or not destino["activo"]:
        return _a_devolucion(traspaso_id, error="Esa bodega no existe o está inactiva.")
    # §0.2: un camión tiene un dueño exclusivo y la oficina no le escribe el
    # inventario. Recibir «en un camión» desde el panel rompería eso por la puerta
    # de atrás, con un documento que sí es legítimo.
    if destino["tipo"] not in TIPOS_DE_DESTINO:
        return _a_devolucion(
            traspaso_id,
            error=f"{destino['codigo']} no es una bodega. La mercancía de un camión "
            "se mueve con una carga, nunca escribiéndole el inventario desde aquí.",
        )

    renglones = (
        await sesion.execute(
            text(
                "SELECT d.id, d.producto_id, d.cantidad, p.nombre "
                "  FROM traspaso_detalle d "
                "  JOIN productos p ON p.id = d.producto_id "
                " WHERE d.traspaso_id = :t ORDER BY p.nombre"
            ),
            {"t": traspaso_id},
        )
    ).mappings().all()
    if not renglones:
        return _a_devolucion(traspaso_id, error="Esta devolución no trae renglones.")

    formulario = await peticion.form()
    try:
        contados = {
            r["id"]: _leer_contado(
                formulario.get(f"contado_{r['id']}"), producto=r["nombre"]
            )
            for r in renglones
        }
    except CapturaInvalida as e:
        return _a_devolucion(traspaso_id, error=str(e))

    ahora = datetime.now(UTC)
    for r in renglones:
        contado = contados[r["id"]]
        await sesion.execute(
            text(
                "UPDATE traspaso_detalle SET cantidad_recibida = :c WHERE id = :id"
            ),
            {"c": contado, "id": r["id"]},
        )
        # Un renglón contado en cero no deja asiento: no se movió nada. La
        # diferencia queda registrada en `cantidad_recibida` y la mercancía sigue
        # en tránsito, que es exactamente lo que pasó.
        if contado == 0:
            continue

        await sesion.execute(
            text(
                """
                INSERT INTO movimientos_inventario
                  (tipo, almacen_origen_id, almacen_destino_id, producto_id,
                   cantidad, documento_tipo, documento_id, usuario_id, fecha_servidor)
                VALUES ('traspaso', :transito, :bodega, :p, :cant,
                        'traspaso', :doc, :quien, :ahora)
                """
            ),
            {
                "transito": traspaso["almacen_destino_id"],
                "bodega": bodega,
                "p": r["producto_id"],
                "cant": contado,
                "doc": traspaso_id,
                "quien": actor.usuario_id,
                "ahora": ahora,
            },
        )
        for almacen, delta in (
            (traspaso["almacen_destino_id"], -contado),
            (bodega, contado),
        ):
            await sesion.execute(
                text(
                    """
                    INSERT INTO existencias (almacen_id, producto_id, cantidad,
                                             actualizado_en)
                    VALUES (:a, :p, :delta, :ahora)
                    ON CONFLICT (almacen_id, producto_id) DO UPDATE
                       SET cantidad = existencias.cantidad + :delta,
                           actualizado_en = :ahora
                    """
                ),
                {"a": almacen, "p": r["producto_id"], "delta": delta, "ahora": ahora},
            )

    # El UPDATE del estado es lo que dispara el delta hacia el teléfono del
    # vendedor (`trg_cambio_traspaso`): su comprobante de que la mercancía dejó de
    # ser su responsabilidad. Va al final, cuando ya está todo escrito.
    await sesion.execute(
        text(
            "UPDATE traspasos SET estado = 'aceptado', resuelto_por = :quien, "
            "       resuelto_en = :ahora WHERE id = :id"
        ),
        {"id": traspaso_id, "quien": actor.usuario_id, "ahora": ahora},
    )

    declarado = sum(Decimal(r["cantidad"]) for r in renglones)
    total = sum(contados.values(), Decimal("0.000"))
    await sesion.execute(
        text(
            """
            INSERT INTO auditoria
              (entidad, entidad_id, accion, usuario_id, motivo,
               datos_antes, datos_despues, ocurrido_en)
            VALUES ('traspaso', :id, 'recibir', :quien, :motivo,
                    CAST(:antes AS jsonb), CAST(:despues AS jsonb), :ahora)
            """
        ),
        {
            "id": traspaso_id,
            "quien": actor.usuario_id,
            "motivo": f"recibida en {destino['codigo']}",
            "antes": json.dumps({"estado": "propuesto", "declarado": str(declarado)}),
            "despues": json.dumps(
                {
                    "estado": "aceptado",
                    "contado": str(total),
                    "almacen_recepcion": str(bodega),
                    "renglones": {str(k): str(v) for k, v in contados.items()},
                }
            ),
            "ahora": ahora,
        },
    )
    await sesion.commit()

    faltan = declarado - total
    if faltan > 0:
        aviso = (
            f" Faltaron {sin_decimales(faltan)} piezas de lo declarado y se quedan "
            "en tránsito: la diferencia queda registrada."
        )
    elif faltan < 0:
        aviso = (
            f" Llegaron {sin_decimales(-faltan)} piezas MÁS de lo declarado. "
            "También queda registrado."
        )
    else:
        aviso = " Cuadró con lo declarado."
    return _a_devolucion(
        traspaso_id,
        guardado=f"{traspaso['folio']} recibida en {destino['codigo']}: "
        f"{sin_decimales(total)} piezas." + aviso,
    )
