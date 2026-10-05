"""La aritmética del cierre del día, sin base de datos.

Los signos de esta ecuación son lo único que hay que acertar para que un cierre
signifique algo, y cada uno se puede equivocar sin que nada falle:

    esperado   = inicial + cargado − vendido − merma + devuelto
    diferencia = contado − esperado

Que la columna generada de PostgreSQL dé lo mismo se prueba en
`tests/test_panel_liquidacion.py`, que sí necesita la base.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.liquidacion import (
    RenglonDeLiquidacion,
    diferencia_de_efectivo,
    saldo_inicial,
)


def test_el_saldo_INICIAL_suma_al_esperado():
    """El camión es un almacén rodante: amanece con lo que no se vendió ayer.

    Es el término que faltaba, y el que decidía cuánto se le cobraba a una
    persona: sin él, todo lo que durmió arriba del camión aparecía como faltante.
    Decisión de la dirección, octubre 2026.
    """
    con = RenglonDeLiquidacion(
        inicial=Decimal("60"), cargado=Decimal("240"), vendido=Decimal("180"),
        merma=Decimal("0"), devuelto=Decimal("0"), contado=Decimal("120"),
    )
    sin = RenglonDeLiquidacion(
        inicial=Decimal("0"), cargado=Decimal("240"), vendido=Decimal("180"),
        merma=Decimal("0"), devuelto=Decimal("0"), contado=Decimal("120"),
    )
    assert con.esperado == Decimal("120.000")
    assert con.cuadra
    # Sin el inicial, esas 60 piezas que traía desde antes se le cobran.
    assert sin.diferencia == Decimal("60.000")


def test_el_devuelto_SUMA_al_esperado():
    """Una devolución de cliente entra al camión, así que sigue arriba al contarlo.

    Restarla haría aparecer un faltante del tamaño exacto de las devoluciones del
    día, y el vendedor pagaría por mercancía que devolvió bien. Es el signo que se
    escribe mal.
    """
    con = RenglonDeLiquidacion(
        cargado=Decimal("240"), vendido=Decimal("180"), merma=Decimal("0"),
        devuelto=Decimal("12"), contado=Decimal("72"),
    )
    sin = RenglonDeLiquidacion(
        cargado=Decimal("240"), vendido=Decimal("180"), merma=Decimal("0"),
        devuelto=Decimal("0"), contado=Decimal("72"),
    )
    assert con.esperado == Decimal("72.000")
    assert con.cuadra
    # Sin la devolución, esas mismas 72 piezas contadas se verían como sobrante.
    assert sin.esperado == Decimal("60.000")
    assert sin.diferencia == Decimal("12.000")


def test_la_merma_resta():
    r = RenglonDeLiquidacion(
        cargado=Decimal("240"), vendido=Decimal("180"), merma=Decimal("6"),
        devuelto=Decimal("0"), contado=Decimal("54"),
    )
    assert r.esperado == Decimal("54.000")
    assert r.cuadra


def test_el_faltante_es_negativo_y_el_sobrante_positivo():
    base = {
        "inicial": Decimal("0"), "cargado": Decimal("240"),
        "vendido": Decimal("180"), "merma": Decimal("0"), "devuelto": Decimal("0"),
    }
    falta = RenglonDeLiquidacion(**base, contado=Decimal("54"))
    sobra = RenglonDeLiquidacion(**base, contado=Decimal("66"))

    assert falta.diferencia == Decimal("-6.000")
    assert falta.es_faltante
    assert sobra.diferencia == Decimal("6.000")
    assert not sobra.es_faltante


def test_un_dia_SIN_carga_cuadra_con_lo_que_el_camion_traia():
    """El día que el vendedor sale solo con lo que le quedó.

    Con el camión como almacén rodante esto es un día normal, no una excepción, y
    el cierre tiene que cuadrar sin una sola pieza cargada.
    """
    r = RenglonDeLiquidacion(
        inicial=Decimal("90"), cargado=Decimal("0"), vendido=Decimal("30"),
        merma=Decimal("0"), devuelto=Decimal("0"), contado=Decimal("60"),
    )
    assert r.esperado == Decimal("60.000")
    assert r.cuadra


def test_el_saldo_inicial_es_la_ecuacion_DESPEJADA():
    """`saldo_inicial` y `esperado_en_camion` tienen que ser la misma ecuación.

    Si se separaran, el inicial que se guarda en el cierre no sería el que hace
    que `esperado` dé el saldo vivo del camión, y la diferencia que se le cobra al
    vendedor dejaría de ser «lo que conté menos lo que el sistema tiene».
    """
    en_camion = Decimal("150")
    movimientos = {
        "cargado": Decimal("240"), "vendido": Decimal("180"),
        "merma": Decimal("6"), "devuelto": Decimal("12"),
    }
    inicial = saldo_inicial(en_camion, **movimientos)
    assert inicial == Decimal("84.000")

    r = RenglonDeLiquidacion(inicial=inicial, **movimientos, contado=en_camion)
    assert r.esperado == en_camion
    assert r.cuadra, "contar lo que el sistema tiene siempre debe cuadrar"


def test_la_diferencia_de_efectivo_redondea_a_centavos():
    assert diferencia_de_efectivo(Decimal("2200.00"), Decimal("2250.00")) == Decimal("-50.00")
    assert diferencia_de_efectivo(Decimal("2250.00"), Decimal("2250.00")) == Decimal("0.00")


def test_la_cantidad_queda_a_TRES_decimales():
    """La columna es `numeric(14,3)`. Si el dominio diera más decimales, la
    comparación con la base fallaría por un redondeo y no por un descuadre real."""
    r = RenglonDeLiquidacion(
        cargado=Decimal("240"), vendido=Decimal("180.5"), merma=Decimal("0"),
        devuelto=Decimal("0"), contado=Decimal("59.5"),
    )
    assert r.esperado == Decimal("59.500")
    assert r.diferencia == Decimal("0.000")
    assert r.cuadra
