"""Compras: proveedores, el costo del inventario y lo que se le debe a cada uno.

────────────────────────────────────────────────────────────────────────────
TRES PANTALLAS QUE CONTESTAN TRES PREGUNTAS QUE NO TENÍAN RESPUESTA
────────────────────────────────────────────────────────────────────────────
    ¿a quién le compramos?        → el catálogo de proveedores
    ¿cuánto dinero hay en el      → el valor del inventario, al promedio
     almacén?                       ponderado
    ¿a quién le debemos y          → la cartera de proveedores por
     cuándo vence?                   antigüedad

La tercera es el espejo de la cobranza y se lee igual: la antigüedad se cuenta
desde el VENCIMIENTO y no desde la emisión, así que un proveedor a 30 días no
aparece vencido el día 15.

────────────────────────────────────────────────────────────────────────────
DOS PERMISOS, PORQUE SON DOS MANOS
────────────────────────────────────────────────────────────────────────────
`compras.administrar` para el catálogo y los costos; `compras.pagar` para
registrar un pago. Recibir mercancía y pagarla son actos distintos: quien
captura la entrada registra lo que llegó, y quien paga mueve dinero de la
empresa. Que la misma persona pueda hacer las dos cosas sin que nadie más lo vea
es la receta de una factura inventada.

El supervisor tiene el primero —ya recibe mercancía, necesita capturar su
costo— y no el segundo.

────────────────────────────────────────────────────────────────────────────
UN PAGO SE APLICA A UNA CUENTA, SIN FIFO
────────────────────────────────────────────────────────────────────────────
Y es deliberadamente distinto de la cobranza. Un cobro del vendedor se aplica
FIFO porque el cliente paga «lo que debe» sin decir cuál factura: llega un abono
y hay que repartirlo. Un pago a proveedor es al revés — se paga LA factura
F-45821, con su transferencia y su referencia. Inventar un FIFO aquí repartiría
un pago entre facturas que nadie quiso pagar, y después nadie podría conciliar
contra el estado de cuenta del proveedor.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.api.admin.comun import (
    CapturaInvalida,
    SesionDep,
    leer_dinero,
    leer_entero,
    render,
    texto_o_nulo,
)
from app.api.admin.sesion_web import ActorWeb, exigir_csrf
from app.domain.compras import (
    SQL_CXP_POR_ANTIGUEDAD,
    SQL_VALOR_DE_INVENTARIO,
    estado_de_cuenta,
)

router = APIRouter(prefix="/panel/compras", tags=["panel"], include_in_schema=False)

PERMISO = "compras.administrar"
PERMISO_PAGAR = "compras.pagar"

FORMAS_DE_PAGO = ("efectivo", "transferencia", "cheque")

# Tope de días de crédito, igual que el CHECK de la tabla. Un año es generoso y
# 3650 es un dedazo que haría que la cuenta nunca apareciera como vencida.
DIAS_CREDITO_MAXIMO = 365


def _a_lista(*, error: str = "", guardado: str = "") -> RedirectResponse:
    cola = []
    if error:
        cola.append(f"error={quote(error)}")
    if guardado:
        cola.append(f"guardado={quote(guardado)}")
    destino = "/panel/compras" + (f"?{'&'.join(cola)}" if cola else "")
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


def _a_cuenta(entrada_id, *, error: str = "", guardado: str = "") -> RedirectResponse:
    cola = []
    if error:
        cola.append(f"error={quote(error)}")
    if guardado:
        cola.append(f"guardado={quote(guardado)}")
    destino = f"/panel/compras/cuenta/{entrada_id}" + (
        f"?{'&'.join(cola)}" if cola else ""
    )
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


# ===========================================================================
# El tablero de compras
# ===========================================================================
@router.get("", response_class=HTMLResponse)
async def ver(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    error: str = "",
    guardado: str = "",
) -> HTMLResponse:
    actor.exigir("inventario.ver")

    proveedores = (
        await sesion.execute(
            text(
                """
                SELECT p.id, p.codigo, p.nombre, p.rfc, p.contacto, p.telefono,
                       p.dias_credito, p.activo, p.notas,
                       COALESCE(c.saldo, 0)   AS saldo,
                       COALESCE(c.cuentas, 0) AS cuentas,
                       COALESCE(e.compras, 0) AS compras,
                       e.ultima_compra
                  FROM proveedores p
                  LEFT JOIN LATERAL (
                        SELECT sum(saldo) AS saldo, count(*) AS cuentas
                          FROM cuentas_por_pagar
                         WHERE proveedor_id = p.id
                           AND estado IN ('abierta','parcial')
                  ) c ON true
                  LEFT JOIN LATERAL (
                        SELECT count(*) AS compras, max(fecha_operativa) AS ultima_compra
                          FROM entradas
                         WHERE proveedor_id = p.id AND estado = 'confirmada'
                  ) e ON true
                 ORDER BY p.activo DESC, p.nombre
                """
            )
        )
    ).mappings().all()

    cartera = (
        await sesion.execute(text(SQL_CXP_POR_ANTIGUEDAD))
    ).mappings().all()

    # Las cuentas abiertas, ordenadas por lo que vence primero: es el orden en
    # que alguien decide qué pagar esta semana.
    cuentas = (
        await sesion.execute(
            text(
                """
                SELECT c.entrada_id, c.importe_original, c.importe_pagado,
                       c.saldo, c.estado, c.fecha_emision, c.fecha_vencimiento,
                       c.referencia, p.nombre AS proveedor, e.folio,
                       (c.fecha_vencimiento - CURRENT_DATE) AS dias
                  FROM cuentas_por_pagar c
                  JOIN proveedores p ON p.id = c.proveedor_id
                  JOIN entradas e ON e.id = c.entrada_id
                 WHERE c.estado IN ('abierta','parcial')
                 ORDER BY c.fecha_vencimiento, p.nombre
                 LIMIT 100
                """
            )
        )
    ).mappings().all()

    valor = (await sesion.execute(text(SQL_VALOR_DE_INVENTARIO))).mappings().all()

    return render(
        peticion,
        "compras.html",
        {
            "proveedores": proveedores,
            "cartera": cartera,
            "cuentas": cuentas,
            "valor": valor,
            "valor_total": sum(Decimal(v["valor"]) for v in valor),
            "sin_costo": sum(int(v["productos_sin_costo"]) for v in valor),
            "saldo_total": sum(Decimal(c["saldo"]) for c in cuentas),
            "vencido": sum(
                Decimal(c["saldo"]) for c in cuentas if int(c["dias"]) < 0
            ),
            "puede_editar": actor.puede(PERMISO),
            "puede_pagar": actor.puede(PERMISO_PAGAR),
            "hoy": date.today().isoformat(),
            "error": error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Compras",
    )


# ===========================================================================
# El catálogo
# ===========================================================================
@router.post("/proveedor")
async def guardar_proveedor(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    proveedor_id: Annotated[str, Form()] = "",
    codigo: Annotated[str, Form()] = "",
    nombre: Annotated[str, Form()] = "",
    rfc: Annotated[str, Form()] = "",
    contacto: Annotated[str, Form()] = "",
    telefono: Annotated[str, Form()] = "",
    dias_credito: Annotated[str, Form()] = "",
    notas: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Da de alta o actualiza un proveedor.

    El código es la llave con la que la gente lo busca, así que se normaliza a
    mayúsculas: `abarrotes` y `ABARROTES` serían dos proveedores distintos con
    la misma deuda repartida entre los dos.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    clave = (codigo or "").strip().upper()
    razon = (nombre or "").strip()
    if not clave or not razon:
        return _a_lista(error="El proveedor necesita código y nombre.")
    if len(clave) > 30:
        return _a_lista(error="El código es muy largo: máximo 30 caracteres.")

    try:
        dias = leer_entero(
            dias_credito, campo="Los días de crédito", maximo=DIAS_CREDITO_MAXIMO
        )
    except CapturaInvalida as e:
        return _a_lista(error=str(e))

    datos = {
        "codigo": clave,
        "nombre": razon[:160],
        # El RFC en mayúsculas y sin validar su forma: un CHECK rechazaría un
        # RFC extranjero justo cuando alguien captura una factura real, y el
        # dato no alimenta ningún cálculo.
        "rfc": (texto_o_nulo(rfc, maximo=20) or "").upper() or None,
        "contacto": texto_o_nulo(contacto, maximo=120),
        "telefono": texto_o_nulo(telefono, maximo=40),
        "dias": dias,
        "notas": texto_o_nulo(notas, maximo=500),
        "quien": actor.usuario_id,
    }

    if (proveedor_id or "").strip():
        try:
            datos["id"] = uuid.UUID(proveedor_id)
        except ValueError:
            return _a_lista(error="Ese proveedor no existe.")
        resultado = await sesion.execute(
            text(
                """
                UPDATE proveedores
                   SET codigo = :codigo, nombre = :nombre, rfc = :rfc,
                       contacto = :contacto, telefono = :telefono,
                       dias_credito = :dias, notas = :notas,
                       actualizado_en = now()
                 WHERE id = :id
                """
            ),
            datos,
        )
        if not resultado.rowcount:
            return _a_lista(error="Ese proveedor no existe.")
        await sesion.commit()
        return _a_lista(guardado=f"{datos['nombre']} actualizado.")

    existe = (
        await sesion.execute(
            text("SELECT nombre FROM proveedores WHERE codigo = :c"), {"c": clave}
        )
    ).scalar()
    if existe:
        return _a_lista(error=f"El código {clave} ya es de {existe}.")

    await sesion.execute(
        text(
            """
            INSERT INTO proveedores
                (codigo, nombre, rfc, contacto, telefono, dias_credito, notas,
                 creado_por)
            VALUES (:codigo, :nombre, :rfc, :contacto, :telefono, :dias, :notas,
                    :quien)
            """
        ),
        datos,
    )
    await sesion.commit()
    return _a_lista(
        guardado=(
            f"{datos['nombre']} dado de alta"
            + (f" con {dias} días de crédito." if dias else " de contado.")
        )
    )


@router.post("/proveedor/{proveedor_id}/estado")
async def cambiar_estado(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    proveedor_id: uuid.UUID,
    csrf: Annotated[str, Form()] = "",
):
    """Activa o desactiva un proveedor. No se borra, y eso es a propósito.

    Las entradas de hace dos años apuntan a él, y borrarlo dejaría una compra
    sin saber de quién fue. Desactivado deja de ofrecerse al capturar y sus
    cuentas por pagar siguen vivas — se le puede dejar de comprar y seguirle
    debiendo.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    fila = (
        await sesion.execute(
            text(
                "UPDATE proveedores SET activo = NOT activo, actualizado_en = now() "
                " WHERE id = :p RETURNING nombre, activo"
            ),
            {"p": proveedor_id},
        )
    ).mappings().first()
    if fila is None:
        return _a_lista(error="Ese proveedor no existe.")
    await sesion.commit()
    return _a_lista(
        guardado=f"{fila['nombre']}: {'activo' if fila['activo'] else 'inactivo'}."
    )


# ===========================================================================
# La cuenta por pagar y sus pagos
# ===========================================================================
@router.get("/cuenta/{entrada_id}", response_class=HTMLResponse)
async def cuenta(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    entrada_id: uuid.UUID,
    error: str = "",
    guardado: str = "",
) -> HTMLResponse:
    actor.exigir("inventario.ver")

    fila = (
        await sesion.execute(
            text(
                """
                SELECT c.*, p.nombre AS proveedor, p.codigo AS proveedor_codigo,
                       p.dias_credito, e.folio, e.fecha_operativa,
                       e.importe_total, a.codigo AS bodega
                  FROM cuentas_por_pagar c
                  JOIN proveedores p ON p.id = c.proveedor_id
                  JOIN entradas e ON e.id = c.entrada_id
                  JOIN almacenes a ON a.id = e.almacen_destino_id
                 WHERE c.entrada_id = :e
                """
            ),
            {"e": entrada_id},
        )
    ).mappings().first()
    if fila is None:
        return _a_lista(error="Esa cuenta por pagar no existe.")

    pagos = (
        await sesion.execute(
            text(
                """
                SELECT g.id, g.importe, g.forma_pago, g.referencia, g.fecha_pago,
                       g.nota, g.registrado_en, u.nombre AS registrado_por
                  FROM pagos_proveedor g
                  LEFT JOIN usuarios u ON u.id = g.registrado_por
                 WHERE g.entrada_id = :e
                 ORDER BY g.fecha_pago DESC, g.registrado_en DESC
                """
            ),
            {"e": entrada_id},
        )
    ).mappings().all()

    return render(
        peticion,
        "compras_cuenta.html",
        {
            "cuenta": fila,
            "pagos": pagos,
            "formas": FORMAS_DE_PAGO,
            "dias": (fila["fecha_vencimiento"] - date.today()).days,
            "puede_pagar": actor.puede(PERMISO_PAGAR),
            "hoy": date.today().isoformat(),
            "error": error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Compras",
    )


@router.post("/cuenta/{entrada_id}/pago")
async def registrar_pago(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    entrada_id: uuid.UUID,
    importe: Annotated[str, Form()] = "",
    forma_pago: Annotated[str, Form()] = "transferencia",
    referencia: Annotated[str, Form()] = "",
    fecha: Annotated[str, Form()] = "",
    nota: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Registra un pago contra ESTA cuenta. Nunca más que el saldo.

    El tope no es burocracia: un pago mayor que el saldo violaría el CHECK
    `pago_no_excede_el_original` y el error que PostgreSQL devuelve no le dice
    nada a quien está capturando una transferencia. Si de verdad se le pagó más
    de lo que se le debía, eso es un anticipo y no un pago de esta factura — y
    los anticipos no están construidos, así que es mejor decirlo que guardarlo
    mal.
    """
    actor.exigir(PERMISO_PAGAR)
    exigir_csrf(peticion, csrf)

    if forma_pago not in FORMAS_DE_PAGO:
        return _a_cuenta(entrada_id, error="Falta la forma de pago.")

    # `FOR UPDATE` sobre la cuenta: dos pagos capturados a la vez leerían el
    # mismo saldo y los dos pasarían la validación, dejando la cuenta pagada de
    # más — y ahí sí reventaría el CHECK, después de cobrar el dinero.
    cuenta_fila = (
        await sesion.execute(
            text(
                "SELECT importe_original, importe_pagado, saldo, estado "
                "  FROM cuentas_por_pagar WHERE entrada_id = :e FOR UPDATE"
            ),
            {"e": entrada_id},
        )
    ).mappings().first()
    if cuenta_fila is None:
        return _a_lista(error="Esa cuenta por pagar no existe.")
    if cuenta_fila["estado"] == "liquidada":
        return _a_cuenta(entrada_id, error="Esta cuenta ya está liquidada.")
    if cuenta_fila["estado"] == "cancelada":
        return _a_cuenta(entrada_id, error="Esta cuenta está cancelada.")

    try:
        monto = leer_dinero(importe, campo="El importe del pago")
    except CapturaInvalida as e:
        return _a_cuenta(entrada_id, error=str(e))
    if monto <= 0:
        return _a_cuenta(entrada_id, error="El pago tiene que ser mayor que cero.")

    saldo = Decimal(cuenta_fila["saldo"])
    if monto > saldo:
        return _a_cuenta(
            entrada_id,
            error=f"El pago de ${monto:,.2f} excede el saldo de ${saldo:,.2f}. "
            "Si se le pagó de más, eso es un anticipo y no un pago de esta "
            "factura — los anticipos no están construidos.",
        )

    hoy = date.today()
    try:
        cuando = date.fromisoformat(fecha) if fecha else hoy
    except ValueError:
        cuando = hoy
    if cuando > hoy:
        return _a_cuenta(
            entrada_id, error="La fecha del pago no puede ser futura."
        )

    await sesion.execute(
        text(
            """
            INSERT INTO pagos_proveedor
                (entrada_id, importe, forma_pago, referencia, fecha_pago, nota,
                 registrado_por)
            VALUES (:e, :importe, :forma, :ref, :fecha, :nota, :quien)
            """
        ),
        {
            "e": entrada_id,
            "importe": monto,
            "forma": forma_pago,
            "ref": texto_o_nulo(referencia, maximo=80),
            "fecha": cuando,
            "nota": texto_o_nulo(nota, maximo=300),
            "quien": actor.usuario_id,
        },
    )

    pagado = (Decimal(cuenta_fila["importe_pagado"]) + monto).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    await sesion.execute(
        text(
            """
            UPDATE cuentas_por_pagar
               SET importe_pagado = :pagado, estado = :estado,
                   actualizado_en = now()
             WHERE entrada_id = :e
            """
        ),
        {
            "e": entrada_id,
            "pagado": pagado,
            "estado": estado_de_cuenta(
                Decimal(cuenta_fila["importe_original"]), pagado
            ),
        },
    )
    await sesion.commit()

    resto = Decimal(cuenta_fila["importe_original"]) - pagado
    return _a_cuenta(
        entrada_id,
        guardado=(
            f"Pago de ${monto:,.2f} registrado. "
            + ("Cuenta liquidada." if resto <= 0 else f"Queda ${resto:,.2f}.")
        ),
    )
