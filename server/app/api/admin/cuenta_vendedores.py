"""La cuenta de cada vendedor: lo que debe, de dónde sale y cómo lo paga.

────────────────────────────────────────────────────────────────────────────
POR QUÉ EXISTE
────────────────────────────────────────────────────────────────────────────
El Corte del día calculaba el faltante con el nombre del vendedor y ahí
terminaba. Sin un lugar donde acumularlo, en un mes nadie sabía cuánto debía
cada uno y el faltante quedaba perdonado de hecho, sin que nadie lo decidiera.

Los cargos nacen solos al cerrar el Corte (`app/infra/cuenta_vendedor.py`), la
mercancía a COSTO —regla de la dirección—. Aquí se ven, y aquí se registra cómo
bajan:

    descuento de nómina, pago   abonos: el vendedor pagó
    condonar                    la empresa decide no cobrarlo: con motivo y nombre
    cargo a mano                cualquier otro cargo, con su concepto

────────────────────────────────────────────────────────────────────────────
LO QUE NO SE PUEDE HACER
────────────────────────────────────────────────────────────────────────────
Editar o borrar un movimiento. Es un libro (la base lo impide): un cargo
equivocado se compensa con una condonación que dice por qué, y los dos quedan a
la vista. Tampoco se abona más de lo que debe: un saldo a favor del vendedor no
es algo que este libro deba fabricar por un dedazo.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from starlette import status

from app.api.admin.comun import CapturaInvalida, SesionDep, dinero, leer_dinero, render
from app.api.admin.sesion_web import ActorWeb, exigir_csrf
from app.infra.cuenta_vendedor import ORIGENES, saldo_de

router = APIRouter(
    prefix="/panel/vendedores/cuenta", tags=["panel"], include_in_schema=False
)

PERMISO_VER = "vendedores.cuenta_ver"
PERMISO_MOVER = "vendedores.cuenta_mover"

# Lo que una persona puede registrar desde la pantalla, y de qué tipo es.
MOVIMIENTOS_A_MANO = {
    "descuento_nomina": "abono",
    "pago": "abono",
    "condonacion": "condonacion",
    "cargo_manual": "cargo",
}


@router.get("", response_class=HTMLResponse)
async def listar(peticion: Request, actor: ActorWeb, sesion: SesionDep) -> HTMLResponse:
    """Todos los vendedores con su saldo, el que más debe arriba."""
    actor.exigir(PERMISO_VER)

    filas = (
        await sesion.execute(
            text(
                """
                SELECT u.id, u.codigo, u.nombre, u.activo,
                       COALESCE(c.cargos, 0) AS cargos,
                       COALESCE(c.abonos, 0) AS abonos,
                       COALESCE(c.condonado, 0) AS condonado,
                       COALESCE(c.saldo, 0) AS saldo,
                       c.ultimo_movimiento
                  FROM usuarios u
                  LEFT JOIN v_cuenta_vendedor c ON c.vendedor_id = u.id
                 WHERE u.rol_codigo = 'vendedor'
                   AND (u.activo OR COALESCE(c.saldo, 0) <> 0)
                 ORDER BY COALESCE(c.saldo, 0) DESC, u.nombre
                """
            )
        )
    ).mappings().all()

    return render(
        peticion,
        "cuenta_vendedores.html",
        {
            "filas": filas,
            "total": sum((Decimal(f["saldo"]) for f in filas), Decimal(0)),
        },
        actor=actor,
        seccion="Cuenta de vendedores",
    )


@router.get("/{vendedor_id}", response_class=HTMLResponse)
async def detalle(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    vendedor_id: uuid.UUID,
    guardado: str = "",
    error: str = "",
) -> HTMLResponse:
    """Los movimientos de un vendedor, con el saldo después de cada uno."""
    actor.exigir(PERMISO_VER)

    vendedor = (
        await sesion.execute(
            text("SELECT id, codigo, nombre FROM usuarios WHERE id = :v"),
            {"v": vendedor_id},
        )
    ).mappings().first()
    if vendedor is None:
        return RedirectResponse(
            "/panel/vendedores/cuenta", status_code=status.HTTP_303_SEE_OTHER
        )

    movimientos = (
        await sesion.execute(
            text(
                """
                SELECT m.id, m.tipo, m.origen, m.importe, m.fecha, m.concepto,
                       m.detalle, m.registrado_en, m.liquidacion_id, m.cobro_id,
                       l.folio AS corte, k.folio_local AS recibo,
                       r.nombre AS registro,
                       sum(CASE WHEN m.tipo = 'cargo' THEN m.importe ELSE -m.importe END)
                         OVER (ORDER BY m.fecha, m.registrado_en, m.id) AS saldo
                  FROM cuenta_vendedor m
                  LEFT JOIN liquidaciones l ON l.id = m.liquidacion_id
                  LEFT JOIN cobros k ON k.id = m.cobro_id
                  LEFT JOIN usuarios r ON r.id = m.registrado_por
                 WHERE m.vendedor_id = :v
                 ORDER BY m.fecha DESC, m.registrado_en DESC, m.id DESC
                 LIMIT 300
                """
            ),
            {"v": vendedor_id},
        )
    ).mappings().all()

    return render(
        peticion,
        "cuenta_vendedor.html",
        {
            "vendedor": vendedor,
            "movimientos": [
                {
                    **dict(m),
                    "detalle": m["detalle"]
                    if not isinstance(m["detalle"], str)
                    else json.loads(m["detalle"]),
                }
                for m in movimientos
            ],
            "saldo": await saldo_de(sesion, vendedor_id),
            "origenes": ORIGENES,
            "puede_mover": actor.puede(PERMISO_MOVER),
            "guardado": guardado,
            "error": error,
            "hoy": datetime.now(UTC).date(),
        },
        actor=actor,
        seccion="Cuenta de vendedores",
    )


@router.post("/{vendedor_id}/movimiento")
async def registrar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    vendedor_id: uuid.UUID,
    csrf: str = Form(""),
    origen: str = Form(""),
    importe: str = Form(""),
    concepto: str = Form(""),
    fecha: str = Form(""),
) -> RedirectResponse:
    """Un abono, una condonación o un cargo a mano."""
    exigir_csrf(peticion, csrf)
    actor.exigir(PERMISO_MOVER)

    tipo = MOVIMIENTOS_A_MANO.get(origen)
    if tipo is None:
        return _volver(vendedor_id, error="Elige qué vas a registrar.")

    try:
        cantidad = leer_dinero(importe, campo="El importe")
    except CapturaInvalida as e:
        return _volver(vendedor_id, error=str(e))
    if cantidad <= 0:
        return _volver(vendedor_id, error="El importe tiene que ser mayor que cero.")

    nota = (concepto or "").strip()
    if not nota:
        return _volver(
            vendedor_id,
            error="Escribe el concepto: «quincena del 15/10», «se le perdonó por "
            "la caja que llegó rota de la bodega».",
        )

    try:
        dia = datetime.strptime(fecha, "%Y-%m-%d").date() if fecha else datetime.now(UTC).date()
    except ValueError:
        return _volver(vendedor_id, error="La fecha no se entiende.")

    # Bloquear la cuenta del vendedor mientras se decide: dos abonos simultáneos
    # leerían el mismo saldo y entre los dos podrían dejarlo a favor del vendedor.
    await sesion.execute(
        text("SELECT 1 FROM usuarios WHERE id = :v FOR UPDATE"), {"v": vendedor_id}
    )
    if tipo != "cargo":
        saldo = await saldo_de(sesion, vendedor_id)
        if cantidad > saldo:
            return _volver(
                vendedor_id,
                error=f"Debe {dinero(saldo)} y quieres bajar {dinero(cantidad)}. Un "
                "abono no puede dejarle saldo a favor: si de verdad pagó de más, "
                "registra solo lo que debía.",
            )

    await sesion.execute(
        text(
            """
            INSERT INTO cuenta_vendedor
              (vendedor_id, tipo, origen, importe, fecha, concepto, registrado_por)
            VALUES (:v, :tipo, :origen, :importe, :fecha, :concepto, :quien)
            """
        ),
        {
            "v": vendedor_id,
            "tipo": tipo,
            "origen": origen,
            "importe": cantidad,
            "fecha": dia,
            "concepto": nota[:300],
            "quien": actor.usuario_id,
        },
    )
    await sesion.execute(
        text(
            """
            INSERT INTO auditoria (entidad, entidad_id, accion, usuario_id, motivo,
                                   datos_despues, ocurrido_en)
            VALUES ('cuenta_vendedor', :v, :accion, :quien, :motivo,
                    CAST(:despues AS jsonb), now())
            """
        ),
        {
            "v": vendedor_id,
            "accion": origen,
            "quien": actor.usuario_id,
            "motivo": nota[:300],
            "despues": json.dumps({"importe": str(cantidad), "fecha": dia.isoformat()}),
        },
    )
    await sesion.commit()

    return _volver(
        vendedor_id,
        guardado=f"{ORIGENES[origen]} por {dinero(cantidad)} registrado. "
        f"Ahora debe {dinero(await saldo_de(sesion, vendedor_id))}.",
    )


def _volver(vendedor_id, *, guardado: str = "", error: str = "") -> RedirectResponse:
    destino = f"/panel/vendedores/cuenta/{vendedor_id}"
    if guardado:
        destino += f"?guardado={quote(guardado)}"
    elif error:
        destino += f"?error={quote(error)}"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)
