"""Lo que salió y lo que entró, cada uno en su lugar (ADR 0002 §195).

«Explícame estas dos operaciones»: un cambio físico y una devolución del cliente.
En el detalle de cada una, la tabla de lo que se movería al eliminarla iba arriba,
sola, como «Lo que regresa», y se leía como lo que había pasado —al revés—. Y en
el Excel, la tabla «Mermas y devoluciones» sumaba las dos aunque una sale del
camión y la otra entra.

1. **La tabla va dentro de «Si la eliminas»**, con «Sale de» y «Va a».
2. **En el Excel, lo que salió y lo que entró van en columnas aparte**, con su
   total cada una, y en el Resumen en dos renglones.
"""

from __future__ import annotations

import uuid

import pytest

from app.infra.sync.manejadores import obtener_manejador
from tests.conftest import solo_texto
from tests.test_cierre_del_vendedor import _ctx, dia  # noqa: F401
from tests.test_panel_liquidacion import _entrar
from tests.test_reporte_excel import _bajar, _renglon

pytestmark = pytest.mark.asyncio


async def _del_telefono(sesion, semilla, dia, *, tipo: str, piezas: str, n: int):  # noqa: F811
    """Como la sube el teléfono: el cambio físico saca del camión el producto
    bueno; la devolución del cliente mete al camión lo que la tienda regresó."""
    merma_id = uuid.uuid4()
    await obtener_manejador("merma.crear")(
        sesion,
        _ctx(dia, semilla),
        merma_id,
        {
            "folio_consecutivo": n,
            "folio_local": f"VEND01-M{n:05d}",
            "tipo": tipo,
            "motivo_codigo": "CADUCADO",
            "fecha_dispositivo": f"{dia['dia'].isoformat()}T17:42:03.250Z",
            "fecha_operativa": dia["dia"].isoformat(),
            "cliente_id": str(dia["cliente"]),
            "detalle": [{"producto_id": str(dia["producto"]), "cantidad_base": piezas}],
        },
    )
    await sesion.commit()
    return merma_id


async def test_lo_que_se_moveria_al_eliminar_va_dentro_de_si_la_eliminas(
    cliente, sesion, semilla, dia  # noqa: F811
):
    devolucion = await _del_telefono(
        sesion, semilla, dia, tipo="devolucion_cliente", piezas="12.000", n=1
    )
    cambio = await _del_telefono(sesion, semilla, dia, tipo="cambio", piezas="4.000", n=2)
    await _entrar(cliente)

    pagina = await cliente.get(f"/panel/operaciones/devolucion/{devolucion}")
    assert pagina.status_code == 200, pagina.text
    plano = solo_texto(pagina)
    assert "Lo que regresa" not in plano
    # Primero lo que va a pasar, y la tabla ahí mismo: no sola, arriba.
    assert pagina.text.index('id="lo_que_va_a_pasar"') < pagina.text.index(
        'id="renglones_operacion"'
    )
    assert "La mercancía se movería así, al revés de como la movió esta operación" in plano
    # Las 12 entraron al camión: al eliminarla, salen de él.
    assert "Producto Piezas Sale de Va a Atún en agua 140 g 12 Camión 01 fuera del sistema" in plano

    pagina = await cliente.get(f"/panel/operaciones/cambio/{cambio}")
    plano = solo_texto(pagina)
    # Las 4 buenas salieron del camión: al eliminarlo, regresan a él.
    assert "Atún en agua 140 g 4 fuera del sistema Camión 01" in plano
    assert "Las piezas regresan a Camión 01" in plano


async def test_en_el_excel_lo_que_salio_y_lo_que_entro_van_aparte(
    cliente, sesion, semilla, dia  # noqa: F811
):
    await _del_telefono(sesion, semilla, dia, tipo="devolucion_cliente", piezas="12.000", n=1)
    await _del_telefono(sesion, semilla, dia, tipo="cambio", piezas="4.000", n=2)
    await _entrar(cliente)
    _, libro = await _bajar(cliente)

    hoja = libro["Cargas y mermas"]
    filas = [list(f) for f in hoja.iter_rows(values_only=True)]
    titulo = next(i for i, f in enumerate(filas) if f[0] == "Mermas y devoluciones")
    encabezado, *renglones = filas[titulo + 1:]
    assert encabezado[6:8] == ["Salió (piezas)", "Entró (piezas)"]
    por_folio = {f[1]: f for f in renglones if f[1]}
    assert por_folio["VEND01-M00002"][3] == "Cambio físico"
    assert por_folio["VEND01-M00002"][6:8] == [4, None]
    assert por_folio["VEND01-M00001"][3] == "Devolución del cliente"
    assert por_folio["VEND01-M00001"][6:8] == [None, 12]
    total = next(f for f in renglones if f[0] == "Total")
    assert total[6:8] == [4, 12]
    assert any(
        f[0] and str(f[0]).startswith("Salió: la merma del camión") for f in renglones
    )

    resumen = libro["Resumen"]
    salieron = _renglon(resumen, "Mermas y cambios físicos")
    assert salieron[1:3] == [4, "1 registro(s): salieron del camión o de la bodega"]
    entraron = _renglon(resumen, "Devoluciones de clientes")
    assert entraron[1:3] == [12, "1 registro(s): entraron al camión"]
    assert not any(f[0] == "Mermas y devoluciones" for f in resumen.iter_rows(values_only=True))
