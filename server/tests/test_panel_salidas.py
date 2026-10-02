"""Salidas de bodega: el conteo que encuentra menos, y la merma.

────────────────────────────────────────────────────────────────────────────
LA REGLA QUE ESTAS PRUEBAS DEFIENDEN SOBRE TODAS
────────────────────────────────────────────────────────────────────────────
Una salida NO deja la existencia en negativo, y eso parece contradecir §0.1 —«el
mundo físico ya ocurrió, el servidor marca y no rechaza»— cuando en realidad lo
confirma:

  · §0.1 es para hechos que YA PASARON EN LA CALLE y llegan tarde. El manejador
    de sincronización lo dice con todas sus letras: «se MARCA, no se rechaza, el
    cartón ya está roto». Hay pruebas de eso en test_merma_ingesta.py.
  · Una salida de bodega la está TECLEANDO alguien con el anaquel a la vista. El
    anaquel no puede tener menos que nada, así que una salida de más es un
    dedazo — y un asiento equivocado en un libro append-only no se borra.

Y las otras cuatro:

1. En un conteo se captura LO CONTADO, y la base impone que la resta cuadre.
2. Si la existencia se movió desde que se contó, no se aplica: la resta guardada
   ya no describe el anaquel.
3. Contar dos veces REEMPLAZA; mermar dos veces SUMA.
4. Un faltante de conteo NO lleva motivo, y una merma sí.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.domain.entradas import TIPOS_DE_SALIDA, tipo_de_movimiento_de_salida
from tests.conftest import PASSWORD_VENDEDOR, solo_texto

# Sin `pytestmark`: `asyncio_mode = "auto"` ya está en pyproject.toml y la marca
# a nivel de módulo avisaría por cada prueba pura de este archivo.


# ===========================================================================
# Andamios
# ===========================================================================
async def _entrar(cliente, codigo: str = "ADMIN01") -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": codigo, "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


def _csrf(cliente) -> str:
    """Calculado de la cookie, igual que `sesion_web.token_csrf`.

    Rasparlo del HTML no sirve: una salida confirmada no dibuja formularios, y
    un rol sin `inventario.ajustar` tampoco.
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


async def _producto(sesion, *, sku: str = "COCA600", factor: int = 24,
                    maneja_lote: bool = False) -> uuid.UUID:
    categoria = (
        await sesion.execute(text("SELECT id FROM categorias LIMIT 1"))
    ).scalar()
    if categoria is None:
        categoria = uuid.uuid4()
        await sesion.execute(
            text("INSERT INTO categorias (id, codigo, nombre) VALUES (:i,'ABA','Abarrotes')"),
            {"i": categoria},
        )
    identificador = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, categoria_id, unidad_base, "
            "                       maneja_lote, creado_en, actualizado_en) "
            "VALUES (:i, :sku, :nom, :c, 'PZA', :lote, now(), now())"
        ),
        {"i": identificador, "sku": sku, "nom": f"Producto {sku}",
         "c": categoria, "lote": maneja_lote},
    )
    for codigo, f in (("PZA", 1), ("CAJA", factor)):
        await sesion.execute(
            text(
                "INSERT INTO producto_unidades (producto_id, unidad_codigo, "
                "                               factor, es_default, activo) "
                "VALUES (:p, :u, :f, :pred, true)"
            ),
            {"p": identificador, "u": codigo, "f": f, "pred": codigo == "CAJA"},
        )
    await sesion.commit()
    return identificador


async def _sembrar(sesion, almacen, producto, cantidad: str) -> None:
    """Existencia inicial, directo en la tabla. Aquí sí: lo que se prueba es la
    salida, y el camino de la entrada ya tiene sus 32 pruebas."""
    await sesion.execute(
        text(
            "INSERT INTO existencias (almacen_id, producto_id, cantidad) "
            "VALUES (:a, :p, :c) "
            "ON CONFLICT (almacen_id, producto_id) DO UPDATE SET cantidad = :c"
        ),
        {"a": almacen, "p": producto, "c": Decimal(cantidad)},
    )
    await sesion.commit()


async def _abrir(cliente, semilla, *, tipo: str = "conteo", motivo: str = "",
                 nota: str = "conteo del anaquel 3, con Beto", almacen=None,
                 fecha: str = ""):
    return await cliente.post(
        "/panel/salidas/nueva",
        data={
            "csrf": _csrf(cliente),
            "tipo": tipo,
            "almacen_origen_id": str(almacen or semilla["bodega"]),
            "motivo_codigo": motivo,
            "fecha": fecha,
            "nota": nota,
        },
        follow_redirects=False,
    )


def _id_de(respuesta) -> uuid.UUID:
    destino = respuesta.headers["location"]
    return uuid.UUID(destino.split("/panel/salidas/")[1].split("?")[0])


async def _renglon(cliente, salida_id, *, sku="COCA600", unidad="PZA",
                   cantidad="80", lote=""):
    return await cliente.post(
        f"/panel/salidas/{salida_id}/renglon",
        data={"csrf": _csrf(cliente), "producto": sku, "unidad_codigo": unidad,
              "cantidad": cantidad, "lote": lote},
        follow_redirects=False,
    )


async def _confirmar(cliente, salida_id):
    return await cliente.post(
        f"/panel/salidas/{salida_id}/confirmar",
        data={"csrf": _csrf(cliente)}, follow_redirects=False,
    )


async def _existencia(sesion, almacen, producto) -> Decimal:
    valor = (
        await sesion.execute(
            text("SELECT cantidad FROM existencias WHERE almacen_id = :a "
                 "  AND producto_id = :p"),
            {"a": almacen, "p": producto},
        )
    ).scalar()
    return Decimal(valor) if valor is not None else Decimal(0)


# ===========================================================================
# El tipo decide el asiento
# ===========================================================================
def test_cada_tipo_de_salida_mapea_a_su_asiento():
    """Aquí sí son dos tipos distintos del libro mayor, al contrario que en las
    entradas: separar una pérdida identificada de un descuadre sin explicar es
    lo que dice si hay algo que arreglar en la bodega."""
    assert tipo_de_movimiento_de_salida("conteo") == "ajuste"
    assert tipo_de_movimiento_de_salida("merma") == "merma"


def test_un_tipo_de_salida_desconocido_no_se_adivina():
    with pytest.raises(ValueError, match="tipo de salida desconocido"):
        tipo_de_movimiento_de_salida("regalo")


async def test_los_asientos_de_salida_existen_en_el_libro_mayor(sesion):
    """Verificación de frescura: el CHECK de PostgreSQL es el que manda."""
    definicion = (
        await sesion.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                " WHERE conrelid = 'movimientos_inventario'::regclass "
                "   AND conname = 'movimientos_inventario_tipo_check'"
            )
        )
    ).scalar_one()
    for tipo in TIPOS_DE_SALIDA:
        assert f"'{tipo_de_movimiento_de_salida(tipo)}'" in definicion, tipo


# ===========================================================================
# LA REGLA: ninguna salida deja la existencia en negativo
# ===========================================================================
async def test_una_merma_mayor_que_la_existencia_se_rechaza(cliente, sesion, semilla):
    """El anaquel no puede tener menos que nada: es un dedazo, no un hecho."""
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _sembrar(sesion, semilla["bodega"], producto, "100")

    salida = _id_de(await _abrir(cliente, semilla, tipo="merma",
                                 motivo="DANADO_BODEGA", nota="se mojó el anaquel"))
    await _renglon(cliente, salida, cantidad="150")

    r = await _confirmar(cliente, salida)
    assert "en%20negativo" in r.headers["location"]
    # Y nada se movió: todo o nada.
    assert await _existencia(sesion, semilla["bodega"], producto) == Decimal("100")
    assert (
        await sesion.execute(text("SELECT count(*) FROM movimientos_inventario"))
    ).scalar() == 0
    assert (await sesion.execute(text("SELECT estado FROM salidas"))).scalar() == "borrador"


async def test_el_mensaje_dice_cuanto_hay_y_cuanto_sale(cliente, sesion, semilla):
    """Sin las dos cifras, quien lo lee no sabe qué corregir."""
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _sembrar(sesion, semilla["bodega"], producto, "100")
    salida = _id_de(await _abrir(cliente, semilla, tipo="merma",
                                 motivo="ROTO", nota="cajas rotas"))
    await _renglon(cliente, salida, cantidad="150")
    destino = (await _confirmar(cliente, salida)).headers["location"]
    texto = solo_texto(await cliente.get(destino))
    assert "hay 100" in texto
    assert "salen 150" in texto


async def test_el_borrador_avisa_antes_de_llegar_al_final(cliente, sesion, semilla):
    """Capturar veinte renglones y enterarse al confirmar es perder el trabajo."""
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _sembrar(sesion, semilla["bodega"], producto, "100")
    salida = _id_de(await _abrir(cliente, semilla, tipo="merma",
                                 motivo="ROBO", nota="faltan tarimas"))
    await _renglon(cliente, salida, cantidad="150")
    texto = solo_texto(await cliente.get(f"/panel/salidas/{salida}"))
    assert "dejaría la bodega en negativo" in texto


async def test_una_salida_exacta_hasta_cero_si_se_acepta(cliente, sesion, semilla):
    """Vaciar el anaquel es legítimo; dejarlo en negativo no."""
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _sembrar(sesion, semilla["bodega"], producto, "100")
    salida = _id_de(await _abrir(cliente, semilla, tipo="merma",
                                 motivo="CADUCADO", nota="todo el lote caducó"))
    await _renglon(cliente, salida, cantidad="100")
    assert (await _confirmar(cliente, salida)).status_code == 303
    assert await _existencia(sesion, semilla["bodega"], producto) == Decimal("0")


async def test_dos_renglones_del_mismo_producto_se_suman_para_la_regla(
    cliente, sesion, semilla
):
    """60 + 60 contra 100 no pasa, aunque ninguno de los dos exceda por separado.

    Validar renglón por renglón dejaría pasar el descuadre que la regla existe
    para impedir.
    """
    await _entrar(cliente)
    producto = await _producto(sesion, maneja_lote=True)
    await _sembrar(sesion, semilla["bodega"], producto, "100")
    salida = _id_de(await _abrir(cliente, semilla, tipo="merma",
                                 motivo="DANADO_BODEGA", nota="dos tarimas"))
    await _renglon(cliente, salida, cantidad="60", lote="L-A")
    await _renglon(cliente, salida, cantidad="60", lote="L-B")

    r = await _confirmar(cliente, salida)
    assert "en%20negativo" in r.headers["location"]
    assert await _existencia(sesion, semilla["bodega"], producto) == Decimal("100")


# ===========================================================================
# 1. El conteo captura lo contado, y la base impone la resta
# ===========================================================================
async def test_en_un_conteo_se_captura_lo_contado_y_el_sistema_resta(
    cliente, sesion, semilla
):
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _sembrar(sesion, semilla["bodega"], producto, "100")

    salida = _id_de(await _abrir(cliente, semilla, tipo="conteo"))
    r = await _renglon(cliente, salida, cantidad="80")
    assert "contaste 80" in r.headers["location"].replace("%20", " ")

    fila = (
        await sesion.execute(
            text(
                "SELECT cantidad, contado, existencia_al_capturar, lote "
                "  FROM salida_detalle"
            )
        )
    ).mappings().one()
    assert fila["contado"] == Decimal("80.000")
    assert fila["existencia_al_capturar"] == Decimal("100.000")
    assert fila["cantidad"] == Decimal("20.000")      # la resta, hecha por el sistema
    assert fila["lote"] is None

    await _confirmar(cliente, salida)
    assert await _existencia(sesion, semilla["bodega"], producto) == Decimal("80")
    assert (
        await sesion.execute(text("SELECT tipo FROM movimientos_inventario"))
    ).scalar() == "ajuste"


async def test_la_base_impone_que_la_resta_del_conteo_cuadre(sesion, semilla):
    """Un bug en Python no puede escribir un renglón de conteo que no cuadre."""
    producto = await _producto(sesion)
    salida = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO salidas (id, folio, almacen_origen_id, tipo, nota) "
            "VALUES (:i, 'SA-X', :a, 'conteo', 'prueba')"
        ),
        {"i": salida, "a": semilla["bodega"]},
    )
    await sesion.commit()
    with pytest.raises(Exception, match="conteo_cuadra"):
        await sesion.execute(
            text(
                "INSERT INTO salida_detalle (salida_id, producto_id, cantidad, "
                "        contado, existencia_al_capturar) "
                "VALUES (:s, :p, 99, 80, 100)"
            ),
            {"s": salida, "p": producto},
        )


async def test_contar_cero_vacia_el_anaquel(cliente, sesion, semilla):
    """Es el resultado más común de un conteo, y `cantidad_base` exige > 0: el
    cero necesita su propio camino."""
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _sembrar(sesion, semilla["bodega"], producto, "48")
    salida = _id_de(await _abrir(cliente, semilla, tipo="conteo"))
    r = await _renglon(cliente, salida, cantidad="0")
    assert r.status_code == 303
    fila = (
        await sesion.execute(text("SELECT cantidad, contado FROM salida_detalle"))
    ).mappings().one()
    assert fila["contado"] == Decimal("0.000")
    assert fila["cantidad"] == Decimal("48.000")
    await _confirmar(cliente, salida)
    assert await _existencia(sesion, semilla["bodega"], producto) == Decimal("0")


async def test_si_el_conteo_encuentra_mas_manda_a_las_entradas(
    cliente, sesion, semilla
):
    """Este no es el documento, y decirlo vale más que rechazarlo sin más."""
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _sembrar(sesion, semilla["bodega"], producto, "100")
    salida = _id_de(await _abrir(cliente, semilla, tipo="conteo"))
    r = await _renglon(cliente, salida, cantidad="120")
    destino = r.headers["location"]
    assert "ENTRADA" in destino or "entrada" in destino.lower()
    texto = solo_texto(await cliente.get(destino))
    assert "sobran 20" in texto
    assert (
        await sesion.execute(text("SELECT count(*) FROM salida_detalle"))
    ).scalar() == 0


async def test_un_conteo_que_cuadra_no_es_un_ajuste(cliente, sesion, semilla):
    """Un renglón de cero no describe ningún ajuste, y `cantidad > 0` lo
    impediría de todos modos: mejor decirlo con palabras."""
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _sembrar(sesion, semilla["bodega"], producto, "100")
    salida = _id_de(await _abrir(cliente, semilla, tipo="conteo"))
    r = await _renglon(cliente, salida, cantidad="100")
    assert "cuadra" in r.headers["location"]
    assert (
        await sesion.execute(text("SELECT count(*) FROM salida_detalle"))
    ).scalar() == 0


async def test_un_conteo_en_cajas_se_convierte(cliente, sesion, semilla):
    """Se cuenta en cajas y el sistema trabaja en piezas."""
    await _entrar(cliente)
    producto = await _producto(sesion, factor=24)
    await _sembrar(sesion, semilla["bodega"], producto, "240")
    salida = _id_de(await _abrir(cliente, semilla, tipo="conteo"))
    await _renglon(cliente, salida, unidad="CAJA", cantidad="8")
    fila = (
        await sesion.execute(
            text("SELECT contado, cantidad, unidades_capturadas, unidad_codigo "
                 "  FROM salida_detalle")
        )
    ).mappings().one()
    assert fila["contado"] == Decimal("192.000")        # 8 × 24
    assert fila["cantidad"] == Decimal("48.000")        # 240 − 192
    assert fila["unidades_capturadas"] == Decimal("8.000")
    assert fila["unidad_codigo"] == "CAJA"


async def test_un_conteo_no_acepta_lote(cliente, sesion, semilla):
    """`existencias` no tiene dimensión de lote: aceptarlo prometería una
    precisión que la tabla contra la que se compara no tiene."""
    await _entrar(cliente)
    await _producto(sesion)
    salida = _id_de(await _abrir(cliente, semilla, tipo="conteo"))
    r = await _renglon(cliente, salida, cantidad="80", lote="L-1")
    assert "por%20producto" in r.headers["location"]


# ===========================================================================
# 2. Si la existencia se movió desde el conteo, no se aplica
# ===========================================================================
async def test_si_la_existencia_se_movio_el_conteo_no_se_aplica(
    cliente, sesion, semilla
):
    """Salió una carga entre el conteo y el cierre: el anaquel también perdió
    esas piezas, así que aplicar la resta guardada descontaría dos veces."""
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _sembrar(sesion, semilla["bodega"], producto, "100")

    salida = _id_de(await _abrir(cliente, semilla, tipo="conteo"))
    await _renglon(cliente, salida, cantidad="80")      # faltan 20

    # Entre el conteo y el cierre salen 30 piezas en una carga.
    await _sembrar(sesion, semilla["bodega"], producto, "70")

    r = await _confirmar(cliente, salida)
    assert "se%20movi%C3%B3" in r.headers["location"]
    # Nada se aplicó: ni 70−20 ni nada.
    assert await _existencia(sesion, semilla["bodega"], producto) == Decimal("70")
    assert (
        await sesion.execute(text("SELECT count(*) FROM movimientos_inventario"))
    ).scalar() == 0


async def test_el_borrador_avisa_que_la_existencia_se_movio(cliente, sesion, semilla):
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _sembrar(sesion, semilla["bodega"], producto, "100")
    salida = _id_de(await _abrir(cliente, semilla, tipo="conteo"))
    await _renglon(cliente, salida, cantidad="80")
    await _sembrar(sesion, semilla["bodega"], producto, "70")
    texto = solo_texto(await cliente.get(f"/panel/salidas/{salida}"))
    assert "se movió desde que se contó" in texto


async def test_recontar_despues_de_que_se_movio_lo_arregla(cliente, sesion, semilla):
    """El camino de salida del aviso anterior: volver a capturar reemplaza."""
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _sembrar(sesion, semilla["bodega"], producto, "100")
    salida = _id_de(await _abrir(cliente, semilla, tipo="conteo"))
    await _renglon(cliente, salida, cantidad="80")
    await _sembrar(sesion, semilla["bodega"], producto, "70")

    await _renglon(cliente, salida, cantidad="60")   # se vuelve a contar: 60
    fila = (
        await sesion.execute(
            text("SELECT contado, existencia_al_capturar, cantidad "
                 "  FROM salida_detalle")
        )
    ).mappings().one()
    assert fila["contado"] == Decimal("60.000")
    assert fila["existencia_al_capturar"] == Decimal("70.000")
    assert fila["cantidad"] == Decimal("10.000")

    assert (await _confirmar(cliente, salida)).status_code == 303
    assert await _existencia(sesion, semilla["bodega"], producto) == Decimal("60")


# ===========================================================================
# 3. Contar dos veces reemplaza; mermar dos veces suma
# ===========================================================================
async def test_contar_dos_veces_el_mismo_producto_reemplaza(cliente, sesion, semilla):
    """La primera cuenta estaba mal, no hay el doble de faltante."""
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _sembrar(sesion, semilla["bodega"], producto, "100")
    salida = _id_de(await _abrir(cliente, semilla, tipo="conteo"))
    await _renglon(cliente, salida, cantidad="80")
    await _renglon(cliente, salida, cantidad="90")

    fila = (
        await sesion.execute(
            text("SELECT contado, cantidad FROM salida_detalle")
        )
    ).mappings().one()
    assert fila["contado"] == Decimal("90.000")
    assert fila["cantidad"] == Decimal("10.000")      # 100 − 90, no 30
    await _confirmar(cliente, salida)
    assert await _existencia(sesion, semilla["bodega"], producto) == Decimal("90")


async def test_mermar_dos_veces_el_mismo_lote_suma(cliente, sesion, semilla):
    """Son dos pérdidas distintas de la misma tarima, no una corrección."""
    await _entrar(cliente)
    producto = await _producto(sesion, maneja_lote=True)
    await _sembrar(sesion, semilla["bodega"], producto, "100")
    salida = _id_de(await _abrir(cliente, semilla, tipo="merma",
                                 motivo="ROTO", nota="se rompieron cajas"))
    await _renglon(cliente, salida, cantidad="10", lote="L-A")
    await _renglon(cliente, salida, cantidad="5", lote="L-A")

    assert (
        await sesion.execute(text("SELECT cantidad FROM salida_detalle"))
    ).scalar() == Decimal("15.000")


# ===========================================================================
# 4. El motivo: obligatorio en merma, prohibido en conteo
# ===========================================================================
async def test_una_merma_sin_motivo_se_rechaza(cliente, sesion, semilla):
    await _entrar(cliente)
    r = await _abrir(cliente, semilla, tipo="merma", motivo="", nota="algo pasó")
    assert "motivo%20del%20cat%C3%A1logo" in r.headers["location"]
    assert (await sesion.execute(text("SELECT count(*) FROM salidas"))).scalar() == 0


async def test_un_conteo_con_motivo_se_rechaza_y_dice_por_que(
    cliente, sesion, semilla
):
    """Un faltante de conteo es un faltante CUYA CAUSA NO SE CONOCE. Obligar a
    elegir uno haría que alguien marcara 'ROBO' sin saber, y eso convierte un
    dato duro en una acusación inventada."""
    await _entrar(cliente)
    r = await _abrir(cliente, semilla, tipo="conteo", motivo="ROBO")
    destino = r.headers["location"]
    assert (await sesion.execute(text("SELECT count(*) FROM salidas"))).scalar() == 0
    texto = solo_texto(await cliente.get(destino))
    assert "acusación inventada" in texto


async def test_las_dos_clases_exigen_nota(cliente, sesion, semilla):
    await _entrar(cliente)
    for tipo, motivo in (("conteo", ""), ("merma", "CADUCADO")):
        r = await _abrir(cliente, semilla, tipo=tipo, motivo=motivo, nota="  ")
        assert "nota" in r.headers["location"].lower(), tipo
    assert (await sesion.execute(text("SELECT count(*) FROM salidas"))).scalar() == 0


async def test_el_motivo_viene_del_catalogo_cerrado_que_ya_existe(cliente, sesion, semilla):
    """No se inventó un catálogo nuevo: `motivos_merma` es el de la Fase 6 y ya
    trae DANADO_BODEGA, CADUCADO y ROBO."""
    await _entrar(cliente)
    texto = solo_texto(await cliente.get("/panel/salidas"))
    assert "Dañado en bodega" in texto
    assert "Producto caducado" in texto


# ===========================================================================
# La merma viaja al almacén de merma si existe
# ===========================================================================
async def test_una_merma_entra_al_almacen_de_merma_si_hay_uno(
    cliente, sesion, semilla
):
    """Igual que la merma del camión (`infra/sync/manejadores.py`): si el
    almacén existe, el libro mayor dice origen Y destino, y las existencias de
    los dos lados lo reflejan — o el almacén de merma quedaría siempre en cero.
    """
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _sembrar(sesion, semilla["bodega"], producto, "100")
    almacen_merma = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO almacenes (id, codigo, nombre, tipo) "
            "VALUES (:i, 'MERMA', 'Almacén de merma', 'merma')"
        ),
        {"i": almacen_merma},
    )
    await sesion.commit()

    salida = _id_de(await _abrir(cliente, semilla, tipo="merma",
                                 motivo="CADUCADO", nota="lote vencido"))
    await _renglon(cliente, salida, cantidad="30")
    await _confirmar(cliente, salida)

    assert await _existencia(sesion, semilla["bodega"], producto) == Decimal("70")
    assert await _existencia(sesion, almacen_merma, producto) == Decimal("30")
    asiento = (
        await sesion.execute(
            text(
                "SELECT tipo, almacen_origen_id, almacen_destino_id "
                "  FROM movimientos_inventario"
            )
        )
    ).mappings().one()
    assert asiento["tipo"] == "merma"
    assert asiento["almacen_origen_id"] == semilla["bodega"]
    assert asiento["almacen_destino_id"] == almacen_merma


async def test_sin_almacen_de_merma_el_destino_queda_nulo(cliente, sesion, semilla):
    """El CHECK del libro mayor solo exige uno de los dos lados."""
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _sembrar(sesion, semilla["bodega"], producto, "100")
    salida = _id_de(await _abrir(cliente, semilla, tipo="merma",
                                 motivo="MUESTRA", nota="degustación en tienda"))
    await _renglon(cliente, salida, cantidad="12")
    await _confirmar(cliente, salida)
    assert (
        await sesion.execute(
            text("SELECT almacen_destino_id FROM movimientos_inventario")
        )
    ).scalar() is None
    assert await _existencia(sesion, semilla["bodega"], producto) == Decimal("88")


# ===========================================================================
# El ciclo del documento
# ===========================================================================
async def test_el_borrador_no_mueve_nada(cliente, sesion, semilla):
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _sembrar(sesion, semilla["bodega"], producto, "100")
    salida = _id_de(await _abrir(cliente, semilla, tipo="conteo"))
    await _renglon(cliente, salida, cantidad="80")
    assert await _existencia(sesion, semilla["bodega"], producto) == Decimal("100")
    assert (
        await sesion.execute(text("SELECT count(*) FROM movimientos_inventario"))
    ).scalar() == 0


async def test_confirmar_dos_veces_es_idempotente(cliente, sesion, semilla):
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _sembrar(sesion, semilla["bodega"], producto, "100")
    salida = _id_de(await _abrir(cliente, semilla, tipo="conteo"))
    await _renglon(cliente, salida, cantidad="80")

    assert (await _confirmar(cliente, salida)).status_code == 303
    segunda = await _confirmar(cliente, salida)
    assert "ya%20est%C3%A1%20confirmada" in segunda.headers["location"]
    assert await _existencia(sesion, semilla["bodega"], producto) == Decimal("80")
    assert (
        await sesion.execute(text("SELECT count(*) FROM movimientos_inventario"))
    ).scalar() == 1


async def test_no_se_edita_una_salida_confirmada(cliente, sesion, semilla):
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _sembrar(sesion, semilla["bodega"], producto, "100")
    salida = _id_de(await _abrir(cliente, semilla, tipo="conteo"))
    await _renglon(cliente, salida, cantidad="80")
    await _confirmar(cliente, salida)

    r = await _renglon(cliente, salida, cantidad="70")
    assert "ya%20est%C3%A1%20confirmada" in r.headers["location"]
    assert await _existencia(sesion, semilla["bodega"], producto) == Decimal("80")


async def test_no_se_cancela_una_salida_confirmada(cliente, sesion, semilla):
    """Se compensa con una entrada de ajuste, que es el camino que existe."""
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _sembrar(sesion, semilla["bodega"], producto, "100")
    salida = _id_de(await _abrir(cliente, semilla, tipo="conteo"))
    await _renglon(cliente, salida, cantidad="80")
    await _confirmar(cliente, salida)

    r = await cliente.post(
        f"/panel/salidas/{salida}/cancelar",
        data={"csrf": _csrf(cliente), "motivo": "me equivoqué"},
        follow_redirects=False,
    )
    assert "entrada%20de%20ajuste" in r.headers["location"]
    assert (await sesion.execute(text("SELECT estado FROM salidas"))).scalar() == "confirmada"


async def test_una_salida_sin_renglones_no_se_confirma(cliente, sesion, semilla):
    await _entrar(cliente)
    salida = _id_de(await _abrir(cliente, semilla, tipo="conteo"))
    r = await _confirmar(cliente, salida)
    assert "sin%20un%20solo%20rengl%C3%B3n" in r.headers["location"]


async def test_un_camion_no_se_ajusta_por_aqui(cliente, sesion, semilla):
    """Su faltante se descubre en la liquidación, que compara lo cargado contra
    lo retornado. Ajustarlo aquí lo contaría dos veces."""
    await _entrar(cliente)
    r = await _abrir(cliente, semilla, tipo="conteo", almacen=semilla["camion"])
    destino = r.headers["location"]
    assert (await sesion.execute(text("SELECT count(*) FROM salidas"))).scalar() == 0
    texto = solo_texto(await cliente.get(destino))
    assert "liquidación" in texto
    assert "dos veces" in texto


async def test_el_formulario_solo_ofrece_bodegas(cliente, sesion, semilla):
    await _entrar(cliente)
    r = await cliente.get("/panel/salidas")
    assert str(semilla["bodega"]) in r.text
    assert str(semilla["camion"]) not in r.text


async def test_una_fecha_futura_se_rechaza(cliente, sesion, semilla):
    await _entrar(cliente)
    manana = (date.today() + timedelta(days=1)).isoformat()
    r = await _abrir(cliente, semilla, tipo="conteo", fecha=manana)
    assert "futura" in r.headers["location"]


async def test_sin_el_permiso_se_ve_y_no_se_captura(cliente, sesion, semilla):
    from app.core.seguridad import hashear_password

    await sesion.execute(
        text(
            "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
            "                      rol_codigo, creado_en, actualizado_en) "
            "VALUES (:u, :s, 'GER01', 'Gerente', :h, 'gerente', now(), now())"
        ),
        {"u": uuid.uuid4(), "s": semilla["sucursal"],
         "h": hashear_password(PASSWORD_VENDEDOR)},
    )
    await sesion.commit()
    await _entrar(cliente, "GER01")

    r = await cliente.get("/panel/salidas")
    assert r.status_code == 200
    assert "inventario.ajustar" in solo_texto(r)
    assert "Registrar una salida" not in solo_texto(r)

    bloqueado = await _abrir(cliente, semilla, tipo="conteo")
    assert bloqueado.status_code == 403


# ===========================================================================
# El ciclo completo del inventario, ahora en los dos sentidos
# ===========================================================================
async def test_la_pantalla_de_inventario_dice_como_arreglar_un_negativo(
    cliente, sesion, semilla
):
    """Hasta ahora decía que un negativo «hay que revisar» y no cómo.

    Con los dos documentos construidos ya hay respuesta, y es distinta según el
    almacén: en un camión la da su liquidación, en una bodega el conteo.
    """
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _sembrar(sesion, semilla["bodega"], producto, "-5")
    texto = solo_texto(
        await cliente.get(
            f"/panel/inventario?almacen={semilla['bodega']}&filtro=negativos"
        )
    )
    assert "salida por conteo" in texto
    assert "entrada con motivo" in texto

    # En un camión, la respuesta es la liquidación y NO estas pantallas.
    await _sembrar(sesion, semilla["camion"], producto, "-3")
    texto_camion = solo_texto(
        await cliente.get(
            f"/panel/inventario?almacen={semilla['camion']}&filtro=negativos"
        )
    )
    assert "liquidación" in texto_camion
    assert "salida por conteo" not in texto_camion


async def test_entra_sale_y_la_cache_cuadra_con_el_libro_mayor(
    cliente, sesion, semilla
):
    """Entrada, conteo que encuentra menos, y las dos tablas siguen cuadrando.

    Es la prueba que cierra el ciclo: el libro mayor es append-only y la caché
    se escribe en la misma transacción, así que la suma de los asientos de un
    almacén tiene que ser exactamente su existencia — entre y salga lo que sea.
    """
    await _entrar(cliente)
    producto = await _producto(sesion, factor=24)

    # Entra: 10 cajas = 240 piezas.
    entrada = await cliente.post(
        "/panel/entradas/nueva",
        data={"csrf": _csrf(cliente), "motivo": "compra",
              "almacen_destino_id": str(semilla["bodega"]),
              "proveedor": "Abarrotes", "referencia": "F-1", "fecha": "", "nota": ""},
        follow_redirects=False,
    )
    entrada_id = uuid.UUID(
        entrada.headers["location"].split("/panel/entradas/")[1].split("?")[0]
    )
    await cliente.post(
        f"/panel/entradas/{entrada_id}/renglon",
        data={"csrf": _csrf(cliente), "producto": "COCA600",
              "unidad_codigo": "CAJA", "cantidad": "10", "lote": "", "caducidad": ""},
        follow_redirects=False,
    )
    await cliente.post(
        f"/panel/entradas/{entrada_id}/confirmar",
        data={"csrf": _csrf(cliente)}, follow_redirects=False,
    )
    assert await _existencia(sesion, semilla["bodega"], producto) == Decimal("240")

    # Sale: el conteo encuentra 200.
    salida = _id_de(await _abrir(cliente, semilla, tipo="conteo"))
    await _renglon(cliente, salida, cantidad="200")
    await _confirmar(cliente, salida)
    assert await _existencia(sesion, semilla["bodega"], producto) == Decimal("200")

    # Y la caché cuadra con el libro mayor: entradas − salidas.
    saldo = (
        await sesion.execute(
            text(
                """
                SELECT COALESCE(sum(
                         CASE WHEN almacen_destino_id = :a THEN cantidad
                              WHEN almacen_origen_id  = :a THEN -cantidad
                              ELSE 0 END), 0)
                  FROM movimientos_inventario
                 WHERE :a IN (almacen_origen_id, almacen_destino_id)
                """
            ),
            {"a": semilla["bodega"]},
        )
    ).scalar()
    assert Decimal(saldo) == Decimal("200")
