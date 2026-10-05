"""La aritmética del cierre del día.

────────────────────────────────────────────────────────────────────────────
EL CAMIÓN ES UN ALMACÉN RODANTE
────────────────────────────────────────────────────────────────────────────
Decisión de negocio de la dirección, octubre 2026: la mercancía que no se vende
se queda a dormir en el camión y se acumula con la carga del día siguiente. El
camión NO amanece en ceros.

Eso cambia la ecuación, y el término que entra es el que decide si se le cobra o
no al vendedor mercancía que está físicamente arriba de su camión:

    esperado   = inicial + cargado − vendido − merma + devuelto
    diferencia = contado − esperado

`inicial` es el saldo con el que el camión amaneció. Sin él, TODO el sobrante de
los días anteriores aparecía como faltante, cada noche, y el vendedor pagaba por
mercancía que podía tocar.

`contado` es lo que se **cuenta físicamente arriba del camión** al cerrar el día.
Antes se llamaba `retornado` y significaba «lo que bajó a la bodega»; ya no baja
nada. Todo lo demás lo calcula el sistema de sus propios documentos, y la
diferencia es lo único que importa del cierre:

    diferencia < 0  →  FALTANTE: salió mercancía sin documento. Se le cobra.
    diferencia > 0  →  SOBRANTE: hay más de lo que el sistema sabe. Casi siempre
                       es una venta que el teléfono no ha sincronizado todavía.
    diferencia = 0  →  cuadra, y no hay nada que mover.

El signo del sobrante es la razón por la que **no se puede cerrar una liquidación
con operaciones pendientes** (§2.3): una venta que entra después del cierre
convierte un sobrante en un cuadre, y el cierre ya dijo lo contrario por escrito.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTO ES UN MÓDULO DE DOMINIO Y NO UNA RESTA EN LA PANTALLA
────────────────────────────────────────────────────────────────────────────
La misma ecuación vive en tres lugares: aquí, en la columna generada
`liquidacion_detalle.diferencia` de PostgreSQL, y en el teléfono, que la calcula
para que el vendedor sepa su faltante antes de llegar a la oficina. Si los tres no
coinciden, el vendedor discute con la oficina sobre un número que cada uno calculó
distinto.

Escrito una vez, replicado textualmente, y con la prueba que compara este módulo
contra la columna generada de la base.

El devuelto **suma** al esperado y es el signo que se escribe mal: una devolución
de cliente entra al camión, así que sigue arriba al contarlo. Restarla haría
aparecer un faltante exactamente del tamaño de las devoluciones del día.

Módulo de dominio puro: sin imports de FastAPI ni de SQLAlchemy.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

__all__ = [
    "RenglonDeLiquidacion",
    "diferencia_de_efectivo",
    "esperado_en_camion",
    "saldo_inicial",
]

_MILESIMA = Decimal("0.001")
_CENTAVO = Decimal("0.01")


def esperado_en_camion(
    inicial: Decimal,
    cargado: Decimal,
    vendido: Decimal,
    merma: Decimal,
    devuelto: Decimal,
) -> Decimal:
    """Lo que el sistema cree que debe estar arriba del camión, en unidad base.

    `inicial` **suma**: el camión es un almacén rodante y amaneció con lo que no
    se vendió los días anteriores. Omitirlo le cobraba al vendedor, como faltante,
    todo lo que traía desde antes.

    `devuelto` **suma**: una devolución de cliente entra al camión. Restarla haría
    aparecer un faltante exactamente del tamaño de las devoluciones del día, y la
    discusión con el vendedor sería sobre un número mal calculado.
    """
    return (inicial + cargado - vendido - merma + devuelto).quantize(
        _MILESIMA, rounding=ROUND_HALF_UP
    )


def saldo_inicial(
    en_camion: Decimal,
    cargado: Decimal,
    vendido: Decimal,
    merma: Decimal,
    devuelto: Decimal,
) -> Decimal:
    """Con cuánto amaneció el camión, deducido del saldo de ahora.

    Es `esperado_en_camion` despejada: el saldo que el sistema tiene AHORA menos
    lo que los documentos de hoy le hicieron. No se guarda un snapshot al
    confirmar la carga, y es deliberado:

    · Un snapshot envejece. Entre confirmar la carga y cerrar el día entran ventas
      que el teléfono sincroniza tarde, y el inicial tiene que seguir siendo el de
      esa mañana sin que nadie lo recalcule a mano.
    · Un ajuste de oficina a media mañana —una corrección de inventario, un
      traspaso entre camiones— queda ABSORBIDO aquí, y eso es lo correcto: al
      vendedor se le cobra la diferencia entre su conteo y lo que el sistema
      tiene, nunca las correcciones que hizo la oficina.

    La consecuencia útil: `esperado` siempre acaba siendo el saldo vivo del camión,
    así que `diferencia` es siempre «lo que conté menos lo que el sistema tiene» —
    el único número que se le puede cobrar a una persona y defender frente a ella.
    """
    return (en_camion - cargado + vendido + merma - devuelto).quantize(
        _MILESIMA, rounding=ROUND_HALF_UP
    )


@dataclass(frozen=True)
class RenglonDeLiquidacion:
    """Un producto en el cierre del día, con su veredicto."""

    cargado: Decimal
    vendido: Decimal
    merma: Decimal
    devuelto: Decimal
    contado: Decimal
    inicial: Decimal = Decimal(0)

    @property
    def esperado(self) -> Decimal:
        return esperado_en_camion(
            self.inicial, self.cargado, self.vendido, self.merma, self.devuelto
        )

    @property
    def diferencia(self) -> Decimal:
        """Contado menos esperado. Negativo es faltante.

        Réplica textual de la columna generada `liquidacion_detalle.diferencia`.
        """
        return (self.contado - self.esperado).quantize(
            _MILESIMA, rounding=ROUND_HALF_UP
        )

    @property
    def cuadra(self) -> bool:
        return self.diferencia == 0

    @property
    def es_faltante(self) -> bool:
        return self.diferencia < 0


def diferencia_de_efectivo(entregado: Decimal, esperado: Decimal) -> Decimal:
    """Lo que trae de menos (negativo) o de más (positivo) en la bolsa.

    Réplica de `liquidaciones.diferencia_efectivo`, que es columna generada.
    """
    return (entregado - esperado).quantize(_CENTAVO, rounding=ROUND_HALF_UP)
