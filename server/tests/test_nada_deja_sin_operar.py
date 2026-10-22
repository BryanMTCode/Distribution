"""Lo que podía dejar a un vendedor sin operar (ADR 0002 §172).

«Quiero que la app ya no vaya a tener problemas de sincronización, que ya
puedan operar y que no vaya a haber errores que los deje sin operar.» De la
revisión salieron, del lado del servidor:

1. **Un error nuestro no frena al teléfono.** Un bug en un manejador salía del
   push como 500: el teléfono reintentaba el mismo lote para siempre y no bajaba
   nada. Ahora ese sobre va a la cuarentena con «error interno» y los demás
   entran.
2. **La ubicación de una tienda sin ruta se guarda.** Con RLS, la corrección
   desde la ficha caía en cuarentena como «ese cliente no existe» —el mismo
   hueco que tuvo la venta (§165)—. La de otra ruta se sigue rechazando.
3. **La cuarentena frena solo lo que explica una diferencia.** Antes cualquier
   pendiente, de cualquier día, frenaba el corte y con él la carga de mañana.
4. **Lo que el servidor no pudo leer queda a la vista.** Un 422 no dejaba rastro
   en la cuarentena; ahora sí, y si después entra se da por resuelto solo.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.api.admin.cargas import _bloqueos_para_cargar
from app.api.admin.liquidaciones import _bloqueos_para_cerrar
from app.domain.sync.sobres import Operacion
from app.infra.sync import ingesta
from tests.ayudas_sync import a_json, operacion_cliente, sobre
from tests.test_rls import cliente_rls, motor_restringido, rol_api  # noqa: F401
from tests.test_venta_ingesta import RELOJ_DISPOSITIVO, escenario, payload  # noqa: F401
from tests.test_venta_tienda_que_no_ve import _cab_vendedor

pytestmark = pytest.mark.asyncio


async def _cuarentena(sesion) -> list[dict]:
    await sesion.rollback()
    return [
        dict(f)
        for f in (
            await sesion.execute(
                text(
                    "SELECT operacion_id, tipo, error_codigo, error_mensaje, estado, "
                    "       nota_resolucion FROM sync_cuarentena ORDER BY id"
                )
            )
        ).mappings()
    ]


# ---------------------------------------------------------------------------
# 1. Un error nuestro no frena al teléfono
# ---------------------------------------------------------------------------


async def test_un_bug_en_una_operacion_no_tumba_el_lote(
    cliente, sesion, escenario, monkeypatch  # noqa: F811
):
    malo = uuid.uuid4()
    original = ingesta.obtener_manejador

    def con_un_bug(tipo):
        manejador = original(tipo)

        async def envuelto(sesion, ctx, entidad_id, datos):
            if entidad_id == malo:
                raise RuntimeError("un bug de prueba")
            return await manejador(sesion, ctx, entidad_id, datos)

        return envuelto

    monkeypatch.setattr(ingesta, "obtener_manejador", con_un_bug)
    cab = await _cab_vendedor(cliente, escenario)
    buena = sobre(operacion_cliente("Abarrotes Lupita"), secuencia=1)
    con_bug = sobre(Operacion("cliente.crear", malo, {"nombre_comercial": "Tienda X"}),
                    secuencia=2)

    r = await cliente.post("/v1/sync/push", json=a_json([buena, con_bug]), headers=cab)

    assert r.status_code == 200, "antes: 500, y el teléfono no volvía a bajar nada"
    resultados = {x["operacion_id"]: x for x in r.json()["resultados"]}
    assert resultados[str(buena.operacion_id)]["estado"] == "aceptada"
    assert resultados[str(con_bug.operacion_id)]["estado"] == "rechazada"
    assert resultados[str(con_bug.operacion_id)]["error_codigo"] == "error_interno"
    [fila] = await _cuarentena(sesion)
    assert fila["error_codigo"] == "error_interno"
    assert "RuntimeError: un bug de prueba" in fila["error_mensaje"]


# ---------------------------------------------------------------------------
# 2. La ubicación de una tienda sin ruta
# ---------------------------------------------------------------------------


def _ubicar(entidad_id) -> dict:
    return a_json([
        sobre(Operacion("cliente.ubicar", entidad_id, {
            "lat": "23.2494100", "lng": "-106.4111400", "ubicacion_origen": "gps",
            "ubicacion_precision_m": "8.50", "fecha_dispositivo": RELOJ_DISPOSITIVO,
        }))
    ])


async def test_la_ubicacion_de_una_tienda_sin_ruta_se_guarda_con_rls(
    cliente_rls, sesion, escenario  # noqa: F811
):
    await sesion.execute(
        text("UPDATE clientes SET ruta_id = NULL WHERE id = :c"), {"c": escenario["cliente"]}
    )
    await sesion.commit()
    cab = await _cab_vendedor(cliente_rls, escenario)

    r = await cliente_rls.post("/v1/sync/push", json=_ubicar(escenario["cliente"]),
                               headers=cab)

    assert r.json()["aceptadas"] == 1, r.json()["resultados"]
    await sesion.rollback()
    fila = (
        await sesion.execute(
            text("SELECT lat, lng, ubicacion_origen, ruta_id FROM clientes WHERE id = :c"),
            {"c": escenario["cliente"]},
        )
    ).mappings().one()
    assert (str(fila["lat"]), str(fila["lng"]), fila["ubicacion_origen"]) == (
        "23.2494100", "-106.4111400", "gps"
    )
    assert fila["ruta_id"] is None, "solo cambia la ubicación"
    assert (
        await sesion.execute(
            text("SELECT count(*) FROM auditoria WHERE entidad = 'cliente' "
                 "   AND entidad_id = :c AND accion = 'ubicacion'"),
            {"c": escenario["cliente"]},
        )
    ).scalar_one() == 1


async def test_la_de_otra_ruta_se_sigue_rechazando_con_rls(
    cliente_rls, sesion, escenario  # noqa: F811
):
    otra = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO rutas (id, codigo, nombre) VALUES (:r, 'R77', 'Ruta 77')"), {"r": otra}
    )
    await sesion.execute(
        text("UPDATE clientes SET ruta_id = :r WHERE id = :c"),
        {"r": otra, "c": escenario["cliente"]},
    )
    await sesion.commit()
    cab = await _cab_vendedor(cliente_rls, escenario)

    r = await cliente_rls.post("/v1/sync/push", json=_ubicar(escenario["cliente"]),
                               headers=cab)

    [resultado] = r.json()["resultados"]
    assert resultado["estado"] == "rechazada"
    assert "su ubicación la mueve la oficina" in resultado["error_mensaje"]


# ---------------------------------------------------------------------------
# 3. La cuarentena frena solo lo que explica una diferencia
# ---------------------------------------------------------------------------


async def _pendiente(sesion, escenario, tipo: str, *, recibido_hace_dias: int = 0):  # noqa: F811
    await sesion.execute(
        text(
            "INSERT INTO sync_cuarentena (operacion_id, dispositivo_id, usuario_id, tipo, "
            "  payload, hash_payload, error_codigo, error_mensaje, recibido_en) "
            "VALUES (:o, :d, :u, :t, '{}', 'x', 'conflicto_de_datos', 'de prueba', "
            "        now() - make_interval(days => :dias))"
        ),
        {"o": uuid.uuid4(), "d": escenario["dispositivo"], "u": escenario["vendedor"],
         "t": tipo, "dias": recibido_hace_dias},
    )
    await sesion.commit()


async def _frena_el_corte(sesion, escenario) -> bool:  # noqa: F811
    bloqueos = await _bloqueos_para_cerrar(
        sesion, {"vendedor_id": escenario["vendedor"], "fecha_operativa": date.today()}
    )
    return any("cuarentena" in b for b in bloqueos)


async def test_una_ubicacion_o_un_no_drop_en_cuarentena_no_frenan_el_corte(
    sesion, escenario  # noqa: F811
):
    await _pendiente(sesion, escenario, "cliente.ubicar")
    await _pendiente(sesion, escenario, "no_drop.crear")
    await _pendiente(sesion, escenario, "cliente.crear")

    assert not await _frena_el_corte(sesion, escenario)


async def test_la_venta_de_hoy_en_cuarentena_si_frena(sesion, escenario):  # noqa: F811
    await _pendiente(sesion, escenario, "cliente.crear+venta.crear")

    assert await _frena_el_corte(sesion, escenario)
    bloqueos = await _bloqueos_para_cerrar(
        sesion, {"vendedor_id": escenario["vendedor"], "fecha_operativa": date.today()}
    )
    assert any("Reprocesar o Descartar" in b for b in bloqueos)


async def test_la_venta_de_un_dia_ya_cerrado_no_frena_el_de_hoy(sesion, escenario):  # noqa: F811
    """Llegó y se quedó en cuarentena hace una semana: no cambia la cuenta de hoy."""
    await _pendiente(sesion, escenario, "venta.crear", recibido_hace_dias=7)

    assert not await _frena_el_corte(sesion, escenario)


async def test_la_carga_de_manana_tampoco_la_frena_lo_viejo(sesion, escenario):  # noqa: F811
    ayer = date.today() - timedelta(days=1)
    await sesion.execute(
        text(
            "INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id, "
            "  vendedor_id, ruta_id, fecha_operativa, estado) "
            "VALUES (gen_random_uuid(), 'CG-0001', :b, :c, :v, :r, :d, 'liquidada')"
        ),
        {"b": escenario["bodega"], "c": escenario["camion"], "v": escenario["vendedor"],
         "r": escenario["ruta"], "d": ayer},
    )
    await sesion.commit()
    await _pendiente(sesion, escenario, "venta.crear", recibido_hace_dias=5)
    await _pendiente(sesion, escenario, "cliente.ubicar")

    bloqueos = await _bloqueos_para_cargar(
        sesion, escenario["vendedor"], date.today() + timedelta(days=1)
    )

    assert not any("cuarentena" in b for b in bloqueos), bloqueos

    await _pendiente(sesion, escenario, "merma.crear")
    bloqueos = await _bloqueos_para_cargar(
        sesion, escenario["vendedor"], date.today() + timedelta(days=1)
    )
    assert any("cuarentena" in b for b in bloqueos)


# ---------------------------------------------------------------------------
# 4. Lo que el servidor no pudo leer queda a la vista
# ---------------------------------------------------------------------------


async def test_un_lote_invalido_queda_en_cuarentena_y_se_resuelve_al_entrar(
    cliente, sesion, escenario  # noqa: F811
):
    cab = await _cab_vendedor(cliente, escenario)
    uno = sobre(operacion_cliente("Abarrotes Lupita"), secuencia=1)
    otro = sobre(operacion_cliente("Tienda Chela"), secuencia=1)  # la misma secuencia

    r = await cliente.post("/v1/sync/push", json=a_json([uno, otro]), headers=cab)

    assert r.status_code == 422
    filas = await _cuarentena(sesion)
    assert {f["operacion_id"] for f in filas} == {uno.operacion_id, otro.operacion_id}
    assert {f["tipo"] for f in filas} == {"cliente.crear"}
    assert all("no pudo leer" in f["error_mensaje"] for f in filas)

    # El teléfono los reintenta de uno en uno: entran, y dejan de estar pendientes.
    for s in (uno, otro):
        r = await cliente.post("/v1/sync/push", json=a_json([s]), headers=cab)
        assert r.json()["aceptadas"] == 1
    filas = await _cuarentena(sesion)
    assert {f["estado"] for f in filas} == {"reprocesada"}
    assert {f["nota_resolucion"] for f in filas} == {
        "Entró sola en un envío posterior del teléfono."
    }


async def test_un_push_que_no_encaja_en_el_esquema_queda_en_cuarentena(
    cliente, sesion, escenario  # noqa: F811
):
    cab = await _cab_vendedor(cliente, escenario)
    cuerpo = a_json([sobre(operacion_cliente("Abarrotes Lupita"))])
    operacion_id = cuerpo["sobres"][0]["operacion_id"]
    del cuerpo["sobres"][0]["hash_payload"]

    r = await cliente.post("/v1/sync/push", json=cuerpo, headers=cab)

    assert r.status_code == 422
    [fila] = await _cuarentena(sesion)
    assert str(fila["operacion_id"]) == operacion_id
    assert fila["tipo"] == "cliente.crear"
    assert fila["error_codigo"] == "payload_invalido"
    assert "hash_payload" in fila["error_mensaje"]


async def test_sin_token_el_422_no_aparta_nada(cliente, sesion):
    r = await cliente.post("/v1/sync/push", json={"lote_id": "x", "sobres": [{}]})

    assert r.status_code in (401, 422)
    assert await _cuarentena(sesion) == []


async def test_con_rls_tambien_queda_a_la_vista(cliente_rls, sesion, escenario):  # noqa: F811
    """Con el rol de producción: el lote inválido y el que no encaja en el esquema."""
    cab = await _cab_vendedor(cliente_rls, escenario)
    uno = sobre(operacion_cliente("Abarrotes Lupita"), secuencia=1)
    otro = sobre(operacion_cliente("Tienda Chela"), secuencia=1)
    r = await cliente_rls.post("/v1/sync/push", json=a_json([uno, otro]), headers=cab)
    assert r.status_code == 422

    cuerpo = a_json([sobre(operacion_cliente("Mini súper Paty"))])
    del cuerpo["sobres"][0]["hash_payload"]
    r = await cliente_rls.post("/v1/sync/push", json=cuerpo, headers=cab)
    assert r.status_code == 422

    assert len(await _cuarentena(sesion)) == 3
