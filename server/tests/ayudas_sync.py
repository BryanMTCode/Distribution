"""Utilidades para armar lotes de sincronización en las pruebas.

Calculan el hash igual que lo haría el dispositivo, para que las pruebas
ejerzan el camino real y no una versión relajada del contrato.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.domain.sync.sobres import Operacion, Sobre, hash_de_sobre


def operacion_cliente(nombre: str = "Abarrotes Doña Mary", **extra: Any) -> Operacion:
    return Operacion(
        tipo="cliente.crear",
        entidad_id=uuid.uuid4(),
        datos={"nombre_comercial": nombre, **extra},
    )


def sobre(*operaciones: Operacion, secuencia: int = 1, visita_id: uuid.UUID | None = None) -> Sobre:
    """Arma un sobre con su hash correcto."""
    if not operaciones:
        operaciones = (operacion_cliente(),)
    base = Sobre(
        operacion_id=uuid.uuid4(),
        secuencia=secuencia,
        hash_payload="",
        operaciones=list(operaciones),
        visita_id=visita_id,
    )
    return Sobre(
        operacion_id=base.operacion_id,
        secuencia=secuencia,
        hash_payload=hash_de_sobre(base),
        operaciones=list(operaciones),
        visita_id=visita_id,
    )


def a_json(sobres: list[Sobre], lote_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Convierte a la forma que viaja por HTTP."""
    return {
        "lote_id": str(lote_id or uuid.uuid4()),
        "sobres": [
            {
                "operacion_id": str(s.operacion_id),
                "secuencia": s.secuencia,
                "hash_payload": s.hash_payload,
                "visita_id": str(s.visita_id) if s.visita_id else None,
                "operaciones": [
                    {"tipo": o.tipo, "entidad_id": str(o.entidad_id), "datos": o.datos}
                    for o in s.operaciones
                ],
            }
            for s in sobres
        ],
    }
