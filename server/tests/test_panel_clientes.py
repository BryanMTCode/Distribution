"""La pantalla de clientes: confirmar prospectos y decidir el crédito.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
El alta de cliente en la calle quedó construida en la Fase 2 y era, en la
práctica, un formulario que no llevaba a ningún lado: el servidor guardaba el
prospecto sin código, sin lista de precios y sin crédito —correctamente, porque
esa decisión es de la oficina— y **nada podía terminar el trabajo**.

Estas pruebas cubren el circuito que faltaba, y sobre todo las tres cosas que si
se rompen se rompen callando:

1. Que confirmar asigne un código **sin repetirlo ni gastarlo dos veces**.
2. Que la lista de precios quede puesta: sin ella cada venta entra marcada.
3. Que bajar un límite de crédito **no borre lo que ya se debe**.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR

pytestmark = pytest.mark.asyncio


async def _entrar(cliente, codigo: str = "ADMIN01") -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": codigo, "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


def _csrf_de(respuesta) -> str:
    marca = 'name="csrf" value="'
    inicio = respuesta.text.index(marca) + len(marca)
    return respuesta.text[inicio : respuesta.text.index('"', inicio)]


def _csrf_calculado(cliente) -> str:
    """El token, derivado de la cookie como lo hace el servidor.

    Hace falta para probar a un usuario **sin permiso de escritura**: a ése la
    pantalla no le dibuja ni un formulario, así que no hay de dónde leer el token.
    Mandar uno inventado daría 403 por el motivo equivocado —el de CSRF— y la
    prueba pasaría sin comprobar nada del permiso.
    """
    import hashlib
    import hmac

    from app.core.config import obtener_config

    cookie = cliente.cookies.get("dsd_panel", "")
    return hmac.new(
        obtener_config().jwt_secreto.encode(),
        f"csrf:{cookie}".encode(),
        hashlib.sha256,
    ).hexdigest()


@pytest.fixture
async def prospecto(sesion, semilla) -> uuid.UUID:
    """Un alta de campo tal como la deja `manejadores.crear_cliente`.

    Sin código, sin lista de precios, sin crédito y con su georreferencia: es
    exactamente el estado en el que el teléfono la entrega.
    """
    cliente_id = uuid.uuid4()
    await sesion.execute(
        text(
            """
            INSERT INTO clientes (id, nombre_comercial, ruta_id, canal_codigo,
                                  telefono, calle, colonia,
                                  lat, lng, ubicacion_precision_m, ubicacion_origen,
                                  permite_credito, limite_credito, dias_credito,
                                  estatus, origen_alta, creado_por,
                                  creado_en, actualizado_en)
            VALUES (:id, 'La Esquina de Ñoño', :r, 'TIENDITA',
                    '5512345678', 'Morelos', 'Centro',
                    19.4326, -99.1332, 8.5, 'gps',
                    false, 0, 0, 'prospecto', 'campo', :u, now(), now())
            """
        ),
        {"id": cliente_id, "r": semilla["ruta"], "u": semilla["vendedor"]},
    )
    await sesion.commit()
    return cliente_id


# ---------------------------------------------------------------------------
# Confirmar
# ---------------------------------------------------------------------------


async def test_confirmar_cierra_el_circuito_del_alta_en_calle(
    cliente, semilla, prospecto, sesion
):
    """Código, estatus y lista de precios, en una transacción."""
    await _entrar(cliente)
    detalle = await cliente.get(f"/panel/clientes/{prospecto}")
    assert detalle.status_code == 200
    assert "Confirmar el alta" in detalle.text

    r = await cliente.post(
        f"/panel/clientes/{prospecto}/confirmar",
        data={
            "csrf": _csrf_de(detalle),
            "lista_precios_id": str(semilla["lista_precios"]),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    fila = (
        await sesion.execute(
            text(
                "SELECT codigo, estatus, lista_precios_id, requiere_revision, "
                "       permite_credito, limite_credito "
                "  FROM clientes WHERE id = :id"
            ),
            {"id": prospecto},
        )
    ).mappings().one()

    assert fila["codigo"].startswith("C")
    assert fila["estatus"] == "activo"
    assert fila["lista_precios_id"] == semilla["lista_precios"]
    assert fila["requiere_revision"] is False
    # El crédito NO se otorga al confirmar: son dos juicios distintos.
    assert fila["permite_credito"] is False
    assert fila["limite_credito"] == Decimal("0.00")


async def test_dos_confirmaciones_no_dan_el_mismo_codigo(cliente, semilla, sesion):
    """De ahí la secuencia y no `max(codigo) + 1`.

    Con `max()+1`, dos personas confirmando al mismo tiempo obtienen el mismo
    número y una de las dos ve un error de UNIQUE que no significa nada para ella.
    """
    await _entrar(cliente)
    ids = []
    for nombre in ("Tienda A", "Tienda B"):
        cliente_id = uuid.uuid4()
        await sesion.execute(
            text(
                "INSERT INTO clientes (id, nombre_comercial, ruta_id, estatus, "
                "                      origen_alta, creado_en, actualizado_en) "
                "VALUES (:id, :n, :r, 'prospecto', 'campo', now(), now())"
            ),
            {"id": cliente_id, "n": nombre, "r": semilla["ruta"]},
        )
        ids.append(cliente_id)
    await sesion.commit()

    codigos = []
    for cliente_id in ids:
        detalle = await cliente.get(f"/panel/clientes/{cliente_id}")
        await cliente.post(
            f"/panel/clientes/{cliente_id}/confirmar",
            data={
                "csrf": _csrf_de(detalle),
                "lista_precios_id": str(semilla["lista_precios"]),
            },
            follow_redirects=False,
        )
        codigos.append(
            (
                await sesion.execute(
                    text("SELECT codigo FROM clientes WHERE id = :id"), {"id": cliente_id}
                )
            ).scalar_one()
        )

    assert codigos[0] != codigos[1]


async def test_confirmar_dos_veces_no_le_cambia_el_codigo(
    cliente, semilla, prospecto, sesion
):
    """La ruta ya conoce al cliente por ese número.

    Corregir algo de una revisión no puede renumerarlo, ni gastar un consecutivo
    de más.
    """
    await _entrar(cliente)

    async def confirmar() -> str:
        detalle = await cliente.get(f"/panel/clientes/{prospecto}")
        await cliente.post(
            f"/panel/clientes/{prospecto}/confirmar",
            data={
                "csrf": _csrf_de(detalle),
                "lista_precios_id": str(semilla["lista_precios"]),
            },
            follow_redirects=False,
        )
        return (
            await sesion.execute(
                text("SELECT codigo FROM clientes WHERE id = :id"), {"id": prospecto}
            )
        ).scalar_one()

    primero = await confirmar()
    assert await confirmar() == primero


async def test_confirmar_sin_lista_de_precios_se_niega(cliente, semilla, prospecto, sesion):
    """Sin lista, cada venta a este cliente entra marcada `sin_lista_de_precios`.

    Es el motivo de revisión más fácil de evitar, y el que más ruido hace si se
    deja pasar: en cuanto la bandera aparece en ventas legítimas, se ignora.
    """
    await _entrar(cliente)
    detalle = await cliente.get(f"/panel/clientes/{prospecto}")

    r = await cliente.post(
        f"/panel/clientes/{prospecto}/confirmar",
        data={"csrf": _csrf_de(detalle), "lista_precios_id": ""},
        follow_redirects=True,
    )
    assert "sin ella cada venta entra marcada" in r.text

    estatus = (
        await sesion.execute(
            text("SELECT estatus FROM clientes WHERE id = :id"), {"id": prospecto}
        )
    ).scalar_one()
    assert estatus == "prospecto"


async def test_confirmar_publica_su_delta_con_la_ruta(
    cliente, semilla, prospecto, sesion
):
    """El delta del cliente va acotado por ruta: solo le importa a ese teléfono.

    Si llegara sin `ruta_id`, el catálogo de clientes de toda la empresa terminaría
    en cada equipo.
    """
    await _entrar(cliente)
    detalle = await cliente.get(f"/panel/clientes/{prospecto}")
    await cliente.post(
        f"/panel/clientes/{prospecto}/confirmar",
        data={
            "csrf": _csrf_de(detalle),
            "lista_precios_id": str(semilla["lista_precios"]),
        },
        follow_redirects=False,
    )

    fila = (
        await sesion.execute(
            text(
                "SELECT ruta_id, payload FROM change_log "
                " WHERE entidad = 'cliente' AND entidad_id = :id "
                " ORDER BY cursor DESC LIMIT 1"
            ),
            {"id": prospecto},
        )
    ).mappings().one()

    assert fila["ruta_id"] == semilla["ruta"]
    assert fila["payload"]["estatus"] == "activo"
    assert fila["payload"]["lista_precios_id"] == str(semilla["lista_precios"])


# ---------------------------------------------------------------------------
# Condiciones: solo la lista de precios. Todo es de contado (ADR 0002 §81).
# ---------------------------------------------------------------------------


async def test_la_lista_de_precios_se_guarda(cliente, semilla, prospecto, sesion):
    await _entrar(cliente)
    pagina = await cliente.get(f"/panel/clientes/{prospecto}")
    r = await cliente.post(
        f"/panel/clientes/{prospecto}/condiciones",
        data={"csrf": _csrf_de(pagina), "lista_precios_id": str(semilla["lista_precios"])},
        follow_redirects=True,
    )
    assert "Lista de precios guardada" in r.text
    lista = (
        await sesion.execute(
            text("SELECT lista_precios_id FROM clientes WHERE id = :c"), {"c": prospecto}
        )
    ).scalar_one()
    assert lista == semilla["lista_precios"]


async def test_el_credito_y_el_bloqueo_ya_no_estan(cliente, semilla, prospecto):
    await _entrar(cliente)
    pagina = await cliente.get(f"/panel/clientes/{prospecto}")
    assert pagina.status_code == 200
    for ya_no in ("Límite de crédito", "Bloquear el crédito", "Disponible para crédito"):
        assert ya_no not in pagina.text, ya_no
    for ruta in ("credito", "bloqueo"):
        r = await cliente.post(
            f"/panel/clientes/{prospecto}/{ruta}",
            data={"csrf": _csrf_calculado(cliente), "permite_credito": "1"},
        )
        assert r.status_code in (404, 405), ruta


# ---------------------------------------------------------------------------
# Datos y bajas
# ---------------------------------------------------------------------------


async def test_la_georreferencia_no_se_edita_desde_la_oficina(
    cliente, semilla, prospecto, sesion
):
    """La capturó el vendedor parado en la banqueta del negocio.

    Cambiarla desde una computadora a quince kilómetros sería sustituir un dato
    medido por uno supuesto, y encima rompería la distancia con la que se marcan
    las ventas fuera de geocerca.
    """
    await _entrar(cliente)
    html = (await cliente.get(f"/panel/clientes/{prospecto}")).text

    assert 'name="lat"' not in html
    assert 'name="lng"' not in html
    # Pero se muestra, con su origen y su precisión: es dato auditable.
    assert "19.4326" in html
    assert "gps" in html

    # Y aunque se mande a mano, el formulario de datos no la toca.
    detalle = await cliente.get(f"/panel/clientes/{prospecto}")
    await cliente.post(
        f"/panel/clientes/{prospecto}/datos",
        data={
            "csrf": _csrf_de(detalle),
            "nombre_comercial": "La Esquina de Ñoño",
            "lat": "0",
            "lng": "0",
            "estatus": "prospecto",
        },
        follow_redirects=False,
    )
    lat = (
        await sesion.execute(
            text("SELECT lat FROM clientes WHERE id = :id"), {"id": prospecto}
        )
    ).scalar_one()
    assert lat == Decimal("19.4326000")


async def test_la_baja_es_inactivo_y_no_un_delete(cliente, semilla, prospecto, sesion):
    """Tiene ventas, tiene cartera, y puede tener una venta de esta mañana que
    todavía no ha sincronizado. Borrarlo la mandaría a cuarentena por una llave
    foránea: sería violar §0.1 desde la oficina."""
    await _entrar(cliente)
    detalle = await cliente.get(f"/panel/clientes/{prospecto}")

    await cliente.post(
        f"/panel/clientes/{prospecto}/datos",
        data={
            "csrf": _csrf_de(detalle),
            "nombre_comercial": "La Esquina de Ñoño",
            "estatus": "inactivo",
        },
        follow_redirects=False,
    )

    fila = (
        await sesion.execute(
            text("SELECT estatus FROM clientes WHERE id = :id"), {"id": prospecto}
        )
    ).mappings().first()
    assert fila is not None
    assert fila["estatus"] == "inactivo"


# ---------------------------------------------------------------------------
# Lo que la lista tiene que delatar
# ---------------------------------------------------------------------------


async def test_la_lista_abre_en_lo_que_espera_decision(cliente, semilla, prospecto):
    """Abrir en "todos" esconde el trabajo pendiente entre trescientos clientes
    que ya están resueltos."""
    await _entrar(cliente)
    r = await cliente.get("/panel/clientes")

    assert r.status_code == 200
    assert "La Esquina de Ñoño" in r.text
    assert "Por confirmar" in r.text
    assert "sin código" in r.text
    assert "sin lista de precios" in r.text


# ---------------------------------------------------------------------------
# Acceso
# ---------------------------------------------------------------------------


@pytest.fixture
async def gerente(sesion, semilla) -> None:
    from app.core.seguridad import hashear_password

    await sesion.execute(
        text(
            "INSERT INTO usuarios(id, sucursal_id, codigo, nombre, password_hash, "
            "rol_codigo, creado_en, actualizado_en) "
            "VALUES (:id, :s, 'GER01', 'Gerente', :h, 'gerente', now(), now())"
        ),
        {
            "id": uuid.uuid4(),
            "s": semilla["sucursal"],
            "h": hashear_password(PASSWORD_VENDEDOR),
        },
    )
    await sesion.commit()


async def test_el_gerente_no_cambia_la_lista_de_precios(cliente, semilla, prospecto, gerente):
    """Gerencia no tiene `clientes.administrar` (migración 0009)."""
    await _entrar(cliente, "GER01")
    lectura = await cliente.get(f"/panel/clientes/{prospecto}")
    assert lectura.status_code == 200
    assert "Guardar la lista de precios" not in lectura.text

    r = await cliente.post(
        f"/panel/clientes/{prospecto}/condiciones",
        data={
            "csrf": _csrf_calculado(cliente),
            "lista_precios_id": str(semilla["lista_precios"]),
        },
    )
    assert r.status_code == 403
    assert "clientes.administrar" in r.text


async def test_confirmar_exige_el_token_csrf(cliente, semilla, prospecto):
    await _entrar(cliente)
    r = await cliente.post(
        f"/panel/clientes/{prospecto}/confirmar",
        data={"csrf": "inventado", "lista_precios_id": str(semilla["lista_precios"])},
    )
    assert r.status_code == 403
