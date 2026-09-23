"""Prueba de contrato de punta a punta entre el cliente Dart y el servidor.

`contracts/sobres_de_ejemplo.json` lo genera el cliente Dart con su propio
código —el mismo `SobreLocal` y el mismo hash que usará el teléfono— y aquí se
empuja por el endpoint real de sincronización.

Es la única prueba del proyecto que demuestra que **lo que arma el dispositivo
es exactamente lo que el servidor acepta**. Sin ella, la divergencia aparecería
el primer día de piloto, en un mercado y sin señal.

Regenerar el archivo:
    cd mobile/packages/dsd_core && dart run tool/generar_sobres_ejemplo.dart
"""

from __future__ import annotations

import json
import pathlib
import uuid

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR

_ARCHIVO = (
    pathlib.Path(__file__).resolve().parents[2] / "contracts" / "sobres_de_ejemplo.json"
)
DOCUMENTO = json.loads(_ARCHIVO.read_text(encoding="utf-8"))


async def _cab_vendedor(cliente, sesion, semilla) -> dict:
    dispositivo_id = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
             "VALUES (:d,:u,'Moto G54','activo',now())"),
        {"d": dispositivo_id, "u": semilla["vendedor"]},
    )
    await sesion.commit()
    r = await cliente.post("/v1/auth/login", json={
        "codigo": "VEND01", "password": PASSWORD_VENDEDOR, "dispositivo_id": str(dispositivo_id),
    })
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _cuerpo() -> dict:
    return {"lote_id": DOCUMENTO["lote_id"], "sobres": DOCUMENTO["sobres"]}


def test_el_archivo_trae_sobres():
    assert len(DOCUMENTO["sobres"]) >= 3


def test_los_hashes_de_dart_coinciden_con_los_de_python():
    """La comprobación de fondo: el hash lo calculó Dart, y Python recompone la
    misma forma canónica a partir del mismo contenido.

    Si esto falla, el motor de sincronización mandaría a cuarentena todos los
    sobres legítimos del piloto.
    """
    from app.domain.sync.sobres import Operacion, Sobre, hash_de_sobre

    for crudo in DOCUMENTO["sobres"]:
        sobre = Sobre(
            operacion_id=uuid.UUID(crudo["operacion_id"]),
            secuencia=crudo["secuencia"],
            hash_payload=crudo["hash_payload"],
            visita_id=uuid.UUID(crudo["visita_id"]) if crudo["visita_id"] else None,
            operaciones=[
                Operacion(o["tipo"], uuid.UUID(o["entidad_id"]), o["datos"])
                for o in crudo["operaciones"]
            ],
        )
        assert hash_de_sobre(sobre) == crudo["hash_payload"], (
            f"divergencia Dart↔Python en el sobre {crudo['operacion_id']}"
        )


@pytest.mark.asyncio
async def test_el_servidor_acepta_los_sobres_de_dart(cliente, semilla, sesion):
    cab = await _cab_vendedor(cliente, sesion, semilla)

    r = await cliente.post("/v1/sync/push", json=_cuerpo(), headers=cab)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["rechazadas"] == 0, cuerpo["resultados"]
    assert cuerpo["aceptadas"] == len(DOCUMENTO["sobres"])

    # Nada quedó en cuarentena: el hash de Dart pasó la verificación.
    en_cuarentena = (
        await sesion.execute(text("SELECT count(*) FROM sync_cuarentena"))
    ).scalar_one()
    assert en_cuarentena == 0


@pytest.mark.asyncio
async def test_los_acentos_y_el_emoji_sobreviven_el_viaje(cliente, semilla, sesion):
    """Si el escape difiriera entre lenguajes, el hash no coincidiría y el
    sobre acabaría en cuarentena en vez de en la base."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    await cliente.post("/v1/sync/push", json=_cuerpo(), headers=cab)

    nombres = set(
        (await sesion.execute(text("SELECT nombre_comercial FROM clientes"))).scalars()
    )
    assert "La Esquina de Ñoño 🏪" in nombres


@pytest.mark.asyncio
async def test_reenviar_el_lote_de_dart_no_duplica(cliente, semilla, sesion):
    cab = await _cab_vendedor(cliente, sesion, semilla)
    await cliente.post("/v1/sync/push", json=_cuerpo(), headers=cab)
    repetido = await cliente.post("/v1/sync/push", json=_cuerpo(), headers=cab)

    assert repetido.json()["aceptadas"] == 0
    assert repetido.json()["duplicadas"] == len(DOCUMENTO["sobres"])

    total = (await sesion.execute(text("SELECT count(*) FROM clientes"))).scalar_one()
    assert total == sum(len(s["operaciones"]) for s in DOCUMENTO["sobres"])
