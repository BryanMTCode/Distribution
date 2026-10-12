"""Lo común a los objetivos (ADR 0002 §28, §104, §115).

El único objetivo que se captura es el diario de cada vendedor
(`objetivos_diarios.py`); la semana, el mes y la empresa se suman de él. Aquí
queda lo que comparten el dashboard y la API.

Las tablas `objetivos_ruta` (el objetivo del mes, 0021) y `objetivos` (los de
periodo, 0055) ya no se leen ni se escriben desde §115. Se conservan para no
borrar lo que ya se había capturado.
"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

from sqlalchemy import text

# Un error de dedo, no una regla.
OBJETIVO_MAXIMO = Decimal("50000000")


class ObjetivoInvalido(Exception):
    """El objetivo no se guardó, y el mensaje dice por qué."""


def fin_de_mes(dia: date) -> date:
    return dia.replace(day=calendar.monthrange(dia.year, dia.month)[1])


async def rutas_para_objetivos(sesion) -> list[dict]:
    """Las rutas activas —una por vendedor—, en orden de código."""
    return [
        dict(f)
        for f in (
            await sesion.execute(
                text("SELECT id, codigo, nombre FROM rutas WHERE activo ORDER BY codigo")
            )
        ).mappings().all()
    ]
