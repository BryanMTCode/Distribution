"""Contrato de Argon2id para el login offline.

Si estos parámetros y los del cliente Dart divergen, el vendedor no puede
entrar al empezar el día — sin señal y sin forma de arreglarlo desde la calle.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.core.seguridad import (
    PARAMETROS_ARGON2,
    hashear_password,
    requiere_rehash,
    verificar_password,
)

ARCHIVO = pathlib.Path(__file__).resolve().parents[2] / "contracts" / "argon2_vectors.json"
DOCUMENTO = json.loads(ARCHIVO.read_text(encoding="utf-8"))


def test_parametros_coinciden_con_el_contrato():
    """Cambiar un parámetro obliga a regenerar los vectores y a tocar el
    cliente Dart EN EL MISMO COMMIT."""
    p = DOCUMENTO["parametros"]
    assert p["variante"] == PARAMETROS_ARGON2.variante
    assert p["memoria_kib"] == PARAMETROS_ARGON2.memoria_kib
    assert p["iteraciones"] == PARAMETROS_ARGON2.iteraciones
    assert p["paralelismo"] == PARAMETROS_ARGON2.paralelismo
    assert p["hash_bytes"] == PARAMETROS_ARGON2.hash_bytes


@pytest.mark.parametrize("vector", DOCUMENTO["vectores"], ids=lambda v: v["nombre"])
def test_verifica_su_password(vector):
    assert verificar_password(vector["password"], vector["hash_phc"])


@pytest.mark.parametrize("vector", DOCUMENTO["vectores"], ids=lambda v: v["nombre"])
def test_rechaza_password_incorrecta(vector):
    assert not verificar_password(vector["password_incorrecta"], vector["hash_phc"])


@pytest.mark.parametrize("vector", DOCUMENTO["vectores"], ids=lambda v: v["nombre"])
def test_los_vectores_no_necesitan_rehash(vector):
    """Si esto falla, los vectores se generaron con parámetros viejos."""
    assert not requiere_rehash(vector["hash_phc"])


def test_la_sal_es_aleatoria():
    """Dos hashes de la misma contraseña difieren: por eso la prueba de Dart
    verifica en vez de regenerar."""
    assert hashear_password("misma") != hashear_password("misma")


def test_hash_corrupto_no_revienta():
    assert not verificar_password("x", "esto-no-es-un-hash-phc")
