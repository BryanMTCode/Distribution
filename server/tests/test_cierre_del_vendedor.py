"""El cierre del vendedor: corte → solicitud de carga → el gerente acepta (§82).

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
1. **La ingesta guarda la palabra del vendedor y no mueve nada.** El corte y la
   solicitud llegan por la cola, sin señal; se guardan una vez aunque el sobre
   se reintente, y el último del día reemplaza al anterior que nadie vio.
2. **Una carga al día.** Si ese día ya tiene una aceptada, la nueva entra
   rechazada y el teléfono se entera con el motivo.
3. **Aceptar hace las dos cosas con las reglas de siempre.** El corte se cierra
   con lo que contó el vendedor y el efectivo que declaró —el faltante va a su
   cuenta—; la carga de mañana sale de la bodega principal, se confirma y le
   llega al teléfono junto con «aceptada» y su folio.
4. **Quién.** El vendedor no acepta su propio cierre, ni con su token.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.infra.sync.manejadores import Contexto, obtener_manejador
from tests.conftest import PASSWORD_VENDEDOR, csrf_del_panel, solo_texto
from tests.test_cortes_api import _cab, _usuario
from tests.test_panel_liquidacion import _entrar, _reportar_cola, sembrar_dia_de_trabajo

pytestmark = pytest.mark.asyncio

MOMENTO = "2026-10-08T01:30:00.000Z"


@pytest.fixture
async def dia(sesion, semilla) -> dict:
    """El día de siempre: 240 cargadas, 180 vendidas, 60 en el camión; $2,250 en
    efectivo. El teléfono ya subió todo."""
    datos = await sembrar_dia_de_trabajo(sesion, semilla)
    await _reportar_cola(sesion, datos["dispositivo"], 0)
    await _usuario(sesion, semilla, "GER01", "gerente")
    return datos


def _ctx(dia, semilla) -> Contexto:
    return Contexto(
        dispositivo_id=dia["dispositivo"],
        usuario_id=semilla["vendedor"],
        rutas=(semilla["ruta"],),
        almacen_id=semilla["camion"],
    )


async def _corte(sesion, dia, semilla, *, contadas="55", efectivo="2200.00", corte_id=None):
    corte_id = corte_id or uuid.uuid4()
    await obtener_manejador("corte.crear")(
        sesion,
        _ctx(dia, semilla),
        corte_id,
        {
            "fecha_operativa": dia["dia"].isoformat(),
            "carga_id": str(dia["carga"]),
            "efectivo_declarado": efectivo,
            "observaciones": "Se me rompió una lata",
            "fecha_dispositivo": MOMENTO,
            "conteo": [{"producto_id": str(dia["producto"]), "cantidad": f"{contadas}.000"}],
        },
    )
    await sesion.commit()
    return corte_id


async def _solicitud(sesion, dia, semilla, *, corte_id=None, cajas=10, para=None, sid=None):
    sid = sid or uuid.uuid4()
    await obtener_manejador("solicitud_carga.crear")(
        sesion,
        _ctx(dia, semilla),
        sid,
        {
            "fecha_operativa": (para or dia["dia"] + timedelta(days=1)).isoformat(),
            "corte_id": str(corte_id) if corte_id else None,
            "observaciones": None,
            "fecha_dispositivo": MOMENTO,
            "detalle": [
                {
                    "producto_id": str(dia["producto"]),
                    "unidad_codigo": "CAJA",
                    "bultos": f"{cajas}.000",
                    "cantidad": f"{cajas * 24}.000",
                }
            ],
        },
    )
    await sesion.commit()
    return sid


async def _deltas(sesion, entidad_id) -> list[dict]:
    filas = (
        await sesion.execute(
            text(
                "SELECT payload, vendedor_id FROM change_log "
                " WHERE entidad = 'solicitud_carga' AND entidad_id = :id ORDER BY cursor"
            ),
            {"id": entidad_id},
        )
    ).all()
    return [
        {**(f.payload if isinstance(f.payload, dict) else json.loads(f.payload)),
         "_vendedor": f.vendedor_id}
        for f in filas
    ]


# ---------------------------------------------------------------------------
# La ingesta
# ---------------------------------------------------------------------------
async def test_el_corte_se_guarda_una_vez_y_no_mueve_nada(sesion, semilla, dia):
    corte_id = await _corte(sesion, dia, semilla)
    # El sobre se reintenta por diseño: la segunda vez no hace nada.
    await _corte(sesion, dia, semilla, corte_id=corte_id, contadas="1")

    fila = (
        await sesion.execute(text("SELECT * FROM cortes_vendedor WHERE id = :c"),
                             {"c": corte_id})
    ).mappings().one()
    assert fila["estado"] == "pendiente"
    assert fila["efectivo_declarado"] == Decimal("2200.00")
    assert fila["carga_id"] == dia["carga"]
    contada = (
        await sesion.execute(
            text("SELECT cantidad FROM corte_vendedor_conteo WHERE corte_id = :c"),
            {"c": corte_id},
        )
    ).scalar_one()
    assert contada == Decimal("55.000")
    # Ni el camión ni la carga se tocan al llegar.
    camion = (
        await sesion.execute(
            text("SELECT cantidad FROM existencias WHERE almacen_id = :a AND producto_id = :p"),
            {"a": semilla["camion"], "p": dia["producto"]},
        )
    ).scalar_one()
    assert camion == Decimal("60.000")
    estado = (
        await sesion.execute(text("SELECT estado FROM cargas WHERE id = :c"), {"c": dia["carga"]})
    ).scalar_one()
    assert estado == "confirmada"


async def test_el_ultimo_corte_del_dia_reemplaza_al_anterior(sesion, semilla, dia):
    primero = await _corte(sesion, dia, semilla, contadas="50")
    segundo = await _corte(sesion, dia, semilla, contadas="60")
    estados = dict(
        (
            await sesion.execute(text("SELECT id, estado FROM cortes_vendedor"))
        ).all()
    )
    assert estados == {primero: "reemplazado", segundo: "pendiente"}


async def test_la_solicitud_nueva_reemplaza_a_la_pendiente_y_se_avisa(sesion, semilla, dia):
    primera = await _solicitud(sesion, dia, semilla, cajas=5)
    # Nace en el teléfono: devolvérsela al llegar sería un eco.
    assert await _deltas(sesion, primera) == []

    segunda = await _solicitud(sesion, dia, semilla, cajas=8)
    estados = dict(
        (await sesion.execute(text("SELECT id, estado FROM solicitudes_carga"))).all()
    )
    assert estados == {primera: "reemplazada", segunda: "pendiente"}
    [delta] = await _deltas(sesion, primera)
    assert delta["estado"] == "reemplazada"
    assert delta["_vendedor"] == semilla["vendedor"]


async def test_rehacer_el_corte_despues_de_pedir_la_carga_no_parte_el_cierre(
    cliente, sesion, semilla, dia
):
    viejo = await _corte(sesion, dia, semilla, contadas="50")
    sid = await _solicitud(sesion, dia, semilla, corte_id=viejo)
    nuevo = await _corte(sesion, dia, semilla, contadas="55")
    cab = await _cab(cliente, "GER01")

    [cierre] = (await cliente.get("/v1/cierres", headers=cab)).json()["pendientes"]
    assert (cierre["corte"]["id"], cierre["solicitud"]["id"]) == (str(nuevo), str(sid))

    # Aceptar solo con la solicitud también toma el corte vigente.
    r = await cliente.post("/v1/cierres/aceptar", json={"solicitud_id": str(sid)}, headers=cab)
    assert r.status_code == 200, r.text
    estados = dict((await sesion.execute(text("SELECT id, estado FROM cortes_vendedor"))).all())
    assert estados == {viejo: "reemplazado", nuevo: "cerrado"}


async def test_una_carga_al_dia_la_segunda_entra_rechazada(cliente, sesion, semilla, dia):
    corte_id = await _corte(sesion, dia, semilla)
    primera = await _solicitud(sesion, dia, semilla, corte_id=corte_id)
    cab = await _cab(cliente, "GER01")
    r = await cliente.post(
        "/v1/cierres/aceptar",
        json={"corte_id": str(corte_id), "solicitud_id": str(primera)},
        headers=cab,
    )
    assert r.status_code == 200, r.text

    otra = await _solicitud(sesion, dia, semilla, cajas=3)
    fila = (
        await sesion.execute(
            text("SELECT estado, motivo FROM solicitudes_carga WHERE id = :s"), {"s": otra}
        )
    ).one()
    assert fila.estado == "rechazada"
    assert "solo hay una carga al día" in fila.motivo
    [delta] = await _deltas(sesion, otra)
    assert delta["estado"] == "rechazada"
    assert delta["detalle"][0]["cantidad"] == "72.000"


# ---------------------------------------------------------------------------
# Lo que ve el gerente
# ---------------------------------------------------------------------------
async def test_el_gerente_ve_el_corte_junto_con_su_solicitud(cliente, sesion, semilla, dia):
    corte_id = await _corte(sesion, dia, semilla)
    sid = await _solicitud(sesion, dia, semilla, corte_id=corte_id)
    r = await cliente.get("/v1/cierres", headers=await _cab(cliente, "GER01"))
    assert r.status_code == 200, r.text
    [cierre] = r.json()["pendientes"]
    assert cierre["vendedor_codigo"] == "VEND01"
    corte = cierre["corte"]
    assert corte["id"] == str(corte_id)
    assert corte["efectivo_esperado"] == "2250.00"
    assert corte["diferencia_efectivo"] == "-50.00"
    [renglon] = corte["renglones"]
    assert (renglon["contada"], renglon["sistema"], renglon["diferencia"]) == (
        "55.000", "60.000", "-5.000"
    )
    solicitud = cierre["solicitud"]
    assert solicitud["id"] == str(sid)
    assert solicitud["bodega"] == "Bodega"
    [pedido] = solicitud["renglones"]
    assert (pedido["bultos"], pedido["unidad_codigo"], pedido["cantidad"]) == (
        "10.000", "CAJA", "240.000"
    )
    assert pedido["en_bodega"] == "760.000"


async def test_lo_que_el_sistema_tiene_y_no_se_conto_se_ve_antes_de_aceptar(
    cliente, sesion, semilla, dia
):
    await obtener_manejador("corte.crear")(
        sesion, _ctx(dia, semilla), uuid.uuid4(),
        {"fecha_operativa": dia["dia"].isoformat(), "efectivo_declarado": "2250.00",
         "fecha_dispositivo": MOMENTO, "conteo": []},
    )
    await sesion.commit()
    r = await cliente.get("/v1/cierres", headers=await _cab(cliente, "GER01"))
    [renglon] = r.json()["pendientes"][0]["corte"]["renglones"]
    assert renglon["contado"] is False
    assert renglon["diferencia"] == "-60.000"


# ---------------------------------------------------------------------------
# Aceptar
# ---------------------------------------------------------------------------
async def test_aceptar_cierra_el_corte_y_confirma_la_carga_de_manana(
    cliente, sesion, semilla, dia
):
    corte_id = await _corte(sesion, dia, semilla)
    sid = await _solicitud(sesion, dia, semilla, corte_id=corte_id)
    r = await cliente.post(
        "/v1/cierres/aceptar",
        json={"corte_id": str(corte_id), "solicitud_id": str(sid)},
        headers=await _cab(cliente, "GER01"),
    )
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert "cerrada" in cuerpo["mensaje"]
    assert "confirmada para el" in cuerpo["mensaje"]
    assert cuerpo["solicitud"]["estado"] == "aceptada"
    folio = cuerpo["solicitud"]["carga_folio"]
    assert folio.startswith("CG-")

    # El corte: cerrado con lo que contó el vendedor y el efectivo que declaró.
    corte = (
        await sesion.execute(text("SELECT * FROM cortes_vendedor WHERE id = :c"),
                             {"c": corte_id})
    ).mappings().one()
    assert corte["estado"] == "cerrado"
    liquidacion = (
        await sesion.execute(text("SELECT * FROM liquidaciones WHERE id = :l"),
                             {"l": corte["liquidacion_id"]})
    ).mappings().one()
    assert liquidacion["estado"] == "cerrada"
    assert liquidacion["efectivo_entregado"] == Decimal("2200.00")
    contada = (
        await sesion.execute(
            text("SELECT cant_contada FROM liquidacion_detalle WHERE liquidacion_id = :l"),
            {"l": liquidacion["id"]},
        )
    ).scalar_one()
    assert contada == Decimal("55.000")
    # El efectivo que no entregó, a su cuenta.
    efectivo = (
        await sesion.execute(
            text("SELECT importe FROM cuenta_vendedor "
                 " WHERE liquidacion_id = :l AND origen = 'faltante_efectivo'"),
            {"l": liquidacion["id"]},
        )
    ).scalar_one()
    assert efectivo == Decimal("50.00")
    viejo = (
        await sesion.execute(text("SELECT estado FROM cargas WHERE id = :c"), {"c": dia["carga"]})
    ).scalar_one()
    assert viejo == "liquidada"

    # La carga de mañana: confirmada, de la bodega principal, con lo que pidió.
    carga = (
        await sesion.execute(text("SELECT * FROM cargas WHERE folio = :f"), {"f": folio})
    ).mappings().one()
    assert carga["estado"] == "confirmada"
    assert carga["fecha_operativa"] == dia["dia"] + timedelta(days=1)
    assert carga["almacen_origen_id"] == semilla["bodega"]
    cantidad = (
        await sesion.execute(text("SELECT cantidad FROM carga_detalle WHERE carga_id = :c"),
                             {"c": carga["id"]})
    ).scalar_one()
    assert cantidad == Decimal("240.000")
    # El camión: los 55 contados más los 240 de mañana.
    camion = (
        await sesion.execute(
            text("SELECT cantidad FROM existencias WHERE almacen_id = :a AND producto_id = :p"),
            {"a": semilla["camion"], "p": dia["producto"]},
        )
    ).scalar_one()
    assert camion == Decimal("295.000")

    # Y el teléfono se entera: aceptada, con el folio y lo aceptado.
    [delta] = await _deltas(sesion, sid)
    assert delta["estado"] == "aceptada"
    assert delta["carga_folio"] == folio
    assert delta["detalle"][0]["cantidad_aceptada"] == "240.000"


async def test_el_gerente_corrige_lo_pedido_antes_de_aceptar(cliente, sesion, semilla, dia):
    corte_id = await _corte(sesion, dia, semilla)
    sid = await _solicitud(sesion, dia, semilla, corte_id=corte_id, cajas=10)
    r = await cliente.post(
        "/v1/cierres/aceptar",
        json={"solicitud_id": str(sid), "bultos": {str(dia["producto"]): "4"}},
        headers=await _cab(cliente, "GER01"),
    )
    assert r.status_code == 200, r.text
    assert "Se cambiaron 1 renglón" in r.json()["mensaje"]
    # El corte se toma de la solicitud aunque no se mande.
    estado = (
        await sesion.execute(text("SELECT estado FROM cortes_vendedor WHERE id = :c"),
                             {"c": corte_id})
    ).scalar_one()
    assert estado == "cerrado"
    [pedido] = r.json()["solicitud"]["renglones"]
    assert pedido["cantidad_aceptada"] == "96.000"


async def test_quitar_todo_no_crea_una_carga_vacia(cliente, sesion, semilla, dia):
    sid = await _solicitud(sesion, dia, semilla)
    r = await cliente.post(
        "/v1/cierres/aceptar",
        json={"solicitud_id": str(sid), "bultos": {str(dia["producto"]): "0"}},
        headers=await _cab(cliente, "GER01"),
    )
    assert r.status_code == 409
    assert "rechaza la solicitud" in r.json()["detail"]


async def test_aceptar_dos_veces_no_carga_dos_veces(cliente, sesion, semilla, dia):
    corte_id = await _corte(sesion, dia, semilla)
    sid = await _solicitud(sesion, dia, semilla, corte_id=corte_id)
    cab = await _cab(cliente, "GER01")
    cuerpo = {"corte_id": str(corte_id), "solicitud_id": str(sid)}
    assert (await cliente.post("/v1/cierres/aceptar", json=cuerpo, headers=cab)).status_code == 200
    r = await cliente.post("/v1/cierres/aceptar", json=cuerpo, headers=cab)
    assert r.status_code == 409
    assert "ya está aceptada" in r.json()["detail"]
    cargas = (
        await sesion.execute(
            text("SELECT count(*) FROM cargas WHERE fecha_operativa = :d"),
            {"d": dia["dia"] + timedelta(days=1)},
        )
    ).scalar_one()
    assert cargas == 1


async def test_con_el_telefono_atrasado_no_se_acepta_y_se_puede_reintentar(
    cliente, sesion, semilla, dia
):
    corte_id = await _corte(sesion, dia, semilla)
    sid = await _solicitud(sesion, dia, semilla, corte_id=corte_id)
    await _reportar_cola(sesion, dia["dispositivo"], 3)
    cab = await _cab(cliente, "GER01")
    cuerpo = {"corte_id": str(corte_id), "solicitud_id": str(sid)}
    r = await cliente.post("/v1/cierres/aceptar", json=cuerpo, headers=cab)
    assert r.status_code == 409
    assert "sin subir" in r.json()["detail"]
    # Ni se cerró el corte ni se creó la carga.
    estado = (
        await sesion.execute(text("SELECT estado FROM solicitudes_carga WHERE id = :s"),
                             {"s": sid})
    ).scalar_one()
    assert estado == "pendiente"

    await _reportar_cola(sesion, dia["dispositivo"], 0)
    r = await cliente.post("/v1/cierres/aceptar", json=cuerpo, headers=cab)
    assert r.status_code == 200, r.text


async def test_una_solicitud_de_un_dia_que_ya_paso_no_se_acepta(cliente, sesion, semilla, dia):
    sid = await _solicitud(sesion, dia, semilla, para=date.today() - timedelta(days=1))
    r = await cliente.post(
        "/v1/cierres/aceptar", json={"solicitud_id": str(sid)},
        headers=await _cab(cliente, "GER01"),
    )
    assert r.status_code == 409
    assert "ya pasó" in r.json()["detail"]


async def test_si_ese_dia_ya_tiene_carga_no_se_crea_otra(cliente, sesion, semilla, dia):
    manana = dia["dia"] + timedelta(days=1)
    await sesion.execute(
        text(
            "INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id, "
            "                    vendedor_id, fecha_operativa, estado, confirmada_en) "
            "VALUES (:id, 'CG-MANO', :b, :c, :v, :d, 'confirmada', now())"
        ),
        {"id": uuid.uuid4(), "b": semilla["bodega"], "c": semilla["camion"],
         "v": semilla["vendedor"], "d": manana},
    )
    await sesion.commit()
    sid = await _solicitud(sesion, dia, semilla)
    r = await cliente.post(
        "/v1/cierres/aceptar", json={"solicitud_id": str(sid)},
        headers=await _cab(cliente, "GER01"),
    )
    assert r.status_code == 409
    assert "CG-MANO" in r.json()["detail"]
    assert "solo hay una carga al día" in r.json()["detail"]


async def test_rechazar_pide_motivo_y_se_lo_avisa_al_telefono(cliente, sesion, semilla, dia):
    sid = await _solicitud(sesion, dia, semilla)
    cab = await _cab(cliente, "GER01")
    r = await cliente.post(f"/v1/cierres/solicitudes/{sid}/rechazar",
                           json={"motivo": "  "}, headers=cab)
    assert r.status_code == 409
    r = await cliente.post(f"/v1/cierres/solicitudes/{sid}/rechazar",
                           json={"motivo": "Mañana no sales: es tu descanso"}, headers=cab)
    assert r.status_code == 200, r.text
    assert r.json()["solicitud"]["estado"] == "rechazada"
    [delta] = await _deltas(sesion, sid)
    assert delta["estado"] == "rechazada"
    assert delta["motivo"] == "Mañana no sales: es tu descanso"


async def test_el_vendedor_no_acepta_su_propio_cierre(cliente, sesion, semilla, dia):
    sid = await _solicitud(sesion, dia, semilla)
    r = await cliente.post("/v1/auth/login", json={
        "codigo": "VEND01", "password": PASSWORD_VENDEDOR,
        "dispositivo_id": str(dia["dispositivo"]),
    })
    cab = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await cliente.get("/v1/cierres", headers=cab)).status_code == 403
    r = await cliente.post("/v1/cierres/aceptar", json={"solicitud_id": str(sid)}, headers=cab)
    assert r.status_code == 403


async def test_la_carga_a_mano_se_abre_para_manana(cliente, sesion, semilla, dia):
    cab = await _cab(cliente, "GER01")
    opciones = (await cliente.get("/v1/cargas/opciones", headers=cab)).json()
    assert opciones["manana"] == (date.today() + timedelta(days=1)).isoformat()
    r = await cliente.post(
        "/v1/cargas",
        json={"vendedor_id": str(semilla["vendedor"]),
              "almacen_origen_id": str(semilla["bodega"])},
        headers=cab,
    )
    assert r.status_code == 200, r.text
    assert r.json()["fecha_operativa"] == opciones["manana"]


# ---------------------------------------------------------------------------
# El panel
# ---------------------------------------------------------------------------
async def test_el_panel_muestra_y_acepta(cliente, sesion, semilla, dia):
    corte_id = await _corte(sesion, dia, semilla)
    sid = await _solicitud(sesion, dia, semilla, corte_id=corte_id)
    await _entrar(cliente)
    r = await cliente.get("/panel/cierres")
    assert r.status_code == 200
    plano = solo_texto(r)
    assert "Juan" in plano or "VEND01" in plano
    assert "faltan $50.00" in plano
    assert "Atún en agua 140 g" in plano

    r = await cliente.post(
        "/panel/cierres/aceptar",
        data={"csrf": csrf_del_panel(cliente, r), "corte_id": str(corte_id),
              "solicitud_id": str(sid), f"bultos_{dia['producto']}": "6"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "confirmada para el" in solo_texto(r)
    cantidad = (
        await sesion.execute(
            text("SELECT d.cantidad FROM carga_detalle d JOIN cargas c ON c.id = d.carga_id "
                 " WHERE c.fecha_operativa = :d"),
            {"d": dia["dia"] + timedelta(days=1)},
        )
    ).scalar_one()
    assert cantidad == Decimal("144.000")
