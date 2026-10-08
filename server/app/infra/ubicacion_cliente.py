"""La ubicación del cliente: con GPS o escrita a mano (ADR 0002 §84).

Lo usan los tres caminos —la operación `cliente.ubicar` del teléfono del
vendedor, el panel y la app de la oficina—, así que la regla de qué es una
coordenada válida vive aquí una sola vez.

────────────────────────────────────────────────────────────────────────────
QUÉ SE RECHAZA
────────────────────────────────────────────────────────────────────────────
· Lo que no es un número, o se sale del planeta (latitud ±90, longitud ±180).
· El (0, 0): es el mar frente a África, y es lo que deja un GPS que no leyó.
· Latitud y longitud al revés, cuando es evidente: en México la latitud anda
  entre 14 y 33 y la longitud entre −118 y −86. Una «latitud» de −106 no es un
  error de dedo que se pueda guardar: es la longitud en el campo equivocado.

Lo demás se guarda con siete decimales (~1 cm), la escala de la columna.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import text

ORIGENES = ("gps", "manual")
_SIETE = Decimal("0.0000001")


class UbicacionInvalida(Exception):
    """La coordenada no se puede guardar; el mensaje dice por qué."""


def leer_coordenadas(lat: str | None, lng: str | None) -> tuple[Decimal, Decimal]:
    """Dos textos a (lat, lng) con siete decimales, o `UbicacionInvalida`."""

    def numero(texto: str | None, nombre: str) -> Decimal:
        crudo = (texto or "").strip().replace(" ", "")
        if not crudo:
            raise UbicacionInvalida(f"Falta la {nombre}.")
        try:
            valor = Decimal(crudo)
        except InvalidOperation as e:
            raise UbicacionInvalida(f"«{texto}» no es una {nombre}.") from e
        if not valor.is_finite():
            raise UbicacionInvalida(f"«{texto}» no es una {nombre}.")
        return valor.quantize(_SIETE, rounding=ROUND_HALF_UP)

    la = numero(lat, "latitud")
    lo = numero(lng, "longitud")
    if not (-90 <= la <= 90):
        if -180 <= la <= 180 and -90 <= lo <= 90:
            raise UbicacionInvalida(
                "Parece que la latitud y la longitud están al revés: la latitud va "
                "entre −90 y 90 (en Mazatlán, 23.2…) y la longitud es la negativa "
                "(−106.4…)."
            )
        raise UbicacionInvalida(f"La latitud va entre −90 y 90, no {la}.")
    if not (-180 <= lo <= 180):
        raise UbicacionInvalida(f"La longitud va entre −180 y 180, no {lo}.")
    if la == 0 and lo == 0:
        raise UbicacionInvalida(
            "0, 0 es el mar frente a África: es lo que deja un GPS que no leyó."
        )
    if lo > 0 and -118 <= -lo <= -86 and 14 <= la <= 33:
        # En México la longitud es negativa. Un 106.4 sin el signo es el error
        # de dedo más común, y guardarlo pondría la tienda en China.
        raise UbicacionInvalida(
            f"La longitud en México es negativa: ¿quisiste decir −{lo}?"
        )
    return la, lo


async def fijar_ubicacion(
    sesion,
    cliente_id: uuid.UUID,
    *,
    lat: Decimal,
    lng: Decimal,
    origen: str,
    precision_m: Decimal | None = None,
    quien: uuid.UUID | None = None,
    dispositivo_id: uuid.UUID | None = None,
    momento: datetime | None = None,
) -> dict:
    """Guarda la ubicación y deja constancia de la anterior. No confirma.

    Devuelve el cliente como quedó. La actualización publica el delta del
    cliente (0010), así que los teléfonos de su ruta reciben la ubicación nueva
    —y con ella la geocerca de la venta— en su siguiente sincronización.
    """
    if origen not in ORIGENES:
        raise UbicacionInvalida(f"Origen de ubicación desconocido: {origen!r}.")
    antes = (
        await sesion.execute(
            text(
                "SELECT lat, lng, ubicacion_origen FROM clientes WHERE id = :c FOR UPDATE"
            ),
            {"c": cliente_id},
        )
    ).mappings().first()
    if antes is None:
        raise UbicacionInvalida("Ese cliente no existe.")

    ahora = momento or datetime.now(UTC)
    await sesion.execute(
        text(
            "UPDATE clientes "
            "   SET lat = :lat, lng = :lng, ubicacion_origen = :origen, "
            "       ubicacion_precision_m = :precision, ubicacion_capturada_en = :ahora, "
            "       actualizado_en = now() "
            " WHERE id = :c"
        ),
        {
            "c": cliente_id,
            "lat": lat,
            "lng": lng,
            "origen": origen,
            "precision": precision_m,
            "ahora": ahora,
        },
    )
    # La anterior queda escrita: una ubicación movida es la primera pregunta
    # cuando una venta sale «lejos del cliente».
    await sesion.execute(
        text(
            """
            INSERT INTO auditoria (entidad, entidad_id, accion, usuario_id, dispositivo_id,
                                   datos_antes, datos_despues)
            VALUES ('cliente', :c, 'ubicacion', :quien, :equipo,
                    CAST(:antes AS jsonb), CAST(:despues AS jsonb))
            """
        ),
        {
            "c": cliente_id,
            "quien": quien,
            "equipo": dispositivo_id,
            "antes": json.dumps(
                {
                    "lat": str(antes["lat"]) if antes["lat"] is not None else None,
                    "lng": str(antes["lng"]) if antes["lng"] is not None else None,
                    "origen": antes["ubicacion_origen"],
                }
            ),
            "despues": json.dumps({"lat": str(lat), "lng": str(lng), "origen": origen}),
        },
    )
    return {"lat": lat, "lng": lng, "origen": origen}
