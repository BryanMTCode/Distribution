"""Qué versión de la app trae cada vendedor, en el panel (ADR 0002 §144).

«¿Hay forma de poner en el dashboard qué versión tiene instalada cada vendedor?»

1. **El teléfono la dice al bajar**, no solo al subir: uno recién actualizado que
   todavía no vende nada también queda al día en su equipo.
2. **Teléfonos la muestra** junto a cada vendedor y marca la que va atrás de la
   más nueva que ya trae otro, con un aviso de a quién falta instalarle el APK.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR
from tests.test_panel_ajuste_camion import _entrar

pytestmark = pytest.mark.asyncio


async def _telefono(cliente, sesion, usuario, codigo: str, version: str) -> tuple[uuid.UUID, dict]:
    dispositivo = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en, "
             "                          app_version) "
             "VALUES (:d, :u, 'Moto G54', 'activo', now(), :v)"),
        {"d": dispositivo, "u": usuario, "v": version},
    )
    await sesion.commit()
    r = await cliente.post("/v1/auth/login", json={
        "codigo": codigo, "password": PASSWORD_VENDEDOR, "dispositivo_id": str(dispositivo),
    })
    assert r.status_code == 200, r.text
    return dispositivo, {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _version(sesion, dispositivo) -> str | None:
    await sesion.rollback()
    return (
        await sesion.execute(text("SELECT app_version FROM dispositivos WHERE id = :d"),
                             {"d": dispositivo})
    ).scalar_one()


async def test_al_bajar_el_telefono_deja_su_version(cliente, sesion, semilla):
    dispositivo, cab = await _telefono(cliente, sesion, semilla["vendedor"], "VEND01", "0.1.0+40")
    r = await cliente.get("/v1/sync/pull", params={"cursor": 0, "app_version": "0.1.0+47"},
                          headers=cab)
    assert r.status_code == 200, r.text
    assert await _version(sesion, dispositivo) == "0.1.0+47"
    # Sin versión (una app de antes), no se toca la que ya estaba.
    await cliente.get("/v1/sync/pull", params={"cursor": 0}, headers=cab)
    assert await _version(sesion, dispositivo) == "0.1.0+47"


async def test_telefonos_marca_al_que_va_atras(cliente, sesion, semilla):
    from tests.test_panel_equipo import _crear_usuario

    await _entrar(cliente)
    await _crear_usuario(cliente, codigo="VEND02")
    await sesion.rollback()
    otro = (
        await sesion.execute(text("SELECT id FROM usuarios WHERE codigo = 'VEND02'"))
    ).scalar_one()
    await sesion.execute(
        text("INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en, "
             "                          app_version) VALUES "
             "(gen_random_uuid(), :v1, 'Moto de Juan', 'activo', now(), '0.1.0+44'), "
             "(gen_random_uuid(), :v2, 'Moto de María', 'activo', now(), '0.1.0+47')"),
        {"v1": semilla["vendedor"], "v2": otro},
    )
    await sesion.commit()

    pagina = await cliente.get("/panel/equipos")
    assert pagina.status_code == 200
    texto = " ".join(pagina.text.split())
    assert "1 teléfono(s) con una versión anterior a la 0.1.0+47:" in texto
    assert "Juan Pérez (0.1.0+44)" in texto
    assert 'id="version_VEND01"' in pagina.text and "· vieja" in texto
    assert "app 0.1.0+47" in texto


def test_las_versiones_se_comparan_por_numero():
    from app.api.admin.equipos import clave_de_version

    assert clave_de_version("0.1.0+47") > clave_de_version("0.1.0+9")
    assert clave_de_version("0.2.0+1") > clave_de_version("0.1.0+99")
    assert clave_de_version(None) is None and clave_de_version("rara") is None
