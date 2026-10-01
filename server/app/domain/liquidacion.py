"""La aritmética del cierre del día.

────────────────────────────────────────────────────────────────────────────
LA ECUACIÓN QUE ATRAPA LOS DESCUADRES
────────────────────────────────────────────────────────────────────────────
    esperado   = cargado − vendido − merma + devuelto
    diferencia = retornado − esperado

`esperado` es lo que el sistema cree que debe venir de regreso en el camión.
`retornado` es lo que **se contó físicamente** al bajarlo. La diferencia es lo
único que importa del cierre:

    diferencia < 0  →  FALTANTE: salió mercancía sin documento. Se le cobra.
    diferencia > 0  →  SOBRANTE: viene más de lo que el sistema sabe. Casi siempre
                       es una venta que el teléfono no ha sincronizado todavía.
    diferencia = 0  →  cuadra.

El signo del sobrante es la razón por la que **no se puede cerrar una liquidación
con operaciones pendientes** (§2.3): una venta que entra después del cierre
convierte un sobrante en un cuadre, y el cierre ya dijo lo contrario por escrito.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTO ES UN MÓDULO DE DOMINIO Y NO UNA RESTA EN LA PANTALLA
────────────────────────────────────────────────────────────────────────────
La misma ecuación vive en tres lugares: aquí, en la columna generada
`liquidacion_detalle.diferencia` de PostgreSQL, y —cuando llegue la Fase 6— en el
teléfono, que la va a calcular para que el vendedor sepa su faltante antes de
llegar a la oficina. Si los tres no coinciden, el vendedor discute con la oficina
sobre un número que cada uno calculó distinto.

Escrito una vez, replicado textualmente, y con la prueba que compara este módulo
contra la columna generada de la base.

El devuelto **suma** al esperado y es el signo que se escribe mal: una devolución
de cliente entra al camión, así que debe volver a la bodega. Restarla haría
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
]

_MILESIMA = Decimal("0.001")
_CENTAVO = Decimal("0.01")


def esperado_en_camion(
    cargado: Decimal,
    vendido: Decimal,
    merma: Decimal,
    devuelto: Decimal,
) -> Decimal:
    """Lo que el sistema cree que debe venir de regreso, en unidad base.

    `devuelto` **suma**: una devolución de cliente entra al camión. Restarla haría
    aparecer un faltante exactamente del tamaño de las devoluciones del día, y la
    discusión con el vendedor sería sobre un número mal calculado.
    """
    return (cargado - vendido - merma + devuelto).quantize(
        _MILESIMA, rounding=ROUND_HALF_UP
    )


@dataclass(frozen=True)
class RenglonDeLiquidacion:
    """Un producto en el cierre del día, con su veredicto."""

    cargado: Decimal
    vendido: Decimal
    merma: Decimal
    devuelto: Decimal
    retornado: Decimal

    @property
    def esperado(self) -> Decimal:
        return esperado_en_camion(self.cargado, self.vendido, self.merma, self.devuelto)

    @property
    def diferencia(self) -> Decimal:
        """Retornado menos esperado. Negativo es faltante.

        Réplica textual de la columna generada `liquidacion_detalle.diferencia`.
        """
        return (self.retornado - self.esperado).quantize(
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
