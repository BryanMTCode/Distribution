"""Reglas de crédito y bloqueo por límite.

Decisión de negocio (ADR 0002): límite de saldo en dinero por cliente. Al
excederlo se bloquea la venta **a crédito**, pero el cliente sigue pudiendo
comprar **de contado**. Nunca se le cierra la puerta a una venta que entra en
efectivo.

──────────────────────────────────────────────────────────────────────────────
LA TRAMPA DEL OFFLINE
──────────────────────────────────────────────────────────────────────────────
El teléfono valida contra un saldo en caché que puede tener horas. Si el
cálculo usara solo ese número, el vendedor podría hacer cinco ventas a crédito
en la misma mañana —cada una individualmente por debajo del límite— y dejar al
cliente al triple de su línea, porque ninguna alcanzó a sincronizar.

Por eso el saldo efectivo cuenta también lo que este dispositivo tiene sin
sincronizar, en los dos sentidos:

    saldo_efectivo = saldo_confirmado        (lo que dice el servidor)
                   + cargos_pendientes       (ventas a crédito locales)
                   - abonos_pendientes       (cobros locales; liberan línea)

Los abonos cuentan igual de rápido que los cargos. Si el vendedor acaba de
cobrarle al cliente en efectivo, la línea se libera en ese momento: hacerlo
esperar a la sincronización sería negarle una venta que ya pagó.

──────────────────────────────────────────────────────────────────────────────
DOS EVALUACIONES, UNA SOLA FUNCIÓN
──────────────────────────────────────────────────────────────────────────────
La misma regla corre en dos lugares, y hacen cosas distintas:

  · En el TELÉFONO, antes de cerrar el carrito: bloquea de verdad. Es el único
    momento en que bloquear sirve, porque la mercancía todavía no sale.

  · En el SERVIDOR, al recibir la venta: solo MARCA `requiere_revision`. La
    mercancía ya salió del camión y el cliente ya tiene su remisión impresa
    (§0.1 del documento de arquitectura). Rechazarla descuadraría la caja y el
    inventario sin devolver el producto.

Módulo de dominio puro: sin imports de FastAPI ni de SQLAlchemy.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

__all__ = [
    "CERO",
    "EstadoCredito",
    "Motivo",
    "Resultado",
    "evaluar_venta",
]

CERO = Decimal("0.00")


class Motivo(StrEnum):
    """Por qué se permitió o se negó. El código viaja al dispositivo y al
    panel; el texto para el vendedor se arma en la UI."""

    OK = "ok"
    CONTADO_SIEMPRE_PERMITIDO = "contado_siempre_permitido"
    SIN_LINEA_DE_CREDITO = "sin_linea_de_credito"
    CLIENTE_BLOQUEADO = "cliente_bloqueado"
    EXCEDE_LIMITE = "excede_limite"


@dataclass(frozen=True)
class EstadoCredito:
    """Foto del crédito de un cliente en el momento de evaluar.

    En el dispositivo se arma con el saldo sincronizado más lo que el propio
    equipo tiene en la cola; en el servidor, `cargos_pendientes` y
    `abonos_pendientes` van en cero porque ahí el saldo ya es el real.
    """

    limite: Decimal
    saldo_confirmado: Decimal
    permite_credito: bool = True
    bloqueado: bool = False
    cargos_pendientes: Decimal = CERO
    abonos_pendientes: Decimal = CERO

    def __post_init__(self) -> None:
        for campo in ("limite", "saldo_confirmado", "cargos_pendientes", "abonos_pendientes"):
            valor = getattr(self, campo)
            if not isinstance(valor, Decimal):
                raise TypeError(f"{campo} debe ser Decimal, no {type(valor).__name__}")
            if valor < 0:
                raise ValueError(f"{campo} no puede ser negativo: {valor}")

    @property
    def saldo_efectivo(self) -> Decimal:
        """Lo que el cliente debe realmente, contando la cola sin sincronizar.

        Puede quedar en negativo si el cliente pagó de más: eso es saldo a
        favor y suma línea disponible, no se recorta a cero aquí.
        """
        return self.saldo_confirmado + self.cargos_pendientes - self.abonos_pendientes

    @property
    def disponible(self) -> Decimal:
        """Cuánto puede cargar todavía. Nunca negativo: un cliente pasado de
        su límite tiene cero disponible, no una deuda de línea."""
        return max(CERO, self.limite - self.saldo_efectivo)


@dataclass(frozen=True)
class Resultado:
    permitida: bool
    motivo: Motivo
    disponible: Decimal
    # Cuánto se pasa del límite. Sirve para el mensaje al vendedor ("le faltan
    # $340 de abono") y para el marcado en el servidor.
    excedente: Decimal = CERO

    @property
    def requiere_revision(self) -> bool:
        """En el servidor: una venta que la regla habría negado entra igual,
        pero marcada. La mercancía ya salió."""
        return not self.permitida


def evaluar_venta(estado: EstadoCredito, total: Decimal, *, a_credito: bool) -> Resultado:
    """Decide si la venta procede.

    El contado nunca se bloquea: da igual cuánto deba el cliente, si paga en
    efectivo la venta entra. Es la regla explícita del negocio y también la
    correcta — negarla no cobra la deuda vieja y sí pierde la venta nueva.
    """
    if not isinstance(total, Decimal):
        raise TypeError(f"el total debe ser Decimal, no {type(total).__name__}")
    if total < 0:
        raise ValueError(f"el total no puede ser negativo: {total}")

    if not a_credito:
        return Resultado(True, Motivo.CONTADO_SIEMPRE_PERMITIDO, estado.disponible)

    if estado.bloqueado:
        return Resultado(False, Motivo.CLIENTE_BLOQUEADO, estado.disponible)

    if not estado.permite_credito:
        return Resultado(False, Motivo.SIN_LINEA_DE_CREDITO, estado.disponible)

    nuevo_saldo = estado.saldo_efectivo + total
    if nuevo_saldo > estado.limite:
        return Resultado(
            False,
            Motivo.EXCEDE_LIMITE,
            estado.disponible,
            excedente=nuevo_saldo - estado.limite,
        )

    return Resultado(True, Motivo.OK, estado.disponible - total)
