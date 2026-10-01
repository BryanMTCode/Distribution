"""Cobranza: el dinero que el vendedor recibió, y la cartera que queda.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTA PANTALLA NO ES OPCIONAL
────────────────────────────────────────────────────────────────────────────
El manejador de `cobro.crear` es el más permisivo del sistema, y a propósito: en
un cobro **el dinero ya está sobre el mostrador**, así que no rechaza casi nada.
Cobrar más de lo que el cliente debía se registra como saldo a favor; cobrarle a
quien no debía nada se registra igual; un saldo de caché muy desfasado se
registra igual. Las tres cosas se **marcan**.

Marcar sin que nadie mire convierte la bandera en ruido, y entonces el permiso
del manejador deja de ser una decisión y se vuelve un agujero. Hasta hoy esos
cobros marcados solo se alcanzaban con SQL a mano, que es lo mismo que no
alcanzarlos.

────────────────────────────────────────────────────────────────────────────
TRES PREGUNTAS, TRES VISTAS
────────────────────────────────────────────────────────────────────────────
1. **¿Qué entró hoy, y cuánto es efectivo?** Es la cifra que tiene que cuadrar
   contra lo que el vendedor entrega en la mano. Transferencias y cheques entran
   al sistema pero no al arqueo, y mezclarlos hace que la caja nunca cuadre.
2. **¿Qué cobro hay que revisar, y por qué?** Con el motivo escrito en español y
   el saldo que el teléfono creía contra el que había de verdad.
3. **¿Qué nos deben, y desde cuándo?** La cartera por antigüedad: un saldo de
   $40,000 con todo al corriente y uno con $40,000 a 90 días son dos empresas
   distintas, y el total solo no los distingue.

────────────────────────────────────────────────────────────────────────────
LO QUE ESTA PANTALLA *NO* HACE
────────────────────────────────────────────────────────────────────────────
No cancela cobros ni reasigna aplicaciones. Un cobro es dinero recibido y su
aplicación la decidió el FIFO sobre la cartera real; dejar que la oficina lo
mueva con un botón abriría la puerta a maquillar una cartera sin que quede
rastro. Lo que sí hace es **marcar como revisado**, que es un acto de auditoría y
no una corrección: deja quién lo vio y cuándo.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from starlette import status

from app.api.admin.comun import SesionDep, render, texto_o_nulo
from app.api.admin.sesion_web import ActorWeb, exigir_csrf

router = APIRouter(prefix="/panel/cobranza", tags=["panel"], include_in_schema=False)

PERMISO = "cobranza.ver"

# Cómo se le explica a la oficina cada motivo. El código es para el sistema; esto
# es para la persona que tiene que decidir qué hacer con el cobro.
EXPLICACION_MOTIVOS = {
    "cobro_excede_deuda": "Cobró más de lo que el cliente debía",
    "cobro_sin_deuda": "El cliente no tenía facturas abiertas",
    "saldo_del_equipo_desfasado": "El teléfono traía un saldo muy distinto del real",
}

# Las formas de pago que cuentan para el arqueo del día.
#
# Solo el efectivo: una transferencia entra al sistema pero no a la bolsa del
# vendedor, y sumarlas haría que la caja nunca cuadre y que el descuadre se
# atribuyera a la persona equivocada.
FORMAS_EN_ARQUEO = ("efectivo",)

# Los tramos de antigüedad de la cartera.
#
# No es decoración: la pregunta que importa no es cuánto nos deben sino desde
# cuándo. Un saldo de $40,000 al corriente y uno de $40,000 a noventa días son dos
# empresas distintas, y el total solo no los distingue.
TRAMOS = (
    ("al_corriente", "Al corriente"),
    ("1_15", "1 a 15 días"),
    ("16_30", "16 a 30 días"),
    ("31_60", "31 a 60 días"),
    ("mas_60", "Más de 60 días"),
)


@router.get("", response_class=HTMLResponse)
async def listar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    dia: str = "",
    solo_revision: int = 0,
) -> HTMLResponse:
    """Los cobros de un día, con los marcados arriba.

    Abre en **hoy** porque la pregunta de la tarde es siempre la del día que
    termina: cuánto efectivo tiene que entregar cada vendedor.
    """
    actor.exigir(PERMISO)

    filtro = "WHERE k.requiere_revision AND k.estado = 'confirmado'"
    if not solo_revision:
        filtro = (
            "WHERE k.estado = 'confirmado' "
            "  AND k.fecha_operativa = COALESCE(:dia, CURRENT_DATE)"
        )

    filas = (
        await sesion.execute(
            text(
                f"""
                SELECT k.id, k.folio_local, k.importe, k.forma_pago, k.referencia,
                       k.importe_aplicado, k.saldo_a_favor, k.saldo_cache_disp,
                       k.fecha_operativa, k.fecha_dispositivo, k.fecha_servidor,
                       k.requiere_revision, k.revision_motivos, k.impreso,
                       c.nombre_comercial AS cliente, c.codigo AS codigo_cliente,
                       u.nombre AS vendedor
                  FROM cobros k
                  JOIN clientes c ON c.id = k.cliente_id
                  JOIN usuarios u ON u.id = k.vendedor_id
                  {filtro}
                 ORDER BY k.requiere_revision DESC, k.fecha_servidor DESC
                 LIMIT 300
                """  # noqa: S608 — `filtro` es una constante del código, no entrada
            ),
            {"dia": texto_o_nulo(dia)} if not solo_revision else {},
        )
    ).mappings().all()

    # El corte del día, separando lo que entra al arqueo de lo que no.
    #
    # Se calcula en la base y no sumando `filas`: con el filtro de revisión la
    # lista no es el día, y un total que a veces significa una cosa y a veces otra
    # es peor que no tenerlo.
    corte = (
        await sesion.execute(
            text(
                """
                SELECT u.id AS vendedor_id, u.nombre AS vendedor,
                       COALESCE(sum(k.importe) FILTER (
                           WHERE k.forma_pago = ANY(:arqueo)), 0) AS efectivo,
                       COALESCE(sum(k.importe) FILTER (
                           WHERE NOT (k.forma_pago = ANY(:arqueo))), 0) AS otras_formas,
                       count(*) AS cuantos,
                       count(*) FILTER (WHERE k.requiere_revision) AS marcados
                  FROM cobros k
                  JOIN usuarios u ON u.id = k.vendedor_id
                 WHERE k.estado = 'confirmado'
                   AND k.fecha_operativa = COALESCE(:dia, CURRENT_DATE)
                 GROUP BY u.id, u.nombre
                 ORDER BY u.nombre
                """
            ),
            {"dia": texto_o_nulo(dia), "arqueo": list(FORMAS_EN_ARQUEO)},
        )
    ).mappings().all()

    pendientes = (
        await sesion.execute(
            text(
                "SELECT count(*) FROM cobros "
                " WHERE requiere_revision AND estado = 'confirmado'"
            )
        )
    ).scalar_one()

    return render(
        peticion,
        "cobranza.html",
        {
            "filas": filas,
            "corte": corte,
            "dia": dia,
            "solo_revision": bool(solo_revision),
            "pendientes": pendientes,
            "explicacion": EXPLICACION_MOTIVOS,
            "formas_en_arqueo": FORMAS_EN_ARQUEO,
            "puede_editar": actor.puede(PERMISO),
        },
        actor=actor,
        seccion="Cobranza",
    )


@router.get("/{cobro_id}", response_class=HTMLResponse)
async def detalle(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    cobro_id: uuid.UUID,
) -> HTMLResponse:
    """Un cobro con sus aplicaciones y el estado de la cartera del cliente.

    Las aplicaciones son lo que contesta la pregunta de la oficina: el FIFO pagó
    primero lo más viejo, y ver a qué facturas fue es lo que permite responderle
    al cliente que llama preguntando por una en particular.
    """
    actor.exigir(PERMISO)

    cobro = (
        await sesion.execute(
            text(
                """
                SELECT k.*, c.nombre_comercial AS cliente, c.codigo AS codigo_cliente,
                       c.id AS cliente_id, u.nombre AS vendedor,
                       d.etiqueta AS dispositivo
                  FROM cobros k
                  JOIN clientes c ON c.id = k.cliente_id
                  JOIN usuarios u ON u.id = k.vendedor_id
                  JOIN dispositivos d ON d.id = k.dispositivo_id
                 WHERE k.id = :id
                """
            ),
            {"id": cobro_id},
        )
    ).mappings().first()
    if cobro is None:
        return RedirectResponse("/panel/cobranza", status_code=status.HTTP_303_SEE_OTHER)

    aplicaciones = (
        await sesion.execute(
            text(
                """
                SELECT a.importe, a.aplicado_en, v.folio_local, v.folio_servidor,
                       x.importe_original, x.importe_pagado, x.saldo, x.estado,
                       x.fecha_emision, x.fecha_vencimiento
                  FROM cobros_aplicaciones a
                  JOIN cuentas_por_cobrar x ON x.venta_id = a.venta_id
                  JOIN ventas v ON v.id = a.venta_id
                 WHERE a.cobro_id = :id
                 ORDER BY x.fecha_vencimiento, x.fecha_emision
                """
            ),
            {"id": cobro_id},
        )
    ).mappings().all()

    cartera = (
        await sesion.execute(
            text(
                "SELECT saldo, saldo_vencido, facturas_abiertas, facturas_vencidas, "
                "       limite_credito, disponible, vencimiento_mas_antiguo "
                "  FROM v_cartera_cliente WHERE cliente_id = :c"
            ),
            {"c": cobro["cliente_id"]},
        )
    ).mappings().first()

    # La diferencia entre lo que el teléfono creía y lo que había. Es el número que
    # explica un cobro marcado: si el equipo traía $0 y el cliente debía $8,000, el
    # vendedor cobró a ciegas y la culpa es de la sincronización, no suya.
    desfase: Decimal | None = None
    if cobro["saldo_cache_disp"] is not None and cartera is not None:
        # El saldo de hoy ya tiene este cobro aplicado: se le vuelve a sumar para
        # compararlo con lo que el teléfono veía ANTES de cobrar.
        saldo_antes_real = Decimal(cartera["saldo"]) + Decimal(cobro["importe_aplicado"])
        desfase = saldo_antes_real - Decimal(cobro["saldo_cache_disp"])

    return render(
        peticion,
        "cobranza_detalle.html",
        {
            "cobro": cobro,
            "aplicaciones": aplicaciones,
            "cartera": cartera,
            "desfase": desfase,
            "explicacion": EXPLICACION_MOTIVOS,
            "en_arqueo": cobro["forma_pago"] in FORMAS_EN_ARQUEO,
            "puede_editar": actor.puede(PERMISO),
        },
        actor=actor,
        seccion="Cobranza",
    )


@router.post("/{cobro_id}/revisado")
async def marcar_revisado(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    cobro_id: uuid.UUID,
    csrf: str = Form(""),
    nota: str = Form(""),
) -> RedirectResponse:
    """Da por revisado un cobro marcado.

    No corrige nada, y eso es el punto. El dinero entró y el FIFO lo aplicó sobre
    la cartera real; lo que faltaba era que alguien lo mirara. Esto registra que
    alguien lo hizo, con su nombre, y saca el cobro de la lista de pendientes para
    que la lista siga significando algo.

    La nota se guarda en `revision_motivos` junto al motivo original en vez de
    borrarlo: por qué se marcó es parte del historial, y perderlo haría que la
    misma situación se volviera a investigar desde cero el mes que viene.
    """
    exigir_csrf(peticion, csrf)
    actor.exigir(PERMISO)

    limpia = (nota or "").strip()
    sello = f"revisado_por:{actor.usuario_id}"
    if limpia:
        sello = f"{sello}:{limpia[:200]}"

    await sesion.execute(
        text(
            "UPDATE cobros "
            "   SET requiere_revision = false, "
            "       revision_motivos = revision_motivos || :sello "
            " WHERE id = :id AND requiere_revision"
        ),
        {"id": cobro_id, "sello": [sello]},
    )
    await sesion.commit()
    return RedirectResponse(
        f"/panel/cobranza/{cobro_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/cartera/antiguedad", response_class=HTMLResponse)
async def antiguedad(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    ruta: str = "",
) -> HTMLResponse:
    """La cartera por antigüedad, cliente por cliente.

    Los tramos se calculan sobre `fecha_vencimiento`, no sobre la emisión: un
    cliente a 30 días no está vencido el día 15, y contarlo así haría que la
    pantalla gritara todos los días.
    """
    actor.exigir(PERMISO)

    filas = (
        await sesion.execute(
            text(
                """
                SELECT c.id, c.codigo, c.nombre_comercial, c.limite_credito,
                       r.codigo AS ruta, r.nombre AS ruta_nombre,
                       sum(x.saldo) AS saldo,
                       sum(x.saldo) FILTER (
                           WHERE x.fecha_vencimiento >= CURRENT_DATE) AS al_corriente,
                       sum(x.saldo) FILTER (
                           WHERE CURRENT_DATE - x.fecha_vencimiento BETWEEN 1 AND 15
                       ) AS t1_15,
                       sum(x.saldo) FILTER (
                           WHERE CURRENT_DATE - x.fecha_vencimiento BETWEEN 16 AND 30
                       ) AS t16_30,
                       sum(x.saldo) FILTER (
                           WHERE CURRENT_DATE - x.fecha_vencimiento BETWEEN 31 AND 60
                       ) AS t31_60,
                       sum(x.saldo) FILTER (
                           WHERE CURRENT_DATE - x.fecha_vencimiento > 60) AS mas_60,
                       min(x.fecha_vencimiento) AS mas_antigua,
                       count(*) AS facturas
                  FROM cuentas_por_cobrar x
                  JOIN clientes c ON c.id = x.cliente_id
                  LEFT JOIN rutas r ON r.id = c.ruta_id
                 WHERE x.estado IN ('abierta', 'parcial')
                   AND (:ruta = '' OR r.codigo = :ruta)
                 GROUP BY c.id, c.codigo, c.nombre_comercial, c.limite_credito,
                          r.codigo, r.nombre
                HAVING sum(x.saldo) <> 0
                 ORDER BY mas_60 DESC NULLS LAST, saldo DESC
                 LIMIT 500
                """
            ),
            {"ruta": ruta},
        )
    ).mappings().all()

    rutas = (
        await sesion.execute(
            text("SELECT codigo, nombre FROM rutas WHERE activo ORDER BY codigo")
        )
    ).mappings().all()

    # Los totales se suman aquí y no en SQL con ROLLUP: la lista está limitada a
    # 500 renglones y un total que no incluyera el resto mentiría. Si algún día la
    # cartera pasa de eso, el total tiene que salir de su propia consulta.
    campos = ("saldo", "al_corriente", "t1_15", "t16_30", "t31_60", "mas_60")
    totales = {
        campo: sum((f[campo] or Decimal(0)) for f in filas) for campo in campos
    }

    return render(
        peticion,
        "cobranza_antiguedad.html",
        {
            "filas": filas,
            "rutas": rutas,
            "ruta": ruta,
            "totales": totales,
            "completa": len(filas) < 500,
        },
        actor=actor,
        seccion="Cobranza",
    )
