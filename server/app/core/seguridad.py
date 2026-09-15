"""Hash de contraseñas y emisión de tokens.

Los parámetros de Argon2id están fijados explícitamente porque **el mismo hash
se replica al dispositivo** para el login offline, y los bindings FFI de Dart no
comparten los valores por defecto de `argon2-cffi`. Si divergen, el vendedor no
puede entrar al empezar el día. Ver contracts/README.md §2.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from argon2.low_level import Type

from app.core.config import obtener_config


@dataclass(frozen=True)
class ParametrosArgon2:
    """Contrato con el cliente Dart. Cambiar un valor obliga a regenerar
    contracts/argon2_vectors.json y a cambiar el cliente EN EL MISMO COMMIT."""

    memoria_kib: int = 65536
    iteraciones: int = 3
    paralelismo: int = 4
    sal_bytes: int = 16
    hash_bytes: int = 32
    variante: Literal["argon2id"] = "argon2id"


PARAMETROS_ARGON2 = ParametrosArgon2()

_hasher = PasswordHasher(
    time_cost=PARAMETROS_ARGON2.iteraciones,
    memory_cost=PARAMETROS_ARGON2.memoria_kib,
    parallelism=PARAMETROS_ARGON2.paralelismo,
    hash_len=PARAMETROS_ARGON2.hash_bytes,
    salt_len=PARAMETROS_ARGON2.sal_bytes,
    type=Type.ID,
)


def hashear_password(password: str) -> str:
    """Devuelve la cadena PHC que se guarda en `usuarios.password_hash`."""
    return _hasher.hash(password)


def verificar_password(password: str, hash_phc: str) -> bool:
    try:
        return _hasher.verify(hash_phc, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def requiere_rehash(hash_phc: str) -> bool:
    """True si el hash se generó con parámetros viejos.

    Importante: al rehashear hay que re-sincronizar la credencial local del
    dispositivo, o el login offline dejará de funcionar.
    """
    try:
        return _hasher.check_needs_rehash(hash_phc)
    except InvalidHashError:
        return True


# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------

def _emitir(claims: dict[str, Any], expira: timedelta, tipo: str) -> str:
    cfg = obtener_config()
    ahora = datetime.now(UTC)
    cuerpo = {
        **claims,
        "tipo": tipo,
        "iat": int(ahora.timestamp()),
        "exp": int((ahora + expira).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(cuerpo, cfg.jwt_secreto, algorithm=cfg.jwt_algoritmo)


def emitir_access_token(
    usuario_id: uuid.UUID,
    rol: str,
    permisos: list[str],
    rutas: list[str],
    almacen_id: uuid.UUID | None,
    dispositivo_id: uuid.UUID | None,
) -> str:
    """El alcance viaja en el token; el *scope guard* del backend lo aplica.

    La UI oculta, el servidor prohíbe: estos claims no son una sugerencia para
    el cliente, son lo que el backend usa para filtrar.
    """
    cfg = obtener_config()
    return _emitir(
        {
            "sub": str(usuario_id),
            "rol": rol,
            "permisos": permisos,
            "rutas": rutas,
            "almacen_id": str(almacen_id) if almacen_id else None,
            "dispositivo_id": str(dispositivo_id) if dispositivo_id else None,
        },
        timedelta(minutes=cfg.access_token_minutos),
        "access",
    )


def emitir_refresh_token(usuario_id: uuid.UUID, dispositivo_id: uuid.UUID | None) -> str:
    cfg = obtener_config()
    return _emitir(
        {"sub": str(usuario_id), "dispositivo_id": str(dispositivo_id) if dispositivo_id else None},
        timedelta(days=cfg.refresh_token_dias),
        "refresh",
    )


class TokenInvalido(Exception):
    pass


def decodificar_token(token: str, tipo_esperado: str) -> dict[str, Any]:
    cfg = obtener_config()
    try:
        claims = jwt.decode(token, cfg.jwt_secreto, algorithms=[cfg.jwt_algoritmo])
    except jwt.ExpiredSignatureError as e:
        raise TokenInvalido("token expirado") from e
    except jwt.PyJWTError as e:
        raise TokenInvalido("token inválido") from e
    if claims.get("tipo") != tipo_esperado:
        raise TokenInvalido(f"se esperaba un token de tipo {tipo_esperado}")
    return claims
