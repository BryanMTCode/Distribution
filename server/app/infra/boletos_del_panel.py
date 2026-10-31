"""El boleto para abrir el dashboard desde la app de la oficina (ADR 0002 §194).

«Una versión de la app en que el administrador pueda hacer todos los
movimientos que hace en el dashboard, sin tener que actualizar la app a los
vendedores.» La app de la oficina abre el mismo dashboard dentro de ella. La app
ya tiene su sesión —un token—, y el dashboard la suya —una cookie HttpOnly, ver
`sesion_web.py`—, y las dos no se mezclan: el token no se le pasa a la página.

Lo que cruza es un **boleto**: la app lo pide con su token, y el dashboard lo
canjea por su sesión de siempre. Sirve una sola vez y durante un minuto, y en la
base va su hash. Si alguien lo viera en una URL (el Excel se baja en el
navegador del teléfono, que no tiene la cookie de la app), ya estaría usado.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

DURACION = timedelta(minutes=1)

# Lo que el boleto puede abrir: solo pantallas del dashboard. Un «siguiente»
# hacia otro sitio convertiría el canje en una redirección abierta.
INICIO = "/panel"


def _hash(boleto: str) -> str:
    return hashlib.sha256(boleto.encode()).hexdigest()


async def emitir(sesion, usuario_id: uuid.UUID) -> tuple[str, datetime]:
    """Un boleto nuevo para ese usuario. No hace commit."""
    boleto = secrets.token_urlsafe(32)
    expira = datetime.now(UTC) + DURACION
    # Los vencidos de ayer ya no sirven ni para revisar nada.
    await sesion.execute(
        text("DELETE FROM boletos_panel WHERE expira_en < now() - interval '1 day'")
    )
    await sesion.execute(
        text("INSERT INTO boletos_panel (hash, usuario_id, expira_en) VALUES (:h, :u, :e)"),
        {"h": _hash(boleto), "u": usuario_id, "e": expira},
    )
    return boleto, expira


async def canjear(sesion, boleto: str) -> uuid.UUID | None:
    """De quién es el boleto, si sirve; y desde ese momento ya no sirve. No hace
    commit. Lo marca y lo lee en una sola sentencia: dos canjes a la vez no
    pueden usar el mismo."""
    if not boleto:
        return None
    return (
        await sesion.execute(
            text(
                "UPDATE boletos_panel SET usado_en = now() "
                " WHERE hash = :h AND usado_en IS NULL AND expira_en > now() "
                "RETURNING usuario_id"
            ),
            {"h": _hash(boleto)},
        )
    ).scalar_one_or_none()


def destino_seguro(siguiente: str | None) -> str:
    """A dónde llevar después del canje: una pantalla del dashboard, o el
    Tablero."""
    if (
        not siguiente
        or not siguiente.startswith(INICIO)
        or siguiente.startswith("//")
        or "\\" in siguiente
        or any(ord(c) < 32 for c in siguiente)
    ):
        return INICIO
    # «/panelx» no es el dashboard; «/panel», «/panel/…» y «/panel?…» sí.
    resto = siguiente[len(INICIO):]
    if resto and resto[0] not in "/?#":
        return INICIO
    return siguiente
