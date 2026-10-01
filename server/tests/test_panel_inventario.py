"""Existencias y libro mayor en el panel.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
Esta pantalla no escribe nada, así que lo que hay que probar es lo contrario de lo
habitual: que **muestre** las dos cosas que importan y que las distinga.

1. **La caché y el libro mayor son dos tablas distintas**, y cuando no coinciden
   hay un bug en alguna transacción que escribió una y no la otra. Eso no se puede
   detectar mirando la caché — por definición. La pantalla compara y lo delata.
2. **El saldo corriente en orden cronológico**, para poder ver *en qué movimiento*
   el inventario se fue a negativo. Es una pregunta distinta de si hoy está
   negativo, y la que de verdad se hace al investigar.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR, texto_plano

pytestmark = pytest.mark.asyncio


async def _entrar(cliente, codigo: str = "ADMIN01") -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": codigo, "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


@pytest.fixture
async def con_inventario(sesion, semilla) -> dict:
    """Un producto que entró a bodega, salió al camión, y se vendió de más.

    La última parte deja el camión en negativo, que es un estado legítimo: una venta
    offline puede llegar cuando el camión ya marcaba cero.
    """
    producto = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base) "
            "VALUES (:p, 'ATUN-140', 'Atún en agua 140 g', 'PZA')"
        ),
        {"p": producto},
    )
    await sesion.execute(
        text(
            "INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, es_default) "
            "VALUES (:p, 'PZA', 1, true), (:p, 'CAJA', 24, false)"
        ),
        {"p": producto},
    )

    async def mover(tipo, origen, destino, cantidad):
        await sesion.execute(
            text(
                "INSERT INTO movimientos_inventario "
                "  (tipo, almacen_origen_id, almacen_destino_id, producto_id, cantidad, "
                "   documento_tipo, documento_id, usuario_id) "
                "VALUES (:t, :o, :d, :p, :cant, 'prueba', :doc, :u)"
            ),
            {
                "t": tipo, "o": origen, "d": destino, "p": producto,
                "cant": cantidad, "doc": uuid.uuid4(), "u": semilla["admin"],
            },
        )
        for almacen, signo in ((origen, -1), (destino, 1)):
            if almacen is None:
                continue
            await sesion.execute(
                text(
                    "INSERT INTO existencias (almacen_id, producto_id, cantidad) "
                    "VALUES (:a, :p, :cant) "
                    "ON CONFLICT (almacen_id, producto_id) DO UPDATE "
                    "   SET cantidad = existencias.cantidad + :cant"
                ),
                {"a": almacen, "p": producto, "cant": Decimal(cantidad) * signo},
            )

    await mover("compra", None, semilla["bodega"], 1000)
    await mover("carga", semilla["bodega"], semilla["camion"], 240)
    await mover("venta", semilla["camion"], None, 250)  # se vendió más de lo cargado
    await sesion.commit()
    return {"producto": producto}


# ---------------------------------------------------------------------------
# La lista
# ---------------------------------------------------------------------------


async def test_la_pantalla_muestra_cada_almacen_con_su_total(
    cliente, semilla, con_inventario
):
    """Antes esto solo se veía de refilón al armar un borrador de carga, lo cual
    obligaba a abrir un borrador para consultar — y alguien lo podía confirmar."""
    await _entrar(cliente)
    r = await cliente.get("/panel/inventario")

    assert r.status_code == 200
    assert "Bodega" in r.text
    assert "Camión 01" in r.text
    assert "Atún en agua 140 g" in r.text


async def test_abre_en_la_bodega_porque_es_donde_esta_el_inventario(
    cliente, semilla, con_inventario
):
    await _entrar(cliente)
    r = await cliente.get("/panel/inventario")
    # 1000 − 240 = 760 en bodega.
    assert "760" in r.text


async def test_el_camion_en_negativo_se_marca(cliente, semilla, con_inventario):
    """240 cargadas y 250 vendidas: −10. No es un error del sistema, es un conteo
    que hay que revisar — y `existencias` no tiene `CHECK (cantidad >= 0)` justo
    para que una venta offline nunca se rechace por esto."""
    await _entrar(cliente)
    r = await cliente.get(f"/panel/inventario?almacen={semilla['camion']}")

    assert "-10" in r.text
    assert "en negativo" in r.text


async def test_el_filtro_de_negativos_solo_trae_esos(cliente, semilla, con_inventario):
    await _entrar(cliente)
    bodega = await cliente.get(
        f"/panel/inventario?almacen={semilla['bodega']}&filtro=negativos"
    )
    assert "Así debe verse" in bodega.text

    camion = await cliente.get(
        f"/panel/inventario?almacen={semilla['camion']}&filtro=negativos"
    )
    assert "Atún en agua 140 g" in camion.text


async def test_la_busqueda_encuentra_por_sku(cliente, semilla, con_inventario):
    await _entrar(cliente)
    r = await cliente.get(f"/panel/inventario?almacen={semilla['bodega']}&q=ATUN")
    assert "Atún en agua 140 g" in r.text

    nada = await cliente.get(f"/panel/inventario?almacen={semilla['bodega']}&q=NOEXISTE")
    assert "Nada que coincida" in nada.text


async def test_sin_almacenes_lo_dice_en_vez_de_una_tabla_vacia(cliente, semilla, sesion):
    await _entrar(cliente)
    # La semilla crea bodega y camión; se desactivan para ver el caso del día uno.
    await sesion.execute(text("UPDATE almacenes SET activo = false"))
    await sesion.commit()

    r = await cliente.get("/panel/inventario")
    assert "sin bodega no hay de dónde cargar" in texto_plano(r)


# ---------------------------------------------------------------------------
# El descuadre entre la caché y el libro mayor
# ---------------------------------------------------------------------------


async def test_UN_DESCUADRE_ENTRE_LA_CACHE_Y_EL_LIBRO_SE_DELATA(
    cliente, semilla, con_inventario, sesion
):
    """Si la suma del libro mayor no cuadra con la caché, hay un bug en alguna
    transacción que escribió una y no la otra.

    Eso **no se puede detectar mirando la caché**, por definición: el número está
    ahí y se ve perfectamente razonable. El job de reconciliación nocturno existe
    para esto; esta pantalla permite verlo sin esperar a la noche.
    """
    await _entrar(cliente)

    # Se corrompe la caché a mano, que es exactamente lo que haría el bug.
    await sesion.execute(
        text(
            "UPDATE existencias SET cantidad = 999 "
            " WHERE almacen_id = :a AND producto_id = :p"
        ),
        {"a": semilla["bodega"], "p": con_inventario["producto"]},
    )
    await sesion.commit()

    r = await cliente.get(f"/panel/inventario?almacen={semilla['bodega']}")
    assert "no cuadra con el libro mayor" in r.text
    assert "el libro dice 760" in r.text


# ---------------------------------------------------------------------------
# El libro mayor de un producto
# ---------------------------------------------------------------------------


async def test_el_libro_mayor_trae_el_saldo_corriente_en_orden(
    cliente, semilla, con_inventario
):
    """La pregunta que sigue a un número raro es "¿cómo llegó ahí?".

    Sin esta pantalla la respuesta sale de psql, y entonces nadie la hace.
    """
    await _entrar(cliente)
    r = await cliente.get(
        f"/panel/inventario/{semilla['camion']}/{con_inventario['producto']}"
    )

    assert r.status_code == 200
    assert "carga" in r.text
    assert "venta" in r.text
    # Entró 240, salió 250: el saldo pasó por 240 y terminó en −10.
    assert "+240" in r.text
    assert "-250" in r.text
    assert "-10" in r.text
    assert "cuadra" in r.text


async def test_el_libro_mayor_dice_cuando_NO_cuadra(
    cliente, semilla, con_inventario, sesion
):
    await _entrar(cliente)
    await sesion.execute(
        text(
            "UPDATE existencias SET cantidad = 5 "
            " WHERE almacen_id = :a AND producto_id = :p"
        ),
        {"a": semilla["camion"], "p": con_inventario["producto"]},
    )
    await sesion.commit()

    r = await cliente.get(
        f"/panel/inventario/{semilla['camion']}/{con_inventario['producto']}"
    )
    assert "NO cuadra" in r.text
    assert "así que es el que tiene razón" in texto_plano(r)


async def test_el_libro_mayor_dice_que_no_se_puede_editar(
    cliente, semilla, con_inventario
):
    """Es append-only por disparador, no por convención. Que la pantalla lo diga
    evita que alguien pida "una pantallita para corregir ese movimiento"."""
    await _entrar(cliente)
    r = await cliente.get(
        f"/panel/inventario/{semilla['bodega']}/{con_inventario['producto']}"
    )
    assert "append-only" in r.text
    assert "se corrige con un documento compensatorio" in texto_plano(r)


async def test_un_producto_que_nunca_se_movio_lo_dice(cliente, semilla, sesion):
    await _entrar(cliente)
    producto = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base) "
            "VALUES (:p, 'NUEVO', 'Producto nuevo', 'PZA')"
        ),
        {"p": producto},
    )
    await sesion.commit()

    r = await cliente.get(f"/panel/inventario/{semilla['bodega']}/{producto}")
    assert "nunca se movió" in r.text


# ---------------------------------------------------------------------------
# Acceso
# ---------------------------------------------------------------------------


async def test_el_gerente_puede_ver_el_inventario(cliente, semilla, con_inventario, sesion):
    """Gerencia es de solo lectura, y esto es lectura: `inventario.ver` lo tiene."""
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
    r = await cliente.get("/panel/inventario")
    assert r.status_code == 200
    assert "Atún en agua 140 g" in r.text


async def test_sin_sesion_el_inventario_manda_al_login(cliente, semilla):
    r = await cliente.get("/panel/inventario", follow_redirects=False)
    assert r.status_code == 303
    assert "volver=/panel/inventario" in r.headers["location"]
