"""Sesión del panel: cookie, no token en el navegador.

────────────────────────────────────────────────────────────────────────────
POR QUÉ NO SE REUSA EL JWT DEL DISPOSITIVO
────────────────────────────────────────────────────────────────────────────
El teléfono guarda su token en el almacén cifrado del sistema. Un navegador no
tiene dónde guardarlo con esa garantía: `localStorage` lo deja legible para
cualquier script que llegue a la página, y ahí un XSS se convierte en robo de
sesión permanente.

Una cookie **HttpOnly** no la puede leer JavaScript: un XSS podría hacer
peticiones con ella, pero no llevársela. Y una sesión con fila en la base se
**revoca**: cuando alguien deja la empresa se marca `revocada_en` y su sesión
muere en la siguiente petición. Un JWT firmado sigue siendo válido hasta que
expira, haga lo que haga la oficina.

Se reusa la tabla `sesiones` que ya existe, con `dispositivo_id` en NULL: una
sesión de navegador no pertenece a ningún equipo registrado.

────────────────────────────────────────────────────────────────────────────
LAS TRES DEFENSAS DE LA COOKIE
────────────────────────────────────────────────────────────────────────────
1. **HttpOnly** — JavaScript no la lee.
2. **SameSite=Lax** — un POST desde otro sitio no la lleva. Es la defensa base
   contra CSRF y por sí sola cubre el caso clásico del formulario oculto.
3. **Secure** — solo viaja por TLS. El panel vive detrás de Caddy y del túnel de
   Cloudflare, así que en producción siempre hay TLS; en desarrollo sin HTTPS se
   apaga, y por eso depende de la configuración y no de una constante.

Encima va un **token CSRF** en cada formulario que cambia algo. `Lax` protege del
POST cruzado, pero no de un navegador viejo que lo ignore ni de un subdominio
comprometido. Son veinte líneas y cierran la puerta entera.

El valor de la cookie es aleatorio y en la base se guarda **su hash**: si alguien
se lleva un respaldo, no se lleva las sesiones vivas.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor, cargar_permisos
from app.core.config import obtener_config
from app.core.db import fijar_alcance, obtener_sesion
from app.core.registro import ampliar_contexto
from app.infra.models import Usuario

COOKIE = "dsd_panel"

# Ocho horas: un turno de oficina. Más largo deja sesiones vivas toda la noche en
# una computadora compartida; más corto obliga a volver a entrar a media captura.
DURACION = timedelta(hours=8)


def _hash(valor: str) -> str:
    """SHA-256 del token, que es lo que se guarda.

    No hace falta Argon2: el token tiene 256 bits de entropía real y no se puede
    adivinar por fuerza bruta. Argon2 existe para contraseñas que eligen personas,
    y aplicarlo en cada petición del panel costaría cientos de milisegundos por
    página sin ganar nada.
    """
    return hashlib.sha256(valor.encode()).hexdigest()


async def abrir_sesion(
    sesion: AsyncSession,
    usuario: Usuario,
    respuesta: Response,
    *,
    ip: str | None = None,
) -> None:
    """Crea la sesión y pone la cookie."""
    cfg = obtener_config()
    token = secrets.token_urlsafe(32)
    ahora = datetime.now(UTC)

    await sesion.execute(
        text(
            "INSERT INTO sesiones (id, usuario_id, dispositivo_id, refresh_token_hash, "
            "expira_en, ip, creada_en) "
            "VALUES (:id, :u, NULL, :h, :exp, :ip, :ahora)"
        ),
        {
            "id": uuid.uuid4(),
            "u": usuario.id,
            "h": _hash(token),
            "exp": ahora + DURACION,
            "ip": ip,
            "ahora": ahora,
        },
    )
    await sesion.commit()

    respuesta.set_cookie(
        COOKIE,
        token,
        max_age=int(DURACION.total_seconds()),
        httponly=True,
        samesite="lax",
        # En desarrollo no hay TLS; en producción el panel siempre está detrás de
        # Caddy. Que dependa de la configuración evita el error de dejarlo
        # apagado al desplegar.
        secure=not cfg.debug,
        path="/panel",
    )


async def cerrar_sesion(
    sesion: AsyncSession,
    peticion: Request,
    respuesta: Response,
) -> None:
    """Revoca la sesión en la base **y** borra la cookie.

    Las dos cosas: borrar solo la cookie dejaría la sesión viva para quien tenga
    el valor, y revocar solo en la base dejaría al navegador mandando una cookie
    muerta en cada petición.
    """
    token = peticion.cookies.get(COOKIE)
    if token:
        await sesion.execute(
            text(
                "UPDATE sesiones SET revocada_en = now() "
                "WHERE refresh_token_hash = :h AND revocada_en IS NULL"
            ),
            {"h": _hash(token)},
        )
        await sesion.commit()
    respuesta.delete_cookie(COOKIE, path="/panel")


class SinSesionWeb(Exception):
    """No hay sesión válida. El manejador redirige al login."""


async def actor_web_opcional(
    peticion: Request,
    sesion: Annotated[AsyncSession, Depends(obtener_sesion)],
) -> Actor | None:
    """El actor de la sesión, o `None` si no hay.

    Sirve para la propia pantalla de login, que no puede exigir sesión.
    """
    token = peticion.cookies.get(COOKIE)
    if not token:
        return None

    fila = (
        await sesion.execute(
            text(
                "SELECT usuario_id FROM sesiones "
                " WHERE refresh_token_hash = :h "
                "   AND revocada_en IS NULL "
                "   AND expira_en > now()"
            ),
            {"h": _hash(token)},
        )
    ).first()
    if fila is None:
        return None

    usuario = await sesion.get(Usuario, fila[0])
    if usuario is None or not usuario.activo:
        return None

    permisos = await cargar_permisos(sesion, usuario)
    rutas = frozenset(
        (
            await sesion.execute(
                text("SELECT ruta_id FROM usuarios_rutas WHERE usuario_id = :u"),
                {"u": usuario.id},
            )
        ).scalars()
    )
    actor = Actor(
        usuario_id=usuario.id,
        rol=usuario.rol_codigo,
        permisos=frozenset(permisos),
        rutas=rutas,
        almacen_id=usuario.almacen_id,
    )

    # El alcance baja a PostgreSQL igual que en la API (Fase 9), y aquí NO es
    # opcional: `obtener_sesion` deja la sesión en `anonimo`, que las políticas
    # de la 0022 rechazan. Sin esta línea todas las pantallas del panel saldrían
    # vacías — y el síntoma, «el panel ya no muestra nada», no apuntaría a RLS.
    await fijar_alcance(
        sesion, rol=actor.rol, usuario_id=actor.usuario_id, rutas=actor.rutas
    )
    ampliar_contexto(usuario_id=str(actor.usuario_id), rol=actor.rol)
    return actor


async def actor_web(
    actor: Annotated[Actor | None, Depends(actor_web_opcional)],
) -> Actor:
    """Exige sesión. Sin ella, se lanza para que el manejador redirija."""
    if actor is None:
        raise SinSesionWeb
    return actor


ActorWeb = Annotated[Actor, Depends(actor_web)]
ActorWebOpcional = Annotated[Actor | None, Depends(actor_web_opcional)]


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------
# El token se deriva de la cookie de sesión con HMAC del secreto del servidor. No
# hace falta guardarlo en ningún lado: se recalcula y se compara.
#
# Atado a la sesión a propósito: un token válido para cualquiera serviría a un
# atacante que lo obtenga una vez. Atado a la cookie, solo sirve con esa sesión, y
# la cookie es HttpOnly.


def token_csrf(peticion: Request) -> str:
    cookie = peticion.cookies.get(COOKIE, "")
    cfg = obtener_config()
    return hmac.new(
        cfg.jwt_secreto.encode(),
        f"csrf:{cookie}".encode(),
        hashlib.sha256,
    ).hexdigest()


def exigir_csrf(peticion: Request, enviado: str | None) -> None:
    """Compara en tiempo constante.

    `==` sobre cadenas sale en el primer byte distinto, y de esa diferencia de
    tiempo se puede deducir el token byte por byte.
    """
    esperado = token_csrf(peticion)
    if not enviado or not hmac.compare_digest(esperado, enviado):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "el formulario venció o no viene de este panel; vuelve a cargar la página",
        )
