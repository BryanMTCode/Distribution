"""El mapa de Mazatlán para los teléfonos (ADR 0002 §130).

1. **La extracción**: de un PMTiles grande (con directorios en hojas, como el de
   Protomaps) sale uno con solo las teselas de la zona, idénticas, y con pocas
   peticiones: los directorios una vez y las teselas en tramos.
2. **La zona**: la de las tiendas con margen y al menos Mazatlán; una
   coordenada mal puesta en Culiacán no la estira, pero se cuenta como fuera.
3. **La fuente**: el corte diario más reciente que exista.
4. **Lo que ven los teléfonos**: sin mapa, `disponible: false` y 404; con mapa,
   su ficha y el archivo, solo con credencial. `/salud` dice qué versión hay.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date
from pathlib import Path

import pytest
from pmtiles.reader import MemorySource, Reader
from pmtiles.tile import Compression, TileType, tileid_to_zxy
from pmtiles.writer import Writer
from sqlalchemy import text

from app.core.config import obtener_config
from app.infra import mapa
from tests.conftest import PASSWORD_VENDEDOR

# Un cuadro de dos grados alrededor de Mazatlán: con zoom 15 son decenas de miles
# de teselas, y la raíz ya no cabe en 16 KB, así que la fuente lleva hojas.
FUENTE_ZONA = (-107.5, 22.3, -105.5, 24.3)


def _contenido(z: int, x: int, y: int) -> bytes:
    # Una de cada siete es «mar»: el mismo contenido repetido, como en la fuente
    # de verdad, donde se guarda una vez.
    return b"mar" if (x + y) % 7 == 0 else f"{z}/{x}/{y}".encode()


@pytest.fixture(scope="module")
def fuente() -> bytes:
    import io

    teselas = mapa.teselas_de_la_zona(FUENTE_ZONA, 0, 15)
    buf = io.BytesIO()
    escritor = Writer(buf)
    for tid in teselas:
        escritor.write_tile(tid, _contenido(*tileid_to_zxy(tid)))
    escritor.finalize(
        {
            "tile_type": TileType.MVT,
            "tile_compression": Compression.GZIP,
            "min_lon_e7": int(FUENTE_ZONA[0] * 1e7),
            "min_lat_e7": int(FUENTE_ZONA[1] * 1e7),
            "max_lon_e7": int(FUENTE_ZONA[2] * 1e7),
            "max_lat_e7": int(FUENTE_ZONA[3] * 1e7),
        },
        {"name": "protomaps basemap", "attribution": "© OpenStreetMap",
         "vector_layers": [{"id": "roads"}]},
    )
    datos = buf.getvalue()
    cabecera = Reader(MemorySource(datos)).header()
    assert cabecera["leaf_directory_length"] > 0, "la fuente de prueba debe llevar hojas"
    return datos


def _lector_contado(datos: bytes):
    pedidos: list[tuple[int, int]] = []

    def leer(desde: int, cuantos: int) -> bytes:
        pedidos.append((desde, cuantos))
        return datos[desde:desde + cuantos]

    return leer, pedidos


def test_extrae_solo_la_zona_identica_y_en_pocas_peticiones(fuente, tmp_path):
    leer, pedidos = _lector_contado(fuente)
    destino = tmp_path / "mazatlan.pmtiles"
    resultado = mapa.extraer(leer, mapa.MAZATLAN, destino, fuente="prueba")

    esperadas = mapa.teselas_de_la_zona(mapa.MAZATLAN, 0, 15)
    assert resultado.teselas == len(esperadas)
    assert resultado.peticiones == len(pedidos) <= 20

    lector = Reader(MmapSourceDe(destino))
    # Las de la zona, iguales a la fuente; el centro y una esquina.
    for lon, lat in ((-106.42, 23.23), (-106.49, 23.14), (-106.29, 23.32)):
        for z in (5, 12, 15):
            x, y = mapa._x(lon, z), mapa._y(lat, z)
            assert lector.get(z, x, y) == _contenido(z, x, y), (z, x, y)
    # Culiacán no viene.
    z = 15
    assert lector.get(z, mapa._x(-107.39, z), mapa._y(24.80, z)) is None
    assert lector.get(z, mapa._x(-105.6, z), mapa._y(22.4, z)) is None

    cabecera = lector.header()
    assert cabecera["max_zoom"] == 15 and cabecera["min_zoom"] == 0
    assert cabecera["min_lon_e7"] == round(mapa.MAZATLAN[0] * 1e7)
    assert cabecera["max_lat_e7"] == round(mapa.MAZATLAN[3] * 1e7)
    assert cabecera["tile_type"] == TileType.MVT
    assert cabecera["tile_compression"] == Compression.GZIP
    meta = lector.metadata()
    assert meta["attribution"] == "© OpenStreetMap"
    assert meta["vector_layers"] == [{"id": "roads"}]
    assert meta["dsd"]["zona"] == list(mapa.MAZATLAN)
    # El «mar» repetido se guarda una vez.
    assert cabecera["tile_contents_count"] < cabecera["addressed_tiles_count"]
    # Mucho más chico que la fuente.
    assert resultado.bytes < len(fuente) / 5
    assert not destino.with_suffix(".pmtiles.parcial").exists()


def MmapSourceDe(ruta: Path):  # noqa: N802 — igual que MmapSource de pmtiles
    datos = ruta.read_bytes()
    return MemorySource(datos)


def test_con_zoom_14_pesa_menos(fuente, tmp_path):
    leer, _ = _lector_contado(fuente)
    con15 = mapa.extraer(leer, mapa.MAZATLAN, tmp_path / "a.pmtiles")
    con14 = mapa.extraer(leer, mapa.MAZATLAN, tmp_path / "b.pmtiles", zoom_maximo=14)
    assert con14.zoom_maximo == 14 and con14.teselas < con15.teselas / 3
    assert Reader(MmapSourceDe(tmp_path / "b.pmtiles")).header()["max_zoom"] == 14


def test_una_fuente_que_no_es_pmtiles_no_toca_el_mapa_que_habia(tmp_path):
    destino = tmp_path / "mazatlan.pmtiles"
    destino.write_bytes(b"el de ayer")
    with pytest.raises(mapa.MapaInvalido, match="no es un PMTiles"):
        mapa.extraer(lambda d, c: (b"<html>no encontrado</html>" * 10)[d:d + c],
                     mapa.MAZATLAN, destino)
    assert destino.read_bytes() == b"el de ayer"


def test_la_zona_es_la_de_las_tiendas_y_al_menos_mazatlan():
    assert mapa.zona_de_las_tiendas(None) == mapa.MAZATLAN
    # Tiendas en Villa Unión: se estira al este, con su margen.
    zona = mapa.zona_de_las_tiendas((-106.45, 23.18, -106.21, 23.25))
    assert zona[2] == pytest.approx(-106.19)
    assert zona[0] == mapa.MAZATLAN[0] and zona[3] == mapa.MAZATLAN[3]
    # Medio estado: es un error, no un mapa.
    with pytest.raises(mapa.MapaInvalido, match="demasiado grande"):
        mapa.zona_de_las_tiendas((-107.9, 23.1, -106.2, 25.0))
    with pytest.raises(mapa.MapaInvalido):
        mapa.validar_zona((-106.2, 23.1, -106.5, 23.3))   # al revés


def test_la_fuente_es_el_corte_mas_reciente_que_haya():
    hay = {"https://build.protomaps.com/20261015.pmtiles"}
    assert mapa.fuente_reciente(date(2026, 10, 18), existe=hay.__contains__) == (
        "https://build.protomaps.com/20261015.pmtiles"
    )
    with pytest.raises(mapa.MapaInvalido, match="--fuente"):
        mapa.fuente_reciente(date(2026, 10, 18), existe=lambda _: False)



@pytest.fixture
def mapas_en(tmp_path, monkeypatch):
    monkeypatch.setattr(obtener_config(), "mapas_dir", str(tmp_path))
    return tmp_path


async def _poner_tiendas(sesion, semilla, puntos):
    for i, (lng, lat) in enumerate(puntos):
        await sesion.execute(
            text(
                "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, estatus, "
                "creado_en, actualizado_en, lat, lng) VALUES (:c, :cod, :n, :r, "
                "'activo', now(), now(), :lat, :lng)"
            ),
            {"c": uuid.uuid4(), "cod": f"M{i:05d}", "n": f"Tienda {i}",
             "r": semilla["ruta"], "lng": lng, "lat": lat},
        )
    await sesion.commit()


@pytest.mark.asyncio
async def test_una_tienda_mal_ubicada_no_estira_el_mapa_pero_se_cuenta(sesion, semilla):
    # 150 tiendas en Mazatlán y una «en Culiacán».
    puntos = [(-106.42 + (i % 15) * 0.004, 23.20 + (i // 15) * 0.008) for i in range(150)]
    await _poner_tiendas(sesion, semilla, [*puntos, (-107.39, 24.80)])
    percentiles, con_ubicacion = await mapa.percentiles_de_las_tiendas(sesion)
    assert con_ubicacion == 151
    zona = mapa.zona_de_las_tiendas(percentiles)
    assert zona == mapa.MAZATLAN
    assert await mapa.tiendas_fuera(sesion, zona) == 1


def _dejar_mapa(directorio: Path, contenido: bytes = b"PMTiles de prueba") -> mapa.FichaDelMapa:
    (directorio / mapa.ARCHIVO).write_bytes(contenido)
    extraccion = mapa.Extraccion(
        teselas=3, bytes=len(contenido), peticiones=1, zona=mapa.MAZATLAN, zoom_maximo=15
    )
    return mapa.guardar_ficha(directorio, extraccion, version="20261017")


async def _token_vendedor(cliente, sesion, semilla) -> dict[str, str]:
    from tests.test_panel_liquidacion import sembrar_dia_de_trabajo

    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    r = await cliente.post("/v1/auth/login", json={
        "codigo": "VEND01", "password": PASSWORD_VENDEDOR,
        "dispositivo_id": str(dia["dispositivo"]),
    })
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_sin_mapa_lo_dice(cliente, sesion, semilla, mapas_en):
    cab = await _token_vendedor(cliente, sesion, semilla)
    r = await cliente.get("/v1/mapa", headers=cab)
    assert r.status_code == 200 and r.json()["disponible"] is False
    r = await cliente.get("/v1/mapa/archivo", headers=cab)
    assert r.status_code == 404 and "mapa-mazatlan" in r.text
    assert (await cliente.get("/salud")).json()["mapa"] is None


@pytest.mark.asyncio
async def test_con_mapa_el_vendedor_lo_baja_con_su_credencial(cliente, sesion, semilla, mapas_en):
    contenido = b"PMTiles" + bytes(range(256)) * 40
    ficha = _dejar_mapa(mapas_en, contenido)
    assert ficha.sha256 == hashlib.sha256(contenido).hexdigest()

    # Sin credencial, nada.
    assert (await cliente.get("/v1/mapa")).status_code == 401
    assert (await cliente.get("/v1/mapa/archivo")).status_code == 401

    cab = await _token_vendedor(cliente, sesion, semilla)
    info = (await cliente.get("/v1/mapa", headers=cab)).json()
    assert info == {
        "disponible": True, "version": "20261017", "bytes": len(contenido),
        "sha256": ficha.sha256, "zona": list(mapa.MAZATLAN), "zoom_maximo": 15,
        "atribucion": "© OpenStreetMap",
    }
    r = await cliente.get("/v1/mapa/archivo", headers=cab)
    assert r.status_code == 200
    assert r.content == contenido
    assert r.headers["x-mapa-version"] == "20261017"
    assert (await cliente.get("/salud")).json()["mapa"] == "20261017"


def test_la_ficha_sin_su_archivo_no_cuenta(tmp_path):
    _dejar_mapa(tmp_path)
    assert mapa.leer_ficha(tmp_path).version == "20261017"
    (tmp_path / mapa.ARCHIVO).unlink()
    assert mapa.leer_ficha(tmp_path) is None
    (tmp_path / mapa.FICHA).write_text("{roto", encoding="utf-8")
    assert mapa.leer_ficha(tmp_path) is None


@pytest.mark.asyncio
async def test_el_comando_baja_la_zona_de_las_tiendas(
    motor, sesion, semilla, mapas_en, fuente, monkeypatch, capsys
):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    monkeypatch.setattr(
        "app.core.db.CrearSesion",
        async_sessionmaker(motor, expire_on_commit=False, class_=AsyncSession),
    )
    from app.cli import mapa_mazatlan

    await _poner_tiendas(sesion, semilla, [(-106.42, 23.23), (-106.21, 23.19)])
    # Con --resumen dice la zona y no baja nada.
    assert await mapa_mazatlan(["--resumen"]) == 0
    salida = capsys.readouterr().out
    assert "Sale de 2 tiendas con ubicación, más Mazatlán entero." in salida
    assert "Con --resumen no se baja nada." in salida
    assert not (mapas_en / mapa.ARCHIVO).exists()

    # Sin internet en las pruebas: la fuente es la de juguete.
    monkeypatch.setattr(mapa, "lector_http", lambda url: _lector_contado(fuente)[0])
    url = "https://build.protomaps.com/20261017.pmtiles"
    assert await mapa_mazatlan(["--fuente", url, "--zoom", "14"]) == 0
    salida = capsys.readouterr().out
    assert "Listo:" in salida and "Versión 20261017." in salida
    ficha = mapa.leer_ficha(mapas_en)
    assert ficha.version == "20261017" and ficha.zoom_maximo == 14
    # Villa Unión entra.
    assert ficha.zona[2] > -106.21

    # Una zona que no tiene sentido no toca el mapa que hay.
    assert await mapa_mazatlan(["--zona", "-106.2,23.1"]) == 1
    assert "oeste,sur,este,norte" in capsys.readouterr().out
    assert mapa.leer_ficha(mapas_en).version == "20261017"
