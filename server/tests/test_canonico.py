"""El contrato canónico entre Dart y Python.

Estas pruebas son la mitad Python de `contracts/canonical_vectors.json`. La
otra mitad vive en `mobile/test/canonico_test.dart` y corre contra el MISMO
archivo. Si una se pone roja y la otra no, hay divergencia — que es exactamente
lo que este contrato existe para atrapar.
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from app.domain.canonico import (
    PayloadNoCanonico,
    a_texto_canonico,
    formatear_cantidad,
    formatear_dinero,
    formatear_instante,
    hash_payload,
)

_RAIZ = pathlib.Path(__file__).resolve().parents[2]
_ARCHIVO = _RAIZ / "contracts" / "canonical_vectors.json"
VECTORES = json.loads(_ARCHIVO.read_text(encoding="utf-8"))["vectores"]


@pytest.mark.parametrize("vector", VECTORES, ids=lambda v: v["nombre"])
def test_forma_canonica(vector):
    assert a_texto_canonico(vector["payload"]) == vector["canonico"]


@pytest.mark.parametrize("vector", VECTORES, ids=lambda v: v["nombre"])
def test_hash(vector):
    assert hash_payload(vector["payload"]) == vector["sha256"]


def test_hay_vectores_suficientes():
    # Guarda contra un archivo truncado o mal generado.
    assert len(VECTORES) >= 30


# ---------------------------------------------------------------------------
# Las reglas, una por una
# ---------------------------------------------------------------------------

def test_clave_nula_equivale_a_clave_ausente():
    """Regla 2: elimina de raíz la divergencia null/ausente entre lenguajes."""
    assert hash_payload({"a": 1}) == hash_payload({"a": 1, "b": None})


def test_float_prohibido():
    """Regla 4: un double en el payload es un error, no una advertencia."""
    with pytest.raises(PayloadNoCanonico, match="float"):
        a_texto_canonico({"total": 250.0})


def test_decimal_crudo_prohibido():
    """Un Decimal sin formatear es ambiguo: '250.0' y '250.00' son distintos."""
    with pytest.raises(PayloadNoCanonico, match="escala fija"):
        a_texto_canonico({"total": Decimal("250.00")})


def test_datetime_crudo_prohibido():
    with pytest.raises(PayloadNoCanonico, match="formatear_instante"):
        a_texto_canonico({"fecha": datetime.now(UTC)})


def test_instante_sin_zona_horaria_es_error():
    """El reloj del teléfono y el del servidor están en juego: una fecha
    ambigua entre ambos es indefendible."""
    with pytest.raises(PayloadNoCanonico, match="zona horaria"):
        formatear_instante(datetime(2026, 9, 15, 3, 14, 7))


def test_instante_se_normaliza_a_utc():
    en_mexico = datetime(2026, 9, 14, 21, 14, 7, tzinfo=timezone(timedelta(hours=-6)))
    assert formatear_instante(en_mexico) == "2026-09-15T03:14:07.000Z"


def test_orden_de_claves_no_depende_de_insercion():
    assert a_texto_canonico({"z": 1, "a": 2}) == a_texto_canonico({"a": 2, "z": 1})


def test_orden_de_arreglo_si_importa():
    assert hash_payload({"x": [1, 2]}) != hash_payload({"x": [2, 1]})


def test_acentos_y_emoji_van_literales():
    assert a_texto_canonico({"n": "Ñ🔒"}) == '{"n":"Ñ🔒"}'


def test_diagonal_no_se_escapa():
    assert a_texto_canonico({"u": "a/b"}) == '{"u":"a/b"}'


def test_dinero_conserva_escala():
    assert formatear_dinero("250") == "250.00"
    assert formatear_dinero(Decimal("-125.5")) == "-125.50"
    assert formatear_cantidad("1.375") == "1.375"
    assert formatear_cantidad(12) == "12.000"


def test_dinero_rechaza_float():
    """0.1 + 0.2 nunca debe poder entrar al sistema como importe."""
    with pytest.raises(PayloadNoCanonico):
        formatear_dinero(0.1 + 0.2)


# ---------------------------------------------------------------------------
# Propiedades (Hypothesis)
# ---------------------------------------------------------------------------

_valores = st.recursive(
    st.one_of(st.text(), st.booleans(), st.integers(min_value=-(10**12), max_value=10**12)),
    lambda hijos: st.one_of(
        st.lists(hijos, max_size=4),
        st.dictionaries(st.text(min_size=0, max_size=8), hijos, max_size=4),
    ),
    max_leaves=12,
)
_payloads = st.dictionaries(st.text(min_size=0, max_size=10), _valores, max_size=6)


@given(payload=_payloads)
def test_canonizar_es_determinista(payload):
    assert a_texto_canonico(payload) == a_texto_canonico(payload)


@given(payload=_payloads)
def test_el_orden_de_insercion_nunca_cambia_el_hash(payload):
    """La propiedad que más importa: dos dicts con las mismas parejas
    producen el mismo hash, sin importar en qué orden se armaron."""
    invertido = dict(reversed(list(payload.items())))
    assert hash_payload(payload) == hash_payload(invertido)


@given(payload=_payloads, clave=st.text(min_size=1, max_size=8))
def test_agregar_una_clave_nula_nunca_cambia_el_hash(payload, clave):
    # La clave debe ser nueva: poner en None una que ya tenía valor sí cambia
    # el payload, y debe cambiar el hash. Hypothesis encontró justo ese caso.
    assume(clave not in payload)
    esperado = hash_payload(payload)
    assert hash_payload({**payload, clave: None}) == esperado


@given(payload=_payloads.filter(lambda p: bool(p)))
def test_anular_una_clave_existente_si_cambia_el_hash(payload):
    clave = next(iter(payload))
    assume(payload[clave] is not None)
    assert hash_payload({**payload, clave: None}) != hash_payload(payload)


@given(payload=_payloads)
def test_la_forma_canonica_es_json_valido(payload):
    assert json.loads(a_texto_canonico(payload)) is not None or True
