"""Salidas de bodega: el conteo que encuentra menos, y la merma.

────────────────────────────────────────────────────────────────────────────
LA REGLA QUE SEPARA ESTO DE §0.1, Y ES LA DECISIÓN CENTRAL
────────────────────────────────────────────────────────────────────────────
Una salida NO PUEDE dejar la existencia en negativo, y eso parece contradecir el
principio fundacional del sistema —«el mundo físico ya ocurrió, el servidor
marca y no rechaza»—. Es justo lo contrario: lo confirma.

§0.1 habla de hechos que YA PASARON EN LA CALLE y llegan tarde: una venta
offline, una merma del camión. Rechazarlas no devuelve la mercancía, así que se
aceptan y se marcan — el manejador de sincronización lo dice con todas sus
letras: «se MARCA, no se rechaza, el cartón ya está roto».

Una salida de bodega es otra cosa por completo: la está TECLEANDO alguien en la
oficina, ahora, con el anaquel a la vista. No es un hecho que llega tarde, es
una corrección que se está escribiendo. Si el sistema dice 3 y alguien captura
una salida de 5, no hay ningún hecho físico que respaldar: el anaquel no puede
tener menos que nada. Es un dedazo, y bloquearlo no niega la realidad — la
protege, porque un asiento equivocado en un libro append-only no se borra, se
arrastra.

    A lo que ya pasó se le cree; a lo que se está capturando se le revisa.

────────────────────────────────────────────────────────────────────────────
EN UN CONTEO SE CAPTURA LO QUE SE CONTÓ
────────────────────────────────────────────────────────────────────────────
Quien hace un conteo anota lo que ve: «80». No anota «faltan 20» — eso exige
restar a mano, a las siete de la mañana, por cada producto, y la resta hecha a
mano es de donde salen los errores que este documento viene a corregir.

Así que el renglón de conteo guarda las tres cifras y la BASE impone la
aritmética (`CONSTRAINT conteo_cuadra`): un bug aquí no puede escribir un
renglón que no cuadre con su propia resta.

Si el conteo encuentra MÁS de lo registrado, esto no es el documento: es una
entrada con motivo 'ajuste'. La pantalla lo dice y manda para allá.

────────────────────────────────────────────────────────────────────────────
Y SI LA EXISTENCIA SE MOVIÓ DESDE QUE SE CONTÓ, NO SE APLICA
────────────────────────────────────────────────────────────────────────────
El renglón guarda la existencia del momento de capturar. Si al confirmar la
existencia ya es otra —salió una carga entre el conteo y el cierre—, la resta
guardada ya no describe nada: el anaquel también perdió esas piezas, así que
aplicarla descontaría dos veces.

Se rechaza el documento diciendo exactamente eso, en vez de hacer la aritmética
equivocada en silencio. Es el mismo razonamiento que el cuadre del piloto: una
cifra congelada solo vale contra el momento en que se congeló.
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
    CAPTURA_LO_CONTADO,
    EXIGEN_MOTIVO,
    PROHIBEN_MOTIVO,
    SALIDAS_EXIGEN_NOTA,
    TIPOS_DE_DESTINO,
    TIPOS_DE_SALIDA,
    etiqueta_de_salida,
    tipo_de_movimiento_de_salida,
)
from app.domain.importes import CantidadInvalida, cantidad_base

router = APIRouter(prefix="/panel/salidas", tags=["panel"], include_in_schema=False)

# El mismo permiso que las entradas: una salida es el ajuste de inventario por
# antonomasia. Y es la operación con la que se puede tapar un robo, así que el
# documento guarda quién confirmó y cuándo — y gerencia no lo lleva.
PERMISO = "inventario.ajustar"

EDITABLE = ("borrador",)

MAXIMO_BULTOS = Decimal("100000")


def _a_lista(*, error: str = "", guardado: str = "") -> RedirectResponse:
    cola = []
    if error:
        cola.append(f"error={quote(error)}")
    if guardado:
        cola.append(f"guardado={quote(guardado)}")
    destino = "/panel/salidas" + (f"?{'&'.join(cola)}" if cola else "")
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


def _volver(salida_id, *, error: str = "", guardado: str = "") -> RedirectResponse:
    cola = []
    if error:
        cola.append(f"error={quote(error)}")
    if guardado:
        cola.append(f"guardado={quote(guardado)}")
    destino = f"/panel/salidas/{salida_id}" + (f"?{'&'.join(cola)}" if cola else "")
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


def _leer_cantidad(texto: str | None, *, que_es: str) -> Decimal:
    """Un número de bultos o de piezas contadas. Entero, y con tope de dedazo.

    Es la tercera copia de este parser en el panel —`cargas.py` y `entradas.py`
    tienen la suya, con sus mensajes— y aquí hace algo distinto además: en un
    conteo lee LO CONTADO, que sí puede ser cero (contar cero es el resultado
    más común de un anaquel vacío), mientras que en los otros dos una cantidad
    de cero no describe ninguna operación.

    Consolidar los tres en `comun.py` es un refactor que toca tres pantallas
    probadas y no se hace a media función nueva; queda anotado aquí.
    """
    crudo = (texto or "").strip().replace(",", "")
    if not crudo:
        raise CapturaInvalida(f"Falta {que_es}.")
    try:
        valor = Decimal(crudo)
    except ArithmeticError as e:
        raise CapturaInvalida(f"«{texto}» no es una cantidad.") from e
    if not valor.is_finite():
        raise CapturaInvalida(f"«{texto}» no es una cantidad.")
    if valor < 0:
        raise CapturaInvalida("La cantidad no puede ser negativa.")
    if valor != valor.to_integral_value():
        raise CapturaInvalida(
            f"Se cuentan unidades completas, no {valor}. Si la presentación trae "
            "una fracción, captúrala en la unidad más chica."
        )
    if valor > MAXIMO_BULTOS:
        raise CapturaInvalida(
            f"{sin_decimales(valor)} en un renglón parece un cero de más."
        )
    return valor


async def _bodegas(sesion):
    """Solo bodegas: el inventario de un camión lo ajusta su liquidación."""
    return (
        await sesion.execute(
            text(
                "SELECT id, codigo, nombre FROM almacenes "
                " WHERE activo AND tipo = ANY(:tipos) ORDER BY codigo"
            ),
            {"tipos": list(TIPOS_DE_DESTINO)},
        )
    ).mappings().all()


async def _motivos(sesion):
    return (
        await sesion.execute(
            text(
                "SELECT codigo, nombre FROM motivos_merma WHERE activo "
                " ORDER BY nombre"
            )
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

    salidas = (
        await sesion.execute(
            text(
                """
                SELECT s.id, s.folio, s.tipo, s.estado, s.motivo_codigo,
                       s.fecha_operativa, s.confirmada_en, s.nota,
                       a.codigo AS bodega, a.nombre AS bodega_nombre,
                       m.nombre AS motivo,
                       u.nombre AS creado_por,
                       COALESCE(d.renglones, 0) AS renglones,
                       COALESCE(d.piezas, 0)    AS piezas
                  FROM salidas s
                  JOIN almacenes a ON a.id = s.almacen_origen_id
                  LEFT JOIN motivos_merma m ON m.codigo = s.motivo_codigo
                  LEFT JOIN usuarios u ON u.id = s.creado_por
                  LEFT JOIN LATERAL (
                        SELECT count(*) AS renglones, sum(cantidad) AS piezas
                          FROM salida_detalle WHERE salida_id = s.id
                  ) d ON true
                 ORDER BY s.fecha_operativa DESC, s.folio DESC
                 LIMIT 100
                """
            )
        )
    ).mappings().all()

    return render(
        peticion,
        "salidas.html",
        {
            "salidas": salidas,
            "tipos": TIPOS_DE_SALIDA,
            "motivos": await _motivos(sesion),
            "bodegas": await _bodegas(sesion),
            "hoy": date.today().isoformat(),
            "puede_editar": actor.puede(PERMISO),
            "borradores": sum(1 for s in salidas if s["estado"] == "borrador"),
            "error": error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Salidas",
    )


@router.post("/nueva")
async def crear(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    almacen_origen_id: Annotated[str, Form()] = "",
    tipo: Annotated[str, Form()] = "",
    motivo_codigo: Annotated[str, Form()] = "",
    fecha: Annotated[str, Form()] = "",
    nota: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    if tipo not in TIPOS_DE_SALIDA:
        return _a_lista(error="Falta elegir qué clase de salida es.")

    try:
        origen = uuid.UUID(almacen_origen_id)
    except ValueError:
        return _a_lista(error="Falta elegir de qué bodega sale.")

    bodega = (
        await sesion.execute(
            text("SELECT id, nombre, tipo FROM almacenes WHERE id = :a AND activo"),
            {"a": origen},
        )
    ).mappings().first()
    if bodega is None:
        return _a_lista(error="Esa bodega no existe o está inactiva.")
    if bodega["tipo"] not in TIPOS_DE_DESTINO:
        # Un camión no se ajusta desde el panel: su faltante se descubre y se
        # cobra en la LIQUIDACIÓN, que compara lo cargado contra lo retornado y
        # ya tiene su propia pantalla. Ajustarlo por aquí haría que el mismo
        # faltante se registrara dos veces.
        return _a_lista(
            error=f"{bodega['nombre']} es un {bodega['tipo']}. El faltante de un "
            "camión se descubre en su liquidación, que compara lo cargado contra "
            "lo retornado; ajustarlo aquí lo contaría dos veces."
        )

    motivo = (motivo_codigo or "").strip() or None
    if tipo in EXIGEN_MOTIVO and not motivo:
        return _a_lista(error="Una merma lleva motivo del catálogo.")
    if tipo in PROHIBEN_MOTIVO and motivo:
        return _a_lista(
            error="Un faltante de conteo no lleva motivo: si se supiera la causa, "
            "se habría capturado como merma el día que pasó. Elegir uno sin saber "
            "convierte un dato duro en una acusación inventada."
        )

    hoy = date.today()
    try:
        operativa = date.fromisoformat(fecha) if fecha else hoy
    except ValueError:
        operativa = hoy
    if operativa > hoy:
        return _a_lista(error="La fecha no puede ser futura.")

    texto_nota = texto_o_nulo(nota, maximo=500)
    if tipo in SALIDAS_EXIGEN_NOTA and not texto_nota:
        return _a_lista(
            error="Escribe la nota: en un conteo hace falta saber quién contó, y "
            "en una merma qué pasó más allá del código del motivo. Es el "
            "documento que alguien va a leer el día que la cifra no cuadre."
        )

    consecutivo = (
        await sesion.execute(text("SELECT nextval('seq_folio_salida')"))
    ).scalar_one()
    nueva = uuid.uuid4()
    await sesion.execute(
        text(
            """
            INSERT INTO salidas
                (id, folio, almacen_origen_id, tipo, motivo_codigo,
                 fecha_operativa, nota, estado, creado_por)
            VALUES (:id, :folio, :a, :tipo, :motivo, :fecha, :nota,
                    'borrador', :quien)
            """
        ),
        {
            "id": nueva,
            "folio": f"SA-{consecutivo:06d}",
            "a": origen,
            "tipo": tipo,
            "motivo": motivo,
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
@router.get("/{salida_id}", response_class=HTMLResponse)
async def detalle(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    salida_id: uuid.UUID,
    error: str = "",
    guardado: str = "",
) -> HTMLResponse:
    actor.exigir("inventario.ver")

    salida = (
        await sesion.execute(
            text(
                """
                SELECT s.*, a.codigo AS bodega, a.nombre AS bodega_nombre,
                       m.nombre AS motivo, m.afecta_vendedor,
                       u.nombre AS creado_por_nombre,
                       c.nombre AS confirmada_por_nombre
                  FROM salidas s
                  JOIN almacenes a ON a.id = s.almacen_origen_id
                  LEFT JOIN motivos_merma m ON m.codigo = s.motivo_codigo
                  LEFT JOIN usuarios u ON u.id = s.creado_por
                  LEFT JOIN usuarios c ON c.id = s.confirmada_por
                 WHERE s.id = :id
                """
            ),
            {"id": salida_id},
        )
    ).mappings().first()
    if salida is None:
        return _a_lista(error="Esa salida no existe.")

    renglones = (
        await sesion.execute(
            text(
                """
                SELECT d.id, d.producto_id, d.cantidad, d.contado,
                       d.existencia_al_capturar, d.unidad_codigo,
                       d.unidades_capturadas, d.lote,
                       p.nombre, p.sku, p.unidad_base,
                       COALESCE(x.cantidad, 0) AS existencia
                  FROM salida_detalle d
                  JOIN productos p ON p.id = d.producto_id
                  LEFT JOIN existencias x ON x.producto_id = d.producto_id
                                         AND x.almacen_id = :a
                 WHERE d.salida_id = :s
                 ORDER BY p.nombre, d.lote NULLS FIRST
                """
            ),
            {"s": salida_id, "a": salida["almacen_origen_id"]},
        )
    ).mappings().all()

    # La proyección por PRODUCTO, no por renglón: con dos lotes del mismo
    # producto, restar solo lo del renglón daría dos flechas y ninguna sería el
    # número final. Mismo defecto que se corrigió en las entradas.
    proyectado: dict = {}
    # Y el aviso de lo que dejaría en negativo, que es lo que el confirmar va a
    # rechazar. Decirlo en el borrador evita que alguien capture veinte
    # renglones y se entere al final.
    for r in renglones:
        proyectado[r["producto_id"]] = Decimal(r["existencia"]) - sum(
            Decimal(x["cantidad"])
            for x in renglones
            if x["producto_id"] == r["producto_id"]
        )
    negativos = sorted(
        {
            r["nombre"]
            for r in renglones
            if proyectado[r["producto_id"]] < 0
        }
    )
    # Y los renglones de conteo cuya existencia se movió desde que se contó.
    movidos = sorted(
        {
            r["nombre"]
            for r in renglones
            if r["existencia_al_capturar"] is not None
            and Decimal(r["existencia_al_capturar"]) != Decimal(r["existencia"])
        }
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
        "salida_detalle.html",
        {
            "salida": salida,
            "tipo_etiqueta": etiqueta_de_salida(salida["tipo"]),
            "cuenta": salida["tipo"] in CAPTURA_LO_CONTADO,
            "renglones": renglones,
            "proyectado": proyectado,
            "negativos": negativos,
            "movidos": movidos,
            "presentaciones": presentaciones,
            "editable": salida["estado"] in EDITABLE,
            "puede_editar": actor.puede(PERMISO),
            "total_piezas": sum(Decimal(r["cantidad"]) for r in renglones),
            "error": error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Salidas",
    )


@router.post("/{salida_id}/renglon")
async def agregar_renglon(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    salida_id: uuid.UUID,
    producto: Annotated[str, Form()] = "",
    unidad_codigo: Annotated[str, Form()] = "",
    cantidad: Annotated[str, Form()] = "",
    lote: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Agrega un renglón. En un conteo, `cantidad` es LO CONTADO.

    `FOR UPDATE` por lo mismo que en las entradas: sin candado, un renglón
    agregado mientras alguien confirma quedaría en el documento y no en el libro
    mayor.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    salida = (
        await sesion.execute(
            text(
                "SELECT estado, tipo, almacen_origen_id FROM salidas "
                " WHERE id = :id FOR UPDATE"
            ),
            {"id": salida_id},
        )
    ).mappings().first()
    if salida is None:
        return _a_lista(error="Esa salida no existe.")
    if salida["estado"] not in EDITABLE:
        return _volver(
            salida_id,
            error=f"Esta salida ya está {salida['estado']}. Lo confirmado se "
            "corrige con otro documento, no editando el historial.",
        )

    cuenta = salida["tipo"] in CAPTURA_LO_CONTADO

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
        return _volver(salida_id, error=f"No hay producto activo con clave «{clave}».")

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
            salida_id,
            error=f"{fila['nombre']} no tiene la presentación {unidad_codigo}.",
        )

    try:
        tecleado = _leer_cantidad(
            cantidad,
            que_es="cuánto se contó" if cuenta else "cuántos bultos salieron",
        )
        en_base = (
            cantidad_base(tecleado, Decimal(presentacion["factor"]))
            if tecleado
            # `cantidad_base` exige cantidad > 0, y en un conteo el cero es el
            # resultado más común de un anaquel vacío: no hay nada que convertir.
            else Decimal(0)
        )
    except (CapturaInvalida, CantidadInvalida) as e:
        return _volver(salida_id, error=str(e))

    existencia = Decimal(
        (
            await sesion.execute(
                text(
                    "SELECT COALESCE(cantidad, 0) FROM existencias "
                    " WHERE almacen_id = :a AND producto_id = :p"
                ),
                {"a": salida["almacen_origen_id"], "p": fila["id"]},
            )
        ).scalar()
        or 0
    )

    numero_lote = texto_o_nulo(lote, maximo=40)

    if cuenta:
        # En un conteo, lo que SALE es la diferencia, y la calcula el sistema.
        if numero_lote:
            return _volver(
                salida_id,
                error="Un conteo es por producto, no por lote: `existencias` "
                "guarda un número por (almacén, producto) y no tiene dimensión "
                "de lote. Para identificar una tarima, usa una merma.",
            )
        if en_base > existencia:
            # El conteo encontró MÁS. Este no es el documento.
            sobran = en_base - existencia
            return _volver(
                salida_id,
                error=f"Contaste {sin_decimales(en_base)} y el sistema tiene "
                f"{sin_decimales(existencia)}: sobran {sin_decimales(sobran)}. "
                "Eso es una ENTRADA con motivo «ajuste por conteo», no una "
                "salida.",
            )
        if en_base == existencia:
            return _volver(
                salida_id,
                error=f"{fila['nombre']} cuadra: contaste lo mismo que el sistema "
                "tiene. Un renglón de cero no describe ningún ajuste.",
            )
        sale = existencia - en_base
        contado, al_capturar = en_base, existencia
    else:
        if not tecleado:
            return _volver(
                salida_id, error="Una merma de cero no describe ninguna pérdida."
            )
        if fila["maneja_lote"] and not numero_lote:
            return _volver(
                salida_id,
                error=f"{fila['nombre']} maneja lote: captura el de la tarima que "
                "se perdió.",
            )
        sale = en_base
        contado, al_capturar = None, None

    await sesion.execute(
        text(
            """
            INSERT INTO salida_detalle
                (id, salida_id, producto_id, cantidad, contado,
                 existencia_al_capturar, unidad_codigo, unidades_capturadas, lote)
            VALUES (:id, :s, :p, :cant, :contado, :al_capturar, :u, :bultos, :lote)
            ON CONFLICT (salida_id, producto_id, COALESCE(lote, ''))
              DO UPDATE SET
                 -- En un conteo se REEMPLAZA: contar dos veces el mismo producto
                 -- significa que la primera cuenta estaba mal, no que haya el
                 -- doble de faltante. En una merma se SUMA, como en las
                 -- entradas: son dos pérdidas distintas de la misma tarima.
                 cantidad = CASE
                     WHEN excluded.contado IS NOT NULL THEN excluded.cantidad
                     ELSE salida_detalle.cantidad + excluded.cantidad END,
                 contado = excluded.contado,
                 existencia_al_capturar = excluded.existencia_al_capturar,
                 unidades_capturadas = CASE
                     WHEN excluded.contado IS NOT NULL THEN excluded.unidades_capturadas
                     WHEN salida_detalle.unidad_codigo = excluded.unidad_codigo
                     THEN salida_detalle.unidades_capturadas
                          + excluded.unidades_capturadas
                     ELSE NULL END,
                 unidad_codigo = CASE
                     WHEN excluded.contado IS NOT NULL THEN excluded.unidad_codigo
                     WHEN salida_detalle.unidad_codigo = excluded.unidad_codigo
                     THEN salida_detalle.unidad_codigo
                     ELSE NULL END
            """
        ),
        {
            "id": uuid.uuid4(),
            "s": salida_id,
            "p": fila["id"],
            "cant": sale,
            "contado": contado,
            "al_capturar": al_capturar,
            "u": unidad_codigo,
            "bultos": tecleado or None,
            "lote": numero_lote,
        },
    )
    await sesion.commit()

    if cuenta:
        aviso = (
            f"{fila['nombre']}: contaste {sin_decimales(en_base)} "
            f"{fila['unidad_base']} y el sistema tenía "
            f"{sin_decimales(existencia)} → sale(n) {sin_decimales(sale)}."
        )
    else:
        aviso = (
            f"{fila['nombre']}: {sin_decimales(tecleado)} {unidad_codigo} "
            f"= {sin_decimales(sale)} {fila['unidad_base']}."
        )
    return _volver(salida_id, guardado=aviso)


@router.post("/{salida_id}/renglon/{renglon_id}/quitar")
async def quitar_renglon(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    salida_id: uuid.UUID,
    renglon_id: uuid.UUID,
    csrf: Annotated[str, Form()] = "",
):
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    estado = (
        await sesion.execute(
            text("SELECT estado FROM salidas WHERE id = :id FOR UPDATE"),
            {"id": salida_id},
        )
    ).scalar()
    if estado is None:
        return _a_lista(error="Esa salida no existe.")
    if estado not in EDITABLE:
        return _volver(
            salida_id,
            error="Esta salida ya se confirmó: el renglón ya es un asiento del "
            "libro mayor y no se quita, se compensa.",
        )

    await sesion.execute(
        text("DELETE FROM salida_detalle WHERE id = :r AND salida_id = :s"),
        {"r": renglon_id, "s": salida_id},
    )
    await sesion.commit()
    return _volver(salida_id, guardado="Renglón quitado.")


# ---------------------------------------------------------------------------
# Confirmar
# ---------------------------------------------------------------------------
@router.post("/{salida_id}/confirmar")
async def confirmar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    salida_id: uuid.UUID,
    csrf: Annotated[str, Form()] = "",
):
    """Escribe el libro mayor y las existencias. Todo o nada, y con dos guardias.

    Las dos validaciones van DENTRO de la transacción y después de tomar el
    candado de cada renglón de `existencias`. Validarlas antes, al capturar,
    sería mirar un número que cualquier carga puede mover un segundo después: lo
    que importa es la existencia en el instante en que se escribe el asiento.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    salida = (
        await sesion.execute(
            text(
                "SELECT id, folio, estado, tipo, motivo_codigo, "
                "       almacen_origen_id, fecha_operativa "
                "  FROM salidas WHERE id = :id FOR UPDATE"
            ),
            {"id": salida_id},
        )
    ).mappings().first()
    if salida is None:
        return _a_lista(error="Esa salida no existe.")
    if salida["estado"] != "borrador":
        return _volver(salida_id, error=f"Esta salida ya está {salida['estado']}.")

    renglones = (
        await sesion.execute(
            text(
                """
                SELECT d.producto_id, d.cantidad, d.contado,
                       d.existencia_al_capturar, d.lote, p.nombre
                  FROM salida_detalle d
                  JOIN productos p ON p.id = d.producto_id
                 WHERE d.salida_id = :s
                 ORDER BY p.nombre
                """
            ),
            {"s": salida_id},
        )
    ).mappings().all()
    if not renglones:
        return _volver(
            salida_id, error="No puedes confirmar una salida sin un solo renglón."
        )

    # El candado sobre las existencias involucradas, antes de validar nada.
    #
    # El `ORDER BY` NO es una garantía de orden de bloqueo —PostgreSQL puede
    # tomar los candados mientras recorre y ordenar después—, así que no se
    # afirma aquí que el interbloqueo sea imposible. Con la llave primaria
    # (almacen_id, producto_id) el recorrido es por producto y en la práctica
    # queda determinista, que es lo que se puede decir con honestidad. Y si dos
    # confirmaciones simultáneas llegaran a abrazarse, PostgreSQL aborta una: se
    # ve como un error y se reintenta, no corrompe nada — el candado está tomado
    # antes de escribir.
    productos = sorted({r["producto_id"] for r in renglones})
    existencias = {
        f["producto_id"]: Decimal(f["cantidad"])
        for f in (
            await sesion.execute(
                text(
                    "SELECT producto_id, cantidad FROM existencias "
                    " WHERE almacen_id = :a AND producto_id = ANY(:ps) "
                    " ORDER BY producto_id FOR UPDATE"
                ),
                {"a": salida["almacen_origen_id"], "ps": productos},
            )
        ).mappings().all()
    }

    # Guardia 1: en un conteo, la existencia no se puede haber movido desde que
    # se contó. Si se movió, la resta guardada ya no describe nada — el anaquel
    # también perdió esas piezas, así que aplicarla descontaría dos veces.
    movidos = [
        r["nombre"]
        for r in renglones
        if r["existencia_al_capturar"] is not None
        and existencias.get(r["producto_id"], Decimal(0))
        != Decimal(r["existencia_al_capturar"])
    ]
    if movidos:
        return _volver(
            salida_id,
            error="La existencia se movió desde que se contó: "
            + ", ".join(movidos[:5])
            + (f" y {len(movidos) - 5} más" if len(movidos) > 5 else "")
            + ". Salió o entró mercancía en medio, así que la resta guardada ya "
            "no describe el anaquel. Quita esos renglones y vuelve a contarlos.",
        )

    # Guardia 2: ninguna salida deja la existencia en negativo. Ver el
    # encabezado del módulo: a lo que ya pasó se le cree, a lo que se está
    # capturando se le revisa.
    faltaria: list[str] = []
    for producto in productos:
        tiene = existencias.get(producto, Decimal(0))
        sale = sum(
            Decimal(r["cantidad"]) for r in renglones if r["producto_id"] == producto
        )
        if sale > tiene:
            nombre = next(r["nombre"] for r in renglones if r["producto_id"] == producto)
            faltaria.append(
                f"{nombre} (hay {sin_decimales(tiene)}, salen {sin_decimales(sale)})"
            )
    if faltaria:
        return _volver(
            salida_id,
            error="Esto dejaría la bodega en negativo: "
            + "; ".join(faltaria[:5])
            + (f" y {len(faltaria) - 5} más" if len(faltaria) > 5 else "")
            + ". El anaquel no puede tener menos que nada, así que es un error de "
            "captura — y un asiento equivocado en el libro mayor no se borra.",
        )

    tipo = tipo_de_movimiento_de_salida(salida["tipo"])
    # Una merma de bodega viaja al almacén de merma si existe, igual que la del
    # camión (`infra/sync/manejadores.py`). Si no hay almacén de merma
    # configurado, el destino queda en NULL: el CHECK del libro mayor solo exige
    # uno de los dos lados.
    destino = None
    if tipo == "merma":
        destino = (
            await sesion.execute(
                text(
                    "SELECT id FROM almacenes WHERE tipo = 'merma' AND activo "
                    " ORDER BY codigo LIMIT 1"
                )
            )
        ).scalar_one_or_none()

    ahora = datetime.now(UTC)
    for r in renglones:
        await sesion.execute(
            text(
                """
                INSERT INTO movimientos_inventario
                  (tipo, almacen_origen_id, almacen_destino_id, producto_id,
                   cantidad, lote, documento_tipo, documento_id,
                   usuario_id, fecha_servidor)
                VALUES (:tipo, :origen, :destino, :p, :cant, :lote,
                        'salida', :doc, :quien, :ahora)
                """
            ),
            {
                "tipo": tipo,
                "origen": salida["almacen_origen_id"],
                "destino": destino,
                "p": r["producto_id"],
                "cant": r["cantidad"],
                "lote": r["lote"],
                "doc": salida_id,
                "quien": actor.usuario_id,
                "ahora": ahora,
            },
        )
        await sesion.execute(
            text(
                """
                UPDATE existencias
                   SET cantidad = cantidad - :cant, actualizado_en = :ahora
                 WHERE almacen_id = :a AND producto_id = :p
                """
            ),
            {
                "a": salida["almacen_origen_id"],
                "p": r["producto_id"],
                "cant": r["cantidad"],
                "ahora": ahora,
            },
        )
        # Y si la merma va a un almacén de merma, ahí SÍ entra: el libro mayor
        # dice origen y destino, y la existencia de los dos lados tiene que
        # reflejarlo o el almacén de merma quedaría siempre en cero.
        if destino is not None:
            await sesion.execute(
                text(
                    """
                    INSERT INTO existencias (almacen_id, producto_id, cantidad,
                                             actualizado_en)
                    VALUES (:a, :p, :cant, :ahora)
                    ON CONFLICT (almacen_id, producto_id) DO UPDATE
                       SET cantidad = existencias.cantidad + :cant,
                           actualizado_en = :ahora
                    """
                ),
                {
                    "a": destino,
                    "p": r["producto_id"],
                    "cant": r["cantidad"],
                    "ahora": ahora,
                },
            )

    await sesion.execute(
        text(
            """
            UPDATE salidas
               SET estado = 'confirmada', confirmada_en = :ahora,
                   confirmada_por = :quien
             WHERE id = :id
            """
        ),
        {"id": salida_id, "ahora": ahora, "quien": actor.usuario_id},
    )
    await sesion.commit()

    piezas = sum(Decimal(r["cantidad"]) for r in renglones)
    return _volver(
        salida_id,
        guardado=(
            f"{salida['folio']} confirmada: salieron {sin_decimales(piezas)} "
            f"piezas de la bodega en {len(renglones)} renglón(es)."
        ),
    )


@router.post("/{salida_id}/cancelar")
async def cancelar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    salida_id: uuid.UUID,
    motivo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Cancela un borrador. Lo confirmado se compensa con una entrada."""
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    estado = (
        await sesion.execute(
            text("SELECT estado FROM salidas WHERE id = :id FOR UPDATE"),
            {"id": salida_id},
        )
    ).scalar()
    if estado is None:
        return _a_lista(error="Esa salida no existe.")
    if estado != "borrador":
        return _volver(
            salida_id,
            error=f"Esta salida está {estado}. Lo que ya entró al libro mayor se "
            "corrige con una entrada de ajuste, no cancelando.",
        )

    razon = texto_o_nulo(motivo, maximo=300)
    if not razon:
        return _volver(salida_id, error="Escribe por qué se cancela.")

    await sesion.execute(
        text(
            """
            UPDATE salidas
               SET estado = 'cancelada', cancelada_en = now(),
                   cancelada_por = :quien, cancelacion_motivo = :motivo
             WHERE id = :id
            """
        ),
        {"id": salida_id, "quien": actor.usuario_id, "motivo": razon},
    )
    await sesion.commit()
    return _a_lista(guardado="Salida cancelada.")
