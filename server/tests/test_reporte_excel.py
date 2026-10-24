"""El reporte en Excel para el administrador (ADR 0002 §127).

«Quiero generar un reporte en Excel para la persona que usa el perfil de
administrador; él no sabe usar mucho sistemas.» Con las cuatro cosas que eligió
—ventas y efectivo, inventario, cargas y mermas, y la cuenta de cada vendedor—
del periodo que se elija al bajarlo.

1. **Se baja del dashboard** como un .xlsx con su nombre, con una hoja de
   Resumen y una por tema, y dice las mismas cifras que el dashboard.
2. **Lo eliminado no cuenta** en ninguna hoja.
3. **Quién**: la oficina —quien ve el Tablero: administrador, gerencia y
   supervisor—; sin entrar al panel, nadie.
4. **Todo es en efectivo** (§170): lo vendido no se repite como «en efectivo»,
   salvo que en el periodo haya una transferencia de un teléfono viejo.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import load_workbook
from sqlalchemy import text

from tests.test_cierre_del_vendedor import _cerrar, _corte, dia  # noqa: F401
from tests.test_cortes_api import _cab, _usuario
from tests.test_operaciones import _anular
from tests.test_panel_liquidacion import _entrar

pytestmark = pytest.mark.asyncio

HOJAS = ["Resumen", "Ventas y efectivo", "Día por día", "Inventario",
         "Cargas y mermas", "Cuenta de vendedores"]


async def _bajar(cliente, periodo="hoy"):
    r = await cliente.get(f"/panel/reporte/excel?periodo={periodo}")
    assert r.status_code == 200, r.text
    return r, load_workbook(io.BytesIO(r.content))


def _renglon(hoja, primera: str) -> list:
    """Los valores del renglón cuya primera columna dice `primera`."""
    for fila in hoja.iter_rows(values_only=True):
        if fila and fila[0] == primera:
            return list(fila)
    raise AssertionError(f"no está «{primera}» en {hoja.title}")


def _con(hoja, texto: str) -> list:
    """El primer renglón que tiene `texto` en alguna celda."""
    for fila in hoja.iter_rows(values_only=True):
        if any(c == texto for c in fila):
            return list(fila)
    raise AssertionError(f"no está «{texto}» en {hoja.title}")


async def test_el_reporte_trae_sus_hojas_con_las_cifras_del_dashboard(
    cliente, sesion, semilla, dia  # noqa: F811
):
    # El vendedor declara $2,200 de $2,250 y el gerente lo cierra: faltan $50.
    corte_id = await _corte(sesion, dia, semilla, efectivo="2200.00")
    await _cerrar(cliente, corte_id, await _cab(cliente, "GER01"))

    await _entrar(cliente)
    pagina = await cliente.get("/panel/reporte?periodo=hoy")
    assert pagina.status_code == 200 and 'id="bajar_reporte"' in pagina.text
    tablero = await cliente.get("/panel")
    assert 'id="ir_al_reporte"' in tablero.text

    r, libro = await _bajar(cliente)
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    hoy = date.today()
    assert f'filename="reporte_{hoy:%Y-%m-%d}.xlsx"' in r.headers["content-disposition"]
    assert libro.sheetnames == HOJAS

    resumen = libro["Resumen"]
    assert resumen["A1"].value == "Reporte de Distribuciones SE"
    assert "hecho el" in resumen["A2"].value and "por Bryan" in resumen["A2"].value
    assert _renglon(resumen, "Ventas de los vendedores")[1] == 2250
    assert _renglon(resumen, "Efectivo que entregaron")[1] == 2200
    assert _renglon(resumen, "Diferencia en los cortes")[1] == -50
    assert _renglon(resumen, "Lo que deben los vendedores (hoy)")[1] == 50
    assert _renglon(resumen, "Cargas a los camiones")[1] == 240

    ventas = libro["Ventas y efectivo"]
    fila = _con(ventas, "Juan Pérez")
    assert fila[2:5] == [1, 180, 2250]

    # Lo que cargó y vendió cada vendedor cada día, artículo por artículo (§177).
    from app.infra.reporte_excel import _con_dia

    dia_a_dia = libro["Día por día"]
    assert _con(dia_a_dia, f"{_con_dia(hoy)} · Juan Pérez")
    atun = _con(dia_a_dia, "ATUN-140")
    assert atun[2:7] == ["Atún en agua 140 g", 240, 180, 0, 2250]
    corte = _con(ventas, "Faltaron $50.00")
    assert corte[3:6] == [2250, 2200, -50]

    inventario = libro["Inventario"]
    # La bodega primero, con sus 760 del atún; el camión, con los 60 que le quedan.
    titulos = [f[0] for f in inventario.iter_rows(values_only=True) if f and f[0]]
    assert titulos.index("Bodega") < titulos.index("Camión 01 · Juan Pérez")
    atun = [f for f in inventario.iter_rows(values_only=True) if f[1] == "ATUN-140"]
    assert [f[3] for f in atun] == [760, 60]

    cargas = libro["Cargas y mermas"]
    carga = _con(cargas, "CG-TEST")
    assert carga[6:8] == [240, "Cortada"]

    cuentas = libro["Cuenta de vendedores"]
    assert _con(cuentas, "Juan Pérez")[4] == 50
    assert _con(cuentas, "Faltante de efectivo")[5] == 50

    # Con todos los encabezados en español, sin claves internas.
    textos = {c for h in libro for f in h.iter_rows(values_only=True) for c in f if c}
    assert not {"confirmada", "liquidada", "faltante_efectivo", "cargo"} & textos


async def test_lo_eliminado_no_cuenta(cliente, sesion, semilla, dia):  # noqa: F811
    await _entrar(cliente)
    await _anular(cliente, "venta", dia["venta"])
    _, libro = await _bajar(cliente)
    assert _renglon(libro["Resumen"], "Ventas de los vendedores")[1] == 0
    assert "No hubo en este periodo." in [
        f[0] for f in libro["Ventas y efectivo"].iter_rows(values_only=True)
    ]
    # El inventario es el de hoy: la venta eliminada regresó las 180 al camión.
    atun = [f for f in libro["Inventario"].iter_rows(values_only=True) if f[1] == "ATUN-140"]
    assert [Decimal(str(f[3])) for f in atun] == [760, 240]


async def test_lo_baja_la_oficina_y_sin_entrar_nadie(cliente, sesion, semilla, dia):  # noqa: F811
    # Sin entrar al panel, no hay archivo.
    r = await cliente.get("/panel/reporte/excel?periodo=hoy", follow_redirects=False)
    assert r.status_code != 200 and not r.content.startswith(b"PK")

    await _entrar(cliente, "GER01")
    _, libro = await _bajar(cliente, "semana")
    assert libro.sheetnames == HOJAS

    await _usuario(sesion, semilla, "SUP01", "supervisor")
    await _entrar(cliente, "SUP01")
    _, libro = await _bajar(cliente, "hoy")
    assert libro.sheetnames == HOJAS


# ---------------------------------------------------------------------------
# Todo es en efectivo (ADR 0002 §170)
# ---------------------------------------------------------------------------


def _textos(hoja) -> set:
    return {c for f in hoja.iter_rows(values_only=True) for c in f if c}


async def test_todo_en_efectivo_no_repite_lo_vendido(cliente, sesion, semilla, dia):  # noqa: F811
    """«Revisa que todos los pagos son solo en efectivo, incluido en los reportes
    de Excel.» El Resumen decía «Ventas de los vendedores» y abajo «De eso, en
    efectivo» con la misma cifra, como si hubiera otra forma de pago; y la hoja de
    ventas repetía «Importe» en una columna «En efectivo»."""
    await _entrar(cliente)
    _, libro = await _bajar(cliente)

    resumen = libro["Resumen"]
    vendido = _renglon(resumen, "Ventas de los vendedores")
    assert vendido[1] == 2250
    assert "todas en efectivo" in vendido[2]
    assert "De eso, en efectivo" not in _textos(resumen)

    ventas = libro["Ventas y efectivo"]
    assert "En efectivo" not in _textos(ventas)
    assert "Todas las ventas del periodo fueron en efectivo." in _textos(ventas)
    assert _con(ventas, "Juan Pérez")[2:5] == [1, 180, 2250]


async def test_una_transferencia_de_un_telefono_viejo_lo_vuelve_a_separar(
    cliente, sesion, semilla, dia  # noqa: F811
):
    """El servidor la sigue aceptando —la mercancía ya salió— (§81, enmienda):
    entonces el efectivo ya no es lo vendido, y el reporte lo dice."""
    await sesion.execute(
        text(
            "UPDATE ventas SET forma_pago = 'transferencia', pago_estado = 'por_confirmar'"
            " WHERE id = :v"
        ),
        {"v": dia["venta"]},
    )
    await sesion.commit()
    await _entrar(cliente)
    _, libro = await _bajar(cliente)

    resumen = libro["Resumen"]
    assert _renglon(resumen, "Ventas de los vendedores")[1] == 2250
    assert _renglon(resumen, "De eso, en efectivo")[1] == 0
    ventas = libro["Ventas y efectivo"]
    assert "En efectivo" in _textos(ventas)
    assert _con(ventas, "Juan Pérez")[2:6] == [1, 180, 2250, 0]
