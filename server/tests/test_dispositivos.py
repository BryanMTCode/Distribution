"""Registro de dispositivos y rangos de folio.

Los rangos son la defensa contra el escenario que casi nadie prueba: la app se
reinstala, el contador local vuelve a 1, y el equipo empieza a reimprimir
folios que ya están en papel en manos de clientes.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR

pytestmark = pytest.mark.asyncio


async def _token_admin(cliente) -> str:
    r = await cliente.post(
        "/v1/auth/login", json={"codigo": "ADMIN01", "password": PASSWORD_VENDEDOR}
    )
    return r.json()["access_token"]


async def test_registro_es_idempotente(cliente, semilla):
    """La red puede entregar dos veces. Recibirlo dos veces no cambia nada."""
    token = await _token_admin(cliente)
    cabeceras = {"Authorization": f"Bearer {token}"}
    dispositivo_id = str(uuid.uuid4())
    cuerpo = {"id": dispositivo_id, "etiqueta": "Moto G54", "impresora_ancho_mm": 58}

    primera = await cliente.post("/v1/dispositivos/registrar", json=cuerpo, headers=cabeceras)
    segunda = await cliente.post("/v1/dispositivos/registrar", json=cuerpo, headers=cabeceras)

    assert primera.status_code == 200
    assert segunda.status_code == 200
    assert primera.json()["ya_existia"] is False
    assert segunda.json()["ya_existia"] is True
    assert primera.json()["id"] == segunda.json()["id"]


async def test_no_se_puede_secuestrar_el_dispositivo_de_otro(cliente, semilla, sesion):
    ajeno = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'Equipo del vendedor', 'activo', now())"
        ),
        {"d": ajeno, "u": semilla["vendedor"]},
    )
    await sesion.commit()

    token = await _token_admin(cliente)
    r = await cliente.post(
        "/v1/dispositivos/registrar",
        json={"id": str(ajeno), "etiqueta": "me lo apropio"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 409


async def test_un_usuario_un_equipo_activo(cliente, semilla):
    """Regla de negocio, sostenida por uq_dispositivo_activo_por_usuario."""
    token = await _token_admin(cliente)
    cabeceras = {"Authorization": f"Bearer {token}"}
    await cliente.post(
        "/v1/dispositivos/registrar",
        json={"id": str(uuid.uuid4()), "etiqueta": "primero"},
        headers=cabeceras,
    )
    segundo = await cliente.post(
        "/v1/dispositivos/registrar",
        json={"id": str(uuid.uuid4()), "etiqueta": "segundo"},
        headers=cabeceras,
    )
    assert segundo.status_code == 409
    assert "ya tiene un dispositivo activo" in segundo.json()["detail"]


async def test_ancho_de_impresora_invalido_se_rechaza(cliente, semilla):
    token = await _token_admin(cliente)
    r = await cliente.post(
        "/v1/dispositivos/registrar",
        json={"id": str(uuid.uuid4()), "etiqueta": "x", "impresora_ancho_mm": 72},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


async def test_asigna_un_rango_por_tipo_de_documento(cliente, semilla):
    token = await _token_admin(cliente)
    cabeceras = {"Authorization": f"Bearer {token}"}
    dispositivo_id = str(uuid.uuid4())
    await cliente.post(
        "/v1/dispositivos/registrar",
        json={"id": dispositivo_id, "etiqueta": "Moto G54"},
        headers=cabeceras,
    )

    r = await cliente.post(f"/v1/dispositivos/{dispositivo_id}/folios", headers=cabeceras)
    assert r.status_code == 200, r.text
    rangos = {x["documento_tipo"]: x for x in r.json()}
    assert set(rangos) == {"venta", "cobro", "merma", "no_drop"}
    assert rangos["venta"]["desde"] == 1
    assert rangos["venta"]["hasta"] == 1000


async def test_pedir_folios_dos_veces_devuelve_el_mismo_rango(cliente, semilla):
    """Mientras quede rango vigente no se entrega uno nuevo: pedirlo dos veces
    no debe quemar mil folios."""
    token = await _token_admin(cliente)
    cabeceras = {"Authorization": f"Bearer {token}"}
    dispositivo_id = str(uuid.uuid4())
    await cliente.post(
        "/v1/dispositivos/registrar",
        json={"id": dispositivo_id, "etiqueta": "Moto G54"},
        headers=cabeceras,
    )
    primera = await cliente.post(f"/v1/dispositivos/{dispositivo_id}/folios", headers=cabeceras)
    segunda = await cliente.post(f"/v1/dispositivos/{dispositivo_id}/folios", headers=cabeceras)
    assert primera.json() == segunda.json()


async def test_el_rango_nuevo_empieza_donde_termino_el_anterior(cliente, semilla, sesion):
    """El caso de la reinstalación: rango nuevo, sin tocar los folios ya
    impresos del anterior."""
    token = await _token_admin(cliente)
    cabeceras = {"Authorization": f"Bearer {token}"}
    dispositivo_id = str(uuid.uuid4())
    await cliente.post(
        "/v1/dispositivos/registrar",
        json={"id": dispositivo_id, "etiqueta": "Moto G54"},
        headers=cabeceras,
    )
    await cliente.post(f"/v1/dispositivos/{dispositivo_id}/folios", headers=cabeceras)

    await sesion.execute(
        text(
            "UPDATE folios_rangos SET agotado = true "
            "WHERE dispositivo_id = :d AND documento_tipo = 'venta'"
        ),
        {"d": uuid.UUID(dispositivo_id)},
    )
    await sesion.commit()

    r = await cliente.post(f"/v1/dispositivos/{dispositivo_id}/folios", headers=cabeceras)
    venta = next(x for x in r.json() if x["documento_tipo"] == "venta")
    assert venta["desde"] == 1001
    assert venta["hasta"] == 2000


async def test_la_base_impide_rangos_traslapados(sesion, semilla):
    """Defensa en profundidad: aunque un bug de la API lo intentara, el
    constraint EXCLUDE lo impide."""
    from sqlalchemy.exc import IntegrityError

    dispositivo_id = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'x', 'activo', now())"
        ),
        {"d": dispositivo_id, "u": semilla["vendedor"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO folios_rangos(dispositivo_id, documento_tipo, desde, hasta, "
            "consumido_hasta, asignado_en) VALUES (:d, 'venta', 1, 1000, 0, now())"
        ),
        {"d": dispositivo_id},
    )
    await sesion.commit()

    with pytest.raises(IntegrityError):
        await sesion.execute(
            text(
                "INSERT INTO folios_rangos(dispositivo_id, documento_tipo, desde, hasta, "
                "consumido_hasta, asignado_en) VALUES (:d, 'venta', 500, 1500, 0, now())"
            ),
            {"d": dispositivo_id},
        )
        await sesion.commit()
    await sesion.rollback()
