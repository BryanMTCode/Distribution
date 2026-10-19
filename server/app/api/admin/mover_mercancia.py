"""Mover mercancía de la bodega a un camión, y de un camión a la bodega (ADR 0002 §135).

«Quiero en el dashboard poder mover mercancía a los camiones y viceversa.»

Una pestaña de Inventario: se elige el camión, hacia dónde y la bodega; abajo sale
lo que hay en el origen, y se escribe cuánto se mueve de cada artículo, en cajas o
en piezas, como en la carga. Lo que hace por dentro —dos ajustes atados que el
teléfono recibe como diferencia— está en `app/infra/traspaso_camion.py`.

Si algo no cuadra (un dedazo, más de lo que hay) no se mueve nada y la pantalla
regresa con lo que se escribió, para corregir solo ese renglón.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.api.admin.comun import (
    MAXIMO_CAMPOS_DE_RENGLONES,
    CapturaInvalida,
    SesionDep,
    agrupar_por_familia,
    leer_entero,
    render,
    sin_decimales,
)
from app.api.admin.sesion_web import ActorWeb, exigir_csrf
from app.domain.importes import cantidad_base
from app.infra import traspaso_camion

router = APIRouter(prefix="/panel/inventario/mover", tags=["panel"], include_in_schema=False)

# Ver lo que hay y moverlo: los dos (el mismo par que su renglón del menú).
PERMISOS = ("inventario.ver", "inventario.ajustar")
SENTIDOS = {"al_camion": "De la bodega al camión", "a_bodega": "Del camión a la bodega"}


@router.get("", response_class=HTMLResponse)
async def pantalla(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    camion: str = "",
    bodega: str = "",
    sentido: str = "al_camion",
    guardado: str = "",
    error: str = "",
) -> HTMLResponse:
    for permiso in PERMISOS:
        actor.exigir(permiso)
    return await _pintar(
        peticion, actor, sesion, camion=camion, bodega=bodega, sentido=sentido,
        guardado=guardado, error=error,
    )


@router.post("")
async def mover(peticion: Request, actor: ActorWeb, sesion: SesionDep):
    """Mueve lo que traiga cantidad. Todo o nada: ver `traspaso_camion.mover`."""
    for permiso in PERMISOS:
        actor.exigir(permiso)
    formulario = await peticion.form(max_fields=MAXIMO_CAMPOS_DE_RENGLONES)
    exigir_csrf(peticion, str(formulario.get("csrf", "")))

    camion = str(formulario.get("camion", ""))
    bodega = str(formulario.get("bodega", ""))
    sentido = str(formulario.get("sentido", "al_camion"))
    nota = str(formulario.get("nota", ""))
    capturado = {
        clave: str(valor)
        for clave, valor in formulario.multi_items()
        if clave.startswith(("cantidad_", "unidad_"))
    }

    async def de_regreso(error: str) -> HTMLResponse:
        return await _pintar(
            peticion, actor, sesion, camion=camion, bodega=bodega, sentido=sentido,
            error=error, capturado=capturado, nota=nota,
        )

    if sentido not in SENTIDOS:
        return await de_regreso("Elige hacia dónde se mueve.")
    try:
        camion_id, bodega_id = uuid.UUID(camion), uuid.UUID(bodega)
    except ValueError:
        return await de_regreso("Elige el camión y la bodega.")

    try:
        cantidades = await _leer_cantidades(sesion, capturado)
    except CapturaInvalida as e:
        return await de_regreso(str(e))

    try:
        hecho = await traspaso_camion.mover(
            sesion,
            camion_id=camion_id,
            bodega_id=bodega_id,
            al_camion=sentido == "al_camion",
            cantidades=cantidades,
            nota=nota,
            quien=actor.usuario_id,
        )
    except traspaso_camion.MovimientoRechazado as e:
        await sesion.rollback()
        return await de_regreso(str(e))
    await sesion.commit()

    return RedirectResponse(
        "/panel/inventario/mover?"
        + urlencode({"camion": camion, "bodega": bodega, "sentido": sentido})
        + f"&guardado={quote(hecho.mensaje)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def _leer_cantidades(sesion, capturado: dict[str, str]) -> dict[uuid.UUID, Decimal]:
    """Cada renglón con cantidad, en unidad base. Bultos enteros en la
    presentación elegida, como en la carga; si un renglón está mal, se dicen
    todos los que lo están."""
    pedidos: list[tuple[uuid.UUID, str, str]] = []
    for clave, valor in capturado.items():
        if not clave.startswith("cantidad_") or not valor.strip():
            continue
        try:
            producto_id = uuid.UUID(clave.removeprefix("cantidad_"))
        except ValueError:
            continue
        pedidos.append((producto_id, valor, capturado.get(f"unidad_{producto_id}", "")))

    cantidades: dict[uuid.UUID, Decimal] = {}
    malos: list[str] = []
    for producto_id, valor, unidad in pedidos:
        fila = (
            await sesion.execute(
                text(
                    "SELECT p.nombre, u.factor "
                    "  FROM productos p "
                    "  LEFT JOIN producto_unidades u ON u.producto_id = p.id "
                    "       AND u.unidad_codigo = :u AND u.activo "
                    " WHERE p.id = :p"
                ),
                {"p": producto_id, "u": unidad},
            )
        ).mappings().first()
        if fila is None:
            malos.append("un artículo que ya no existe")
            continue
        if fila["factor"] is None:
            malos.append(f"{fila['nombre']}: no tiene la presentación {unidad or '—'}")
            continue
        try:
            bultos = leer_entero(valor, campo=fila["nombre"], maximo=100000)
        except CapturaInvalida as e:
            malos.append(str(e).rstrip("."))
            continue
        if bultos:
            cantidades[producto_id] = cantidad_base(Decimal(bultos), Decimal(fila["factor"]))
    if malos:
        raise CapturaInvalida(f"Revisa: {'; '.join(malos)}. No se movió nada.")
    return cantidades


async def _pintar(
    peticion: Request,
    actor,
    sesion,
    *,
    camion: str,
    bodega: str,
    sentido: str,
    guardado: str = "",
    error: str = "",
    capturado: dict[str, str] | None = None,
    nota: str = "",
) -> HTMLResponse:
    camiones = (
        await sesion.execute(
            text(
                "SELECT a.id, a.codigo, a.nombre, u.nombre AS responsable "
                "  FROM almacenes a LEFT JOIN usuarios u ON u.id = a.responsable_id "
                " WHERE a.tipo = 'camion' AND a.activo ORDER BY a.codigo"
            )
        )
    ).mappings().all()
    bodegas = (
        await sesion.execute(
            text("SELECT id, codigo, nombre FROM almacenes "
                 " WHERE tipo = 'bodega' AND activo ORDER BY codigo")
        )
    ).mappings().all()

    def elegir(opciones, crudo: str, *, sola_si_hay_una: bool):
        try:
            buscado = uuid.UUID(crudo)
        except ValueError:
            buscado = None
        elegido = next((o for o in opciones if o["id"] == buscado), None)
        if elegido is None and opciones and (len(opciones) == 1 or not sola_si_hay_una):
            elegido = opciones[0]
        return elegido

    el_camion = elegir(camiones, camion, sola_si_hay_una=True)
    la_bodega = elegir(bodegas, bodega, sola_si_hay_una=False)
    if sentido not in SENTIDOS:
        sentido = "al_camion"

    filas: list[dict] = []
    origen = None
    if el_camion and la_bodega:
        origen = la_bodega if sentido == "al_camion" else el_camion
        for f in await traspaso_camion.lo_que_hay(sesion, origen["id"]):
            presentaciones = f["presentaciones"] or []
            # La más grande por omisión, como en la carga: en la bodega se cuenta
            # por caja.
            f["por_omision"] = presentaciones[0]["unidad"] if presentaciones else None
            f["en_bultos"] = _en_bultos(Decimal(f["cantidad"]), presentaciones, f["unidad_base"])
            filas.append(f)

    recientes = (
        await sesion.execute(
            text(
                "SELECT j.folio, j.creado_en, -j.delta AS cantidad, j.nota, "
                "       p.nombre AS producto, p.unidad_base, u.nombre AS quien "
                "  FROM ajustes_camion j "
                "  JOIN productos p ON p.id = j.producto_id "
                "  JOIN usuarios u ON u.id = j.usuario_id "
                " WHERE j.tipo = 'traspaso' AND j.pareja_id IS NULL "
                " ORDER BY j.creado_en DESC, j.folio DESC LIMIT 15"
            )
        )
    ).mappings().all()

    return render(
        peticion,
        "mover_mercancia.html",
        {
            "camiones": camiones,
            "bodegas": bodegas,
            "camion": el_camion,
            "bodega": la_bodega,
            "sentido": sentido,
            "sentidos": SENTIDOS,
            "origen": origen,
            "destino": (el_camion if origen is la_bodega else la_bodega) if origen else None,
            "grupos": agrupar_por_familia(filas),
            "capturado": capturado or {},
            "nota": nota,
            "recientes": recientes,
            "guardado": guardado,
            "error": error,
        },
        actor=actor,
        seccion="Inventario",
    )


def _en_bultos(cantidad: Decimal, presentaciones: list[dict], unidad_base: str) -> str:
    """«31 CAJA + 16 PZA»: lo que hay, como se ve en el anaquel. Vacío si el
    artículo no tiene presentación más grande que la pieza."""
    if not presentaciones or Decimal(presentaciones[0]["factor"]) <= 1:
        return ""
    caja = presentaciones[0]
    enteras, resto = divmod(cantidad, Decimal(caja["factor"]))
    texto = f"{int(enteras)} {caja['unidad']}"
    return texto + (f" + {sin_decimales(resto)} {unidad_base}" if resto else "")
