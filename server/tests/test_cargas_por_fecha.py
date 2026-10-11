"""Las cargas por fecha, en el panel y en la app (ADR 0002 §102).

«Que las cargas también se puedan ver por fecha en ambos lugares, para ver qué
cargas se hicieron.»

1. **El panel** tiene «Abiertas» y «Por fecha»; por fecha usa el selector de
   periodo del tablero y agrupa por día, con cuántas cargas y cuántas piezas.
   Los filtros de antes («De hoy», «Todas») siguen sirviendo como periodos.
2. **La API de la app** da las de un periodo en cualquier estado; sin periodo
   siguen siendo las abiertas.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from tests.conftest import solo_texto
from tests.test_cargas_api import _cab
from tests.test_panel_cargas import _entrar

pytestmark = pytest.mark.asyncio

HOY = date.today()
SABADO = HOY - timedelta(days=3)
VIERNES = HOY - timedelta(days=4)


async def _carga(sesion, semilla, *, folio: str, dia: date, estado: str, piezas: int):
    carga = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id, "
            "                    vendedor_id, ruta_id, fecha_operativa, estado) "
            "VALUES (:id, :folio, :b, :c, :v, :r, :d, :e)"
        ),
        {"id": carga, "folio": folio, "b": semilla["bodega"], "c": semilla["camion"],
         "v": semilla["vendedor"], "r": semilla["ruta"], "d": dia, "e": estado},
    )
    producto = (
        await sesion.execute(text("SELECT id FROM productos ORDER BY sku LIMIT 1"))
    ).scalar_one_or_none()
    if producto is None:
        producto = uuid.uuid4()
        await sesion.execute(
            text("INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva) "
                 "VALUES (:p, 'SOPA', 'Sopa', 'PZA', 0)"),
            {"p": producto},
        )
    await sesion.execute(
        text("INSERT INTO carga_detalle (carga_id, producto_id, cantidad) "
             "VALUES (:c, :p, :n)"),
        {"c": carga, "p": producto, "n": piezas},
    )
    await sesion.commit()


@pytest.fixture
async def tres_dias(sesion, semilla):
    # El sábado, dos (una cancelada); el viernes, una ya liquidada; hoy, una
    # abierta.
    await _carga(sesion, semilla, folio="CA-000101", dia=SABADO, estado="liquidada", piezas=120)
    await _carga(sesion, semilla, folio="CA-000102", dia=SABADO, estado="cancelada", piezas=5)
    await _carga(sesion, semilla, folio="CA-000100", dia=VIERNES, estado="liquidada", piezas=80)
    await _carga(sesion, semilla, folio="CA-000103", dia=HOY, estado="borrador", piezas=10)


async def test_el_panel_las_da_por_dia(cliente, semilla, tres_dias):  # noqa: ARG001
    await _entrar(cliente)
    # Abiertas: solo la de hoy, como siempre.
    plano = solo_texto(await cliente.get("/panel/cargas"))
    assert "CA-000103" in plano and "CA-000101" not in plano

    r = await cliente.get(
        f"/panel/cargas?filtro=fecha&periodo=rango&desde={VIERNES}&hasta={SABADO}"
    )
    html, plano = r.text, solo_texto(r)
    assert f'id="dia_{SABADO.isoformat()}"' in html
    assert f'id="dia_{VIERNES.isoformat()}"' in html
    # El sábado va primero, con lo que se cargó; la cancelada, aparte.
    assert html.index(f'id="dia_{SABADO.isoformat()}"') < html.index("CA-000101")
    assert html.index("CA-000101") < html.index(f'id="dia_{VIERNES.isoformat()}"')
    assert "· 1 carga(s) · 120 piezas" in plano
    assert "2 carga(s) en 2 día(s), 200 piezas." in plano
    assert "CA-000103" not in plano
    assert html.index('id="cargas_canceladas"') < html.index("CA-000102")

    # Un solo día.
    plano = solo_texto(
        await cliente.get(f"/panel/cargas?filtro=fecha&periodo=rango&desde={VIERNES}")
    )
    assert "CA-000100" in plano and "CA-000101" not in plano

    # Los filtros de antes siguen sirviendo.
    plano = solo_texto(await cliente.get("/panel/cargas?filtro=hoy"))
    assert "CA-000103" in plano and "CA-000101" not in plano
    plano = solo_texto(await cliente.get("/panel/cargas?filtro=todas"))
    assert "CA-000100" in plano and "CA-000103" in plano

    # Un día sin cargas lo dice.
    vacio = HOY - timedelta(days=30)
    plano = solo_texto(
        await cliente.get(f"/panel/cargas?filtro=fecha&periodo=rango&desde={vacio}")
    )
    assert "Ninguna carga el" in plano


async def test_la_app_las_pide_por_periodo(cliente, semilla, tres_dias):  # noqa: ARG001
    cab = await _cab(cliente)
    abiertas = (await cliente.get("/v1/cargas", headers=cab)).json()
    assert [c["folio"] for c in abiertas] == ["CA-000103"]

    del_sabado = (
        await cliente.get(
            "/v1/cargas",
            params={"periodo": "rango", "desde": SABADO.isoformat(),
                    "hasta": SABADO.isoformat()},
            headers=cab,
        )
    ).json()
    assert [(c["folio"], c["estado"]) for c in del_sabado] == [
        ("CA-000102", "cancelada"), ("CA-000101", "liquidada")
    ]
    assert del_sabado[1]["piezas"] == "120.000"

    todo = (await cliente.get("/v1/cargas", params={"periodo": "todo"}, headers=cab)).json()
    assert [c["folio"] for c in todo] == ["CA-000103", "CA-000102", "CA-000101", "CA-000100"]
