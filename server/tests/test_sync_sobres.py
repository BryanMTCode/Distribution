"""Validación estructural del lote y del hash (dominio puro)."""

from __future__ import annotations

import uuid

import pytest

from app.domain.sync.sobres import (
    MAX_SOBRES_POR_LOTE,
    CodigoError,
    LoteInvalido,
    Operacion,
    Sobre,
    hash_de_sobre,
    validar_lote,
    verificar_hash,
)
from tests.ayudas_sync import operacion_cliente, sobre


def test_los_sobres_se_ordenan_por_secuencia():
    """Un cobro que referencia una venta creada offline tiene que aplicarse
    después de ella. El orden lo fija la secuencia del dispositivo, no el orden
    del JSON, que cualquier serializador puede alterar."""
    desordenados = [sobre(secuencia=3), sobre(secuencia=1), sobre(secuencia=2)]
    assert [s.secuencia for s in validar_lote(desordenados)] == [1, 2, 3]


def test_lote_vacio_se_rechaza():
    with pytest.raises(LoteInvalido, match="no trae sobres"):
        validar_lote([])


def test_operacion_id_repetido_en_el_mismo_lote():
    uno = sobre(secuencia=1)
    dos = Sobre(uno.operacion_id, 2, uno.hash_payload, uno.operaciones)
    with pytest.raises(LoteInvalido, match="repite un operacion_id"):
        validar_lote([uno, dos])


def test_secuencia_repetida():
    with pytest.raises(LoteInvalido, match="repite un número de secuencia"):
        validar_lote([sobre(secuencia=1), sobre(secuencia=1)])


def test_sobre_vacio():
    vacio = Sobre(uuid.uuid4(), 1, "x" * 64, [])
    with pytest.raises(LoteInvalido, match="viene vacío"):
        validar_lote([vacio])


def test_la_misma_entidad_dos_veces_en_un_sobre():
    """Dos operaciones sobre el mismo documento dentro de un sobre: cuál gana
    sería arbitrario."""
    op = operacion_cliente()
    duplicado = sobre(op, Operacion(op.tipo, op.entidad_id, {"nombre_comercial": "otro"}))
    with pytest.raises(LoteInvalido, match="la misma entidad dos veces"):
        validar_lote([duplicado])


def test_lote_demasiado_grande():
    """Un vendedor sin señal una semana acumula miles de operaciones; mandarlas
    de un golpe por 3G en un mercado es la forma más segura de que el lote
    nunca complete."""
    grande = [sobre(secuencia=i) for i in range(MAX_SOBRES_POR_LOTE + 1)]
    with pytest.raises(LoteInvalido, match="máximo"):
        validar_lote(grande)


def test_el_hash_detecta_contenido_alterado():
    original = sobre(operacion_cliente("Doña Mary"))
    assert verificar_hash(original)

    alterado = Sobre(
        original.operacion_id,
        original.secuencia,
        original.hash_payload,
        [
            Operacion(
                "cliente.crear",
                original.operaciones[0].entidad_id,
                {"nombre_comercial": "OTRO"},
            )
        ],
    )
    assert not verificar_hash(alterado)


def test_el_hash_no_depende_del_orden_de_las_claves():
    """Dart y Python pueden serializar en otro orden; el hash canónico no."""
    a = sobre(Operacion("cliente.crear", uuid.UUID(int=1), {"a": "1", "b": "2"}))
    b = Sobre(a.operacion_id, a.secuencia, "",
              [Operacion("cliente.crear", uuid.UUID(int=1), {"b": "2", "a": "1"})])
    assert hash_de_sobre(b) == a.hash_payload


def test_un_float_en_el_payload_no_pasa_la_verificacion():
    """El contrato prohíbe los float: un double no admite forma canónica.

    Se construye el sobre a mano con un hash cualquiera, porque el propio
    ayudante de pruebas no puede calcularlo — que es justo la señal de que el
    dispositivo tampoco habría podido mandarlo bien.
    """
    con_float = Sobre(
        uuid.uuid4(), 1, "0" * 64,
        [Operacion("cliente.crear", uuid.uuid4(), {"total": 250.0})],
    )
    assert not verificar_hash(con_float)


def test_solo_el_error_interno_es_reintentable():
    """El dispositivo decide qué hacer según este código: los reintentables
    vuelven a la cola, los demás se dan por perdidos en el equipo."""
    assert CodigoError.ERROR_INTERNO.reintentable
    assert not CodigoError.HASH_NO_COINCIDE.reintentable
    assert not CodigoError.PAYLOAD_INVALIDO.reintentable
    assert not CodigoError.TIPO_DESCONOCIDO.reintentable
