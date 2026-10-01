"""Usuarios, rutas y almacenes desde el panel, y el arranque del primer usuario.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
Tres filas tienen que quedar atadas para que un vendedor pueda trabajar, y las
dos ataduras que se olvidan no fallan: dejan al vendedor sin poder hacer su
trabajo y sin un mensaje que lo explique.

1. **`rutas.vendedor_id` NO es lo que filtra los datos.** El *scope guard* lee
   `usuarios_rutas`. Con solo el titular, el vendedor aparece como dueño de la
   ruta y su teléfono no recibe un solo cliente.
2. **Un camión exige responsable** y además tiene que quedar como
   `usuarios.almacen_id`. Si falta lo segundo, la carga no encuentra a dónde ir.

Y la parte de seguridad: que no exista un usuario por omisión, que una contraseña
corta no pase, y que desactivar a alguien corte su sesión de verdad.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR

pytestmark = pytest.mark.asyncio


# La que usa `_crear_usuario`. Distinta de la de la semilla a propósito: así una
# prueba que entre con el usuario equivocado falla en vez de pasar por casualidad.
PASSWORD_NUEVA = "camion rojo 14"


async def _entrar(cliente, codigo: str = "ADMIN01", password: str | None = None) -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": codigo, "password": password or PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


def _csrf(cliente, respuesta=None) -> str:
    marca = 'name="csrf" value="'
    if respuesta is not None and marca in respuesta.text:
        inicio = respuesta.text.index(marca) + len(marca)
        return respuesta.text[inicio : respuesta.text.index('"', inicio)]

    import hashlib
    import hmac

    from app.core.config import obtener_config

    return hmac.new(
        obtener_config().jwt_secreto.encode(),
        f"csrf:{cliente.cookies.get('dsd_panel', '')}".encode(),
        hashlib.sha256,
    ).hexdigest()


async def _crear_usuario(cliente, **campos):
    pantalla = await cliente.get("/panel/equipo")
    datos = {
        "csrf": _csrf(cliente, pantalla),
        "codigo": "VEND02",
        "nombre": "María López",
        "rol_codigo": "vendedor",
        "password": PASSWORD_NUEVA,
        "dias_max_offline": "7",
        "con_camion": "1",
    }
    datos.update(campos)
    return await cliente.post("/panel/equipo/usuarios", data=datos, follow_redirects=True)


# ---------------------------------------------------------------------------
# El arranque: no hay usuario por omisión
# ---------------------------------------------------------------------------


async def test_NINGUNA_MIGRACION_SIEMBRA_UN_USUARIO(sesion, esquema):  # noqa: ARG001
    """Un `admin / admin123` en una migración es una puerta trasera.

    La fila viaja a producción, nadie se acuerda de cambiarla, y queda un usuario
    con todos los permisos cuya contraseña está publicada en el repositorio. Con el
    panel detrás de un túnel, eso es acceso a la operación completa desde internet.

    Esta prueba corre **sin** el fixture `semilla`, así que ve la base tal como la
    dejan las migraciones.
    """
    usuarios = (await sesion.execute(text("SELECT count(*) FROM usuarios"))).scalar_one()
    assert usuarios == 0, (
        "alguna migración está sembrando usuarios. El primero se crea con "
        "`make usuario`, que pregunta la contraseña y no la deja en el repositorio."
    )

    # Pero los roles y permisos sí vienen sembrados: son catálogo, no credenciales.
    roles = (await sesion.execute(text("SELECT count(*) FROM roles"))).scalar_one()
    assert roles == 4


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------


async def test_crear_un_vendedor_le_crea_su_camion_en_el_mismo_paso(
    cliente, semilla, sesion
):
    """`camion_requiere_responsable` obliga a que el usuario exista primero.

    Quien da de alta a un vendedor no tiene por qué saber eso: lo que quiere es un
    vendedor que pueda trabajar.
    """
    await _entrar(cliente)
    r = await _crear_usuario(cliente)
    assert "Se le creó su camión" in r.text

    fila = (
        await sesion.execute(
            text(
                "SELECT u.codigo, u.rol_codigo, u.almacen_id, a.tipo, a.responsable_id "
                "  FROM usuarios u "
                "  LEFT JOIN almacenes a ON a.id = u.almacen_id "
                " WHERE u.codigo = 'VEND02'"
            )
        )
    ).mappings().one()

    assert fila["rol_codigo"] == "vendedor"
    assert fila["almacen_id"] is not None
    assert fila["tipo"] == "camion"
    # La atadura en los dos sentidos: el usuario apunta a su camión y el camión a
    # su dueño. Si faltara la segunda, el CHECK de la migración 0004 lo habría
    # rechazado; si faltara la primera, la carga no encontraría a dónde ir.
    assert fila["responsable_id"] is not None


async def test_el_codigo_se_normaliza_porque_es_el_prefijo_del_folio(
    cliente, semilla, sesion
):
    """Un código con espacios produciría `VEND 03-000077` en un papel que el
    cliente dicta por teléfono."""
    await _entrar(cliente)
    await _crear_usuario(cliente, codigo=" vend 03 ")

    codigo = (
        await sesion.execute(
            text("SELECT codigo FROM usuarios WHERE nombre = 'María López'")
        )
    ).scalar_one()
    assert codigo == "VEND03"


async def test_una_contrasena_corta_no_pasa(cliente, semilla, sesion):
    """El hash viaja al teléfono para el login sin red: una contraseña débil se
    puede atacar con el equipo en la mano, sin límite de intentos y sin red."""
    await _entrar(cliente)
    r = await _crear_usuario(cliente, password="corta")

    assert "al menos 12" in r.text
    existe = (
        await sesion.execute(
            text("SELECT count(*) FROM usuarios WHERE codigo = 'VEND02'")
        )
    ).scalar_one()
    assert existe == 0


async def test_un_codigo_repetido_se_explica(cliente, semilla):
    await _entrar(cliente)
    r = await _crear_usuario(cliente, codigo="VEND01")
    assert "ya lo tiene" in r.text
    assert "Juan Pérez" in r.text


async def test_la_contrasena_nueva_sirve_para_entrar(cliente, semilla, sesion):
    """De punta a punta: se cambia desde el panel y el login la acepta."""
    await _entrar(cliente)
    pantalla = await cliente.get("/panel/equipo")

    admin = semilla["admin"]
    r = await cliente.post(
        f"/panel/equipo/usuarios/{admin}/password",
        data={"csrf": _csrf(cliente, pantalla), "password": "frase nueva larga"},
        follow_redirects=True,
    )
    assert "surte efecto cuando sincronice" in r.text

    await cliente.get("/panel/salir")
    nueva = await cliente.post(
        "/panel/entrar",
        data={"codigo": "ADMIN01", "password": "frase nueva larga"},
        follow_redirects=False,
    )
    assert nueva.status_code == 303

    # Y la vieja ya no.
    await cliente.get("/panel/salir")
    vieja = await cliente.post(
        "/panel/entrar",
        data={"codigo": "ADMIN01", "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert vieja.status_code == 200
    assert "incorrectos" in vieja.text


async def test_desactivar_revoca_las_sesiones_del_panel(cliente, semilla, sesion):
    """Es la razón de que la sesión tenga fila en la base y no sea un JWT: cuando
    alguien deja la empresa, su acceso se corta de inmediato."""
    await _entrar(cliente)
    await _crear_usuario(cliente, codigo="GER02", rol_codigo="gerente", con_camion="")

    otro = (
        await sesion.execute(text("SELECT id FROM usuarios WHERE codigo = 'GER02'"))
    ).scalar_one()

    # Ese usuario entra y queda con sesión viva.
    await cliente.get("/panel/salir")
    await _entrar(cliente, "GER02", PASSWORD_NUEVA)
    assert (await cliente.get("/panel", follow_redirects=False)).status_code == 200

    # El admin lo desactiva.
    await cliente.get("/panel/salir")
    await _entrar(cliente, "ADMIN01")
    pantalla = await cliente.get("/panel/equipo")
    r = await cliente.post(
        f"/panel/equipo/usuarios/{otro}/activo",
        data={"csrf": _csrf(cliente, pantalla)},
        follow_redirects=True,
    )
    assert "sesiones del panel revocadas" in r.text

    revocadas = (
        await sesion.execute(
            text(
                "SELECT count(*) FROM sesiones "
                " WHERE usuario_id = :u AND revocada_en IS NOT NULL"
            ),
            {"u": otro},
        )
    ).scalar_one()
    assert revocadas == 1


async def test_no_se_puede_desactivar_a_uno_mismo(cliente, semilla):
    """Quedarse fuera del panel exigiría entrar al servidor para arreglarlo."""
    await _entrar(cliente)
    pantalla = await cliente.get("/panel/equipo")
    r = await cliente.post(
        f"/panel/equipo/usuarios/{semilla['admin']}/activo",
        data={"csrf": _csrf(cliente, pantalla)},
        follow_redirects=True,
    )
    assert "a ti mismo" in r.text


async def test_no_se_puede_desactivar_al_ultimo_administrador(cliente, semilla, sesion):
    """Sin administrador activo nadie puede dar de alta usuarios ni reactivar a
    nadie, y el arreglo sale por línea de comandos en el servidor."""
    await _entrar(cliente)
    # Un segundo admin para poder intentar desactivar al primero.
    await _crear_usuario(cliente, codigo="ADMIN02", rol_codigo="admin", con_camion="")
    segundo = (
        await sesion.execute(text("SELECT id FROM usuarios WHERE codigo = 'ADMIN02'"))
    ).scalar_one()

    pantalla = await cliente.get("/panel/equipo")
    # Desactivar al segundo se puede: queda el primero.
    ok = await cliente.post(
        f"/panel/equipo/usuarios/{segundo}/activo",
        data={"csrf": _csrf(cliente, pantalla)},
        follow_redirects=True,
    )
    assert "desactivado" in ok.text

    # Ahora el primero es el último, y es él mismo: lo frena la otra regla.
    r = await cliente.post(
        f"/panel/equipo/usuarios/{semilla['admin']}/activo",
        data={"csrf": _csrf(cliente, pantalla)},
        follow_redirects=True,
    )
    assert "a ti mismo" in r.text


# ---------------------------------------------------------------------------
# Rutas: la atadura que se olvida
# ---------------------------------------------------------------------------


async def test_crear_una_ruta_CON_TITULAR_escribe_las_dos_filas(
    cliente, semilla, sesion
):
    """`rutas.vendedor_id` dice quién es el titular; `usuarios_rutas` es lo que el
    *scope guard* consulta.

    Con solo la primera, el vendedor aparece como dueño de la ruta y su teléfono no
    recibe un solo cliente: el filtro del pull es `ruta_id = ANY(:rutas)` y esa
    lista sale de `usuarios_rutas`. Es el error que cuesta una tarde de depuración
    con el teléfono en la mano.
    """
    await _entrar(cliente)
    await _crear_usuario(cliente)
    vendedor = (
        await sesion.execute(text("SELECT id FROM usuarios WHERE codigo = 'VEND02'"))
    ).scalar_one()

    pantalla = await cliente.get("/panel/equipo")
    r = await cliente.post(
        "/panel/equipo/rutas",
        data={
            "csrf": _csrf(cliente, pantalla),
            "codigo": "R09",
            "nombre": "Centro poniente",
            "vendedor_id": str(vendedor),
        },
        follow_redirects=True,
    )
    assert "ya la tiene en su alcance" in r.text

    fila = (
        await sesion.execute(
            text(
                "SELECT r.vendedor_id, "
                "       (SELECT count(*) FROM usuarios_rutas ur "
                "         WHERE ur.ruta_id = r.id AND ur.usuario_id = r.vendedor_id) AS alcance "
                "  FROM rutas r WHERE r.codigo = 'R09'"
            )
        )
    ).mappings().one()
    assert fila["vendedor_id"] == vendedor
    assert fila["alcance"] == 1


async def test_una_ruta_sin_titular_lo_dice(cliente, semilla):
    await _entrar(cliente)
    pantalla = await cliente.get("/panel/equipo")
    r = await cliente.post(
        "/panel/equipo/rutas",
        data={
            "csrf": _csrf(cliente, pantalla),
            "codigo": "R10",
            "nombre": "Ruta sin dueño",
            "vendedor_id": "",
        },
        follow_redirects=True,
    )
    assert "ningún teléfono va a recibir sus clientes" in r.text


async def test_cambiar_el_titular_le_quita_el_alcance_al_anterior(
    cliente, semilla, sesion
):
    """Si se le dejara, su teléfono seguiría recibiendo —y pudiendo venderle a— los
    clientes de una ruta que ya no trabaja."""
    await _entrar(cliente)
    await _crear_usuario(cliente)
    nuevo = (
        await sesion.execute(text("SELECT id FROM usuarios WHERE codigo = 'VEND02'"))
    ).scalar_one()

    pantalla = await cliente.get("/panel/equipo")
    await cliente.post(
        f"/panel/equipo/rutas/{semilla['ruta']}/titular",
        data={"csrf": _csrf(cliente, pantalla), "vendedor_id": str(nuevo)},
        follow_redirects=True,
    )

    alcances = dict(
        (
            await sesion.execute(
                text(
                    "SELECT usuario_id, count(*) FROM usuarios_rutas "
                    " WHERE ruta_id = :r GROUP BY usuario_id"
                ),
                {"r": semilla["ruta"]},
            )
        ).all()
    )
    assert nuevo in alcances
    assert semilla["vendedor"] not in alcances

    # Los clientes NO se mueven: siguen siendo de la ruta.
    ruta_del_cliente = (
        await sesion.execute(
            text("SELECT vendedor_id FROM rutas WHERE id = :r"), {"r": semilla["ruta"]}
        )
    ).scalar_one()
    assert ruta_del_cliente == nuevo


# ---------------------------------------------------------------------------
# Almacenes
# ---------------------------------------------------------------------------


async def test_un_camion_sin_responsable_se_niega_con_su_razon(cliente, semilla):
    """El dueño exclusivo del almacén es la garantía de que el trabajo offline no
    tenga conflictos que resolver (§0.2)."""
    await _entrar(cliente)
    pantalla = await cliente.get("/panel/equipo")
    r = await cliente.post(
        "/panel/equipo/almacenes",
        data={
            "csrf": _csrf(cliente, pantalla),
            "codigo": "CAMION_09",
            "nombre": "Camión suelto",
            "tipo": "camion",
            "responsable_id": "",
        },
        follow_redirects=True,
    )
    assert "necesita responsable" in r.text


async def test_crear_un_camion_lo_ata_como_almacen_del_usuario(cliente, semilla, sesion):
    await _entrar(cliente)
    await _crear_usuario(cliente, codigo="VEND04", con_camion="")
    vendedor = (
        await sesion.execute(text("SELECT id FROM usuarios WHERE codigo = 'VEND04'"))
    ).scalar_one()

    pantalla = await cliente.get("/panel/equipo")
    await cliente.post(
        "/panel/equipo/almacenes",
        data={
            "csrf": _csrf(cliente, pantalla),
            "codigo": "CAMION_04",
            "nombre": "Camión 04",
            "tipo": "camion",
            "responsable_id": str(vendedor),
        },
        follow_redirects=True,
    )

    fila = (
        await sesion.execute(
            text(
                "SELECT a.id AS almacen, u.almacen_id FROM almacenes a "
                "  JOIN usuarios u ON u.id = a.responsable_id "
                " WHERE a.codigo = 'CAMION_04'"
            )
        )
    ).mappings().one()
    assert fila["almacen_id"] == fila["almacen"]


# ---------------------------------------------------------------------------
# La pantalla delata los huecos
# ---------------------------------------------------------------------------


async def test_la_pantalla_delata_al_vendedor_sin_camion_y_sin_ruta(
    cliente, semilla, sesion
):
    """Un vendedor incompleto se ve igual que uno completo en una lista de
    usuarios. Aquí se marca, porque es la diferencia entre poder trabajar y no."""
    await _entrar(cliente)
    await _crear_usuario(cliente, codigo="VEND05", con_camion="")

    r = await cliente.get("/panel/equipo")
    assert "Sin camión:" in r.text
    assert "VEND05" in r.text
    assert "No se les puede cargar mercancía" in r.text
    assert "Sin ruta:" in r.text


# ---------------------------------------------------------------------------
# Listas de precios
# ---------------------------------------------------------------------------


@pytest.fixture
async def sin_listas_extra(sesion):
    """Deja solo la lista por omisión, antes y después.

    `listas_precios` es dato de REFERENCIA: el TRUNCATE entre pruebas no la toca,
    porque el código depende de que exista la lista GENERAL. Eso significa que una
    lista creada por una prueba **sobrevive** a las demás y a la siguiente corrida.

    Ya costó un CI rojo: el fixture de deltas se regeneró con dos listas porque
    otra prueba había dejado la suya. Y sin esto, estas dos pruebas pasan la
    primera vez y fallan la segunda con «la lista MAYOREO ya existe», que es el
    peor modo de fallar: el que depende de qué corrió antes.

    Se limpia también el `change_log`: el propio DELETE dispara el trigger y
    dejaría un delta de baja de una lista que ningún dispositivo tuvo.
    """
    async def limpiar():
        await sesion.execute(text("DELETE FROM listas_precios WHERE NOT es_default"))
        await sesion.execute(text("DELETE FROM change_log WHERE entidad = 'lista_precios'"))
        await sesion.commit()

    await limpiar()
    yield
    await limpiar()


async def test_una_lista_nueva_nace_vacia_y_lo_dice(cliente, semilla, sesion, sin_listas_extra):  # noqa: ARG001
    """Un cliente asignado a una lista sin precios no puede comprar nada."""
    await _entrar(cliente)
    pantalla = await cliente.get("/panel/equipo")
    r = await cliente.post(
        "/panel/equipo/listas",
        data={"csrf": _csrf(cliente, pantalla), "codigo": "MAYOREO", "nombre": "Mayoreo"},
        follow_redirects=True,
    )
    assert "no puede comprar nada" in r.text

    fila = (
        await sesion.execute(
            text("SELECT es_default, activo FROM listas_precios WHERE codigo = 'MAYOREO'")
        )
    ).mappings().one()
    # NO se vuelve la de omisión: eso movería el precio de todos los clientes de
    # calle de golpe, sin que nadie lo pida.
    assert fila["es_default"] is False
    assert fila["activo"] is True


async def test_sigue_habiendo_exactamente_una_lista_por_omision(
    cliente, semilla, sesion, sin_listas_extra  # noqa: ARG001
):
    await _entrar(cliente)
    pantalla = await cliente.get("/panel/equipo")
    await cliente.post(
        "/panel/equipo/listas",
        data={"csrf": _csrf(cliente, pantalla), "codigo": "CADENA", "nombre": "Cadena"},
        follow_redirects=True,
    )
    cuantas = (
        await sesion.execute(
            text("SELECT count(*) FROM listas_precios WHERE es_default")
        )
    ).scalar_one()
    assert cuantas == 1


# ---------------------------------------------------------------------------
# Acceso
# ---------------------------------------------------------------------------


async def test_un_gerente_no_da_de_alta_usuarios(cliente, semilla, sesion):
    """`usuarios.administrar` es solo de admin. Gerencia monitorea."""
    await _entrar(cliente)
    await _crear_usuario(cliente, codigo="GER03", rol_codigo="gerente", con_camion="")
    await cliente.get("/panel/salir")

    await _entrar(cliente, "GER03", PASSWORD_NUEVA)
    lectura = await cliente.get("/panel/equipo")
    assert lectura.status_code == 200
    assert "Nuevo usuario" not in lectura.text

    r = await cliente.post(
        "/panel/equipo/usuarios",
        data={
            "csrf": _csrf(cliente),
            "codigo": "HACKER",
            "nombre": "Yo mismo",
            "rol_codigo": "admin",
            "password": "una frase larga",
        },
    )
    assert r.status_code == 403
    assert "usuarios.administrar" in r.text


async def test_crear_un_usuario_exige_el_token_csrf(cliente, semilla):
    await _entrar(cliente)
    r = await cliente.post(
        "/panel/equipo/usuarios",
        data={
            "csrf": "inventado",
            "codigo": "VEND09",
            "nombre": "X",
            "rol_codigo": "vendedor",
            "password": "una frase larga",
        },
    )
    assert r.status_code == 403


async def test_un_vendedor_no_entra_al_panel_aunque_exista(cliente, semilla, sesion):
    """Se crea desde el panel, pero entra por la app del teléfono."""
    await _entrar(cliente)
    await _crear_usuario(cliente)
    await cliente.get("/panel/salir")

    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": "VEND02", "password": PASSWORD_NUEVA},
    )
    assert "app del teléfono" in r.text


# ---------------------------------------------------------------------------
# El comando de arranque
# ---------------------------------------------------------------------------


async def test_el_comando_de_arranque_crea_el_primer_usuario(sesion, esquema, monkeypatch):  # noqa: ARG001
    """`make usuario` tiene que funcionar sobre una base recién migrada.

    Es el único camino de entrada cuando no hay usuarios, así que si se rompe el
    sistema queda inaccesible y el arreglo sale por `psql`.
    """
    from app import cli

    respuestas = iter(["ADMIN-CLI", "Bryan Medina", "admin"])
    monkeypatch.setattr("builtins.input", lambda _: next(respuestas))
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "una frase bien larga")

    assert await cli.crear_usuario_de_oficina() == 0

    fila = (
        await sesion.execute(
            text(
                "SELECT codigo, rol_codigo, sucursal_id, password_hash "
                "  FROM usuarios WHERE codigo = 'ADMIN-CLI'"
            )
        )
    ).mappings().one()
    assert fila["rol_codigo"] == "admin"
    # Con sucursal: un usuario sin ella queda fuera de cualquier reporte que
    # agrupe por sucursal, y eso se descubre con los números ya mal.
    assert fila["sucursal_id"] is not None

    from app.core.seguridad import verificar_password

    assert verificar_password("una frase bien larga", fila["password_hash"])


async def test_el_comando_rechaza_una_contrasena_corta(sesion, esquema, monkeypatch):  # noqa: ARG001
    from app import cli

    respuestas = iter(["ADMIN-CORTA", "Alguien", "admin"])
    monkeypatch.setattr("builtins.input", lambda _: next(respuestas))
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "corta")

    assert await cli.crear_usuario_de_oficina() == 1
    cuantos = (
        await sesion.execute(
            text("SELECT count(*) FROM usuarios WHERE codigo = 'ADMIN-CORTA'")
        )
    ).scalar_one()
    assert cuantos == 0


async def test_el_comando_no_crea_vendedores(sesion, esquema, monkeypatch):  # noqa: ARG001
    """Un vendedor no entra al panel, así que crearlo con este comando produciría
    un usuario que no puede usar lo único que el comando desbloquea."""
    from app import cli

    respuestas = iter(["VEND-CLI", "Alguien", "vendedor"])
    monkeypatch.setattr("builtins.input", lambda _: next(respuestas))

    assert await cli.crear_usuario_de_oficina() == 1
    cuantos = (
        await sesion.execute(
            text("SELECT count(*) FROM usuarios WHERE codigo = 'VEND-CLI'")
        )
    ).scalar_one()
    assert cuantos == 0
