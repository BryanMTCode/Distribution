"""Tipos compartidos del contrato HTTP.

`Dinero` y `Cantidad` son la razón por la que este módulo existe: el dinero
viaja como **string**, nunca como número JSON. Pydantic serializa `Decimal` a
número, y Dart lo recibiría como `double` IEEE-754 — en una cartera con saldos
que se arrastran meses, eso son centavos perdidos. Ver contracts/README.md §1.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from app.domain.canonico import formatear_cantidad, formatear_dinero

Dinero = Annotated[
    Decimal,
    PlainSerializer(formatear_dinero, return_type=str, when_used="json"),
    Field(description="Importe con 2 decimales, serializado como string: '250.00'"),
]

Cantidad = Annotated[
    Decimal,
    PlainSerializer(formatear_cantidad, return_type=str, when_used="json"),
    Field(description="Cantidad con 3 decimales, serializada como string: '12.000'"),
]


class EsquemaBase(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
