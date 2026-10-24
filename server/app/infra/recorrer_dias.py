"""Cambiar los días del plan de visita: recorrerlos, moverlos o intercambiarlos
(ADR 0002 §175).

────────────────────────────────────────────────────────────────────────────
EL PEDIDO
────────────────────────────────────────────────────────────────────────────
«Quiero cambiar las rutas que tienen asignadas, recorrerlas un día para atrás:
lo que iban a visitar el martes que lo visiten el lunes, lo que iban a visitar el
miércoles el martes, y así. Que todo lo que han trabajado estos días siga como
tal; solo que a partir del lunes trabajen con las rutas actualizadas.» Y luego:
«que desde el dashboard sea fácil intercambiar los días de las rutas, con una
opción de mover todas las de un día para otro día».

Un cambio de días es un MAPA de día a día, para una ruta o para todas, a partir
de una fecha:

· **Recorrer para atrás**: el martes al lunes … el sábado al viernes, y el lunes
  —que no tiene día antes en la semana de trabajo— al sábado.
· **Recorrer para adelante**: el lunes al martes … y el sábado al lunes.
· **Mover** todas las de un día a otro, e **intercambiar** dos días.

El orden de cada tienda no cambia. Al juntar un día con otro que ya tiene
tiendas, el orden se mezcla, y se dice.

────────────────────────────────────────────────────────────────────────────
LO TRABAJADO NO SE TOCA
────────────────────────────────────────────────────────────────────────────
Ningún renglón del plan vigente se borra: se cierra el día antes de la fecha
(`hasta`) y el nuevo empieza en la fecha (`desde`), en UNA transacción.
Efectividad, la venta «fuera de su día» y el mapa de la ruta siguen midiendo los
días ya trabajados con el plan que tenían (migración 0070). Lo que todavía no
empezaba —otro cambio para la misma fecha— sí se reacomoda: no tiene historia.

El teléfono recibe el plan nuevo en su siguiente sincronización, en cuanto se
cambia —no sabe de fechas en el plan—, así que se hace al terminar el día.

Dos veces el mismo cambio no se aplica dos veces: el panel confirma con la
«huella» del plan que enseñó, y el comando no recorre si ya hay plan que empiece
esa fecha o después.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text

PALABRA = "RECORRER"
ZONA = ZoneInfo("America/Mazatlan")

NOMBRE = {0: "domingo", 1: "lunes", 2: "martes", 3: "miércoles", 4: "jueves",
          5: "viernes", 6: "sábado"}
CORTO = {0: "Dom", 1: "Lun", 2: "Mar", 3: "Mié", 4: "Jue", 5: "Vie", 6: "Sáb"}
ORDEN = (1, 2, 3, 4, 5, 6, 0)

# Día de antes → día nuevo, con el número de `extract(dow)` (0 domingo). Un
# domingo, si alguna tienda lo tuviera, va al día de trabajo de al lado.
ATRAS = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 1: 6, 0: 6}
ADELANTE = {1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 1, 0: 1}
NUEVO_DIA = ATRAS

OPERACIONES = {
    "mover": "Mover todas las tiendas de un día a otro",
    "intercambiar": "Intercambiar dos días",
    "atras": "Recorrer todos los días uno para atrás (el martes al lunes…)",
    "adelante": "Recorrer todos los días uno para adelante (el lunes al martes…)",
}


def mapa_de(operacion: str, de: int | None = None, a: int | None = None) -> dict[int, int]:
    """El mapa de una operación del panel. ValueError con el porqué."""
    if operacion == "atras":
        return dict(ATRAS)
    if operacion == "adelante":
        return dict(ADELANTE)
    if operacion not in ("mover", "intercambiar"):
        raise ValueError("Elige qué cambio hacer.")
    if de not in NOMBRE or a not in NOMBRE:
        raise ValueError("Elige los dos días.")
    if de == a:
        raise ValueError(f"Los dos días son el {NOMBRE[de]}: no hay nada que cambiar.")
    return {de: a} if operacion == "mover" else {de: a, a: de}


def describir(mapa: dict[int, int]) -> str:
    if mapa == ATRAS:
        return "lo del martes pasa al lunes, el miércoles al martes, y así; lo del lunes al sábado"
    if mapa == ADELANTE:
        return ("lo del lunes pasa al martes, el martes al miércoles, y así; lo del sábado "
                "al lunes")
    if len(mapa) == 2 and all(mapa[b] == a for a, b in mapa.items()):
        a, b = sorted(mapa, key=ORDEN.index)
        return f"lo del {NOMBRE[a]} pasa al {NOMBRE[b]} y lo del {NOMBRE[b]} al {NOMBRE[a]}"
    return ", ".join(f"lo del {NOMBRE[v]} pasa al {NOMBRE[n]}" for v, n in mapa.items())


def hoy_en_mazatlan() -> date:
    return datetime.now(ZONA).date()


def proximo_lunes(hoy: date) -> date:
    """El lunes que sigue; si hoy es lunes, el de la otra semana."""
    return hoy + timedelta(days=7 - hoy.weekday())


def fecha(dia: date) -> str:
    return f"{NOMBRE[dia.isoweekday() % 7]} {dia:%d/%m/%Y}"


def por_dia(cuenta: Counter) -> str:
    return " · ".join(f"{CORTO[d]} {cuenta[d]}" for d in ORDEN if cuenta[d]) or "sin días"


@dataclass
class Plan:
    desde: date
    mapa: dict[int, int]
    # Ruta → (tiendas por día ahora, tiendas por día después).
    rutas: dict[str, tuple[Counter, Counter]] = field(default_factory=dict)
    # Días de visita que cambian, y de cuántas tiendas.
    renglones: int = 0
    tiendas: int = 0
    avisos: list[str] = field(default_factory=list)
    problemas: list[str] = field(default_factory=list)
    huella: str = ""

    @property
    def se_puede(self) -> bool:
        return not self.problemas and self.renglones > 0


@dataclass
class Resultado:
    renglones: int
    tiendas: int


async def _vigentes(sesion, rutas: list[uuid.UUID] | None):
    """El plan vigente del alcance: cada renglón con su tienda y su ruta."""
    return (
        await sesion.execute(
            text(
                """
                SELECT f.id, f.cliente_id, f.dia_semana, f.semana_del_mes, f.desde,
                       COALESCE(r.codigo, 'sin ruta') AS ruta
                  FROM clientes_frecuencia f
                  JOIN clientes c ON c.id = f.cliente_id
                  LEFT JOIN rutas r ON r.id = c.ruta_id
                 WHERE f.hasta IS NULL
                   AND (CAST(:rutas AS uuid[]) IS NULL OR c.ruta_id = ANY(CAST(:rutas AS uuid[])))
                 ORDER BY f.id
                """
            ),
            {"rutas": [str(r) for r in rutas] if rutas is not None else None},
        )
    ).mappings().all()


def _movidas(filas, mapa: dict[int, int]) -> list:
    return [f for f in filas
            if f["dia_semana"] in mapa and mapa[f["dia_semana"]] != f["dia_semana"]]


async def planear(
    sesion,
    *,
    mapa: dict[int, int],
    desde: date,
    hoy: date,
    rutas: list[uuid.UUID] | None = None,
    una_vez: bool = False,
) -> Plan:
    """Lo que va a pasar, sin tocar nada.

    `una_vez`: el comando no recorre si ya hay plan que empiece esa fecha o
    después —sería recorrerlo dos veces—. El panel no lo necesita: confirma con
    la huella.
    """
    plan = Plan(desde=desde, mapa=mapa)
    if desde <= hoy:
        plan.problemas.append(
            f"el cambio empezaría el {fecha(desde)} y hoy es {fecha(hoy)}: tiene que "
            "empezar después de hoy, para no cambiar lo que ya se trabajó"
        )
    filas = await _vigentes(sesion, rutas)
    if una_vez:
        ya = [f for f in filas if f["desde"] >= desde]
        if ya:
            plan.problemas.append(
                f"ya hay {len(ya)} día(s) de visita que empiezan el "
                f"{fecha(min(f['desde'] for f in ya))} o después: ¿ya se recorrió? No se "
                "recorre dos veces"
            )
    plan.huella = hashlib.sha256(
        "|".join(f"{f['id']}:{f['dia_semana']}:{f['desde']}" for f in filas).encode()
    ).hexdigest()[:16]

    ruta_de = {f["cliente_id"]: f["ruta"] for f in filas}
    antes: dict[uuid.UUID, set] = {}
    despues: dict[uuid.UUID, set] = {}
    for f in filas:
        antes.setdefault(f["cliente_id"], set()).add((f["dia_semana"], f["semana_del_mes"]))
        despues.setdefault(f["cliente_id"], set()).add(
            (mapa.get(f["dia_semana"], f["dia_semana"]), f["semana_del_mes"])
        )
    for cliente, dias in antes.items():
        ahora_r, luego_r = plan.rutas.setdefault(ruta_de[cliente], (Counter(), Counter()))
        for d, _ in dias:
            ahora_r[d] += 1
        for d, _ in despues[cliente]:
            luego_r[d] += 1
    movidas = _movidas(filas, mapa)
    plan.renglones = len(movidas)
    plan.tiendas = len({f["cliente_id"] for f in movidas})
    plan.rutas = dict(sorted(plan.rutas.items()))

    # Lo que conviene saber antes de confirmar.
    juntas = sum(len(antes[c]) - len(despues[c]) for c in antes)
    if juntas:
        plan.avisos.append(
            f"{juntas} tienda(s) ya tenían los dos días: se quedan con uno solo."
        )
    destinos = set(mapa.values()) - set(mapa)
    for ruta, (ahora_r, _) in plan.rutas.items():
        for d in sorted(destinos, key=ORDEN.index):
            llegan = sum(1 for f in movidas if f["ruta"] == ruta and mapa[f["dia_semana"]] == d)
            if llegan and ahora_r[d]:
                plan.avisos.append(
                    f"En {ruta} el {NOMBRE[d]} ya tiene {ahora_r[d]} tienda(s): se juntan con "
                    f"la(s) {llegan} que llega(n) y el orden de visita se mezcla. Acomódalo en "
                    "la columna Orden."
                )
    return plan


async def aplicar(
    sesion,
    *,
    mapa: dict[int, int],
    desde: date,
    hoy: date,
    quien: uuid.UUID | None,
    rutas: list[uuid.UUID] | None = None,
    huella: str | None = None,
    una_vez: bool = False,
) -> Resultado:
    """Cierra lo vigente el día antes de `desde` y lo abre cambiado en `desde`.

    En la misma transacción, y en pasos: primero se cierra (o se quita, lo que
    todavía no empezaba) y después se escribe lo nuevo. En uno solo, el martes
    nuevo de una tienda que tiene martes y miércoles chocaría con su martes de
    antes, todavía sin cerrar. Mientras tanto el disparador del plan (0041)
    espera, y al final corre una vez por tienda: al teléfono le llega UN cambio,
    el del plan nuevo.
    """
    plan = await planear(sesion, mapa=mapa, desde=desde, hoy=hoy, rutas=rutas,
                         una_vez=una_vez)
    if plan.problemas:
        raise ValueError("; ".join(plan.problemas))
    if huella is not None and huella != plan.huella:
        raise ValueError("el plan cambió desde que lo viste: revísalo otra vez antes de "
                         "confirmar")
    filas = await _vigentes(sesion, rutas)
    movidas = _movidas(filas, mapa)
    if not movidas:
        return Resultado(renglones=0, tiendas=0)
    ya_empezaron = [f["id"] for f in movidas if f["desde"] < desde]
    sin_empezar = [f["id"] for f in movidas if f["desde"] >= desde]

    await sesion.execute(text("SELECT set_config('dsd.plan_en_pausa', 'si', true)"))
    if ya_empezaron:
        await sesion.execute(
            text("UPDATE clientes_frecuencia SET hasta = CAST(:desde AS date) - 1 "
                 " WHERE id = ANY(:ids)"),
            {"desde": desde, "ids": ya_empezaron},
        )
    if sin_empezar:
        await sesion.execute(
            text("DELETE FROM clientes_frecuencia WHERE id = ANY(:ids)"), {"ids": sin_empezar}
        )
    # Lo nuevo, sin repetir lo que la tienda ya tiene vigente ni dos veces lo mismo
    # (el lunes y el domingo van los dos al sábado).
    movidos = {f["id"] for f in movidas}
    se_quedan = {(f["cliente_id"], f["dia_semana"], f["semana_del_mes"])
                 for f in filas if f["id"] not in movidos}
    nuevas: dict[tuple, date] = {}
    for f in movidas:
        llave = (f["cliente_id"], mapa[f["dia_semana"]], f["semana_del_mes"])
        if llave in se_quedan:
            continue
        empieza = max(f["desde"], desde)
        nuevas[llave] = min(nuevas.get(llave, empieza), empieza)
    if nuevas:
        await sesion.execute(
            text(
                "INSERT INTO clientes_frecuencia (cliente_id, dia_semana, semana_del_mes, desde) "
                "SELECT c, d, s, e "
                "  FROM unnest(CAST(:clientes AS uuid[]), CAST(:dias AS smallint[]), "
                "              CAST(:semanas AS smallint[]), CAST(:desdes AS date[])) "
                "       AS t(c, d, s, e)"
            ),
            {
                "clientes": [c for c, _, _ in nuevas],
                "dias": [d for _, d, _ in nuevas],
                "semanas": [s for _, _, s in nuevas],
                "desdes": list(nuevas.values()),
            },
        )
    # Y ahora sí, el plan al teléfono: una vez por tienda, con todo ya escrito.
    await sesion.execute(text("SELECT set_config('dsd.plan_en_pausa', '', true)"))
    tiendas = sorted({f["cliente_id"] for f in movidas}, key=str)
    await sesion.execute(
        text("UPDATE clientes_frecuencia SET dia_semana = dia_semana "
             " WHERE cliente_id = ANY(:c) AND hasta IS NULL"),
        {"c": tiendas},
    )
    await sesion.execute(
        text(
            "INSERT INTO auditoria (entidad, entidad_id, accion, usuario_id, datos_despues, "
            "                       motivo) "
            "VALUES ('plan_visita', :id, 'cambiar_dias', :u, CAST(:datos AS jsonb), :motivo)"
        ),
        {
            "id": uuid.uuid4(),
            "u": quien,
            "datos": json.dumps({
                "desde": desde.isoformat(),
                "dias": {NOMBRE[v]: NOMBRE[n] for v, n in mapa.items()},
                "renglones": len(movidas),
                "tiendas": len(tiendas),
                "por_ruta": {r: {"antes": por_dia(a), "despues": por_dia(d)}
                             for r, (a, d) in plan.rutas.items()},
            }),
            "motivo": f"Días del plan de visita cambiados desde el {fecha(desde)}: "
                      f"{describir(mapa)} (ADR 0002 §175)",
        },
    )
    await sesion.commit()
    return Resultado(renglones=len(movidas), tiendas=len(tiendas))
