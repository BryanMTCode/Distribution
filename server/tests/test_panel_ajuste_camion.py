"""La oficina ajusta el inventario de un camión.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
Esto es una excepción documentada a §0.2 —el almacén del camión tiene un único
dueño exclusivo— y la razón por la que se puede permitir es una sola: **el ajuste
se publica como delta y el teléfono converge**. Si eso dejara de funcionar, la
pantalla volvería a ser lo que la migración 0025 prohibió: cambiarle el inventario
bajo los pies a alguien que está vendiendo con otra cifra.

Por eso la prueba que más importa de este archivo es la del delta.

Lo demás es lo que la pantalla impide: que se ajuste una bodega por aquí, que un
ajuste capturado a mano empuje el camión a negativo, y que se guarde sin nota.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR, solo_texto

pytestmark = pytest.mark.asyncio

NOTA = "Juan reportó por teléfono que trae 12 cajas, no 30; se contó con él"


async def _entrar(cliente, codigo: str = "ADMIN01") -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": codigo, "password": PASSWORD_VENDEDOR},
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


@pytest.fixture
async def camion_con_sopa(sesion, semilla) -> dict:
    """El camión de Juan con 30 piezas de sopa, y la bodega con 100."""
    producto = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva) "
            "VALUES (:p, 'SOPA-70G', 'Sopa de fideo 70 g', 'PZA', 0)"
        ),
        {"p": producto},
    )
    await sesion.execute(
        text(
            "INSERT INTO existencias (almacen_id, producto_id, cantidad) "
            "VALUES (:c, :p, 30), (:b, :p, 100)"
        ),
        {"c": semilla["camion"], "b": semilla["bodega"], "p": producto},
    )
    await sesion.commit()
    return {"producto": producto}


async def _ajustar(cliente, semilla, camion_con_sopa, **campos):
    almacen = campos.pop("almacen", semilla["camion"])
    producto = campos.pop("producto", camion_con_sopa["producto"])
    pantalla = await cliente.get(f"/panel/inventario/{almacen}/{producto}")
    datos = {"csrf": _csrf(cliente, pantalla), "nota": NOTA, **campos}
    return await cliente.post(
        f"/panel/inventario/{almacen}/{producto}/ajustar",
        data=datos,
        follow_redirects=True,
    )


async def _saldo(sesion, almacen, producto) -> Decimal:
    return Decimal(
        (
            await sesion.execute(
                text(
                    "SELECT COALESCE((SELECT cantidad FROM existencias "
                    "  WHERE almacen_id = :a AND producto_id = :p), 0)"
                ),
                {"a": almacen, "p": producto},
            )
        ).scalar_one()
    )


# ---------------------------------------------------------------------------
# El conteo
# ---------------------------------------------------------------------------


async def test_UN_CONTEO_DEJA_EL_CAMION_EN_LO_CONTADO(
    cliente, semilla, camion_con_sopa, sesion
):
    """Se captura lo que se CONTÓ, no la diferencia: la resta a mano es de donde
    salen los errores que este ajuste viene a corregir (doctrina de la 0026)."""
    await _entrar(cliente)
    r = await _ajustar(cliente, semilla, camion_con_sopa, tipo="conteo", contado="12")

    assert "Ahora dice 12" in solo_texto(r)
    assert await _saldo(sesion, semilla["camion"], camion_con_sopa["producto"]) == Decimal("12.000")

    fila = (
        await sesion.execute(
            text(
                "SELECT folio, tipo, existencia_al_capturar, contado, delta, nota, "
                "       usuario_id FROM ajustes_camion"
            )
        )
    ).mappings().one()
    assert fila["folio"].startswith("AC-")
    assert fila["existencia_al_capturar"] == Decimal("30.000")
    assert fila["contado"] == Decimal("12.000")
    # La base impone la aritmética: un bug en Python no puede escribir un ajuste
    # que no cuadre (`CONSTRAINT conteo_cuadra`).
    assert fila["delta"] == Decimal("-18.000")
    assert fila["nota"] == NOTA
    assert fila["usuario_id"] == semilla["admin"]


async def test_el_asiento_queda_en_el_libro_mayor(
    cliente, semilla, camion_con_sopa, sesion
):
    await _entrar(cliente)
    await _ajustar(cliente, semilla, camion_con_sopa, tipo="conteo", contado="12")

    m = (
        await sesion.execute(
            text(
                "SELECT tipo, cantidad, almacen_origen_id, almacen_destino_id, "
                "       documento_tipo FROM movimientos_inventario"
            )
        )
    ).mappings().one()
    assert m["tipo"] == "ajuste"
    assert m["cantidad"] == Decimal("18.000")
    assert m["almacen_origen_id"] == semilla["camion"]
    assert m["almacen_destino_id"] is None
    assert m["documento_tipo"] == "ajuste_camion"


async def test_un_conteo_que_encuentra_MAS_sube_el_saldo(
    cliente, semilla, camion_con_sopa, sesion
):
    """Los dos sentidos son el mismo acto: «el número está mal, aquí está el
    correcto». Partirlo en dos pantallas obligaría a decidir el signo primero."""
    await _entrar(cliente)
    await _ajustar(cliente, semilla, camion_con_sopa, tipo="conteo", contado="45")

    assert await _saldo(sesion, semilla["camion"], camion_con_sopa["producto"]) == Decimal("45.000")
    m = (
        await sesion.execute(
            text(
                "SELECT almacen_origen_id, almacen_destino_id, cantidad "
                "  FROM movimientos_inventario"
            )
        )
    ).mappings().one()
    assert m["almacen_destino_id"] == semilla["camion"]
    assert m["almacen_origen_id"] is None
    assert m["cantidad"] == Decimal("15.000")


async def test_contar_lo_mismo_no_escribe_nada(
    cliente, semilla, camion_con_sopa, sesion
):
    """Un ajuste que no ajusta nada es un renglón que solo estorba al auditar."""
    await _entrar(cliente)
    r = await _ajustar(cliente, semilla, camion_con_sopa, tipo="conteo", contado="30")

    assert "no hay nada que ajustar" in solo_texto(r)
    assert (
        await sesion.execute(text("SELECT count(*) FROM ajustes_camion"))
    ).scalar_one() == 0


# ---------------------------------------------------------------------------
# Merma y entrada
# ---------------------------------------------------------------------------


async def test_una_merma_baja_el_saldo_y_exige_motivo(
    cliente, semilla, camion_con_sopa, sesion
):
    await _entrar(cliente)
    sin_motivo = await _ajustar(
        cliente, semilla, camion_con_sopa, tipo="merma", cantidad="6"
    )
    assert "necesita su motivo" in solo_texto(sin_motivo)

    await _ajustar(
        cliente,
        semilla,
        camion_con_sopa,
        tipo="merma",
        cantidad="6",
        motivo_codigo="DANADO_TRANSPORTE",
    )
    assert await _saldo(sesion, semilla["camion"], camion_con_sopa["producto"]) == Decimal("24.000")

    fila = (
        await sesion.execute(
            text("SELECT tipo, delta, motivo_codigo, contado FROM ajustes_camion")
        )
    ).mappings().one()
    assert fila["tipo"] == "merma"
    assert fila["delta"] == Decimal("-6.000")
    assert fila["motivo_codigo"] == "DANADO_TRANSPORTE"
    # Un conteo captura lo contado; una merma no (`CONSTRAINT conteo_trae_contado`).
    assert fila["contado"] is None


async def test_una_entrada_sube_el_saldo(cliente, semilla, camion_con_sopa, sesion):
    await _entrar(cliente)
    await _ajustar(cliente, semilla, camion_con_sopa, tipo="entrada", cantidad="10")

    assert await _saldo(sesion, semilla["camion"], camion_con_sopa["producto"]) == Decimal("40.000")
    assert (
        await sesion.execute(text("SELECT delta FROM ajustes_camion"))
    ).scalar_one() == Decimal("10.000")


# ---------------------------------------------------------------------------
# Lo que la pantalla impide
# ---------------------------------------------------------------------------


async def test_UN_AJUSTE_NO_EMPUJA_EL_CAMION_A_NEGATIVO(
    cliente, semilla, camion_con_sopa, sesion
):
    """Un camión SÍ puede estar negativo: una venta offline entró con el conteo en
    cero (§0.1). Pero eso es un hecho que llegó tarde; esto es alguien capturando
    ahora, y a lo que se captura se le revisa (doctrina de la 0026).
    """
    await _entrar(cliente)
    r = await _ajustar(
        cliente,
        semilla,
        camion_con_sopa,
        tipo="merma",
        cantidad="50",
        motivo_codigo="DANADO_TRANSPORTE",
    )

    assert "lo dejaría en -20" in solo_texto(r)
    assert await _saldo(sesion, semilla["camion"], camion_con_sopa["producto"]) == Decimal("30.000")
    assert (
        await sesion.execute(text("SELECT count(*) FROM ajustes_camion"))
    ).scalar_one() == 0


async def test_esta_pantalla_NO_ajusta_bodegas(
    cliente, semilla, camion_con_sopa, sesion
):
    """Para una bodega existen las entradas y las salidas, que llevan folio, tipo,
    motivo y la regla de que un conteo no puede dejar negativo."""
    await _entrar(cliente)
    r = await _ajustar(
        cliente,
        semilla,
        camion_con_sopa,
        almacen=semilla["bodega"],
        tipo="conteo",
        contado="80",
    )

    assert "con una entrada o una salida" in solo_texto(r)
    en_bodega = await _saldo(sesion, semilla["bodega"], camion_con_sopa["producto"])
    assert en_bodega == Decimal("100.000")


async def test_la_nota_es_obligatoria(cliente, semilla, camion_con_sopa, sesion):
    """Esto cambia el inventario del camión de una persona que va a tener que
    explicarlo en su liquidación. Sin nota, ese día no hay nada que leer."""
    await _entrar(cliente)
    r = await _ajustar(
        cliente, semilla, camion_con_sopa, tipo="conteo", contado="12", nota="ajuste"
    )

    assert "al menos 10" in solo_texto(r)
    assert await _saldo(sesion, semilla["camion"], camion_con_sopa["producto"]) == Decimal("30.000")


async def test_ajustar_exige_el_token_csrf(cliente, semilla, camion_con_sopa):
    await _entrar(cliente)
    r = await cliente.post(
        f"/panel/inventario/{semilla['camion']}/{camion_con_sopa['producto']}/ajustar",
        data={"csrf": "inventado", "tipo": "conteo", "contado": "12", "nota": NOTA},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# El delta hacia el teléfono: la razón por la que esto se puede permitir
# ---------------------------------------------------------------------------


async def test_EL_AJUSTE_VIAJA_AL_TELEFONO_DEL_RESPONSABLE(
    cliente, semilla, camion_con_sopa, sesion
):
    """Es la prueba que sostiene la excepción a §0.2.

    Sin este delta, la pantalla sería exactamente lo que la migración 0025
    prohibió: cambiarle el inventario bajo los pies a alguien que está vendiendo
    offline con otra cifra en el teléfono.
    """
    await _entrar(cliente)
    await _ajustar(cliente, semilla, camion_con_sopa, tipo="conteo", contado="12")

    delta = (
        await sesion.execute(
            text(
                "SELECT payload, vendedor_id, ruta_id FROM change_log "
                " WHERE entidad = 'ajuste_camion' ORDER BY cursor DESC LIMIT 1"
            )
        )
    ).mappings().one()

    # Acotado al responsable del camión: el ajuste del camión de Juan no le importa
    # al teléfono de Pedro.
    assert delta["vendedor_id"] == semilla["vendedor"]
    assert delta["payload"]["producto_id"] == str(camion_con_sopa["producto"])
    # La DIFERENCIA firmada, no el saldo: un saldo de hace cinco minutos aplicado
    # ahora borraría las ventas de esos cinco minutos.
    assert delta["payload"]["delta"] == "-18.000"
    assert delta["payload"]["tipo"] == "conteo"
    assert delta["payload"]["nota"] == NOTA


async def test_la_pantalla_muestra_los_ajustes_con_su_nota(
    cliente, semilla, camion_con_sopa
):
    """Es el renglón que el vendedor va a cuestionar: «¿quién me quitó 18?». La
    respuesta tiene que estar antes que la pregunta."""
    await _entrar(cliente)
    await _ajustar(cliente, semilla, camion_con_sopa, tipo="conteo", contado="12")

    plano = solo_texto(
        await cliente.get(
            f"/panel/inventario/{semilla['camion']}/{camion_con_sopa['producto']}"
        )
    )
    assert "Ajustes de la oficina" in plano
    assert NOTA in plano
    assert "Bryan" in plano


async def test_GERENCIA_SI_AJUSTA_EL_CAMION(cliente, semilla, camion_con_sopa, sesion):
    """Decisión de la dirección, octubre 2026. La 0031 le concede
    `inventario.ajustar`."""
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

    await _entrar(cliente, "GER01")
    await _ajustar(cliente, semilla, camion_con_sopa, tipo="conteo", contado="12")

    assert await _saldo(sesion, semilla["camion"], camion_con_sopa["producto"]) == Decimal("12.000")
