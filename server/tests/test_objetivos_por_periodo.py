"""Objetivos de cualquier periodo, en la app y en el panel (ADR 0002 §104).

«Quiero poder agregar los objetivos en la app; los objetivos pueden ser por el
periodo que yo decida, incluso por día.»

1. **Un día, una semana, un rango**; de una ruta o de toda la empresa. El avance
   sale de las ventas del rango, contra lo esperado a la fecha.
2. **Un mes completo de una ruta** es su objetivo del mes: el de la barra del
   tablero.
3. **Lo imposible se dice**: al revés se endereza, más de un año no.
4. **Gerencia y admin los fijan**; un vendedor no los ve.
5. **El panel** los muestra y los da de alta igual.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from tests.conftest import csrf_del_panel, solo_texto
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


async def test_un_objetivo_de_hoy_con_su_avance(cliente, sesion, semilla):
    hoy = date.today()
    await _venta(sesion, semilla, total="1500.00", dia=hoy)
    await _venta(sesion, semilla, total="900.00", dia=hoy - timedelta(days=1))  # no cuenta
    cab = await _cab(cliente)
    r = await cliente.post(
        "/v1/objetivos",
        json={"ruta_id": str(semilla["ruta"]), "desde": hoy.isoformat(),
              "hasta": hoy.isoformat(), "objetivo_venta": "3000", "nota": "Feria"},
        headers=cab,
    )
    assert r.status_code == 201, r.text
    objetivo = next(o for o in r.json()["objetivos"] if o["clase"] == "periodo")
    assert (objetivo["desde"], objetivo["hasta"], objetivo["dias"]) == (
        hoy.isoformat(), hoy.isoformat(), 1
    )
    assert objetivo["ruta"] == "R04 · Ruta 4"
    assert (objetivo["venta"], objetivo["objetivo_venta"]) == ("1500.00", "3000.00")
    assert (objetivo["logrado"], objetivo["esperado"]) == ("50.0", "100.0")
    assert objetivo["estado"] == "en_curso" and objetivo["semaforo"] == "atras"
    assert objetivo["visitas"] == 1 and objetivo["nota"] == "Feria"


async def test_de_toda_la_empresa_y_por_empezar(cliente, sesion, semilla):
    hoy = date.today()
    await _venta(sesion, semilla, total="800.00", dia=hoy)
    cab = await _cab(cliente)
    semana = hoy - timedelta(days=hoy.weekday())
    r = await cliente.post(
        "/v1/objetivos",
        json={"desde": semana.isoformat(), "hasta": (semana + timedelta(days=6)).isoformat(),
              "objetivo_venta": "10000"},
        headers=cab,
    )
    empresa = next(o for o in r.json()["objetivos"] if o["ruta"] is None)
    assert empresa["venta"] == "800.00" and empresa["dias"] == 7
    r = await cliente.post(
        "/v1/objetivos",
        json={"desde": (hoy + timedelta(days=40)).isoformat(),
              "hasta": (hoy + timedelta(days=40)).isoformat(), "objetivo_venta": "500"},
        headers=cab,
    )
    futuro = next(o for o in r.json()["objetivos"] if o["estado"] == "por_empezar")
    assert (futuro["venta"], futuro["esperado"]) == ("0.00", "0.0")


async def test_un_mes_completo_de_una_ruta_es_su_objetivo_del_mes(cliente, sesion, semilla):
    hoy = date.today()
    cab = await _cab(cliente)
    inicio = hoy.replace(day=1)
    fin = (inicio + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    r = await cliente.post(
        "/v1/objetivos",
        json={"ruta_id": str(semilla["ruta"]), "desde": inicio.isoformat(),
              "hasta": fin.isoformat(), "objetivo_venta": "100000"},
        headers=cab,
    )
    assert r.status_code == 201
    mensual = (
        await sesion.execute(text("SELECT objetivo_venta FROM objetivos_ruta"))
    ).scalar_one()
    assert str(mensual) == "100000.00"
    assert (await sesion.execute(text("SELECT count(*) FROM objetivos"))).scalar_one() == 0
    del_mes = next(o for o in r.json()["objetivos"] if o["clase"] == "mes")
    # Y se quita igual que los otros.
    r = await cliente.post(f"/v1/objetivos/{del_mes['id']}/borrar", headers=cab)
    assert r.status_code == 200
    assert (await sesion.execute(text("SELECT count(*) FROM objetivos_ruta"))).scalar_one() == 0


async def test_lo_imposible_se_dice(cliente, semilla):
    hoy = date.today()
    cab = await _cab(cliente)
    r = await cliente.post(
        "/v1/objetivos",
        json={"desde": "2026-01-01", "hasta": "2027-06-30", "objetivo_venta": "1"},
        headers=cab,
    )
    assert r.status_code == 422 and "a lo más un año" in r.json()["detail"]
    # Al revés se endereza.
    r = await cliente.post(
        "/v1/objetivos",
        json={"desde": hoy.isoformat(), "hasta": (hoy - timedelta(days=2)).isoformat(),
              "objetivo_venta": "100"},
        headers=cab,
    )
    o = next(x for x in r.json()["objetivos"] if x["clase"] == "periodo")
    assert (o["desde"], o["hasta"]) == ((hoy - timedelta(days=2)).isoformat(), hoy.isoformat())


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


async def test_un_vendedor_no_ve_los_objetivos(cliente, sesion, semilla):
    cab = await _cab_vendedor(cliente, sesion, semilla)
    assert (await cliente.get("/v1/objetivos", headers=cab)).status_code == 403


async def test_el_panel_los_muestra_y_los_da_de_alta(cliente, sesion, semilla):
    hoy = date.today()
    from tests.test_panel_objetivos import _entrar

    await _entrar(cliente)
    # El Tablero lleva a las metas, y la página tiene el botón arriba.
    assert 'href="/panel/objetivos#diario"' in (await cliente.get("/panel")).text
    r = await cliente.get("/panel/objetivos")
    assert 'id="forma_por_periodo"' in r.text and 'id="ir_a_nueva_meta"' in r.text
    r = await cliente.post(
        "/panel/objetivos/periodo",
        data={"csrf": csrf_del_panel(cliente, r), "ruta_id": "", "desde": hoy.isoformat(),
              "hasta": hoy.isoformat(), "objetivo_venta": "2,500", "objetivo_visitas": "12"},
        follow_redirects=True,
    )
    plano = solo_texto(r)
    assert "Objetivo guardado." in plano
    assert "Toda la empresa $2,500.00 12 visitas" in plano
    objetivo = (await sesion.execute(text("SELECT id FROM objetivos"))).scalar_one()
    r = await cliente.post(
        f"/panel/objetivos/periodo/{objetivo}/borrar",
        data={"csrf": csrf_del_panel(cliente, r)},
        follow_redirects=True,
    )
    assert "Objetivo quitado." in solo_texto(r)


async def test_cada_dia_ya_no_se_pone_desde_la_app(cliente, sesion, semilla):
    """«Cada día» (§109) se quitó: el objetivo diario fijo del panel lo
    reemplaza (§110, §111). Una app +34 sin actualizar lo sigue mandando, y se
    le dice qué hacer en vez de guardar en silencio UN objetivo para la semana."""
    hoy = date.today()
    cab = await _cab(cliente)
    r = await cliente.post(
        "/v1/objetivos",
        json={"ruta_id": str(semilla["ruta"]), "desde": hoy.isoformat(),
              "hasta": (hoy + timedelta(days=6)).isoformat(), "objetivo_venta": "3500",
              "por_dia": True},
        headers=cab,
    )
    assert r.status_code == 422
    assert "Objetivos → Objetivo diario" in r.json()["detail"]
    assert "Actualiza la app." in r.json()["detail"]
    assert (await sesion.execute(text("SELECT count(*) FROM objetivos"))).scalar_one() == 0
