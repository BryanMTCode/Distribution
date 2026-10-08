"""La clave corta para vincular el teléfono (ADR 0002 §85).

1. **La elige la oficina** al vincular —o el panel inventa una de seis—, se
   guarda en mayúsculas y no se repite. La mal escrita se dice con palabras.
2. **El login la cambia por el id del equipo**: el teléfono no sabe su id hasta
   que entra. Sigue exigiendo la contraseña y que el equipo sea de ese vendedor.
3. **Se puede cambiar** después, y al revocar el equipo queda libre.
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR, solo_texto
from tests.test_borrado_remoto import _csrf, _entrar

pytestmark = pytest.mark.asyncio


async def _vincular(cliente, semilla, clave: str):
    return await cliente.post(
        "/panel/equipos/registrar",
        data={
            "vendedor_id": str(semilla["vendedor"]),
            "etiqueta": "Moto G54 — Juan",
            "clave": clave,
            "csrf": await _csrf(cliente),
        },
        follow_redirects=True,
    )


async def _equipo(sesion, semilla) -> dict:
    return dict(
        (
            await sesion.execute(
                text("SELECT id, clave_vinculo, estado FROM dispositivos WHERE usuario_id = :v"),
                {"v": semilla["vendedor"]},
            )
        ).mappings().one()
    )


async def _login(cliente, clave: str, codigo: str = "VEND01"):
    return await cliente.post(
        "/v1/auth/login",
        json={"codigo": codigo, "password": PASSWORD_VENDEDOR, "clave_equipo": clave},
    )


async def test_la_clave_elegida_se_guarda_en_mayusculas_y_se_muestra(cliente, sesion, semilla):
    await _entrar(cliente)
    r = await _vincular(cliente, semilla, " ruta 4 ")
    assert "teclea una sola vez: RUTA4" in solo_texto(r)
    assert (await _equipo(sesion, semilla))["clave_vinculo"] == "RUTA4"
    assert "RUTA4" in solo_texto(await cliente.get("/panel/equipos"))


async def test_vacia_el_panel_inventa_una_que_no_se_confunde(cliente, sesion, semilla):
    await _entrar(cliente)
    await _vincular(cliente, semilla, "")
    clave = (await _equipo(sesion, semilla))["clave_vinculo"]
    assert re.fullmatch(r"[A-HJ-NP-Z2-9]{6}", clave)


@pytest.mark.parametrize(
    "clave", ["AB1", "CAÑADA", "RUTA_4", "UNA-CLAVE-DEMASIADO-LARGA"]
)
async def test_la_mal_escrita_no_se_guarda_y_se_dice_por_que(cliente, sesion, semilla, clave):
    await _entrar(cliente)
    r = await _vincular(cliente, semilla, clave)
    assert "de 4 a 20 letras o números" in solo_texto(r)
    cuantos = (
        await sesion.execute(
            text("SELECT count(*) FROM dispositivos WHERE usuario_id = :v"),
            {"v": semilla["vendedor"]},
        )
    ).scalar_one()
    assert cuantos == 0


async def test_no_se_repite(cliente, sesion, semilla):
    otro = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, clave_vinculo) "
             "VALUES (:d, :u, 'El de la oficina', 'suspendido', 'RUTA4')"),
        {"d": otro, "u": semilla["admin"]},
    )
    await sesion.commit()
    await _entrar(cliente)
    r = await _vincular(cliente, semilla, "Ruta4")
    assert "ya la tiene «El de la oficina»" in solo_texto(r)


async def test_el_login_cambia_la_clave_por_el_id(cliente, sesion, semilla):
    await _entrar(cliente)
    await _vincular(cliente, semilla, "RUTA4")
    equipo = await _equipo(sesion, semilla)

    r = await _login(cliente, "ruta4")
    assert r.status_code == 200, r.text
    assert r.json()["dispositivo_id"] == str(equipo["id"])
    assert r.json()["credencial_local"]["codigo"] == "VEND01"

    # Sin la contraseña no abre nada.
    r = await cliente.post(
        "/v1/auth/login",
        json={"codigo": "VEND01", "password": "otra", "clave_equipo": "RUTA4"},
    )
    assert r.status_code == 401
    r = await _login(cliente, "NOEXISTE")
    assert r.status_code == 409
    assert "clave de equipo no existe" in r.json()["detail"]
    # El id largo sigue sirviendo.
    r = await cliente.post(
        "/v1/auth/login",
        json={"codigo": "VEND01", "password": PASSWORD_VENDEDOR,
              "dispositivo_id": str(equipo["id"])},
    )
    assert r.status_code == 200
    assert r.json()["dispositivo_id"] == str(equipo["id"])


async def test_la_clave_de_otro_vendedor_no_abre(cliente, sesion, semilla):
    await _entrar(cliente)
    await _vincular(cliente, semilla, "RUTA4")
    r = await _login(cliente, "RUTA4", codigo="ADMIN01")
    assert r.status_code == 403
    assert "otro usuario" in r.json()["detail"]


async def test_se_cambia_y_al_revocar_queda_libre(cliente, sesion, semilla):
    await _entrar(cliente)
    await _vincular(cliente, semilla, "RUTA4")
    equipo = await _equipo(sesion, semilla)

    r = await cliente.post(
        f"/panel/equipos/{equipo['id']}/clave",
        data={"clave": "juan-1", "csrf": await _csrf(cliente)},
        follow_redirects=True,
    )
    assert "es JUAN-1" in solo_texto(r)
    assert (await _login(cliente, "RUTA4")).status_code == 409
    assert (await _login(cliente, "JUAN-1")).status_code == 200

    await cliente.post(
        f"/panel/equipos/{equipo['id']}/estado",
        data={"destino": "revocado", "motivo": "se perdió", "csrf": await _csrf(cliente)},
        follow_redirects=True,
    )
    assert (await _equipo(sesion, semilla))["clave_vinculo"] is None
    assert (await _login(cliente, "JUAN-1")).status_code == 409
