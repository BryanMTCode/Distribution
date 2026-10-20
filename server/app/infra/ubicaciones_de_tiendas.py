"""Las coordenadas de las tiendas de las rutas 1101–1107 (ADR 0002 §148).

────────────────────────────────────────────────────────────────────────────
DE DÓNDE SALE
────────────────────────────────────────────────────────────────────────────
Pedido de la dirección (octubre 2026), con el Excel LOCALES_MAZATLAN_2: «vienen
dos hojas con la ruta 1101 y las demás rutas, con las columnas establecimiento,
domicilio y latitud y longitud con las coordenadas de cada tienda; quiero que
uses este archivo para agregar estos datos a las tiendas de mi base de datos».
Las tiendas ya estaban (§120), con su calle, número y colonia, pero ninguna
tenía coordenadas.

El Excel ya pasado a limpio está en
`db/semillas/ubicaciones_tiendas_2026-10-20.csv`, un renglón por tienda. La
limpieza:

· **Solo de lunes a sábado.** Las tres del día 7 se quedan fuera.
· **La hoja «Ruta 1101»** trae en su columna de ruta 1101 de lunes a miércoles y
  1108 de jueves a sábado: «toma lunes, martes y miércoles de las que dicen 1101,
  y jueves, viernes y sábado de las que dicen 1108». Todas son de la 1101.
· **Dos longitudes sin el signo menos** (106.40… en vez de −106.40…): se les
  pone. En México la longitud es negativa; sin el signo la tienda queda en China.
· **Una latitud de 26.27** (ABARROTES NAI, ruta 1107): está a 340 km de Mazatlán.
  Se queda sin coordenadas en vez de adivinar el dígito; la captura su vendedor.
· Espacios de más fuera. Lo demás va como venía, y la columna `correccion` dice
  qué se tocó.

────────────────────────────────────────────────────────────────────────────
CÓMO ENCUENTRA SU TIENDA
────────────────────────────────────────────────────────────────────────────
En la base, no en el archivo de §120: un vendedor pudo haber dado de alta o
cambiado algo desde entonces. Dentro de la ruta del renglón, en este orden, y
solo si queda una sola tienda que cuadre (cada tienda se usa una vez):

1. el mismo nombre y el mismo domicilio (calle, número y colonia);
2. el mismo nombre y el mismo día de visita;
3. el mismo nombre, si en la ruta solo hay una así;
4. el mismo domicilio y el mismo día;
5. y por último el mismo nombre y domicilio **en otra ruta**: se ubica, sin
   cambiarla de ruta, y se avisa.

La ruta de cada tienda se encuentra como en la carga de §120: por su número o
por su vendedor. La del VEND1102 puede llamarse «CENTRO» y sus tiendas siguen
siendo de la 1102.

Los nombres y domicilios se comparan sin acentos, mayúsculas ni signos.
ABARROTES SIXTO viene dos veces en la base (908 y 909, §120) y una en el Excel:
las dos reciben la ubicación, porque son la misma tienda.

────────────────────────────────────────────────────────────────────────────
QUÉ HACE
────────────────────────────────────────────────────────────────────────────
Todo en UNA transacción:

· A cada tienda encontrada que **no tenga ubicación** le pone la del Excel, con
  origen «manual» —escrita, no tomada con el GPS ahí parado—, por el mismo
  camino que el panel (`fijar_ubicacion`): queda la anterior en la auditoría y
  le llega al teléfono de su ruta en su siguiente sincronización.
· La que **ya tenga ubicación no se toca**: la capturó alguien parado en la
  tienda, y eso vale más que una lista.
· **Los números que Excel volvió fecha.** En los Excel de §120, «1-3» llegó como
  46025 y «4404-4» como 914660: Excel los leyó como fechas. Si el número de la
  tienda es puro dígito de cinco o más y el domicilio de este Excel trae, entre la
  calle y la colonia, uno con guion, se corrige.
· Las tiendas del Excel que **no están en la base** no se dan de alta: se dicen,
  para decidir qué hacer con ellas.

Correrlo dos veces no cambia nada la segunda: ya tienen ubicación.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from app.infra.tiendas_de_rutas import RUTAS, numero_de_ruta, rutas_del_sistema
from app.infra.ubicacion_cliente import UbicacionInvalida, fijar_ubicacion, leer_coordenadas

UBICACIONES = (
    Path(__file__).resolve().parents[2] / "db" / "semillas"
    / "ubicaciones_tiendas_2026-10-20.csv"
)

PALABRA = "UBICAR"
DIAS = {1: "lun", 2: "mar", 3: "mié", 4: "jue", 5: "vie", 6: "sáb"}


class HojaInvalida(Exception):
    """No se cambió nada, y el mensaje dice por qué."""


@dataclass(frozen=True)
class Renglon:
    linea: int  # en el CSV, para los avisos
    ruta: str
    dia: int
    nombre: str
    domicilio: str
    lat: Decimal | None
    lng: Decimal | None
    correccion: str

    def __str__(self) -> str:
        return f"{self.nombre} ({self.domicilio}), ruta {self.ruta} {DIAS[self.dia]}"


def leer_renglones(archivo: Path = UBICACIONES) -> list[Renglon]:
    """Lee el CSV y lo revisa. No toca la base."""
    renglones: list[Renglon] = []
    errores: list[str] = []
    with archivo.open(encoding="utf-8", newline="") as f:
        for linea, r in enumerate(csv.DictReader(f), start=2):
            ruta, nombre = r["ruta"].strip(), " ".join(r["nombre"].split())
            try:
                dia = int(r["dia"])
            except ValueError:
                dia = 0
            if ruta not in RUTAS or dia not in DIAS:
                errores.append(f"el renglón {linea} trae ruta «{ruta}» y día «{r['dia']}»")
                continue
            if not nombre:
                errores.append(f"el renglón {linea} no trae nombre")
                continue
            lat = lng = None
            if r["latitud"].strip() or r["longitud"].strip():
                try:
                    lat, lng = leer_coordenadas(r["latitud"], r["longitud"])
                except UbicacionInvalida as e:
                    errores.append(f"el renglón {linea} ({nombre}): {e}")
                    continue
            renglones.append(Renglon(
                linea=linea, ruta=ruta, dia=dia, nombre=nombre,
                domicilio=" ".join(r["domicilio"].split()), lat=lat, lng=lng,
                correccion=r["correccion"].strip(),
            ))
    if errores:
        raise HojaInvalida("; ".join(errores[:5]))
    return renglones


def llave(texto: str | None) -> str:
    """Sin acentos, en mayúsculas y sin signos: «Joaquín Capilla #715» y
    «JOAQUIN CAPILLA 715» son lo mismo."""
    plano = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", plano.upper()).split())


# ---------------------------------------------------------------------------
# Lo que va a pasar
# ---------------------------------------------------------------------------


@dataclass
class Tienda:
    id: uuid.UUID
    codigo: str | None
    nombre: str
    calle: str
    numero: str
    colonia: str
    ruta: str
    dias: set[int]
    lat: Decimal | None
    lng: Decimal | None
    # Las llaves para comparar, calculadas una vez.
    llave_nombre: str = ""
    llave_domicilio: str = ""

    def __post_init__(self) -> None:
        self.llave_nombre = llave(self.nombre)
        self.llave_domicilio = llave(self.domicilio)

    @property
    def domicilio(self) -> str:
        return " ".join(p for p in (self.calle, self.numero, self.colonia) if p)

    def __str__(self) -> str:
        return f"{self.codigo or 'sin código'} {self.nombre}"


@dataclass
class Plan:
    # Renglón → sus tiendas (casi siempre una; dos si la base la tiene repetida).
    parejas: list[tuple[Renglon, list[Tienda]]] = field(default_factory=list)
    sin_tienda: list[Renglon] = field(default_factory=list)
    # Las que se van a ubicar.
    por_ubicar: list[tuple[Renglon, Tienda]] = field(default_factory=list)
    ya_estaban: int = 0
    # Ya tenían otra ubicación: no se tocan.
    con_otra_ubicacion: list[str] = field(default_factory=list)
    sin_coordenadas: list[Renglon] = field(default_factory=list)
    # (tienda, número nuevo)
    numeros: list[tuple[Tienda, str]] = field(default_factory=list)
    repetidas_en_la_base: list[str] = field(default_factory=list)
    en_otra_ruta: list[str] = field(default_factory=list)
    en_otro_dia: list[str] = field(default_factory=list)
    # Tiendas de estas rutas que no vienen en el Excel.
    sin_renglon: list[Tienda] = field(default_factory=list)

    def por_ruta(self) -> dict[str, Counter]:
        """Por ruta: los renglones con tienda, los que no, y las que se ubican."""
        cuenta: dict[str, Counter] = {r: Counter() for r in RUTAS}
        for renglon, _ in self.parejas:
            cuenta[renglon.ruta]["con_tienda"] += 1
        for renglon in self.sin_tienda:
            cuenta[renglon.ruta]["sin_tienda"] += 1
        for renglon, _ in self.por_ubicar:
            cuenta[renglon.ruta]["ubicar"] += 1
        return cuenta


async def _tiendas(sesion) -> list[Tienda]:
    # La ruta de cada tienda, por su número: la del VEND1102 puede llamarse
    # «CENTRO» (§120), y sus tiendas siguen siendo de la 1102.
    numero = {ruta_id: n for n, ruta_id in (await rutas_del_sistema(sesion)).items()}
    filas = (
        await sesion.execute(
            text(
                """
                SELECT c.id, c.codigo, c.nombre_comercial, c.calle, c.numero, c.colonia,
                       c.lat, c.lng, c.ruta_id, r.codigo AS ruta,
                       COALESCE(array_agg(f.dia_semana) FILTER (
                                  WHERE f.dia_semana IS NOT NULL), '{}') AS dias
                  FROM clientes c
                  LEFT JOIN rutas r ON r.id = c.ruta_id
                  LEFT JOIN clientes_frecuencia f ON f.cliente_id = c.id
                 WHERE c.estatus <> 'baja'
                 GROUP BY c.id, r.codigo
                 ORDER BY c.codigo NULLS LAST, c.nombre_comercial
                """
            )
        )
    ).mappings().all()
    return [
        Tienda(
            id=f["id"], codigo=f["codigo"], nombre=f["nombre_comercial"] or "",
            calle=f["calle"] or "", numero=f["numero"] or "", colonia=f["colonia"] or "",
            ruta=numero.get(f["ruta_id"]) or numero_de_ruta(f["ruta"] or ""),
            dias=set(f["dias"]),
            lat=f["lat"], lng=f["lng"],
        )
        for f in filas
    ]


def _numero_que_era_fecha(tienda: Tienda, renglon: Renglon) -> str | None:
    """«46025» que en el Excel nuevo es «1-3»: lo que hay entre la calle y la
    colonia del domicilio, si trae guion y la tienda tiene puros dígitos."""
    if not (tienda.numero.isdigit() and len(tienda.numero) >= 5):
        return None
    palabras = renglon.domicilio.split()
    calle, colonia = tienda.calle.split(), tienda.colonia.split()
    if len(palabras) <= len(calle) + len(colonia):
        return None
    if llave(" ".join(palabras[: len(calle)])) != llave(tienda.calle):
        return None
    if colonia and llave(" ".join(palabras[-len(colonia):])) != llave(tienda.colonia):
        return None
    medio = " ".join(palabras[len(calle): len(palabras) - len(colonia)])
    return medio if "-" in medio and medio != tienda.numero else None


async def planear(sesion, renglones: list[Renglon]) -> Plan:
    plan = Plan()
    tiendas = await _tiendas(sesion)
    de_la_ruta: dict[str, list[Tienda]] = defaultdict(list)
    for t in tiendas:
        de_la_ruta[t.ruta].append(t)
    usadas: set[uuid.UUID] = set()
    parejas: dict[int, tuple[list[Tienda], str]] = {}
    llaves = {r.linea: (llave(r.nombre), llave(r.domicilio)) for r in renglones}

    def mismo_nombre(r: Renglon, t: Tienda) -> bool:
        return t.llave_nombre == llaves[r.linea][0]

    def mismo_domicilio(r: Renglon, t: Tienda) -> bool:
        return t.llave_domicilio == llaves[r.linea][1]

    pasos = (
        ("ruta", lambda r, t: mismo_nombre(r, t) and mismo_domicilio(r, t)),
        ("ruta", lambda r, t: mismo_nombre(r, t) and r.dia in t.dias),
        ("ruta", mismo_nombre),
        ("ruta", lambda r, t: mismo_domicilio(r, t) and r.dia in t.dias),
        ("otra", lambda r, t: mismo_nombre(r, t) and mismo_domicilio(r, t)),
    )
    for indice, (donde, cuadra) in enumerate(pasos):
        for r in renglones:
            if r.linea in parejas:
                continue
            universo = de_la_ruta[r.ruta] if donde == "ruta" else [
                t for t in tiendas if t.ruta != r.ruta
            ]
            candidatas = [t for t in universo if t.id not in usadas and cuadra(r, t)]
            if not candidatas:
                continue
            if len(candidatas) > 1:
                # Solo en el primer paso, y solo si son la misma tienda repetida.
                iguales = {(t.llave_nombre, t.llave_domicilio) for t in candidatas}
                if indice != 0 or len(iguales) != 1:
                    continue
                plan.repetidas_en_la_base.append(
                    " y ".join(t.codigo or "sin código" for t in candidatas)
                    + f" {candidatas[0].nombre} ({candidatas[0].domicilio}): las "
                    f"{len(candidatas)} reciben la ubicación"
                )
            parejas[r.linea] = (candidatas, donde)
            usadas.update(t.id for t in candidatas)

    for r in renglones:
        if r.linea not in parejas:
            plan.sin_tienda.append(r)
            continue
        suyas, donde = parejas[r.linea]
        plan.parejas.append((r, suyas))
        for t in suyas:
            if donde == "otra":
                plan.en_otra_ruta.append(
                    f"{r.nombre} viene en la {r.ruta} y en la base es {t.codigo or 'sin código'}"
                    f" de la {t.ruta or 'sin ruta'}: se ubica sin cambiarla de ruta"
                )
            elif t.dias and r.dia not in t.dias:
                plan.en_otro_dia.append(
                    f"{t} viene el {DIAS[r.dia]} y en la base es el "
                    + ", ".join(DIAS.get(d, "dom") for d in sorted(t.dias))
                )
            nuevo = _numero_que_era_fecha(t, r)
            if nuevo:
                plan.numeros.append((t, nuevo))
            if r.lat is None:
                plan.sin_coordenadas.append(r)
            elif t.lat is None or t.lng is None:
                plan.por_ubicar.append((r, t))
            elif (Decimal(t.lat), Decimal(t.lng)) == (r.lat, r.lng):
                plan.ya_estaban += 1
            else:
                plan.con_otra_ubicacion.append(
                    f"{t} ya está en {t.lat}, {t.lng}; el Excel dice {r.lat}, {r.lng}"
                )
    plan.sin_renglon = [
        t for ruta in RUTAS for t in de_la_ruta[ruta] if t.id not in usadas
    ]
    return plan


# ---------------------------------------------------------------------------
# Cargar
# ---------------------------------------------------------------------------


@dataclass
class Resultado:
    ubicadas: int = 0
    numeros: int = 0


async def aplicar(sesion, renglones: list[Renglon], *, quien: uuid.UUID | None) -> Resultado:
    """Pone las ubicaciones y corrige los números, y hace commit."""
    plan = await planear(sesion, renglones)
    resultado = Resultado()
    for renglon, tienda in plan.por_ubicar:
        await fijar_ubicacion(
            sesion, tienda.id, lat=renglon.lat, lng=renglon.lng, origen="manual", quien=quien
        )
        resultado.ubicadas += 1
    for tienda, numero in plan.numeros:
        await sesion.execute(
            text("UPDATE clientes SET numero = :n, actualizado_en = now() WHERE id = :c"),
            {"n": numero[:40], "c": tienda.id},
        )
        resultado.numeros += 1
    await sesion.execute(
        text("INSERT INTO auditoria (entidad, entidad_id, accion, usuario_id, datos_despues, "
             "                       motivo) "
             "VALUES ('clientes', :id, 'ubicar_tiendas', :quien, CAST(:datos AS jsonb), "
             "        :motivo)"),
        {
            "id": uuid.uuid4(),
            "quien": quien,
            "datos": json.dumps(
                {
                    "ubicadas": resultado.ubicadas,
                    "numeros": [
                        {"codigo": t.codigo, "antes": t.numero, "despues": n}
                        for t, n in plan.numeros
                    ],
                    "sin_tienda": len(plan.sin_tienda),
                },
                ensure_ascii=False,
            ),
            "motivo": "Coordenadas de las tiendas, del Excel LOCALES_MAZATLAN_2.",
        },
    )
    await sesion.commit()
    return resultado
