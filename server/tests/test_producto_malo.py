"""El producto malo que el vendedor tiene que regresar a la bodega (ADR 0002 §196).

«Si Federico tiene 4 malas, quiero llevar control de esas para que las regrese al
almacén.» Lo malo de cada cambio físico queda a nombre del vendedor hasta que la
oficina lo recibe en la bodega o se lo carga a su cuenta:

1. **Queda pendiente solo**, de los cambios físicos que ya están, y un cambio
   eliminado deja de contar.
2. **Recibirlo** lo mete al almacén «Producto malo»; eliminar ese ajuste en
   Operaciones lo deja pendiente otra vez. No se recibe más de lo que debe.
3. **Cargarlo** lo cobra a costo en su cuenta, y solo con la casilla.
4. **Las mermas no van a ese almacén**, y de él se sale con una salida.
5. **El Excel** dice lo que falta, a nombre de cada vendedor.
6. **Cambiar** (§197): el vendedor entrega lo malo y se lleva lo mismo bueno de
   la bodega; lo malo entra a «Producto malo» a su nombre y su camión no cambia.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.infra import producto_malo
from app.infra.sync.manejadores import obtener_manejador
from tests.conftest import csrf_del_panel, solo_texto
from tests.test_cierre_del_vendedor import _ctx, dia  # noqa: F401
from tests.test_operaciones import _anular
from tests.test_panel_liquidacion import _entrar
from tests.test_panel_salidas import _abrir
from tests.test_reporte_excel import _bajar, _renglon

pytestmark = pytest.mark.asyncio

PANTALLA = "/panel/inventario/producto-malo"


async def _del_telefono(sesion, semilla, dia, *, tipo="cambio", piezas="4.000", n=1):  # noqa: F811
    """Como la sube el teléfono: el cambio físico saca del camión el bueno."""
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
            **({"cliente_id": str(dia["cliente"])} if tipo != "merma" else {}),
            "detalle": [{"producto_id": str(dia["producto"]), "cantidad_base": piezas}],
        },
    )
    await sesion.commit()
    return merma_id


async def _mandar(cliente, accion: str, semilla, dia, piezas: int, **extra):  # noqa: F811
    pagina = await cliente.get(PANTALLA)
    return await cliente.post(
        f"{PANTALLA}/{accion}",
        data={
            "csrf": csrf_del_panel(cliente, pagina),
            "vendedor_id": str(semilla["vendedor"]),
            f"c_{dia['producto']}": str(piezas),
            **extra,
        },
        follow_redirects=True,
    )


async def _pendientes(sesion) -> list:
    return [
        (r["vendedor"], r["articulo"], int(r["pendientes"]))
        for r in await producto_malo.por_regresar(sesion)
    ]


async def _existencia(sesion, almacen, producto) -> Decimal:
    return Decimal(
        (
            await sesion.execute(
                text(
                    "SELECT COALESCE((SELECT cantidad FROM existencias "
                    " WHERE almacen_id = :a AND producto_id = :p), 0)"
                ),
                {"a": almacen, "p": producto},
            )
        ).scalar_one()
    )


async def test_el_cambio_fisico_queda_por_regresar_a_nombre_del_vendedor(
    cliente, sesion, semilla, dia  # noqa: F811
):
    await _del_telefono(sesion, semilla, dia)
    assert await _pendientes(sesion) == [("Juan Pérez", "Atún en agua 140 g", 4)]

    await _entrar(cliente)
    pagina = await cliente.get(PANTALLA)
    assert pagina.status_code == 200, pagina.text
    plano = solo_texto(pagina)
    assert "Juan Pérez · 4 pza por regresar" in plano
    # Cambió 4, entregó 0, se le cargó 0: le faltan 4.
    assert "Atún en agua 140 g ATUN-140 Cambió 4 · entregó 0 · se le cargó 0" in plano
    fila = pagina.text.split(f'id="falta_{semilla["vendedor"]}_{dia["producto"]}"')[1]
    assert "<strong>4</strong>" in fila.split("</tr>")[0]
    assert "De dónde viene: sus cambios físicos" in plano and "VEND01-M00001" in plano
    # Y está en el menú, en Inventario.
    assert 'href="/panel/inventario/producto-malo"' in (await cliente.get("/panel/inventario")).text


async def test_recibirlo_lo_pasa_al_almacen_y_eliminarlo_lo_deja_pendiente(
    cliente, sesion, semilla, dia  # noqa: F811
):
    await _del_telefono(sesion, semilla, dia)
    await _entrar(cliente)
    r = await _mandar(cliente, "recibir", semilla, dia, 3)
    assert "Recibidas 3 pieza(s) en «Producto malo»" in solo_texto(r)
    almacen = await producto_malo.almacen(sesion)
    assert await _existencia(sesion, almacen, dia["producto"]) == 3
    assert await _pendientes(sesion) == [("Juan Pérez", "Atún en agua 140 g", 1)]
    # No suma en lo que vale el inventario: tiene su propia pantalla.
    assert str(almacen) not in (await cliente.get("/panel/inventario")).text

    # El ajuste se ve en Operaciones y ahí se elimina: vuelve a estar pendiente.
    ajuste = (
        await sesion.execute(
            text("SELECT ajuste_id FROM producto_malo_resuelto WHERE como = 'recibido'")
        )
    ).scalar_one()
    r = await _anular(cliente, "ajuste", ajuste)
    assert "anulada" in solo_texto(r)
    assert await _existencia(sesion, almacen, dia["producto"]) == 0
    assert await _pendientes(sesion) == [("Juan Pérez", "Atún en agua 140 g", 4)]
    assert "Recibido, y se eliminó: vuelve a estar pendiente" in solo_texto(
        await cliente.get(PANTALLA)
    )


async def test_no_se_recibe_mas_de_lo_que_tiene_por_regresar(
    cliente, sesion, semilla, dia  # noqa: F811
):
    await _del_telefono(sesion, semilla, dia)
    await _entrar(cliente)
    r = await _mandar(cliente, "recibir", semilla, dia, 5)
    assert "Juan Pérez tiene 4 de Atún en agua 140 g por regresar, no 5." in solo_texto(r)
    assert (
        await sesion.execute(text("SELECT count(*) FROM producto_malo_resuelto"))
    ).scalar_one() == 0


async def test_cargarlo_a_su_cuenta_es_a_costo_y_con_la_casilla(
    cliente, sesion, semilla, dia  # noqa: F811
):
    await _del_telefono(sesion, semilla, dia)
    await _entrar(cliente)
    r = await _mandar(cliente, "cargar", semilla, dia, 2, confirmo_cargo="1")
    assert "Sin costo capturado: Atún en agua 140 g" in solo_texto(r)

    await sesion.execute(
        text("INSERT INTO producto_costos (producto_id, costo_promedio) VALUES (:p, 7.5)"),
        {"p": dia["producto"]},
    )
    await sesion.commit()
    r = await _mandar(cliente, "cargar", semilla, dia, 2)
    assert "marca «Sí, cargárselo»" in solo_texto(r)

    r = await _mandar(cliente, "cargar", semilla, dia, 2, confirmo_cargo="1")
    assert "Se le cargaron $15.00 a su cuenta, a costo." in solo_texto(r)
    cargo = (
        await sesion.execute(
            text("SELECT origen, importe, concepto FROM cuenta_vendedor WHERE vendedor_id = :v"),
            {"v": semilla["vendedor"]},
        )
    ).mappings().one()
    assert (cargo["origen"], cargo["importe"]) == ("cargo_manual", Decimal("15.00"))
    assert "Producto malo" in cargo["concepto"]
    assert await _pendientes(sesion) == [("Juan Pérez", "Atún en agua 140 g", 2)]


async def test_un_cambio_eliminado_deja_de_estar_pendiente(
    cliente, sesion, semilla, dia  # noqa: F811
):
    cambio = await _del_telefono(sesion, semilla, dia)
    await _entrar(cliente)
    await _anular(cliente, "cambio", cambio)
    assert await _pendientes(sesion) == []
    assert "Por regresar, de cambios que registró el teléfono" not in solo_texto(
        await cliente.get(PANTALLA)
    )


async def test_las_mermas_no_van_al_almacen_y_de_el_se_sale_con_una_salida(
    cliente, sesion, semilla, dia  # noqa: F811
):
    almacen = await producto_malo.almacen(sesion, crear=True)
    await sesion.commit()
    # Con el almacén ya creado, la merma y el cambio del teléfono siguen saliendo
    # del sistema: a «Producto malo» solo entra lo que la oficina recibe.
    await _del_telefono(sesion, semilla, dia, tipo="merma", n=1)
    await _del_telefono(sesion, semilla, dia, n=2)
    destinos = (
        await sesion.execute(
            text(
                "SELECT DISTINCT almacen_destino_id FROM movimientos_inventario "
                " WHERE documento_tipo = 'merma'"
            )
        )
    ).scalars().all()
    assert destinos == [None]
    assert await _existencia(sesion, almacen, dia["producto"]) == 0

    await _entrar(cliente)
    lista = await cliente.get("/panel/salidas")
    assert f'value="{almacen}"' in lista.text
    r = await _abrir(cliente, semilla, tipo="merma", motivo="CADUCADO", almacen=almacen,
                     nota="se tiró en la basura de la bodega")
    assert r.status_code == 303, r.text
    assert "/panel/salidas/" in r.headers["location"]


async def test_el_excel_dice_lo_que_falta_a_nombre_de_cada_vendedor(
    cliente, sesion, semilla, dia  # noqa: F811
):
    await _del_telefono(sesion, semilla, dia)
    await _entrar(cliente)
    _, libro = await _bajar(cliente)
    hoja = libro["Cargas y mermas"]
    filas = [list(f) for f in hoja.iter_rows(values_only=True)]
    titulo = next(i for i, f in enumerate(filas) if f[0] == "Producto malo por regresar (hoy)")
    assert filas[titulo + 1][:6] == ["Vendedor", "Artículo", "Cambió", "Entregó",
                                     "Se le cargó", "Falta"]
    assert filas[titulo + 2][:6] == ["Juan Pérez", "Atún en agua 140 g", 4, 0, 0, 4]
    assert _renglon(libro["Resumen"], "Producto malo por regresar (hoy)")[1] == 4


async def _cambiar(cliente, semilla, dia, piezas: int):  # noqa: F811
    pagina = await cliente.get(PANTALLA)
    return await cliente.post(
        f"{PANTALLA}/cambiar",
        data={
            "csrf": csrf_del_panel(cliente, pagina),
            "vendedor_id": str(semilla["vendedor"]),
            "bodega_id": str(semilla["bodega"]),
            f"c_{dia['producto']}": str(piezas),
        },
        follow_redirects=True,
    )


async def test_cambiarlo_saca_lo_bueno_de_la_bodega_y_no_toca_su_camion(
    cliente, sesion, semilla, dia  # noqa: F811
):
    await _entrar(cliente)
    bodega_antes = await _existencia(sesion, semilla["bodega"], dia["producto"])
    camion_antes = await _existencia(sesion, semilla["camion"], dia["producto"])
    avisos_antes = (
        await sesion.execute(
            text("SELECT count(*) FROM change_log WHERE vendedor_id = :v"),
            {"v": semilla["vendedor"]},
        )
    ).scalar_one()

    r = await _cambiar(cliente, semilla, dia, 4)
    plano = solo_texto(r)
    assert "Cambiadas 4 pieza(s)" in plano and "Su camión no cambia." in plano
    almacen = await producto_malo.almacen(sesion)
    assert await _existencia(sesion, semilla["bodega"], dia["producto"]) == bodega_antes - 4
    assert await _existencia(sesion, almacen, dia["producto"]) == 4
    assert await _existencia(sesion, semilla["camion"], dia["producto"]) == camion_antes
    # Su camión no cambió: a su teléfono no le llega nada.
    assert (
        await sesion.execute(
            text("SELECT count(*) FROM change_log WHERE vendedor_id = :v"),
            {"v": semilla["vendedor"]},
        )
    ).scalar_one() == avisos_antes
    # A su nombre, en lo que trajo este mes y en la historia.
    pagina = solo_texto(await cliente.get(PANTALLA))
    assert "Lo que cada vendedor ha traído este mes Vendedor Veces Piezas Juan Pérez 1 4" in pagina
    assert "Cambiado por bueno" in pagina
    # No es lo pendiente de los cambios del teléfono.
    assert await _pendientes(sesion) == []

    # Una sola operación en Operaciones; eliminarla lo deshace todo.
    cabeza = (
        await sesion.execute(
            text(
                "SELECT COALESCE(a.pareja_id, a.id) FROM producto_malo_resuelto r "
                "  JOIN ajustes_camion a ON a.id = r.ajuste_id WHERE r.como = 'cambiado'"
            )
        )
    ).scalar_one()
    r = await _anular(cliente, "ajuste", cabeza)
    assert "anulada" in solo_texto(r)
    assert await _existencia(sesion, semilla["bodega"], dia["producto"]) == bodega_antes
    assert await _existencia(sesion, almacen, dia["producto"]) == 0
    assert "Lo que cada vendedor ha traído este mes" not in solo_texto(
        await cliente.get(PANTALLA)
    )


async def test_no_se_cambia_mas_de_lo_bueno_que_hay_en_la_bodega(
    cliente, sesion, semilla, dia  # noqa: F811
):
    await _entrar(cliente)
    hay = await _existencia(sesion, semilla["bodega"], dia["producto"])
    r = await _cambiar(cliente, semilla, dia, int(hay) + 1)
    assert "No alcanza en" in solo_texto(r)
    assert await _existencia(sesion, semilla["bodega"], dia["producto"]) == hay
    assert (
        await sesion.execute(text("SELECT count(*) FROM producto_malo_resuelto"))
    ).scalar_one() == 0
