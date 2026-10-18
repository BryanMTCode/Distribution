"""El mapa de Mazatlán para los teléfonos (ADR 0002 §130).

«Yo quiero que les aparezca el mapa en la app… tal vez podría descargar el mapa
de Mazatlán o algo así.» Y la restricción de antes sigue en pie: el vendedor
trabaja sin señal, y un mapa que baja sus teselas en vivo se queda en blanco.

────────────────────────────────────────────────────────────────────────────
DE DÓNDE SALE
────────────────────────────────────────────────────────────────────────────
De OpenStreetMap, en el corte diario que publica Protomaps
(`build.protomaps.com/AAAAMMDD.pmtiles`): el planeta entero en un solo archivo
PMTiles, vectorial. Un PMTiles se puede leer **por pedazos** con peticiones de
rango, así que de ese archivo de cien y tantos GB se baja solo la zona de las
tiendas, hasta el zoom de calle. Son pocas decenas de MB.

Lo hace el servidor, una vez, con `python -m app.cli mapa-mazatlan`. Los
teléfonos no salen a internet por el mapa: se lo piden al servidor con su
credencial (`GET /v1/mapa/archivo`), con WiFi, y lo guardan.

────────────────────────────────────────────────────────────────────────────
QUÉ ZONA
────────────────────────────────────────────────────────────────────────────
La de las tiendas que tienen ubicación, con un margen, y siempre al menos la
mancha urbana de Mazatlán. Se usan los percentiles 1 y 99 y no el mínimo y el
máximo: una coordenada mal capturada en Culiacán (o al revés, en el mar) no debe
volver el mapa de medio estado. Las que queden fuera se cuentan y se dicen.

────────────────────────────────────────────────────────────────────────────
POCAS PETICIONES
────────────────────────────────────────────────────────────────────────────
Pedir cada tesela por separado serían miles de idas y vueltas. Se hace como
`pmtiles extract`: primero se leen los directorios (la cabecera y la raíz en
una sola petición, cada hoja una vez), y luego las teselas que se necesitan se
juntan en tramos contiguos del archivo, que en un PMTiles agrupado están casi
seguidas. Una docena de peticiones para toda la ciudad.

La licencia de OpenStreetMap (ODbL) pide decir de dónde salen los datos: el
archivo lo lleva en sus metadatos y la app lo pone sobre el mapa.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from pmtiles.tile import (
    Compression,
    deserialize_directory,
    deserialize_header,
    find_tile,
    zxy_to_tileid,
)
from pmtiles.writer import Writer

from app.core.config import obtener_config

ATRIBUCION = "© OpenStreetMap"
FUENTE_DIARIA = "https://build.protomaps.com/{fecha}.pmtiles"

ARCHIVO = "mazatlan.pmtiles"
FICHA = "mazatlan.json"

# Zoom 15 es el de calle: todas las calles con su nombre y las manzanas. La app
# acerca más sin pedir otro archivo (agranda el 15). El 14 pesa una cuarta parte
# y todavía trae todas las calles; se puede pedir con `--zoom 14`.
ZOOM_MAXIMO = 15

# La mancha urbana de Mazatlán, de El Habal y Cerritos a la isla de la Piedra y
# el aeropuerto. Oeste, sur, este, norte.
MAZATLAN = (-106.50, 23.13, -106.28, 23.33)

# Unos 2 km alrededor de la última tienda.
MARGEN_GRADOS = 0.02

# Más de esto ya no es «Mazatlán y alrededores» sino un error en las
# coordenadas, y el archivo saldría de cientos de MB.
LADO_MAXIMO_GRADOS = 1.2

# Dos tramos se juntan en una sola petición si entre ellos hay menos de esto:
# bajar unos KB de más cuesta menos que otra ida y vuelta.
HUECO_MAXIMO = 512 * 1024
TRAMO_MAXIMO = 32 * 1024 * 1024

LeerBytes = Callable[[int, int], bytes]

Zona = tuple[float, float, float, float]


class MapaInvalido(Exception):
    """La fuente no es un PMTiles que se pueda usar, o la zona no tiene sentido."""


def directorio_de_mapas() -> Path:
    return Path(obtener_config().mapas_dir)


# ---------------------------------------------------------------------------
# La zona
# ---------------------------------------------------------------------------


def zona_de_las_tiendas(
    percentiles: Zona | None, *, margen: float = MARGEN_GRADOS
) -> Zona:
    """La zona del mapa: la de las tiendas con su margen, y al menos Mazatlán.

    `percentiles` son (oeste, sur, este, norte) de las tiendas con ubicación,
    con los percentiles 1 y 99; `None` si ninguna tiene.
    """
    oeste, sur, este, norte = MAZATLAN
    if percentiles is not None:
        o, s, e, n = percentiles
        oeste, sur = min(oeste, o - margen), min(sur, s - margen)
        este, norte = max(este, e + margen), max(norte, n + margen)
    zona = (round(oeste, 4), round(sur, 4), round(este, 4), round(norte, 4))
    validar_zona(zona)
    return zona


def validar_zona(zona: Zona) -> None:
    oeste, sur, este, norte = zona
    if not (-180 <= oeste < este <= 180 and -85 <= sur < norte <= 85):
        raise MapaInvalido(f"la zona {zona} no es oeste,sur,este,norte")
    if este - oeste > LADO_MAXIMO_GRADOS or norte - sur > LADO_MAXIMO_GRADOS:
        raise MapaInvalido(
            f"la zona mide {este - oeste:.2f}° × {norte - sur:.2f}°: es demasiado "
            "grande para un mapa de calles. Revisa las coordenadas de las tiendas o "
            "da la zona con --zona oeste,sur,este,norte"
        )


def _x(lon: float, z: int) -> int:
    n = 2**z
    return min(n - 1, max(0, int((lon + 180.0) / 360.0 * n)))


def _y(lat: float, z: int) -> int:
    n = 2**z
    rad = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(rad)) / math.pi) / 2.0 * n
    return min(n - 1, max(0, int(y)))


def teselas_de_la_zona(zona: Zona, zoom_minimo: int, zoom_maximo: int) -> list[int]:
    """Los identificadores PMTiles de las teselas que cubren la zona, en orden."""
    oeste, sur, este, norte = zona
    ids = []
    for z in range(zoom_minimo, zoom_maximo + 1):
        for x in range(_x(oeste, z), _x(este, z) + 1):
            for y in range(_y(norte, z), _y(sur, z) + 1):
                ids.append(zxy_to_tileid(z, x, y))
    return sorted(ids)


# ---------------------------------------------------------------------------
# La extracción
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Extraccion:
    teselas: int
    bytes: int
    peticiones: int
    zona: Zona
    zoom_maximo: int


def extraer(leer: LeerBytes, zona: Zona, destino: Path, *,
            zoom_maximo: int = ZOOM_MAXIMO, fuente: str = "") -> Extraccion:
    """Copia a `destino` las teselas de `zona` del PMTiles que `leer` sabe leer.

    `leer(desde, cuantos)` devuelve esos bytes de la fuente: un archivo local en
    las pruebas, peticiones de rango en el servidor.
    """
    validar_zona(zona)
    peticiones = 0

    def pedir(desde: int, cuantos: int, *, exacto: bool = True) -> bytes:
        nonlocal peticiones
        peticiones += 1
        datos = leer(desde, cuantos)
        if exacto and len(datos) != cuantos:
            raise MapaInvalido(
                f"la fuente devolvió {len(datos)} bytes de {cuantos} pedidos"
            )
        return datos

    # La cabecera y, casi siempre, la raíz entera vienen en los primeros 16 KB
    # (un archivo más chico que eso llega completo).
    inicio = pedir(0, 16384, exacto=False)
    if len(inicio) < 127:
        raise MapaInvalido("la fuente no es un PMTiles v3 (está vacía o cortada)")
    try:
        cabecera = deserialize_header(inicio[:127])
    except Exception as e:  # noqa: BLE001 — cualquier cosa aquí es «no es un PMTiles»
        raise MapaInvalido(f"la fuente no es un PMTiles v3 ({e.__class__.__name__})") from e
    if cabecera["internal_compression"] != Compression.GZIP:
        raise MapaInvalido("solo se leen PMTiles con directorios en gzip")

    def bloque(desde: int, cuantos: int) -> bytes:
        if desde + cuantos <= len(inicio):
            return inicio[desde:desde + cuantos]
        return pedir(desde, cuantos)

    raiz = deserialize_directory(bloque(cabecera["root_offset"], cabecera["root_length"]))
    hojas: dict[tuple[int, int], list] = {}

    zoom_maximo = min(zoom_maximo, cabecera["max_zoom"])
    zoom_minimo = cabecera["min_zoom"]
    # Dónde está cada tesela que se necesita: (id, desde, cuantos).
    ubicadas: list[tuple[int, int, int]] = []
    for tid in teselas_de_la_zona(zona, zoom_minimo, zoom_maximo):
        entradas = raiz
        for _ in range(4):  # la profundidad máxima del formato
            entrada = find_tile(entradas, tid)
            if entrada is None:
                break   # el mar sin nada, o fuera de lo que trae la fuente
            if entrada.run_length == 0:
                clave = (cabecera["leaf_directory_offset"] + entrada.offset, entrada.length)
                if clave not in hojas:
                    hojas[clave] = deserialize_directory(pedir(*clave))
                entradas = hojas[clave]
                continue
            ubicadas.append(
                (tid, cabecera["tile_data_offset"] + entrada.offset, entrada.length)
            )
            break
    if not ubicadas:
        raise MapaInvalido(f"la fuente no tiene nada en la zona {zona}")

    # Los tramos: las teselas distintas, en el orden del archivo, juntando las
    # que están cerca.
    distintas = sorted({(desde, cuantos) for _, desde, cuantos in ubicadas})
    tramos: list[list[tuple[int, int]]] = []
    for desde, cuantos in distintas:
        if tramos:
            ultimo = tramos[-1]
            ini = ultimo[0][0]
            fin = ultimo[-1][0] + ultimo[-1][1]
            if desde - fin <= HUECO_MAXIMO and desde + cuantos - ini <= TRAMO_MAXIMO:
                ultimo.append((desde, cuantos))
                continue
        tramos.append([(desde, cuantos)])
    contenido: dict[int, bytes] = {}
    for tramo in tramos:
        ini = tramo[0][0]
        fin = max(d + c for d, c in tramo)
        datos = pedir(ini, fin - ini)
        for desde, cuantos in tramo:
            contenido[desde] = datos[desde - ini:desde - ini + cuantos]

    metadatos = _metadatos(cabecera, bloque)
    metadatos["attribution"] = metadatos.get("attribution") or ATRIBUCION
    metadatos["dsd"] = {
        "zona": list(zona),
        "zoom_maximo": zoom_maximo,
        "fuente": fuente,
    }

    oeste, sur, este, norte = zona
    salida = dict(cabecera)
    salida.update(
        min_lon_e7=round(oeste * 1e7), min_lat_e7=round(sur * 1e7),
        max_lon_e7=round(este * 1e7), max_lat_e7=round(norte * 1e7),
        center_lon_e7=round((oeste + este) / 2 * 1e7),
        center_lat_e7=round((sur + norte) / 2 * 1e7),
        center_zoom=min(13, zoom_maximo),
    )
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_suffix(destino.suffix + ".parcial")
    with temporal.open("wb") as f:
        escritor = Writer(f)
        for tid, desde, _ in ubicadas:   # ya van en orden de id: queda agrupado
            escritor.write_tile(tid, contenido[desde])
        escritor.finalize(salida, metadatos)
    # Se cambia de un golpe: un teléfono que lo pida a la mitad recibe el viejo
    # entero, nunca uno a medias.
    os.replace(temporal, destino)
    return Extraccion(
        teselas=len(ubicadas),
        bytes=destino.stat().st_size,
        peticiones=peticiones,
        zona=zona,
        zoom_maximo=zoom_maximo,
    )


def _metadatos(cabecera: dict, bloque: Callable[[int, int], bytes]) -> dict:
    if not cabecera["metadata_length"]:
        return {}
    crudo = bloque(cabecera["metadata_offset"], cabecera["metadata_length"])
    try:
        return json.loads(gzip.decompress(crudo))
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# La fuente por internet
# ---------------------------------------------------------------------------


def lector_http(url: str, *, intentos: int = 4) -> LeerBytes:
    """Lee la fuente por rangos (`Range: bytes=a-b`), reintentando lo que se corte."""

    def leer(desde: int, cuantos: int) -> bytes:
        peticion = urllib.request.Request(
            url,
            headers={
                "Range": f"bytes={desde}-{desde + cuantos - 1}",
                "User-Agent": "dsd-server (mapa de rutas)",
            },
        )
        for intento in range(intentos):
            try:
                with urllib.request.urlopen(peticion, timeout=120) as r:
                    if r.status != 206:
                        raise MapaInvalido(
                            f"{url} no aceptó leer por pedazos (respondió {r.status})"
                        )
                    return r.read()
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if intento == intentos - 1:
                    raise
                time.sleep(2 ** (intento + 1))
        raise AssertionError("inalcanzable")

    return leer


def existe(url: str) -> bool:
    try:
        lector_http(url, intentos=1)(0, 127)
        return True
    except (MapaInvalido, urllib.error.URLError, TimeoutError, ConnectionError):
        return False


def fuente_reciente(hoy: date, *, existe: Callable[[str], bool] = existe,
                    dias: int = 10) -> str:
    """El corte diario más reciente que haya: Protomaps borra los viejos."""
    for atras in range(dias):
        url = FUENTE_DIARIA.format(fecha=(hoy - timedelta(days=atras)).strftime("%Y%m%d"))
        if existe(url):
            return url
    raise MapaInvalido(
        f"no hay un corte de Protomaps de los últimos {dias} días; da la fuente con "
        "--fuente https://…/archivo.pmtiles"
    )


# ---------------------------------------------------------------------------
# Lo que el servidor tiene, para la app y el panel
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FichaDelMapa:
    version: str
    bytes: int
    sha256: str
    zona: Zona
    zoom_maximo: int
    teselas: int
    atribucion: str
    tiendas_fuera: int = 0


def guardar_ficha(directorio: Path, extraccion: Extraccion, *, version: str,
                  tiendas_fuera: int = 0) -> FichaDelMapa:
    archivo = directorio / ARCHIVO
    suma = hashlib.sha256()
    with archivo.open("rb") as f:
        for pedazo in iter(lambda: f.read(1024 * 1024), b""):
            suma.update(pedazo)
    ficha = FichaDelMapa(
        version=version,
        bytes=archivo.stat().st_size,
        sha256=suma.hexdigest(),
        zona=extraccion.zona,
        zoom_maximo=extraccion.zoom_maximo,
        teselas=extraccion.teselas,
        atribucion=ATRIBUCION,
        tiendas_fuera=tiendas_fuera,
    )
    temporal = directorio / (FICHA + ".parcial")
    temporal.write_text(json.dumps(ficha.__dict__, ensure_ascii=False), encoding="utf-8")
    os.replace(temporal, directorio / FICHA)
    return ficha


def leer_ficha(directorio: Path | None = None) -> FichaDelMapa | None:
    """La ficha del mapa que hay, o `None` si todavía no se ha bajado."""
    directorio = directorio or directorio_de_mapas()
    try:
        datos = json.loads((directorio / FICHA).read_text(encoding="utf-8"))
        if not (directorio / ARCHIVO).is_file():
            return None
        datos["zona"] = tuple(datos["zona"])
        return FichaDelMapa(**datos)
    except (OSError, ValueError, TypeError, KeyError):
        return None


# ---------------------------------------------------------------------------
# Las tiendas
# ---------------------------------------------------------------------------


async def percentiles_de_las_tiendas(sesion) -> tuple[Zona | None, int]:
    """(oeste, sur, este, norte) de las tiendas con ubicación, con los
    percentiles 1 y 99, y cuántas tienen ubicación."""
    from sqlalchemy import text

    fila = (await sesion.execute(text("""
        SELECT count(*) AS n,
               percentile_cont(0.01) WITHIN GROUP (ORDER BY lng) AS o,
               percentile_cont(0.01) WITHIN GROUP (ORDER BY lat) AS s,
               percentile_cont(0.99) WITHIN GROUP (ORDER BY lng) AS e,
               percentile_cont(0.99) WITHIN GROUP (ORDER BY lat) AS n2
          FROM clientes
         WHERE lat IS NOT NULL AND lng IS NOT NULL
    """))).one()
    if not fila.n:
        return None, 0
    return (float(fila.o), float(fila.s), float(fila.e), float(fila.n2)), fila.n


async def tiendas_fuera(sesion, zona: Zona) -> int:
    """Cuántas tiendas con ubicación quedan fuera de la zona del mapa."""
    from sqlalchemy import text

    oeste, sur, este, norte = zona
    return (await sesion.execute(text("""
        SELECT count(*) FROM clientes
         WHERE lat IS NOT NULL AND lng IS NOT NULL
           AND NOT (lng BETWEEN :o AND :e AND lat BETWEEN :s AND :n)
    """), {"o": oeste, "s": sur, "e": este, "n": norte})).scalar_one()
