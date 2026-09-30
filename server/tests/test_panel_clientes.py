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
from datetime import UTC, datetime, timedelta
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
# Crédito
# ---------------------------------------------------------------------------


@pytest.fixture
async def con_adeudo(sesion, semilla, prospecto) -> uuid.UUID:
    """El cliente confirmado, con crédito y una venta a crédito de $2,000 abierta.

    La cuenta por cobrar tiene como llave primaria la venta que la originó: no se
    puede deber sin haber comprado. Por eso el fixture arma la venta completa en
    vez de insertar un adeudo suelto.
    """
    dispositivo, venta = uuid.uuid4(), uuid.uuid4()
    await sesion.execute(
        text(
            "UPDATE clientes SET codigo = 'C00001', estatus = 'activo', "
            "       lista_precios_id = :l, permite_credito = true, "
            "       limite_credito = 5000, dias_credito = 7 WHERE id = :id"
        ),
        {"id": prospecto, "l": semilla["lista_precios"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO dispositivos(id, usuario_id, etiqueta) "
            "VALUES (:d, :u, 'POCO M5s de Juan')"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, almacen_id, tipo,
                                subtotal, total, lista_precios_id,
                                lista_precios_version, fecha_dispositivo,
                                fecha_operativa)
            VALUES (:v, :d, 1, 'VEND01-000001', :c, :u, :a, 'credito',
                    2000.00, 2000.00, :l, 1, now(), CURRENT_DATE)
            """
        ),
        {
            "v": venta,
            "d": dispositivo,
            "c": prospecto,
            "u": semilla["vendedor"],
            "a": semilla["camion"],
            "l": semilla["lista_precios"],
        },
    )
    await sesion.execute(
        text(
            """
            INSERT INTO cuentas_por_cobrar
              (venta_id, cliente_id, importe_original, fecha_emision,
               fecha_vencimiento, estado)
            VALUES (:v, :c, 2000.00, CURRENT_DATE, :vence, 'abierta')
            """
        ),
        {
            "v": venta,
            "c": prospecto,
            "vence": (datetime.now(UTC) + timedelta(days=7)).date(),
        },
    )
    await sesion.commit()
    return prospecto


async def test_bajar_el_limite_no_perdona_la_deuda(cliente, semilla, con_adeudo, sesion):
    """Quien lo escribe casi siempre cree que está perdonando el adeudo.

    Lo que pasa es otra cosa: el saldo sigue igual y el disponible se va a cero, así
    que el teléfono corta la venta a crédito solo. La pantalla lo dice con números.
    """
    await _entrar(cliente)
    detalle = await cliente.get(f"/panel/clientes/{con_adeudo}")

    r = await cliente.post(
        f"/panel/clientes/{con_adeudo}/credito",
        data={
            "csrf": _csrf_de(detalle),
            "lista_precios_id": str(semilla["lista_precios"]),
            "permite_credito": "1",
            "limite_credito": "1000",
            "dias_credito": "7",
        },
        follow_redirects=True,
    )
    assert "La deuda no cambió" in r.text
    assert "$2,000.00" in r.text

    cartera = (
        await sesion.execute(
            text(
                "SELECT saldo, disponible, credito_agotado "
                "  FROM v_cartera_cliente WHERE cliente_id = :id"
            ),
            {"id": con_adeudo},
        )
    ).mappings().one()

    assert cartera["saldo"] == Decimal("2000.00")
    # max(límite − saldo, 0): el disponible se va a cero, no a negativo.
    assert cartera["disponible"] == Decimal("0.00")
    assert cartera["credito_agotado"] is True


async def test_credito_autorizado_con_limite_en_cero_se_niega(
    cliente, semilla, prospecto
):
    """Es la contradicción que deja al vendedor peleando con el teléfono: la
    oficina cree que autorizó el crédito y la pantalla del carrito no lo deja."""
    await _entrar(cliente)
    detalle = await cliente.get(f"/panel/clientes/{prospecto}")

    r = await cliente.post(
        f"/panel/clientes/{prospecto}/credito",
        data={
            "csrf": _csrf_de(detalle),
            "lista_precios_id": str(semilla["lista_precios"]),
            "permite_credito": "1",
            "limite_credito": "0",
            "dias_credito": "0",
        },
        follow_redirects=True,
    )
    assert "Pon el límite o quita el crédito" in r.text


async def test_el_plazo_de_credito_tiene_tope(cliente, semilla, prospecto):
    """365 días de plazo en abarrotes es un dedazo, no una condición comercial."""
    await _entrar(cliente)
    detalle = await cliente.get(f"/panel/clientes/{prospecto}")

    r = await cliente.post(
        f"/panel/clientes/{prospecto}/credito",
        data={
            "csrf": _csrf_de(detalle),
            "lista_precios_id": str(semilla["lista_precios"]),
            "limite_credito": "1000",
            "dias_credito": "365",
        },
        follow_redirects=True,
    )
    assert "no puede pasar de 90" in r.text


async def test_el_saldo_no_se_puede_editar_desde_ninguna_pantalla(
    cliente, semilla, con_adeudo
):
    """Un campo editable de saldo sería una segunda verdad.

    El saldo sale de `cuentas_por_cobrar`. Si además se pudiera escribir a mano,
    tarde o temprano las dos cifras no coinciden y nadie sabe cuál cobrar.
    """
    await _entrar(cliente)
    html = (await cliente.get(f"/panel/clientes/{con_adeudo}")).text

    assert 'name="saldo"' not in html
    # Pero el número sí se ve: es lo primero que se pregunta de un cliente.
    assert "$2,000.00" in html


# ---------------------------------------------------------------------------
# Bloqueo
# ---------------------------------------------------------------------------


async def test_bloquear_exige_el_motivo(cliente, semilla, prospecto, sesion):
    """El vendedor va a preguntar por qué no le puede vender a esa tienda, y
    "bloqueado" sin más no le sirve a nadie."""
    await _entrar(cliente)
    detalle = await cliente.get(f"/panel/clientes/{prospecto}")

    r = await cliente.post(
        f"/panel/clientes/{prospecto}/bloqueo",
        data={"csrf": _csrf_de(detalle), "bloquear": "1", "motivo": "   "},
        follow_redirects=True,
    )
    assert "Escribe por qué se bloquea" in r.text

    bloqueado = (
        await sesion.execute(
            text("SELECT bloqueado FROM clientes WHERE id = :id"), {"id": prospecto}
        )
    ).scalar_one()
    assert bloqueado is False


async def test_el_motivo_del_bloqueo_viaja_al_telefono(
    cliente, semilla, prospecto, sesion
):
    await _entrar(cliente)
    detalle = await cliente.get(f"/panel/clientes/{prospecto}")

    await cliente.post(
        f"/panel/clientes/{prospecto}/bloqueo",
        data={
            "csrf": _csrf_de(detalle),
            "bloquear": "1",
            "motivo": "Cheque devuelto del 12/09",
        },
        follow_redirects=False,
    )

    payload = (
        await sesion.execute(
            text(
                "SELECT payload FROM change_log "
                " WHERE entidad = 'cliente' AND entidad_id = :id "
                " ORDER BY cursor DESC LIMIT 1"
            ),
            {"id": prospecto},
        )
    ).scalar_one()
    assert payload["bloqueado"] is True
    assert payload["bloqueo_motivo"] == "Cheque devuelto del 12/09"


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


async def test_el_gerente_no_decide_el_credito(cliente, semilla, prospecto, gerente):
    """Gerencia monitorea, no opera (migración 0009): no tiene
    `clientes.administrar`."""
    await _entrar(cliente, "GER01")
    lectura = await cliente.get(f"/panel/clientes/{prospecto}")
    assert lectura.status_code == 200
    assert "Guardar condiciones" not in lectura.text

    r = await cliente.post(
        f"/panel/clientes/{prospecto}/credito",
        data={
            "csrf": _csrf_calculado(cliente),
            "lista_precios_id": str(semilla["lista_precios"]),
            "permite_credito": "1",
            "limite_credito": "999999",
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
