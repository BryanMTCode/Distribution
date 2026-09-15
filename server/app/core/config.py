"""Configuración del servidor. Todo por variables de entorno, nada hardcodeado."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DSD_", extra="ignore")

    entorno: str = "desarrollo"
    debug: bool = False

    # postgresql+psycopg://usuario:clave@host:5432/dsd
    database_url: str = "postgresql+psycopg://dsd:dsd@localhost:5432/dsd"
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # 32 bytes es el mínimo para HMAC-SHA256 (RFC 7518 §3.2). PyJWT avisa por
    # debajo de eso, y el aviso es correcto.
    jwt_secreto: str = Field(
        default="cambiar-en-produccion-con-openssl-rand-hex-32", min_length=32
    )
    jwt_algoritmo: str = "HS256"
    access_token_minutos: int = 30
    refresh_token_dias: int = 30

    # Días que un dispositivo puede operar sin sincronizar antes de exigir
    # login online. Se replica a la credencial local del teléfono.
    dias_max_offline: int = 7

    # Radio a partir del cual una venta se marca requiere_revision por estar
    # lejos del domicilio registrado del cliente. No la rechaza: la marca.
    geocerca_metros: int = 300

    @property
    def es_produccion(self) -> bool:
        return self.entorno == "produccion"

    @model_validator(mode="after")
    def _secreto_real_en_produccion(self) -> Config:
        if self.es_produccion and self.jwt_secreto.startswith("cambiar-en-produccion"):
            raise ValueError(
                "DSD_JWT_SECRETO sigue en el valor por defecto; genera uno con "
                "'openssl rand -hex 32'"
            )
        return self


@lru_cache
def obtener_config() -> Config:
    return Config()
