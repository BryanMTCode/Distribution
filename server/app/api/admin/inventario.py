"""Existencias y el libro mayor: poder ver el inventario sin abrir una carga.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTA PANTALLA FALTABA Y SE NOTA
────────────────────────────────────────────────────────────────────────────
Las existencias estaban visibles solo de refilón: al armar un borrador de carga,
al lado de cada renglón. Para saber qué hay en la bodega había que empezar a
cargar un camión, lo cual es absurdo y además peligroso — se abre un borrador para
consultar y alguien lo confirma.

────────────────────────────────────────────────────────────────────────────
DOS TABLAS, Y LA DIFERENCIA ENTRE ELLAS ES LA PANTALLA
────────────────────────────────────────────────────────────────────────────
`existencias` es una **caché transaccional**: un número por (almacén, producto),
actualizado en la misma transacción que el movimiento.

`movimientos_inventario` es el **libro mayor inmutable**: append-only garantizado
por disparador. Es la verdad auditable.

La caché es lo que se consulta cien veces al día; el libro mayor es lo que
responde "¿y cómo llegó a ese número?". Esta pantalla muestra la primera y deja
ver la segunda por producto, porque la pregunta que sigue a un número raro es
siempre la misma.

**Y compara las dos.** Si la suma del libro mayor no cuadra con la caché, hay un
bug en alguna transacción que escribió una y no la otra; eso no se detecta mirando
la caché, por definición. El job de reconciliación nocturno existe para esto, y
esta pantalla permite verlo sin esperar a la noche.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.api.admin.comun import CapturaInvalida, SesionDep, render
from app.api.admin.sesion_web import ActorWeb, exigir_csrf
from app.infra.valor_de_inventario import precio_de_venta

# Los almacenes que se pueden ajustar a mano desde el panel.
TIPOS_AJUSTABLES = ("camion", "bodega")

router = APIRouter(prefix="/panel/inventario", tags=["panel"], include_in_schema=False)


@router.get("", response_class=HTMLResponse)
async def listar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    almacen: str = "",
    q: str = "",
    filtro: str = "con_existencia",
) -> HTMLResponse:
    """Las existencias de un almacén.

    Abre en la bodega principal y mostrando **solo lo que tiene existencia**: una
    lista con los trescientos productos del catálogo en cero no responde ninguna
    pregunta operativa.
    """
    actor.exigir("inventario.ver")

    if filtro not in ("con_existencia", "negativos", "todos"):
        filtro = "con_existencia"

    almacenes = (
        await sesion.execute(
            text(
                f"""
                SELECT a.id, a.codigo, a.nombre, a.tipo,
                       u.nombre AS responsable,
                       COALESCE(e.renglones, 0) AS renglones,
                       COALESCE(e.piezas, 0) AS piezas,
                       COALESCE(e.negativos, 0) AS negativos,
                       COALESCE(e.valor, 0) AS valor
                  FROM almacenes a
                  LEFT JOIN usuarios u ON u.id = a.responsable_id
                  LEFT JOIN LATERAL (
                        SELECT count(*) FILTER (WHERE x.cantidad <> 0) AS renglones,
                               sum(x.cantidad) AS piezas,
                               count(*) FILTER (WHERE x.cantidad < 0) AS negativos,
                               -- Lo que vale, a precio de venta (lo negativo no resta).
                               sum(round(x.cantidad * pv.precio, 2))
                                 FILTER (WHERE x.cantidad > 0) AS valor
                          FROM existencias x
                          {precio_de_venta("x.producto_id")}
                         WHERE x.almacen_id = a.id
                  ) e ON true
                 WHERE a.activo
                 ORDER BY a.tipo, a.codigo
                """  # noqa: S608 — fragmento constante del código
            )
        )
    ).mappings().all()

    if not almacenes:
        return render(
            peticion,
            "inventario.html",
            {
                "almacenes": [],
                "elegido": None,
                "filas": [],
                "q": "",
                "filtro": filtro,
                "total_piezas": Decimal(0),
                "total_valor": Decimal(0),
                "sin_precio": 0,
            },
            actor=actor,
            seccion="Inventario",
        )

    # El elegido, o la primera bodega: es donde está casi todo el inventario y la
    # pregunta más frecuente ("¿tengo para cargar mañana?") es sobre ella.
    elegido = None
    if almacen:
        try:
            buscado = uuid.UUID(almacen)
            elegido = next((a for a in almacenes if a["id"] == buscado), None)
        except ValueError:
            elegido = None
    if elegido is None:
        elegido = next((a for a in almacenes if a["tipo"] == "bodega"), almacenes[0])

    condiciones = ["e.almacen_id = :a"]
    parametros: dict[str, object] = {"a": elegido["id"]}

    if filtro == "con_existencia":
        condiciones.append("e.cantidad <> 0")
    elif filtro == "negativos":
        condiciones.append("e.cantidad < 0")

    busqueda = q.strip()
    if busqueda:
        condiciones.append("(p.nombre ILIKE :q OR p.sku ILIKE :q OR p.codigo_barras = :exacto)")
        parametros["q"] = f"%{busqueda}%"
        parametros["exacto"] = busqueda

    filas = (
        await sesion.execute(
            text(
                f"""
                SELECT p.id, p.sku, p.nombre, p.unidad_base, p.activo,
                       e.cantidad, e.actualizado_en,
                       pu.presentaciones,
                       pv.precio, round(e.cantidad * pv.precio, 2) AS valor,
                       -- La suma del libro mayor para este almacén y producto.
                       -- Si no cuadra con la caché, alguna transacción escribió
                       -- una y no la otra: es un bug, no un dato.
                       COALESCE(m.entradas, 0) - COALESCE(m.salidas, 0) AS segun_libro
                  FROM existencias e
                  JOIN productos p ON p.id = e.producto_id
                  LEFT JOIN LATERAL (
                        SELECT json_agg(json_build_object(
                                 'unidad', u.unidad_codigo, 'factor', u.factor::text)
                               ORDER BY u.factor DESC) AS presentaciones
                          FROM producto_unidades u
                         WHERE u.producto_id = p.id AND u.activo
                  ) pu ON true
                  LEFT JOIN LATERAL (
                        SELECT
                          sum(cantidad) FILTER (WHERE almacen_destino_id = e.almacen_id)
                            AS entradas,
                          sum(cantidad) FILTER (WHERE almacen_origen_id = e.almacen_id)
                            AS salidas
                          FROM movimientos_inventario
                         WHERE producto_id = e.producto_id
                           AND (almacen_origen_id = e.almacen_id
                                OR almacen_destino_id = e.almacen_id)
                  ) m ON true
                  {precio_de_venta("p.id")}
                 WHERE {" AND ".join(condiciones)}
                 ORDER BY p.nombre
                 LIMIT 500
                """  # noqa: S608 — las condiciones son constantes del código
            ),
            parametros,
        )
    ).mappings().all()

    descuadres = [
        f for f in filas if Decimal(f["cantidad"]) != Decimal(f["segun_libro"])
    ]

    return render(
        peticion,
        "inventario.html",
        {
            "almacenes": almacenes,
            "elegido": elegido,
            "filas": filas,
            "descuadres": descuadres,
            "q": busqueda,
            "filtro": filtro,
            "total_piezas": sum((Decimal(f["cantidad"]) for f in filas), Decimal(0)),
            # El total de lo que se ve, a precio de venta, como el de la tarjeta del
            # almacén: lo negativo no suma. Lo que no tiene precio tampoco, y se
            # dice cuántos son: un total que parece completo y no lo está es peor
            # que uno que avisa.
            "total_valor": sum(
                (Decimal(f["valor"]) for f in filas
                 if f["valor"] is not None and f["cantidad"] > 0),
                Decimal(0),
            ),
            "sin_precio": sum(1 for f in filas if f["precio"] is None),
            "puede_ajustar": (
                elegido["tipo"] in TIPOS_AJUSTABLES
                and actor.puede("inventario.ajustar")
            ),
        },
        actor=actor,
        seccion="Inventario",
    )


@router.get("/{almacen_id}/{producto_id}", response_class=HTMLResponse)
async def movimientos(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    almacen_id: uuid.UUID,
    producto_id: uuid.UUID,
    error: str = "",
    guardado: str = "",
) -> HTMLResponse:
    """El libro mayor de un producto en un almacén, con su saldo corriente.

    La pregunta que sigue a un número raro es siempre "¿cómo llegó ahí?". Sin esta
    pantalla la respuesta sale de psql, y entonces nadie la hace.

    El saldo corriente se calcula en orden cronológico: así se ve **en qué
    movimiento** el inventario se fue a negativo, que es distinto de saber que hoy
    está negativo.
    """
    actor.exigir("inventario.ver")

    cabecera = (
        await sesion.execute(
            text(
                "SELECT p.sku, p.nombre, p.unidad_base, a.nombre AS almacen, "
                "       a.codigo AS almacen_codigo, a.id AS almacen_id, "
                "       a.tipo AS almacen_tipo, r.nombre AS responsable, "
                "       COALESCE(e.cantidad, 0) AS cantidad "
                "  FROM productos p "
                "  CROSS JOIN almacenes a "
                "  LEFT JOIN usuarios r ON r.id = a.responsable_id "
                "  LEFT JOIN existencias e ON e.producto_id = p.id AND e.almacen_id = a.id "
                " WHERE p.id = :p AND a.id = :a"
            ),
            {"p": producto_id, "a": almacen_id},
        )
    ).mappings().first()
    if cabecera is None:
        return RedirectResponse("/panel/inventario", status_code=303)

    crudos = (
        await sesion.execute(
            text(
                """
                SELECT m.id, m.tipo, m.cantidad, m.lote, m.fecha_servidor,
                       m.documento_tipo, m.documento_id,
                       m.almacen_origen_id, m.almacen_destino_id,
                       o.codigo AS origen, d.codigo AS destino,
                       u.nombre AS quien
                  FROM movimientos_inventario m
                  LEFT JOIN almacenes o ON o.id = m.almacen_origen_id
                  LEFT JOIN almacenes d ON d.id = m.almacen_destino_id
                  LEFT JOIN usuarios u ON u.id = m.usuario_id
                 WHERE m.producto_id = :p
                   AND (m.almacen_origen_id = :a OR m.almacen_destino_id = :a)
                 ORDER BY m.fecha_servidor, m.id
                """
            ),
            {"p": producto_id, "a": almacen_id},
        )
    ).mappings().all()

    saldo = Decimal(0)
    renglones = []
    for m in crudos:
        entra = m["almacen_destino_id"] == almacen_id
        delta = Decimal(m["cantidad"]) if entra else -Decimal(m["cantidad"])
        saldo += delta
        renglones.append({**dict(m), "entra": entra, "delta": delta, "saldo": saldo})

    # Lo más reciente arriba para leer, pero el saldo se calculó en orden.
    renglones.reverse()

    # Los ajustes que la oficina ya hizo sobre este camión y este producto: se
    # muestran con su nota porque son el renglón que alguien va a cuestionar.
    ajustes = (
        await sesion.execute(
            text(
                "SELECT c.folio, c.tipo, c.delta, c.contado, c.nota, c.creado_en, "
                "       u.nombre AS quien, m.nombre AS motivo "
                "  FROM ajustes_camion c "
                "  JOIN usuarios u ON u.id = c.usuario_id "
                "  LEFT JOIN motivos_merma m ON m.codigo = c.motivo_codigo "
                " WHERE c.almacen_id = :a AND c.producto_id = :p "
                " ORDER BY c.creado_en DESC LIMIT 20"
            ),
            {"a": almacen_id, "p": producto_id},
        )
    ).mappings().all()

    return render(
        peticion,
        "inventario_movimientos.html",
        {
            "cabecera": cabecera,
            "producto_id": producto_id,
            "renglones": renglones,
            "segun_libro": saldo,
            "cuadra": saldo == Decimal(cabecera["cantidad"]),
            "ajustes": ajustes,
            "motivos": (
                await sesion.execute(
                    text(
                        "SELECT codigo, nombre FROM motivos_merma "
                        " WHERE activo ORDER BY nombre"
                    )
                )
            ).mappings().all(),
            # Camiones y bodegas (desde la 0037). Tránsito y merma no: lo que está
            # en tránsito se resuelve recibiendo la devolución, y el almacén de
            # merma es un destino, no algo que se cuente.
            "puede_ajustar": (
                cabecera["almacen_tipo"] in TIPOS_AJUSTABLES
                and actor.puede("inventario.ajustar")
            ),
            "es_camion": cabecera["almacen_tipo"] == "camion",
            "error": error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Inventario",
    )


# ---------------------------------------------------------------------------
# Ajustar el inventario de un camión
# ---------------------------------------------------------------------------
# Excepción documentada a §0.2. El razonamiento completo está en el encabezado de
# la migración 0032; lo que importa aquí es lo que la pantalla impide:
#
#   · camiones y bodegas. El de una bodega no viaja a ningún teléfono, y eso lo
#     garantiza el disparador (migración 0037), no esta pantalla: publicaba con el
#     responsable del almacén, una bodega no tiene, y un `vendedor_id` nulo en el
#     pull significa «a todos los teléfonos» — cada vendedor habría sumado el
#     ajuste de la bodega a su camión;
#   · el ajuste no puede EMPUJAR el camión a negativo. Que un camión esté negativo
#     es legítimo (§0.1: una venta offline entró con el conteo en cero), pero eso
#     es un hecho que llegó tarde. Esto es alguien capturando ahora, y a lo que se
#     captura se le revisa (doctrina de la 0026);
#   · la nota es obligatoria: esto cambia el inventario del camión de una persona
#     que va a tener que explicarlo en su liquidación.


@router.post("/{almacen_id}/{producto_id}/ajustar")
async def ajustar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    almacen_id: uuid.UUID,
    producto_id: uuid.UUID,
    tipo: Annotated[str, Form()] = "conteo",
    contado: Annotated[str, Form()] = "",
    cantidad: Annotated[str, Form()] = "",
    motivo_codigo: Annotated[str, Form()] = "",
    nota: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Corrige el inventario de un camión o de una bodega, con documento.

    El de un camión viaja al teléfono de su dueño; el de una bodega no viaja a
    ninguno (migración 0037).
    """
    actor.exigir("inventario.ajustar")
    exigir_csrf(peticion, csrf)

    volver = f"/panel/inventario/{almacen_id}/{producto_id}"

    if tipo not in ("conteo", "merma", "entrada"):
        return _a(volver, error="Elige qué clase de ajuste es.")

    limpia = (nota or "").strip()
    if len(limpia) < 10:
        return _a(
            volver,
            error="Escribe la nota del ajuste, con al menos 10 caracteres: es lo "
            "único que va a explicar, meses después, por qué el inventario cambió "
            "sin una venta ni una carga.",
        )

    cabecera = (
        await sesion.execute(
            text(
                "SELECT a.tipo, a.nombre, u.nombre AS responsable "
                "  FROM almacenes a "
                "  LEFT JOIN usuarios u ON u.id = a.responsable_id "
                " WHERE a.id = :a AND a.activo"
            ),
            {"a": almacen_id},
        )
    ).mappings().first()
    if cabecera is None:
        return RedirectResponse("/panel/inventario", status_code=303)
    if cabecera["tipo"] not in TIPOS_AJUSTABLES:
        return _a(
            volver,
            error=f"«{cabecera['nombre']}» es un almacén de {cabecera['tipo']}: no "
            "se ajusta a mano. Lo que está en tránsito se resuelve recibiendo la "
            "devolución en Entradas.",
        )
    es_camion = cabecera["tipo"] == "camion"

    # La existencia se lee DENTRO de la transacción y con candado: entre leerla para
    # pintar la pantalla y guardar el ajuste pudo entrar una venta del teléfono, y
    # el conteo se calcularía contra un número que ya no existe.
    actual = Decimal(
        (
            await sesion.execute(
                text(
                    "SELECT COALESCE(("
                    "  SELECT cantidad FROM existencias "
                    "   WHERE almacen_id = :a AND producto_id = :p FOR UPDATE"
                    "), 0)"
                ),
                {"a": almacen_id, "p": producto_id},
            )
        ).scalar_one()
    )

    try:
        if tipo == "conteo":
            cuenta = _leer_cantidad(contado, que_es="lo que contaste")
            delta = cuenta - actual
            if delta == 0:
                return _a(
                    volver,
                    error=f"El sistema ya dice {actual}: no hay nada que ajustar.",
                )
        else:
            cuanto = _leer_cantidad(cantidad, que_es="la cantidad")
            if cuanto == 0:
                return _a(volver, error="La cantidad no puede ser cero.")
            cuenta = None
            delta = -cuanto if tipo == "merma" else cuanto
    except CapturaInvalida as e:
        return _a(volver, error=str(e))

    if tipo == "merma" and not motivo_codigo:
        return _a(volver, error="Una merma necesita su motivo.")

    if delta < 0 and actual + delta < 0:
        return _a(
            volver,
            error=f"{'El camión' if es_camion else 'La bodega'} tiene {actual} y "
            f"eso lo dejaría en {actual + delta}. Un almacén sí puede quedar "
            "negativo —una venta que entra tarde con el conteo en cero—, pero eso "
            "es un hecho que llegó solo; un ajuste capturado a mano que lo empuja a "
            "negativo es un dedazo.",
        )

    ahora = datetime.now(UTC)
    ajuste_id = uuid.uuid4()
    consecutivo = (
        await sesion.execute(text("SELECT nextval('seq_folio_ajuste_camion')"))
    ).scalar_one()

    await sesion.execute(
        text(
            """
            INSERT INTO ajustes_camion
              (id, folio, almacen_id, producto_id, tipo, existencia_al_capturar,
               contado, delta, motivo_codigo, nota, usuario_id, creado_en)
            VALUES (:id, :folio, :a, :p, :tipo, :actual, :contado, :delta,
                    :motivo, :nota, :quien, :ahora)
            """
        ),
        {
            "id": ajuste_id,
            # AC para camión, AB para bodega: el folio dice de dónde es sin abrir
            # el documento. La serie es una sola.
            "folio": f"{'AC' if es_camion else 'AB'}-{consecutivo:06d}",
            "a": almacen_id,
            "p": producto_id,
            "tipo": tipo,
            "actual": actual,
            "contado": cuenta,
            "delta": delta,
            "motivo": motivo_codigo or None,
            "nota": limpia[:600],
            "quien": actor.usuario_id,
            "ahora": ahora,
        },
    )

    # El asiento en el libro mayor y la caché, juntos y siempre: si divergieran, la
    # propia pantalla de inventario marcaría un descuadre que no corresponde a nada
    # físico.
    #
    # El tipo del asiento es `ajuste` para los tres casos. Por qué se ajustó vive en
    # el documento, que es donde se puede leer con su nota; un `merma` en el libro
    # mayor del camión no entraría de todos modos en la columna de merma de la
    # liquidación, que se calcula desde los documentos de merma del vendedor.
    sube = delta > 0
    await sesion.execute(
        text(
            """
            INSERT INTO movimientos_inventario
              (tipo, almacen_origen_id, almacen_destino_id, producto_id, cantidad,
               documento_tipo, documento_id, usuario_id, fecha_servidor)
            VALUES ('ajuste', :origen, :destino, :p, :cant, 'ajuste_camion', :doc,
                    :quien, :ahora)
            """
        ),
        {
            "origen": None if sube else almacen_id,
            "destino": almacen_id if sube else None,
            "p": producto_id,
            "cant": abs(delta),
            "doc": ajuste_id,
            "quien": actor.usuario_id,
            "ahora": ahora,
        },
    )
    await sesion.execute(
        text(
            """
            INSERT INTO existencias (almacen_id, producto_id, cantidad, actualizado_en)
            VALUES (:a, :p, :cant, :ahora)
            ON CONFLICT (almacen_id, producto_id) DO UPDATE
               SET cantidad = existencias.cantidad + :cant, actualizado_en = :ahora
            """
        ),
        {"a": almacen_id, "p": producto_id, "cant": delta, "ahora": ahora},
    )
    await sesion.commit()

    signo = "+" if sube else "−"
    return _a(
        volver,
        guardado=(
            f"Ajuste AC-{consecutivo:06d}: {signo}{abs(delta)} en "
            f"{cabecera['nombre']}. Ahora dice {actual + delta}. "
            "El teléfono lo recibe en la siguiente sincronización."
        ),
    )


def _leer_cantidad(texto: str | None, *, que_es: str) -> Decimal:
    """Una cantidad en unidad base. Admite fracción por si entra una báscula.

    Es la cuarta copia de este lector en el panel —`cargas.py`, `entradas.py` y
    `salidas.py` tienen la suya, con sus mensajes—. Consolidarlas en `comun.py` es
    un refactor que toca cuatro pantallas probadas y no se hace a media función
    nueva; queda anotado aquí como en las otras.
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
        raise CapturaInvalida(f"{que_es.capitalize()} no puede ser negativo.")
    if valor > Decimal("100000"):
        raise CapturaInvalida(f"«{texto}» no cabe en un camión. Revisa el número.")
    return valor.quantize(Decimal("0.001"))


def _a(destino: str, *, error: str = "", guardado: str = "") -> RedirectResponse:
    cola = []
    if error:
        cola.append(f"error={quote(error)}")
    if guardado:
        cola.append(f"guardado={quote(guardado)}")
    return RedirectResponse(
        destino + (f"?{'&'.join(cola)}" if cola else ""),
        status_code=status.HTTP_303_SEE_OTHER,
    )
