"""Las tiendas de las rutas 1101–1107, con su día de visita (ADR 0002 §120).

────────────────────────────────────────────────────────────────────────────
DE DÓNDE SALE
────────────────────────────────────────────────────────────────────────────
Pedido de la dirección (octubre 2026), con un Excel por ruta y una hoja por día,
de lunes a sábado: tienda, calle, número, colonia y localidad. «Los Ids los vas
generando en orden: para la ruta 1101 empiezan por 100 y van aumentando en uno,
de la primera del lunes hasta la última del sábado; luego las del lunes de la
1102 hasta las del sábado, y así hasta la 1107.» Con el número de tiendas que
debe tener cada día, para verificar que concuerden. Sin coordenadas: «luego
agregaremos los otros datos también mediante los vendedores».

Los siete Excel ya pasados a limpio están en
`db/semillas/tiendas_rutas_1101_1107.csv`, un renglón por tienda en ese orden,
con su código (100 a 1933), su ruta, su día y su lugar en la hoja. La limpieza:
espacios de más fuera, «701.0» → «701», el número vacío o 0 → «S/N», y las diez
formas en que la hoja escribe Mazatlán («MAZATLAN», «NAZATLAN», «MAZTLAN»…) →
«MAZATLÁN». Lo demás va como venía.

────────────────────────────────────────────────────────────────────────────
QUÉ HACE
────────────────────────────────────────────────────────────────────────────
Todo en UNA transacción —o queda todo, o nada—:

1. Revisa el archivo: los códigos seguidos desde 100, las rutas y los días en
   orden, y cuántas tiendas trae cada día contra lo que dijo la dirección. Si
   algo no cuadra, no toca nada.
2. Busca cada ruta: la que tenga ese número en su código («1101», «R1101»,
   «RUTA 1101») o, si no hay, la única del vendedor VEND1101. Si no existe, la
   crea como R1101 «Ruta 1101» con VEND1101 de titular (si existe), igual que el
   alta del panel: titular y alcance, o su teléfono no recibe las tiendas.
3. Da de alta cada tienda con su código, activa, con la lista de precios
   general, en su ruta, con su lugar en la hoja como orden de visita, y su día en
   el plan de visita (desde hoy: lo anterior no se le reclama a nadie).

Correrlo dos veces no duplica nada: la tienda cuyo código ya existe con el mismo
nombre se deja como está. Si el código ya lo tiene OTRA tienda, no se toca nada
y se dice cuál.

────────────────────────────────────────────────────────────────────────────
LO QUE VE EL TELÉFONO
────────────────────────────────────────────────────────────────────────────
Cada tienda viaja al teléfono del vendedor de su ruta en su siguiente
sincronización (el delta de `cliente` de siempre, con su plan de visita), y
«Hoy» le muestra las que le tocan. Las coordenadas las captura el vendedor en la
tienda, como cualquier cliente sin ubicación.
"""

from __future__ import annotations

import csv
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import text

from app.api.admin.equipo import _sucursal_por_omision

TIENDAS = (
    Path(__file__).resolve().parents[2] / "db" / "semillas" / "tiendas_rutas_1101_1107.csv"
)

PALABRA = "TIENDAS"
PRIMER_CODIGO = 100

# Lunes = 1 … sábado = 6, como `extract(dow)` y `clientes_frecuencia`.
DIAS: dict[str, int] = {
    "lunes": 1, "martes": 2, "miercoles": 3, "jueves": 4, "viernes": 5, "sabado": 6,
}
CORTO = {"lunes": "lun", "martes": "mar", "miercoles": "mié", "jueves": "jue",
         "viernes": "vie", "sabado": "sáb"}

# Las tiendas de cada día, de lunes a sábado, como las dio la dirección.
CONTEOS: dict[str, tuple[int, ...]] = {
    "1101": (45, 44, 47, 46, 37, 41),
    "1102": (45, 44, 38, 41, 46, 42),
    "1103": (50, 47, 32, 38, 42, 31),
    "1104": (48, 52, 47, 50, 36, 40),
    "1105": (58, 47, 37, 41, 42, 45),
    "1106": (50, 44, 48, 47, 41, 38),
    "1107": (45, 46, 57, 42, 49, 28),
}
RUTAS = tuple(CONTEOS)


class HojaInvalida(Exception):
    """No se cargó nada, y el mensaje dice por qué."""


@dataclass(frozen=True)
class Tienda:
    codigo: str
    ruta: str
    dia: str
    orden: int
    nombre: str
    calle: str
    numero: str
    colonia: str
    localidad: str


def leer_tiendas(archivo: Path = TIENDAS) -> list[Tienda]:
    """Lee el CSV y revisa que sea lo que la dirección dijo. No toca la base."""
    with archivo.open(encoding="utf-8", newline="") as f:
        tiendas = [
            Tienda(
                codigo=r["codigo"].strip(),
                ruta=r["ruta"].strip(),
                dia=r["dia"].strip(),
                orden=int(r["orden"]),
                nombre=" ".join(r["nombre"].split()),
                calle=" ".join(r["calle"].split()),
                numero=" ".join(r["numero"].split()),
                colonia=" ".join(r["colonia"].split()),
                localidad=" ".join(r["localidad"].split()),
            )
            for r in csv.DictReader(f)
        ]

    errores: list[str] = []
    for i, t in enumerate(tiendas):
        if t.codigo != str(PRIMER_CODIGO + i):
            errores.append(f"el renglón {i + 2} trae el código {t.codigo} y le toca "
                           f"{PRIMER_CODIGO + i}")
            break
        if t.ruta not in CONTEOS or t.dia not in DIAS:
            errores.append(f"el código {t.codigo} trae ruta «{t.ruta}» y día «{t.dia}»")
        if not t.nombre:
            errores.append(f"el código {t.codigo} no trae nombre")
    # El orden: ruta por ruta, y en cada una de lunes a sábado.
    secuencia = [(RUTAS.index(t.ruta), DIAS[t.dia]) for t in tiendas
                 if t.ruta in CONTEOS and t.dia in DIAS]
    if secuencia != sorted(secuencia):
        errores.append("las tiendas no vienen ruta por ruta y de lunes a sábado")
    cuenta = Counter((t.ruta, t.dia) for t in tiendas)
    for ruta, por_dia in CONTEOS.items():
        for dia, esperadas in zip(DIAS, por_dia, strict=True):
            if cuenta[(ruta, dia)] != esperadas:
                errores.append(f"la ruta {ruta} trae {cuenta[(ruta, dia)]} tiendas el "
                               f"{dia} y deben ser {esperadas}")
    if errores:
        raise HojaInvalida("; ".join(errores[:5]))
    return tiendas


def por_ruta(tiendas: list[Tienda]) -> dict[str, list[Tienda]]:
    return {r: [t for t in tiendas if t.ruta == r] for r in RUTAS}


def resumen_de_dias(tiendas: list[Tienda]) -> str:
    """«lun 45 · mar 44 · …», para que la persona lo compare con su arreglo."""
    cuenta = Counter(t.dia for t in tiendas)
    return " · ".join(f"{CORTO[d]} {cuenta[d]}" for d in DIAS)


def numero_de_ruta(codigo: str) -> str:
    """«R1101», «RUTA 1101», «1101» → «1101»."""
    limpio = re.sub(r"[^0-9A-Z]", "", (codigo or "").upper())
    return re.sub(r"^(RUTA|R)", "", limpio)


def _llave(nombre: str) -> str:
    return " ".join((nombre or "").upper().split())


# ---------------------------------------------------------------------------
# Lo que va a pasar
# ---------------------------------------------------------------------------


@dataclass
class RutaDelExcel:
    numero: str
    id: uuid.UUID | None
    codigo: str
    nombre: str
    titular: str | None          # «VEND1101 · Juan Pérez»
    titular_id: uuid.UUID | None
    nueva: bool
    activa: bool = True


@dataclass
class Plan:
    rutas: dict[str, RutaDelExcel] = field(default_factory=dict)
    nuevas: list[Tienda] = field(default_factory=list)
    ya_estaban: int = 0
    # El código ya lo tiene otra tienda: no se toca nada.
    conflictos: list[str] = field(default_factory=list)
    # Una ruta que no se puede escoger sola (dos con el mismo número).
    rutas_dudosas: list[str] = field(default_factory=list)
    # Tiendas que ya existen con otro código y el mismo nombre y dirección.
    parecidas: list[str] = field(default_factory=list)
    repetidas_en_el_excel: list[str] = field(default_factory=list)
    hay_lista_de_precios: bool = True

    @property
    def se_puede(self) -> bool:
        return not self.conflictos and not self.rutas_dudosas


async def planear(sesion, tiendas: list[Tienda]) -> Plan:
    plan = Plan()
    await _buscar_rutas(sesion, plan)

    codigos = [t.codigo for t in tiendas]
    existentes = {
        f["codigo"]: f["nombre_comercial"]
        for f in (
            await sesion.execute(
                text("SELECT codigo, nombre_comercial FROM clientes WHERE codigo = ANY(:c)"),
                {"c": codigos},
            )
        ).mappings().all()
    }
    for t in tiendas:
        ya = existentes.get(t.codigo)
        if ya is None:
            plan.nuevas.append(t)
        elif _llave(ya) == _llave(t.nombre):
            plan.ya_estaban += 1
        else:
            plan.conflictos.append(f"el código {t.codigo} ya es «{ya}», no «{t.nombre}»")

    # Las que ya están con otro código (las dio de alta un vendedor en la calle,
    # por ejemplo): se avisan; la tienda del Excel se crea de todos modos.
    otras = (
        await sesion.execute(
            text(
                "SELECT codigo, nombre_comercial, calle, numero FROM clientes "
                " WHERE estatus <> 'baja' AND (codigo IS NULL OR NOT (codigo = ANY(:c)))"
            ),
            {"c": codigos},
        )
    ).mappings().all()
    conocidas = {
        (_llave(f["nombre_comercial"]), _llave(f["calle"]), _llave(f["numero"])):
            f["codigo"] or "sin código"
        for f in otras
    }
    for t in plan.nuevas:
        otra = conocidas.get((_llave(t.nombre), _llave(t.calle), _llave(t.numero)))
        if otra:
            plan.parecidas.append(f"{t.codigo} {t.nombre} ({t.calle} {t.numero}) ya existe "
                                  f"como {otra}")

    vistas: dict[tuple, str] = {}
    for t in tiendas:
        llave = (t.ruta, _llave(t.nombre), _llave(t.calle), _llave(t.numero),
                 _llave(t.colonia))
        if llave in vistas:
            plan.repetidas_en_el_excel.append(
                f"{vistas[llave]} y {t.codigo} {t.nombre} ({t.calle} {t.numero}, "
                f"{t.colonia}), ruta {t.ruta} {t.dia}"
            )
        else:
            vistas[llave] = t.codigo

    plan.hay_lista_de_precios = await _lista_general(sesion) is not None
    return plan


async def _buscar_rutas(sesion, plan: Plan) -> None:
    rutas = (
        await sesion.execute(
            text(
                "SELECT r.id, r.codigo, r.nombre, r.activo, r.vendedor_id, "
                "       u.codigo AS titular_codigo, u.nombre AS titular_nombre "
                "  FROM rutas r LEFT JOIN usuarios u ON u.id = r.vendedor_id "
                " ORDER BY r.activo DESC, r.codigo"
            )
        )
    ).mappings().all()
    vendedores = {
        f["codigo"]: f
        for f in (
            await sesion.execute(
                text(
                    "SELECT id, codigo, nombre FROM usuarios "
                    " WHERE codigo = ANY(:c) AND rol_codigo = 'vendedor' AND activo"
                ),
                {"c": [f"VEND{n}" for n in RUTAS]},
            )
        ).mappings().all()
    }
    for numero in RUTAS:
        candidatas = [r for r in rutas if numero_de_ruta(r["codigo"]) == numero]
        activas = [r for r in candidatas if r["activo"]]
        if len(activas) > 1:
            plan.rutas_dudosas.append(
                f"hay {len(activas)} rutas con el número {numero}: "
                + ", ".join(r["codigo"] for r in activas)
            )
            continue
        elegida = (activas or candidatas or [None])[0]
        if elegida is None:
            del_vendedor = [
                r for r in rutas
                if r["activo"] and r["titular_codigo"] == f"VEND{numero}"
            ]
            if len(del_vendedor) == 1:
                elegida = del_vendedor[0]
        if elegida is not None:
            plan.rutas[numero] = RutaDelExcel(
                numero=numero,
                id=elegida["id"],
                codigo=elegida["codigo"],
                nombre=elegida["nombre"],
                titular=(f"{elegida['titular_codigo']} · {elegida['titular_nombre']}"
                         if elegida["titular_codigo"] else None),
                titular_id=elegida["vendedor_id"],
                nueva=False,
                activa=elegida["activo"],
            )
            continue
        vendedor = vendedores.get(f"VEND{numero}")
        plan.rutas[numero] = RutaDelExcel(
            numero=numero,
            id=None,
            codigo=f"R{numero}",
            nombre=f"Ruta {numero}",
            titular=f"{vendedor['codigo']} · {vendedor['nombre']}" if vendedor else None,
            titular_id=vendedor["id"] if vendedor else None,
            nueva=True,
        )


async def _lista_general(sesion) -> uuid.UUID | None:
    return (
        await sesion.execute(
            text(
                "SELECT id FROM listas_precios WHERE activo "
                " ORDER BY es_default DESC, vigente_desde LIMIT 1"
            )
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Cargar
# ---------------------------------------------------------------------------


@dataclass
class Resultado:
    tiendas: int = 0
    ya_estaban: int = 0
    rutas_nuevas: list[str] = field(default_factory=list)


async def aplicar(sesion, tiendas: list[Tienda], *, quien: uuid.UUID | None) -> Resultado:
    """Da de alta las rutas que falten y las tiendas nuevas, y hace commit."""
    plan = await planear(sesion, tiendas)
    if plan.conflictos:
        raise HojaInvalida("; ".join(plan.conflictos[:5]))
    if plan.rutas_dudosas:
        raise HojaInvalida("; ".join(plan.rutas_dudosas))

    resultado = Resultado(ya_estaban=plan.ya_estaban)
    for ruta in plan.rutas.values():
        if not ruta.nueva:
            continue
        ruta.id = uuid.uuid4()
        await sesion.execute(
            text(
                "INSERT INTO rutas (id, codigo, nombre, sucursal_id, vendedor_id) "
                "VALUES (:id, :c, :n, :s, :v)"
            ),
            {"id": ruta.id, "c": ruta.codigo, "n": ruta.nombre,
             "s": await _sucursal_por_omision(sesion), "v": ruta.titular_id},
        )
        # Titular y alcance, como el alta del panel: sin `usuarios_rutas` el
        # teléfono del titular no recibe ni una tienda de la ruta.
        if ruta.titular_id is not None:
            await sesion.execute(
                text(
                    "INSERT INTO usuarios_rutas (usuario_id, ruta_id) VALUES (:u, :r) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"u": ruta.titular_id, "r": ruta.id},
            )
        resultado.rutas_nuevas.append(ruta.codigo)

    lista = await _lista_general(sesion)
    ids: list[uuid.UUID] = []
    dias: list[int] = []
    renglones = []
    for t in plan.nuevas:
        cliente = uuid.uuid4()
        ids.append(cliente)
        dias.append(DIAS[t.dia])
        renglones.append({
            "id": cliente,
            "codigo": t.codigo,
            "nombre": t.nombre[:200],
            "calle": t.calle[:200] or None,
            "numero": t.numero[:40] or None,
            "colonia": t.colonia[:120] or None,
            "municipio": t.localidad[:120] or None,
            "ruta": plan.rutas[t.ruta].id,
            "orden": t.orden,
            "lista": lista,
            "quien": quien,
        })
    if renglones:
        await sesion.execute(
            text(
                """
                INSERT INTO clientes (id, codigo, nombre_comercial, calle, numero, colonia,
                                      municipio, ruta_id, secuencia, estatus, origen_alta,
                                      lista_precios_id, creado_por, creado_en, actualizado_en)
                VALUES (:id, :codigo, :nombre, :calle, :numero, :colonia, :municipio, :ruta,
                        :orden, 'activo', 'oficina', :lista, :quien, now(), now())
                """
            ),
            renglones,
        )
        # El plan de visita en UNA sentencia: su disparador (migración 0041) copia
        # el plan a cada tienda una sola vez, no una por renglón.
        await sesion.execute(
            text(
                "INSERT INTO clientes_frecuencia (cliente_id, dia_semana) "
                "SELECT * FROM unnest(CAST(:ids AS uuid[]), CAST(:dias AS smallint[]))"
            ),
            {"ids": ids, "dias": dias},
        )
    await sesion.commit()
    resultado.tiendas = len(renglones)
    return resultado
