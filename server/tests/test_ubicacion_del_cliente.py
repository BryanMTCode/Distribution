"""La ubicación del cliente: con GPS o escrita a mano (ADR 0002 §84).

1. **Qué es una coordenada válida**, una sola regla para los tres caminos: el
   (0, 0) no, la latitud y la longitud al revés no, la longitud de México sin el
   signo menos tampoco.
2. **El vendedor** la fija sin señal (`cliente.ubicar`), solo de su ruta.
3. **La oficina** la corrige desde su app o desde el panel.
4. Cada cambio deja la anterior escrita, y publica el cliente a los teléfonos.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.infra.sync.manejadores import Contexto, ErrorDeManejador, obtener_manejador
from app.infra.ubicacion_cliente import UbicacionInvalida, leer_coordenadas
from tests.conftest import csrf_del_panel
from tests.test_cortes_api import _cab, _usuario
from tests.test_panel_liquidacion import _entrar

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def tienda(sesion, semilla) -> uuid.UUID:
    cliente = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, creado_en, "
            "                      actualizado_en) "
            "VALUES (:c, 'CLI-U1', 'La Esquina', :r, now(), now())"
        ),
        {"c": cliente, "r": semilla["ruta"]},
    )
    await sesion.commit()
    return cliente


async def _ubicacion(sesion, cliente) -> dict:
    return dict(
        (
            await sesion.execute(
                text("SELECT lat, lng, ubicacion_origen, ubicacion_precision_m "
                     "  FROM clientes WHERE id = :c"),
                {"c": cliente},
            )
        ).mappings().one()
    )


# ---------------------------------------------------------------------------
# La regla
# ---------------------------------------------------------------------------
async def test_una_coordenada_buena_se_lee_con_siete_decimales():
    assert leer_coordenadas(" 23.24941 ", "-106.41114") == (
        Decimal("23.2494100"), Decimal("-106.4111400")
    )


@pytest.mark.parametrize(
    ("lat", "lng", "dice"),
    [
        ("0", "0", "mar"),
        ("-106.41", "23.24", "al revés"),
        ("23.24", "106.41", "negativa"),
        ("91", "-106", "entre −90 y 90"),
        ("23.2", "-181", "entre −180 y 180"),
        ("veintitrés", "-106", "no es una latitud"),
        ("", "-106", "Falta la latitud"),
    ],
)
async def test_lo_que_no_se_guarda(lat, lng, dice):
    with pytest.raises(UbicacionInvalida, match=dice):
        leer_coordenadas(lat, lng)


# ---------------------------------------------------------------------------
# El vendedor, sin señal
# ---------------------------------------------------------------------------
def _ctx(semilla, rutas=None) -> Contexto:
    return Contexto(
        dispositivo_id=uuid.uuid4(),
        usuario_id=semilla["vendedor"],
        rutas=tuple(rutas if rutas is not None else [semilla["ruta"]]),
    )


async def test_el_vendedor_la_fija_con_gps_y_queda_la_anterior(sesion, semilla, tienda):
    ctx = _ctx(semilla)
    await sesion.execute(
        text("INSERT INTO dispositivos (id, usuario_id, etiqueta, estado) "
             "VALUES (:d, :u, 'POCO', 'activo')"),
        {"d": ctx.dispositivo_id, "u": semilla["vendedor"]},
    )
    await sesion.commit()
    await obtener_manejador("cliente.ubicar")(
        sesion, ctx, tienda,
        {"lat": "23.2494100", "lng": "-106.4111400", "ubicacion_origen": "gps",
         "ubicacion_precision_m": "8.50", "fecha_dispositivo": "2026-10-07T18:00:00Z"},
    )
    await sesion.commit()
    assert await _ubicacion(sesion, tienda) == {
        "lat": Decimal("23.2494100"), "lng": Decimal("-106.4111400"),
        "ubicacion_origen": "gps", "ubicacion_precision_m": Decimal("8.50"),
    }
    antes = (
        await sesion.execute(
            text("SELECT datos_antes FROM auditoria "
                 " WHERE entidad = 'cliente' AND entidad_id = :c AND accion = 'ubicacion'"),
            {"c": tienda},
        )
    ).scalar_one()
    assert antes == {"lat": None, "lng": None, "origen": None}
    # Y el cliente viaja a los teléfonos de su ruta con la ubicación nueva.
    payload = (
        await sesion.execute(
            text("SELECT payload FROM change_log WHERE entidad = 'cliente' "
                 "   AND entidad_id = :c ORDER BY cursor DESC LIMIT 1"),
            {"c": tienda},
        )
    ).scalar_one()
    assert Decimal(str(payload["lat"])) == Decimal("23.2494100")


async def test_de_otra_ruta_no(sesion, semilla, tienda):
    with pytest.raises(ErrorDeManejador, match="no es de la ruta"):
        await obtener_manejador("cliente.ubicar")(
            sesion, _ctx(semilla, rutas=[]), tienda,
            {"lat": "23.24", "lng": "-106.41", "ubicacion_origen": "manual",
             "fecha_dispositivo": "2026-10-07T18:00:00Z"},
        )


async def test_una_mal_escrita_va_a_cuarentena(sesion, semilla, tienda):
    with pytest.raises(ErrorDeManejador, match="negativa"):
        await obtener_manejador("cliente.ubicar")(
            sesion, _ctx(semilla), tienda,
            {"lat": "23.24", "lng": "106.41", "ubicacion_origen": "manual",
             "fecha_dispositivo": "2026-10-07T18:00:00Z"},
        )


# ---------------------------------------------------------------------------
# La oficina
# ---------------------------------------------------------------------------
async def test_el_gerente_la_corrige_desde_su_app(cliente, sesion, semilla, tienda):
    await _usuario(sesion, semilla, "GER01", "gerente")
    cab = await _cab(cliente, "GER01")
    ficha = (await cliente.get(f"/v1/oficina/clientes/{tienda}", headers=cab)).json()
    assert ficha["puede_ubicar"] is True
    assert ficha["lat"] is None

    r = await cliente.post(f"/v1/oficina/clientes/{tienda}/ubicacion",
                           json={"lat": "23.2", "lng": "106.4"}, headers=cab)
    assert r.status_code == 409
    assert "negativa" in r.json()["detail"]

    r = await cliente.post(
        f"/v1/oficina/clientes/{tienda}/ubicacion",
        json={"lat": "23.2494", "lng": "-106.4111", "origen": "gps", "precision_m": "12"},
        headers=cab,
    )
    assert r.status_code == 200, r.text
    assert r.json()["ubicacion_origen"] == "gps"
    assert Decimal(r.json()["lat"]) == Decimal("23.2494")
    assert "sincronizar" in r.json()["mensaje"]


async def test_un_cliente_que_no_existe_es_404(cliente, sesion, semilla):
    await _usuario(sesion, semilla, "GER01", "gerente")
    r = await cliente.post(f"/v1/oficina/clientes/{uuid.uuid4()}/ubicacion",
                           json={"lat": "23.2", "lng": "-106.4"},
                           headers=await _cab(cliente, "GER01"))
    assert r.status_code == 404


async def test_el_panel_la_corrige_a_mano(cliente, sesion, semilla, tienda):
    await _entrar(cliente)
    pagina = await cliente.get(f"/panel/clientes/{tienda}")
    assert "forma_ubicacion" in pagina.text
    r = await cliente.post(
        f"/panel/clientes/{tienda}/ubicacion",
        data={"csrf": csrf_del_panel(cliente, pagina), "lat": "23.25", "lng": "-106.42"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    ubicacion = await _ubicacion(sesion, tienda)
    assert (ubicacion["lat"], ubicacion["ubicacion_origen"]) == (Decimal("23.2500000"), "manual")
