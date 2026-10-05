"""El panel de operación.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
Un panel de oficina parece inofensivo, y es donde se capturan los precios y se
resuelve la cuarentena: quien entre aquí puede cambiar lo que cobran cinco
vendedores. La mitad de estas pruebas son de acceso y de CSRF.

La otra mitad comprueba que las pantallas operativas **muestren** lo que hay que
atender. Una bandera `requiere_revision` que nadie ve es peor que no tenerla:
convierte el principio §0.1 en una excusa para no validar.
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import PASSWORD_VENDEDOR

pytestmark = pytest.mark.asyncio


async def _entrar(cliente, codigo: str = "ADMIN01") -> None:
    """Entra al panel. La cookie queda en el cliente HTTP."""
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": codigo, "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


# ---------------------------------------------------------------------------
# Acceso
# ---------------------------------------------------------------------------


async def test_sin_sesion_el_panel_manda_al_login(cliente, semilla):
    """Un 401 con JSON es correcto para la API y desconcertante en un navegador."""
    r = await cliente.get("/panel", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/panel/entrar")


async def test_conserva_a_donde_iba(cliente, semilla):
    # Quien abre un enlace directo a la cuarentena debe volver ahí después de
    # entrar, no al tablero.
    r = await cliente.get("/panel/cuarentena", follow_redirects=False)
    assert "volver=/panel/cuarentena" in r.headers["location"]


async def test_una_contrasena_mala_no_dice_cual_de_las_dos_falla(cliente, semilla):
    """Distinguir "no existe" de "contraseña incorrecta" confirmaría qué códigos
    de empleado son válidos, y el código es lo primero que alguien adivina."""
    mala = await cliente.post(
        "/panel/entrar", data={"codigo": "ADMIN01", "password": "equivocada"}
    )
    inexistente = await cliente.post(
        "/panel/entrar", data={"codigo": "NO-EXISTE", "password": "equivocada"}
    )

    assert "incorrectos" in mala.text
    assert "incorrectos" in inexistente.text
    # Exactamente el mismo mensaje.
    assert mala.text == inexistente.text


async def test_el_vendedor_no_entra_al_panel(cliente, semilla):
    """Tiene su app. Dejarlo entrar le daría pantallas de captura de oficina."""
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": "VEND01", "password": PASSWORD_VENDEDOR},
    )
    assert "app del teléfono" in r.text
    assert "dsd_panel" not in r.headers.get("set-cookie", "")


async def test_la_cookie_trae_sus_tres_defensas(cliente, semilla):
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": "ADMIN01", "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    cookie = r.headers["set-cookie"]
    # HttpOnly: un XSS puede usarla pero no llevársela.
    assert "HttpOnly" in cookie
    # SameSite: un POST desde otro sitio no la manda.
    assert "SameSite=lax" in cookie.lower().replace("samesite=lax", "SameSite=lax")
    # Acotada al panel: no viaja en las peticiones de la API del dispositivo.
    assert "Path=/panel" in cookie
    # Secure: solo por TLS. El panel vive detrás de Caddy y del túnel.
    assert "Secure" in cookie


async def test_salir_revoca_la_sesion_en_la_base(cliente, semilla, sesion):
    """Borrar solo la cookie dejaría la sesión viva para quien tenga el valor."""
    await _entrar(cliente)
    assert (await cliente.get("/panel", follow_redirects=False)).status_code == 200

    await cliente.get("/panel/salir", follow_redirects=False)

    revocadas = (
        await sesion.execute(
            text(
                "SELECT count(*) FROM sesiones "
                " WHERE dispositivo_id IS NULL AND revocada_en IS NOT NULL"
            )
        )
    ).scalar_one()
    assert revocadas == 1
    # Y la cookie ya no sirve.
    assert (await cliente.get("/panel", follow_redirects=False)).status_code == 303


async def test_una_sesion_revocada_muere_en_la_siguiente_peticion(
    cliente, semilla, sesion
):
    """Es la razón de usar sesión con fila y no un JWT: cuando alguien deja la
    empresa, su acceso se corta de inmediato."""
    await _entrar(cliente)
    await sesion.execute(text("UPDATE sesiones SET revocada_en = now()"))
    await sesion.commit()

    assert (await cliente.get("/panel", follow_redirects=False)).status_code == 303


async def test_una_sesion_vencida_no_sirve(cliente, semilla, sesion):
    await _entrar(cliente)
    await sesion.execute(text("UPDATE sesiones SET expira_en = now() - interval '1 hour'"))
    await sesion.commit()

    assert (await cliente.get("/panel", follow_redirects=False)).status_code == 303


# ---------------------------------------------------------------------------
# Tablero
# ---------------------------------------------------------------------------


async def test_el_tablero_cuenta_lo_que_necesita_atencion(
    cliente, semilla, sesion
):
    await _entrar(cliente)

    # Un producto sin precio: al vendedor no le aparece, y eso es un problema
    # operativo que el tablero tiene que delatar.
    await sesion.execute(
        text(
            "INSERT INTO productos(id, sku, nombre, unidad_base) "
            "VALUES (:id, 'SIN-PRECIO', 'Producto sin precio', 'PZA')"
        ),
        {"id": uuid.uuid4()},
    )
    await sesion.commit()

    r = await cliente.get("/panel")
    assert r.status_code == 200
    assert "sin precio en la lista general" in r.text
    assert "Tablero de operación" in r.text


async def test_el_efectivo_del_tablero_incluye_las_ventas_de_contado(
    cliente, semilla, sesion
):
    """El tablero decía «$0.00 efectivo a entregar hoy» con ventas de contado ya
    sincronizadas en el mismo tablero.

    Contaba solo los cobros, y una venta de contado es el caso más común de una
    ruta: dinero que el vendedor trae en la bolsa y tiene que entregar.

    Esta cuenta tiene que dar lo mismo que `_efectivo_esperado` de
    `liquidaciones.py`, que es la que decide el arqueo. Si no coinciden, el
    tablero promete un número y la liquidación cobra otro.
    """
    await _entrar(cliente)

    # El semillero no trae cliente ni equipo: los mínimos para que una venta exista.
    equipo, comprador = uuid.uuid4(), uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta) "
            "VALUES (:id, :u, 'Equipo de prueba')"
        ),
        {"id": equipo, "u": semilla["vendedor"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, nombre_comercial, ruta_id, origen_alta) "
            "VALUES (:id, 'Tienda del tablero', :r, 'oficina')"
        ),
        {"id": comprador, "r": semilla["ruta"]},
    )
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, almacen_id, tipo, estado,
                                subtotal, total, fecha_dispositivo, fecha_operativa)
            VALUES (:id, :disp, 9001, 'TEST-9001', :cli, :vend, :alm,
                    'contado', 'confirmada', 137.50, 137.50, now(), CURRENT_DATE)
            """
        ),
        {
            "id": uuid.uuid4(),
            "disp": equipo,
            "cli": comprador,
            "vend": semilla["vendedor"],
            "alm": semilla["camion"],
        },
    )
    await sesion.commit()

    r = await cliente.get("/panel")
    assert r.status_code == 200

    # Se lee EL RECUADRO, no la página: el mismo importe aparece en «vendido hoy»,
    # así que un `assert "137.50" in r.text` pasa con el efectivo en cero. Lo
    # comprobé quitando el arreglo: la prueba seguía verde. Es la cuarta vez en
    # este repositorio que una afirmación lee de más y por eso no vale nada.
    recuadro = re.search(
        r'<div class="numero">([^<]+)</div>\s*<div class="etiqueta">efectivo a entregar hoy</div>',
        r.text,
    )
    assert recuadro, "no se encontró el recuadro del efectivo en el tablero"
    assert "137.50" in recuadro.group(1), (
        "la venta de contado no está en el efectivo a entregar: el vendedor trae "
        f"ese dinero en la bolsa y el tablero muestra {recuadro.group(1).strip()}"
    )


async def test_el_tablero_marca_los_equipos_que_no_sincronizan(
    cliente, semilla, sesion
):
    """Un equipo que no sincroniza acumula ventas que nadie ve. Es el indicador
    que avisa antes de que el vendedor llegue a la oficina con 60 tickets."""
    await _entrar(cliente)
    await sesion.execute(
        text(
            "INSERT INTO dispositivos(id, usuario_id, etiqueta, ultima_sync_push_en) "
            "VALUES (:id, :u, 'POCO sin sincronizar', now() - interval '3 days')"
        ),
        {"id": uuid.uuid4(), "u": semilla["vendedor"]},
    )
    await sesion.commit()

    r = await cliente.get("/panel")
    assert "sin sincronizar hoy" in r.text
    assert "acumula ventas que nadie ve" in r.text


# ---------------------------------------------------------------------------
# Cuarentena
# ---------------------------------------------------------------------------


@pytest.fixture
async def en_cuarentena(sesion: AsyncSession, semilla: dict) -> dict:
    dispositivo = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos(id, usuario_id, etiqueta) "
            "VALUES (:d, :u, 'POCO M5s de Juan')"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    fila = (
        await sesion.execute(
            text(
                """
                INSERT INTO sync_cuarentena
                  (operacion_id, dispositivo_id, usuario_id, tipo, payload,
                   hash_payload, error_codigo, error_mensaje)
                VALUES (:op, :d, :u, 'venta.crear',
                        '{"cliente_id": "abc", "total": "592.00"}'::jsonb,
                        'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2',
                        'payload_invalido',
                        'el total 500.00 no cuadra con 592.00 - 0 + 0')
                RETURNING id
                """
            ),
            {"op": uuid.uuid4(), "d": dispositivo, "u": semilla["vendedor"]},
        )
    ).scalar_one()
    await sesion.commit()
    return {"id": fila, "dispositivo": dispositivo}


async def test_la_cuarentena_lista_lo_rechazado(cliente, semilla, en_cuarentena):
    """Hasta ahora esto quedaba en una tabla que nadie miraba."""
    await _entrar(cliente)
    r = await cliente.get("/panel/cuarentena")

    assert r.status_code == 200
    assert "payload_invalido" in r.text
    assert "POCO M5s de Juan" in r.text
    assert "no cuadra" in r.text
    # Y explica por qué urge.
    assert "ya entregó" in r.text


async def test_el_detalle_muestra_el_payload_INTEGRO(cliente, semilla, en_cuarentena):
    """Para decidir qué hacer con una operación rechazada hay que ver exactamente
    qué mandó el equipo, no un resumen."""
    await _entrar(cliente)
    r = await cliente.get(f"/panel/cuarentena/{en_cuarentena['id']}")

    assert r.status_code == 200
    assert "592.00" in r.text
    assert "cliente_id" in r.text


async def test_descartar_exige_el_token_csrf(cliente, semilla, en_cuarentena):
    """SameSite=Lax ya frena el POST cruzado; el token cierra el caso del
    navegador que lo ignora o del subdominio comprometido."""
    await _entrar(cliente)
    r = await cliente.post(
        f"/panel/cuarentena/{en_cuarentena['id']}/descartar",
        data={"nota": "capturada a mano", "csrf": "inventado"},
        follow_redirects=False,
    )
    assert r.status_code == 403


async def test_descartar_exige_una_nota(cliente, semilla, en_cuarentena, sesion):
    """"Descartada" sin explicación es peor que pendiente: dentro de un mes nadie
    sabrá si se corrigió a mano o se perdió."""
    await _entrar(cliente)
    csrf = _csrf_de(await cliente.get(f"/panel/cuarentena/{en_cuarentena['id']}"))

    r = await cliente.post(
        f"/panel/cuarentena/{en_cuarentena['id']}/descartar",
        data={"nota": "   ", "csrf": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "error=nota" in r.headers["location"]

    estado = (
        await sesion.execute(
            text("SELECT estado FROM sync_cuarentena WHERE id = :id"),
            {"id": en_cuarentena["id"]},
        )
    ).scalar_one()
    assert estado == "pendiente"


async def test_descartar_deja_rastro_y_NO_borra_el_payload(
    cliente, semilla, en_cuarentena, sesion
):
    """El payload es la única evidencia de una venta que existió en la calle y
    nunca entró. No se borra nunca."""
    await _entrar(cliente)
    csrf = _csrf_de(await cliente.get(f"/panel/cuarentena/{en_cuarentena['id']}"))

    r = await cliente.post(
        f"/panel/cuarentena/{en_cuarentena['id']}/descartar",
        data={"nota": "capturada a mano con folio 1234", "csrf": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303

    fila = (
        await sesion.execute(
            text(
                "SELECT estado, nota_resolucion, resuelto_por, resuelto_en, payload "
                "  FROM sync_cuarentena WHERE id = :id"
            ),
            {"id": en_cuarentena["id"]},
        )
    ).mappings().one()

    assert fila["estado"] == "descartada"
    assert fila["nota_resolucion"] == "capturada a mano con folio 1234"
    assert fila["resuelto_por"] == semilla["admin"]
    assert fila["resuelto_en"] is not None
    # Intacto.
    assert fila["payload"]["total"] == "592.00"


def _csrf_de(respuesta) -> str:
    """Saca el token del formulario, como haría un navegador."""
    marca = 'name="csrf" value="'
    inicio = respuesta.text.index(marca) + len(marca)
    return respuesta.text[inicio : respuesta.text.index('"', inicio)]


# ---------------------------------------------------------------------------
# Ventas en revisión
# ---------------------------------------------------------------------------


async def test_las_ventas_marcadas_se_ven_con_su_motivo_EN_ESPANOL(
    cliente, semilla, sesion
):
    """El código del motivo es para el sistema. Quien tiene que decidir qué hacer
    necesita leer qué pasó."""
    await _entrar(cliente)

    dispositivo, venta_cliente = uuid.uuid4(), uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos(id, usuario_id, etiqueta) "
            "VALUES (:d, :u, 'POCO')"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO clientes(id, codigo, nombre_comercial, ruta_id, creado_en, "
            "actualizado_en) VALUES (:c, 'CLI-9', 'La Esquina', :r, now(), now())"
        ),
        {"c": venta_cliente, "r": semilla["ruta"]},
    )
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, almacen_id, tipo,
                                subtotal, total,
                                lista_precios_id, lista_precios_version,
                                fecha_dispositivo, fecha_operativa,
                                requiere_revision, revision_motivos,
                                distancia_cliente_m, desfase_reloj_seg)
            VALUES (:v, :d, 77, 'VEND01-000077', :c, :u, :a, 'credito',
                    592.00, 592.00,
                    :l, 7, now(), CURRENT_DATE, true,
                    ARRAY['excede_limite_credito','fuera_de_geocerca'],
                    1850.5, 7200)
            """
        ),
        {
            "v": uuid.uuid4(),
            "d": dispositivo,
            "c": venta_cliente,
            "u": semilla["vendedor"],
            "a": semilla["camion"],
            "l": semilla["lista_precios"],
        },
    )
    await sesion.commit()

    r = await cliente.get("/panel/ventas")
    assert r.status_code == 200
    assert "VEND01-000077" in r.text
    # En español, no el código.
    assert "Pasó su límite de crédito" in r.text
    assert "Lejos del domicilio registrado" in r.text
    # Y con el número que permite juzgar.
    assert "1850" in r.text or "1851" in r.text
    assert "Reloj desfasado" in r.text


async def test_sin_ventas_marcadas_lo_dice_en_vez_de_una_tabla_vacia(
    cliente, semilla
):
    await _entrar(cliente)
    r = await cliente.get("/panel/ventas")
    assert "Ninguna venta marcada" in r.text
