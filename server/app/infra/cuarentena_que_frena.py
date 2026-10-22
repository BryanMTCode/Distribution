"""Lo de la cuarentena que frena un corte o una carga (ADR 0002 §172).

Antes frenaba TODO lo pendiente del vendedor, de cualquier día: una ubicación
corregida de hace dos semanas, un no-drop a una tienda que cambió de ruta.
Y como aceptar la carga cierra el corte (§169), un pendiente olvidado dejaba al
camión sin carga al día siguiente —«No se aceptó: … en cuarentena»—.

La razón de frenar es que una operación en cuarentena puede ser la venta que
explica un faltante o un sobrante. Así que frena solo lo que puede explicarlo:

· **lo que mueve dinero o mercancía** —ventas y sus cancelaciones o cambios,
  mermas y devoluciones, lo que el camión devuelve, el corte mismo—, y lo que
  llegó tan mal formado que no se sabe qué es;
· **recibido desde el día de la carga** que se está cortando. Lo anterior es de
  días que ya se cerraron, y no cambia esta cuenta.

Un alta de tienda, un no-drop o una ubicación siguen en la cuarentena para que
la oficina los vea; solo dejan de frenar.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import text

# Los tipos de operación que pueden explicar una diferencia de dinero o de
# mercancía. `desconocido` es lo que llegó sin poderse leer (§172): podría ser
# una venta, y por eso frena.
_QUE_EXPLICA_UNA_DIFERENCIA = r"(^|\+)((venta|merma|traspaso|corte)\.|desconocido)"


async def cuarentena_que_frena(
    sesion, vendedor_id: uuid.UUID, *, desde: date | None
) -> int:
    """Cuántas operaciones pendientes del vendedor frenan su corte o su carga."""
    return (
        await sesion.execute(
            text(
                """
                SELECT count(*) FROM sync_cuarentena c
                  JOIN dispositivos dis ON dis.id = c.dispositivo_id
                 WHERE dis.usuario_id = :v AND c.estado = 'pendiente'
                   AND c.tipo ~ :tipos
                   AND (CAST(:desde AS date) IS NULL
                        OR c.recibido_en >= CAST(:desde AS date))
                """
            ),
            {"v": vendedor_id, "tipos": _QUE_EXPLICA_UNA_DIFERENCIA, "desde": desde},
        )
    ).scalar_one()
