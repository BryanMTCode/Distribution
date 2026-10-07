"""El periodo que se está mirando: hoy, la semana, el mes, o un rango a mano.

Lo usan el tablero, la pantalla de vendedores y la de sincronizaciones. Vive en un
solo lugar porque «esta semana» tiene que querer decir lo mismo en las tres: si el
tablero la empezara el lunes y vendedores el domingo, las cifras de una y otra no
cuadrarían y nadie sabría por qué.

La semana empieza en **lunes**: es como se trabaja la ruta, y es lo que ya usa el
plan de visita.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

# El orden es el del selector: de lo más cercano a lo más amplio.
PERIODOS: tuple[tuple[str, str], ...] = (
    ("hoy", "Hoy"),
    ("ayer", "Ayer"),
    ("semana", "Esta semana"),
    ("semana_pasada", "Semana pasada"),
    ("mes", "Este mes"),
    ("mes_pasado", "Mes pasado"),
    ("rango", "Personalizado"),
)

# Un rango a mano de varios años recorrería tablas enteras en cada recarga. Un año
# alcanza para cualquier pregunta de operación; lo demás es analítica.
DIAS_MAXIMOS = 366


@dataclass(frozen=True)
class Periodo:
    clave: str
    etiqueta: str
    inicio: date
    fin: date

    @property
    def dias(self) -> int:
        return (self.fin - self.inicio).days + 1

    @property
    def es_hoy(self) -> bool:
        return self.clave == "hoy"

    @property
    def descripcion(self) -> str:
        """Cómo se dice en una oración: «hoy», «del 1 al 7 de octubre»."""
        if self.inicio == self.fin:
            return "hoy" if self.es_hoy else f"el {_fecha(self.inicio)}"
        return f"del {_fecha(self.inicio)} al {_fecha(self.fin)}"

    def como_parametros(self) -> str:
        """Para armar un enlace que conserve el periodo."""
        if self.clave == "rango":
            return f"periodo=rango&desde={self.inicio.isoformat()}&hasta={self.fin.isoformat()}"
        return f"periodo={self.clave}"


_MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre",
)


def _fecha(dia: date) -> str:
    return f"{dia.day} de {_MESES[dia.month - 1]} de {dia.year}"


def _fecha_o(texto: str) -> date | None:
    try:
        return date.fromisoformat(texto) if texto else None
    except ValueError:
        return None


def leer_periodo(
    clave: str = "", desde: str = "", hasta: str = "", *, hoy: date | None = None
) -> Periodo:
    """El periodo del formulario. Lo que no se entienda cae en «hoy».

    Un periodo mal escrito no es un error que valga la pena mostrar: quien abrió la
    pantalla quiere ver cifras, y las de hoy son la respuesta útil. Un rango al revés
    se endereza, y uno de más de un año se recorta a un año contado hacia atrás desde
    su fin.
    """
    hoy = hoy or date.today()
    etiquetas = dict(PERIODOS)

    # Fechas escritas sin elegir «Personalizado»: es lo que la persona quiso.
    if clave not in etiquetas:
        clave = "rango" if (desde or hasta) else "hoy"

    if clave == "hoy":
        inicio = fin = hoy
    elif clave == "ayer":
        inicio = fin = hoy - timedelta(days=1)
    elif clave == "semana":
        inicio, fin = hoy - timedelta(days=hoy.weekday()), hoy
    elif clave == "semana_pasada":
        fin = hoy - timedelta(days=hoy.weekday() + 1)
        inicio = fin - timedelta(days=6)
    elif clave == "mes":
        inicio, fin = hoy.replace(day=1), hoy
    elif clave == "mes_pasado":
        fin = hoy.replace(day=1) - timedelta(days=1)
        inicio = fin.replace(day=1)
    else:
        fin = _fecha_o(hasta) or hoy
        inicio = _fecha_o(desde) or fin
        if inicio > fin:
            inicio, fin = fin, inicio
        if (fin - inicio).days + 1 > DIAS_MAXIMOS:
            inicio = fin - timedelta(days=DIAS_MAXIMOS - 1)

    return Periodo(clave=clave, etiqueta=etiquetas[clave], inicio=inicio, fin=fin)
