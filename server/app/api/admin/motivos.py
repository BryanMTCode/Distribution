"""Los catálogos de motivos: por qué se perdió algo, y por qué no se vendió.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTA PANTALLA PESA MÁS DE LO QUE PARECE
────────────────────────────────────────────────────────────────────────────
Los motivos se sembraron por migración y no había dónde editarlos. Desde la
cuenta del vendedor (migración 0039) uno de sus campos mueve dinero:
`afecta_vendedor` decide si una merma se le cobra a costo en el Corte del día. Esa
decisión es de la oficina, nunca del vendedor al capturar —sería pedirle que
elija si se le cobra—, y por eso vive aquí, detrás de `catalogo.administrar`.

────────────────────────────────────────────────────────────────────────────
«ELIMINAR» UN MOTIVO ES DESACTIVARLO, SIEMPRE
────────────────────────────────────────────────────────────────────────────
Un motivo viaja a todos los teléfonos en cuanto se crea (migración 0017), y el
teléfono no sabe borrarlo: lo que sí sabe es dejar de ofrecerlo cuando llega
`activo = false`. Un borrado de verdad dejaría el motivo en cada teléfono para
siempre y sin forma de quitarlo, y además rompería la historia: cada merma y cada
no-drop guarda el código de su motivo. Así que aquí eliminar es desactivar, y la
pantalla lo dice.

El código no se edita: es la llave con la que cada documento lo recuerda.
"""

from __future__ import annotations

import re
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.api.admin.comun import CapturaInvalida, SesionDep, auditar, leer_entero, render
from app.api.admin.efectividad import PERMISO as PERMISO_EFECTIVIDAD
from app.api.admin.sesion_web import ActorWeb, exigir_csrf

router = APIRouter(prefix="/panel/motivos", tags=["panel"], include_in_schema=False)

PERMISO = "catalogo.administrar"
PERMISO_VER = PERMISO_EFECTIVIDAD

CATEGORIAS = (
    ("cliente", "Del cliente (cerrado, sin dinero)"),
    ("operacion", "De la operación (llegó tarde, faltó surtido)"),
    ("producto", "Del producto (no lo traía, le pareció caro)"),
    ("vendedor", "Del vendedor (no alcanzó a visitarlo)"),
)


def _volver(*, guardado: str = "", error: str = "") -> RedirectResponse:
    destino = "/panel/motivos"
    if error:
        destino += f"?error={quote(error)}"
    elif guardado:
        destino += f"?guardado={quote(guardado)}"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


def _codigo(texto: str) -> str:
    """MAYÚSCULAS_CON_GUION_BAJO: así están los que sembró la migración."""
    limpio = re.sub(r"[^A-Z0-9_]", "_", (texto or "").strip().upper().replace(" ", "_"))
    return re.sub(r"_+", "_", limpio).strip("_")[:40]


@router.get("", response_class=HTMLResponse)
async def listar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    guardado: str = "",
    error: str = "",
) -> HTMLResponse:
    # Verlos: quien ve Efectividad, que es donde se usan. Editarlos: catálogo.
    if not actor.puede(PERMISO):
        actor.exigir(PERMISO_VER)
    merma = (
        await sesion.execute(
            text(
                """
                SELECT m.codigo, m.nombre, m.afecta_vendedor, m.activo,
                       (SELECT count(*) FROM mermas x WHERE x.motivo_codigo = m.codigo) AS usos
                  FROM motivos_merma m
                 ORDER BY m.activo DESC, m.nombre
                """
            )
        )
    ).mappings().all()
    no_drop = (
        await sesion.execute(
            text(
                """
                SELECT m.codigo, m.nombre, m.categoria, m.requiere_nota, m.orden, m.activo,
                       (SELECT count(*) FROM no_drops x WHERE x.motivo_codigo = m.codigo) AS usos
                  FROM motivos_no_drop m
                 ORDER BY m.activo DESC, m.orden, m.nombre
                """
            )
        )
    ).mappings().all()
    return render(
        peticion,
        "motivos.html",
        {
            "merma": merma,
            "no_drop": no_drop,
            "categorias": CATEGORIAS,
            "puede_editar": actor.puede(PERMISO),
            "guardado": guardado,
            "error": error,
        },
        actor=actor,
        seccion="Desempeño",
    )


@router.post("/merma")
async def guardar_merma(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    codigo: Annotated[str, Form()] = "",
    nombre: Annotated[str, Form()] = "",
    afecta_vendedor: Annotated[str, Form()] = "",
    activo: Annotated[str, Form()] = "",
    nuevo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Crea o edita un motivo de merma. Viaja a los teléfonos por la 0017."""
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)
    clave = _codigo(codigo)
    limpio = (nombre or "").strip()
    if not clave or not limpio:
        return _volver(error="El motivo necesita código y nombre.")

    antes = (
        await sesion.execute(
            text("SELECT nombre, afecta_vendedor, activo FROM motivos_merma WHERE codigo = :c"),
            {"c": clave},
        )
    ).mappings().first()
    if nuevo and antes is not None:
        return _volver(error=f"Ya existe un motivo con el código {clave}: edítalo en la tabla.")
    if not nuevo and antes is None:
        return _volver(error="Ese motivo no existe.")

    await sesion.execute(
        text(
            """
            INSERT INTO motivos_merma (codigo, nombre, afecta_vendedor, activo)
            VALUES (:c, :n, :a, :act)
            ON CONFLICT (codigo) DO UPDATE SET
              nombre = excluded.nombre, afecta_vendedor = excluded.afecta_vendedor,
              activo = excluded.activo
            """
        ),
        {
            "c": clave,
            "n": limpio[:80],
            "a": bool(afecta_vendedor),
            "act": bool(activo) if not nuevo else True,
        },
    )
    await auditar(
        sesion,
        entidad="motivo_merma",
        entidad_id=None,
        accion="crear" if nuevo else "editar",
        quien=actor.usuario_id,
        antes=dict(antes) if antes else None,
        despues={"codigo": clave, "nombre": limpio, "afecta_vendedor": bool(afecta_vendedor)},
    )
    await sesion.commit()
    aviso = f"Motivo {clave} {'creado' if nuevo else 'guardado'}."
    if antes is not None and antes["afecta_vendedor"] != bool(afecta_vendedor):
        aviso += (
            " Cambió si se le cobra al vendedor: cuenta para los Cortes que se cierren "
            "desde ahora, no para los ya cerrados."
        )
    return _volver(guardado=aviso + " Los teléfonos lo reciben al sincronizar.")


@router.post("/no-drop")
async def guardar_no_drop(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    codigo: Annotated[str, Form()] = "",
    nombre: Annotated[str, Form()] = "",
    categoria: Annotated[str, Form()] = "",
    requiere_nota: Annotated[str, Form()] = "",
    orden: Annotated[str, Form()] = "0",
    activo: Annotated[str, Form()] = "",
    nuevo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Crea o edita un motivo de visita sin venta."""
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)
    clave = _codigo(codigo)
    limpio = (nombre or "").strip()
    if not clave or not limpio:
        return _volver(error="El motivo necesita código y nombre.")
    if categoria not in {c for c, _ in CATEGORIAS}:
        return _volver(error="Elige de quién es la culpa: cliente, operación, producto o vendedor.")
    try:
        posicion = leer_entero(orden, campo="El orden", maximo=999)
    except CapturaInvalida as e:
        return _volver(error=str(e))

    antes = (
        await sesion.execute(
            text(
                "SELECT nombre, categoria, requiere_nota, orden, activo "
                "  FROM motivos_no_drop WHERE codigo = :c"
            ),
            {"c": clave},
        )
    ).mappings().first()
    if nuevo and antes is not None:
        return _volver(error=f"Ya existe un motivo con el código {clave}: edítalo en la tabla.")
    if not nuevo and antes is None:
        return _volver(error="Ese motivo no existe.")

    await sesion.execute(
        text(
            """
            INSERT INTO motivos_no_drop (codigo, nombre, categoria, requiere_nota, orden, activo)
            VALUES (:c, :n, :cat, :nota, :orden, :act)
            ON CONFLICT (codigo) DO UPDATE SET
              nombre = excluded.nombre, categoria = excluded.categoria,
              requiere_nota = excluded.requiere_nota, orden = excluded.orden,
              activo = excluded.activo
            """
        ),
        {
            "c": clave,
            "n": limpio[:80],
            "cat": categoria,
            "nota": bool(requiere_nota),
            "orden": posicion,
            "act": bool(activo) if not nuevo else True,
        },
    )
    await auditar(
        sesion,
        entidad="motivo_no_drop",
        entidad_id=None,
        accion="crear" if nuevo else "editar",
        quien=actor.usuario_id,
        antes=dict(antes) if antes else None,
        despues={"codigo": clave, "nombre": limpio, "categoria": categoria},
    )
    await sesion.commit()
    return _volver(
        guardado=f"Motivo {clave} {'creado' if nuevo else 'guardado'}. Los teléfonos lo "
        "reciben al sincronizar."
    )


@router.post("/{catalogo}/{codigo}/eliminar")
async def eliminar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    catalogo: str,
    codigo: str,
    csrf: Annotated[str, Form()] = "",
):
    """Desactiva. Ver el encabezado: un motivo nunca se borra de verdad."""
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)
    tabla, entidad = {
        "merma": ("motivos_merma", "motivo_merma"),
        "no-drop": ("motivos_no_drop", "motivo_no_drop"),
    }.get(catalogo, (None, None))
    if tabla is None:
        return _volver(error="Catálogo desconocido.")
    hecho = (
        await sesion.execute(
            text(f"UPDATE {tabla} SET activo = false WHERE codigo = :c AND activo"),  # noqa: S608
            {"c": codigo},
        )
    ).rowcount
    if not hecho:
        return _volver(error="Ese motivo no existe o ya estaba desactivado.")
    await auditar(
        sesion,
        entidad=entidad,
        entidad_id=None,
        accion="dar_de_baja",
        quien=actor.usuario_id,
        despues={"codigo": codigo},
    )
    await sesion.commit()
    return _volver(
        guardado=f"Motivo {codigo} desactivado: los teléfonos lo dejan de ofrecer al "
        "sincronizar, y lo que ya se registró con él conserva su motivo."
    )
