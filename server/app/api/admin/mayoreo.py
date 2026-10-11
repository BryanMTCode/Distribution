"""Ventas de mayoreo desde el panel (ADR 0002 §105).

«El administrador puede hacer ventas a mayoreo al precio que él decida, y los
clientes él los maneja.» La lógica vive en `app/infra/mayoreo.py`, la misma que
usa la app; aquí solo se lee el formulario y se dibuja.

Sin JavaScript, como todo el panel: la venta nueva trae renglones en blanco —el
artículo con su presentación y su precio de lista en la misma opción—, y un
precio en blanco toma el de la lista.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.admin.comun import CapturaInvalida, SesionDep, leer_precio, render
from app.api.admin.periodo import PERIODOS, periodo_pedido
from app.api.admin.sesion_web import ActorWeb, exigir_csrf
from app.infra import mayoreo
from app.infra.mayoreo import MayoreoInvalido, RenglonPedido

router = APIRouter(prefix="/panel/mayoreo", tags=["panel"], include_in_schema=False)

# Cuántos renglones en blanco trae la venta nueva. Una venta más grande se parte
# en dos: es mejor que una página con cien selectores.
RENGLONES_EN_BLANCO = 12


def _a(ruta: str, **que: str) -> RedirectResponse:
    extra = "&".join(f"{k}={quote(v)}" for k, v in que.items() if v)
    separador = "&" if "?" in ruta else "?"
    return RedirectResponse(
        f"{ruta}{separador + extra if extra else ''}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("", response_class=HTMLResponse)
async def listar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    periodo: str = "",
    desde: str = "",
    hasta: str = "",
    error: str = "",
    guardado: str = "",
) -> HTMLResponse:
    actor.exigir(mayoreo.PERMISO)
    elegido = await periodo_pedido(sesion, periodo or "mes", desde, hasta or desde)
    ventas = await mayoreo.ventas_del_periodo(sesion, elegido.inicio, elegido.fin)
    vivas = [v for v in ventas if v["estado"] != "cancelada"]
    return render(
        peticion,
        "mayoreo.html",
        {
            "ventas": vivas,
            "canceladas": [v for v in ventas if v["estado"] == "cancelada"],
            "total": sum((Decimal(v["total"]) for v in vivas), Decimal(0)),
            "piezas": sum((Decimal(v["piezas"] or 0) for v in vivas), Decimal(0)),
            "clientes": await mayoreo.clientes_de_mayoreo(sesion),
            "periodo": elegido,
            "periodos": PERIODOS,
            "error": error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Ventas",
    )


@router.post("/clientes")
async def alta_de_cliente(peticion: Request, actor: ActorWeb, sesion: SesionDep):
    actor.exigir(mayoreo.PERMISO)
    formulario = await peticion.form()
    exigir_csrf(peticion, str(formulario.get("csrf") or ""))
    volver = str(formulario.get("volver") or "/panel/mayoreo")
    if not volver.startswith("/panel/mayoreo"):
        volver = "/panel/mayoreo"
    try:
        nuevo = await mayoreo.alta_de_cliente(
            sesion,
            nombre=str(formulario.get("nombre") or ""),
            telefono=str(formulario.get("telefono") or ""),
            calle=str(formulario.get("calle") or ""),
            colonia=str(formulario.get("colonia") or ""),
            quien=actor.usuario_id,
        )
    except MayoreoInvalido as e:
        await sesion.rollback()
        return _a(volver, error=str(e))
    await sesion.commit()
    if volver.startswith("/panel/mayoreo/nueva"):
        return _a(f"/panel/mayoreo/nueva?cliente={nuevo['id']}",
                  guardado=f"Cliente {nuevo['codigo']} dado de alta.")
    return _a(volver, guardado=f"Cliente {nuevo['codigo']} · {nuevo['nombre_comercial']} "
                               "dado de alta.")


@router.get("/nueva", response_class=HTMLResponse)
async def nueva(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    bodega: str = "",
    cliente: str = "",
    error: str = "",
    guardado: str = "",
) -> HTMLResponse:
    actor.exigir(mayoreo.PERMISO)
    bodegas = await mayoreo.bodegas(sesion)
    elegida = next((b for b in bodegas if str(b["id"]) == bodega), bodegas[0] if bodegas else None)
    surtido = await mayoreo.surtido(sesion, elegida["id"]) if elegida else []
    return render(
        peticion,
        "mayoreo_nueva.html",
        {
            "bodegas": bodegas,
            "bodega": elegida,
            "surtido": surtido,
            "clientes": await mayoreo.clientes_de_mayoreo(sesion),
            "cliente": cliente,
            "renglones": range(RENGLONES_EN_BLANCO),
            "error": error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Ventas",
    )


@router.post("/nueva")
async def vender(peticion: Request, actor: ActorWeb, sesion: SesionDep):
    actor.exigir(mayoreo.PERMISO)
    formulario = await peticion.form()
    exigir_csrf(peticion, str(formulario.get("csrf") or ""))
    bodega = str(formulario.get("almacen_id") or "")
    cliente = str(formulario.get("cliente_id") or "")
    volver = f"/panel/mayoreo/nueva?bodega={bodega}&cliente={cliente}"
    try:
        if not cliente:
            raise MayoreoInvalido("Escoge el cliente.")
        renglones = []
        for i in range(RENGLONES_EN_BLANCO):
            articulo = str(formulario.get(f"articulo_{i}") or "")
            cantidad = str(formulario.get(f"cantidad_{i}") or "").strip()
            precio = str(formulario.get(f"precio_{i}") or "").strip()
            if not articulo and not cantidad:
                continue
            if not articulo or not cantidad:
                raise MayoreoInvalido(f"Al renglón {i + 1} le falta el artículo o la cantidad.")
            producto, unidad, lista = (articulo.split("|") + ["", ""])[:3]
            try:
                bultos = Decimal(cantidad.replace(",", ""))
            except ArithmeticError as e:
                raise MayoreoInvalido(f"Renglón {i + 1}: la cantidad no es un número.") from e
            # Un precio en blanco toma el de la lista: «al precio que él decida»
            # incluye decidir que el de siempre está bien.
            if precio:
                valor = leer_precio(precio, campo=f"El precio del renglón {i + 1}")
            elif lista:
                valor = Decimal(lista)
            else:
                raise MayoreoInvalido(f"Renglón {i + 1}: ese artículo no tiene precio de lista; "
                                      "escribe el precio.")
            renglones.append(RenglonPedido(uuid.UUID(producto), unidad, bultos, valor))
        venta_id, folio = await mayoreo.vender(
            sesion,
            cliente_id=uuid.UUID(cliente),
            almacen_id=uuid.UUID(bodega),
            renglones=renglones,
            forma_pago=str(formulario.get("forma_pago") or "efectivo"),
            referencia_pago=str(formulario.get("referencia_pago") or ""),
            nota=str(formulario.get("nota") or ""),
            quien=actor.usuario_id,
        )
    except (MayoreoInvalido, CapturaInvalida, ValueError) as e:
        await sesion.rollback()
        return _a(volver, error=str(e))
    await sesion.commit()
    return _a(f"/panel/mayoreo/{venta_id}", guardado=f"Venta {folio} guardada.")


@router.get("/{venta_id}", response_class=HTMLResponse)
async def detalle(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    venta_id: uuid.UUID,
    error: str = "",
    guardado: str = "",
) -> HTMLResponse:
    actor.exigir(mayoreo.PERMISO)
    venta = await mayoreo.detalle(sesion, venta_id)
    if venta is None:
        return _a("/panel/mayoreo", error="Esa venta no existe.")
    return render(
        peticion,
        "mayoreo_detalle.html",
        {"venta": venta, "ticket": mayoreo.ticket(venta), "error": error, "guardado": guardado},
        actor=actor,
        seccion="Ventas",
    )


@router.post("/{venta_id}/cancelar")
async def cancelar(peticion: Request, actor: ActorWeb, sesion: SesionDep, venta_id: uuid.UUID):
    actor.exigir(mayoreo.PERMISO)
    formulario = await peticion.form()
    exigir_csrf(peticion, str(formulario.get("csrf") or ""))
    try:
        folio = await mayoreo.cancelar(
            sesion, venta_id, motivo=str(formulario.get("motivo") or ""), quien=actor.usuario_id
        )
    except MayoreoInvalido as e:
        await sesion.rollback()
        return _a(f"/panel/mayoreo/{venta_id}", error=str(e))
    await sesion.commit()
    return _a(f"/panel/mayoreo/{venta_id}",
              guardado=f"Venta {folio} cancelada: la mercancía volvió a la bodega.")
