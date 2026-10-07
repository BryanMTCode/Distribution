"""Fase 7 · El cierre del día: el camión es un almacén rodante.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
Es la única pantalla que **compara** en vez de registrar, así que lo que se
prueba no es que guarde bien: es que la ecuación dé el número correcto y que el
inventario quede consistente después.

    esperado   = inicial + cargado − vendido − merma + devuelto
    diferencia = contado − esperado

Seis cosas que se rompen en silencio, y cada una le cuesta dinero a una persona:

1. **El saldo INICIAL.** La mercancía que no se vendió duerme arriba del camión y
   se acumula con la carga del día siguiente (dirección, octubre 2026). Sin ese
   término, todo lo que durmió arriba se le cobraba al vendedor como faltante,
   cada noche.
2. **El camión se queda con LO CONTADO**, no en cero. Y no baja nada a la bodega:
   ya no hay retorno diario.
3. **El signo del devuelto.** Una devolución de cliente ENTRA al camión, así que
   suma al esperado. Restarla haría aparecer un faltante del tamaño exacto de las
   devoluciones del día.
4. **Los renglones son los del camión, no los de la carga.** Un producto que lleva
   tres días arriba y hoy no se cargó tiene que contarse igual, o nadie notaría si
   desapareciera.
5. **El módulo de dominio y la columna generada de PostgreSQL** tienen que dar lo
   mismo, o el vendedor y la oficina discuten sobre dos números distintos.
6. **No se puede cerrar con operaciones pendientes.** Una venta que entra después
   del cierre convierte un sobrante en un cuadre, y el cierre ya lo dijo por
   escrito.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.domain.liquidacion import RenglonDeLiquidacion
from tests.conftest import PASSWORD_VENDEDOR, solo_texto, texto_plano

pytestmark = pytest.mark.asyncio


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
async def dia_de_trabajo(sesion, semilla) -> dict:
    return await sembrar_dia_de_trabajo(sesion, semilla)


async def sembrar_dia_de_trabajo(sesion, semilla) -> dict:
    """Un día completo: carga confirmada de 240 piezas y una venta de 180.

    Función aparte del fixture para que otros módulos la reusen
    (`test_cuenta_vendedor.py`) sin importar un fixture por su nombre.

    Montado con los mismos movimientos que escribe el panel, para que las
    existencias de partida sean las reales: 240 en el camión, 240 menos en bodega.
    """
    producto = uuid.uuid4()
    carga = uuid.uuid4()
    dispositivo = uuid.uuid4()
    hoy = date.today()

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
    await sesion.execute(
        text(
            "INSERT INTO existencias (almacen_id, producto_id, cantidad) "
            "VALUES (:b, :p, 760), (:c, :p, 240)"
        ),
        {"b": semilla["bodega"], "c": semilla["camion"], "p": producto},
    )
    await sesion.execute(
        text(
            """
            INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id,
                                vendedor_id, ruta_id, fecha_operativa, estado,
                                confirmada_en)
            VALUES (:id, 'CG-TEST', :b, :c, :v, :r, :d, 'confirmada', now())
            """
        ),
        {
            "id": carga,
            "b": semilla["bodega"],
            "c": semilla["camion"],
            "v": semilla["vendedor"],
            "r": semilla["ruta"],
            "d": hoy,
        },
    )
    await sesion.execute(
        text(
            "INSERT INTO carga_detalle (carga_id, producto_id, cantidad) "
            "VALUES (:c, :p, 240)"
        ),
        {"c": carga, "p": producto},
    )
    await sesion.execute(
        text(
            "INSERT INTO movimientos_inventario "
            "  (tipo, almacen_origen_id, almacen_destino_id, producto_id, cantidad, "
            "   documento_tipo, documento_id) "
            "VALUES ('carga', :b, :c, :p, 240, 'carga', :doc)"
        ),
        {"b": semilla["bodega"], "c": semilla["camion"], "p": producto, "doc": carga},
    )

    # El equipo, que ya sincronizó hoy: si no, el cierre se bloquea con razón.
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, "
            "                          ultima_sync_push_en) "
            "VALUES (:d, :u, 'POCO M5s de Juan', 'activo', now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )

    # Un cliente y una venta de contado por 180 piezas.
    cliente_id = uuid.uuid4()
    venta = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, "
            "                      creado_en, actualizado_en) "
            "VALUES (:c, 'CLI-L1', 'La Esquina', :r, now(), now())"
        ),
        {"c": cliente_id, "r": semilla["ruta"]},
    )
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, almacen_id, carga_id, tipo,
                                subtotal, total, fecha_dispositivo, fecha_operativa)
            VALUES (:v, :d, 1, 'VEND01-000001', :c, :u, :a, :carga, 'contado',
                    2250.00, 2250.00, now(), :dia)
            """
        ),
        {
            "v": venta,
            "d": dispositivo,
            "c": cliente_id,
            "u": semilla["vendedor"],
            "a": semilla["camion"],
            "carga": carga,
            "dia": hoy,
        },
    )
    await sesion.execute(
        text(
            """
            INSERT INTO venta_partidas (id, venta_id, linea, producto_id, unidad_codigo,
                                        factor_unidad, cantidad, cantidad_base,
                                        precio_unitario, importe)
            VALUES (:id, :v, 1, :p, 'PZA', 1, 180.000, 180.000, 12.5000, 2250.00)
            """
        ),
        {"id": uuid.uuid4(), "v": venta, "p": producto},
    )
    # La venta sale del camión, como la escribiría el manejador real.
    await sesion.execute(
        text(
            "INSERT INTO movimientos_inventario "
            "  (tipo, almacen_origen_id, producto_id, cantidad, documento_tipo, "
            "   documento_id) "
            "VALUES ('venta', :c, :p, 180, 'venta', :doc)"
        ),
        {"c": semilla["camion"], "p": producto, "doc": venta},
    )
    await sesion.execute(
        text(
            "UPDATE existencias SET cantidad = cantidad - 180 "
            " WHERE almacen_id = :c AND producto_id = :p"
        ),
        {"c": semilla["camion"], "p": producto},
    )
    await sesion.commit()

    return {
        "producto": producto,
        "carga": carga,
        "dispositivo": dispositivo,
        "venta": venta,
        "cliente": cliente_id,
        "dia": hoy,
    }


async def _abrir(cliente, carga_id) -> str:
    lista = await cliente.get("/panel/liquidaciones")
    r = await cliente.post(
        "/panel/liquidaciones/abrir",
        data={"csrf": _csrf(cliente, lista), "carga_id": str(carga_id)},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    assert "/panel/liquidaciones/" in r.headers["location"]
    return r.headers["location"].split("/panel/liquidaciones/")[1].split("?")[0]


async def _contar(
    cliente, liquidacion_id: str, sesion, cuanto: str, *, producto=None
):
    """Captura el conteo de UN renglón. `producto` cuando hay más de uno."""
    consulta = "SELECT id FROM liquidacion_detalle WHERE liquidacion_id = :l"
    parametros: dict = {"l": uuid.UUID(liquidacion_id)}
    if producto is not None:
        consulta += " AND producto_id = :p"
        parametros["p"] = producto
    renglon = (await sesion.execute(text(consulta), parametros)).scalar_one()
    detalle = await cliente.get(f"/panel/liquidaciones/{liquidacion_id}")
    return await cliente.post(
        f"/panel/liquidaciones/{liquidacion_id}/contar",
        data={"csrf": _csrf(cliente, detalle), f"contada_{renglon}": cuanto},
        follow_redirects=True,
    )


async def _contar_todos(cliente, liquidacion_id: str, sesion, conteos: dict):
    """Captura el conteo de VARIOS renglones en un solo POST.

    Tiene que ser un solo POST: el formulario trata un campo ausente como cero —al
    contar un camión, el producto que no se anotó es el que no está arriba—, así que
    mandar un renglón a la vez borraría el conteo del anterior.
    """
    renglones = (
        await sesion.execute(
            text(
                "SELECT id, producto_id FROM liquidacion_detalle "
                " WHERE liquidacion_id = :l"
            ),
            {"l": uuid.UUID(liquidacion_id)},
        )
    ).mappings().all()
    detalle = await cliente.get(f"/panel/liquidaciones/{liquidacion_id}")
    datos = {"csrf": _csrf(cliente, detalle)}
    for f in renglones:
        datos[f"contada_{f['id']}"] = conteos[f["producto_id"]]
    return await cliente.post(
        f"/panel/liquidaciones/{liquidacion_id}/contar",
        data=datos,
        follow_redirects=True,
    )


async def _cerrar(cliente, liquidacion_id: str):
    detalle = await cliente.get(f"/panel/liquidaciones/{liquidacion_id}")
    return await cliente.post(
        f"/panel/liquidaciones/{liquidacion_id}/cerrar",
        data={"csrf": _csrf(cliente, detalle), "confirmo_sincronizado": "1"},
        follow_redirects=True,
    )


# ---------------------------------------------------------------------------
# La ecuación, contra PostgreSQL
# ---------------------------------------------------------------------------
# Los signos de la ecuación se prueban sin base de datos en
# `tests/test_liquidacion_aritmetica.py`: son dominio puro. Aquí se comprueba lo
# que SÍ necesita la base — que la columna generada dé lo mismo.


async def test_LA_ECUACION_DE_PYTHON_Y_LA_DE_POSTGRESQL_DAN_LO_MISMO(
    cliente, semilla, dia_de_trabajo, sesion
):
    """`liquidacion_detalle.diferencia` es una columna GENERADA.

    Si la ecuación del módulo de dominio y la de la base divergieran, el vendedor y
    la oficina estarían discutiendo sobre dos números distintos, cada uno
    convencido de tener el del sistema. Esta prueba es la que lo impide.
    """
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])

    for contado in ("54", "60", "66", "0"):
        await _contar(cliente, liq, sesion, contado)
        fila = (
            await sesion.execute(
                text(
                    "SELECT cant_inicial, cant_cargada, cant_vendida, cant_merma, "
                    "       cant_devuelta, cant_contada, diferencia "
                    "  FROM liquidacion_detalle WHERE liquidacion_id = :l"
                ),
                {"l": uuid.UUID(liq)},
            )
        ).mappings().one()

        en_python = RenglonDeLiquidacion(
            inicial=Decimal(fila["cant_inicial"]),
            cargado=Decimal(fila["cant_cargada"]),
            vendido=Decimal(fila["cant_vendida"]),
            merma=Decimal(fila["cant_merma"]),
            devuelto=Decimal(fila["cant_devuelta"]),
            contado=Decimal(fila["cant_contada"]),
        ).diferencia
        assert en_python == Decimal(fila["diferencia"]), (
            f"con {contado} contadas, Python dice {en_python} y PostgreSQL "
            f"{fila['diferencia']}"
        )


# ---------------------------------------------------------------------------
# Abrir
# ---------------------------------------------------------------------------


async def test_abrir_trae_lo_cargado_y_lo_vendido_de_los_documentos(
    cliente, semilla, dia_de_trabajo, sesion
):
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])

    fila = (
        await sesion.execute(
            text(
                "SELECT cant_inicial, cant_cargada, cant_vendida, cant_contada "
                "  FROM liquidacion_detalle WHERE liquidacion_id = :l"
            ),
            {"l": uuid.UUID(liq)},
        )
    ).mappings().one()

    assert fila["cant_cargada"] == Decimal("240.000")
    assert fila["cant_vendida"] == Decimal("180.000")
    # Este camión amaneció vacío: 60 arriba − 240 cargadas + 180 vendidas = 0.
    assert fila["cant_inicial"] == Decimal("0.000")
    # El conteo nace en CERO, no en el esperado: prellenarlo haría que cerrar sin
    # contar diera cuadre perfecto, y el cierre no significaría nada.
    assert fila["cant_contada"] == Decimal("0.000")


async def test_el_efectivo_esperado_son_las_ventas_de_contado(
    cliente, semilla, dia_de_trabajo, sesion
):
    """El crédito no suma: no se cobró nada."""
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])

    esperado = (
        await sesion.execute(
            text("SELECT efectivo_esperado FROM liquidaciones WHERE id = :l"),
            {"l": uuid.UUID(liq)},
        )
    ).scalar_one()
    assert esperado == Decimal("2250.00")


async def test_abrir_dos_veces_lleva_a_la_misma_liquidacion(
    cliente, semilla, dia_de_trabajo
):
    """`uq_liquidacion_carga` lo impide, pero su error no le dice nada a nadie."""
    await _entrar(cliente)
    primera = await _abrir(cliente, dia_de_trabajo["carga"])
    segunda = await _abrir(cliente, dia_de_trabajo["carga"])
    assert primera == segunda


async def test_la_carga_pasa_a_en_ruta_al_abrir(cliente, semilla, dia_de_trabajo, sesion):
    """El camión ya salió y volvió. Es un estado que el teléfono aplica sin vaciar
    nada: vaciarlo aquí dejaría al vendedor sin inventario antes de contarlo."""
    await _entrar(cliente)
    await _abrir(cliente, dia_de_trabajo["carga"])

    estado = (
        await sesion.execute(
            text("SELECT estado FROM cargas WHERE id = :c"), {"c": dia_de_trabajo["carga"]}
        )
    ).scalar_one()
    assert estado == "en_ruta"


# ---------------------------------------------------------------------------
# Contar
# ---------------------------------------------------------------------------


async def test_un_campo_vacio_en_el_conteo_vale_CERO(
    cliente, semilla, dia_de_trabajo, sesion
):
    """Al contar un camión, el producto que no se anotó es el que no venía.

    Si significara "no lo cambies", un producto que se terminó quedaría con el
    conteo de un intento anterior y el faltante desaparecería sin que nadie lo
    decidiera.
    """
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "60")
    await _contar(cliente, liq, sesion, "")

    contado = (
        await sesion.execute(
            text("SELECT cant_contada FROM liquidacion_detalle WHERE liquidacion_id = :l"),
            {"l": uuid.UUID(liq)},
        )
    ).scalar_one()
    assert contado == Decimal("0.000")


async def test_el_conteo_no_acepta_negativos(cliente, semilla, dia_de_trabajo, sesion):
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    r = await _contar(cliente, liq, sesion, "-5")
    assert "no puede ser negativo" in r.text


# ---------------------------------------------------------------------------
# Cerrar: el ajuste, y la mercancía que se queda arriba
# ---------------------------------------------------------------------------


async def _existencias(sesion, producto) -> dict:
    return dict(
        (
            await sesion.execute(
                text(
                    "SELECT almacen_id, cantidad FROM existencias WHERE producto_id = :p"
                ),
                {"p": producto},
            )
        ).all()
    )


async def test_cerrar_cuadrado_DEJA_LA_MERCANCIA_ARRIBA_DEL_CAMION(
    cliente, semilla, dia_de_trabajo, sesion
):
    """240 cargadas − 180 vendidas = 60 esperadas, y se cuentan 60.

    El camión es un almacén rodante: esas 60 se quedan arriba para mañana. Antes
    bajaban a la bodega y el camión quedaba en cero, que es justo lo que hacía que
    al día siguiente el vendedor empezara con un inventario que no correspondía a
    lo que traía encima.
    """
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "60")
    r = await _cerrar(cliente, liq)

    assert "Cuadró producto por producto" in r.text
    assert "sin ajustes" in r.text

    existencias = await _existencias(sesion, dia_de_trabajo["producto"])
    assert existencias[semilla["camion"]] == Decimal("60.000")
    # Y la bodega no recibió nada: no bajó mercancía.
    assert existencias[semilla["bodega"]] == Decimal("760.000")

    # Ni un movimiento: cuando el conteo cuadra no pasó nada físico que registrar.
    movimientos = (
        await sesion.execute(
            text(
                "SELECT count(*) FROM movimientos_inventario "
                " WHERE tipo IN ('retorno', 'ajuste')"
            )
        )
    ).scalar_one()
    assert movimientos == 0


async def test_LO_QUE_DURMIO_EN_EL_CAMION_NO_ES_FALTANTE(
    cliente, semilla, dia_de_trabajo, sesion
):
    """La prueba de la decisión de octubre 2026, y la razón de la migración 0030.

    El camión amaneció con 40 piezas de días anteriores, le cargaron 240 y vendió
    180: debe haber 100 arriba. Se cuentan 100.

    Con la ecuación vieja —sin el término inicial— el esperado habría sido 60 y
    esas 40 piezas que el vendedor podía tocar aparecían como sobrante; y el cierre
    le habría dejado el camión en cero, cobrándole las 100 como faltante al día
    siguiente. Era mercancía fantasma: estaba arriba del camión y el sistema la
    declaraba perdida.
    """
    await sesion.execute(
        text(
            "UPDATE existencias SET cantidad = cantidad + 40 "
            " WHERE almacen_id = :a AND producto_id = :p"
        ),
        {"a": semilla["camion"], "p": dia_de_trabajo["producto"]},
    )
    await sesion.commit()

    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])

    fila = (
        await sesion.execute(
            text(
                "SELECT cant_inicial FROM liquidacion_detalle WHERE liquidacion_id = :l"
            ),
            {"l": uuid.UUID(liq)},
        )
    ).mappings().one()
    assert fila["cant_inicial"] == Decimal("40.000")

    await _contar(cliente, liq, sesion, "100")
    r = await _cerrar(cliente, liq)

    assert "Cuadró producto por producto" in r.text
    assert "faltante de" not in r.text

    existencias = await _existencias(sesion, dia_de_trabajo["producto"])
    assert existencias[semilla["camion"]] == Decimal("100.000")

    # Y ni un movimiento de ajuste: no había nada que cobrarle.
    ajustes = (
        await sesion.execute(
            text("SELECT count(*) FROM movimientos_inventario WHERE tipo = 'ajuste'")
        )
    ).scalar_one()
    assert ajustes == 0


async def test_un_producto_que_HOY_NO_SE_CARGO_se_cuenta_igual(
    cliente, semilla, dia_de_trabajo, sesion
):
    """El que lleva tres días arriba y hoy no vino en la carga.

    Si el cierre solo mirara `carga_detalle`, ese producto no tendría renglón:
    nadie lo contaría y nadie notaría si desapareciera del camión. Con el camión
    como almacén rodante ese caso es el normal, no la excepción.
    """
    viejo = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base) "
            "VALUES (:p, 'SOPA-70', 'Sopa de fideo 70 g', 'PZA')"
        ),
        {"p": viejo},
    )
    await sesion.execute(
        text(
            "INSERT INTO existencias (almacen_id, producto_id, cantidad) "
            "VALUES (:c, :p, 25)"
        ),
        {"c": semilla["camion"], "p": viejo},
    )
    await sesion.commit()

    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])

    renglones = (
        await sesion.execute(
            text(
                "SELECT producto_id, cant_inicial, cant_cargada "
                "  FROM liquidacion_detalle WHERE liquidacion_id = :l"
            ),
            {"l": uuid.UUID(liq)},
        )
    ).mappings().all()
    porProducto = {f["producto_id"]: f for f in renglones}
    assert viejo in porProducto, "el sobrante de días anteriores no entró al cierre"
    assert porProducto[viejo]["cant_inicial"] == Decimal("25.000")
    assert porProducto[viejo]["cant_cargada"] == Decimal("0.000")

    # Se cuentan 20 de las 25: faltan 5, y el camión se queda con 20.
    await _contar_todos(
        cliente, liq, sesion, {dia_de_trabajo["producto"]: "60", viejo: "20"}
    )
    r = await _cerrar(cliente, liq)

    assert "faltante de 5 unidades" in r.text
    existencias = await _existencias(sesion, viejo)
    assert existencias[semilla["camion"]] == Decimal("20.000")


async def test_UN_FALTANTE_SALE_DEL_SISTEMA_Y_EL_RESTO_SE_QUEDA(
    cliente, semilla, dia_de_trabajo, sesion
):
    """Se esperaban 60 y se cuentan 54: faltan 6.

    Las 6 salen del sistema —es la pérdida, y es lo que se le cobra—, y las 54 que
    sí están se quedan arriba del camión. Si el faltante se quedara como existencia,
    el cierre de mañana empezaría con un sobrante que nadie puso ahí.
    """
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "54")
    r = await _cerrar(cliente, liq)

    assert "1 producto(s) con diferencia" in r.text
    assert "faltante de 6 unidades" in r.text

    existencias = await _existencias(sesion, dia_de_trabajo["producto"])
    assert existencias[semilla["camion"]] == Decimal("54.000")
    # La bodega no recibió nada: la mercancía no bajó.
    assert existencias[semilla["bodega"]] == Decimal("760.000")

    ajuste = (
        await sesion.execute(
            text(
                "SELECT cantidad, almacen_origen_id, almacen_destino_id "
                "  FROM movimientos_inventario WHERE tipo = 'ajuste'"
            )
        )
    ).mappings().one()
    assert ajuste["cantidad"] == Decimal("6.000")
    assert ajuste["almacen_origen_id"] == semilla["camion"]
    assert ajuste["almacen_destino_id"] is None


async def test_un_sobrante_entra_al_camion(cliente, semilla, dia_de_trabajo, sesion):
    """Se cuentan 66 donde se esperaban 60: casi siempre es una venta sin sincronizar.

    El ajuste tiene que ENTRAR al camión. Si el signo estuviera al revés, el camión
    quedaría en 54 y mañana el vendedor vería menos de lo que trae.
    """
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "66")
    r = await _cerrar(cliente, liq)

    assert "sobrante de 6 unidades" in r.text

    existencias = await _existencias(sesion, dia_de_trabajo["producto"])
    assert existencias[semilla["camion"]] == Decimal("66.000")

    ajuste = (
        await sesion.execute(
            text(
                "SELECT cantidad, almacen_origen_id, almacen_destino_id "
                "  FROM movimientos_inventario WHERE tipo = 'ajuste'"
            )
        )
    ).mappings().one()
    assert ajuste["cantidad"] == Decimal("6.000")
    assert ajuste["almacen_origen_id"] is None
    assert ajuste["almacen_destino_id"] == semilla["camion"]


async def test_el_cierre_RECALCULA_antes_de_declarar(
    cliente, semilla, dia_de_trabajo, sesion
):
    """Una venta que entra entre abrir y cerrar no puede acabar cobrada como faltante.

    Es el mismo motivo por el que el arqueo recalcula el efectivo esperado, y aquí
    pesa más: lo que quedara viejo sería la cantidad de mercancía que se le cobra a
    una persona.
    """
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "40")

    # Llegan 20 piezas vendidas, tarde.
    otra = uuid.uuid4()
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, almacen_id, carga_id, tipo,
                                subtotal, total, fecha_dispositivo, fecha_operativa)
            VALUES (:v, :d, 9, 'VEND01-000009', :c, :u, :a, :carga, 'contado',
                    250.00, 250.00, now(), :dia)
            """
        ),
        {
            "v": otra,
            "d": dia_de_trabajo["dispositivo"],
            "c": dia_de_trabajo["cliente"],
            "u": semilla["vendedor"],
            "a": semilla["camion"],
            "carga": dia_de_trabajo["carga"],
            "dia": dia_de_trabajo["dia"],
        },
    )
    await sesion.execute(
        text(
            """
            INSERT INTO venta_partidas (id, venta_id, linea, producto_id, unidad_codigo,
                                        factor_unidad, cantidad, cantidad_base,
                                        precio_unitario, importe)
            VALUES (:id, :v, 1, :p, 'PZA', 1, 20.000, 20.000, 12.5000, 250.00)
            """
        ),
        {"id": uuid.uuid4(), "v": otra, "p": dia_de_trabajo["producto"]},
    )
    await sesion.execute(
        text(
            "UPDATE existencias SET cantidad = cantidad - 20 "
            " WHERE almacen_id = :c AND producto_id = :p"
        ),
        {"c": semilla["camion"], "p": dia_de_trabajo["producto"]},
    )
    await sesion.commit()

    await _cerrar(cliente, liq)

    fila = (
        await sesion.execute(
            text(
                "SELECT cant_vendida, diferencia FROM liquidacion_detalle "
                " WHERE liquidacion_id = :l"
            ),
            {"l": uuid.UUID(liq)},
        )
    ).mappings().one()
    assert fila["cant_vendida"] == Decimal("200.000"), "no recalculó lo vendido"
    # 240 cargadas − 200 vendidas = 40 esperadas, y se contaron 40: cuadra.
    assert fila["diferencia"] == Decimal("0.000")

    existencias = await _existencias(sesion, dia_de_trabajo["producto"])
    assert existencias[semilla["camion"]] == Decimal("40.000")


async def test_CERRAR_LE_LLEVA_AL_TELEFONO_EL_AJUSTE_NO_EL_VACIADO(
    cliente, semilla, dia_de_trabajo, sesion
):
    """La carga pasa a `liquidada`, y ese UPDATE publica el delta del cierre.

    Antes ese delta era la orden de **borrar** `existencias_camion`: el teléfono
    amanecía en ceros. Ahora lleva el AJUSTE que la oficina escribió al comparar el
    conteo contra el saldo del sistema, y el teléfono se lo SUMA a lo que tenga.

    Se publica la diferencia y no el conteo a propósito: la oficina puede liquidar
    lo de ayer a media mañana, con la carga de hoy ya encima del camión. Un conteo
    de ayer aplicado como «el camión tiene esto» borraría la carga de hoy y las
    ventas de la mañana. Una diferencia sigue siendo correcta cuando llega tarde.
    """
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "54")
    await _cerrar(cliente, liq)

    estado = (
        await sesion.execute(
            text("SELECT estado FROM cargas WHERE id = :c"), {"c": dia_de_trabajo["carga"]}
        )
    ).scalar_one()
    assert estado == "liquidada"

    delta = (
        await sesion.execute(
            text(
                "SELECT payload, vendedor_id FROM change_log "
                " WHERE entidad = 'carga' AND entidad_id = :c "
                " ORDER BY cursor DESC LIMIT 1"
            ),
            {"c": dia_de_trabajo["carga"]},
        )
    ).mappings().one()
    assert delta["payload"]["estado"] == "liquidada"
    # Acotado al vendedor: la carga solo le importa a su equipo.
    assert delta["vendedor_id"] == semilla["vendedor"]

    ajustes = delta["payload"]["ajustes"]
    assert len(ajustes) == 1
    assert ajustes[0]["producto_id"] == str(dia_de_trabajo["producto"])
    # Negativo: al teléfono le sobran 6 piezas que no están en el camión.
    assert Decimal(ajustes[0]["cantidad"]) == Decimal("-6.000")


async def test_un_cierre_que_cuadra_no_publica_ajustes(
    cliente, semilla, dia_de_trabajo, sesion
):
    """El caso normal. Publicar ceros sería ruido que el teléfono tendría que
    ignorar, y cada renglón de ruido es una oportunidad de aplicarlo mal."""
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "60")
    await _cerrar(cliente, liq)

    delta = (
        await sesion.execute(
            text(
                "SELECT payload FROM change_log "
                " WHERE entidad = 'carga' AND entidad_id = :c "
                " ORDER BY cursor DESC LIMIT 1"
            ),
            {"c": dia_de_trabajo["carga"]},
        )
    ).mappings().one()
    assert delta["payload"]["ajustes"] == []


async def test_cerrar_dos_veces_no_duplica_el_ajuste(
    cliente, semilla, dia_de_trabajo, sesion
):
    """Un doble clic cobraría el faltante dos veces."""
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "54")
    await _cerrar(cliente, liq)

    segunda = await _cerrar(cliente, liq)
    assert "Ya estaba cerrada" in segunda.text

    ajustes = (
        await sesion.execute(
            text("SELECT count(*) FROM movimientos_inventario WHERE tipo = 'ajuste'")
        )
    ).scalar_one()
    assert ajustes == 1
    existencias = await _existencias(sesion, dia_de_trabajo["producto"])
    assert existencias[semilla["camion"]] == Decimal("54.000")


async def test_el_libro_mayor_del_cierre_no_se_puede_editar(
    cliente, semilla, dia_de_trabajo, sesion
):
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "54")
    await _cerrar(cliente, liq)

    with pytest.raises(Exception, match="append-only"):
        await sesion.execute(
            text("DELETE FROM movimientos_inventario WHERE tipo = 'ajuste'")
        )
    await sesion.rollback()


# ---------------------------------------------------------------------------
# Lo que bloquea el cierre
# ---------------------------------------------------------------------------


async def test_NO_SE_CIERRA_CON_SOBRES_EN_CUARENTENA(
    cliente, semilla, dia_de_trabajo, sesion
):
    """Cada sobre en cuarentena es una operación que NO entró, y puede ser justo la
    venta que explica el sobrante.

    Cerrar con cuarentena pendiente es firmar un número que todavía puede cambiar.
    """
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "60")

    await sesion.execute(
        text(
            """
            INSERT INTO sync_cuarentena
              (operacion_id, dispositivo_id, usuario_id, tipo, payload, hash_payload,
               error_codigo, error_mensaje)
            VALUES (:op, :d, :u, 'venta.crear', '{}'::jsonb,
                    'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2',
                    'payload_invalido', 'el total no cuadra')
            """
        ),
        {
            "op": uuid.uuid4(),
            "d": dia_de_trabajo["dispositivo"],
            "u": semilla["vendedor"],
        },
    )
    await sesion.commit()

    r = await _cerrar(cliente, liq)
    assert "en cuarentena" in r.text

    estado = (
        await sesion.execute(
            text("SELECT estado FROM liquidaciones WHERE id = :l"), {"l": uuid.UUID(liq)}
        )
    ).scalar_one()
    assert estado != "cerrada"
    # Y no se movió nada de inventario.
    movimientos = (
        await sesion.execute(
            text(
                "SELECT count(*) FROM movimientos_inventario "
                " WHERE tipo IN ('retorno', 'ajuste')"
            )
        )
    ).scalar_one()
    assert movimientos == 0


async def test_no_se_cierra_si_el_equipo_no_ha_sincronizado(
    cliente, semilla, dia_de_trabajo, sesion
):
    """Si el equipo no empujó nada desde el día de la carga, con seguridad hay
    operaciones del día que el servidor no tiene."""
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "60")

    await sesion.execute(
        text("UPDATE dispositivos SET ultima_sync_push_en = NULL WHERE id = :d"),
        {"d": dia_de_trabajo["dispositivo"]},
    )
    await sesion.commit()

    r = await _cerrar(cliente, liq)
    assert "no han sincronizado" in r.text


async def _reportar_cola(sesion, dispositivo, pendientes, *, dias_atras=0):
    """Simula lo que el teléfono manda en su push: cuántos sobres le quedan.

    `dias_atras` sirve para el caso que importa: un cero viejo. Un equipo que
    reportó cero anteayer pudo levantar veinte ventas desde entonces, así que ese
    dato no respalda el cierre de hoy (§0.3).
    """
    await sesion.execute(
        text(
            "UPDATE dispositivos "
            "   SET cola_pendiente = :n, "
            "       cola_reportada_en = now() - make_interval(days => :dias) "
            " WHERE id = :d"
        ),
        {"n": pendientes, "dias": dias_atras, "d": dispositivo},
    )
    await sesion.commit()


async def _leer_cierre(sesion, liq) -> dict:
    return dict(
        (
            await sesion.execute(
                text(
                    "SELECT estado, sync_completa, operaciones_pendientes "
                    "  FROM liquidaciones WHERE id = :l"
                ),
                {"l": uuid.UUID(liq)},
            )
        ).mappings().one()
    )


async def test_una_cola_reportada_bloquea_el_cierre(
    cliente, semilla, dia_de_trabajo, sesion
):
    """Cuando el teléfono dice que le quedan operaciones, eso no es una sospecha.

    Es el único dato del sistema que solo el teléfono puede dar, y cada sobre que le
    queda puede ser la venta que explica el sobrante que este cierre está por
    declarar por escrito.
    """
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "60")
    await _reportar_cola(sesion, dia_de_trabajo["dispositivo"], 4)

    r = await _cerrar(cliente, liq)
    assert "reportó 4 operación(es) sin subir" in texto_plano(r)
    assert (await _leer_cierre(sesion, liq))["estado"] != "cerrada"

    # Y la pantalla NO puede decir al mismo tiempo que el equipo está al día: dos
    # afirmaciones contrarias en la misma vista cuestan más que el problema que
    # explican.
    plano = texto_plano(await cliente.get(f"/panel/liquidaciones/{liq}"))
    assert "reportó 4 operación(es) sin subir" in plano
    assert "El equipo reportó que no le queda nada por subir" not in plano


async def test_con_cero_reportado_hoy_ya_no_se_pide_la_casilla(
    cliente, semilla, dia_de_trabajo, sesion
):
    """El dato sustituye a la confirmación, que es el punto de todo esto.

    Pedirle a una persona que jure algo que el sistema ya sabe es pedirle que
    respalde con su nombre un dato que no produjo.
    """
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "60")
    await _reportar_cola(sesion, dia_de_trabajo["dispositivo"], 0)

    detalle = await cliente.get(f"/panel/liquidaciones/{liq}")
    plano = texto_plano(detalle)
    assert "El equipo reportó que no le queda nada por subir" in plano
    assert "Confirmo que el teléfono terminó de sincronizar" not in plano

    # Y cierra SIN mandar la casilla.
    r = await cliente.post(
        f"/panel/liquidaciones/{liq}/cerrar",
        data={"csrf": _csrf(cliente, detalle)},
        follow_redirects=True,
    )
    assert "Confirma que el teléfono" not in r.text

    fila = await _leer_cierre(sesion, liq)
    assert fila["estado"] == "cerrada"
    assert fila["sync_completa"] is True


async def test_un_cero_de_anteayer_no_respalda_el_cierre_de_hoy(
    cliente, semilla, dia_de_trabajo, sesion
):
    """§0.3 otra vez: el dato vale por su hora, no solo por su valor.

    El equipo reportó cero hace dos días y desde entonces pudo levantar veinte
    ventas. Si ese cero bastara, el respaldo diría "estaba al día" sobre un equipo
    que lleva dos días sin hablar.
    """
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "60")
    await _reportar_cola(sesion, dia_de_trabajo["dispositivo"], 0, dias_atras=2)

    detalle = await cliente.get(f"/panel/liquidaciones/{liq}")
    plano = texto_plano(detalle)
    assert "Confirmo que el teléfono terminó de sincronizar" in plano
    assert "antes del día de la carga" in plano


async def test_cerrar_sin_respaldo_exige_la_casilla_y_lo_deja_asentado(
    cliente, semilla, dia_de_trabajo, sesion
):
    """Un equipo que nunca reportó su cola —app vieja— no bloquea el cierre.

    Cerrar el día no puede quedar atorado por una actualización pendiente. Pero
    `sync_completa` queda en **false**, que es lo que de verdad pasó: al revisar
    después un sobrante, lo primero que se pregunta es si el equipo estaba al día, y
    un `true` sin respaldo contesta esa pregunta con una afirmación disfrazada de
    hecho.
    """
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "60")

    detalle = await cliente.get(f"/panel/liquidaciones/{liq}")
    r = await cliente.post(
        f"/panel/liquidaciones/{liq}/cerrar",
        data={"csrf": _csrf(cliente, detalle)},
        follow_redirects=True,
    )
    assert "Confirma que el teléfono terminó de sincronizar" in r.text
    assert "nunca ha reportado su cola" in texto_plano(r)

    await _cerrar(cliente, liq)
    fila = await _leer_cierre(sesion, liq)
    assert fila["estado"] == "cerrada"
    assert fila["sync_completa"] is False
    assert fila["operaciones_pendientes"] == 0


async def test_cerrar_encola_el_recalculo_del_laboratorio(
    cliente, semilla, dia_de_trabajo, sesion
):
    """Al cerrar, las cifras del día quedan firmes: es el momento de recalcular.

    Antes de esto, el laboratorio mostraba siempre un día a medio sincronizar y
    no había nada que avisara cuándo dejaba de estarlo.
    """
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "60")
    await _cerrar(cliente, liq)

    job = (
        await sesion.execute(
            text(
                "SELECT tipo, payload, clave_unica, estado FROM jobs "
                " WHERE tipo = 'refrescar_analitica'"
            )
        )
    ).mappings().all()
    assert len(job) == 1
    assert job[0]["estado"] == "pendiente"
    assert job[0]["payload"]["motivo"] == "liquidacion_cerrada"


async def test_cerrar_cinco_rutas_encola_un_solo_recalculo(
    cliente, semilla, dia_de_trabajo, sesion
):
    """La `clave_unica` por día es lo que lo hace idempotente.

    Sin ella, una oficina con ocho rutas encolaría ocho refrescos completos del
    esquema estrella al final del día: el worker haría ocho veces el mismo
    trabajo y el último taparía al primero.
    """
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "60")
    await _cerrar(cliente, liq)

    # Se simula el cierre de otra ruta del MISMO día encolando con la misma clave,
    # que es exactamente lo que haría el segundo cierre.
    from app.workers.cola import encolar

    segundo = await encolar(
        sesion,
        "refrescar_analitica",
        {"motivo": "liquidacion_cerrada"},
        clave_unica=f"refrescar_analitica:{dia_de_trabajo['dia']}",
    )
    await sesion.commit()
    assert segundo is None, "la segunda vez debe ser un no-op, no un job nuevo"

    cuantos = (
        await sesion.execute(
            text("SELECT count(*) FROM jobs WHERE tipo = 'refrescar_analitica'")
        )
    ).scalar_one()
    assert cuantos == 1


async def test_el_cierre_dice_sobre_que_descansa(
    cliente, semilla, dia_de_trabajo, sesion
):
    """Al abrir una liquidación cerrada hay que poder saber en qué se apoyó."""
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "60")
    await _cerrar(cliente, liq)

    plano = texto_plano(await cliente.get(f"/panel/liquidaciones/{liq}"))
    assert "sin respaldo de sincronización" in plano


# ---------------------------------------------------------------------------
# Arqueo de efectivo
# ---------------------------------------------------------------------------


async def test_el_arqueo_dice_cuanto_falta_en_la_bolsa(
    cliente, semilla, dia_de_trabajo, sesion
):
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    detalle = await cliente.get(f"/panel/liquidaciones/{liq}")

    r = await cliente.post(
        f"/panel/liquidaciones/{liq}/efectivo",
        data={
            "csrf": _csrf(cliente, detalle),
            "efectivo_entregado": "2,200.00",
            "observaciones": "dice que le faltó un cambio",
        },
        follow_redirects=True,
    )
    assert "Faltan $50.00" in r.text

    fila = (
        await sesion.execute(
            text(
                "SELECT efectivo_entregado, diferencia_efectivo, observaciones "
                "  FROM liquidaciones WHERE id = :l"
            ),
            {"l": uuid.UUID(liq)},
        )
    ).mappings().one()
    assert fila["efectivo_entregado"] == Decimal("2200.00")
    # Columna generada en PostgreSQL: entregado − esperado.
    assert fila["diferencia_efectivo"] == Decimal("-50.00")
    assert fila["observaciones"] == "dice que le faltó un cambio"


async def test_el_esperado_se_recalcula_al_guardar_el_arqueo(
    cliente, semilla, dia_de_trabajo, sesion
):
    """Entre abrir y cerrar pueden entrar ventas que el teléfono sincronizó tarde.

    Usar el esperado guardado al abrir haría aparecer un faltante de efectivo del
    tamaño exacto de lo que llegó en medio.
    """
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])

    # Llega otra venta de contado, tarde.
    otra = uuid.uuid4()
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, almacen_id, carga_id, tipo,
                                subtotal, total, fecha_dispositivo, fecha_operativa)
            VALUES (:v, :d, 2, 'VEND01-000002', :c, :u, :a, :carga, 'contado',
                    500.00, 500.00, now(), :dia)
            """
        ),
        {
            "v": otra,
            "d": dia_de_trabajo["dispositivo"],
            "c": dia_de_trabajo["cliente"],
            "u": semilla["vendedor"],
            "a": semilla["camion"],
            "carga": dia_de_trabajo["carga"],
            "dia": dia_de_trabajo["dia"],
        },
    )
    await sesion.commit()

    detalle = await cliente.get(f"/panel/liquidaciones/{liq}")
    await cliente.post(
        f"/panel/liquidaciones/{liq}/efectivo",
        data={"csrf": _csrf(cliente, detalle), "efectivo_entregado": "2750.00"},
        follow_redirects=True,
    )

    fila = (
        await sesion.execute(
            text(
                "SELECT efectivo_esperado, diferencia_efectivo "
                "  FROM liquidaciones WHERE id = :l"
            ),
            {"l": uuid.UUID(liq)},
        )
    ).mappings().one()
    assert fila["efectivo_esperado"] == Decimal("2750.00")
    assert fila["diferencia_efectivo"] == Decimal("0.00")


# ---------------------------------------------------------------------------
# Acceso
# ---------------------------------------------------------------------------


async def test_el_gerente_tambien_cierra_el_dia(cliente, semilla, dia_de_trabajo, sesion):
    """Antes: «gerencia monitorea, no cierra». Desde la migración 0045 el gerente
    tiene `inventario.liquidar`: el corte lo hacen los puestos de arriba —también
    desde la app— y el vendedor no."""
    from app.core.seguridad import hashear_password

    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "60")
    await cliente.get("/panel/salir")

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
    lectura = await cliente.get(f"/panel/liquidaciones/{liq}")
    assert "Cerrar la liquidación" in lectura.text

    await cliente.post(
        f"/panel/liquidaciones/{liq}/cerrar",
        data={"csrf": _csrf(cliente, lectura), "confirmo_sincronizado": "1"},
    )
    assert (await _leer_cierre(sesion, liq))["estado"] == "cerrada"


async def test_cerrar_exige_el_token_csrf(cliente, semilla, dia_de_trabajo, sesion):
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "60")

    r = await cliente.post(
        f"/panel/liquidaciones/{liq}/cerrar",
        data={"csrf": "inventado", "confirmo_sincronizado": "1"},
    )
    assert r.status_code == 403


async def test_una_venta_SIN_CARGA_cuenta_en_el_cierre_del_dia(
    cliente, semilla, dia_de_trabajo, sesion
):
    """El vendedor que sale a vender lo que le quedó, sin carga nueva.

    Con el camión rodante eso dejó de ser raro: puede salir con el sobrante, o
    vender después de que la oficina liquidó la carga de ayer. Esas ventas llevan
    `carga_id` nulo. Si el cierre no las contara, el renglón diría que el camión
    amaneció con menos de lo que amaneció, y el papel que firma el vendedor
    explicaría su día con un número falso.

    La diferencia no cambia —el esperado siempre acaba siendo el saldo vivo del
    camión, ver `saldo_inicial`—, pero las columnas que la explican sí, y son las
    que alguien lee seis meses después para entender un faltante.
    """
    huerfana = uuid.uuid4()
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, almacen_id, carga_id, tipo,
                                subtotal, total, fecha_dispositivo, fecha_operativa)
            VALUES (:v, :d, 7, 'VEND01-000007', :c, :u, :a, NULL, 'contado',
                    125.00, 125.00, now(), :dia)
            """
        ),
        {
            "v": huerfana,
            "d": dia_de_trabajo["dispositivo"],
            "c": dia_de_trabajo["cliente"],
            "u": semilla["vendedor"],
            "a": semilla["camion"],
            "dia": dia_de_trabajo["dia"],
        },
    )
    await sesion.execute(
        text(
            """
            INSERT INTO venta_partidas (id, venta_id, linea, producto_id, unidad_codigo,
                                        factor_unidad, cantidad, cantidad_base,
                                        precio_unitario, importe)
            VALUES (:id, :v, 1, :p, 'PZA', 1, 10.000, 10.000, 12.5000, 125.00)
            """
        ),
        {"id": uuid.uuid4(), "v": huerfana, "p": dia_de_trabajo["producto"]},
    )
    await sesion.execute(
        text(
            "UPDATE existencias SET cantidad = cantidad - 10 "
            " WHERE almacen_id = :c AND producto_id = :p"
        ),
        {"c": semilla["camion"], "p": dia_de_trabajo["producto"]},
    )
    await sesion.commit()

    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])

    fila = (
        await sesion.execute(
            text(
                "SELECT cant_inicial, cant_vendida FROM liquidacion_detalle "
                " WHERE liquidacion_id = :l"
            ),
            {"l": uuid.UUID(liq)},
        )
    ).mappings().one()
    assert fila["cant_vendida"] == Decimal("190.000"), "no contó la venta sin carga"
    # Y el inicial sigue siendo cero: ese camión amaneció vacío de verdad.
    assert fila["cant_inicial"] == Decimal("0.000")


# ===========================================================================
# El defecto de octubre: «vendo en la app y el camión del panel nunca baja»
# ===========================================================================
async def _venta_tardia(sesion, semilla, dia_de_trabajo, *, piezas, carga, folio=9):
    """Una venta que sincroniza DESPUÉS de abrir la liquidación, con su salida."""
    otra = uuid.uuid4()
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, almacen_id, carga_id, tipo,
                                subtotal, total, fecha_dispositivo, fecha_operativa)
            VALUES (:v, :d, :f, :fl, :c, :u, :a, :carga, 'contado',
                    :total, :total, now(), :dia)
            """
        ),
        {
            "v": otra,
            "d": dia_de_trabajo["dispositivo"],
            "f": folio,
            "fl": f"VEND01-{folio:06d}",
            "c": dia_de_trabajo["cliente"],
            "u": semilla["vendedor"],
            "a": semilla["camion"],
            "carga": carga,
            "total": Decimal(piezas) * Decimal("12.50"),
            "dia": dia_de_trabajo["dia"],
        },
    )
    await sesion.execute(
        text(
            """
            INSERT INTO venta_partidas (id, venta_id, linea, producto_id, unidad_codigo,
                                        factor_unidad, cantidad, cantidad_base,
                                        precio_unitario, importe)
            VALUES (:id, :v, 1, :p, 'PZA', 1, :n, :n, 12.5000, :importe)
            """
        ),
        {
            "id": uuid.uuid4(),
            "v": otra,
            "p": dia_de_trabajo["producto"],
            "n": piezas,
            "importe": Decimal(piezas) * Decimal("12.50"),
        },
    )
    await sesion.execute(
        text(
            "UPDATE existencias SET cantidad = cantidad - :n "
            " WHERE almacen_id = :c AND producto_id = :p"
        ),
        {"n": piezas, "c": semilla["camion"], "p": dia_de_trabajo["producto"]},
    )
    await sesion.commit()


async def _renglon(sesion, liq) -> dict:
    return dict(
        (
            await sesion.execute(
                text(
                    "SELECT cant_vendida, cant_contada, diferencia "
                    "  FROM liquidacion_detalle WHERE liquidacion_id = :l"
                ),
                {"l": uuid.UUID(liq)},
            )
        ).mappings().one()
    )


async def test_LA_PANTALLA_ABIERTA_MUESTRA_LA_VENTA_QUE_LLEGO_DESPUES(
    cliente, semilla, dia_de_trabajo, sesion
):
    """El reporte de la dirección, octubre 2026, reproducido tal cual.

    La liquidación se abre en la mañana; el teléfono sincroniza una venta a
    mediodía; la pantalla donde se cuenta el camión tiene que decir lo que el
    camión trae AHORA. Antes guardaba una foto al abrir —«un primer borrador»— y
    solo recalculaba al cerrar: quien contaba lo hacía contra el camión de la
    mañana, y el cierre cobraba otra cosa.
    """
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    antes = await cliente.get(f"/panel/liquidaciones/{liq}")
    assert antes.status_code == 200

    async def efectivo_esperado():
        return (
            await sesion.execute(
                text("SELECT efectivo_esperado FROM liquidaciones WHERE id = :l"),
                {"l": uuid.UUID(liq)},
            )
        ).scalar_one()

    efectivo_de_la_manana = await efectivo_esperado()

    await _venta_tardia(sesion, semilla, dia_de_trabajo, piezas=20, carga=dia_de_trabajo["carga"])

    plano = solo_texto(await cliente.get(f"/panel/liquidaciones/{liq}"))
    renglon = await _renglon(sesion, liq)
    # 180 de la mañana + 20 de mediodía.
    assert renglon["cant_vendida"] == Decimal("200.000")
    # 240 cargadas − 200 vendidas: el camión trae 40, y eso dice la pantalla.
    assert "40" in plano
    # Y el efectivo esperado también se movió: 20 piezas a $12.50 de contado.
    assert await efectivo_esperado() == efectivo_de_la_manana + Decimal("250.00")


async def test_refrescar_la_pantalla_NO_BORRA_LO_YA_CONTADO(
    cliente, semilla, dia_de_trabajo, sesion
):
    """Lo único que esta pantalla no puede calcular es lo que una persona contó.

    Recalcular lo vendido no puede tocarlo: alguien contó 40 cajas, llegó una venta
    tardía y recargó la página — su conteo sigue ahí.
    """
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "40")

    await _venta_tardia(sesion, semilla, dia_de_trabajo, piezas=20, carga=dia_de_trabajo["carga"])
    await cliente.get(f"/panel/liquidaciones/{liq}")

    renglon = await _renglon(sesion, liq)
    assert renglon["cant_contada"] == Decimal("40.000")
    assert renglon["cant_vendida"] == Decimal("200.000")
    # 40 contadas contra 40 esperadas.
    assert renglon["diferencia"] == Decimal("0.000")


async def test_una_venta_con_la_carga_de_AYER_cuenta_en_el_corte_de_HOY(
    cliente, semilla, dia_de_trabajo, sesion
):
    """El camión rodante: el vendedor sale a vender lo que le sobró antes de que su
    teléfono reciba la carga de hoy, así que la venta lleva la carga de ayer.

    Se busca por vendedor, camión y DÍA —como las mermas—, no por `carga_id`. Antes
    esa venta no contaba en ningún corte abierto: «vendido» decía de menos y
    `inicial` lo absorbía en silencio.
    """
    ayer = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id, "
            "                    vendedor_id, fecha_operativa, estado) "
            "VALUES (:c, 'CG-AYER', :b, :cam, :v, :d, 'liquidada')"
        ),
        {
            "c": ayer,
            "b": semilla["bodega"],
            "cam": semilla["camion"],
            "v": semilla["vendedor"],
            "d": dia_de_trabajo["dia"] - timedelta(days=1),
        },
    )
    await sesion.commit()

    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _venta_tardia(sesion, semilla, dia_de_trabajo, piezas=20, carga=ayer)
    await cliente.get(f"/panel/liquidaciones/{liq}")

    assert (await _renglon(sesion, liq))["cant_vendida"] == Decimal("200.000")
