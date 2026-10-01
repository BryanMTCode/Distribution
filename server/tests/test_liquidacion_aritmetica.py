"""La aritmética del cierre del día, sin base de datos.

Los signos de esta ecuación son lo único que hay que acertar para que un cierre
signifique algo, y los tres se pueden equivocar sin que nada falle:

    esperado   = cargado − vendido − merma + devuelto
    diferencia = retornado − esperado

Que la columna generada de PostgreSQL dé lo mismo se prueba en
`tests/test_panel_liquidacion.py`, que sí necesita la base.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.liquidacion import RenglonDeLiquidacion, diferencia_de_efectivo


def test_el_devuelto_SUMA_al_esperado():
    """Una devolución de cliente entra al camión, así que debe volver a la bodega.

    Restarla haría aparecer un faltante del tamaño exacto de las devoluciones del
    día, y el vendedor pagaría por mercancía que devolvió bien. Es el signo que se
    escribe mal.
    """
    con = RenglonDeLiquidacion(
        cargado=Decimal("240"), vendido=Decimal("180"), merma=Decimal("0"),
        devuelto=Decimal("12"), retornado=Decimal("72"),
    )
    sin = RenglonDeLiquidacion(
        cargado=Decimal("240"), vendido=Decimal("180"), merma=Decimal("0"),
        devuelto=Decimal("0"), retornado=Decimal("72"),
    )
    assert con.esperado == Decimal("72.000")
    assert con.cuadra
    # Sin la devolución, esas mismas 72 piezas contadas se verían como sobrante.
    assert sin.esperado == Decimal("60.000")
    assert sin.diferencia == Decimal("12.000")


def test_la_merma_resta():
    r = RenglonDeLiquidacion(
        cargado=Decimal("240"), vendido=Decimal("180"), merma=Decimal("6"),
        devuelto=Decimal("0"), retornado=Decimal("54"),
    )
    assert r.esperado == Decimal("54.000")
    assert r.cuadra


def test_el_faltante_es_negativo_y_el_sobrante_positivo():
    base = {
        "cargado": Decimal("240"), "vendido": Decimal("180"),
        "merma": Decimal("0"), "devuelto": Decimal("0"),
    }
    falta = RenglonDeLiquidacion(**base, retornado=Decimal("54"))
    sobra = RenglonDeLiquidacion(**base, retornado=Decimal("66"))

    assert falta.diferencia == Decimal("-6.000")
    assert falta.es_faltante
    assert sobra.diferencia == Decimal("6.000")
    assert not sobra.es_faltante


def test_la_diferencia_de_efectivo_redondea_a_centavos():
    assert diferencia_de_efectivo(Decimal("2200.00"), Decimal("2250.00")) == Decimal("-50.00")
    assert diferencia_de_efectivo(Decimal("2250.00"), Decimal("2250.00")) == Decimal("0.00")


def test_la_cantidad_queda_a_TRES_decimales():
    """La columna es `numeric(14,3)`. Si el dominio diera más decimales, la
    comparación con la base fallaría por un redondeo y no por un descuadre real."""
    r = RenglonDeLiquidacion(
        cargado=Decimal("240"), vendido=Decimal("180.5"), merma=Decimal("0"),
        devuelto=Decimal("0"), retornado=Decimal("59.5"),
    )
    assert r.esperado == Decimal("59.500")
    assert r.diferencia == Decimal("0.000")
    assert r.cuadra
