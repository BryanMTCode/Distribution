"""Login, credencial offline y revocación de dispositivos."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR

pytestmark = pytest.mark.asyncio


async def _registrar_dispositivo(cliente, token, dispositivo_id) -> None:
    respuesta = await cliente.post(
        "/v1/dispositivos/registrar",
        json={"id": str(dispositivo_id), "etiqueta": "Moto G54"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert respuesta.status_code == 200, respuesta.text


async def test_gerencia_entra_sin_dispositivo(cliente, semilla):
    r = await cliente.post(
        "/v1/auth/login", json={"codigo": "ADMIN01", "password": PASSWORD_VENDEDOR}
    )
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["access_token"]
    # El perfil de gerencia es online: no necesita credencial local.
    assert cuerpo["credencial_local"] is None


async def test_vendedor_exige_dispositivo_registrado(cliente, semilla):
    """Un vendedor sin equipo registrado no puede operar: el dispositivo es la
    unidad de confianza y el espacio de nombres de los folios."""
    r = await cliente.post(
        "/v1/auth/login", json={"codigo": "VEND01", "password": PASSWORD_VENDEDOR}
    )
    assert r.status_code == 400
    assert "dispositivo registrado" in r.json()["detail"]


async def test_login_con_dispositivo_devuelve_credencial_offline(cliente, semilla):
    admin = (
        await cliente.post(
            "/v1/auth/login", json={"codigo": "ADMIN01", "password": PASSWORD_VENDEDOR}
        )
    ).json()

    # El vendedor registra su propio equipo con un token obtenido... del admin
    # no: cada quien registra el suyo. Se simula el alta desde la oficina.
    dispositivo_id = uuid.uuid4()
    await _registrar_dispositivo(cliente, admin["access_token"], dispositivo_id)

    # Ese dispositivo quedó ligado al admin; el vendedor necesita el suyo.
    otro = uuid.uuid4()
    vendedor_sin_equipo = await cliente.post(
        "/v1/auth/login",
        json={
            "codigo": "VEND01",
            "password": PASSWORD_VENDEDOR,
            "dispositivo_id": str(otro),
        },
    )
    assert vendedor_sin_equipo.status_code == 409


async def test_credencial_local_trae_el_hash_y_los_parametros(cliente, semilla, sesion):
    """Es lo que permite al vendedor entrar sin señal: el mismo hash Argon2id
    que está en la base, más los parámetros que Dart debe usar."""
    dispositivo_id = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'Moto G54', 'activo', now())"
        ),
        {"d": dispositivo_id, "u": semilla["vendedor"]},
    )
    await sesion.commit()

    r = await cliente.post(
        "/v1/auth/login",
        json={
            "codigo": "VEND01",
            "password": PASSWORD_VENDEDOR,
            "dispositivo_id": str(dispositivo_id),
        },
    )
    assert r.status_code == 200, r.text
    credencial = r.json()["credencial_local"]
    assert credencial["password_hash"].startswith("$argon2id$")
    assert credencial["argon2"]["memoria_kib"] == 65536
    assert credencial["rol"] == "vendedor"
    assert "ventas.crear" in credencial["permisos"]
    # Vencimiento explícito: un equipo extraviado no opera indefinidamente.
    assert credencial["valida_hasta"].endswith("Z")


async def test_password_incorrecta(cliente, semilla):
    r = await cliente.post("/v1/auth/login", json={"codigo": "ADMIN01", "password": "nope"})
    assert r.status_code == 401
    assert r.json()["detail"] == "credenciales inválidas"


async def test_usuario_inexistente_da_el_mismo_mensaje(cliente, semilla):
    """No se regala la existencia de una cuenta."""
    r = await cliente.post("/v1/auth/login", json={"codigo": "NADIE", "password": "x"})
    assert r.status_code == 401
    assert r.json()["detail"] == "credenciales inválidas"


async def test_yo_devuelve_el_alcance(cliente, semilla):
    token = (
        await cliente.post(
            "/v1/auth/login", json={"codigo": "ADMIN01", "password": PASSWORD_VENDEDOR}
        )
    ).json()["access_token"]
    r = await cliente.get("/v1/auth/yo", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["rol"] == "admin"


async def test_sin_token_es_401(cliente, semilla):
    assert (await cliente.get("/v1/auth/yo")).status_code == 401


async def test_token_basura_es_401(cliente, semilla):
    r = await cliente.get("/v1/auth/yo", headers={"Authorization": "Bearer no.es.un.jwt"})
    assert r.status_code == 401


async def test_revocar_dispositivo_invalida_el_token_vivo(cliente, semilla, sesion):
    """Un access token dura 30 minutos. Treinta minutos con la cartera completa
    en un teléfono robado es demasiado: la revocación se consulta en cada
    petición, no al expirar el token."""
    dispositivo_id = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'Moto G54', 'activo', now())"
        ),
        {"d": dispositivo_id, "u": semilla["vendedor"]},
    )
    await sesion.commit()

    token = (
        await cliente.post(
            "/v1/auth/login",
            json={
                "codigo": "VEND01",
                "password": PASSWORD_VENDEDOR,
                "dispositivo_id": str(dispositivo_id),
            },
        )
    ).json()["access_token"]
    cabeceras = {"Authorization": f"Bearer {token}"}
    assert (await cliente.get("/v1/auth/yo", headers=cabeceras)).status_code == 200

    admin_token = (
        await cliente.post(
            "/v1/auth/login", json={"codigo": "ADMIN01", "password": PASSWORD_VENDEDOR}
        )
    ).json()["access_token"]
    revocacion = await cliente.post(
        f"/v1/dispositivos/{dispositivo_id}/revocar",
        json={"motivo": "equipo robado"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert revocacion.status_code == 204

    # Mismo token, ahora inservible.
    assert (await cliente.get("/v1/auth/yo", headers=cabeceras)).status_code == 401


async def test_un_vendedor_no_puede_revocar_dispositivos(cliente, semilla, sesion):
    """La UI oculta, el servidor prohíbe."""
    dispositivo_id = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'Moto G54', 'activo', now())"
        ),
        {"d": dispositivo_id, "u": semilla["vendedor"]},
    )
    await sesion.commit()
    token = (
        await cliente.post(
            "/v1/auth/login",
            json={
                "codigo": "VEND01",
                "password": PASSWORD_VENDEDOR,
                "dispositivo_id": str(dispositivo_id),
            },
        )
    ).json()["access_token"]

    r = await cliente.post(
        f"/v1/dispositivos/{dispositivo_id}/revocar",
        json={"motivo": "no debería poder"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


async def test_refresh_emite_access_nuevo(cliente, semilla):
    login = (
        await cliente.post(
            "/v1/auth/login", json={"codigo": "ADMIN01", "password": PASSWORD_VENDEDOR}
        )
    ).json()
    r = await cliente.post("/v1/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert r.status_code == 200
    assert r.json()["access_token"]


async def test_refresh_con_token_de_access_es_rechazado(cliente, semilla):
    """Los tipos de token no son intercambiables."""
    login = (
        await cliente.post(
            "/v1/auth/login", json={"codigo": "ADMIN01", "password": PASSWORD_VENDEDOR}
        )
    ).json()
    r = await cliente.post("/v1/auth/refresh", json={"refresh_token": login["access_token"]})
    assert r.status_code == 401


async def test_al_renovar_el_telefono_trae_la_contrasena_que_cambio_la_oficina(
    cliente, semilla, sesion
):
    """ADR 0002 §136: la contraseña cambiada en el panel le llega al teléfono en
    cuanto renueva su acceso —al sincronizar con señal—, sin volver a vincularlo.
    Sin dispositivo (Gerencia en la computadora) no viaja ninguna credencial."""
    from app.core.seguridad import hashear_password, verificar_password

    dispositivo_id = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'Moto G54', 'activo', now())"
        ),
        {"d": dispositivo_id, "u": semilla["vendedor"]},
    )
    await sesion.commit()
    login = (
        await cliente.post(
            "/v1/auth/login",
            json={"codigo": "VEND01", "password": PASSWORD_VENDEDOR,
                  "dispositivo_id": str(dispositivo_id)},
        )
    ).json()

    # La oficina le pone otra.
    await sesion.execute(
        text("UPDATE usuarios SET password_hash = :h WHERE id = :u"),
        {"h": hashear_password("frase nueva larga"), "u": semilla["vendedor"]},
    )
    await sesion.commit()

    r = await cliente.post("/v1/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert r.status_code == 200, r.text
    credencial = r.json()["credencial_local"]
    assert verificar_password("frase nueva larga", credencial["password_hash"])
    assert not verificar_password(PASSWORD_VENDEDOR, credencial["password_hash"])
    assert credencial["codigo"] == "VEND01"

    # La nueva entra en línea con el mismo teléfono: es lo que hace la app
    # cuando lo tecleado no coincide con lo que guardaba.
    nueva = await cliente.post(
        "/v1/auth/login",
        json={"codigo": "VEND01", "password": "frase nueva larga",
              "dispositivo_id": str(dispositivo_id)},
    )
    assert nueva.status_code == 200
    assert nueva.json()["credencial_local"]["password_hash"] == credencial["password_hash"]

    sin_equipo = (
        await cliente.post(
            "/v1/auth/login", json={"codigo": "ADMIN01", "password": PASSWORD_VENDEDOR}
        )
    ).json()
    r = await cliente.post(
        "/v1/auth/refresh", json={"refresh_token": sin_equipo["refresh_token"]}
    )
    assert r.json()["credencial_local"] is None
