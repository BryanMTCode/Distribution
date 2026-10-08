"""Usuarios, rutas y almacenes: la estructura sobre la que corre todo lo demás.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTAS TRES COSAS VAN JUNTAS
────────────────────────────────────────────────────────────────────────────
Para que un vendedor pueda salir a la calle hacen falta tres filas atadas entre
sí, y si falta una el sistema no avisa: simplemente no deja hacer el trabajo.

  usuario (rol vendedor)  →  sin él no hay quién venda
  almacén tipo camión     →  sin él la carga no tiene a dónde ir
  ruta                    →  sin ella el teléfono no recibe clientes

Hasta hoy las tres se creaban con `INSERT` a mano. Un sistema que exige psql para
dar de alta a un empleado no está terminado, por más que lo demás funcione.

Y el orden importa: un almacén de tipo camión **exige responsable**
(`camion_requiere_responsable`, migración 0004), así que el usuario va primero. El
panel lo hace en un solo paso para que nadie tenga que saberlo.

────────────────────────────────────────────────────────────────────────────
LO QUE ESTA PANTALLA NO HACE
────────────────────────────────────────────────────────────────────────────
**No muestra ni exporta contraseñas.** Lo único que se puede hacer es
reemplazarlas: `usuarios.password_hash` es un hash de Argon2id y no hay vuelta
atrás, que es exactamente la propiedad que se busca. Si alguien olvida la suya, se
le pone otra; no se le recupera la anterior.

**No borra usuarios.** Un usuario tiene ventas, cobros y movimientos de inventario
firmados con su id. Se desactiva, y con eso sus sesiones mueren en la siguiente
petición y su dispositivo deja de poder sincronizar.
"""

from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.api.admin.comun import (
    CapturaInvalida,
    SesionDep,
    leer_entero,
    render,
)
from app.api.admin.sesion_web import ActorWeb, exigir_csrf
from app.core.seguridad import hashear_password

router = APIRouter(prefix="/panel/equipo", tags=["panel"], include_in_schema=False)

PERMISO = "usuarios.administrar"

# El mismo mínimo que exige el comando de arranque (`app/cli.py`). Doce
# caracteres es donde una frase corta ya resiste fuerza bruta offline con los
# parámetros de Argon2 de este sistema — y el hash viaja al teléfono para el login
# sin red, así que una contraseña débil se puede atacar con el equipo en la mano.
MINIMO_PASSWORD = 12

ROLES = [
    ("vendedor", "Vendedor", "Opera una ruta con su camión. Entra por la app, no por el panel."),
    ("supervisor", "Supervisor", "Carga, corte del día, cuarentena y transferencias."),
    ("gerente", "Gerencia", "Solo lectura sobre la operación."),
    ("admin", "Administrador", "Todos los permisos, incluido dar de alta usuarios."),
]


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
    """Las tres tablas en una pantalla, con los huecos marcados.

    Un vendedor sin camión o sin ruta se ve igual que uno completo en una lista de
    usuarios. Aquí se marca, porque es la diferencia entre poder trabajar y no.
    """
    usuarios = (
        await sesion.execute(
            text(
                """
                SELECT u.id, u.codigo, u.nombre, u.rol_codigo, u.activo,
                       u.dias_max_offline,
                       a.nombre AS camion, a.codigo AS camion_codigo,
                       s.nombre AS sucursal,
                       COALESCE(r.rutas, '') AS rutas,
                       COALESCE(d.equipos, 0) AS equipos
                  FROM usuarios u
                  LEFT JOIN almacenes a ON a.id = u.almacen_id
                  LEFT JOIN sucursales s ON s.id = u.sucursal_id
                  LEFT JOIN LATERAL (
                        SELECT string_agg(ru.codigo, ', ' ORDER BY ru.codigo) AS rutas
                          FROM usuarios_rutas ur
                          JOIN rutas ru ON ru.id = ur.ruta_id
                         WHERE ur.usuario_id = u.id
                  ) r ON true
                  LEFT JOIN LATERAL (
                        SELECT count(*) AS equipos FROM dispositivos
                         WHERE usuario_id = u.id AND estado = 'activo'
                  ) d ON true
                 ORDER BY u.activo DESC, u.rol_codigo, u.codigo
                """
            )
        )
    ).mappings().all()

    rutas = (
        await sesion.execute(
            text(
                """
                SELECT r.id, r.codigo, r.nombre, r.activo,
                       u.nombre AS vendedor, u.codigo AS vendedor_codigo,
                       COALESCE(c.clientes, 0) AS clientes
                  FROM rutas r
                  LEFT JOIN usuarios u ON u.id = r.vendedor_id
                  LEFT JOIN LATERAL (
                        SELECT count(*) AS clientes FROM clientes
                         WHERE ruta_id = r.id AND estatus <> 'inactivo'
                  ) c ON true
                 ORDER BY r.activo DESC, r.codigo
                """
            )
        )
    ).mappings().all()

    almacenes = (
        await sesion.execute(
            text(
                """
                SELECT a.id, a.codigo, a.nombre, a.tipo, a.activo,
                       u.nombre AS responsable,
                       COALESCE(e.productos, 0) AS productos,
                       COALESCE(e.negativos, 0) AS negativos
                  FROM almacenes a
                  LEFT JOIN usuarios u ON u.id = a.responsable_id
                  LEFT JOIN LATERAL (
                        SELECT count(*) FILTER (WHERE cantidad <> 0) AS productos,
                               count(*) FILTER (WHERE cantidad < 0) AS negativos
                          FROM existencias WHERE almacen_id = a.id
                  ) e ON true
                 ORDER BY a.activo DESC, a.tipo, a.codigo
                """
            )
        )
    ).mappings().all()

    listas = (
        await sesion.execute(
            text(
                """
                SELECT l.id, l.codigo, l.nombre, l.es_default, l.activo,
                       COALESCE(p.productos, 0) AS productos,
                       COALESCE(c.clientes, 0) AS clientes
                  FROM listas_precios l
                  LEFT JOIN LATERAL (
                        SELECT count(DISTINCT producto_id) AS productos
                          FROM precios WHERE lista_id = l.id
                  ) p ON true
                  LEFT JOIN LATERAL (
                        SELECT count(*) AS clientes FROM clientes
                         WHERE lista_precios_id = l.id AND estatus <> 'inactivo'
                  ) c ON true
                 ORDER BY l.es_default DESC, l.codigo
                """
            )
        )
    ).mappings().all()

    return render(
        peticion,
        "equipo.html",
        {
            "usuarios": usuarios,
            "rutas": rutas,
            "almacenes": almacenes,
            "listas": listas,
            "roles": ROLES,
            "sucursales": await _sucursales(sesion),
            "vendedores_sin_camion": [
                u for u in usuarios if u["rol_codigo"] == "vendedor" and not u["camion"]
            ],
            "vendedores_sin_ruta": [
                u for u in usuarios if u["rol_codigo"] == "vendedor" and not u["rutas"]
            ],
            "minimo_password": MINIMO_PASSWORD,
            "puede_editar": actor.puede(PERMISO),
            "error": error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Usuarios y rutas",
    )


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------


@router.post("/usuarios")
async def crear_usuario(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    codigo: Annotated[str, Form()] = "",
    nombre: Annotated[str, Form()] = "",
    rol_codigo: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    sucursal_id: Annotated[str, Form()] = "",
    dias_max_offline: Annotated[str, Form()] = "7",
    con_camion: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Da de alta un usuario, y de paso su camión si es vendedor.

    ────────────────────────────────────────────────────────────────────────
    EL CÓDIGO ES EL PREFIJO DEL FOLIO IMPRESO
    ────────────────────────────────────────────────────────────────────────
    `usuarios.codigo` no es solo con lo que se entra: es el espacio de nombres de
    los folios locales que el teléfono imprime (`VEND01-000077`). Por eso se
    normaliza a mayúsculas y sin espacios — un código con un espacio produciría un
    folio con un espacio en medio, en un papel que el cliente dicta por teléfono.

    Y por eso **no se puede cambiar después**: hay tickets en la calle con ese
    prefijo.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    clave = codigo.strip().upper().replace(" ", "")
    if not clave:
        return _volver(error="Falta el código de empleado: es con lo que entra.")
    if not nombre.strip():
        return _volver(error="Falta el nombre.")
    if rol_codigo not in {r[0] for r in ROLES}:
        return _volver(error="Elige un rol válido.")
    if len(password) < MINIMO_PASSWORD:
        return _volver(
            error=f"La contraseña necesita al menos {MINIMO_PASSWORD} caracteres. "
            "Una frase corta sirve, y el hash viaja al teléfono para el login sin "
            "red: una contraseña débil se puede atacar con el equipo en la mano."
        )

    try:
        dias = leer_entero(dias_max_offline, campo="Los días sin sincronizar", maximo=30)
    except CapturaInvalida as e:
        return _volver(error=str(e))
    if dias < 1:
        dias = 7

    existe = (
        await sesion.execute(
            text("SELECT nombre FROM usuarios WHERE codigo = :c"), {"c": clave}
        )
    ).scalar_one_or_none()
    if existe is not None:
        return _volver(error=f"El código {clave} ya lo tiene «{existe}».")

    sucursal = uuid.UUID(sucursal_id) if sucursal_id else await _sucursal_por_omision(sesion)

    usuario_id = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
            "                      rol_codigo, dias_max_offline) "
            "VALUES (:id, :s, :c, :n, :h, :r, :d)"
        ),
        {
            "id": usuario_id,
            "s": sucursal,
            "c": clave,
            "n": nombre.strip()[:120],
            "h": hashear_password(password),
            "r": rol_codigo,
            "d": dias,
        },
    )

    aviso = f"{clave} creado como {rol_codigo}."

    # El camión, en el mismo paso. `camion_requiere_responsable` exige que el
    # usuario exista antes, y quien da de alta a un vendedor no tiene por qué
    # saber eso: lo que quiere es un vendedor que pueda trabajar.
    if rol_codigo == "vendedor" and con_camion:
        almacen_id = uuid.uuid4()
        codigo_camion = f"CAMION_{clave}"
        ya = (
            await sesion.execute(
                text("SELECT 1 FROM almacenes WHERE codigo = :c"), {"c": codigo_camion}
            )
        ).first()
        if ya is None:
            await sesion.execute(
                text(
                    "INSERT INTO almacenes (id, codigo, nombre, tipo, sucursal_id, "
                    "                       responsable_id) "
                    "VALUES (:id, :c, :n, 'camion', :s, :u)"
                ),
                {
                    "id": almacen_id,
                    "c": codigo_camion,
                    "n": f"Camión de {nombre.strip()}",
                    "s": sucursal,
                    "u": usuario_id,
                },
            )
            await sesion.execute(
                text("UPDATE usuarios SET almacen_id = :a WHERE id = :u"),
                {"a": almacen_id, "u": usuario_id},
            )
            aviso += " Se le creó su camión."

    await sesion.commit()
    return _volver(guardado=aviso)


@router.post("/usuarios/{usuario_id}/password")
async def cambiar_password(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    usuario_id: uuid.UUID,
    password: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Reemplaza la contraseña. No hay forma de recuperar la anterior.

    El hash **también viaja al teléfono** para el login sin red, así que el cambio
    no surte efecto en el dispositivo hasta que sincronice. Mientras no lo haga,
    el vendedor sigue entrando con la vieja — y eso es correcto: si el cambio
    bloqueara el acceso offline de inmediato, cambiar una contraseña dejaría a un
    vendedor sin poder trabajar en media ruta.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    if len(password) < MINIMO_PASSWORD:
        return _volver(error=f"La contraseña necesita al menos {MINIMO_PASSWORD} caracteres.")

    fila = (
        await sesion.execute(
            text("SELECT codigo FROM usuarios WHERE id = :u"), {"u": usuario_id}
        )
    ).scalar_one_or_none()
    if fila is None:
        return _volver(error="Ese usuario no existe.")

    await sesion.execute(
        text(
            "UPDATE usuarios SET password_hash = :h, actualizado_en = now() WHERE id = :u"
        ),
        {"h": hashear_password(password), "u": usuario_id},
    )
    await sesion.commit()
    return _volver(
        guardado=f"Contraseña de {fila} cambiada. En su teléfono surte efecto "
        "cuando sincronice: hasta entonces entra con la anterior, y así no se "
        "queda sin poder trabajar a media ruta."
    )


@router.post("/usuarios/{usuario_id}/activo")
async def cambiar_activo(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    usuario_id: uuid.UUID,
    activo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Activa o desactiva. **No borra.**

    Un usuario tiene ventas, cobros y movimientos de inventario firmados con su
    id. Desactivarlo corta el acceso sin perder de quién fue cada documento: sus
    sesiones del panel mueren en la siguiente petición —por eso la sesión tiene
    fila en la base y no es un JWT— y su token de dispositivo deja de servir.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    quiere_activo = bool(activo)

    if not quiere_activo and usuario_id == actor.usuario_id:
        # Desactivarse a uno mismo deja la oficina sin nadie que pueda reactivar a
        # nadie, y el arreglo sale por línea de comandos en el servidor.
        return _volver(
            error="No puedes desactivarte a ti mismo: te quedarías fuera del panel "
            "y reactivarte exigiría entrar al servidor."
        )

    if not quiere_activo:
        admins = (
            await sesion.execute(
                text(
                    "SELECT count(*) FROM usuarios "
                    " WHERE rol_codigo = 'admin' AND activo AND id <> :u"
                ),
                {"u": usuario_id},
            )
        ).scalar_one()
        es_admin = (
            await sesion.execute(
                text("SELECT rol_codigo FROM usuarios WHERE id = :u"), {"u": usuario_id}
            )
        ).scalar_one_or_none()
        if es_admin == "admin" and admins == 0:
            return _volver(
                error="Es el último administrador activo. Sin él nadie puede dar de "
                "alta usuarios ni reactivar a nadie."
            )

    await sesion.execute(
        text("UPDATE usuarios SET activo = :a, actualizado_en = now() WHERE id = :u"),
        {"a": quiere_activo, "u": usuario_id},
    )
    if not quiere_activo:
        # Las sesiones del panel se revocan de una vez. La fila en `sesiones` es lo
        # que hace esto posible: un JWT firmado seguiría siendo válido hasta
        # expirar, haga lo que haga la oficina.
        await sesion.execute(
            text(
                "UPDATE sesiones SET revocada_en = now() "
                " WHERE usuario_id = :u AND revocada_en IS NULL"
            ),
            {"u": usuario_id},
        )
    await sesion.commit()
    return _volver(
        guardado="Usuario activado." if quiere_activo else
        "Usuario desactivado y sus sesiones del panel revocadas."
    )


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------


@router.post("/rutas")
async def crear_ruta(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    codigo: Annotated[str, Form()] = "",
    nombre: Annotated[str, Form()] = "",
    vendedor_id: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Crea la ruta y, si trae titular, le da el alcance en el mismo paso.

    `rutas.vendedor_id` dice quién es el titular; `usuarios_rutas` es lo que el
    *scope guard* consulta para filtrar datos. **Son dos cosas distintas y las dos
    hacen falta**: con solo la primera, el vendedor aparece como titular y su
    teléfono no recibe un solo cliente de esa ruta, porque el filtro del pull es
    `ruta_id = ANY(:rutas)` y esa lista sale de `usuarios_rutas`.

    Es el error que se comete una vez y cuesta una tarde de depuración con el
    teléfono en la mano, así que el panel escribe las dos.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    clave = codigo.strip().upper().replace(" ", "")
    if not clave:
        return _volver(error="Falta el código de la ruta (por ejemplo R04).")
    if not nombre.strip():
        return _volver(error="Falta el nombre de la ruta.")

    existe = (
        await sesion.execute(
            text("SELECT nombre FROM rutas WHERE codigo = :c"), {"c": clave}
        )
    ).scalar_one_or_none()
    if existe is not None:
        return _volver(error=f"La ruta {clave} ya existe: «{existe}».")

    titular = uuid.UUID(vendedor_id) if vendedor_id else None
    ruta_id = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO rutas (id, codigo, nombre, sucursal_id, vendedor_id) "
            "VALUES (:id, :c, :n, :s, :v)"
        ),
        {
            "id": ruta_id,
            "c": clave,
            "n": nombre.strip()[:120],
            "s": await _sucursal_por_omision(sesion),
            "v": titular,
        },
    )
    if titular is not None:
        await sesion.execute(
            text(
                "INSERT INTO usuarios_rutas (usuario_id, ruta_id) VALUES (:u, :r) "
                "ON CONFLICT DO NOTHING"
            ),
            {"u": titular, "r": ruta_id},
        )
    await sesion.commit()
    return _volver(
        guardado=f"Ruta {clave} creada."
        + (" Su titular ya la tiene en su alcance." if titular else
           " Sin titular todavía: ningún teléfono va a recibir sus clientes.")
    )


@router.post("/rutas/{ruta_id}/titular")
async def asignar_titular(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    ruta_id: uuid.UUID,
    vendedor_id: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Cambia el titular de una ruta, y mueve el alcance con él.

    Al titular anterior **se le quita** `usuarios_rutas`: si se le dejara, su
    teléfono seguiría recibiendo los clientes de una ruta que ya no trabaja, y
    podría venderles. Los clientes no se mueven: siguen siendo de la ruta.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    anterior = (
        await sesion.execute(
            text("SELECT vendedor_id, codigo FROM rutas WHERE id = :r"), {"r": ruta_id}
        )
    ).mappings().first()
    if anterior is None:
        return _volver(error="Esa ruta no existe.")

    nuevo = uuid.UUID(vendedor_id) if vendedor_id else None

    await sesion.execute(
        text("UPDATE rutas SET vendedor_id = :v WHERE id = :r"), {"v": nuevo, "r": ruta_id}
    )
    if anterior["vendedor_id"] is not None and anterior["vendedor_id"] != nuevo:
        await sesion.execute(
            text("DELETE FROM usuarios_rutas WHERE usuario_id = :u AND ruta_id = :r"),
            {"u": anterior["vendedor_id"], "r": ruta_id},
        )
    if nuevo is not None:
        await sesion.execute(
            text(
                "INSERT INTO usuarios_rutas (usuario_id, ruta_id) VALUES (:u, :r) "
                "ON CONFLICT DO NOTHING"
            ),
            {"u": nuevo, "r": ruta_id},
        )
    await sesion.commit()
    return _volver(
        guardado=f"Ruta {anterior['codigo']}: titular actualizado y alcance movido."
    )


# ---------------------------------------------------------------------------
# Almacenes
# ---------------------------------------------------------------------------


@router.post("/almacenes")
async def crear_almacen(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    codigo: Annotated[str, Form()] = "",
    nombre: Annotated[str, Form()] = "",
    tipo: Annotated[str, Form()] = "bodega",
    responsable_id: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Crea un almacén.

    Un **camión exige responsable** y además se le ata como almacén propio del
    usuario (`usuarios.almacen_id`): esa es la propiedad exclusiva de §0.2, la
    garantía de que nadie más escribe sobre ese inventario, y es lo que permite
    que el trabajo offline no tenga conflictos que resolver.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    if tipo not in ("bodega", "camion", "transito", "merma"):
        return _volver(error="Tipo de almacén inválido.")

    clave = codigo.strip().upper().replace(" ", "")
    if not clave:
        return _volver(error="Falta el código del almacén.")
    if not nombre.strip():
        return _volver(error="Falta el nombre del almacén.")

    responsable = uuid.UUID(responsable_id) if responsable_id else None
    if tipo == "camion" and responsable is None:
        return _volver(
            error="Un camión necesita responsable: el dueño exclusivo del almacén es "
            "lo que hace que el trabajo offline no tenga conflictos."
        )

    existe = (
        await sesion.execute(
            text("SELECT nombre FROM almacenes WHERE codigo = :c"), {"c": clave}
        )
    ).scalar_one_or_none()
    if existe is not None:
        return _volver(error=f"El almacén {clave} ya existe: «{existe}».")

    almacen_id = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO almacenes (id, codigo, nombre, tipo, sucursal_id, responsable_id) "
            "VALUES (:id, :c, :n, :t, :s, :u)"
        ),
        {
            "id": almacen_id,
            "c": clave,
            "n": nombre.strip()[:120],
            "t": tipo,
            "s": await _sucursal_por_omision(sesion),
            "u": responsable,
        },
    )
    if tipo == "camion":
        await sesion.execute(
            text("UPDATE usuarios SET almacen_id = :a WHERE id = :u"),
            {"a": almacen_id, "u": responsable},
        )
    await sesion.commit()
    return _volver(guardado=f"Almacén {clave} creado.")


# ---------------------------------------------------------------------------
# Listas de precios
# ---------------------------------------------------------------------------


@router.post("/listas")
async def crear_lista(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    codigo: Annotated[str, Form()] = "",
    nombre: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Crea una lista de precios.

    **No se puede marcar como la de omisión desde aquí.** La lista por omisión es
    la que se le cotiza a un cliente dado de alta en la calle, que nace sin lista
    asignada; cambiarla mueve el precio de todos esos clientes de golpe, sin que
    nadie lo pida. Es una decisión que merece su propio flujo, no un botón al lado
    de un formulario de alta.

    Y la nueva lista nace **sin un solo precio**: hay que capturarlos producto por
    producto, o copiarlos. Un cliente asignado a una lista vacía no puede comprar
    nada, así que la pantalla lo dice en la tabla.
    """
    actor.exigir("catalogo.administrar")
    exigir_csrf(peticion, csrf)

    clave = codigo.strip().upper().replace(" ", "")
    if not clave:
        return _volver(error="Falta el código de la lista.")
    if not nombre.strip():
        return _volver(error="Falta el nombre de la lista.")

    existe = (
        await sesion.execute(
            text("SELECT nombre FROM listas_precios WHERE codigo = :c"), {"c": clave}
        )
    ).scalar_one_or_none()
    if existe is not None:
        return _volver(error=f"La lista {clave} ya existe: «{existe}».")

    await sesion.execute(
        text(
            "INSERT INTO listas_precios (id, codigo, nombre) VALUES (:id, :c, :n)"
        ),
        {"id": uuid.uuid4(), "c": clave, "n": nombre.strip()[:120]},
    )
    await sesion.commit()
    return _volver(
        guardado=f"Lista {clave} creada, y vacía: hasta que tenga precios, un "
        "cliente asignado a ella no puede comprar nada."
    )


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------


def _volver(*, error: str = "", guardado: str = ""):
    destino = "/panel/equipo"
    if error:
        destino += f"?error={quote(error)}"
    elif guardado:
        destino += f"?guardado={quote(guardado)}"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


async def _sucursales(sesion) -> list[dict]:
    return (
        await sesion.execute(
            text("SELECT id, codigo, nombre FROM sucursales WHERE activo ORDER BY codigo")
        )
    ).mappings().all()


async def _sucursal_por_omision(sesion) -> uuid.UUID:
    """La primera sucursal, creándola si no hay ninguna.

    Un usuario o una ruta sin sucursal quedan fuera de cualquier reporte que
    agrupe por ella, y eso se descubre meses después con los números ya mal. Es
    más barato crear 'MATRIZ' aquí que explicar el hueco luego.
    """
    fila = (
        await sesion.execute(text("SELECT id FROM sucursales ORDER BY codigo LIMIT 1"))
    ).scalar_one_or_none()
    if fila is not None:
        return fila

    nueva = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO sucursales (id, codigo, nombre) VALUES (:id, 'MATRIZ', 'Matriz')"),
        {"id": nueva},
    )
    return nueva
