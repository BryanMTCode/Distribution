"""Autenticación y credencial para el login offline."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import ActorDep, SesionDep, cargar_permisos
from app.core.config import obtener_config
from app.core.seguridad import (
    PARAMETROS_ARGON2,
    TokenInvalido,
    decodificar_token,
    emitir_access_token,
    emitir_refresh_token,
    verificar_password,
)
from app.domain.canonico import formatear_instante
from app.domain.identificadores import nuevo_id
from app.infra.models import Dispositivo, Sesion, Usuario, UsuarioRuta

router = APIRouter(prefix="/auth", tags=["auth"])


class PeticionLogin(BaseModel):
    codigo: str = Field(description="Código del usuario, p. ej. 'VEND01'")
    password: str
    dispositivo_id: uuid.UUID | None = Field(
        default=None, description="UUID generado en el dispositivo; requerido para vendedores"
    )


class CredencialLocal(BaseModel):
    """Lo que el teléfono guarda para poder autenticar sin señal.

    Es el mismo hash Argon2id de la base de datos. El dispositivo lo verifica
    localmente contra el PIN que teclea el vendedor. Ver contracts/README.md §2.
    """

    usuario_id: uuid.UUID
    codigo: str
    nombre: str
    rol: str
    password_hash: str
    permisos: list[str]
    almacen_id: uuid.UUID | None
    valida_hasta: str
    argon2: dict[str, int | str]


class RespuestaLogin(BaseModel):
    access_token: str
    refresh_token: str
    expira_en_seg: int
    credencial_local: CredencialLocal | None = None


async def _armar_credencial(sesion, usuario: Usuario, permisos: list[str]) -> CredencialLocal:
    cfg = obtener_config()
    # Después de esta fecha el login exige conexión: un equipo extraviado no
    # debe poder operar indefinidamente.
    dias = usuario.dias_max_offline or cfg.dias_max_offline
    hasta = datetime.now(UTC) + timedelta(days=dias)
    return CredencialLocal(
        usuario_id=usuario.id,
        codigo=usuario.codigo,
        nombre=usuario.nombre,
        rol=usuario.rol_codigo,
        password_hash=usuario.password_hash,
        permisos=permisos,
        almacen_id=usuario.almacen_id,
        valida_hasta=formatear_instante(hasta),
        argon2={
            "variante": PARAMETROS_ARGON2.variante,
            "memoria_kib": PARAMETROS_ARGON2.memoria_kib,
            "iteraciones": PARAMETROS_ARGON2.iteraciones,
            "paralelismo": PARAMETROS_ARGON2.paralelismo,
            "hash_bytes": PARAMETROS_ARGON2.hash_bytes,
        },
    )


@router.post("/login", response_model=RespuestaLogin)
async def login(peticion: PeticionLogin, request: Request, sesion: SesionDep) -> RespuestaLogin:
    cfg = obtener_config()
    usuario = (
        await sesion.execute(select(Usuario).where(Usuario.codigo == peticion.codigo))
    ).scalar_one_or_none()

    # Mismo mensaje y mismo costo aproximado para usuario inexistente y
    # contraseña incorrecta: no se regala la existencia de una cuenta.
    if usuario is None or not usuario.activo:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "credenciales inválidas")
    if not verificar_password(peticion.password, usuario.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "credenciales inválidas")

    dispositivo = None
    if peticion.dispositivo_id is not None:
        dispositivo = await sesion.get(Dispositivo, peticion.dispositivo_id)
        if dispositivo is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "dispositivo no registrado: regístralo antes de iniciar sesión",
            )
        if dispositivo.usuario_id != usuario.id:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "el dispositivo pertenece a otro usuario"
            )
        if dispositivo.estado != "activo":
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"dispositivo {dispositivo.estado}")

    if usuario.rol_codigo == "vendedor" and dispositivo is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "un vendedor debe iniciar sesión desde un dispositivo registrado",
        )

    permisos = await cargar_permisos(sesion, usuario)
    rutas = list(
        (
            await sesion.execute(
                select(UsuarioRuta.ruta_id).where(UsuarioRuta.usuario_id == usuario.id)
            )
        ).scalars()
    )

    access = emitir_access_token(
        usuario.id, usuario.rol_codigo, permisos,
        [str(r) for r in rutas], usuario.almacen_id,
        dispositivo.id if dispositivo else None,
    )
    refresh = emitir_refresh_token(usuario.id, dispositivo.id if dispositivo else None)

    ahora = datetime.now(UTC)
    sesion.add(
        Sesion(
            id=nuevo_id(),
            usuario_id=usuario.id,
            dispositivo_id=dispositivo.id if dispositivo else None,
            # Nunca se guarda el refresh token en claro.
            refresh_token_hash=hashlib.sha256(refresh.encode()).hexdigest(),
            expira_en=ahora + timedelta(days=cfg.refresh_token_dias),
            ip=request.client.host if request.client else None,
            creada_en=ahora,
        )
    )
    await sesion.commit()

    return RespuestaLogin(
        access_token=access,
        refresh_token=refresh,
        expira_en_seg=cfg.access_token_minutos * 60,
        # Solo los perfiles que operan offline necesitan credencial local.
        credencial_local=(
            await _armar_credencial(sesion, usuario, permisos) if dispositivo else None
        ),
    )


class PeticionRefresh(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=RespuestaLogin)
async def refrescar(peticion: PeticionRefresh, sesion: SesionDep) -> RespuestaLogin:
    cfg = obtener_config()
    try:
        claims = decodificar_token(peticion.refresh_token, "refresh")
    except TokenInvalido as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e

    huella = hashlib.sha256(peticion.refresh_token.encode()).hexdigest()
    registro = (
        await sesion.execute(
            select(Sesion).where(
                Sesion.refresh_token_hash == huella, Sesion.revocada_en.is_(None)
            )
        )
    ).scalar_one_or_none()
    if registro is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sesión revocada")

    usuario = await sesion.get(Usuario, uuid.UUID(claims["sub"]))
    if usuario is None or not usuario.activo:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "usuario inactivo")

    dispositivo_id = registro.dispositivo_id
    if dispositivo_id is not None:
        dispositivo = await sesion.get(Dispositivo, dispositivo_id)
        if dispositivo is None or dispositivo.estado != "activo":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "dispositivo no activo")

    permisos = await cargar_permisos(sesion, usuario)
    rutas = list(
        (
            await sesion.execute(
                select(UsuarioRuta.ruta_id).where(UsuarioRuta.usuario_id == usuario.id)
            )
        ).scalars()
    )
    access = emitir_access_token(
        usuario.id, usuario.rol_codigo, permisos,
        [str(r) for r in rutas], usuario.almacen_id, dispositivo_id,
    )
    return RespuestaLogin(
        access_token=access,
        refresh_token=peticion.refresh_token,
        expira_en_seg=cfg.access_token_minutos * 60,
    )


class Yo(BaseModel):
    usuario_id: uuid.UUID
    rol: str
    permisos: list[str]
    rutas: list[uuid.UUID]
    almacen_id: uuid.UUID | None
    dispositivo_id: uuid.UUID | None


@router.get("/yo", response_model=Yo)
async def yo(actor: ActorDep) -> Yo:
    return Yo(
        usuario_id=actor.usuario_id,
        rol=actor.rol,
        permisos=sorted(actor.permisos),
        rutas=sorted(actor.rutas),
        almacen_id=actor.almacen_id,
        dispositivo_id=actor.dispositivo_id,
    )
