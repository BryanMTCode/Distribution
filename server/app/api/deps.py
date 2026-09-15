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

from app.core.db import obtener_sesion
from app.core.seguridad import TokenInvalido, decodificar_token
from app.infra.models import Dispositivo, Usuario

_bearer = HTTPBearer(auto_error=False)


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
        return self.rol in ("admin", "gerente") or ruta_id in self.rutas


async def actor_actual(
    credenciales: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    sesion: Annotated[AsyncSession, Depends(obtener_sesion)],
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
        if dispositivo is None or dispositivo.estado != "activo":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "dispositivo no activo")

    return Actor(
        usuario_id=usuario_id,
        rol=claims["rol"],
        permisos=frozenset(claims.get("permisos") or []),
        rutas=frozenset(uuid.UUID(r) for r in (claims.get("rutas") or [])),
        almacen_id=uuid.UUID(claims["almacen_id"]) if claims.get("almacen_id") else None,
        dispositivo_id=uuid.UUID(dispositivo_id) if dispositivo_id else None,
    )


ActorDep = Annotated[Actor, Depends(actor_actual)]
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
