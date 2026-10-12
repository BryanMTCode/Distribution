"""Los objetivos en la app de la oficina: solo para ver (ADR 0002 §104, §115).

«Quiero que todo se sume.» El único objetivo que se captura es el diario de
cada vendedor, en el dashboard. La app ve lo que suma:

1. **Hoy, esta semana y este mes**, de toda la empresa (la suma) y de cada
   vendedor con objetivo, con lo vendido y lo esperado a la fecha.
2. **No se captura desde la app**: `puede_fijar` va en falso, y una app
   anterior que intente poner o quitar uno recibe dónde se pone.
3. **Un vendedor no los ve.**
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.test_panel_objetivos import _diario
from tests.test_tablero_api import _cab

pytestmark = pytest.mark.asyncio


async def _venta(sesion, semilla, *, total: str, dia: date) -> None:
    cliente = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, creado_en, "
             "actualizado_en) VALUES (:c, :cod, 'Tienda', :r, now(), now())"),
        {"c": cliente, "cod": f"C-{str(cliente)[:6]}", "r": semilla["ruta"]},
    )
    dispositivo = (
        await sesion.execute(text("SELECT id FROM dispositivos LIMIT 1"))
    ).scalar_one_or_none()
    if dispositivo is None:
        dispositivo = uuid.uuid4()
        await sesion.execute(
            text("INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
                 "VALUES (:d, :u, 'Moto', 'activo', now())"),
            {"d": dispositivo, "u": semilla["vendedor"]},
        )
    folio = abs(hash((total, dia))) % 100000 + 1
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, ruta_id, almacen_id, tipo,
                                forma_pago, subtotal, total, fecha_dispositivo, fecha_operativa)
            VALUES (:v, :d, :f, :fl, :c, :u, :r, :a, 'contado', 'efectivo', :t, :t, now(), :dia)
            """
        ),
        {"v": uuid.uuid4(), "d": dispositivo, "f": folio, "fl": f"VEND01-{folio:06d}",
         "c": cliente, "u": semilla["vendedor"], "r": semilla["ruta"], "a": semilla["camion"],
         "t": total, "dia": dia},
    )
    await sesion.commit()


async def _cab_vendedor(cliente, sesion, semilla) -> dict:
    from tests.conftest import PASSWORD_VENDEDOR

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
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_la_app_ve_lo_que_suma_hoy_la_semana_y_el_mes(cliente, sesion, semilla):
    hoy = date.today()
    await _diario(sesion, semilla["ruta"], "1000", desde=hoy - timedelta(days=40))
    await _venta(sesion, semilla, total="1500.00", dia=hoy)
    cab = await _cab(cliente)
    cuerpo = (await cliente.get("/v1/objetivos", headers=cab)).json()
    assert cuerpo["puede_fijar"] is False

    empresa = [o for o in cuerpo["objetivos"] if o["ruta"] is None]
    de_ruta = [o for o in cuerpo["objetivos"] if o["ruta"] == "R04 · Ruta 4"]
    # Primero la empresa, luego cada vendedor: hoy, la semana y el mes.
    assert cuerpo["objetivos"][:3] == empresa
    assert [o["id"].rsplit("-", 1)[1] for o in de_ruta] == ["hoy", "semana", "mes"]
    hoy_ruta, semana, mes = de_ruta
    assert (hoy_ruta["objetivo_venta"], hoy_ruta["venta"], hoy_ruta["logrado"]) == (
        "1000.00", "1500.00", "150.0"
    )
    assert semana["objetivo_venta"] == "7000.00" and semana["dias"] == 7
    assert mes["desde"] == hoy.replace(day=1).isoformat()
    assert mes["nota"] == "Este mes: la suma de sus objetivos diarios."
    # Toda la empresa es la suma: con un solo vendedor, lo mismo.
    assert [o["objetivo_venta"] for o in empresa] == [o["objetivo_venta"] for o in de_ruta]
    assert empresa[0]["nota"] == "Hoy: la suma de todos los vendedores."


async def test_ya_no_se_captura_desde_la_app(cliente, sesion, semilla):
    """Una app anterior todavía trae «Nuevo objetivo» y «Quitar»: se le dice
    dónde se pone, y nada se guarda."""
    hoy = date.today().isoformat()
    cab = await _cab(cliente)
    r = await cliente.post(
        "/v1/objetivos",
        json={"ruta_id": str(semilla["ruta"]), "desde": hoy, "hasta": hoy,
              "objetivo_venta": "3500", "por_dia": True},
        headers=cab,
    )
    assert r.status_code == 422
    assert "Objetivos → «Objetivo diario de cada vendedor»" in r.json()["detail"]
    assert (await cliente.post("/v1/objetivos/o1/borrar", headers=cab)).status_code == 422
    for tabla in ("objetivos", "objetivos_ruta", "objetivos_diarios"):
        assert (await sesion.execute(text(f"SELECT count(*) FROM {tabla}"))).scalar_one() == 0  # noqa: S608


async def test_un_vendedor_no_ve_los_objetivos(cliente, sesion, semilla):
    cab = await _cab_vendedor(cliente, sesion, semilla)
    assert (await cliente.get("/v1/objetivos", headers=cab)).status_code == 403


async def test_la_barra_por_hoy_la_semana_o_el_mes(cliente, sesion, semilla):
    """La barra del Tablero de la app por la vista que se escoja (§116)."""
    hoy = date.today()
    await _diario(sesion, semilla["ruta"], "1000", desde=hoy - timedelta(days=40))
    await _venta(sesion, semilla, total="1500.00", dia=hoy)
    cab = await _cab(cliente)

    de_hoy = (await cliente.get("/v1/objetivos/avance?vista=hoy", headers=cab)).json()
    assert (de_hoy["desde"], de_hoy["hasta"]) == (hoy.isoformat(), hoy.isoformat())
    [ruta] = de_hoy["rutas"]
    assert (ruta["ruta"], ruta["objetivo"], ruta["venta"], ruta["logrado"]) == (
        "R04 · Ruta 4", "1000.00", "1500.00", "150.0"
    )
    assert (ruta["semaforo"], ruta["falta"]) == ("adelante", "0.00")
    assert de_hoy["empresa"]["objetivo"] == "1000.00" and de_hoy["empresa"]["ruta"] is None

    semana = (await cliente.get("/v1/objetivos/avance?vista=semana", headers=cab)).json()
    assert semana["rutas"][0]["objetivo"] == "7000.00"
    assert semana["rutas"][0]["esperado"] == str(
        (Decimal(1000 * (hoy.weekday() + 1)) * 100 / 7000).quantize(Decimal("0.1"))
    )
    mes = (await cliente.get("/v1/objetivos/avance?vista=mes", headers=cab)).json()
    assert mes["desde"] == hoy.replace(day=1).isoformat()

    # La semana de otro día: la de ese día, no la de hoy.
    hace_tres_semanas = hoy - timedelta(days=21)
    otra = (
        await cliente.get(
            f"/v1/objetivos/avance?vista=semana&fecha={hace_tres_semanas.isoformat()}",
            headers=cab,
        )
    ).json()
    lunes = hace_tres_semanas - timedelta(days=hace_tres_semanas.weekday())
    assert otra["desde"] == lunes.isoformat()
    assert (await cliente.get("/v1/objetivos/avance?vista=año", headers=cab)).status_code == 422
