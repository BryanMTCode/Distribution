"""Operaciones: todas juntas, y anular deshaciendo (ADR 0002 §119).

«Quiero una parte en el dashboard donde aparezcan todas las operaciones juntas
para borrar lo que yo necesite cuando quiera.» Y «borrar» es ANULAR Y DESHACER:

1. **La lista junta todo**: ventas, mermas, visitas sin venta, cargas,
   entradas, ajustes, cortes… y filtra por tipo.
2. **Anular deshace lo que movió**: la venta vuelve al camión, la merma también
   (y sale del almacén de merma), la carga a la bodega, la entrada sale de la
   bodega con su cuenta por pagar, el ajuste se compensa.
3. **El teléfono se entera**: la venta y la carga por su propio delta; la
   merma y el ajuste por un ajuste del camión de tipo «anulación».
4. **Queda en la lista como anulada**, con quién, cuándo y por qué; una visita
   sin venta, que no tiene estado, se borra y queda solo su anulación.
5. **Lo que no se deja**: dejar un almacén en negativo, anular dos veces, sin
   motivo, el corte del día, y que alguien sin el permiso anule.
6. **Un día con corte cerrado pide confirmar**, y el corte no cambia.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.infra.ajuste_de_existencia import registrar_ajuste
from app.infra.sync.manejadores import Contexto, obtener_manejador
from tests.conftest import csrf_del_panel, solo_texto
from tests.test_panel_objetivos import _crear_usuario
from tests.test_panel_venta_editar import _entrar, venta_del_dia  # noqa: F401

pytestmark = pytest.mark.asyncio

MOTIVO = "se capturó dos veces, el cliente lo confirmó por teléfono"


async def _anular(cliente, tipo, ref, *, motivo=MOTIVO, entiendo=False):
    pagina = await cliente.get(f"/panel/operaciones/{tipo}/{ref}")
    datos = {"csrf": csrf_del_panel(cliente, pagina), "motivo": motivo}
    if entiendo:
        datos["entiendo"] = "1"
    return await cliente.post(
        f"/panel/operaciones/{tipo}/{ref}/anular", data=datos, follow_redirects=True
    )


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


async def _ultimo_delta(sesion, entidad, entidad_id=None):
    return (
        await sesion.execute(
            text(
                "SELECT payload, vendedor_id FROM change_log WHERE entidad = :e "
                "   AND (CAST(:id AS uuid) IS NULL OR entidad_id = CAST(:id AS uuid)) "
                " ORDER BY cursor DESC LIMIT 1"
            ),
            {"e": entidad, "id": entidad_id},
        )
    ).mappings().first()


def _contexto(semilla, venta_del_dia) -> Contexto:  # noqa: F811
    return Contexto(
        dispositivo_id=venta_del_dia["dispositivo"],
        usuario_id=semilla["vendedor"],
        rutas=(semilla["ruta"],),
        almacen_id=semilla["camion"],
    )


async def _merma(sesion, semilla, venta_del_dia, *, tipo="merma", piezas="6.000"):  # noqa: F811
    merma_id = uuid.uuid4()
    datos = {
        "folio_consecutivo": 7,
        "folio_local": "VEND01-M00007",
        "tipo": tipo,
        "motivo_codigo": "ROTO",
        "fecha_dispositivo": f"{date.today().isoformat()}T17:42:03.250Z",
        "fecha_operativa": date.today().isoformat(),
        "detalle": [{"producto_id": str(venta_del_dia["producto"]), "cantidad_base": piezas}],
    }
    if tipo != "merma":
        datos["cliente_id"] = str(venta_del_dia["cliente"])
    await obtener_manejador("merma.crear")(
        sesion, _contexto(semilla, venta_del_dia), merma_id, datos
    )
    await sesion.commit()
    return merma_id


@pytest.fixture
async def almacen_de_merma(sesion):
    almacen = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO almacenes (id, codigo, nombre, tipo) "
            "VALUES (:a, 'MERMA_01', 'Almacén de merma', 'merma')"
        ),
        {"a": almacen},
    )
    await sesion.commit()
    return almacen


@pytest.fixture
async def carga_en_ruta(sesion, semilla, venta_del_dia):  # noqa: F811
    """Una carga de 100 piezas de mañana, escrita como la escribe la bodega:
    con su asiento bodega → camión."""
    carga = uuid.uuid4()
    await sesion.execute(
        text(
            """
            INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id,
                                vendedor_id, ruta_id, fecha_operativa, estado, confirmada_en)
            VALUES (:id, 'CG-OPER', :b, :c, :v, :r, :d, 'confirmada', now())
            """
        ),
        {"id": carga, "b": semilla["bodega"], "c": semilla["camion"],
         "v": semilla["vendedor"], "r": semilla["ruta"],
         "d": date.today() - timedelta(days=1)},
    )
    await sesion.execute(
        text("INSERT INTO carga_detalle (carga_id, producto_id, cantidad) VALUES (:c, :p, 100)"),
        {"c": carga, "p": venta_del_dia["producto"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO movimientos_inventario (tipo, almacen_origen_id, almacen_destino_id, "
            "  producto_id, cantidad, documento_tipo, documento_id) "
            "VALUES ('carga', :b, :c, :p, 100, 'carga', :doc)"
        ),
        {"b": semilla["bodega"], "c": semilla["camion"], "p": venta_del_dia["producto"],
         "doc": carga},
    )
    # La bodega tenía 400 y el camión 120: la carga ya los movió.
    await sesion.execute(
        text("UPDATE existencias SET cantidad = cantidad - 100 "
             " WHERE almacen_id = :b AND producto_id = :p"),
        {"b": semilla["bodega"], "p": venta_del_dia["producto"]},
    )
    await sesion.execute(
        text("UPDATE existencias SET cantidad = cantidad + 100 "
             " WHERE almacen_id = :c AND producto_id = :p"),
        {"c": semilla["camion"], "p": venta_del_dia["producto"]},
    )
    await sesion.commit()
    return carga


# ---------------------------------------------------------------------------
# La lista
# ---------------------------------------------------------------------------


async def test_la_lista_junta_todas_las_operaciones(
    cliente, sesion, semilla, venta_del_dia  # noqa: F811
):
    merma = await _merma(sesion, semilla, venta_del_dia)
    await _entrar(cliente)
    r = await cliente.get("/panel/operaciones?periodo=todo")
    assert r.status_code == 200
    plano = solo_texto(r)
    assert "VEND01-000001" in plano and "VEND01-M00007" in plano and "CG-VENTA" in plano
    assert f'href="/panel/operaciones/venta/{venta_del_dia["venta"]}"' in r.text
    assert f'id="anular_{merma}"' in r.text
    # El menú la tiene en Administración, marcada.
    assert 'aria-current="page">Operaciones<' in r.text

    # Filtrar por tipo deja solo ese.
    r = await cliente.get("/panel/operaciones?periodo=todo&tipo=merma")
    plano = solo_texto(r)
    assert "VEND01-M00007" in plano and "VEND01-000001" not in plano


# ---------------------------------------------------------------------------
# Anular una venta
# ---------------------------------------------------------------------------


async def test_anular_una_venta_la_cancela_y_la_mercancia_vuelve_al_camion(
    cliente, sesion, semilla, venta_del_dia  # noqa: F811
):
    venta = venta_del_dia["venta"]
    await _entrar(cliente)
    pagina = await cliente.get(f"/panel/operaciones/venta/{venta}")
    plano = solo_texto(pagina)
    assert "Si la anulas" in plano
    assert "La venta pasa a cancelada y sus 480 piezas vuelven a" in plano

    r = await _anular(cliente, "venta", venta)
    assert "Venta VEND01-000001 anulada." in solo_texto(r)

    assert (
        await sesion.execute(text("SELECT estado FROM ventas WHERE id = :v"), {"v": venta})
    ).scalar_one() == "cancelada"
    # 120 en el camión + las 480 de la venta.
    assert await _existencia(sesion, semilla["camion"], venta_del_dia["producto"]) == 600
    # El teléfono la recibe cancelada, con el motivo.
    delta = await _ultimo_delta(sesion, "venta", venta)
    assert delta["payload"]["estado"] == "cancelada"
    assert delta["payload"]["cancelacion_motivo"] == MOTIVO

    # En la lista: anulada, con quién y por qué; sin botón de anular.
    r = await cliente.get("/panel/operaciones?periodo=todo&estado=anuladas")
    plano = solo_texto(r)
    assert "VEND01-000001" in plano and "anulada" in plano and MOTIVO in plano
    assert f'id="anular_{venta}"' not in r.text

    # Dos veces no: la segunda ya la encuentra anulada.
    r = await _anular(cliente, "venta", venta)
    assert "Ya se anuló" in solo_texto(r)
    assert await _existencia(sesion, semilla["camion"], venta_del_dia["producto"]) == 600


async def test_sin_motivo_no_se_anula(cliente, sesion, semilla, venta_del_dia):  # noqa: F811
    await _entrar(cliente)
    r = await _anular(cliente, "venta", venta_del_dia["venta"], motivo="porque sí")
    assert "al menos 10 letras" in solo_texto(r)
    assert (
        await sesion.execute(
            text("SELECT estado FROM ventas WHERE id = :v"), {"v": venta_del_dia["venta"]}
        )
    ).scalar_one() == "confirmada"
    assert (await sesion.execute(text("SELECT count(*) FROM anulaciones"))).scalar_one() == 0


async def test_un_dia_con_corte_cerrado_pide_confirmar_y_el_corte_no_cambia(
    cliente, sesion, semilla, venta_del_dia  # noqa: F811
):
    await sesion.execute(
        text(
            "INSERT INTO liquidaciones (id, folio, carga_id, vendedor_id, fecha_operativa, "
            "  estado, cerrada_en, efectivo_esperado, efectivo_entregado) "
            "VALUES (:id, 'LQ-000009', :c, :v, :d, 'cerrada', now(), 6000, 6000)"
        ),
        {"id": uuid.uuid4(), "c": venta_del_dia["carga"], "v": semilla["vendedor"],
         "d": venta_del_dia["dia"]},
    )
    await sesion.commit()
    await _entrar(cliente)
    pagina = await cliente.get(f"/panel/operaciones/venta/{venta_del_dia['venta']}")
    assert 'id="entiendo_el_corte"' in pagina.text
    assert "(LQ-000009) ya se cerró y NO cambia" in solo_texto(pagina)

    r = await _anular(cliente, "venta", venta_del_dia["venta"])
    assert "Marca la casilla" in solo_texto(r)
    assert await _existencia(sesion, semilla["camion"], venta_del_dia["producto"]) == 120

    r = await _anular(cliente, "venta", venta_del_dia["venta"], entiendo=True)
    assert "anulada" in solo_texto(r)
    assert await _existencia(sesion, semilla["camion"], venta_del_dia["producto"]) == 600
    # El corte firmado se queda como se firmó.
    corte = (
        await sesion.execute(
            text("SELECT estado, efectivo_esperado FROM liquidaciones WHERE folio = 'LQ-000009'")
        )
    ).mappings().one()
    assert corte["estado"] == "cerrada" and corte["efectivo_esperado"] == 6000


# ---------------------------------------------------------------------------
# Mermas
# ---------------------------------------------------------------------------


async def test_anular_una_merma_la_regresa_al_camion_y_el_telefono_se_entera(
    cliente, sesion, semilla, venta_del_dia, almacen_de_merma  # noqa: F811
):
    merma = await _merma(sesion, semilla, venta_del_dia)
    producto = venta_del_dia["producto"]
    assert await _existencia(sesion, semilla["camion"], producto) == 114
    assert await _existencia(sesion, almacen_de_merma, producto) == 6

    await _entrar(cliente)
    pagina = solo_texto(await cliente.get(f"/panel/operaciones/merma/{merma}"))
    assert "Las piezas regresan a" in pagina and "salen del almacén de merma" in pagina
    r = await _anular(cliente, "merma", merma)
    assert "Merma VEND01-M00007 anulada." in solo_texto(r)

    assert await _existencia(sesion, semilla["camion"], producto) == 120
    assert await _existencia(sesion, almacen_de_merma, producto) == 0
    assert (
        await sesion.execute(text("SELECT estado FROM mermas WHERE id = :m"), {"m": merma})
    ).scalar_one() == "cancelada"

    # El teléfono recibe un ajuste del camión de +6, con el motivo en la nota.
    ajuste = (
        await sesion.execute(
            text("SELECT folio, delta, nota FROM ajustes_camion WHERE tipo = 'anulacion'")
        )
    ).mappings().one()
    assert ajuste["delta"] == 6 and MOTIVO in ajuste["nota"]
    delta = await _ultimo_delta(sesion, "ajuste_camion")
    assert delta["payload"]["delta"] == "6.000"
    assert delta["vendedor_id"] == semilla["vendedor"]
    assert ajuste["folio"] in solo_texto(r)

    # El libro mayor no se borra: se escribe al revés.
    movimientos = (
        await sesion.execute(
            text("SELECT count(*) FROM movimientos_inventario WHERE producto_id = :p "
                 "   AND tipo = 'ajuste'"),
            {"p": producto},
        )
    ).scalar_one()
    assert movimientos == 1

    # Y deja de contar: el ajuste de anulación no aparece como operación.
    r = await cliente.get("/panel/operaciones?periodo=todo&tipo=ajuste")
    assert 'id="sin_operaciones"' in r.text


async def test_anular_una_devolucion_no_deja_el_camion_en_negativo(
    cliente, sesion, semilla, venta_del_dia  # noqa: F811
):
    devolucion = await _merma(
        sesion, semilla, venta_del_dia, tipo="devolucion_cliente", piezas="10.000"
    )
    # Entraron 10 (130) y luego se vendieron 125: quedan 5.
    await sesion.execute(
        text("UPDATE existencias SET cantidad = 5 WHERE almacen_id = :c AND producto_id = :p"),
        {"c": semilla["camion"], "p": venta_del_dia["producto"]},
    )
    await sesion.commit()
    await _entrar(cliente)
    pagina = solo_texto(await cliente.get(f"/panel/operaciones/devolucion/{devolucion}"))
    assert "No se puede anular." in pagina
    assert "ya no tiene 10 de Atún en agua 140 g (hay 5)" in pagina
    r = await _anular(cliente, "devolucion", devolucion)
    assert "No se puede anular" in solo_texto(r)
    assert await _existencia(sesion, semilla["camion"], venta_del_dia["producto"]) == 5


# ---------------------------------------------------------------------------
# Visita sin venta
# ---------------------------------------------------------------------------


async def test_anular_una_visita_sin_venta_la_borra_y_queda_anotada(
    cliente, sesion, semilla, venta_del_dia  # noqa: F811
):
    no_venta = uuid.uuid4()
    await obtener_manejador("no_drop.crear")(
        sesion,
        _contexto(semilla, venta_del_dia),
        no_venta,
        {
            "folio_consecutivo": 3,
            "cliente_id": str(venta_del_dia["cliente"]),
            "motivo_codigo": "CERRADO",
            "lat": "20.6736000",
            "lng": "-103.3440000",
            "ubicacion_precision_m": "12.50",
            "fecha_dispositivo": f"{date.today().isoformat()}T17:42:03.250Z",
            "fecha_operativa": date.today().isoformat(),
        },
    )
    await sesion.commit()

    await _entrar(cliente)
    r = await _anular(cliente, "no_venta", no_venta, motivo="el vendedor sí le vendió, se equivocó")
    assert "Visita sin venta 3 anulada." in solo_texto(r)
    assert (
        await sesion.execute(text("SELECT count(*) FROM no_drops WHERE id = :n"), {"n": no_venta})
    ).scalar_one() == 0

    # Ya no existe, pero la lista la sigue mostrando: anulada, con su motivo.
    r = await cliente.get("/panel/operaciones?periodo=todo&tipo=no_venta")
    plano = solo_texto(r)
    assert "La Esquina" in plano and "anulada" in plano
    assert "el vendedor sí le vendió, se equivocó" in plano


# ---------------------------------------------------------------------------
# Cargas
# ---------------------------------------------------------------------------


async def test_anular_una_carga_la_regresa_a_la_bodega_y_el_telefono_la_deshace(
    cliente, sesion, semilla, venta_del_dia, carga_en_ruta  # noqa: F811
):
    producto = venta_del_dia["producto"]
    await _entrar(cliente)
    r = await _anular(cliente, "carga", carga_en_ruta)
    assert "Carga CG-OPER anulada." in solo_texto(r)
    assert await _existencia(sesion, semilla["bodega"], producto) == 400
    assert await _existencia(sesion, semilla["camion"], producto) == 120
    delta = await _ultimo_delta(sesion, "carga", carga_en_ruta)
    assert delta["payload"]["estado"] == "cancelada"
    assert delta["payload"]["detalle"][0]["cantidad"] == "100.000"
    # Sin ajuste: la carga cancelada ya le dice al teléfono que la deshaga.
    assert (
        await sesion.execute(text("SELECT count(*) FROM ajustes_camion"))
    ).scalar_one() == 0


async def test_no_se_anula_una_carga_que_ya_se_vendio(
    cliente, sesion, semilla, venta_del_dia, carga_en_ruta  # noqa: F811
):
    await sesion.execute(
        text("UPDATE existencias SET cantidad = 40 WHERE almacen_id = :c AND producto_id = :p"),
        {"c": semilla["camion"], "p": venta_del_dia["producto"]},
    )
    await sesion.commit()
    await _entrar(cliente)
    r = await _anular(cliente, "carga", carga_en_ruta)
    assert "ya no tiene 100 de Atún en agua 140 g (hay 40)" in solo_texto(r)
    assert (
        await sesion.execute(text("SELECT estado FROM cargas WHERE id = :c"), {"c": carga_en_ruta})
    ).scalar_one() == "confirmada"


# ---------------------------------------------------------------------------
# Entradas y ajustes
# ---------------------------------------------------------------------------


async def test_anular_una_compra_saca_la_mercancia_y_borra_la_cuenta_por_pagar(
    cliente, sesion, semilla, venta_del_dia  # noqa: F811
):
    from app.api.admin.entradas import (
        abrir_entrada,
        agregar_renglon_a_entrada,
        confirmar_entrada,
    )

    proveedor = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO proveedores (id, codigo, nombre) VALUES (:p, 'ABARR01', 'Abarrotes')"),
        {"p": proveedor},
    )
    await sesion.commit()
    entrada = await abrir_entrada(
        sesion, almacen_destino_id=str(semilla["bodega"]), motivo="compra",
        proveedor_id=str(proveedor), referencia="F-123", quien=semilla["admin"],
    )
    await agregar_renglon_a_entrada(
        sesion, entrada, producto="ATUN-140", unidad_codigo="PZA", cantidad="10", costo="5"
    )
    await confirmar_entrada(sesion, entrada, quien=semilla["admin"])
    assert await _existencia(sesion, semilla["bodega"], venta_del_dia["producto"]) == 410

    await _entrar(cliente)
    pagina = solo_texto(await cliente.get(f"/panel/operaciones/entrada/{entrada}"))
    assert "Se borra la cuenta por pagar de $50.00 a Abarrotes." in pagina
    await _anular(cliente, "entrada", entrada)
    assert await _existencia(sesion, semilla["bodega"], venta_del_dia["producto"]) == 400
    assert (
        await sesion.execute(
            text("SELECT count(*) FROM cuentas_por_pagar WHERE entrada_id = :e"), {"e": entrada}
        )
    ).scalar_one() == 0
    fila = (
        await sesion.execute(
            text("SELECT estado, cancelacion_motivo FROM entradas WHERE id = :e"), {"e": entrada}
        )
    ).mappings().one()
    assert fila["estado"] == "cancelada" and fila["cancelacion_motivo"] == MOTIVO


async def test_anular_un_ajuste_del_camion_escribe_el_contrario(
    cliente, sesion, semilla, venta_del_dia  # noqa: F811
):
    producto = venta_del_dia["producto"]
    folio = await registrar_ajuste(
        sesion, almacen_id=semilla["camion"], producto_id=producto, es_camion=True,
        tipo="entrada", actual=Decimal(120), delta=Decimal(5), contado=None,
        nota="llegaron 5 de más en la carga", quien=semilla["admin"],
    )
    await sesion.commit()
    ajuste = (
        await sesion.execute(text("SELECT id FROM ajustes_camion WHERE folio = :f"), {"f": folio})
    ).scalar_one()
    assert await _existencia(sesion, semilla["camion"], producto) == 125

    await _entrar(cliente)
    await _anular(cliente, "ajuste", ajuste)
    assert await _existencia(sesion, semilla["camion"], producto) == 120
    contrario = (
        await sesion.execute(text("SELECT delta FROM ajustes_camion WHERE tipo = 'anulacion'"))
    ).scalar_one()
    assert contrario == -5
    assert (await _ultimo_delta(sesion, "ajuste_camion"))["payload"]["delta"] == "-5.000"


# ---------------------------------------------------------------------------
# Lo que no se anula, y quién puede
# ---------------------------------------------------------------------------


async def test_el_corte_no_se_anula(cliente, sesion, semilla, venta_del_dia):  # noqa: F811
    corte = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO liquidaciones (id, folio, carga_id, vendedor_id, fecha_operativa, "
            "  estado, cerrada_en) VALUES (:id, 'LQ-000010', :c, :v, :d, 'cerrada', now())"
        ),
        {"id": corte, "c": venta_del_dia["carga"], "v": semilla["vendedor"],
         "d": venta_del_dia["dia"]},
    )
    await sesion.commit()
    await _entrar(cliente)
    pagina = solo_texto(await cliente.get(f"/panel/operaciones/corte/{corte}"))
    assert "Un corte no se anula" in pagina
    r = await _anular(cliente, "corte", corte)
    assert "No se puede anular: Un corte no se anula" in solo_texto(r)
    assert (await sesion.execute(text("SELECT count(*) FROM anulaciones"))).scalar_one() == 0


async def test_un_supervisor_ve_la_lista_pero_no_anula(
    cliente, sesion, semilla, venta_del_dia  # noqa: F811
):
    await _crear_usuario(sesion, semilla, "SUP01", "supervisor")
    await _entrar(cliente, "SUP01")
    r = await cliente.get("/panel/operaciones?periodo=todo")
    assert r.status_code == 200
    assert "VEND01-000001" in solo_texto(r)
    assert 'id="anular_' not in r.text
    pagina = await cliente.get(f"/panel/operaciones/venta/{venta_del_dia['venta']}")
    assert 'id="forma_anular"' not in pagina.text
    r = await cliente.post(
        f"/panel/operaciones/venta/{venta_del_dia['venta']}/anular",
        data={"csrf": csrf_del_panel(cliente), "motivo": MOTIVO},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Devolución a bodega y mayoreo
# ---------------------------------------------------------------------------


async def test_anular_una_devolucion_a_bodega_recibida_la_regresa_al_camion(
    cliente, sesion, semilla, venta_del_dia  # noqa: F811
):
    """El vendedor declaró 20, la bodega contó 18 y 2 se quedaron en tránsito.
    Anular regresa las 20 al camión: 18 de la bodega y 2 de tránsito."""
    producto = venta_del_dia["producto"]
    traspaso = uuid.uuid4()
    await obtener_manejador("traspaso.crear")(
        sesion,
        _contexto(semilla, venta_del_dia),
        traspaso,
        {
            "fecha_dispositivo": f"{date.today().isoformat()}T18:00:00.000Z",
            "fecha_operativa": date.today().isoformat(),
            "detalle": [{"producto_id": str(producto), "cantidad": "20.000"}],
        },
    )
    await sesion.commit()
    renglon = (
        await sesion.execute(
            text("SELECT id FROM traspaso_detalle WHERE traspaso_id = :t"), {"t": traspaso}
        )
    ).scalar_one()
    await _entrar(cliente)
    pagina = await cliente.get(f"/panel/entradas/devolucion/{traspaso}")
    r = await cliente.post(
        f"/panel/entradas/devolucion/{traspaso}/recibir",
        data={"csrf": csrf_del_panel(cliente, pagina), "almacen_destino_id": str(semilla["bodega"]),
              f"contado_{renglon}": "18"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert await _existencia(sesion, semilla["camion"], producto) == 100
    assert await _existencia(sesion, semilla["bodega"], producto) == 418

    r = await _anular(cliente, "devolucion_bodega", traspaso)
    assert "Devolución a bodega" in solo_texto(r) and "anulada" in solo_texto(r)
    assert await _existencia(sesion, semilla["camion"], producto) == 120
    assert await _existencia(sesion, semilla["bodega"], producto) == 400
    transito = (
        await sesion.execute(text("SELECT almacen_destino_id FROM traspasos WHERE id = :t"),
                             {"t": traspaso})
    ).scalar_one()
    assert await _existencia(sesion, transito, producto) == 0
    # El teléfono la ve cancelada y recibe las 20 de vuelta.
    assert (await _ultimo_delta(sesion, "traspaso", traspaso))["payload"]["estado"] == "cancelado"
    assert (await _ultimo_delta(sesion, "ajuste_camion"))["payload"]["delta"] == "20.000"


async def test_anular_una_venta_de_mayoreo_regresa_a_la_bodega(
    cliente, sesion, semilla, venta_del_dia  # noqa: F811
):
    from app.infra import mayoreo

    await sesion.execute(
        text("UPDATE clientes SET mayoreo = true, ruta_id = NULL WHERE id = :c"),
        {"c": venta_del_dia["cliente"]},
    )
    venta, folio = await mayoreo.vender(
        sesion, cliente_id=venta_del_dia["cliente"], almacen_id=semilla["bodega"],
        renglones=[mayoreo.RenglonPedido(venta_del_dia["producto"], "CAJA", Decimal(2),
                                         Decimal("280"))],
        forma_pago="efectivo", referencia_pago=None, nota=None, quien=semilla["admin"],
    )
    await sesion.commit()
    assert await _existencia(sesion, semilla["bodega"], venta_del_dia["producto"]) == 352

    await _entrar(cliente)
    r = await cliente.get("/panel/operaciones?periodo=hoy&tipo=mayoreo")
    assert folio in solo_texto(r)
    r = await _anular(cliente, "mayoreo", venta)
    assert f"Venta de mayoreo {folio} anulada." in solo_texto(r)
    assert await _existencia(sesion, semilla["bodega"], venta_del_dia["producto"]) == 400
    assert (
        await sesion.execute(text("SELECT estado FROM ventas_mayoreo WHERE id = :v"), {"v": venta})
    ).scalar_one() == "cancelada"
