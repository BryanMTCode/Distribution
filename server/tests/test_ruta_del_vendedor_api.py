"""`/v1/vendedores/{id}/ruta`: la ruta de cada vendedor, día por día (ADR 0002 §121).

«Quiero poder ver las rutas de cada vendedor de cada día en la app del
administrador.»

1. **Cada día con sus tiendas**, de lunes a sábado con su cuenta, y las del día
   pedido en el orden de la ruta; las que no tienen ubicación, contadas.
2. **Solo las suyas**: las de sus rutas, activas, sin las de mayoreo ni las dadas
   de baja.
3. **Hoy dice cuáles ya visitó**, con venta o sin venta, y la que se le
   reprogramó para hoy aparece aunque no sea su día.
4. **Solo la oficina**: el vendedor no ve la ruta de otro por aquí.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR
from tests.test_vendedores_api import _cab

pytestmark = pytest.mark.asyncio

LUNES = date(2026, 10, 12)


async def _tienda(sesion, ruta, codigo, nombre, dias, *, orden=None, semana=None,
                  ubicada=False, **campos) -> uuid.UUID:
    cliente = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, calle, numero, colonia, "
            "  ruta_id, secuencia, lat, lng, estatus, mayoreo, visita_reprogramada, "
            "  creado_en, actualizado_en) "
            "VALUES (:id, :c, :n, 'CARNAVAL', '819', 'PLAYA SUR', :r, :o, :lat, :lng, "
            "        :estatus, :mayoreo, :reprogramada, now(), now())"
        ),
        {"id": cliente, "c": codigo, "n": nombre, "r": ruta, "o": orden,
         "lat": 23.2 if ubicada else None, "lng": -106.4 if ubicada else None,
         "estatus": campos.get("estatus", "activo"), "mayoreo": campos.get("mayoreo", False),
         "reprogramada": campos.get("reprogramada")},
    )
    for d in dias:
        await sesion.execute(
            text("INSERT INTO clientes_frecuencia (cliente_id, dia_semana, semana_del_mes) "
                 "VALUES (:c, :d, :s)"),
            {"c": cliente, "d": d, "s": semana},
        )
    return cliente


@pytest.fixture
async def ruta_de_juan(sesion, semilla) -> dict:
    """La ruta R04 de Juan: dos el lunes (en orden 2 y 1), una el martes solo la
    primera semana, y una del miércoles reprogramada para el lunes 12. Y lo que no
    es suyo: otra ruta, una de mayoreo y una dada de baja."""
    r = semilla["ruta"]
    ids = {
        "super": await _tienda(sesion, r, "100", "SUPER ATENAS", [1, 4], orden=2),
        "olas": await _tienda(sesion, r, "101", "OLAS NEGRAS", [1], orden=1, ubicada=True),
        "faro": await _tienda(sesion, r, "102", "EL FARO", [2], orden=1, semana=1),
        "reprogramada": await _tienda(sesion, r, "103", "LA PASADITA", [3], orden=1,
                                      reprogramada=LUNES),
        "baja": await _tienda(sesion, r, "104", "CERRADA", [1], estatus="baja"),
        "mayoreo": await _tienda(sesion, r, "105", "MAYORISTA", [1], mayoreo=True),
    }
    otra = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO rutas (id, codigo, nombre) VALUES (:r, 'R09', 'Ruta 9')"), {"r": otra}
    )
    ids["ajena"] = await _tienda(sesion, otra, "106", "DE OTRA RUTA", [1])
    await sesion.commit()
    return ids


async def _visitas_del_lunes(sesion, semilla, ids) -> None:
    """Le vendió a OLAS NEGRAS y SUPER ATENAS no le compró."""
    dispositivo = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
             "VALUES (:d, :u, 'Hyundai', 'activo', now())"),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local, cliente_id, "
            "  vendedor_id, ruta_id, almacen_id, tipo, subtotal, total, fecha_dispositivo, "
            "  fecha_operativa) "
            "VALUES (:id, :d, 1, 'VEND01-000001', :c, :v, :r, :a, 'contado', 350, 350, now(), :f)"
        ),
        {"id": uuid.uuid4(), "d": dispositivo, "c": ids["olas"], "v": semilla["vendedor"],
         "r": semilla["ruta"], "a": semilla["camion"], "f": LUNES},
    )
    await sesion.execute(
        text(
            "INSERT INTO no_drops (id, dispositivo_id, folio_consecutivo, cliente_id, vendedor_id, "
            "  ruta_id, motivo_codigo, lat, lng, fecha_dispositivo, fecha_operativa) "
            "VALUES (:id, :d, 1, :c, :v, :r, 'CERRADO', 23.2, -106.4, now(), :f)"
        ),
        {"id": uuid.uuid4(), "d": dispositivo, "c": ids["super"], "v": semilla["vendedor"],
         "r": semilla["ruta"], "f": LUNES},
    )
    await sesion.commit()


async def test_cada_dia_con_sus_tiendas_en_orden(cliente, sesion, semilla, ruta_de_juan):
    cab = await _cab(cliente)
    r = await cliente.get(
        f"/v1/vendedores/{semilla['vendedor']}/ruta?dia=1&fecha=2026-10-14", headers=cab
    )
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["vendedor"] == "Juan Pérez" and cuerpo["rutas"] == ["R04"]
    # De lunes a sábado con su cuenta; el domingo no, porque nadie lo trabaja.
    assert [(d["etiqueta"], d["tiendas"]) for d in cuerpo["dias"]] == [
        ("Lun", 2), ("Mar", 1), ("Mié", 1), ("Jue", 1), ("Vie", 0), ("Sáb", 0),
    ]
    # El lunes, en el orden de la ruta, solo las suyas y activas.
    assert [t["codigo"] for t in cuerpo["tiendas"]] == ["101", "100"]
    primera = cuerpo["tiendas"][0]
    assert primera["nombre"] == "OLAS NEGRAS"
    assert primera["direccion"] == "CARNAVAL 819, PLAYA SUR"
    assert primera["con_ubicacion"] and not cuerpo["tiendas"][1]["con_ubicacion"]
    assert cuerpo["sin_ubicacion"] == 1
    # No es hoy (es miércoles 14): nada dice si se visitó.
    assert not cuerpo["es_hoy"] and {t["visita"] for t in cuerpo["tiendas"]} == {None}

    martes = (await cliente.get(
        f"/v1/vendedores/{semilla['vendedor']}/ruta?dia=2&fecha=2026-10-14", headers=cab
    )).json()
    assert [(t["codigo"], t["semanas"]) for t in martes["tiendas"]] == [("102", [1])]


async def test_hoy_dice_cuales_ya_visito(cliente, sesion, semilla, ruta_de_juan):
    await _visitas_del_lunes(sesion, semilla, ruta_de_juan)
    cab = await _cab(cliente)
    # Sin `dia`, el de la fecha: el lunes 12.
    cuerpo = (await cliente.get(
        f"/v1/vendedores/{semilla['vendedor']}/ruta?fecha=2026-10-12", headers=cab
    )).json()
    assert cuerpo["dia"] == 1 and cuerpo["es_hoy"]
    por_codigo = {t["codigo"]: t for t in cuerpo["tiendas"]}
    assert (por_codigo["101"]["visita"], por_codigo["101"]["importe"]) == ("venta", "350.00")
    assert por_codigo["100"]["visita"] == "no_venta"
    # La del miércoles reprogramada para hoy, al final y marcada.
    assert por_codigo["103"]["reprogramada"] and por_codigo["103"]["visita"] is None
    assert list(por_codigo) == ["101", "100", "103"]


async def test_solo_la_oficina_y_un_dia_que_existe(cliente, sesion, semilla, ruta_de_juan):
    dispositivo = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
             "VALUES (:d,:u,'Moto G54','activo',now())"),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.commit()
    r = await cliente.post("/v1/auth/login", json={
        "codigo": "VEND01", "password": PASSWORD_VENDEDOR, "dispositivo_id": str(dispositivo),
    })
    cab_vendedor = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = await cliente.get(f"/v1/vendedores/{semilla['vendedor']}/ruta", headers=cab_vendedor)
    assert r.status_code == 403

    cab = await _cab(cliente)
    r = await cliente.get(f"/v1/vendedores/{semilla['vendedor']}/ruta?dia=9", headers=cab)
    assert r.status_code == 422
    r = await cliente.get(f"/v1/vendedores/{uuid.uuid4()}/ruta", headers=cab)
    assert r.status_code == 404
