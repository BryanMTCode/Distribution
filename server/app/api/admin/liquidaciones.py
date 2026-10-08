"""Fase 7 · Liquidación: el cierre del día.

────────────────────────────────────────────────────────────────────────────
EL CAMIÓN ES UN ALMACÉN RODANTE
────────────────────────────────────────────────────────────────────────────
Decisión de la dirección, octubre 2026: la mercancía que no se vende se queda a
dormir en el camión y se acumula con la carga del día siguiente. **El camión no
amanece en ceros, y lo que durmió arriba no es un faltante.**

Este módulo hacía lo contrario: bajaba todo a la bodega y escribía un ajuste para
dejar el camión en cero, así que cada noche le cobraba al vendedor todo lo que no
había vendido. Lo que cambió, en concreto:

  · la ecuación gana el saldo inicial;
  · el conteo es de lo que SE QUEDA arriba, no de lo que baja (`cant_contada`);
  · el cierre deja el camión en lo contado, no en cero, y cuando cuadra no
    escribe ningún movimiento;
  · el delta de la carga liquidada le lleva al teléfono el ajuste, no el vaciado.

────────────────────────────────────────────────────────────────────────────
AQUÍ SE ATRAPAN LOS DESCUADRES, Y ES LA ÚNICA PANTALLA QUE LOS ATRAPA
────────────────────────────────────────────────────────────────────────────
    esperado   = inicial + cargado − vendido − merma + devuelto
    diferencia = contado − esperado

`contado` es lo que se **cuenta físicamente arriba del camión**. Todo lo demás lo
calcula el sistema de sus propios documentos. La diferencia es lo único que importa
del cierre, y tiene dos lecturas:

    faltante (negativo)  salió mercancía sin documento. Se le cobra al vendedor.
    sobrante (positivo)  hay más de lo que el sistema sabe. Casi siempre es una
                         venta que el teléfono no ha sincronizado todavía.

Y los renglones del cierre son los del CAMIÓN, no los de la carga: un producto que
lleva tres días arriba y hoy no se cargó tiene que contarse igual, o nadie notaría
si desapareció.

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

1. Las cifras calculadas se **recalculan**: entre abrir y cerrar entran ventas que
   el teléfono sincronizó tarde, y lo que quedara viejo sería la cantidad de
   mercancía que se le cobra a una persona.
2. **`ajuste`** por la diferencia, para que el camión quede EXACTAMENTE en lo
   contado. Si cuadra no se escribe nada: no pasó nada físico que registrar.
3. La carga pasa a **`liquidada`**, y ese UPDATE publica el delta que le lleva al
   teléfono **el ajuste** (migración 0030 + el aplicador de Dart). Antes le llevaba
   la orden de vaciar el camión.
4. La liquidación queda `cuadrada` o `con_diferencia`, según la ecuación.

No hay movimiento camión → bodega. Cuando el vendedor sí entrega mercancía —cambia
de ruta, se descontinúa un producto— eso es un traspaso, que es un documento con su
propia huella y su propia aceptación en el teléfono.
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
from app.domain.liquidacion import (
    RenglonDeLiquidacion,
    diferencia_de_efectivo,
    saldo_inicial,
)
from app.infra.cuenta_vendedor import ORIGENES, cargar_el_corte
from app.workers.cola import encolar

router = APIRouter(
    prefix="/panel/liquidaciones", tags=["panel"], include_in_schema=False
)

PERMISO = "inventario.liquidar"


# ---------------------------------------------------------------------------
# El corte, aparte de la pantalla
# ---------------------------------------------------------------------------
# Las funciones `abrir_corte`, `datos_del_corte`, `guardar_conteo`,
# `guardar_arqueo` y `cerrar_corte` son el corte: las usan el panel y la app
# (`/v1/cortes`). Viven aquí, una vez, para que el corte hecho desde el teléfono
# mueva el inventario, le cobre al vendedor y avise al teléfono EXACTAMENTE igual
# que el del panel. Las rutas del panel solo traducen a HTML.


class CorteNoExiste(Exception):
    """La liquidación (o la carga) que se pidió no existe."""


class CorteRechazado(Exception):
    """El corte no se puede hacer así; el mensaje dice por qué y qué hacer."""


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

    return render(
        peticion,
        "liquidaciones.html",
        {
            "pendientes": await cargas_por_cortar(sesion),
            "liquidaciones": await cortes_recientes(sesion),
            "puede_editar": actor.puede(PERMISO),
            "error": error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Corte del día",
    )


async def cargas_por_cortar(sesion) -> list:
    """Las cargas que salieron y nadie ha cortado, las más viejas primero."""
    return list(
        (
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
    )


async def cortes_recientes(sesion) -> list:
    """Los cortes abiertos y cerrados, los más nuevos primero."""
    return list(
        (
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

    `cant_contada` nace en **cero**, no en "lo esperado". Prellenarla con el
    esperado haría que cerrar sin contar diera cuadre perfecto, y entonces el
    cierre no significaría nada: sería un botón que dice que todo está bien.

    Contar el camión es el único dato que esta pantalla no puede calcular, y es
    justamente el que le da sentido a los demás.

    Las cifras calculadas que se guardan aquí son un primer borrador para la
    pantalla: entre abrir y cerrar entran ventas que el teléfono sincroniza tarde.
    El cierre las vuelve a calcular antes de declarar nada.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    try:
        carga = uuid.UUID(carga_id)
    except ValueError:
        return _a_lista(error="Falta la carga.")

    try:
        liquidacion_id = await abrir_corte(sesion, carga)
    except (CorteNoExiste, CorteRechazado) as e:
        return _a_lista(error=str(e))
    return RedirectResponse(
        f"/panel/liquidaciones/{liquidacion_id}", status_code=status.HTTP_303_SEE_OTHER
    )


async def abrir_corte(sesion, carga: uuid.UUID) -> uuid.UUID:
    """Abre el corte de esa carga y devuelve su id; si ya estaba abierto, ese.

    Ver `abrir` para el porqué de cada decisión.
    """
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
        raise CorteNoExiste("Esa carga no existe.")
    if cabecera["estado"] not in ("confirmada", "en_ruta"):
        raise CorteRechazado(f"Esa carga está {cabecera['estado']}: no hay qué liquidar.")

    ya = (
        await sesion.execute(
            text("SELECT id FROM liquidaciones WHERE carga_id = :c"), {"c": carga}
        )
    ).scalar_one_or_none()
    if ya is not None:
        return ya

    renglones = _con_inicial(await _renglones_calculados(sesion, carga))
    if not renglones:
        raise CorteRechazado(
            "Ni esa carga trae renglones ni el camión tiene saldo: no hay qué liquidar."
        )

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
                  (id, liquidacion_id, producto_id, cant_inicial, cant_cargada,
                   cant_vendida, cant_merma, cant_devuelta, cant_contada)
                VALUES (:id, :l, :p, :inicial, :cargada, :vendida, :merma,
                        :devuelta, 0)
                """
            ),
            {
                "id": uuid.uuid4(),
                "l": liquidacion_id,
                "p": r["producto_id"],
                "inicial": r["inicial"],
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
    return liquidacion_id


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

    datos = await datos_del_corte(sesion, liquidacion_id)
    if datos is None:
        return RedirectResponse("/panel/liquidaciones", status_code=303)
    cabecera = datos["cabecera"]

    return render(
        peticion,
        "liquidacion_detalle.html",
        {
            "c": cabecera,
            "respaldo": datos["respaldo"],
            "renglones": datos["renglones"],
            "descuadres": datos["descuadres"],
            "divergencias": datos["divergencias"],
            "bloqueos": datos["bloqueos"],
            "abierta": cabecera["estado"] != "cerrada",
            "puede_editar": actor.puede(PERMISO),
            "esperado_texto": dinero(cabecera["efectivo_esperado"]),
            "entregado_texto": dinero(cabecera["efectivo_entregado"]),
            "diferencia_efectivo": datos["diferencia_efectivo"],
            "error": error,
            "guardado": guardado,
            "cargos": datos["cargos"],
            "total_cargado": datos["total_cargado"],
            "origenes": ORIGENES,
        },
        actor=actor,
        seccion="Corte del día",
    )


async def datos_del_corte(sesion, liquidacion_id: uuid.UUID) -> dict | None:
    """Todo lo que se ve de un corte. Si sigue abierto, con las cifras de AHORA."""
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
        return None

    # Mientras no se cierre, la pantalla muestra el camión de AHORA: lo vendido,
    # mermado y devuelto que haya sincronizado hasta este momento, y el efectivo
    # que eso implica. Ver `_refrescar_cifras`: era el defecto de «vendo y el
    # camión del panel no baja». Lo contado no se toca.
    if cabecera["estado"] != "cerrada":
        await _refrescar_cifras(sesion, liquidacion_id, cabecera["carga_id"])
        esperado = await _efectivo_esperado(
            sesion, cabecera["vendedor_id"], cabecera["fecha_operativa"]
        )
        await sesion.execute(
            text(
                "UPDATE liquidaciones SET efectivo_esperado = :e "
                " WHERE id = :l AND estado <> 'cerrada'"
            ),
            {"e": esperado, "l": liquidacion_id},
        )
        await sesion.commit()
        cabecera = {**dict(cabecera), "efectivo_esperado": esperado}

    crudos = (
        await sesion.execute(
            text(
                """
                SELECT d.id, d.producto_id, d.cant_inicial, d.cant_cargada,
                       d.cant_vendida, d.cant_merma, d.cant_devuelta,
                       d.cant_contada, d.diferencia,
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
            inicial=Decimal(d["cant_inicial"]),
            cargado=Decimal(d["cant_cargada"]),
            vendido=Decimal(d["cant_vendida"]),
            merma=Decimal(d["cant_merma"]),
            devuelto=Decimal(d["cant_devuelta"]),
            contado=Decimal(d["cant_contada"]),
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

    # Lo que este Corte le cargó al vendedor, si ya se cerró. Es la respuesta a
    # «¿y esto cuánto le costó?» sin salir de la pantalla.
    cargos = (
        await sesion.execute(
            text(
                "SELECT origen, importe FROM cuenta_vendedor "
                " WHERE liquidacion_id = :l ORDER BY origen"
            ),
            {"l": liquidacion_id},
        )
    ).mappings().all()

    return {
        "cabecera": dict(cabecera),
        "respaldo": respaldo,
        "renglones": renglones,
        "descuadres": descuadres,
        "divergencias": divergencias,
        "bloqueos": bloqueos,
        "diferencia_efectivo": diferencia_de_efectivo(
            Decimal(cabecera["efectivo_entregado"]),
            Decimal(cabecera["efectivo_esperado"]),
        ),
        "cargos": cargos,
        "total_cargado": sum((Decimal(x["importe"]) for x in cargos), Decimal(0)),
    }


@router.post("/{liquidacion_id}/contar")
async def contar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    liquidacion_id: uuid.UUID,
):
    """Captura el conteo físico del camión.

    Los campos vienen como `contada_<id del renglón>`, así que se leen del
    formulario crudo. Un campo **vacío significa cero**, no "no lo cambies": al
    contar un camión, el producto que no se anotó es el que no está arriba. Si
    significara "no lo toques", un producto que se terminó quedaría con el conteo de
    un intento anterior y el faltante desaparecería sin que nadie lo decidiera.
    """
    actor.exigir(PERMISO)
    formulario = await peticion.form()
    exigir_csrf(peticion, str(formulario.get("csrf") or ""))

    contados = {
        clave.removeprefix("contada_"): str(valor)
        for clave, valor in formulario.multi_items()
        if clave.startswith("contada_")
    }
    try:
        await guardar_conteo(sesion, liquidacion_id, contados)
    except CorteNoExiste:
        return RedirectResponse("/panel/liquidaciones", status_code=303)
    except (CorteRechazado, CapturaInvalida) as e:
        return _volver(liquidacion_id, error=str(e))
    return _volver(liquidacion_id, guardado="Conteo guardado.")


async def guardar_conteo(
    sesion, liquidacion_id: uuid.UUID, contados: dict[str, str]
) -> None:
    """Guarda el conteo físico. `contados` va de id de renglón a lo tecleado.

    El renglón que no viene, o viene vacío, vale CERO: ver `contar`. Si una
    cantidad no se entiende, no se guarda nada (`CapturaInvalida`).
    """
    estado = (
        await sesion.execute(
            text("SELECT estado FROM liquidaciones WHERE id = :l"), {"l": liquidacion_id}
        )
    ).scalar_one_or_none()
    if estado is None:
        raise CorteNoExiste("Ese corte no existe.")
    if estado == "cerrada":
        raise CorteRechazado("Esta liquidación ya está cerrada.")

    renglones = (
        await sesion.execute(
            text("SELECT id FROM liquidacion_detalle WHERE liquidacion_id = :l"),
            {"l": liquidacion_id},
        )
    ).scalars().all()

    # Primero se leen TODAS: si una no se entiende no se guarda ninguna, y quien
    # cuenta no se queda con medio camión guardado sin saberlo.
    leidos = []
    for renglon in renglones:
        crudo = contados.get(str(renglon))
        # Se lee con el lector de cantidades de tres decimales, no con `int`:
        # la columna es numeric(14,3) y un día puede haber un producto a granel.
        leidos.append((renglon, _leer_cantidad(crudo if crudo is not None else "")))

    for renglon, contado in leidos:
        await sesion.execute(
            text("UPDATE liquidacion_detalle SET cant_contada = :r WHERE id = :id"),
            {"r": contado, "id": renglon},
        )

    await sesion.commit()


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
    guardó al abrir: entre abrir y cerrar pueden haber entrado ventas en efectivo
    que el teléfono sincronizó tarde. Usar el número viejo haría aparecer un
    faltante de efectivo del tamaño exacto de lo que llegó en medio.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    try:
        aviso = await guardar_arqueo(sesion, liquidacion_id, efectivo_entregado, observaciones)
    except CorteNoExiste:
        return RedirectResponse("/panel/liquidaciones", status_code=303)
    except (CorteRechazado, CapturaInvalida) as e:
        return _volver(liquidacion_id, error=str(e))
    return _volver(liquidacion_id, guardado=aviso)


async def guardar_arqueo(
    sesion, liquidacion_id: uuid.UUID, efectivo_entregado: str, observaciones: str
) -> str:
    """Guarda lo entregado contra lo esperado (recalculado). Devuelve el aviso."""
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
        raise CorteNoExiste("Ese corte no existe.")
    if cabecera["estado"] == "cerrada":
        raise CorteRechazado("Esta liquidación ya está cerrada.")

    entregado = leer_dinero(efectivo_entregado, campo="El efectivo entregado")

    esperado = await _efectivo_esperado(
        sesion, cabecera["vendedor_id"], cabecera["fecha_operativa"]
    )

    await sesion.execute(
        text(
            "UPDATE liquidaciones "
            "   SET efectivo_entregado = :e, efectivo_esperado = :esp, "
            "       observaciones = :obs, arqueo_en = now() "
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
            f"Sobran {dinero(falta)}. Suele ser una venta que el teléfono todavía "
            "no sincronizó, o una transferencia que se capturó como efectivo."
        )
    return aviso


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
    """Cierra el día: cifras al día, ajuste por la diferencia, carga liquidada.

    Todo en una transacción, y en este orden, porque el ajuste se calcula **sobre
    las cifras recién recalculadas**: hacerlo antes daría un número que no
    corresponde a nada.

    No baja mercancía a la bodega. El camión es un almacén rodante: se queda con
    lo que se contó.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    try:
        aviso = await cerrar_corte(
            sesion,
            liquidacion_id,
            quien=actor.usuario_id,
            confirmo_sincronizado=bool(confirmo_sincronizado),
        )
    except CorteNoExiste:
        return RedirectResponse("/panel/liquidaciones", status_code=303)
    except CorteRechazado as e:
        return _volver(liquidacion_id, error=str(e))
    return _a_lista(guardado=aviso)


async def cerrar_corte(
    sesion, liquidacion_id: uuid.UUID, *, quien: uuid.UUID, confirmo_sincronizado: bool
) -> str:
    """Cierra el corte (ver `cerrar` para el porqué) y devuelve el aviso."""
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
        raise CorteNoExiste("Ese corte no existe.")
    if cabecera["estado"] == "cerrada":
        raise CorteRechazado("Ya estaba cerrada.")

    bloqueos = await _bloqueos_para_cerrar(sesion, cabecera)
    if bloqueos:
        raise CorteRechazado("No se puede cerrar: " + " ".join(bloqueos))
    respaldo = await _respaldo_de_sincronizacion(sesion, cabecera)
    if not respaldo["respaldado"] and not confirmo_sincronizado:
        raise CorteRechazado(
            "Confirma que el teléfono terminó de sincronizar: "
            f"{respaldo['motivo']}. Una venta que entre después del cierre convierte "
            "un sobrante en un cuadre, y el cierre ya dijo lo contrario por escrito."
        )

    ahora = datetime.now(UTC)
    camion = cabecera["almacen_destino_id"]

    # ------------------------------------------------------------------
    # Las cifras calculadas, otra vez, justo antes de declarar.
    # ------------------------------------------------------------------
    # Las que se guardaron al abrir son un borrador: entre abrir y cerrar entran
    # ventas que el teléfono sincronizó tarde. Es el mismo motivo por el que el
    # arqueo recalcula el efectivo esperado, y aquí pesa más: lo que quedara
    # desactualizado sería la cantidad de mercancía que se le cobra a una persona.
    #
    # Con esto, `diferencia` —la columna generada— acaba siendo exactamente
    # «lo contado menos el saldo vivo del camión», que es el único número
    # defendible frente al vendedor. Ver `saldo_inicial`.
    await _refrescar_cifras(sesion, liquidacion_id, cabecera["carga_id"])

    # Y el efectivo esperado, por la misma razón: ahora de esto sale un cargo a
    # una persona (la cuenta del vendedor), y una venta que sincronizó después
    # del arqueo no puede quedar fuera de la cuenta. `diferencia_efectivo` es
    # columna generada y se recalcula sola.
    await sesion.execute(
        text("UPDATE liquidaciones SET efectivo_esperado = :e WHERE id = :l"),
        {
            "e": await _efectivo_esperado(
                sesion, cabecera["vendedor_id"], cabecera["fecha_operativa"]
            ),
            "l": liquidacion_id,
        },
    )

    renglones = (
        await sesion.execute(
            text(
                "SELECT producto_id, cant_contada, diferencia "
                "  FROM liquidacion_detalle WHERE liquidacion_id = :l"
            ),
            {"l": liquidacion_id},
        )
    ).mappings().all()

    # ------------------------------------------------------------------
    # El ajuste que deja el camión EXACTAMENTE EN LO CONTADO.
    # ------------------------------------------------------------------
    # Aquí estaba el defecto que la dirección mandó corregir en octubre de 2026.
    # Este bloque dejaba el camión en CERO y le cobraba el resto al vendedor como
    # faltante. Con mercancía que duerme arriba del camión, eso le cobraba cada
    # noche todo lo que no había vendido: mercancía que se puede tocar y contar, y
    # que el sistema declaraba perdida.
    #
    # Ahora el conteo manda y el camión se queda con lo contado. Lo que se escribe
    # es un solo movimiento por producto, el de la diferencia, y **cuando el conteo
    # cuadra no se escribe nada**: no pasó nada físico que registrar.
    #
    # Tampoco baja mercancía a la bodega: ya no hay retorno diario. Cuando el
    # vendedor sí entrega algo —cambia de ruta, se descontinúa un producto— eso es
    # un traspaso camión → bodega, que es un documento con su propia huella y su
    # propia aceptación.
    ajustes = 0
    sobrantes = Decimal(0)
    faltantes = Decimal(0)
    for r in renglones:
        # Se usa `diferencia` y no una resta hecha aquí: es la columna generada,
        # la misma que la pantalla le muestra al vendedor y la misma que el
        # teléfono va a aplicar. Un número distinto en cualquiera de los tres
        # lados es una discusión sin árbitro.
        diferencia = Decimal(r["diferencia"])
        if diferencia == 0:
            continue

        if diferencia < 0:
            # Se contó menos de lo que el sistema tenía: faltante. Sale del
            # sistema, y es lo que se le cobra al vendedor.
            await _mover(
                sesion, tipo="ajuste", origen=camion, destino=None,
                producto=r["producto_id"], cantidad=-diferencia,
                documento=liquidacion_id, quien=quien, ahora=ahora,
            )
            faltantes += -diferencia
        else:
            # Se contó más: sobrante. Entra al camión. Casi siempre es una venta
            # que el teléfono no ha sincronizado, y por eso el cierre se bloquea
            # cuando quedan operaciones pendientes.
            await _mover(
                sesion, tipo="ajuste", origen=None, destino=camion,
                producto=r["producto_id"], cantidad=diferencia,
                documento=liquidacion_id, quien=quien, ahora=ahora,
            )
            sobrantes += diferencia
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
            "       sync_completa = :respaldada, "
            "       operaciones_pendientes = :pendientes "
            " WHERE id = :l"
        ),
        {
            "ahora": ahora,
            "quien": quien,
            "l": liquidacion_id,
            "respaldada": respaldo["respaldado"],
            # Las operaciones que el equipo reportaba al cerrar, no un 0 literal.
            #
            # Se escribía cero a secas, y eso borraba el único número que
            # contesta «¿cuántas faltaban?» cuando alguien audita un sobrante
            # meses después. `sync_completa` ya decía SI estaba al día; esto dice
            # CUÁNTO le faltaba. Cero sigue siendo cero cuando de verdad lo está,
            # y entonces es un dato y no un relleno.
            "pendientes": respaldo["pendientes"],
        },
    )

    # ------------------------------------------------------------------
    # Lo que se le carga al vendedor (migración 0039).
    # ------------------------------------------------------------------
    # Antes el Corte decía «faltan 3 cajas» y ahí terminaba. Ahora el faltante,
    # las mermas a su cargo y el efectivo que no entregó van a su cuenta, a
    # COSTO —regla de la dirección—, en esta misma transacción: si el cierre se
    # deshace, el cargo también.
    cargos = await cargar_el_corte(
        sesion, liquidacion_id=liquidacion_id, quien=quien, ahora=ahora
    )

    # Este UPDATE publica el delta de la carga liquidada. Ya no vacía el camión
    # del teléfono: le lleva el AJUSTE que se acaba de escribir, para que el saldo
    # del teléfono y el del servidor queden en el mismo número. Ver la migración
    # 0030 y `_carga` en el aplicador de deltas.
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
            f"Se escribieron {ajustes} ajuste(s) para dejar el camión en lo contado "
            f"({' y '.join(partes)}). "
        )
    else:
        aviso += "El camión se queda con lo contado, sin ajustes. "
    aviso += (
        "La mercancía se queda arriba del camión: el teléfono recibe el saldo "
        "corregido en la siguiente sincronización."
    )
    if cargos.total > 0:
        partes = []
        if cargos.mercancia:
            partes.append(f"mercancía {dinero(cargos.mercancia)}")
        if cargos.merma:
            partes.append(f"mermas a su cargo {dinero(cargos.merma)}")
        if cargos.efectivo:
            partes.append(f"efectivo {dinero(cargos.efectivo)}")
        aviso += (
            f" Se cargaron {dinero(cargos.total)} a la cuenta del vendedor "
            f"({', '.join(partes)}), la mercancía a costo."
        )
    if cargos.sin_costo:
        aviso += (
            f" OJO: {len(cargos.sin_costo)} producto(s) sin costo capturado no se "
            f"cobraron ({', '.join(cargos.sin_costo[:5])}). Captura su costo en "
            "Compras y, si corresponde, cárgalo a mano en su cuenta."
        )
    if cargos.sin_arqueo:
        aviso += (
            " OJO: no se capturó el arqueo, así que el efectivo no se le cobró "
            "al vendedor: nadie lo contó."
        )
    return aviso


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------


async def _refrescar_cifras(sesion, liquidacion_id: uuid.UUID, carga_id: uuid.UUID) -> None:
    """Recalcula inicial, cargado, vendido, merma y devuelto. NO toca lo contado.

    ────────────────────────────────────────────────────────────────────────
    POR QUÉ CORRE AL MOSTRAR Y NO SOLO AL CERRAR
    ────────────────────────────────────────────────────────────────────────
    Al principio solo corría al cerrar, y las cifras que se guardaban al abrir
    eran «un primer borrador para la pantalla». Ese borrador era el defecto que
    la dirección reportó en octubre de 2026 como «vendo en la app y el camión
    del panel nunca baja»: la liquidación se abría en la mañana, las ventas
    sincronizaban durante el día, y la pantalla donde se CUENTA el camión seguía
    diciendo vendido 0, esperado 240. Quien contaba lo hacía contra el camión de
    la mañana, y al cerrar el sistema recalculaba y cobraba otra cosa: dos
    números distintos con la misma etiqueta, y el que se vio no era el que se
    cobró.

    Ahora la pantalla y el cierre leen lo mismo, porque los dos pasan por aquí.

    `cant_contada` se queda fuera del `UPDATE` a propósito: es lo único que esta
    pantalla no puede calcular —lo escribe una persona contando cajas— y
    recalcular las demás cifras no puede borrar su trabajo. Un producto que
    aparece después de abrir (una devolución de cliente de algo que no venía en la
    carga) entra como renglón nuevo con su conteo en cero, para que se pueda
    contar.
    """
    for r in _con_inicial(await _renglones_calculados(sesion, carga_id)):
        await sesion.execute(
            text(
                """
                INSERT INTO liquidacion_detalle
                  (id, liquidacion_id, producto_id, cant_inicial, cant_cargada,
                   cant_vendida, cant_merma, cant_devuelta, cant_contada)
                VALUES (:id, :l, :p, :inicial, :cargada, :vendida, :merma,
                        :devuelta, 0)
                ON CONFLICT (liquidacion_id, producto_id) DO UPDATE SET
                  cant_inicial  = excluded.cant_inicial,
                  cant_cargada  = excluded.cant_cargada,
                  cant_vendida  = excluded.cant_vendida,
                  cant_merma    = excluded.cant_merma,
                  cant_devuelta = excluded.cant_devuelta
                """
            ),
            {
                "id": uuid.uuid4(),
                "l": liquidacion_id,
                "p": r["producto_id"],
                "inicial": r["inicial"],
                "cargada": r["cargada"],
                "vendida": r["vendida"],
                "merma": r["merma"],
                "devuelta": r["devuelta"],
            },
        )


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
    """Inicial, cargado, vendido, merma y devuelto por producto, de los documentos.

    Las tres de en medio salen de las operaciones del **día operativo de esa carga
    y ese vendedor**, no de la carga: una merma no trae `carga_id`, y amarrarla por
    fecha y vendedor es lo que el modelo permite. Si un vendedor llegara a tener dos
    cargas el mismo día el índice `uq_carga_vendedor_dia` lo impide, así que la
    atadura es única.

    ───────────────────────────────────────────────────────────────────────────
    LOS RENGLONES SON LOS DEL CAMIÓN, NO LOS DE LA CARGA
    ───────────────────────────────────────────────────────────────────────────
    Antes se partía de `carga_detalle`: lo que la bodega entregó hoy. Con el
    camión como almacén rodante eso deja fuera justo lo que importa — el producto
    que lleva tres días arriba y hoy no se cargó no aparecería en el cierre, así
    que nadie lo contaría y nadie notaría si desapareció.

    Así que los renglones son la UNIÓN de dos conjuntos: lo que se cargó hoy y lo
    que el camión tiene con saldo distinto de cero. Un producto en los dos sale
    una vez.

    `inicial` se deduce del saldo vivo del camión, no de un snapshot. El por qué
    está en `saldo_inicial`, y la consecuencia es la que importa: `esperado` acaba
    siendo el saldo que el sistema tiene AHORA, así que la diferencia que se le
    cobra al vendedor es siempre «lo que conté menos lo que el sistema tiene».
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
                    ),
                    cargado AS (
                        SELECT producto_id, sum(cantidad) AS cargada
                          FROM carga_detalle
                         WHERE carga_id = :c
                         GROUP BY producto_id
                    ),
                    arriba AS (
                        SELECT e.producto_id, e.cantidad AS en_camion
                          FROM existencias e
                          CROSS JOIN la_carga c
                         WHERE e.almacen_id = c.almacen_destino_id
                           AND e.cantidad <> 0
                    ),
                    productos_del_cierre AS (
                        SELECT producto_id FROM cargado
                        UNION
                        SELECT producto_id FROM arriba
                    )
                    SELECT pc.producto_id,
                           COALESCE(g.cargada, 0) AS cargada,
                           COALESCE(a.en_camion, 0) AS en_camion,
                           COALESCE(v.vendida, 0) AS vendida,
                           COALESCE(m.merma, 0) AS merma,
                           COALESCE(m.devuelta, 0) AS devuelta
                      FROM productos_del_cierre pc
                      CROSS JOIN la_carga c
                      LEFT JOIN cargado g ON g.producto_id = pc.producto_id
                      LEFT JOIN arriba  a ON a.producto_id = pc.producto_id
                      LEFT JOIN LATERAL (
                            -- Las ventas del DÍA de este camión: mismo vendedor,
                            -- mismo camión, misma fecha operativa. Igual que las
                            -- mermas de abajo.
                            --
                            -- Antes se buscaban por `carga_id`, y con el camión
                            -- rodante eso dejó de ser cierto: el teléfono pone en
                            -- la venta la carga activa QUE CONOCE, y si el vendedor
                            -- sale a vender lo que le sobró antes de sincronizar la
                            -- carga de hoy, la venta lleva la carga de AYER. El
                            -- corte de hoy no la contaba como vendida —y el de
                            -- ayer ya estaba cerrado—, así que la columna
                            -- «vendido» decía de menos y `inicial` lo absorbía en
                            -- silencio. El esperado seguía bien, porque sale del
                            -- saldo vivo; lo que mentía era la explicación.
                            --
                            -- Una venta tiene un solo día operativo y
                            -- `uq_carga_vendedor_dia` impide dos cargas del mismo
                            -- vendedor el mismo día: no hay forma de contarla en
                            -- dos cortes.
                            SELECT sum(vp.cantidad_base) AS vendida
                              FROM venta_partidas vp
                              JOIN ventas ve ON ve.id = vp.venta_id
                             WHERE vp.producto_id = pc.producto_id
                               AND ve.estado = 'confirmada'
                               AND ve.vendedor_id = c.vendedor_id
                               AND ve.almacen_id = c.almacen_destino_id
                               AND ve.fecha_operativa = c.fecha_operativa
                      ) v ON true
                      LEFT JOIN LATERAL (
                            SELECT
                              -- El cambio físico (migración 0040) también:
                              -- el fresco salió del camión con documento.
                              sum(md.cantidad_base)
                                FILTER (WHERE me.tipo IN ('merma', 'cambio'))
                                AS merma,
                              sum(md.cantidad_base)
                                FILTER (WHERE me.tipo = 'devolucion_cliente')
                                AS devuelta
                              FROM merma_detalle md
                              JOIN mermas me ON me.id = md.merma_id
                             WHERE md.producto_id = pc.producto_id
                               AND me.vendedor_id = c.vendedor_id
                               AND me.almacen_id = c.almacen_destino_id
                               AND me.fecha_operativa = c.fecha_operativa
                               AND me.estado = 'confirmada'
                      ) m ON true
                    """
                ),
                {"c": carga_id},
            )
        ).mappings().all()
    ]


def _con_inicial(renglones: list[dict]) -> list[dict]:
    """Agrega `inicial` a cada renglón, con el módulo de dominio.

    En Python y no en SQL a propósito: es la ecuación del negocio despejada, y
    vive en un solo lugar con su explicación. Ver `saldo_inicial`.
    """
    for r in renglones:
        r["inicial"] = saldo_inicial(
            Decimal(r["en_camion"]),
            Decimal(r["cargada"]),
            Decimal(r["vendida"]),
            Decimal(r["merma"]),
            Decimal(r["devuelta"]),
        )
    return renglones


async def _efectivo_esperado(sesion, vendedor_id, fecha_operativa) -> Decimal:
    """Las ventas del día pagadas en efectivo.

    La operación es de contado (ADR 0002 §81): toda venta se pagó al entregar.
    La transferencia no viene en la bolsa —se confirma contra el banco en
    Transferencias—, así que no suma aquí.
    """
    fila = (
        await sesion.execute(
            text(
                """
                SELECT COALESCE(sum(total), 0) AS esperado
                  FROM ventas
                 WHERE vendedor_id = :v AND fecha_operativa = :d
                   AND estado = 'confirmada' AND tipo = 'contado'
                   AND forma_pago = 'efectivo'
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
        return {
            "respaldado": False,
            "motivo": "el vendedor no tiene equipos activos",
            "pendientes": 0,
        }

    # La suma de lo que reportaron los equipos, calculada UNA vez y devuelta en
    # todas las salidas. Si cada rama la calculara, la próxima rama se olvidaría
    # — y el campo que se olvida en una rama es el que acaba guardando un cero
    # que parece un dato.
    pendientes = sum(d["cola_pendiente"] or 0 for d in equipos)

    desde = cabecera["fecha_operativa"]
    for d in equipos:
        if d["cola_pendiente"] is None or d["cola_reportada_en"] is None:
            return {
                "respaldado": False,
                "motivo": f"«{d['etiqueta']}» nunca ha reportado su cola "
                "(probablemente trae una versión vieja de la app)",
                "pendientes": pendientes,
            }
        if d["cola_reportada_en"].date() < desde:
            return {
                "respaldado": False,
                "motivo": f"«{d['etiqueta']}» reportó su cola el "
                f"{d['cola_reportada_en'].date().isoformat()}, antes del día de la "
                "carga: ese cero no dice nada sobre hoy",
                "pendientes": pendientes,
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
                "pendientes": pendientes,
            }

    return {"respaldado": True, "motivo": None, "pendientes": pendientes}


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
