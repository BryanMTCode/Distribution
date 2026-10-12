"""El objetivo diario de cada vendedor, y todo lo que se suma de él
(ADR 0002 §110, §115).

«Por día 3,500, hasta que yo lo modifique.» Y después: «quiero que todo se
sume». Es el ÚNICO objetivo que se captura: a cada vendedor (su ruta), una
cifra por día, con los días de la semana en que aplica, desde una fecha y sin
fin. Lo demás no se captura, se calcula:

  · la semana, el mes o cualquier periodo = la suma de sus días;
  · toda la empresa = la suma de todos los vendedores.

Cambiarlo es poner otro desde el día que se diga: los días anteriores se quedan
medidos contra el que tenían, así que la historia no se reescribe. Cada cambio
es un renglón de `objetivos_diarios` (0057); el objetivo de un día es el del
último renglón que ya había empezado, y un renglón sin monto es «sin objetivo
diario desde ese día». Los renglones sin ruta (la empresa, de antes de §115) ya
no cuentan: la empresa es la suma.

Lo usan Objetivos y el Tablero del dashboard, y el Tablero y Objetivos de la
app (solo para leer).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text

from app.domain.tablero import porcentaje
from app.infra.objetivos import OBJETIVO_MAXIMO, ObjetivoInvalido, rutas_para_objetivos

DIAS_CORTOS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
_EN_PLURAL = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábados", "domingos")
TODA_LA_SEMANA = (1, 2, 3, 4, 5, 6, 7)
LUNES_A_SABADO = (1, 2, 3, 4, 5, 6)

# Cuántos días atrás se ven junto a hoy: una semana se lee de un vistazo.
DIAS_ATRAS = 6

# Un cambio puede valer desde unos días atrás —«desde el lunes»—, pero no
# reescribir meses: lo de antes ya se midió y ya se habló con el vendedor.
DIAS_ATRAS_MAXIMOS = 31


def dias_en_palabras(dias) -> str:
    """(1, 2, 3, 4, 5, 6) → «de lunes a sábado»; (1, 3, 5) → «lun, mié, vie»."""
    dias = tuple(sorted(set(dias)))
    if dias == TODA_LA_SEMANA:
        return "todos los días"
    if dias == LUNES_A_SABADO:
        return "de lunes a sábado"
    if dias == (1, 2, 3, 4, 5):
        return "de lunes a viernes"
    if len(dias) == 1:
        return f"los {_EN_PLURAL[dias[0] - 1]}"
    return ", ".join(DIAS_CORTOS[d - 1] for d in dias)


@dataclass(frozen=True)
class Tramo:
    """Un cambio: desde `desde`, tanto cada día, estos días de la semana."""

    id: uuid.UUID
    desde: date
    objetivo_venta: Decimal | None
    objetivo_visitas: int | None
    dias_semana: tuple[int, ...]
    nota: str | None
    fijado_por: str | None

    @property
    def quitado(self) -> bool:
        return self.objetivo_venta is None

    def aplica(self, dia: date) -> bool:
        return self.objetivo_venta is not None and dia.isoweekday() in self.dias_semana

    @property
    def dias_en_palabras(self) -> str:
        return dias_en_palabras(self.dias_semana)


@dataclass(frozen=True)
class DiaMedido:
    """Un día contra su objetivo. `objetivo` nulo: ese día no tocaba."""

    dia: date
    objetivo: Decimal | None
    objetivo_visitas: int | None
    venta: Decimal
    visitas: int
    # Había objetivo, pero ese día de la semana no aplica (el domingo, digamos).
    descanso: bool

    @property
    def corto(self) -> str:
        return f"{DIAS_CORTOS[self.dia.weekday()]} {self.dia.day}"

    @property
    def es_hoy(self) -> bool:
        return self.dia == date.today()

    @property
    def logrado(self) -> Decimal | None:
        if not self.objetivo:
            return None
        return (self.venta * 100 / self.objetivo).quantize(Decimal("0.1"))

    @property
    def cumplido(self) -> bool:
        return self.objetivo is not None and self.venta >= self.objetivo

    @property
    def falta(self) -> Decimal:
        return max(Decimal(0), (self.objetivo or Decimal(0)) - self.venta)


@dataclass(frozen=True)
class ObjetivoDiario:
    ruta_id: uuid.UUID | None
    # «R1101 · Ruta 1101»; nulo = toda la empresa.
    ruta: str | None
    # El que vale hoy (nulo si hoy no hay), y los cambios ya programados.
    vigente: Tramo | None
    proximos: list[Tramo]
    # Del más viejo a hoy.
    dias: list[DiaMedido]

    @property
    def hoy(self) -> DiaMedido:
        return self.dias[-1]

    @property
    def medidos(self) -> int:
        """Los días ya terminados que tenían objetivo."""
        return sum(1 for d in self.dias[:-1] if d.objetivo is not None)

    @property
    def cumplidos(self) -> int:
        return sum(1 for d in self.dias[:-1] if d.cumplido)


def _tramo_del_dia(tramos: list[Tramo], dia: date) -> Tramo | None:
    """El último cambio que ya había empezado ese día (vienen por `desde`)."""
    elegido = None
    for t in tramos:
        if t.desde > dia:
            break
        elegido = t
    return elegido


async def _existe_la_ruta(sesion, ruta_id: uuid.UUID | None) -> None:
    # La empresa no tiene objetivo propio: es la suma de los vendedores (§115).
    if ruta_id is None:
        raise ObjetivoInvalido("Escoge el vendedor, o «Todos los vendedores».")
    fila = (
        await sesion.execute(text("SELECT 1 FROM rutas WHERE id = :r"), {"r": ruta_id})
    ).first()
    if fila is None:
        raise ObjetivoInvalido("Esa ruta ya no existe.")


_GUARDAR = text(
    """
    INSERT INTO objetivos_diarios
        (ruta_id, desde, objetivo_venta, objetivo_visitas, dias_semana, nota, fijado_por)
    VALUES (:r, :desde, :monto, :visitas, CAST(:dias AS smallint[]), :nota, :quien)
    ON CONFLICT (ruta_id, desde) DO UPDATE SET
        objetivo_venta   = excluded.objetivo_venta,
        objetivo_visitas = excluded.objetivo_visitas,
        dias_semana      = excluded.dias_semana,
        nota             = excluded.nota,
        fijado_por       = excluded.fijado_por,
        actualizado_en   = now()
    RETURNING id
    """
)


async def fijar_objetivo_diario(
    sesion,
    *,
    ruta_id: uuid.UUID | None,
    desde: date,
    objetivo_venta: Decimal,
    objetivo_visitas: int | None,
    dias_semana,
    nota: str | None,
    quien: uuid.UUID | None,
) -> Tramo:
    """Pone (o cambia) el objetivo diario desde `desde`. No hace commit.

    Si ya tenía uno, este lo reemplaza desde esa fecha; los días anteriores se
    quedan con el que tenían. Guardar dos veces el mismo día lo corrige.
    """
    hoy = date.today()
    if desde < hoy - timedelta(days=DIAS_ATRAS_MAXIMOS):
        raise ObjetivoInvalido(
            f"Un cambio vale a lo más desde {DIAS_ATRAS_MAXIMOS} días atrás: lo de "
            "antes ya se midió."
        )
    if desde > hoy + timedelta(days=366):
        raise ObjetivoInvalido("Un cambio se programa a lo más con un año de anticipación.")
    if objetivo_venta <= 0:
        raise ObjetivoInvalido("Escribe cuánto se tiene que vender cada día.")
    if objetivo_venta > OBJETIVO_MAXIMO:
        raise ObjetivoInvalido(
            f"Un objetivo de ${objetivo_venta:,.2f} al día parece un error de dedo."
        )
    try:
        dias = tuple(sorted({int(d) for d in dias_semana}))
    except (TypeError, ValueError) as e:
        raise ObjetivoInvalido("Escoge los días de la semana en que aplica.") from e
    if not dias or dias[0] < 1 or dias[-1] > 7:
        raise ObjetivoInvalido("Escoge al menos un día de la semana en que aplica.")
    if objetivo_visitas is not None and objetivo_visitas <= 0:
        objetivo_visitas = None
    await _existe_la_ruta(sesion, ruta_id)

    nuevo = (
        await sesion.execute(
            _GUARDAR,
            {"r": ruta_id, "desde": desde, "monto": objetivo_venta,
             "visitas": objetivo_visitas, "dias": list(dias), "nota": nota, "quien": quien},
        )
    ).scalar_one()
    return Tramo(
        id=nuevo, desde=desde, objetivo_venta=objetivo_venta,
        objetivo_visitas=objetivo_visitas, dias_semana=dias, nota=nota, fijado_por=None,
    )


async def quitar_objetivo_diario(sesion, *, ruta_id: uuid.UUID | None, quien) -> None:
    """Desde hoy ya no hay objetivo diario. Los días pasados se quedan medidos
    como estaban, y los cambios que estaban programados ya no aplican. No hace
    commit."""
    await _existe_la_ruta(sesion, ruta_id)
    hoy = date.today()
    await sesion.execute(
        text(
            "DELETE FROM objetivos_diarios "
            " WHERE ruta_id IS NOT DISTINCT FROM :r AND desde > :hoy"
        ),
        {"r": ruta_id, "hoy": hoy},
    )
    await sesion.execute(
        _GUARDAR,
        {"r": ruta_id, "desde": hoy, "monto": None, "visitas": None,
         "dias": list(LUNES_A_SABADO), "nota": None, "quien": quien},
    )


async def cancelar_cambio(sesion, tramo_id: uuid.UUID) -> bool:
    """Borra un cambio que todavía no empieza. Uno que ya empezó es historia:
    se corrige poniendo otro. No hace commit."""
    fila = (
        await sesion.execute(
            text("DELETE FROM objetivos_diarios WHERE id = :id AND desde > :hoy RETURNING id"),
            {"id": tramo_id, "hoy": date.today()},
        )
    ).first()
    return fila is not None


async def _vendido_por_dia(
    sesion, desde: date, hasta: date
) -> dict[tuple[uuid.UUID | None, date], tuple[Decimal, int]]:
    """Lo vendido (confirmado) y las visitas de cada ruta cada día. La llave con
    ruta nula es todo lo del día, mayoreo incluido; las sumas de §115 no la usan:
    la empresa es la suma de los vendedores, y el mayoreo se dice aparte."""
    parametros = {"desde": desde, "hasta": hasta}
    ventas = (
        await sesion.execute(
            text(
                """
                SELECT GROUPING(ruta_id) = 1 AS de_todos, ruta_id,
                       fecha_operativa AS dia, sum(total) AS venta
                  FROM ventas
                 WHERE estado = 'confirmada'
                   AND fecha_operativa BETWEEN :desde AND :hasta
                 GROUP BY GROUPING SETS ((ruta_id, fecha_operativa), (fecha_operativa))
                """
            ),
            parametros,
        )
    ).mappings().all()
    mayoreo = (
        await sesion.execute(
            text(
                """
                SELECT fecha AS dia, sum(total) AS venta FROM ventas_mayoreo
                 WHERE estado = 'confirmada' AND fecha BETWEEN :desde AND :hasta
                 GROUP BY fecha
                """
            ),
            parametros,
        )
    ).mappings().all()
    visitas = (
        await sesion.execute(
            text(
                """
                SELECT GROUPING(ruta_id) = 1 AS de_todos, ruta_id, dia,
                       count(DISTINCT cliente_id) AS visitas
                  FROM (SELECT cliente_id, fecha_operativa AS dia, ruta_id FROM ventas
                         WHERE fecha_operativa BETWEEN :desde AND :hasta
                        UNION
                        SELECT cliente_id, fecha_operativa, ruta_id FROM no_drops
                         WHERE fecha_operativa BETWEEN :desde AND :hasta) x
                 GROUP BY GROUPING SETS ((ruta_id, dia), (dia))
                """
            ),
            parametros,
        )
    ).mappings().all()

    venta: dict[tuple[uuid.UUID | None, date], Decimal] = defaultdict(Decimal)
    for f in ventas:
        if f["de_todos"] or f["ruta_id"] is not None:
            venta[(None if f["de_todos"] else f["ruta_id"], f["dia"])] += Decimal(f["venta"])
    for f in mayoreo:
        venta[(None, f["dia"])] += Decimal(f["venta"])
    cuantas: dict[tuple[uuid.UUID | None, date], int] = {}
    for f in visitas:
        if f["de_todos"] or f["ruta_id"] is not None:
            cuantas[(None if f["de_todos"] else f["ruta_id"], f["dia"])] = int(f["visitas"])
    return {
        llave: (venta.get(llave, Decimal(0)), cuantas.get(llave, 0))
        for llave in set(venta) | set(cuantas)
    }


async def _vendedores(sesion) -> list[tuple[uuid.UUID, str]]:
    """Las rutas activas —una por vendedor—, en orden de código."""
    return [
        (r["id"], f"{r['codigo']} · {r['nombre']}") for r in await rutas_para_objetivos(sesion)
    ]


async def _tramos_por_ruta(sesion) -> dict[uuid.UUID, list[Tramo]]:
    """Los cambios de cada ruta, del más viejo al más nuevo."""
    filas = (
        await sesion.execute(
            text(
                """
                SELECT o.id, o.ruta_id, o.desde, o.objetivo_venta, o.objetivo_visitas,
                       o.dias_semana, o.nota, u.nombre AS fijado_por
                  FROM objetivos_diarios o
                  LEFT JOIN usuarios u ON u.id = o.fijado_por
                 WHERE o.ruta_id IS NOT NULL
                 ORDER BY o.ruta_id, o.desde
                """
            )
        )
    ).mappings().all()
    tramos: dict[uuid.UUID, list[Tramo]] = defaultdict(list)
    for f in filas:
        tramos[f["ruta_id"]].append(
            Tramo(
                id=f["id"],
                desde=f["desde"],
                objetivo_venta=(
                    Decimal(f["objetivo_venta"]) if f["objetivo_venta"] is not None else None
                ),
                objetivo_visitas=f["objetivo_visitas"],
                dias_semana=tuple(f["dias_semana"]),
                nota=f["nota"],
                fijado_por=f["fijado_por"],
            )
        )
    return tramos


def _medir(tramos: list[Tramo], dia: date, venta: Decimal, visitas: int) -> DiaMedido:
    tramo = _tramo_del_dia(tramos, dia)
    aplica = tramo is not None and tramo.aplica(dia)
    return DiaMedido(
        dia=dia,
        objetivo=tramo.objetivo_venta if aplica else None,
        objetivo_visitas=tramo.objetivo_visitas if aplica else None,
        venta=venta,
        visitas=visitas,
        descanso=tramo is not None and not tramo.quitado and not aplica,
    )


async def objetivos_diarios(sesion, *, dias_atras: int = DIAS_ATRAS) -> list[ObjetivoDiario]:
    """Cada vendedor (ruta activa), tenga o no objetivo diario, con el que vale
    hoy, los cambios programados y los últimos días medidos."""
    hoy = date.today()
    inicio = hoy - timedelta(days=dias_atras)
    vendedores = await _vendedores(sesion)
    tramos = await _tramos_por_ruta(sesion)
    vendido = await _vendido_por_dia(sesion, inicio, hoy)
    resultado = []
    for ruta_id, ruta in vendedores:
        suyos = tramos.get(ruta_id, [])
        dias = [
            _medir(suyos, dia, *vendido.get((ruta_id, dia), (Decimal(0), 0)))
            for dia in (inicio + timedelta(days=i) for i in range(dias_atras + 1))
        ]
        vigente = _tramo_del_dia(suyos, hoy)
        if vigente is not None and vigente.quitado:
            vigente = None
        resultado.append(
            ObjetivoDiario(
                ruta_id=ruta_id,
                ruta=ruta,
                vigente=vigente,
                proximos=[t for t in suyos if t.desde > hoy],
                dias=dias,
            )
        )
    return resultado


def de_toda_la_empresa(diarios: list[ObjetivoDiario]) -> list[DiaMedido]:
    """Día por día, la suma de todos los vendedores: sus objetivos y lo que
    vendieron. Sin objetivo ese día si ninguno lo tenía."""
    if not diarios:
        return []
    suma = []
    for i, dia in enumerate(d.dia for d in diarios[0].dias):
        del_dia = [o.dias[i] for o in diarios]
        objetivos = [d.objetivo for d in del_dia if d.objetivo is not None]
        suma.append(
            DiaMedido(
                dia=dia,
                objetivo=sum(objetivos, Decimal(0)) if objetivos else None,
                objetivo_visitas=None,
                venta=sum((d.venta for d in del_dia), Decimal(0)),
                visitas=sum(d.visitas for d in del_dia),
                descanso=False,
            )
        )
    return suma


# ---------------------------------------------------------------------------
# Lo que suma: cualquier periodo, por vendedor y de toda la empresa (§115)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Meta:
    """El objetivo de un periodo, sumado de sus días, contra lo vendido."""

    ruta_id: uuid.UUID | None
    # «R1101 · Ruta 1101»; nulo = toda la empresa (la suma).
    ruta: str | None
    objetivo: Decimal
    # Lo que suman los días ya transcurridos (hoy incluido): contra eso se dice
    # si va adelante o atrás. Un domingo de descanso no suma, y no atrasa.
    a_la_fecha: Decimal
    venta: Decimal
    visitas: int
    dias_con_objetivo: int

    @property
    def tiene_objetivo(self) -> bool:
        return self.objetivo > 0

    @property
    def logrado(self) -> Decimal | None:
        return porcentaje(self.venta, self.objetivo) if self.tiene_objetivo else None

    @property
    def esperado(self) -> Decimal:
        return porcentaje(self.a_la_fecha, self.objetivo)

    @property
    def diferencia(self) -> Decimal | None:
        return None if self.logrado is None else self.logrado - self.esperado

    @property
    def semaforo(self) -> str:
        """'sin_objetivo' | 'adelante' | 'cerca' | 'atras', como el tablero."""
        if (diferencia := self.diferencia) is None:
            return "sin_objetivo"
        if diferencia >= 0:
            return "adelante"
        if diferencia >= -10:
            return "cerca"
        return "atras"

    @property
    def falta(self) -> Decimal:
        return max(Decimal(0), self.objetivo - self.venta)


async def metas_del_rango(sesion, desde: date, hasta: date) -> tuple[list[Meta], Meta, Decimal]:
    """Cada vendedor con la suma de sus objetivos diarios del periodo y lo que
    vendió; la empresa, la suma de todos; y aparte el mayoreo del periodo, que
    no es de ningún vendedor y por eso no entra en la suma."""
    if hasta < desde:
        desde, hasta = hasta, desde
    hoy = date.today()
    vendedores = await _vendedores(sesion)
    tramos = await _tramos_por_ruta(sesion)
    vendido = await _vendido_por_dia(sesion, desde, hasta)
    metas = []
    for ruta_id, ruta in vendedores:
        suyos = tramos.get(ruta_id, [])
        objetivo = a_la_fecha = venta = Decimal(0)
        visitas = dias = 0
        dia = desde
        while dia <= hasta:
            tramo = _tramo_del_dia(suyos, dia)
            if tramo is not None and tramo.aplica(dia):
                objetivo += tramo.objetivo_venta
                dias += 1
                if dia <= hoy:
                    a_la_fecha += tramo.objetivo_venta
            v, n = vendido.get((ruta_id, dia), (Decimal(0), 0))
            venta += v
            visitas += n
            dia += timedelta(days=1)
        metas.append(Meta(ruta_id, ruta, objetivo, a_la_fecha, venta, visitas, dias))
    empresa = Meta(
        ruta_id=None,
        ruta=None,
        objetivo=sum((m.objetivo for m in metas), Decimal(0)),
        a_la_fecha=sum((m.a_la_fecha for m in metas), Decimal(0)),
        venta=sum((m.venta for m in metas), Decimal(0)),
        visitas=sum(m.visitas for m in metas),
        dias_con_objetivo=max((m.dias_con_objetivo for m in metas), default=0),
    )
    mayoreo = (
        await sesion.execute(
            text(
                "SELECT COALESCE(sum(total), 0) FROM ventas_mayoreo "
                " WHERE estado = 'confirmada' AND fecha BETWEEN :desde AND :hasta"
            ),
            {"desde": desde, "hasta": hasta},
        )
    ).scalar_one()
    return metas, empresa, Decimal(mayoreo)
