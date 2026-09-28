"""La aritmética de una partida, en los tres lugares donde se calcula.

Estas pruebas cierran el quinto contrato. El importe de una línea lo calculan:

  1. El teléfono, con enteros (dsd_core/precio.dart).
  2. Este servidor, con `Decimal` (app/domain/importes.py).
  3. PostgreSQL, con `numeric`, en el CHECK de `venta_partidas`.

`contracts/importes_de_ejemplo.json` es el árbitro: lo genera Python, lo
consume Dart, y aquí se empuja **contra la base real** para que los tres queden
enfrentados en la misma suite. Si alguno se separa en un centavo, esta prueba lo
dice antes de que una venta legítima caiga en revisión por un redondeo.
"""

from __future__ import annotations

import json
import pathlib
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.canonico import PayloadNoCanonico, formatear_precio
from app.domain.importes import CantidadInvalida, cantidad_base, importe_de_linea

CONTRATO = (
    pathlib.Path(__file__).resolve().parents[2] / "contracts" / "importes_de_ejemplo.json"
)
CASOS = json.loads(CONTRATO.read_text(encoding="utf-8"))["casos"]


# ---------------------------------------------------------------------------
# El formato del precio
# ---------------------------------------------------------------------------


def test_el_precio_lleva_cuatro_decimales():
    # Escala distinta al dinero, y no por gusto: 296.00 / 24 = 12.3333 por
    # pieza. Con 2 decimales la caja completa valdría 295.92.
    assert formatear_precio("175") == "175.0000"
    assert formatear_precio(Decimal("12.3333")) == "12.3333"
    assert formatear_precio("0") == "0.0000"


def test_un_float_nunca_es_un_precio():
    with pytest.raises(PayloadNoCanonico):
        formatear_precio(12.3333)


# ---------------------------------------------------------------------------
# La regla, en Python
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("caso", CASOS, ids=[c["nombre"] for c in CASOS])
def test_el_importe_coincide_con_el_contrato(caso):
    calculado = importe_de_linea(
        Decimal(caso["cantidad"]), Decimal(caso["precio_unitario"])
    )
    assert f"{calculado:.2f}" == caso["importe"], caso["por_que"]


@pytest.mark.parametrize("caso", CASOS, ids=[c["nombre"] for c in CASOS])
def test_la_cantidad_base_coincide_con_el_contrato(caso):
    calculada = cantidad_base(Decimal(caso["cantidad"]), Decimal(caso["factor_unidad"]))
    assert f"{calculada:.3f}" == caso["cantidad_base"], caso["por_que"]


def test_el_medio_va_hacia_arriba_no_al_par():
    # El modo por omisión de Decimal es ROUND_HALF_EVEN: daría 0.12 aquí, y
    # PostgreSQL daría 0.13. Esta es la divergencia que el módulo existe para
    # cerrar, así que se prueba explícitamente y no solo vía el contrato.
    assert importe_de_linea(Decimal("1"), Decimal("0.1250")) == Decimal("0.13")
    assert importe_de_linea(Decimal("1"), Decimal("0.1350")) == Decimal("0.14")
    assert importe_de_linea(Decimal("1"), Decimal("0.1240")) == Decimal("0.12")


def test_se_redondea_una_sola_vez_al_final():
    # Si el precio se redondeara antes de multiplicar, esto daría 295.92.
    assert importe_de_linea(Decimal("24"), Decimal("12.3333")) == Decimal("296.00")


def test_un_float_no_entra_a_la_aritmetica():
    with pytest.raises(CantidadInvalida):
        importe_de_linea(2.0, Decimal("10"))
    with pytest.raises(CantidadInvalida):
        importe_de_linea(Decimal("2"), 10.0)


def test_una_cantidad_no_positiva_no_es_una_partida():
    # Una línea de cero no es una venta de cero: es una línea que no debió
    # existir. Se atrapa aquí, no en el ticket impreso.
    for mala in [Decimal("0"), Decimal("-1")]:
        with pytest.raises(CantidadInvalida):
            importe_de_linea(mala, Decimal("10"))


def test_un_precio_negativo_no_existe():
    with pytest.raises(CantidadInvalida):
        importe_de_linea(Decimal("1"), Decimal("-0.01"))


# ---------------------------------------------------------------------------
# La regla, en PostgreSQL
# ---------------------------------------------------------------------------


@pytest.fixture
async def para_vender(sesion: AsyncSession, semilla: dict) -> dict:
    """Lo mínimo de lo que cuelga una venta: dispositivo, cliente y producto.

    La `semilla` global trae vendedor, camión y ruta; falta el resto de la
    cadena de llaves foráneas que una partida exige.
    """
    ids = {
        "dispositivo": uuid.uuid4(),
        "cliente": uuid.uuid4(),
        "producto": uuid.uuid4(),
    }
    await sesion.execute(
        text(
            "INSERT INTO dispositivos(id, usuario_id, etiqueta, modelo) "
            "VALUES (:id, :usuario, :etiqueta, 'POCO M5s')"
        ),
        {
            "id": ids["dispositivo"],
            "etiqueta": f"POCO M5s — pruebas {uuid.uuid4().hex[:6]}",
            "usuario": semilla["vendedor"],
        },
    )
    await sesion.execute(
        text(
            "INSERT INTO clientes(id, codigo, nombre_comercial, ruta_id, "
            "lista_precios_id, creado_en, actualizado_en) "
            "VALUES (:id, :codigo, 'Abarrotes de prueba', :ruta, :lista, now(), now())"
        ),
        {
            "id": ids["cliente"],
            "codigo": f"CLI-{uuid.uuid4().hex[:8]}",
            "ruta": semilla["ruta"],
            "lista": semilla["lista_precios"],
        },
    )
    await sesion.execute(
        text(
            "INSERT INTO productos(id, sku, nombre, unidad_base) "
            "VALUES (:id, :sku, 'Sopa de fideo 70 g', 'PZA')"
        ),
        {"id": ids["producto"], "sku": f"SKU-{uuid.uuid4().hex[:8]}"},
    )
    await sesion.commit()
    return {**semilla, **ids}


async def _venta_minima(sesion: AsyncSession, ids: dict) -> uuid.UUID:
    """Una venta vacía a la que colgarle partidas."""
    venta_id = uuid.uuid4()
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, almacen_id, tipo, total,
                                lista_precios_id, lista_precios_version,
                                fecha_dispositivo, fecha_operativa)
            SELECT :id, :dispositivo, :folio, :folio_local, :cliente, :vendedor,
                   :almacen, 'contado', 0,
                   (SELECT id FROM listas_precios WHERE es_default), 1,
                   now(), CURRENT_DATE
            """
        ),
        {
            "id": venta_id,
            "dispositivo": ids["dispositivo"],
            "folio": int(uuid.uuid4().int % 1_000_000) + 1,
            "folio_local": f"PRUEBA-{uuid.uuid4().hex[:10]}",
            "cliente": ids["cliente"],
            "vendedor": ids["vendedor"],
            "almacen": ids["camion"],
        },
    )
    return venta_id


async def _insertar_partida(
    sesion: AsyncSession,
    venta_id: uuid.UUID,
    ids: dict,
    *,
    cantidad: str,
    precio: str,
    factor: str,
    importe: str,
    cantidad_base_: str,
    descuento: str = "0",
) -> None:
    await sesion.execute(
        text(
            """
            INSERT INTO venta_partidas (id, venta_id, linea, producto_id,
                                        unidad_codigo, factor_unidad, cantidad,
                                        cantidad_base, precio_unitario, descuento,
                                        importe)
            VALUES (:id, :venta, 1, :producto, 'PZA', :factor, :cantidad,
                    :cantidad_base, :precio, :descuento, :importe)
            """
        ),
        {
            "id": uuid.uuid4(),
            "venta": venta_id,
            "producto": ids["producto"],
            "factor": Decimal(factor),
            "cantidad": Decimal(cantidad),
            "cantidad_base": Decimal(cantidad_base_),
            "precio": Decimal(precio),
            "descuento": Decimal(descuento),
            "importe": Decimal(importe),
        },
    )


@pytest.mark.parametrize("caso", CASOS, ids=[c["nombre"] for c in CASOS])
async def test_postgresql_acepta_los_importes_del_contrato(
    sesion: AsyncSession, para_vender: dict, caso: dict
):
    """El tercer cálculo. Si el CHECK de PostgreSQL discrepara del contrato, la
    partida sería rechazada y la venta caería en cuarentena."""
    venta_id = await _venta_minima(sesion, para_vender)
    await _insertar_partida(
        sesion,
        venta_id,
        para_vender,
        cantidad=caso["cantidad"],
        precio=caso["precio_unitario"],
        factor=caso["factor_unidad"],
        importe=caso["importe"],
        cantidad_base_=caso["cantidad_base"],
    )
    await sesion.commit()


async def test_un_importe_que_no_cuadra_no_se_guarda(
    sesion: AsyncSession, para_vender: dict
):
    """`importe` distinto de `cantidad × precio` no describe ninguna venta
    posible: es un bug del cliente o un payload manipulado.

    No se rechaza la venta en silencio — el motor de sincronización la manda a
    `sync_cuarentena` con el payload íntegro (ver 0012_importes_rigidos.sql).
    Lo que no puede pasar es que entre a la contabilidad mal sumada.
    """
    venta_id = await _venta_minima(sesion, para_vender)
    with pytest.raises(IntegrityError) as error:
        await _insertar_partida(
            sesion,
            venta_id,
            para_vender,
            cantidad="5",
            precio="13.5000",
            factor="1",
            importe="60.00",  # debería ser 67.50
            cantidad_base_="5",
        )
        await sesion.commit()
    assert "partida_importe_coherente" in str(error.value)


async def test_un_descuento_sigue_cuadrando(sesion: AsyncSession, para_vender: dict):
    """El CHECK admite descuento porque el día que la OFICINA active una
    promoción, la aritmética tiene que seguir cuadrando.

    Lo que la regla de negocio prohíbe es que el VENDEDOR decida el precio, y
    eso se cierra en el teléfono: el carrito no tiene parámetro de precio ni de
    descuento. Un CHECK que exigiera `descuento = 0` rechazaría promociones
    legítimas, y eso sí violaría §0.1.
    """
    venta_id = await _venta_minima(sesion, para_vender)
    await _insertar_partida(
        sesion,
        venta_id,
        para_vender,
        cantidad="5",
        precio="13.5000",
        factor="1",
        descuento="7.50",
        importe="60.00",  # 67.50 - 7.50
        cantidad_base_="5",
    )
    await sesion.commit()


async def test_una_cantidad_base_que_no_cuadra_no_se_guarda(
    sesion: AsyncSession, para_vender: dict
):
    """Si la partida y el movimiento de inventario cuentan historias distintas,
    el descuadre aparece en la liquidación del final del día, que es donde ya no
    se puede investigar."""
    venta_id = await _venta_minima(sesion, para_vender)
    with pytest.raises(IntegrityError) as error:
        await _insertar_partida(
            sesion,
            venta_id,
            para_vender,
            cantidad="2",
            precio="296.0000",
            factor="24",
            importe="592.00",
            cantidad_base_="24",  # debería ser 48
        )
        await sesion.commit()
    assert "partida_cantidad_base_coherente" in str(error.value)


# La venta sin lista de precios NO se prueba aquí, porque ya no es un
# constraint: es un motivo de revisión. Ver la sección "LO QUE AQUÍ NO SE PUSO"
# en 0012_importes_rigidos.sql. La prueba vive con el manejador de `venta.crear`
# cuando ese manejador exista (Parte 2 del carrito).
