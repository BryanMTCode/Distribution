"""La ficha de cada usuario, ruta, almacén y lista: editarlo y eliminarlo.

────────────────────────────────────────────────────────────────────────────
POR QUÉ UNA FICHA Y NO UN BOTÓN EN LA TABLA
────────────────────────────────────────────────────────────────────────────
La pantalla de Equipo creaba y poco más: cambiar el nombre de una ruta o el
responsable de un almacén exigía SQL a mano. La dirección pidió poder editar y
eliminar todo lo que el panel muestra (octubre 2026). Cada renglón de la tabla
lleva ahora a su ficha, donde están los datos, las consecuencias y el botón: lo
mismo que con clientes y productos (ADR 0002 §52).

────────────────────────────────────────────────────────────────────────────
«ELIMINAR» FUNCIONA SIEMPRE, Y DECIDE QUÉ ES SEGURO
────────────────────────────────────────────────────────────────────────────
La misma regla que clientes y productos:

  · si nada lo usa, se borra de verdad;
  · si tiene historia —una venta firmada por ese usuario, un movimiento en ese
    almacén—, se DA DE BAJA: sale de las listas y de los selectores, y su
    historia sigue en pie;
  · y se NIEGA cuando borrarlo dejaría algo roto en la calle: una ruta con
    clientes, un almacén con mercancía, la lista por omisión. La ficha dice qué
    hacer primero, y casi siempre ofrece hacerlo ahí mismo (mover los clientes).

Quién decide si «tiene historia» es la base, no una lista de tablas en este
archivo: ver `borrar_si_nadie_lo_usa`.

────────────────────────────────────────────────────────────────────────────
LO QUE NO SE EDITA, Y POR QUÉ
────────────────────────────────────────────────────────────────────────────
**El código de un usuario.** Es el prefijo del folio impreso (`VEND01-000077`):
hay tickets en la calle con él.
**El camión de un vendedor, si trae mercancía.** El teléfono del nuevo
responsable empezaría su camión en cero —el inventario local se reinicia al
cambiar de camión— y no podría vender lo que sí está arriba.
"""

from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api.admin.comun import (
    CapturaInvalida,
    SesionDep,
    auditar,
    borrar_si_nadie_lo_usa,
    leer_entero,
    render,
)
from app.api.admin.equipo import PERMISO, ROLES, _sucursales
from app.api.admin.sesion_web import ActorWeb, exigir_csrf

router = APIRouter(prefix="/panel/equipo", tags=["panel"], include_in_schema=False)

TIPOS_DE_ALMACEN = ("bodega", "camion", "transito", "merma")


def _clave(texto: str) -> str:
    """Los códigos van en mayúsculas y sin espacios, como al crearlos."""
    return (texto or "").strip().upper().replace(" ", "")


def _a_ficha(tipo: str, id_, *, guardado: str = "", error: str = "") -> RedirectResponse:
    destino = f"/panel/equipo/{tipo}/{id_}"
    if error:
        destino += f"?error={quote(error)}"
    elif guardado:
        destino += f"?guardado={quote(guardado)}"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


def _a_equipo(*, guardado: str = "", error: str = "") -> RedirectResponse:
    destino = "/panel/equipo"
    if error:
        destino += f"?error={quote(error)}"
    elif guardado:
        destino += f"?guardado={quote(guardado)}"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


async def _otros_admins_activos(sesion, usuario_id) -> int:
    return (
        await sesion.execute(
            text(
                "SELECT count(*) FROM usuarios "
                " WHERE rol_codigo = 'admin' AND activo AND id <> :u"
            ),
            {"u": usuario_id},
        )
    ).scalar_one()


# ===========================================================================
# Usuarios
# ===========================================================================


@router.get("/usuarios/{usuario_id}", response_class=HTMLResponse)
async def ficha_usuario(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    usuario_id: uuid.UUID,
    guardado: str = "",
    error: str = "",
) -> HTMLResponse:
    actor.exigir(PERMISO)
    usuario = (
        await sesion.execute(
            text(
                """
                SELECT u.*, a.id AS camion_id, a.codigo AS camion_codigo,
                       a.nombre AS camion
                  FROM usuarios u
                  LEFT JOIN almacenes a ON a.id = u.almacen_id
                 WHERE u.id = :u
                """
            ),
            {"u": usuario_id},
        )
    ).mappings().first()
    if usuario is None:
        return _a_equipo(error="Ese usuario no existe.")

    rutas = (
        await sesion.execute(
            text(
                "SELECT r.id, r.codigo, r.nombre, r.vendedor_id = :u AS titular "
                "  FROM usuarios_rutas ur JOIN rutas r ON r.id = ur.ruta_id "
                " WHERE ur.usuario_id = :u ORDER BY r.codigo"
            ),
            {"u": usuario_id},
        )
    ).mappings().all()

    return render(
        peticion,
        "equipo_usuario.html",
        {
            "u": usuario,
            "rutas": rutas,
            "roles": ROLES,
            "sucursales": await _sucursales(sesion),
            "es_uno_mismo": usuario_id == actor.usuario_id,
            "guardado": guardado,
            "error": error,
        },
        actor=actor,
        seccion="Usuarios y rutas",
    )


@router.post("/usuarios/{usuario_id}/datos")
async def editar_usuario(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    usuario_id: uuid.UUID,
    nombre: Annotated[str, Form()] = "",
    rol_codigo: Annotated[str, Form()] = "",
    sucursal_id: Annotated[str, Form()] = "",
    dias_max_offline: Annotated[str, Form()] = "7",
    csrf: Annotated[str, Form()] = "",
):
    """Nombre, rol, sucursal y días sin sincronizar. El código no se toca."""
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    antes = (
        await sesion.execute(
            text(
                "SELECT nombre, rol_codigo, sucursal_id, dias_max_offline, almacen_id "
                "  FROM usuarios WHERE id = :u FOR UPDATE"
            ),
            {"u": usuario_id},
        )
    ).mappings().first()
    if antes is None:
        return _a_equipo(error="Ese usuario no existe.")

    limpio = (nombre or "").strip()
    if not limpio:
        return _a_ficha("usuarios", usuario_id, error="Falta el nombre.")
    if rol_codigo not in {r[0] for r in ROLES}:
        return _a_ficha("usuarios", usuario_id, error="Elige un rol válido.")
    try:
        dias = leer_entero(dias_max_offline, campo="Los días sin sincronizar", maximo=30)
    except CapturaInvalida as e:
        return _a_ficha("usuarios", usuario_id, error=str(e))
    dias = dias or 7

    # Dejar el panel sin administrador se arregla solo desde el servidor.
    if antes["rol_codigo"] == "admin" and rol_codigo != "admin":
        if usuario_id == actor.usuario_id:
            return _a_ficha(
                "usuarios",
                usuario_id,
                error="No puedes quitarte el rol de administrador a ti mismo: te "
                "quedarías sin poder deshacerlo.",
            )
        if await _otros_admins_activos(sesion, usuario_id) == 0:
            return _a_ficha(
                "usuarios",
                usuario_id,
                error="Es el último administrador activo: sin él nadie puede dar de "
                "alta usuarios.",
            )

    sucursal = uuid.UUID(sucursal_id) if sucursal_id else antes["sucursal_id"]
    await sesion.execute(
        text(
            "UPDATE usuarios SET nombre = :n, rol_codigo = :r, sucursal_id = :s, "
            "       dias_max_offline = :d, actualizado_en = now() WHERE id = :u"
        ),
        {"n": limpio[:120], "r": rol_codigo, "s": sucursal, "d": dias, "u": usuario_id},
    )
    await auditar(
        sesion,
        entidad="usuario",
        entidad_id=usuario_id,
        accion="editar",
        quien=actor.usuario_id,
        antes=dict(antes),
        despues={"nombre": limpio, "rol_codigo": rol_codigo, "dias_max_offline": dias},
    )
    await sesion.commit()

    aviso = "Datos guardados."
    if antes["rol_codigo"] == "vendedor" and rol_codigo != "vendedor" and antes["almacen_id"]:
        aviso += (
            " Sigue como responsable de su camión: si ya no va a salir a ruta, "
            "pásale el camión a otro vendedor desde la ficha del almacén."
        )
    if rol_codigo != antes["rol_codigo"]:
        aviso += " El rol nuevo cuenta desde su siguiente entrada."
    return _a_ficha("usuarios", usuario_id, guardado=aviso)


@router.post("/usuarios/{usuario_id}/eliminar")
async def eliminar_usuario(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    usuario_id: uuid.UUID,
    confirmo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Lo borra de verdad. Si tiene documentos firmados, no lo toca y dice cuáles.

    ───────────────────────────────────────────────────────────────────────
    ANTES SE «DESACTIVABA», Y CASI SIEMPRE
    ───────────────────────────────────────────────────────────────────────
    La regla era «si algo lo usa, se da de baja». Pero casi todo usuario tiene
    algo que lo apunta sin ser historia —su teléfono vinculado, una sesión, los
    avisos que esperaban a su teléfono, la ruta de la que es titular—, así que
    «Eliminar» terminaba siempre en «desactivado». La dirección pidió que se
    borre (octubre 2026, ADR 0002 §88).

    Ahora se suelta primero todo lo que es SUYO y no es historia: sus teléfonos
    (con sus folios y sesiones), los avisos pendientes de su teléfono, su
    asignación a rutas, su camión si está vacío; y donde aparece como «quién lo
    capturó» (un cliente, un proveedor, la auditoría) se queda sin nombre. Lo que
    sí es historia —ventas, cargas, cortes, movimientos de inventario, cuentas—
    no se borra: si existe, no se borra NADA, se dice qué tiene y se ofrece
    desactivarlo. Todo va en un punto de guardado: o se borra completo, o no
    cambia nada.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)
    if not confirmo:
        return _a_ficha(
            "usuarios", usuario_id, error="Marca la casilla para confirmar que quieres eliminarlo."
        )
    if usuario_id == actor.usuario_id:
        return _a_ficha(
            "usuarios", usuario_id, error="No puedes eliminarte a ti mismo."
        )

    usuario = (
        await sesion.execute(
            text("SELECT codigo, nombre, rol_codigo FROM usuarios WHERE id = :u FOR UPDATE"),
            {"u": usuario_id},
        )
    ).mappings().first()
    if usuario is None:
        return _a_equipo(error="Ese usuario no existe.")
    if usuario["rol_codigo"] == "admin" and await _otros_admins_activos(sesion, usuario_id) == 0:
        return _a_ficha(
            "usuarios",
            usuario_id,
            error="Es el último administrador activo: sin él nadie puede dar de alta "
            "usuarios.",
        )

    camiones: list[str] = []
    try:
        async with sesion.begin_nested():
            camiones = await _soltar_lo_suyo(sesion, usuario_id, usuario["codigo"])
            await sesion.execute(text("DELETE FROM usuarios WHERE id = :u"), {"u": usuario_id})
    except IntegrityError:
        documentos = await _sus_documentos(sesion, usuario_id)
        return _a_ficha(
            "usuarios",
            usuario_id,
            error=(
                f"{usuario['codigo']} no se puede borrar: tiene "
                f"{documentos or 'documentos firmados'}, y borrarlo borraría esos "
                "papeles. No se cambió nada. Si era de prueba, pon la base en blanco "
                "y elimínalo después; si no, usa «Desactivar»."
            ),
        )

    await auditar(
        sesion,
        entidad="usuario",
        entidad_id=usuario_id,
        accion="eliminar",
        quien=actor.usuario_id,
        antes=dict(usuario),
    )
    await sesion.commit()
    aviso = f"{usuario['codigo']} se eliminó, con sus teléfonos vinculados."
    if camiones:
        aviso += f" También su camión, que estaba vacío: {', '.join(camiones)}."
    return _a_equipo(guardado=aviso)


async def _soltar_lo_suyo(sesion, usuario_id: uuid.UUID, codigo: str) -> list[str]:
    """Quita o suelta lo que apunta al usuario sin ser historia. Ver arriba."""
    p = {"u": usuario_id}
    # Sus teléfonos, con lo que cuelga de ellos y no es un documento.
    equipos = "SELECT id FROM dispositivos WHERE usuario_id = :u"
    await sesion.execute(text(f"DELETE FROM folios_rangos WHERE dispositivo_id IN ({equipos})"), p)
    await sesion.execute(
        text(f"UPDATE auditoria SET dispositivo_id = NULL WHERE dispositivo_id IN ({equipos})"), p
    )
    await sesion.execute(
        text(f"DELETE FROM sync_operaciones WHERE dispositivo_id IN ({equipos})"), p
    )
    await sesion.execute(text(f"DELETE FROM sync_lotes WHERE dispositivo_id IN ({equipos})"), p)
    await sesion.execute(text("DELETE FROM dispositivos WHERE usuario_id = :u"), p)
    await sesion.execute(
        text("UPDATE dispositivos SET borrado_ordenado_por = NULL WHERE borrado_ordenado_por = :u"),
        p,
    )
    # Su camión: si está vacío y sin historia se va con él; un camión no existe
    # sin responsable. Si tiene historia, el DELETE de abajo lo rechaza.
    await sesion.execute(
        text("UPDATE usuarios SET almacen_id = NULL WHERE id = :u"), p
    )
    camiones = (
        await sesion.execute(
            text("SELECT id, nombre FROM almacenes WHERE responsable_id = :u AND tipo = 'camion'"),
            p,
        )
    ).mappings().all()
    for camion in camiones:
        await sesion.execute(
            text("DELETE FROM existencias WHERE almacen_id = :a AND cantidad = 0"),
            {"a": camion["id"]},
        )
        await sesion.execute(
            text("UPDATE usuarios SET almacen_id = NULL WHERE almacen_id = :a"), {"a": camion["id"]}
        )
        await sesion.execute(text("DELETE FROM almacenes WHERE id = :a"), {"a": camion["id"]})
    await sesion.execute(
        text("UPDATE almacenes SET responsable_id = NULL WHERE responsable_id = :u"), p
    )
    # Donde solo es «quién lo capturó» o «de quién es la ruta»: sin nombre.
    for tabla, columna in (
        ("rutas", "vendedor_id"),
        ("clientes", "creado_por"),
        ("proveedores", "creado_por"),
        ("pilotos", "creado_por"),
        ("objetivos_ruta", "fijado_por"),
    ):
        await sesion.execute(
            text(f"UPDATE {tabla} SET {columna} = NULL WHERE {columna} = :u"), p  # noqa: S608
        )
    await sesion.execute(
        text(
            "UPDATE auditoria SET usuario_id = NULL, "
            "       motivo = concat_ws(' · ', motivo, CAST(:quien AS text)) "
            " WHERE usuario_id = :u"
        ),
        {"u": usuario_id, "quien": f"lo hizo {codigo} (usuario eliminado)"},
    )
    # Los avisos que esperaban a su teléfono: ya no hay teléfono que los lea.
    await sesion.execute(text("DELETE FROM change_log WHERE vendedor_id = :u"), p)
    return [c["nombre"] for c in camiones]


async def _sus_documentos(sesion, usuario_id: uuid.UUID) -> str:
    """«3 venta(s), 1 carga(s)»: lo que impide borrarlo, dicho con palabras."""
    partes = []
    for tabla, columna, nombre in (
        ("ventas", "vendedor_id", "venta(s)"),
        ("cargas", "vendedor_id", "carga(s)"),
        ("cortes_vendedor", "vendedor_id", "corte(s)"),
        ("mermas", "vendedor_id", "merma(s)"),
        ("cuenta_vendedor", "vendedor_id", "movimiento(s) en su cuenta"),
        ("movimientos_inventario", "usuario_id", "movimiento(s) de inventario"),
        ("entradas", "creado_por", "entrada(s) de mercancía"),
    ):
        n = (
            await sesion.execute(
                text(f"SELECT count(*) FROM {tabla} WHERE {columna} = :u"),  # noqa: S608
                {"u": usuario_id},
            )
        ).scalar_one()
        if n:
            partes.append(f"{n} {nombre}")
    return ", ".join(partes)


# ===========================================================================
# Rutas
# ===========================================================================


@router.get("/rutas/{ruta_id}", response_class=HTMLResponse)
async def ficha_ruta(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    ruta_id: uuid.UUID,
    guardado: str = "",
    error: str = "",
) -> HTMLResponse:
    actor.exigir(PERMISO)
    ruta = (
        await sesion.execute(
            text(
                """
                SELECT r.*, u.nombre AS vendedor, u.codigo AS vendedor_codigo,
                       (SELECT count(*) FROM clientes c
                         WHERE c.ruta_id = r.id AND c.estatus <> 'baja') AS clientes
                  FROM rutas r LEFT JOIN usuarios u ON u.id = r.vendedor_id
                 WHERE r.id = :r
                """
            ),
            {"r": ruta_id},
        )
    ).mappings().first()
    if ruta is None:
        return _a_equipo(error="Esa ruta no existe.")

    otras = (
        await sesion.execute(
            text(
                "SELECT id, codigo, nombre FROM rutas WHERE activo AND id <> :r ORDER BY codigo"
            ),
            {"r": ruta_id},
        )
    ).mappings().all()
    vendedores = (
        await sesion.execute(
            text(
                "SELECT id, codigo, nombre FROM usuarios "
                " WHERE rol_codigo = 'vendedor' AND activo ORDER BY codigo"
            )
        )
    ).mappings().all()

    return render(
        peticion,
        "equipo_ruta.html",
        {
            "r": ruta,
            "otras": otras,
            "vendedores": vendedores,
            "guardado": guardado,
            "error": error,
        },
        actor=actor,
        seccion="Usuarios y rutas",
    )


@router.post("/rutas/{ruta_id}/datos")
async def editar_ruta(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    ruta_id: uuid.UUID,
    codigo: Annotated[str, Form()] = "",
    nombre: Annotated[str, Form()] = "",
    activo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Código, nombre y si está activa. El titular tiene su propio formulario."""
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    antes = (
        await sesion.execute(
            text(
                "SELECT codigo, nombre, activo, "
                "       (SELECT count(*) FROM clientes c "
                "         WHERE c.ruta_id = r.id AND c.estatus <> 'baja') AS clientes "
                "  FROM rutas r WHERE id = :r FOR UPDATE"
            ),
            {"r": ruta_id},
        )
    ).mappings().first()
    if antes is None:
        return _a_equipo(error="Esa ruta no existe.")

    clave = _clave(codigo)
    if not clave or not (nombre or "").strip():
        return _a_ficha("rutas", ruta_id, error="La ruta necesita código y nombre.")
    choca = (
        await sesion.execute(
            text("SELECT nombre FROM rutas WHERE codigo = :c AND id <> :r"),
            {"c": clave, "r": ruta_id},
        )
    ).scalar_one_or_none()
    if choca is not None:
        return _a_ficha("rutas", ruta_id, error=f"El código {clave} ya lo tiene «{choca}».")

    quiere_activa = bool(activo)
    if antes["activo"] and not quiere_activa and antes["clientes"]:
        # Una ruta inactiva sale de los selectores, pero sus clientes siguen en el
        # teléfono de su titular: quedarían en una ruta que nadie puede elegir.
        return _a_ficha(
            "rutas",
            ruta_id,
            error=f"Tiene {antes['clientes']} cliente(s). Muévelos a otra ruta abajo "
            "antes de desactivarla.",
        )

    await sesion.execute(
        text("UPDATE rutas SET codigo = :c, nombre = :n, activo = :a WHERE id = :r"),
        {"c": clave, "n": nombre.strip()[:120], "a": quiere_activa, "r": ruta_id},
    )
    await auditar(
        sesion,
        entidad="ruta",
        entidad_id=ruta_id,
        accion="editar",
        quien=actor.usuario_id,
        antes={k: antes[k] for k in ("codigo", "nombre", "activo")},
        despues={"codigo": clave, "nombre": nombre.strip(), "activo": quiere_activa},
    )
    await sesion.commit()
    return _a_ficha("rutas", ruta_id, guardado=f"Ruta {clave} guardada.")


@router.post("/rutas/{ruta_id}/mover-clientes")
async def mover_clientes(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    ruta_id: uuid.UUID,
    destino_id: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Pasa todos los clientes de esta ruta a otra, de una vez.

    Cada cliente cambia de ruta con un UPDATE, y el disparador de clientes
    (migración 0034) hace el resto: publica la BAJA al teléfono de la ruta vieja
    —o se quedaría ofreciéndole a clientes que ya no son suyos— y el alta al de
    la nueva. Su orden de visita y sus días de visita viajan con ellos.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)
    try:
        destino = uuid.UUID(destino_id)
    except ValueError:
        return _a_ficha("rutas", ruta_id, error="Elige a qué ruta pasan.")
    if destino == ruta_id:
        return _a_ficha("rutas", ruta_id, error="Elige una ruta distinta.")

    nueva = (
        await sesion.execute(
            text("SELECT codigo FROM rutas WHERE id = :r AND activo"), {"r": destino}
        )
    ).scalar_one_or_none()
    if nueva is None:
        return _a_ficha("rutas", ruta_id, error="La ruta destino no existe o está inactiva.")

    movidos = (
        await sesion.execute(
            text(
                "UPDATE clientes SET ruta_id = :d, actualizado_en = now() "
                " WHERE ruta_id = :r AND estatus <> 'baja'"
            ),
            {"d": destino, "r": ruta_id},
        )
    ).rowcount
    await auditar(
        sesion,
        entidad="ruta",
        entidad_id=ruta_id,
        accion="mover_clientes",
        quien=actor.usuario_id,
        despues={"destino": str(destino), "clientes": movidos},
    )
    await sesion.commit()
    return _a_ficha(
        "rutas",
        ruta_id,
        guardado=f"{movidos} cliente(s) pasaron a {nueva}. Salen del teléfono de esta "
        "ruta y llegan al de la nueva en su siguiente sincronización.",
    )


@router.post("/rutas/{ruta_id}/eliminar")
async def eliminar_ruta(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    ruta_id: uuid.UUID,
    confirmo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Sin clientes: se borra si nunca operó, y se da de baja si ya tiene historia."""
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)
    if not confirmo:
        return _a_ficha("rutas", ruta_id, error="Marca la casilla para confirmar.")

    ruta = (
        await sesion.execute(
            text(
                "SELECT codigo, nombre, vendedor_id, "
                "       (SELECT count(*) FROM clientes c "
                "         WHERE c.ruta_id = r.id AND c.estatus <> 'baja') AS clientes "
                "  FROM rutas r WHERE id = :r FOR UPDATE"
            ),
            {"r": ruta_id},
        )
    ).mappings().first()
    if ruta is None:
        return _a_equipo(error="Esa ruta no existe.")
    if ruta["clientes"]:
        return _a_ficha(
            "rutas",
            ruta_id,
            error=f"Tiene {ruta['clientes']} cliente(s): muévelos a otra ruta primero "
            "(aquí abajo). Borrarla los dejaría sin ruta y fuera de todos los teléfonos.",
        )

    borrada = await borrar_si_nadie_lo_usa(
        sesion, "DELETE FROM rutas WHERE id = :r", {"r": ruta_id}
    )
    if not borrada:
        # Sin titular y sin alcance: nadie recibe nada de una ruta dada de baja.
        await sesion.execute(
            text("UPDATE rutas SET activo = false, vendedor_id = NULL WHERE id = :r"),
            {"r": ruta_id},
        )
        await sesion.execute(text("DELETE FROM usuarios_rutas WHERE ruta_id = :r"), {"r": ruta_id})
    await auditar(
        sesion,
        entidad="ruta",
        entidad_id=ruta_id,
        accion="eliminar" if borrada else "dar_de_baja",
        quien=actor.usuario_id,
        antes={"codigo": ruta["codigo"], "nombre": ruta["nombre"]},
    )
    await sesion.commit()
    if borrada:
        return _a_equipo(guardado=f"Ruta {ruta['codigo']} eliminada.")
    return _a_equipo(
        guardado=f"La ruta {ruta['codigo']} ya tiene ventas o cargas, así que se dio de "
        "baja en vez de borrarse: sale de los selectores y su historia sigue en los reportes."
    )


# ===========================================================================
# Almacenes
# ===========================================================================


async def _existencia(sesion, almacen_id) -> dict:
    return dict(
        (
            await sesion.execute(
                text(
                    "SELECT count(*) FILTER (WHERE cantidad <> 0) AS productos, "
                    "       COALESCE(sum(cantidad) FILTER (WHERE cantidad <> 0), 0) AS piezas "
                    "  FROM existencias WHERE almacen_id = :a"
                ),
                {"a": almacen_id},
            )
        ).mappings().one()
    )


async def _tiene_movimientos(sesion, almacen_id) -> bool:
    return (
        await sesion.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM movimientos_inventario "
                " WHERE almacen_origen_id = :a OR almacen_destino_id = :a)"
            ),
            {"a": almacen_id},
        )
    ).scalar_one()


@router.get("/almacenes/{almacen_id}", response_class=HTMLResponse)
async def ficha_almacen(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    almacen_id: uuid.UUID,
    guardado: str = "",
    error: str = "",
) -> HTMLResponse:
    actor.exigir(PERMISO)
    almacen = (
        await sesion.execute(
            text(
                "SELECT a.*, u.nombre AS responsable, u.codigo AS responsable_codigo "
                "  FROM almacenes a LEFT JOIN usuarios u ON u.id = a.responsable_id "
                " WHERE a.id = :a"
            ),
            {"a": almacen_id},
        )
    ).mappings().first()
    if almacen is None:
        return _a_equipo(error="Ese almacén no existe.")

    usuarios = (
        await sesion.execute(
            text(
                """
                SELECT u.id, u.codigo, u.nombre, u.rol_codigo, a.codigo AS su_camion
                  FROM usuarios u LEFT JOIN almacenes a ON a.id = u.almacen_id
                 WHERE u.activo ORDER BY u.codigo
                """
            )
        )
    ).mappings().all()

    return render(
        peticion,
        "equipo_almacen.html",
        {
            "a": almacen,
            "existencia": await _existencia(sesion, almacen_id),
            "con_movimientos": await _tiene_movimientos(sesion, almacen_id),
            "usuarios": usuarios,
            "tipos": TIPOS_DE_ALMACEN,
            "guardado": guardado,
            "error": error,
        },
        actor=actor,
        seccion="Usuarios y rutas",
    )


@router.post("/almacenes/{almacen_id}/datos")
async def editar_almacen(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    almacen_id: uuid.UUID,
    codigo: Annotated[str, Form()] = "",
    nombre: Annotated[str, Form()] = "",
    tipo: Annotated[str, Form()] = "",
    responsable_id: Annotated[str, Form()] = "",
    activo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Código, nombre, tipo, responsable y si está activo.

    ───────────────────────────────────────────────────────────────────────
    EL RESPONSABLE DE UN CAMIÓN
    ───────────────────────────────────────────────────────────────────────
    El camión es propiedad exclusiva de su responsable (§0.2) y está atado en los
    dos sentidos: `almacenes.responsable_id` y `usuarios.almacen_id`. Cambiarlo
    mueve los dos, y el teléfono del vendedor se entera por el delta de
    `identidad`, que le **reinicia el inventario local**. Por eso solo se permite
    con el camión vacío: con mercancía arriba, el nuevo responsable vería su
    camión en cero y no podría venderla.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    antes = (
        await sesion.execute(
            text(
                "SELECT codigo, nombre, tipo, responsable_id, activo "
                "  FROM almacenes WHERE id = :a FOR UPDATE"
            ),
            {"a": almacen_id},
        )
    ).mappings().first()
    if antes is None:
        return _a_equipo(error="Ese almacén no existe.")

    clave = _clave(codigo)
    if not clave or not (nombre or "").strip():
        return _a_ficha("almacenes", almacen_id, error="El almacén necesita código y nombre.")
    if tipo not in TIPOS_DE_ALMACEN:
        return _a_ficha("almacenes", almacen_id, error="Tipo de almacén inválido.")
    choca = (
        await sesion.execute(
            text("SELECT nombre FROM almacenes WHERE codigo = :c AND id <> :a"),
            {"c": clave, "a": almacen_id},
        )
    ).scalar_one_or_none()
    if choca is not None:
        return _a_ficha("almacenes", almacen_id, error=f"El código {clave} ya lo tiene «{choca}».")

    existencia = await _existencia(sesion, almacen_id)
    con_mercancia = existencia["productos"] > 0

    # El tipo cambia lo que el almacén SIGNIFICA para el libro mayor y el Corte:
    # una bodega que se vuelve camión con mercancía y movimientos reescribiría la
    # historia de ambos.
    if tipo != antes["tipo"] and (con_mercancia or await _tiene_movimientos(sesion, almacen_id)):
        return _a_ficha(
            "almacenes",
            almacen_id,
            error="El tipo solo se cambia en un almacén que nunca se ha movido. Crea uno "
            "nuevo del tipo que necesitas.",
        )

    responsable = uuid.UUID(responsable_id) if responsable_id else None
    if tipo == "camion" and responsable is None:
        return _a_ficha(
            "almacenes",
            almacen_id,
            error="Un camión necesita responsable: el dueño exclusivo del almacén es lo "
            "que hace que el trabajo offline no tenga conflictos.",
        )

    quiere_activo = bool(activo)
    if antes["activo"] and not quiere_activo and con_mercancia:
        return _a_ficha(
            "almacenes",
            almacen_id,
            error=f"Tiene mercancía ({existencia['productos']} producto(s)). Pásala a "
            "otro almacén o ajústala a cero antes de desactivarlo.",
        )

    cambia_responsable = tipo == "camion" and (
        responsable != antes["responsable_id"] or antes["tipo"] != "camion"
    )
    if cambia_responsable:
        if con_mercancia:
            return _a_ficha(
                "almacenes",
                almacen_id,
                error="El camión trae mercancía. Al cambiar de responsable, el teléfono "
                "del nuevo vendedor empieza su camión en cero y no podría venderla. "
                "Devuélvela a la bodega (o ajústala) y vuelve a cargársela al nuevo.",
            )
        nuevo = (
            await sesion.execute(
                text(
                    "SELECT u.rol_codigo, u.activo, u.almacen_id, a.codigo AS su_camion "
                    "  FROM usuarios u LEFT JOIN almacenes a ON a.id = u.almacen_id "
                    " WHERE u.id = :u"
                ),
                {"u": responsable},
            )
        ).mappings().first()
        if nuevo is None or not nuevo["activo"] or nuevo["rol_codigo"] != "vendedor":
            return _a_ficha(
                "almacenes", almacen_id, error="El responsable de un camión es un vendedor activo."
            )
        if nuevo["almacen_id"] is not None and nuevo["almacen_id"] != almacen_id:
            return _a_ficha(
                "almacenes",
                almacen_id,
                error=f"Ese vendedor ya tiene el camión {nuevo['su_camion']}. Un vendedor "
                "tiene un solo camión: cámbiale ése primero.",
            )

    await sesion.execute(
        text(
            "UPDATE almacenes SET codigo = :c, nombre = :n, tipo = :t, "
            "       responsable_id = :u, activo = :act WHERE id = :a"
        ),
        {
            "c": clave,
            "n": nombre.strip()[:120],
            "t": tipo,
            "u": responsable,
            "act": quiere_activo,
            "a": almacen_id,
        },
    )
    if antes["tipo"] == "camion" and tipo != "camion":
        # Dejó de ser camión (solo pasa vacío y sin movimientos): nadie lo tiene.
        await sesion.execute(
            text("UPDATE usuarios SET almacen_id = NULL WHERE almacen_id = :a"),
            {"a": almacen_id},
        )
    if tipo == "camion":
        # Las dos puntas de la atadura. El viejo deja de tener camión; el nuevo lo
        # recibe, y su teléfono se entera por el delta de `identidad`.
        if antes["responsable_id"] and antes["responsable_id"] != responsable:
            await sesion.execute(
                text("UPDATE usuarios SET almacen_id = NULL WHERE id = :u AND almacen_id = :a"),
                {"u": antes["responsable_id"], "a": almacen_id},
            )
        if quiere_activo:
            await sesion.execute(
                text("UPDATE usuarios SET almacen_id = :a WHERE id = :u"),
                {"a": almacen_id, "u": responsable},
            )
        else:
            await sesion.execute(
                text("UPDATE usuarios SET almacen_id = NULL WHERE almacen_id = :a"),
                {"a": almacen_id},
            )
    await auditar(
        sesion,
        entidad="almacen",
        entidad_id=almacen_id,
        accion="editar",
        quien=actor.usuario_id,
        antes=dict(antes),
        despues={
            "codigo": clave,
            "nombre": nombre.strip(),
            "tipo": tipo,
            "responsable_id": responsable,
            "activo": quiere_activo,
        },
    )
    await sesion.commit()
    aviso = f"Almacén {clave} guardado."
    if cambia_responsable:
        aviso += " El camión cambió de responsable: su teléfono lo recibe al sincronizar."
    return _a_ficha("almacenes", almacen_id, guardado=aviso)


@router.post("/almacenes/{almacen_id}/eliminar")
async def eliminar_almacen(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    almacen_id: uuid.UUID,
    confirmo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Vacío y sin movimientos se borra; con historia se da de baja; con mercancía, no."""
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)
    if not confirmo:
        return _a_ficha("almacenes", almacen_id, error="Marca la casilla para confirmar.")

    almacen = (
        await sesion.execute(
            text("SELECT codigo, nombre, tipo FROM almacenes WHERE id = :a FOR UPDATE"),
            {"a": almacen_id},
        )
    ).mappings().first()
    if almacen is None:
        return _a_equipo(error="Ese almacén no existe.")
    existencia = await _existencia(sesion, almacen_id)
    if existencia["productos"]:
        return _a_ficha(
            "almacenes",
            almacen_id,
            error=f"Tiene mercancía ({existencia['productos']} producto(s)): borrarlo la "
            "desaparecería del inventario. Pásala a otro almacén o ajústala a cero primero.",
        )

    # Los renglones de existencia en cero no son historia: son la caché vacía.
    # El libro mayor sí lo es, y si tiene movimientos el DELETE no pasará.
    borrado = await borrar_si_nadie_lo_usa(
        sesion,
        "WITH limpia AS (DELETE FROM existencias WHERE almacen_id = :a AND cantidad = 0) "
        "DELETE FROM almacenes WHERE id = :a",
        {"a": almacen_id},
    )
    if borrado:
        await sesion.execute(
            text("UPDATE usuarios SET almacen_id = NULL WHERE almacen_id = :a"),
            {"a": almacen_id},
        )
    else:
        await sesion.execute(
            text("UPDATE almacenes SET activo = false WHERE id = :a"), {"a": almacen_id}
        )
        await sesion.execute(
            text("UPDATE usuarios SET almacen_id = NULL WHERE almacen_id = :a"),
            {"a": almacen_id},
        )
    await auditar(
        sesion,
        entidad="almacen",
        entidad_id=almacen_id,
        accion="eliminar" if borrado else "dar_de_baja",
        quien=actor.usuario_id,
        antes=dict(almacen),
    )
    await sesion.commit()
    if borrado:
        return _a_equipo(guardado=f"Almacén {almacen['codigo']} eliminado.")
    return _a_equipo(
        guardado=f"El almacén {almacen['codigo']} tiene movimientos en el libro mayor, así "
        "que se dio de baja en vez de borrarse: sale de los selectores y su historia queda."
    )


# ===========================================================================
# Listas de precios
# ===========================================================================


@router.get("/listas/{lista_id}", response_class=HTMLResponse)
async def ficha_lista(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    lista_id: uuid.UUID,
    guardado: str = "",
    error: str = "",
) -> HTMLResponse:
    actor.exigir("catalogo.administrar")
    lista = (
        await sesion.execute(
            text(
                """
                SELECT l.*,
                       (SELECT count(DISTINCT producto_id) FROM precios p
                         WHERE p.lista_id = l.id) AS productos,
                       (SELECT count(*) FROM clientes c
                         WHERE c.lista_precios_id = l.id AND c.estatus <> 'baja') AS clientes,
                       (SELECT count(*) FROM clientes c
                         WHERE c.lista_precios_id IS NULL AND c.estatus <> 'baja')
                         AS clientes_sin_lista
                  FROM listas_precios l WHERE l.id = :l
                """
            ),
            {"l": lista_id},
        )
    ).mappings().first()
    if lista is None:
        return _a_equipo(error="Esa lista no existe.")
    otras = (
        await sesion.execute(
            text(
                "SELECT id, codigo, nombre FROM listas_precios "
                " WHERE activo AND id <> :l ORDER BY codigo"
            ),
            {"l": lista_id},
        )
    ).mappings().all()
    return render(
        peticion,
        "equipo_lista.html",
        {"l": lista, "otras": otras, "guardado": guardado, "error": error},
        actor=actor,
        seccion="Usuarios y rutas",
    )


@router.post("/listas/{lista_id}/datos")
async def editar_lista(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    lista_id: uuid.UUID,
    codigo: Annotated[str, Form()] = "",
    nombre: Annotated[str, Form()] = "",
    activo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    actor.exigir("catalogo.administrar")
    exigir_csrf(peticion, csrf)
    antes = (
        await sesion.execute(
            text(
                "SELECT codigo, nombre, activo, es_default, "
                "       (SELECT count(*) FROM clientes c WHERE c.lista_precios_id = l.id "
                "           AND c.estatus <> 'baja') AS clientes "
                "  FROM listas_precios l WHERE id = :l FOR UPDATE"
            ),
            {"l": lista_id},
        )
    ).mappings().first()
    if antes is None:
        return _a_equipo(error="Esa lista no existe.")
    clave = _clave(codigo)
    if not clave or not (nombre or "").strip():
        return _a_ficha("listas", lista_id, error="La lista necesita código y nombre.")
    choca = (
        await sesion.execute(
            text("SELECT nombre FROM listas_precios WHERE codigo = :c AND id <> :l"),
            {"c": clave, "l": lista_id},
        )
    ).scalar_one_or_none()
    if choca is not None:
        return _a_ficha("listas", lista_id, error=f"El código {clave} ya lo tiene «{choca}».")

    quiere_activa = bool(activo)
    if antes["activo"] and not quiere_activa:
        if antes["es_default"]:
            return _a_ficha(
                "listas",
                lista_id,
                error="Es la lista por omisión: los clientes sin lista cotizan con ella. "
                "Marca otra como por omisión primero.",
            )
        if antes["clientes"]:
            return _a_ficha(
                "listas",
                lista_id,
                error=f"La usan {antes['clientes']} cliente(s): sin lista activa no pueden "
                "comprar. Pásalos a otra lista abajo primero.",
            )

    await sesion.execute(
        text("UPDATE listas_precios SET codigo = :c, nombre = :n, activo = :a WHERE id = :l"),
        {"c": clave, "n": nombre.strip()[:120], "a": quiere_activa, "l": lista_id},
    )
    await auditar(
        sesion,
        entidad="lista_precios",
        entidad_id=lista_id,
        accion="editar",
        quien=actor.usuario_id,
        antes={k: antes[k] for k in ("codigo", "nombre", "activo")},
        despues={"codigo": clave, "nombre": nombre.strip(), "activo": quiere_activa},
    )
    await sesion.commit()
    return _a_ficha("listas", lista_id, guardado=f"Lista {clave} guardada.")


@router.post("/listas/{lista_id}/por-omision")
async def hacer_por_omision(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    lista_id: uuid.UUID,
    confirmo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """La lista con la que cotiza un cliente que no tiene lista asignada.

    Cambiarla mueve el precio de todos esos clientes de golpe, así que va en su
    propio formulario, con la cuenta de cuántos son y una casilla de
    confirmación, y nunca al lado de un formulario de alta.
    """
    actor.exigir("catalogo.administrar")
    exigir_csrf(peticion, csrf)
    if not confirmo:
        return _a_ficha(
            "listas", lista_id, error="Marca la casilla: esto cambia el precio de varios clientes."
        )
    lista = (
        await sesion.execute(
            text("SELECT codigo, activo FROM listas_precios WHERE id = :l FOR UPDATE"),
            {"l": lista_id},
        )
    ).mappings().first()
    if lista is None or not lista["activo"]:
        return _a_ficha("listas", lista_id, error="Solo una lista activa puede ser la de omisión.")

    # Primero se apaga la anterior: el índice único parcial no deja dos.
    await sesion.execute(
        text("UPDATE listas_precios SET es_default = false WHERE es_default AND id <> :l"),
        {"l": lista_id},
    )
    await sesion.execute(
        text("UPDATE listas_precios SET es_default = true WHERE id = :l"), {"l": lista_id}
    )
    await auditar(
        sesion,
        entidad="lista_precios",
        entidad_id=lista_id,
        accion="por_omision",
        quien=actor.usuario_id,
    )
    await sesion.commit()
    return _a_ficha(
        "listas",
        lista_id,
        guardado=f"{lista['codigo']} es ahora la lista por omisión. Los teléfonos la "
        "reciben en su siguiente sincronización.",
    )


@router.post("/listas/{lista_id}/mover-clientes")
async def mover_clientes_de_lista(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    lista_id: uuid.UUID,
    destino_id: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Pasa a otra lista a todos los clientes que usan ésta."""
    actor.exigir("catalogo.administrar")
    exigir_csrf(peticion, csrf)
    try:
        destino = uuid.UUID(destino_id)
    except ValueError:
        return _a_ficha("listas", lista_id, error="Elige a qué lista pasan.")
    nueva = (
        await sesion.execute(
            text("SELECT codigo FROM listas_precios WHERE id = :l AND activo AND id <> :o"),
            {"l": destino, "o": lista_id},
        )
    ).scalar_one_or_none()
    if nueva is None:
        return _a_ficha("listas", lista_id, error="La lista destino no existe o está inactiva.")
    movidos = (
        await sesion.execute(
            text(
                "UPDATE clientes SET lista_precios_id = :d, actualizado_en = now() "
                " WHERE lista_precios_id = :l AND estatus <> 'baja'"
            ),
            {"d": destino, "l": lista_id},
        )
    ).rowcount
    await auditar(
        sesion,
        entidad="lista_precios",
        entidad_id=lista_id,
        accion="mover_clientes",
        quien=actor.usuario_id,
        despues={"destino": str(destino), "clientes": movidos},
    )
    await sesion.commit()
    return _a_ficha(
        "listas",
        lista_id,
        guardado=f"{movidos} cliente(s) pasaron a {nueva}: desde su siguiente "
        "sincronización se les cotiza con esa lista.",
    )


@router.post("/listas/{lista_id}/eliminar")
async def eliminar_lista(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    lista_id: uuid.UUID,
    confirmo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Sin clientes y sin ventas, se borra con sus precios; con historia, se da de baja."""
    actor.exigir("catalogo.administrar")
    exigir_csrf(peticion, csrf)
    if not confirmo:
        return _a_ficha("listas", lista_id, error="Marca la casilla para confirmar.")
    lista = (
        await sesion.execute(
            text(
                "SELECT codigo, nombre, es_default, "
                "       (SELECT count(*) FROM clientes c WHERE c.lista_precios_id = l.id "
                "           AND c.estatus <> 'baja') AS clientes "
                "  FROM listas_precios l WHERE id = :l FOR UPDATE"
            ),
            {"l": lista_id},
        )
    ).mappings().first()
    if lista is None:
        return _a_equipo(error="Esa lista no existe.")
    if lista["es_default"]:
        return _a_ficha(
            "listas",
            lista_id,
            error="Es la lista por omisión: marca otra como por omisión primero.",
        )
    if lista["clientes"]:
        return _a_ficha(
            "listas",
            lista_id,
            error=f"La usan {lista['clientes']} cliente(s): pásalos a otra lista abajo primero.",
        )

    # Sus precios se van con ella (ON DELETE CASCADE), y cada uno publica su
    # borrado: los teléfonos los quitan en la siguiente sincronización.
    borrada = await borrar_si_nadie_lo_usa(
        sesion, "DELETE FROM listas_precios WHERE id = :l", {"l": lista_id}
    )
    if not borrada:
        await sesion.execute(
            text("UPDATE listas_precios SET activo = false WHERE id = :l"), {"l": lista_id}
        )
    await auditar(
        sesion,
        entidad="lista_precios",
        entidad_id=lista_id,
        accion="eliminar" if borrada else "dar_de_baja",
        quien=actor.usuario_id,
        antes={"codigo": lista["codigo"], "nombre": lista["nombre"]},
    )
    await sesion.commit()
    if borrada:
        return _a_equipo(guardado=f"Lista {lista['codigo']} eliminada con sus precios.")
    return _a_equipo(
        guardado=f"La lista {lista['codigo']} ya tiene ventas, así que se dio de baja en "
        "vez de borrarse."
    )
