"""Ajustar el inventario de una bodega o un camión contando, de un jalón (ADR 0002 §142).

Una pestaña de Inventario: se elige el almacén, sale todo el catálogo con lo que
dice el sistema, y se escribe lo que hay —en cajas y piezas— de los que se
contaron. Lo que hace por dentro está en `app/infra/ajuste_por_conteo.py`.

Si algo no cuadra (un dedazo, la nota) no se guarda nada y la pantalla regresa
con lo que se escribió.
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
from app.infra import ajuste_por_conteo

router = APIRouter(prefix="/panel/inventario/ajustar", tags=["panel"], include_in_schema=False)

# Ver lo que hay y ajustarlo: los dos (el mismo par que su renglón del menú).
PERMISOS = ("inventario.ver", "inventario.ajustar")
NOTA_POR_OMISION = "Conteo para arrancar la operación"


@router.get("", response_class=HTMLResponse)
async def pantalla(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    almacen: str = "",
    buscar: str = "",
    guardado: str = "",
    error: str = "",
) -> HTMLResponse:
    for permiso in PERMISOS:
        actor.exigir(permiso)
    return await _pintar(
        peticion, actor, sesion, almacen=almacen, buscar=buscar, guardado=guardado, error=error
    )


@router.post("")
async def guardar(peticion: Request, actor: ActorWeb, sesion: SesionDep):
    """Guarda el conteo. Todo o nada: ver `ajuste_por_conteo.aplicar_conteo`."""
    for permiso in PERMISOS:
        actor.exigir(permiso)
    formulario = await peticion.form(max_fields=MAXIMO_CAMPOS_DE_RENGLONES)
    exigir_csrf(peticion, str(formulario.get("csrf", "")))

    almacen = str(formulario.get("almacen", ""))
    buscar = str(formulario.get("buscar", ""))
    nota = str(formulario.get("nota", ""))
    capturado = {
        clave: str(valor)
        for clave, valor in formulario.multi_items()
        if clave.startswith(("cajas_", "piezas_"))
    }

    async def de_regreso(error: str) -> HTMLResponse:
        return await _pintar(
            peticion, actor, sesion, almacen=almacen, buscar=buscar, error=error,
            capturado=capturado, nota=nota,
        )

    try:
        almacen_id = uuid.UUID(almacen)
    except ValueError:
        return await de_regreso("Elige una bodega o un camión.")
    try:
        conteos = await _leer_conteos(sesion, capturado)
    except CapturaInvalida as e:
        return await de_regreso(str(e))
    try:
        hecho = await ajuste_por_conteo.aplicar_conteo(
            sesion, almacen_id=almacen_id, conteos=conteos, nota=nota, quien=actor.usuario_id
        )
    except ajuste_por_conteo.ConteoRechazado as e:
        await sesion.rollback()
        return await de_regreso(str(e))
    await sesion.commit()

    consulta = {"almacen": almacen}
    if buscar.strip():
        consulta["buscar"] = buscar.strip()
    return RedirectResponse(
        f"/panel/inventario/ajustar?{urlencode(consulta)}&guardado={quote(hecho.mensaje)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def _leer_conteos(sesion, capturado: dict[str, str]) -> dict[uuid.UUID, Decimal]:
    """Lo contado de cada renglón con algo escrito, en unidad base: cajas por su
    factor más piezas. El factor sale del catálogo, no del formulario. Si un
    renglón está mal, se dicen todos los que lo están."""
    ids: set[uuid.UUID] = set()
    for clave, valor in capturado.items():
        if not valor.strip():
            continue
        try:
            ids.add(uuid.UUID(clave.split("_", 1)[1]))
        except ValueError:
            continue
    if not ids:
        return {}
    filas = {
        f["id"]: f
        for f in (
            await sesion.execute(
                text(
                    "SELECT p.id, p.nombre, "
                    "       (SELECT u.factor FROM producto_unidades u "
                    "         WHERE u.producto_id = p.id AND u.activo AND u.factor > 1 "
                    "         ORDER BY u.factor DESC LIMIT 1) AS factor_caja "
                    "  FROM productos p WHERE p.id = ANY(:ids)"
                ),
                {"ids": list(ids)},
            )
        ).mappings()
    }
    conteos: dict[uuid.UUID, Decimal] = {}
    malos: list[str] = []
    for producto_id in ids:
        fila = filas.get(producto_id)
        if fila is None:
            malos.append("un artículo que ya no existe")
            continue
        try:
            cajas = leer_entero(
                capturado.get(f"cajas_{producto_id}", ""), campo=f"{fila['nombre']} (cajas)",
                maximo=100000,
            )
            piezas = leer_entero(
                capturado.get(f"piezas_{producto_id}", ""), campo=f"{fila['nombre']} (piezas)",
                maximo=1000000,
            )
        except CapturaInvalida as e:
            malos.append(str(e).rstrip("."))
            continue
        if cajas and fila["factor_caja"] is None:
            malos.append(f"{fila['nombre']} no tiene caja: escríbelo en piezas")
            continue
        conteos[producto_id] = (
            Decimal(cajas) * Decimal(fila["factor_caja"] or 1) + Decimal(piezas)
        ).quantize(Decimal("0.001"))
    if malos:
        raise CapturaInvalida(f"Revisa: {'; '.join(malos)}. No se guardó nada.")
    return conteos


async def _pintar(
    peticion: Request,
    actor,
    sesion,
    *,
    almacen: str,
    buscar: str = "",
    guardado: str = "",
    error: str = "",
    capturado: dict[str, str] | None = None,
    nota: str = NOTA_POR_OMISION,
) -> HTMLResponse:
    almacenes = await ajuste_por_conteo.almacenes_ajustables(sesion)
    try:
        buscado = uuid.UUID(almacen)
    except ValueError:
        buscado = None
    # El elegido, o la primera bodega: es donde se cuenta primero.
    elegido = next((a for a in almacenes if a["id"] == buscado), None) or next(
        (a for a in almacenes if a["tipo"] == "bodega"), almacenes[0] if almacenes else None
    )

    filas: list[dict] = []
    telefono_atrasado = None
    recientes: list = []
    if elegido:
        for f in await ajuste_por_conteo.catalogo_con_existencia(sesion, elegido["id"], buscar):
            f["en_cajas"] = _en_cajas(Decimal(f["cantidad"]), f["factor_caja"], f["caja"],
                                      f["unidad_base"])
            filas.append(f)
        if elegido["tipo"] == "camion" and elegido["responsable_id"]:
            # Si su teléfono tiene ventas sin subir, el sistema va atrás.
            telefono_atrasado = (
                await sesion.execute(
                    text(
                        "SELECT COALESCE(sum(COALESCE(cola_pendiente, 0)), 0) FROM dispositivos "
                        " WHERE usuario_id = :u AND estado = 'activo'"
                    ),
                    {"u": elegido["responsable_id"]},
                )
            ).scalar_one()
        recientes = (
            await sesion.execute(
                text(
                    "SELECT j.folio, j.creado_en, j.tipo, j.delta, j.contado, j.nota, "
                    "       p.nombre AS producto, p.unidad_base, u.nombre AS quien "
                    "  FROM ajustes_camion j "
                    "  JOIN productos p ON p.id = j.producto_id "
                    "  JOIN usuarios u ON u.id = j.usuario_id "
                    " WHERE j.almacen_id = :a "
                    " ORDER BY j.creado_en DESC, j.folio DESC LIMIT 15"
                ),
                {"a": elegido["id"]},
            )
        ).mappings().all()

    return render(
        peticion,
        "ajustar_inventario.html",
        {
            "almacenes": almacenes,
            "elegido": elegido,
            "grupos": agrupar_por_familia(filas),
            "buscar": buscar.strip(),
            "capturado": capturado or {},
            "nota": nota,
            "telefono_atrasado": telefono_atrasado,
            "recientes": recientes,
            "guardado": guardado,
            "error": error,
        },
        actor=actor,
        seccion="Inventario",
    )


def _en_cajas(cantidad: Decimal, factor, caja: str | None, unidad_base: str) -> str:
    """«31 CAJA + 16 PZA», como en el anaquel. Vacío sin caja o en negativo."""
    if not caja or not factor or cantidad <= 0:
        return ""
    enteras, resto = divmod(cantidad, Decimal(factor))
    texto = f"{int(enteras)} {caja}"
    return texto + (f" + {sin_decimales(resto)} {unidad_base}" if resto else "")
