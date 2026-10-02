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
NO HAY COSTO, Y ES UNA DECISIÓN
────────────────────────────────────────────────────────────────────────────
Una compra tiene un costo y era tentador capturarlo: es un campo más.

Pero `productos` no tiene columna de costo y no hay módulo de compras —es Fase
10 del plan—, así que el número no alimentaría nada: ni margen, ni valuación, ni
costo de lo vendido. Un campo que se captura y nadie lee es peor que su
ausencia, porque **parece** que el sistema sabe el costo. Y elegir aquí entre
costo promedio, último costo o PEPS sería improvisar una regla contable en una
pantalla de bodega.

Lo que sí se guarda es la referencia del papel: con la remisión a la mano, el
costo se recupera el día que exista dónde ponerlo.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
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
# Y gerencia no lo tiene, a propósito: es la operación con la que se puede tapar
# un faltante, y quien mide no es quien ajusta (ADR 0002 §0).
PERMISO = "inventario.ajustar"

# Lo único editable es el borrador. Lo ya confirmado se corrige con otro
# documento —un ajuste en sentido contrario—, nunca editando el historial.
EDITABLE = ("borrador",)

# Tope de error de dedo, no regla de negocio. Un renglón de 100,000 piezas en
# una bodega de abarrotes es un cero de más, y confirmado deja la existencia
# inservible hasta que alguien capture el ajuste contrario.
MAXIMO_BULTOS = Decimal("100000")


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

    entradas = (
        await sesion.execute(
            text(
                """
                SELECT e.id, e.folio, e.motivo, e.estado, e.proveedor,
                       e.referencia, e.fecha_operativa, e.confirmada_en,
                       a.codigo AS bodega, a.nombre AS bodega_nombre,
                       u.nombre AS creado_por,
                       COALESCE(d.renglones, 0) AS renglones,
                       COALESCE(d.piezas, 0)    AS piezas
                  FROM entradas e
                  JOIN almacenes a ON a.id = e.almacen_destino_id
                  LEFT JOIN usuarios u ON u.id = e.creado_por
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

    return render(
        peticion,
        "entradas.html",
        {
            "entradas": entradas,
            "motivos": MOTIVOS,
            "bodegas": await _bodegas(sesion),
            "hoy": date.today().isoformat(),
            "puede_editar": actor.puede(PERMISO),
            "borradores": sum(1 for e in entradas if e["estado"] == "borrador"),
            "error": error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Entradas",
    )


@router.post("/nueva")
async def crear(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    almacen_destino_id: Annotated[str, Form()] = "",
    motivo: Annotated[str, Form()] = "",
    proveedor: Annotated[str, Form()] = "",
    referencia: Annotated[str, Form()] = "",
    fecha: Annotated[str, Form()] = "",
    nota: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Abre el borrador. No mueve nada todavía."""
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    if motivo not in MOTIVOS:
        return _a_lista(error="Falta elegir por qué entra la mercancía.")

    try:
        destino = uuid.UUID(almacen_destino_id)
    except ValueError:
        return _a_lista(error="Falta elegir a qué bodega entra.")

    bodega = (
        await sesion.execute(
            text("SELECT id, codigo, nombre, tipo FROM almacenes WHERE id = :a AND activo"),
            {"a": destino},
        )
    ).mappings().first()
    if bodega is None:
        return _a_lista(error="Esa bodega no existe o está inactiva.")
    if bodega["tipo"] not in TIPOS_DE_DESTINO:
        # §0.2. El mensaje dice el camino correcto, no solo que no se puede.
        return _a_lista(
            error=f"{bodega['nombre']} es un {bodega['tipo']}, no una bodega. La "
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
        return _a_lista(
            error="La fecha no puede ser futura: una entrada con fecha de mañana "
            "es mercancía que todavía no llegó."
        )

    texto_nota = texto_o_nulo(nota, maximo=500)
    if motivo in EXIGEN_NOTA and not texto_nota:
        return _a_lista(
            error="El inventario inicial necesita una nota: es el documento que "
            "explica de dónde salió todo el inventario del arranque, y se lee el "
            "día que algo no cuadra."
        )

    consecutivo = (
        await sesion.execute(text("SELECT nextval('seq_folio_entrada')"))
    ).scalar_one()
    nueva = uuid.uuid4()
    await sesion.execute(
        text(
            """
            INSERT INTO entradas
                (id, folio, almacen_destino_id, motivo, proveedor, referencia,
                 fecha_operativa, nota, estado, creado_por)
            VALUES (:id, :folio, :a, :motivo, :prov, :ref, :fecha, :nota,
                    'borrador', :quien)
            """
        ),
        {
            "id": nueva,
            "folio": f"EN-{consecutivo:06d}",
            "a": destino,
            "motivo": motivo,
            "prov": texto_o_nulo(proveedor, maximo=160),
            "ref": texto_o_nulo(referencia, maximo=80),
            "fecha": operativa,
            "nota": texto_nota,
            "quien": actor.usuario_id,
        },
    )
    await sesion.commit()
    return _volver(nueva, guardado="Borrador abierto. Captura los renglones.")


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
        return _a_lista(error="Esa entrada no existe.")

    # Cada renglón con la existencia ACTUAL del producto en esa bodega al lado.
    # No es adorno: quien recibe tiene que poder ver si lo que está capturando
    # deja una cifra con sentido, y es donde se nota un cero de más antes de
    # confirmar — después ya es un asiento que no se borra.
    renglones = (
        await sesion.execute(
            text(
                """
                SELECT d.id, d.producto_id, d.cantidad, d.unidad_codigo,
                       d.unidades_capturadas, d.lote, d.caducidad,
                       p.nombre, p.sku, p.unidad_base,
                       COALESCE(x.cantidad, 0) AS existencia
                  FROM entrada_detalle d
                  JOIN productos p ON p.id = d.producto_id
                  LEFT JOIN existencias x ON x.producto_id = d.producto_id
                                         AND x.almacen_id = :a
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
            "renglones": renglones,
            "proyectado": proyectado,
            "presentaciones": presentaciones,
            "editable": entrada["estado"] in EDITABLE,
            "puede_editar": actor.puede(PERMISO),
            "total_piezas": sum(Decimal(r["cantidad"]) for r in renglones),
            "error": error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Entradas",
    )


@router.post("/{entrada_id}/renglon")
async def agregar_renglon(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    entrada_id: uuid.UUID,
    producto: Annotated[str, Form()] = "",
    unidad_codigo: Annotated[str, Form()] = "",
    cantidad: Annotated[str, Form()] = "",
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
                "SELECT estado, almacen_destino_id FROM entradas "
                " WHERE id = :id FOR UPDATE"
            ),
            {"id": entrada_id},
        )
    ).mappings().first()
    if entrada is None:
        return _a_lista(error="Esa entrada no existe.")
    if entrada["estado"] not in EDITABLE:
        return _volver(
            entrada_id,
            error=f"Esta entrada ya está {entrada['estado']}. Lo confirmado se "
            "corrige con otro documento, no editando el historial.",
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
        return _volver(entrada_id, error=f"No hay producto activo con clave «{clave}».")

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
        return _volver(
            entrada_id,
            error=f"{fila['nombre']} no tiene la presentación {unidad_codigo}.",
        )

    try:
        bultos = _leer_bultos(cantidad)
        # La multiplicación, una sola vez y con la misma función que usa el
        # teléfono al armar una partida: la conversión caja→pieza está escrita
        # en un solo lugar del sistema.
        en_base = cantidad_base(bultos, Decimal(presentacion["factor"]))
    except (CapturaInvalida, CantidadInvalida) as e:
        return _volver(entrada_id, error=str(e))

    vence = None
    if (caducidad or "").strip():
        try:
            vence = date.fromisoformat(caducidad.strip())
        except ValueError:
            return _volver(
                entrada_id, error=f"«{caducidad}» no es una fecha de caducidad."
            )

    numero_lote = texto_o_nulo(lote, maximo=40)
    if fila["maneja_lote"] and not numero_lote:
        # El producto se marcó como de lote en el catálogo: recibirlo sin lote
        # deja una merma por caducidad imposible de rastrear hasta su tarima.
        return _volver(
            entrada_id,
            error=f"{fila['nombre']} maneja lote: captura el de la tarima que "
            "estás recibiendo.",
        )

    await sesion.execute(
        text(
            """
            INSERT INTO entrada_detalle
                (id, entrada_id, producto_id, cantidad, unidad_codigo,
                 unidades_capturadas, lote, caducidad)
            VALUES (:id, :e, :p, :cant, :u, :bultos, :lote, :cad)
            ON CONFLICT (entrada_id, producto_id, COALESCE(lote, ''))
              DO UPDATE SET
                 cantidad = entrada_detalle.cantidad + excluded.cantidad,
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
            "u": unidad_codigo,
            "bultos": bultos,
            "lote": numero_lote,
            "cad": vence,
        },
    )
    await sesion.commit()
    return _volver(
        entrada_id,
        guardado=(
            f"{fila['nombre']}: {sin_decimales(bultos)} {unidad_codigo} "
            f"= {sin_decimales(en_base)} {fila['unidad_base']}."
        ),
    )


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
        return _a_lista(error="Esa entrada no existe.")
    if estado not in EDITABLE:
        return _volver(
            entrada_id,
            error="Esta entrada ya se confirmó: el renglón ya es un asiento del "
            "libro mayor y no se quita, se compensa.",
        )

    await sesion.execute(
        text("DELETE FROM entrada_detalle WHERE id = :r AND entrada_id = :e"),
        {"r": renglon_id, "e": entrada_id},
    )
    await sesion.commit()
    return _volver(entrada_id, guardado="Renglón quitado.")


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

    entrada = (
        await sesion.execute(
            text(
                "SELECT id, folio, estado, motivo, almacen_destino_id, "
                "       fecha_operativa "
                "  FROM entradas WHERE id = :id FOR UPDATE"
            ),
            {"id": entrada_id},
        )
    ).mappings().first()
    if entrada is None:
        return _a_lista(error="Esa entrada no existe.")
    if entrada["estado"] != "borrador":
        return _volver(entrada_id, error=f"Esta entrada ya está {entrada['estado']}.")

    renglones = (
        await sesion.execute(
            text(
                "SELECT producto_id, cantidad, lote, caducidad "
                "  FROM entrada_detalle WHERE entrada_id = :e"
            ),
            {"e": entrada_id},
        )
    ).mappings().all()
    if not renglones:
        return _volver(
            entrada_id, error="No puedes confirmar una entrada sin un solo renglón."
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
                "quien": actor.usuario_id,
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

    await sesion.execute(
        text(
            """
            UPDATE entradas
               SET estado = 'confirmada', confirmada_en = :ahora,
                   confirmada_por = :quien
             WHERE id = :id
            """
        ),
        {"id": entrada_id, "ahora": ahora, "quien": actor.usuario_id},
    )
    await sesion.commit()

    piezas = sum(Decimal(r["cantidad"]) for r in renglones)
    return _volver(
        entrada_id,
        guardado=(
            f"{entrada['folio']} confirmada: {len(renglones)} renglón(es), "
            f"{sin_decimales(piezas)} piezas en la bodega. Ya se puede cargar a "
            "un camión."
        ),
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

    estado = (
        await sesion.execute(
            text("SELECT estado FROM entradas WHERE id = :id FOR UPDATE"),
            {"id": entrada_id},
        )
    ).scalar()
    if estado is None:
        return _a_lista(error="Esa entrada no existe.")
    if estado != "borrador":
        return _volver(
            entrada_id,
            error=f"Esta entrada está {estado}. Lo que ya entró al libro mayor se "
            "corrige con un documento en contra, no cancelando.",
        )

    razon = texto_o_nulo(motivo, maximo=300)
    if not razon:
        return _volver(entrada_id, error="Escribe por qué se cancela.")

    await sesion.execute(
        text(
            """
            UPDATE entradas
               SET estado = 'cancelada', cancelada_en = now(),
                   cancelada_por = :quien, cancelacion_motivo = :motivo
             WHERE id = :id
            """
        ),
        {"id": entrada_id, "quien": actor.usuario_id, "motivo": razon},
    )
    await sesion.commit()
    return _a_lista(guardado="Entrada cancelada.")
