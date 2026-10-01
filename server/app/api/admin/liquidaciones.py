"""Fase 7 · Liquidación y retorno: el cierre del día.

────────────────────────────────────────────────────────────────────────────
AQUÍ SE ATRAPAN LOS DESCUADRES, Y ES LA ÚNICA PANTALLA QUE LOS ATRAPA
────────────────────────────────────────────────────────────────────────────
    esperado   = cargado − vendido − merma + devuelto
    diferencia = retornado − esperado

`retornado` es lo que se **cuenta físicamente** al bajar el camión. Todo lo demás
lo calcula el sistema de sus propios documentos. La diferencia es lo único que
importa del cierre, y tiene dos lecturas:

    faltante (negativo)  salió mercancía sin documento. Se le cobra al vendedor.
    sobrante (positivo)  viene más de lo que el sistema sabe. Casi siempre es una
                         venta que el teléfono no ha sincronizado todavía.

────────────────────────────────────────────────────────────────────────────
POR QUÉ NO SE PUEDE CERRAR CON OPERACIONES PENDIENTES
────────────────────────────────────────────────────────────────────────────
Esa segunda lectura es la razón. Una venta que entra **después** del cierre
convierte un sobrante en un cuadre, y el cierre ya dijo por escrito que había
sobrante — con el nombre del vendedor y su firma. Es §2.3 del documento de
arquitectura, y es la regla que más tienta a saltarse cuando el vendedor tiene
prisa.

Lo que el servidor **puede** comprobar hoy:

  · sobres en cuarentena de ese equipo. Cada uno es una operación que NO entró, y
    puede ser justo la venta que explica el sobrante. **Bloquea.**
  · cuándo fue el último push del equipo. Si fue antes de la última venta
    registrada, seguro falta algo. Se muestra siempre y **bloquea** si el equipo
    no ha sincronizado después de confirmarse la carga.

  · **cuántas operaciones le quedan en la bandeja al teléfono.** Esto solo lo sabe
    el teléfono, y ahora lo reporta en cada push (`dispositivos.cola_pendiente`,
    migración 0019). Si dice que le quedan, **bloquea**: cada sobre pendiente puede
    ser la venta que explica el sobrante que el cierre está por declarar.

Y con eso `sync_completa` dejó de ser una casilla. Se escribe `true` solo cuando
**todos** los equipos activos del vendedor reportaron cero pendientes **después**
del día de la carga; un cero de anteayer no dice nada sobre hoy (§0.3). Cuando no
hay ese respaldo —un equipo con app vieja, por ejemplo— se sigue pidiendo la
confirmación de la persona, porque cerrar el día no puede quedar bloqueado por una
actualización pendiente, pero la columna queda en `false`: al revisar un cierre con
sobrante, lo primero que se pregunta es si el equipo estaba al día, y un `true` sin
respaldo contesta esa pregunta con una afirmación disfrazada de hecho.

────────────────────────────────────────────────────────────────────────────
QUÉ PASA AL CERRAR
────────────────────────────────────────────────────────────────────────────
En una transacción:

1. **`retorno`** camión → bodega por lo que se contó. Es el movimiento físico.
2. **`ajuste`** por lo que sobra o falta, para que el camión quede EXACTAMENTE en
   cero. Sin esto el camión arrastra un saldo fantasma para siempre, y el faltante
   de hoy contamina el cierre de mañana.
3. La carga pasa a **`liquidada`**, y ese UPDATE publica el delta que **vacía
   `existencias_camion` en el teléfono** (migración 0015 + el aplicador de Dart).
4. La liquidación queda `cuadrada` o `con_diferencia`, según la ecuación.

El paso 2 es el que se olvida, y es el que hace que el inventario del camión
signifique algo al día siguiente.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.api.admin.comun import (
    CapturaInvalida,
    SesionDep,
    dinero,
    leer_dinero,
    render,
    texto_o_nulo,
)
from app.api.admin.sesion_web import ActorWeb, exigir_csrf
from app.domain.liquidacion import RenglonDeLiquidacion, diferencia_de_efectivo
from app.workers.cola import encolar

router = APIRouter(
    prefix="/panel/liquidaciones", tags=["panel"], include_in_schema=False
)

PERMISO = "inventario.liquidar"


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
    """Lo que falta cerrar, y lo que ya se cerró.

    Las cargas sin liquidar van primero y con su antigüedad: una carga de hace tres
    días sin cerrar significa que nadie contó ese camión, y cada día que pasa el
    conteo vale menos.
    """
    actor.exigir("inventario.ver")

    pendientes = (
        await sesion.execute(
            text(
                """
                SELECT c.id, c.folio, c.fecha_operativa, c.estado,
                       u.nombre AS vendedor, u.codigo AS vendedor_codigo,
                       a.nombre AS camion,
                       CURRENT_DATE - c.fecha_operativa AS dias,
                       COALESCE(v.ventas, 0) AS ventas,
                       COALESCE(v.importe, 0) AS importe
                  FROM cargas c
                  JOIN usuarios u ON u.id = c.vendedor_id
                  JOIN almacenes a ON a.id = c.almacen_destino_id
                  LEFT JOIN LATERAL (
                        SELECT count(*) AS ventas, sum(total) AS importe
                          FROM ventas
                         WHERE carga_id = c.id AND estado = 'confirmada'
                  ) v ON true
                 WHERE c.estado IN ('confirmada', 'en_ruta')
                   AND NOT EXISTS (SELECT 1 FROM liquidaciones l WHERE l.carga_id = c.id)
                 ORDER BY c.fecha_operativa
                """
            )
        )
    ).mappings().all()

    liquidaciones = (
        await sesion.execute(
            text(
                """
                SELECT l.id, l.folio, l.estado, l.fecha_operativa,
                       l.efectivo_esperado, l.efectivo_entregado,
                       l.diferencia_efectivo, l.cerrada_en, l.sync_completa,
                       u.nombre AS vendedor, u.codigo AS vendedor_codigo,
                       c.folio AS carga,
                       COALESCE(d.faltantes, 0) AS faltantes
                  FROM liquidaciones l
                  JOIN usuarios u ON u.id = l.vendedor_id
                  JOIN cargas c ON c.id = l.carga_id
                  LEFT JOIN LATERAL (
                        SELECT count(*) AS faltantes FROM liquidacion_detalle
                         WHERE liquidacion_id = l.id AND diferencia <> 0
                  ) d ON true
                 ORDER BY l.fecha_operativa DESC, l.folio DESC
                 LIMIT 100
                """
            )
        )
    ).mappings().all()

    return render(
        peticion,
        "liquidaciones.html",
        {
            "pendientes": pendientes,
            "liquidaciones": liquidaciones,
            "puede_editar": actor.puede(PERMISO),
            "error": error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Liquidación",
    )


# ---------------------------------------------------------------------------
# Abrir
# ---------------------------------------------------------------------------


@router.post("/abrir")
async def abrir(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    carga_id: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Abre la liquidación con las cantidades que el sistema ya conoce.

    `cant_retornada` nace en **cero**, no en "lo esperado". Prellenarla con el
    esperado haría que cerrar sin contar diera cuadre perfecto, y entonces el
    cierre no significaría nada: sería un botón que dice que todo está bien.

    Contar el camión es el único dato que esta pantalla no puede calcular, y es
    justamente el que le da sentido a los demás.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    try:
        carga = uuid.UUID(carga_id)
    except ValueError:
        return _a_lista(error="Falta la carga.")

    cabecera = (
        await sesion.execute(
            text(
                "SELECT id, folio, vendedor_id, fecha_operativa, estado "
                "  FROM cargas WHERE id = :c FOR UPDATE"
            ),
            {"c": carga},
        )
    ).mappings().first()
    if cabecera is None:
        return _a_lista(error="Esa carga no existe.")
    if cabecera["estado"] not in ("confirmada", "en_ruta"):
        return _a_lista(error=f"Esa carga está {cabecera['estado']}: no hay qué liquidar.")

    ya = (
        await sesion.execute(
            text("SELECT id FROM liquidaciones WHERE carga_id = :c"), {"c": carga}
        )
    ).scalar_one_or_none()
    if ya is not None:
        return RedirectResponse(
            f"/panel/liquidaciones/{ya}", status_code=status.HTTP_303_SEE_OTHER
        )

    renglones = await _renglones_calculados(sesion, carga)
    if not renglones:
        return _a_lista(error="Esa carga no tiene renglones: no hay qué liquidar.")

    liquidacion_id = uuid.uuid4()
    consecutivo = (
        await sesion.execute(text("SELECT nextval('seq_folio_liquidacion')"))
    ).scalar_one()

    esperado = await _efectivo_esperado(sesion, cabecera["vendedor_id"],
                                        cabecera["fecha_operativa"])

    await sesion.execute(
        text(
            """
            INSERT INTO liquidaciones (id, folio, carga_id, vendedor_id,
                                       fecha_operativa, efectivo_esperado)
            VALUES (:id, :folio, :c, :v, :d, :efectivo)
            """
        ),
        {
            "id": liquidacion_id,
            "folio": f"LQ-{consecutivo:06d}",
            "c": carga,
            "v": cabecera["vendedor_id"],
            "d": cabecera["fecha_operativa"],
            "efectivo": esperado,
        },
    )
    for r in renglones:
        await sesion.execute(
            text(
                """
                INSERT INTO liquidacion_detalle
                  (id, liquidacion_id, producto_id, cant_cargada, cant_vendida,
                   cant_merma, cant_devuelta, cant_retornada)
                VALUES (:id, :l, :p, :cargada, :vendida, :merma, :devuelta, 0)
                """
            ),
            {
                "id": uuid.uuid4(),
                "l": liquidacion_id,
                "p": r["producto_id"],
                "cargada": r["cargada"],
                "vendida": r["vendida"],
                "merma": r["merma"],
                "devuelta": r["devuelta"],
            },
        )

    # La carga pasa a 'en_ruta' si todavía estaba en 'confirmada': el camión ya
    # salió y volvió. Es un estado que el teléfono ya sabe aplicar sin vaciar nada.
    if cabecera["estado"] == "confirmada":
        await sesion.execute(
            text("UPDATE cargas SET estado = 'en_ruta' WHERE id = :c"), {"c": carga}
        )

    await sesion.commit()
    return RedirectResponse(
        f"/panel/liquidaciones/{liquidacion_id}", status_code=status.HTTP_303_SEE_OTHER
    )


# ---------------------------------------------------------------------------
# Detalle
# ---------------------------------------------------------------------------


@router.get("/{liquidacion_id}", response_class=HTMLResponse)
async def detalle(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    liquidacion_id: uuid.UUID,
    error: str = "",
    guardado: str = "",
) -> HTMLResponse:
    actor.exigir("inventario.ver")

    cabecera = (
        await sesion.execute(
            text(
                "SELECT l.*, u.nombre AS vendedor, u.codigo AS vendedor_codigo, "
                "       c.folio AS carga_folio, c.estado AS carga_estado, "
                "       c.almacen_origen_id, c.almacen_destino_id, "
                "       a.nombre AS camion, o.nombre AS bodega, "
                "       q.nombre AS cerrada_por_nombre "
                "  FROM liquidaciones l "
                "  JOIN usuarios u ON u.id = l.vendedor_id "
                "  JOIN cargas c ON c.id = l.carga_id "
                "  JOIN almacenes a ON a.id = c.almacen_destino_id "
                "  JOIN almacenes o ON o.id = c.almacen_origen_id "
                "  LEFT JOIN usuarios q ON q.id = l.cerrada_por "
                " WHERE l.id = :id"
            ),
            {"id": liquidacion_id},
        )
    ).mappings().first()
    if cabecera is None:
        return RedirectResponse("/panel/liquidaciones", status_code=303)

    crudos = (
        await sesion.execute(
            text(
                """
                SELECT d.id, d.producto_id, d.cant_cargada, d.cant_vendida,
                       d.cant_merma, d.cant_devuelta, d.cant_retornada, d.diferencia,
                       p.sku, p.nombre, p.unidad_base,
                       pu.presentaciones
                  FROM liquidacion_detalle d
                  JOIN productos p ON p.id = d.producto_id
                  LEFT JOIN LATERAL (
                        SELECT json_agg(json_build_object(
                                 'unidad', u.unidad_codigo, 'factor', u.factor::text)
                               ORDER BY u.factor DESC) AS presentaciones
                          FROM producto_unidades u
                         WHERE u.producto_id = d.producto_id AND u.activo
                  ) pu ON true
                 WHERE d.liquidacion_id = :l
                 ORDER BY p.nombre
                """
            ),
            {"l": liquidacion_id},
        )
    ).mappings().all()

    # El esperado se calcula con el módulo de dominio, y se compara con la columna
    # generada de PostgreSQL. Si divergieran, el vendedor y la oficina estarían
    # discutiendo sobre dos números distintos.
    renglones = []
    for d in crudos:
        r = RenglonDeLiquidacion(
            cargado=Decimal(d["cant_cargada"]),
            vendido=Decimal(d["cant_vendida"]),
            merma=Decimal(d["cant_merma"]),
            devuelto=Decimal(d["cant_devuelta"]),
            retornado=Decimal(d["cant_retornada"]),
        )
        renglones.append(
            {
                **dict(d),
                "esperado": r.esperado,
                "calculada": r.diferencia,
                "coincide_con_la_base": r.diferencia == Decimal(d["diferencia"]),
            }
        )

    descuadres = [r for r in renglones if r["calculada"] != 0]
    divergencias = [r for r in renglones if not r["coincide_con_la_base"]]

    bloqueos = await _bloqueos_para_cerrar(sesion, cabecera)
    respaldo = await _respaldo_de_sincronizacion(sesion, cabecera)

    return render(
        peticion,
        "liquidacion_detalle.html",
        {
            "c": cabecera,
            "respaldo": respaldo,
            "renglones": renglones,
            "descuadres": descuadres,
            "divergencias": divergencias,
            "bloqueos": bloqueos,
            "abierta": cabecera["estado"] != "cerrada",
            "puede_editar": actor.puede(PERMISO),
            "esperado_texto": dinero(cabecera["efectivo_esperado"]),
            "entregado_texto": dinero(cabecera["efectivo_entregado"]),
            "diferencia_efectivo": diferencia_de_efectivo(
                Decimal(cabecera["efectivo_entregado"]),
                Decimal(cabecera["efectivo_esperado"]),
            ),
            "error": error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Liquidación",
    )


@router.post("/{liquidacion_id}/contar")
async def contar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    liquidacion_id: uuid.UUID,
):
    """Captura el conteo físico del camión.

    Los campos vienen como `retornada_<id del renglón>`, así que se leen del
    formulario crudo. Un campo **vacío significa cero**, no "no lo cambies": al
    contar un camión, el producto que no se anotó es el que no venía. Si significara
    "no lo toques", un producto que se terminó quedaría con el conteo de un intento
    anterior y el faltante desaparecería sin que nadie lo decidiera.
    """
    actor.exigir(PERMISO)
    formulario = await peticion.form()
    exigir_csrf(peticion, str(formulario.get("csrf") or ""))

    estado = (
        await sesion.execute(
            text("SELECT estado FROM liquidaciones WHERE id = :l"), {"l": liquidacion_id}
        )
    ).scalar_one_or_none()
    if estado is None:
        return RedirectResponse("/panel/liquidaciones", status_code=303)
    if estado == "cerrada":
        return _volver(liquidacion_id, error="Esta liquidación ya está cerrada.")

    renglones = (
        await sesion.execute(
            text("SELECT id FROM liquidacion_detalle WHERE liquidacion_id = :l"),
            {"l": liquidacion_id},
        )
    ).scalars().all()

    for renglon in renglones:
        crudo = formulario.get(f"retornada_{renglon}")
        try:
            # Se lee con el lector de cantidades de tres decimales, no con `int`:
            # la columna es numeric(14,3) y un día puede haber un producto a granel.
            contado = _leer_cantidad(str(crudo) if crudo is not None else "")
        except CapturaInvalida as e:
            return _volver(liquidacion_id, error=str(e))

        await sesion.execute(
            text("UPDATE liquidacion_detalle SET cant_retornada = :r WHERE id = :id"),
            {"r": contado, "id": renglon},
        )

    await sesion.commit()
    return _volver(liquidacion_id, guardado="Conteo guardado.")


@router.post("/{liquidacion_id}/efectivo")
async def arqueo(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    liquidacion_id: uuid.UUID,
    efectivo_entregado: Annotated[str, Form()] = "",
    observaciones: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """El arqueo: lo que trae en la bolsa contra lo que el sistema esperaba.

    `efectivo_esperado` se recalcula aquí a propósito, en vez de usar el que se
    guardó al abrir: entre abrir y cerrar pueden haber entrado ventas de contado y
    cobros que el teléfono sincronizó tarde. Usar el número viejo haría aparecer un
    faltante de efectivo del tamaño exacto de lo que llegó en medio.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    cabecera = (
        await sesion.execute(
            text(
                "SELECT estado, vendedor_id, fecha_operativa "
                "  FROM liquidaciones WHERE id = :l"
            ),
            {"l": liquidacion_id},
        )
    ).mappings().first()
    if cabecera is None:
        return RedirectResponse("/panel/liquidaciones", status_code=303)
    if cabecera["estado"] == "cerrada":
        return _volver(liquidacion_id, error="Esta liquidación ya está cerrada.")

    try:
        entregado = leer_dinero(efectivo_entregado, campo="El efectivo entregado")
    except CapturaInvalida as e:
        return _volver(liquidacion_id, error=str(e))

    esperado = await _efectivo_esperado(
        sesion, cabecera["vendedor_id"], cabecera["fecha_operativa"]
    )

    await sesion.execute(
        text(
            "UPDATE liquidaciones "
            "   SET efectivo_entregado = :e, efectivo_esperado = :esp, "
            "       observaciones = :obs "
            " WHERE id = :l"
        ),
        {
            "e": entregado,
            "esp": esperado,
            "obs": texto_o_nulo(observaciones, maximo=600),
            "l": liquidacion_id,
        },
    )
    await sesion.commit()

    falta = diferencia_de_efectivo(entregado, esperado)
    if falta == 0:
        aviso = f"Arqueo cuadrado: {dinero(entregado)}."
    elif falta < 0:
        aviso = (
            f"Faltan {dinero(-falta)}: esperado {dinero(esperado)}, "
            f"entregado {dinero(entregado)}."
        )
    else:
        aviso = (
            f"Sobran {dinero(falta)}. Suele ser un cobro que el teléfono todavía "
            "no sincronizó."
        )
    return _volver(liquidacion_id, guardado=aviso)


# ---------------------------------------------------------------------------
# Cerrar
# ---------------------------------------------------------------------------


@router.post("/{liquidacion_id}/cerrar")
async def cerrar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    liquidacion_id: uuid.UUID,
    confirmo_sincronizado: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Cierra el día: retorno, ajuste, carga liquidada.

    Todo en una transacción, y en este orden, porque el ajuste se calcula **sobre
    la existencia que deja el retorno**: hacerlo antes daría un número que no
    corresponde a nada.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    cabecera = (
        await sesion.execute(
            text(
                "SELECT l.*, c.almacen_origen_id, c.almacen_destino_id, c.folio AS carga_folio "
                "  FROM liquidaciones l JOIN cargas c ON c.id = l.carga_id "
                " WHERE l.id = :l FOR UPDATE OF l"
            ),
            {"l": liquidacion_id},
        )
    ).mappings().first()
    if cabecera is None:
        return RedirectResponse("/panel/liquidaciones", status_code=303)
    if cabecera["estado"] == "cerrada":
        return _volver(liquidacion_id, error="Ya estaba cerrada.")

    bloqueos = await _bloqueos_para_cerrar(sesion, cabecera)
    if bloqueos:
        return _volver(
            liquidacion_id,
            error="No se puede cerrar: " + " ".join(bloqueos),
        )
    respaldo = await _respaldo_de_sincronizacion(sesion, cabecera)
    if not respaldo["respaldado"] and not confirmo_sincronizado:
        return _volver(
            liquidacion_id,
            error="Confirma que el teléfono terminó de sincronizar: "
            f"{respaldo['motivo']}. Una venta que entre después del cierre convierte "
            "un sobrante en un cuadre, y el cierre ya dijo lo contrario por escrito.",
        )

    renglones = (
        await sesion.execute(
            text(
                "SELECT producto_id, cant_retornada, diferencia "
                "  FROM liquidacion_detalle WHERE liquidacion_id = :l"
            ),
            {"l": liquidacion_id},
        )
    ).mappings().all()

    ahora = datetime.now(UTC)
    camion = cabecera["almacen_destino_id"]
    bodega = cabecera["almacen_origen_id"]

    for r in renglones:
        retornada = Decimal(r["cant_retornada"])
        if retornada > 0:
            await _mover(
                sesion,
                tipo="retorno",
                origen=camion,
                destino=bodega,
                producto=r["producto_id"],
                cantidad=retornada,
                documento=liquidacion_id,
                quien=actor.usuario_id,
                ahora=ahora,
            )

    # ------------------------------------------------------------------
    # El ajuste que deja el camión EXACTAMENTE en cero.
    # ------------------------------------------------------------------
    # Es el paso que se olvida. Sin él el camión arrastra un saldo fantasma para
    # siempre: el faltante de hoy queda como existencia y contamina el cierre de
    # mañana, que empezaría con un sobrante que nadie puso ahí.
    #
    # Se lee la existencia DESPUÉS del retorno, no se deduce de `diferencia`: si
    # por cualquier razón las dos no coincidieran, el que tiene razón es el
    # inventario, y el objetivo es que el camión quede en cero.
    ajustes = 0
    sobrantes = Decimal(0)
    faltantes = Decimal(0)
    for r in renglones:
        quedan = (
            await sesion.execute(
                text(
                    "SELECT cantidad FROM existencias "
                    " WHERE almacen_id = :a AND producto_id = :p"
                ),
                {"a": camion, "p": r["producto_id"]},
            )
        ).scalar_one_or_none()
        if quedan is None:
            continue
        resto = Decimal(quedan)
        if resto == 0:
            continue

        if resto > 0:
            # El sistema creía que había más de lo que volvió: faltante. Sale del
            # sistema, y es lo que se le cobra al vendedor.
            await _mover(
                sesion, tipo="ajuste", origen=camion, destino=None,
                producto=r["producto_id"], cantidad=resto,
                documento=liquidacion_id, quien=actor.usuario_id, ahora=ahora,
            )
            faltantes += resto
        else:
            # Volvió más de lo que el sistema sabía: sobrante. Entra al camión para
            # que el retorno ya registrado cuadre y el saldo quede en cero.
            await _mover(
                sesion, tipo="ajuste", origen=None, destino=camion,
                producto=r["producto_id"], cantidad=-resto,
                documento=liquidacion_id, quien=actor.usuario_id, ahora=ahora,
            )
            sobrantes += -resto
        ajustes += 1

    con_diferencia = (
        await sesion.execute(
            text(
                "SELECT count(*) FROM liquidacion_detalle "
                " WHERE liquidacion_id = :l AND diferencia <> 0"
            ),
            {"l": liquidacion_id},
        )
    ).scalar_one()

    # `sync_completa` dice si el cierre descansa en un DATO o en la palabra de
    # quien lo cerró, y por eso no se escribe `true` a secas. Un `true` sin
    # respaldo contesta "¿estaba el equipo al día?" con una afirmación disfrazada
    # de hecho, y esa es justo la pregunta que se hace al auditar un sobrante.
    await sesion.execute(
        text(
            "UPDATE liquidaciones "
            "   SET estado = 'cerrada', cerrada_en = :ahora, cerrada_por = :quien, "
            "       sync_completa = :respaldada, operaciones_pendientes = 0 "
            " WHERE id = :l"
        ),
        {
            "ahora": ahora,
            "quien": actor.usuario_id,
            "l": liquidacion_id,
            "respaldada": respaldo["respaldado"],
        },
    )

    # Este UPDATE publica el delta que VACÍA `existencias_camion` en el teléfono.
    await sesion.execute(
        text("UPDATE cargas SET estado = 'liquidada' WHERE id = :c"),
        {"c": cabecera["carga_id"]},
    )

    # Y se pide recalcular el laboratorio (Fase 8).
    #
    # Éste es el momento natural: al cerrar, las cifras del día quedan firmes, y
    # hasta ahora el esquema estrella mostraba un día a medio sincronizar. La
    # `clave_unica` por día lo hace idempotente — cerrar cinco rutas encola UN
    # refresh, no cinco— y va en la MISMA transacción que el cierre: si el cierre
    # se deshace, el job no queda encolado pidiendo recalcular algo que no pasó.
    await encolar(
        sesion,
        "refrescar_analitica",
        {"motivo": "liquidacion_cerrada", "fecha": str(cabecera["fecha_operativa"])},
        clave_unica=f"refrescar_analitica:{cabecera['fecha_operativa']}",
        retraso=timedelta(minutes=1),
    )

    # Y el tablero de Gerencia (Fase 7), que es otra cadencia y otra tabla.
    #
    # El cierre puede CAMBIAR las cifras del día —los ajustes de liquidación
    # mueven inventario— y además es el momento en que un sobrante o faltante
    # queda declarado. Sin esto, el tablero seguiría mostrando el día tal como
    # lo dejó la última sincronización, y nadie entendería por qué la merma del
    # cierre no aparece.
    await encolar(
        sesion,
        "recalcular_tablero",
        {"motivo": "liquidacion_cerrada", "fecha": str(cabecera["fecha_operativa"])},
        clave_unica="recalcular_tablero",
        retraso=timedelta(seconds=30),
    )
    await sesion.commit()

    aviso = f"Liquidación {cabecera['folio']} cerrada. "
    if con_diferencia:
        aviso += f"{con_diferencia} producto(s) con diferencia. "
    else:
        aviso += "Cuadró producto por producto. "
    if ajustes:
        partes = []
        if faltantes:
            partes.append(f"faltante de {faltantes.to_integral_value():,} unidades")
        if sobrantes:
            partes.append(f"sobrante de {sobrantes.to_integral_value():,} unidades")
        aviso += (
            f"Se escribieron {ajustes} ajuste(s) para dejar el camión en cero "
            f"({' y '.join(partes)}). "
        )
    aviso += "El teléfono vacía su inventario en la siguiente sincronización."
    return _a_lista(guardado=aviso)


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------


async def _mover(
    sesion,
    *,
    tipo: str,
    origen: uuid.UUID | None,
    destino: uuid.UUID | None,
    producto: uuid.UUID,
    cantidad: Decimal,
    documento: uuid.UUID,
    quien: uuid.UUID,
    ahora: datetime,
) -> None:
    """Un movimiento en el libro mayor, con su efecto en la caché de existencias.

    Las dos cosas juntas y siempre: si divergieran, el job de reconciliación
    nocturno estaría arreglando un error evitable, y la pantalla de inventario
    marcaría un descuadre que no corresponde a nada físico.
    """
    await sesion.execute(
        text(
            """
            INSERT INTO movimientos_inventario
              (tipo, almacen_origen_id, almacen_destino_id, producto_id, cantidad,
               documento_tipo, documento_id, usuario_id, fecha_servidor)
            VALUES (:tipo, :origen, :destino, :p, :cant, 'liquidacion', :doc, :quien, :ahora)
            """
        ),
        {
            "tipo": tipo,
            "origen": origen,
            "destino": destino,
            "p": producto,
            "cant": cantidad,
            "doc": documento,
            "quien": quien,
            "ahora": ahora,
        },
    )
    for almacen, signo in ((origen, -1), (destino, 1)):
        if almacen is None:
            continue
        await sesion.execute(
            text(
                """
                INSERT INTO existencias (almacen_id, producto_id, cantidad, actualizado_en)
                VALUES (:a, :p, :cant, :ahora)
                ON CONFLICT (almacen_id, producto_id) DO UPDATE
                   SET cantidad = existencias.cantidad + :cant, actualizado_en = :ahora
                """
            ),
            {"a": almacen, "p": producto, "cant": cantidad * signo, "ahora": ahora},
        )


async def _renglones_calculados(sesion, carga_id: uuid.UUID) -> list[dict]:
    """Cargado, vendido, merma y devuelto por producto, de los documentos.

    Las tres últimas salen de las operaciones del **día operativo de esa carga y
    ese vendedor**, no de la carga: una merma no trae `carga_id`, y amarrarla por
    fecha y vendedor es lo que el modelo permite. Si un vendedor llegara a tener dos
    cargas el mismo día el índice `uq_carga_vendedor_dia` lo impide, así que la
    atadura es única.
    """
    return [
        dict(f)
        for f in (
            await sesion.execute(
                text(
                    """
                    WITH la_carga AS (
                        SELECT id, vendedor_id, fecha_operativa, almacen_destino_id
                          FROM cargas WHERE id = :c
                    )
                    SELECT d.producto_id,
                           sum(d.cantidad) AS cargada,
                           COALESCE(v.vendida, 0) AS vendida,
                           COALESCE(m.merma, 0) AS merma,
                           COALESCE(m.devuelta, 0) AS devuelta
                      FROM carga_detalle d
                      CROSS JOIN la_carga c
                      LEFT JOIN LATERAL (
                            SELECT sum(vp.cantidad_base) AS vendida
                              FROM venta_partidas vp
                              JOIN ventas ve ON ve.id = vp.venta_id
                             WHERE vp.producto_id = d.producto_id
                               AND ve.carga_id = c.id
                               AND ve.estado = 'confirmada'
                      ) v ON true
                      LEFT JOIN LATERAL (
                            SELECT
                              sum(md.cantidad_base) FILTER (WHERE me.tipo = 'merma')
                                AS merma,
                              sum(md.cantidad_base)
                                FILTER (WHERE me.tipo = 'devolucion_cliente')
                                AS devuelta
                              FROM merma_detalle md
                              JOIN mermas me ON me.id = md.merma_id
                             WHERE md.producto_id = d.producto_id
                               AND me.vendedor_id = c.vendedor_id
                               AND me.almacen_id = c.almacen_destino_id
                               AND me.fecha_operativa = c.fecha_operativa
                               AND me.estado = 'confirmada'
                      ) m ON true
                     WHERE d.carga_id = :c
                     GROUP BY d.producto_id, v.vendida, m.merma, m.devuelta
                    """
                ),
                {"c": carga_id},
            )
        ).mappings().all()
    ]


async def _efectivo_esperado(sesion, vendedor_id, fecha_operativa) -> Decimal:
    """Ventas de contado más cobros en efectivo del día.

    Las ventas a crédito NO suman: no se cobró nada. Y de los cobros solo los de
    forma `efectivo` — una transferencia no viene en la bolsa.
    """
    fila = (
        await sesion.execute(
            text(
                """
                SELECT
                  COALESCE((SELECT sum(total) FROM ventas
                             WHERE vendedor_id = :v AND fecha_operativa = :d
                               AND estado = 'confirmada' AND tipo = 'contado'), 0)
                  +
                  COALESCE((SELECT sum(importe) FROM cobros
                             WHERE vendedor_id = :v AND fecha_operativa = :d
                               AND estado = 'confirmado' AND forma_pago = 'efectivo'), 0)
                  AS esperado
                """
            ),
            {"v": vendedor_id, "d": fecha_operativa},
        )
    ).scalar_one()
    return Decimal(fila)


async def _bloqueos_para_cerrar(sesion, cabecera) -> list[str]:
    """Lo que el servidor SÍ puede comprobar antes de permitir el cierre.

    Cada bloqueo es un hecho verificable, no una sospecha. Lo que el servidor no
    puede ver por sí mismo —cuántas operaciones le quedan en la bandeja al
    teléfono— ahora llega **reportado por el teléfono** en cada push, así que ya es
    un hecho más y no una casilla: ver `_respaldo_de_sincronizacion`.
    """
    bloqueos: list[str] = []

    cuarentena = (
        await sesion.execute(
            text(
                """
                SELECT count(*) FROM sync_cuarentena c
                  JOIN dispositivos dis ON dis.id = c.dispositivo_id
                 WHERE dis.usuario_id = :v AND c.estado = 'pendiente'
                """
            ),
            {"v": cabecera["vendedor_id"]},
        )
    ).scalar_one()
    if cuarentena:
        bloqueos.append(
            f"el equipo tiene {cuarentena} operación(es) en cuarentena, y cualquiera "
            "puede ser la venta que explica una diferencia. Atiéndelas primero."
        )

    # ¿Sincronizó el equipo después de que la carga se confirmó? Si no, con
    # seguridad hay operaciones del día que el servidor no tiene.
    sin_sincronizar = (
        await sesion.execute(
            text(
                """
                SELECT count(*) FROM dispositivos d
                 WHERE d.usuario_id = :v AND d.estado = 'activo'
                   AND (d.ultima_sync_push_en IS NULL
                        OR d.ultima_sync_push_en < :desde)
                """
            ),
            {"v": cabecera["vendedor_id"], "desde": cabecera["fecha_operativa"]},
        )
    ).scalar_one()
    if sin_sincronizar:
        bloqueos.append(
            f"{sin_sincronizar} equipo(s) del vendedor no han sincronizado desde el "
            "día de la carga: lo que hayan hecho no está aquí todavía."
        )

    # Lo que el teléfono dijo de su propia cola. Es la única fuente que lo sabe, y
    # cuando dice que le quedan operaciones eso ya no es una sospecha: es un
    # bloqueo, porque cada sobre pendiente puede ser la venta que explica el
    # sobrante que esta liquidación está por declarar.
    con_cola = (
        await sesion.execute(
            text(
                """
                SELECT d.etiqueta, d.cola_pendiente
                  FROM dispositivos d
                 WHERE d.usuario_id = :v AND d.estado = 'activo'
                   AND COALESCE(d.cola_pendiente, 0) > 0
                 ORDER BY d.cola_pendiente DESC
                """
            ),
            {"v": cabecera["vendedor_id"]},
        )
    ).mappings().all()
    for equipo in con_cola:
        bloqueos.append(
            f"«{equipo['etiqueta']}» reportó {equipo['cola_pendiente']} operación(es) "
            "sin subir en su última sincronización. Cualquiera puede ser la venta que "
            "explica una diferencia: sincroniza el equipo y vuelve a abrir el conteo."
        )

    return bloqueos


async def _respaldo_de_sincronizacion(sesion, cabecera) -> dict:
    """¿Hay un DATO que respalde que el equipo terminó de subir todo?

    Esto es la diferencia entre `sync_completa = true` significando algo y
    significando "alguien marcó una casilla". Al auditar un cierre con sobrante, lo
    primero que se pregunta es si el equipo estaba al día; un `true` sin respaldo
    contesta esa pregunta con una afirmación disfrazada de hecho.

    El respaldo existe cuando **todos** los equipos activos del vendedor reportaron
    cero pendientes, y lo reportaron **después** del día de la carga. Un cero de
    anteayer no dice nada sobre hoy (§0.3): el equipo pudo levantar veinte ventas
    desde entonces.

    Cuando no hay respaldo se sigue pidiendo la confirmación de la persona —cerrar
    el día no puede quedar bloqueado porque un vendedor no actualizó la app— pero
    `sync_completa` se queda en `false`, que es lo que de verdad pasó.
    """
    equipos = (
        await sesion.execute(
            text(
                """
                SELECT d.etiqueta, d.cola_pendiente, d.cola_reportada_en
                  FROM dispositivos d
                 WHERE d.usuario_id = :v AND d.estado = 'activo'
                """
            ),
            {"v": cabecera["vendedor_id"]},
        )
    ).mappings().all()

    if not equipos:
        return {"respaldado": False, "motivo": "el vendedor no tiene equipos activos"}

    desde = cabecera["fecha_operativa"]
    for d in equipos:
        if d["cola_pendiente"] is None or d["cola_reportada_en"] is None:
            return {
                "respaldado": False,
                "motivo": f"«{d['etiqueta']}» nunca ha reportado su cola "
                "(probablemente trae una versión vieja de la app)",
            }
        if d["cola_reportada_en"].date() < desde:
            return {
                "respaldado": False,
                "motivo": f"«{d['etiqueta']}» reportó su cola el "
                f"{d['cola_reportada_en'].date().isoformat()}, antes del día de la "
                "carga: ese cero no dice nada sobre hoy",
            }
        if d["cola_pendiente"] > 0:
            # Lo normal es que `_bloqueos_para_cerrar` ya haya frenado el cierre por
            # esto. Se vuelve a comprobar porque la pantalla de detalle llama a las
            # dos funciones por separado, y sin esta línea diría «no le queda nada
            # por subir» justo arriba del bloqueo que dice que le quedan tres. Dos
            # afirmaciones contrarias en la misma pantalla cuestan más que el
            # problema que explican.
            return {
                "respaldado": False,
                "motivo": f"«{d['etiqueta']}» reportó {d['cola_pendiente']} "
                "operación(es) sin subir",
            }

    return {"respaldado": True, "motivo": None}


def _leer_cantidad(texto: str) -> Decimal:
    """El conteo físico de un producto. El vacío vale cero.

    Al contar un camión, el producto que no se anotó es el que no venía.
    """
    crudo = (texto or "").strip().replace(",", "")
    if not crudo:
        return Decimal("0.000")
    try:
        valor = Decimal(crudo)
    except ArithmeticError as e:
        raise CapturaInvalida(f"«{texto}» no es una cantidad.") from e
    if not valor.is_finite():
        raise CapturaInvalida(f"«{texto}» no es una cantidad.")
    if valor < 0:
        raise CapturaInvalida("Un conteo no puede ser negativo.")
    if valor > 1000000:
        raise CapturaInvalida(f"{valor} parece un error de dedo.")
    return valor.quantize(Decimal("0.001"))


def _volver(liquidacion_id: uuid.UUID, *, error: str = "", guardado: str = ""):
    destino = f"/panel/liquidaciones/{liquidacion_id}"
    if error:
        destino += f"?error={quote(error)}"
    elif guardado:
        destino += f"?guardado={quote(guardado)}"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


def _a_lista(*, error: str = "", guardado: str = ""):
    destino = "/panel/liquidaciones"
    if error:
        destino += f"?error={quote(error)}"
    elif guardado:
        destino += f"?guardado={quote(guardado)}"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)
