"""Reglas de crédito (ADR 0002).

La prueba que más importa es `test_ventas_encadenadas_sin_sincronizar`: es el
agujero real de esta regla en un sistema offline.
"""

from __future__ import annotations

from decimal import Decimal as D

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from app.domain.credito import CERO, EstadoCredito, Motivo, evaluar_venta


def estado(limite="5000.00", saldo="0.00", **kw) -> EstadoCredito:
    return EstadoCredito(limite=D(limite), saldo_confirmado=D(saldo), **kw)


# ---------------------------------------------------------------------------
# La regla del negocio
# ---------------------------------------------------------------------------

def test_credito_dentro_del_limite_pasa():
    r = evaluar_venta(estado(saldo="1000.00"), D("500.00"), a_credito=True)
    assert r.permitida
    assert r.motivo is Motivo.OK
    assert r.disponible == D("3500.00")


def test_credito_que_excede_el_limite_se_bloquea():
    r = evaluar_venta(estado(saldo="4800.00"), D("500.00"), a_credito=True)
    assert not r.permitida
    assert r.motivo is Motivo.EXCEDE_LIMITE
    assert r.excedente == D("300.00")     # lo que le falta abonar


def test_justo_en_el_limite_pasa():
    """El límite es el tope permitido, no el primer valor prohibido."""
    r = evaluar_venta(estado(saldo="4500.00"), D("500.00"), a_credito=True)
    assert r.permitida
    assert r.disponible == CERO


def test_un_peso_arriba_del_limite_no_pasa():
    assert not evaluar_venta(estado(saldo="4500.00"), D("500.01"), a_credito=True).permitida


def test_el_contado_nunca_se_bloquea():
    """La regla explícita del negocio, y también la correcta: negar la venta de
    contado no cobra la deuda vieja y sí pierde la venta nueva."""
    sin_linea = estado(saldo="99999.00", limite="100.00", bloqueado=True, permite_credito=False)
    r = evaluar_venta(sin_linea, D("500.00"), a_credito=False)
    assert r.permitida
    assert r.motivo is Motivo.CONTADO_SIEMPRE_PERMITIDO


def test_cliente_bloqueado_no_compra_a_credito():
    r = evaluar_venta(estado(bloqueado=True), D("10.00"), a_credito=True)
    assert not r.permitida
    assert r.motivo is Motivo.CLIENTE_BLOQUEADO


def test_cliente_sin_linea_no_compra_a_credito():
    r = evaluar_venta(estado(permite_credito=False), D("10.00"), a_credito=True)
    assert not r.permitida
    assert r.motivo is Motivo.SIN_LINEA_DE_CREDITO


def test_el_bloqueo_manual_gana_sobre_el_limite():
    """Un cliente bloqueado a mano y además pasado de límite reporta el bloqueo:
    es la causa que la oficina tiene que resolver primero."""
    r = evaluar_venta(estado(saldo="9999.00", bloqueado=True), D("10.00"), a_credito=True)
    assert r.motivo is Motivo.CLIENTE_BLOQUEADO


# ---------------------------------------------------------------------------
# El agujero del offline
# ---------------------------------------------------------------------------

def test_ventas_encadenadas_sin_sincronizar():
    """Sin contar la cola local, cinco ventas de la mañana dejarían al cliente
    muy por encima de su línea: ninguna alcanzó a sincronizar y cada una, por
    separado, cabía en el límite."""
    limite, saldo = "5000.00", "4000.00"

    primera = evaluar_venta(estado(limite, saldo), D("800.00"), a_credito=True)
    assert primera.permitida

    segunda = evaluar_venta(
        estado(limite, saldo, cargos_pendientes=D("800.00")), D("800.00"), a_credito=True
    )
    assert not segunda.permitida
    assert segunda.excedente == D("600.00")


def test_un_abono_sin_sincronizar_libera_linea_de_inmediato():
    """Si el vendedor acaba de cobrarle en efectivo, la línea se libera ya.
    Hacerlo esperar a la sincronización sería negarle una venta que ya pagó."""
    sin_abono = estado(saldo="4800.00")
    assert not evaluar_venta(sin_abono, D("500.00"), a_credito=True).permitida

    con_abono = estado(saldo="4800.00", abonos_pendientes=D("1000.00"))
    assert evaluar_venta(con_abono, D("500.00"), a_credito=True).permitida


def test_saldo_a_favor_suma_linea():
    """Un cliente que pagó de más tiene más línea, no la misma."""
    e = estado(saldo="0.00", abonos_pendientes=D("500.00"))
    assert e.saldo_efectivo == D("-500.00")
    assert e.disponible == D("5500.00")
    assert evaluar_venta(e, D("5500.00"), a_credito=True).permitida


def test_disponible_nunca_es_negativo():
    """Un cliente pasado de su límite tiene cero disponible, no una deuda de
    línea que confundiría al vendedor en pantalla."""
    assert estado(saldo="9000.00").disponible == CERO


def test_limite_en_cero_bloquea_todo_credito():
    r = evaluar_venta(estado(limite="0.00"), D("0.01"), a_credito=True)
    assert not r.permitida
    assert r.motivo is Motivo.EXCEDE_LIMITE


def test_venta_de_cero_a_credito_pasa():
    assert evaluar_venta(estado(saldo="5000.00"), CERO, a_credito=True).permitida


# ---------------------------------------------------------------------------
# Contratos de tipo — el dinero nunca es float
# ---------------------------------------------------------------------------

def test_rechaza_float_en_el_total():
    with pytest.raises(TypeError):
        evaluar_venta(estado(), 500.0, a_credito=True)


def test_rechaza_float_en_el_estado():
    with pytest.raises(TypeError):
        EstadoCredito(limite=5000.0, saldo_confirmado=D("0"))


def test_rechaza_negativos():
    with pytest.raises(ValueError, match="negativo"):
        EstadoCredito(limite=D("-1"), saldo_confirmado=D("0"))
    with pytest.raises(ValueError, match="negativo"):
        evaluar_venta(estado(), D("-1"), a_credito=True)


# ---------------------------------------------------------------------------
# Propiedades
# ---------------------------------------------------------------------------

_dinero = st.decimals(
    min_value=D("0"), max_value=D("1000000"), places=2, allow_nan=False, allow_infinity=False
)


@given(limite=_dinero, saldo=_dinero, total=_dinero)
def test_el_contado_pasa_siempre(limite, saldo, total):
    e = EstadoCredito(limite=limite, saldo_confirmado=saldo, bloqueado=True, permite_credito=False)
    assert evaluar_venta(e, total, a_credito=False).permitida


@given(limite=_dinero, saldo=_dinero, total=_dinero)
def test_permitir_implica_no_rebasar_el_limite(limite, saldo, total):
    """La invariante del negocio: si se permitió, el saldo resultante cabe."""
    e = EstadoCredito(limite=limite, saldo_confirmado=saldo)
    if evaluar_venta(e, total, a_credito=True).permitida:
        assert saldo + total <= limite


@given(limite=_dinero, saldo=_dinero, total=_dinero)
def test_lo_disponible_siempre_se_puede_gastar(limite, saldo, total):
    """Si la pantalla dice 'disponible $X', vender exactamente $X debe pasar.
    Un disponible que no se puede gastar es una mentira al vendedor."""
    e = EstadoCredito(limite=limite, saldo_confirmado=saldo)
    assume(e.disponible > 0)
    assert evaluar_venta(e, e.disponible, a_credito=True).permitida


@given(limite=_dinero, saldo=_dinero, cargos=_dinero, abonos=_dinero, total=_dinero)
def test_bloquear_por_limite_siempre_reporta_excedente(limite, saldo, cargos, abonos, total):
    e = EstadoCredito(
        limite=limite, saldo_confirmado=saldo, cargos_pendientes=cargos, abonos_pendientes=abonos
    )
    r = evaluar_venta(e, total, a_credito=True)
    if r.motivo is Motivo.EXCEDE_LIMITE:
        assert r.excedente > 0
        assert r.requiere_revision
