"""Dependencias de FastAPI: autenticación y *scope guard*.

**La UI oculta, el servidor prohíbe.** Cambiar la interfaz por rol es
cosmética; el filtrado por ruta y almacén ocurre aquí y no es negociable.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import fijar_alcance, obtener_sesion
from app.core.registro import ampliar_contexto
from app.core.seguridad import TokenInvalido, decodificar_token
from app.infra.models import Dispositivo, Usuario, UsuarioRuta

_bearer = HTTPBearer(auto_error=False)

# Los roles que NO están limitados a sus rutas asignadas.
#
# ────────────────────────────────────────────────────────────────────────────
# POR QUÉ ESTA CONSTANTE EXISTE (y la encontró la Fase 9)
# ────────────────────────────────────────────────────────────────────────────
# Esta lista estaba escrita DOS VECES con contenidos distintos: el filtro de la
# lista de clientes incluía a 'supervisor' y `alcanza_ruta()` no. Así que un
# supervisor veía a todos los clientes en la lista y recibía un 403 al abrir la
# cartera de uno que no fuera de su ruta.
#
# Nadie lo había notado porque las dos cosas se ven correctas por separado. Lo
# delató escribir las políticas de PostgreSQL: al tener que declarar la lista
# una tercera vez, las dos primeras no coincidían.
#
# Se unifica incluyendo a 'supervisor', y no es una decisión de comodidad: ese
# rol ya tiene `ventas.ver_todas`, `clientes.administrar`, `clientes.fusionar`,
# `inventario.liquidar` y `sync.cuarentena` — puede cerrar la liquidación de
# cualquier ruta y leer el payload de cualquier equipo en cuarentena. Un rol con
# esas atribuciones ya es de oficina; el límite por ruta en `alcanza_ruta()` era
# el que estaba fuera de lugar.
#
# La misma lista está en `dsd_ve_todo()` (migración 0022) y hay una prueba que
# compara las dos: si se separan, la de PostgreSQL es la que manda.
ROLES_DE_OFICINA = frozenset({"admin", "gerente", "supervisor"})


@dataclass
class Actor:
    """Quién hace la petición y hasta dónde alcanza."""

    usuario_id: uuid.UUID
    rol: str
    permisos: frozenset[str] = field(default_factory=frozenset)
    rutas: frozenset[uuid.UUID] = field(default_factory=frozenset)
    almacen_id: uuid.UUID | None = None
    dispositivo_id: uuid.UUID | None = None

    def puede(self, permiso: str) -> bool:
        return self.rol == "admin" or permiso in self.permisos

    def exigir(self, permiso: str) -> None:
        if not self.puede(permiso):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"falta el permiso {permiso}")

    def alcanza_ruta(self, ruta_id: uuid.UUID) -> bool:
        return self.rol in ROLES_DE_OFICINA or ruta_id in self.rutas


# Los estados de dispositivo que pueden ENTREGAR lo que traen (Fase 9).
#
# `suspendido` existe para esto y solo para esto: un equipo al que se le ordenó
# el borrado —o que la oficina bloqueó— tiene que poder subir las ventas que
# lleva dentro antes de borrarlas. Si no pudiera, la regla «nunca se borra lo
# que no se ha entregado» sería imposible de cumplir y el borrado remoto
# costaría un día de ventas cada vez que se usa.
#
# No puede hacer pull: entrega y no recibe nada nuevo. Ver migración 0023.
ESTADOS_QUE_ENTREGAN = ("activo", "suspendido")


async def _resolver_actor(
    credenciales: HTTPAuthorizationCredentials | None,
    sesion: AsyncSession,
    *,
    estados_de_dispositivo: tuple[str, ...],
) -> Actor:
    if credenciales is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "falta el token")
    try:
        claims = decodificar_token(credenciales.credentials, "access")
    except TokenInvalido as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e

    usuario_id = uuid.UUID(claims["sub"])
    usuario = await sesion.get(Usuario, usuario_id)
    if usuario is None or not usuario.activo:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "usuario inactivo")

    # El token puede seguir vivo después de que el equipo fue reportado como
    # robado. La lista de revocación se consulta en cada petición: un access
    # token dura 30 minutos, y 30 minutos con la cartera completa es demasiado.
    dispositivo_id = claims.get("dispositivo_id")
    if dispositivo_id:
        dispositivo = await sesion.get(Dispositivo, uuid.UUID(dispositivo_id))
        if dispositivo is None or dispositivo.estado not in estados_de_dispositivo:
            # El mensaje lleva el estado para que el cliente pueda distinguir
            # «revocado» de «suspendido» sin adivinar: el primero no se arregla
            # de ninguna forma y el segundo se arregla entregando la cola.
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                f"dispositivo {dispositivo.estado if dispositivo else 'no registrado'}",
            )

    # El rol, los permisos, las rutas y el camión se leen de la base en CADA
    # petición, no del token, por la misma razón que la revocación de arriba. El
    # token es una foto de hasta 30 minutos, y con la foto pasaba esto: la oficina
    # le da la ruta R04 a Pedro, el pull de su teléfono sigue filtrando con las
    # rutas viejas, los deltas de R04 de esa media hora se quedan atrás del cursor
    # que avanza... y no le llegan nunca. Con el camión, igual: ventas descontadas
    # del camión que ya no maneja. Con un permiso quitado, media hora más de
    # usarlo. Son consultas por llave; la foto salía más cara. Es lo mismo que ya
    # hacía la sesión del panel (`sesion_web.py`): los dos guardias, una regla.
    permisos = frozenset(await cargar_permisos(sesion, usuario))
    rutas = frozenset(
        (
            await sesion.execute(
                select(UsuarioRuta.ruta_id).where(UsuarioRuta.usuario_id == usuario_id)
            )
        ).scalars()
    )

    actor = Actor(
        usuario_id=usuario_id,
        rol=usuario.rol_codigo,
        permisos=permisos,
        rutas=rutas,
        almacen_id=usuario.almacen_id,
        dispositivo_id=uuid.UUID(dispositivo_id) if dispositivo_id else None,
    )

    # El alcance baja a PostgreSQL (Fase 9). Es la SEGUNDA cerradura: el
    # filtrado por ruta de cada consulta sigue siendo la primera, y esto es lo
    # que convierte un olvido en una consulta vacía en vez de en una fuga.
    await fijar_alcance(
        sesion, rol=actor.rol, usuario_id=actor.usuario_id, rutas=actor.rutas
    )

    # Y al log, para que la línea de la petición diga de quién era. El nombre no
    # entra: basta el id para correlacionar (ver core/registro.py).
    ampliar_contexto(
        usuario_id=str(actor.usuario_id),
        rol=actor.rol,
        dispositivo_id=str(actor.dispositivo_id) if actor.dispositivo_id else None,
    )
    return actor


async def actor_actual(
    credenciales: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    sesion: Annotated[AsyncSession, Depends(obtener_sesion)],
) -> Actor:
    """El guardia normal: solo equipos ACTIVOS.

    Es el valor por omisión y lo usa todo el sistema. La excepción se declara
    endpoint por endpoint (`ActorQueEntrega`), nunca al revés: si el relajado
    fuera el default, cada endpoint nuevo nacería aceptando equipos suspendidos
    y nadie lo notaría.
    """
    return await _resolver_actor(
        credenciales, sesion, estados_de_dispositivo=("activo",)
    )


async def actor_que_entrega(
    credenciales: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    sesion: Annotated[AsyncSession, Depends(obtener_sesion)],
) -> Actor:
    """Para los DOS endpoints de la entrega final: el push y las órdenes.

    Acepta además un equipo `suspendido`, que es el estado en que queda uno al
    que se le ordenó el borrado. Sin esta excepción, ese equipo no podría subir
    las ventas que lleva dentro y el borrado remoto costaría un día de
    operación cada vez (ver migración 0023).

    Lo que NO acepta es `revocado`: ahí ya se entregó todo o se decidió que no
    hay nada que esperar.
    """
    return await _resolver_actor(
        credenciales, sesion, estados_de_dispositivo=ESTADOS_QUE_ENTREGAN
    )


ActorDep = Annotated[Actor, Depends(actor_actual)]
ActorQueEntregaDep = Annotated[Actor, Depends(actor_que_entrega)]
SesionDep = Annotated[AsyncSession, Depends(obtener_sesion)]


async def cargar_permisos(sesion: AsyncSession, usuario: Usuario) -> list[str]:
    """Permisos del rol, más las excepciones a nivel usuario."""
    from app.infra.models import RolPermiso, UsuarioPermiso

    del_rol = set(
        (
            await sesion.execute(
                select(RolPermiso.permiso_codigo).where(
                    RolPermiso.rol_codigo == usuario.rol_codigo
                )
            )
        ).scalars()
    )
    excepciones = (
        await sesion.execute(
            select(UsuarioPermiso.permiso_codigo, UsuarioPermiso.otorgado).where(
                UsuarioPermiso.usuario_id == usuario.id
            )
        )
    ).all()
    for codigo, otorgado in excepciones:
        del_rol.add(codigo) if otorgado else del_rol.discard(codigo)
    return sorted(del_rol)
