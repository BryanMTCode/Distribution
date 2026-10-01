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

    # -----------------------------------------------------------------------
    # Observabilidad (Fase 9)
    # -----------------------------------------------------------------------
    # Versión que se reporta en /salud y en los eventos de Sentry. No es la del
    # paquete: en un despliegue por `git pull` lo que importa es el commit, y
    # eso lo inyecta el despliegue.
    version: str = "0.1.0"

    # Logs en JSON. Por omisión sigue al entorno: en producción los consume un
    # agregador y en desarrollo los lee una persona en una terminal.
    #
    # `None` significa «decide por el entorno». Un booleano con default `False`
    # habría dejado producción en texto hasta que alguien se acordara.
    logs_json: bool | None = None
    log_nivel: str = "INFO"

    # Sentry: APAGADO sin DSN, y el paquete vive en un extra. Manda errores a un
    # tercero, así que es una decisión explícita y no un valor por omisión.
    sentry_dsn: str = ""

    # Token del endpoint /metrics. Vacío = el endpoint NO EXISTE (404).
    #
    # Un 404 y no un 401: un 401 confirma que hay algo ahí. Y apagado por
    # omisión significa que nadie lo publica sin querer al desplegar.
    metricas_token: str = ""

    # -----------------------------------------------------------------------
    # Endurecimiento de la base (Fase 9)
    # -----------------------------------------------------------------------
    # DSN del rol restringido con el que la API habla con PostgreSQL. Ese rol NO
    # tiene BYPASSRLS, así que las políticas de seguridad por renglón se aplican
    # de verdad; los workers, el CLI y Alembic siguen con el rol dueño.
    #
    # Vacío = la API usa `database_url` y RLS queda SIN EFECTO. Se permite en
    # desarrollo y se RECHAZA en producción (ver el validador de abajo): un
    # sistema que cree tener RLS y no lo tiene es peor que uno que sabe que no.
    database_url_api: str = ""

    @property
    def es_produccion(self) -> bool:
        return self.entorno == "produccion"

    @property
    def logs_en_json(self) -> bool:
        return self.es_produccion if self.logs_json is None else self.logs_json

    @property
    def url_de_la_api(self) -> str:
        """La URL con la que la API abre su pool. El rol restringido si lo hay."""
        return self.database_url_api or self.database_url

    @property
    def rls_activa(self) -> bool:
        """Si la API está hablando con el rol restringido.

        Se reporta en `/salud` a propósito: la diferencia entre «RLS puesto» y
        «RLS puesto y en uso» es un `.env` sin una línea, y no se nota hasta que
        alguien la busca.
        """
        return bool(self.database_url_api)

    @model_validator(mode="after")
    def _secreto_real_en_produccion(self) -> Config:
        if self.es_produccion and self.jwt_secreto.startswith("cambiar-en-produccion"):
            raise ValueError(
                "DSD_JWT_SECRETO sigue en el valor por defecto; genera uno con "
                "'openssl rand -hex 32'"
            )
        return self

    @model_validator(mode="after")
    def _rls_obligatoria_en_produccion(self) -> Config:
        """En producción, la API no arranca sin su rol restringido.

        Es el mismo criterio que el secreto JWT: lo que protege de verdad no
        puede quedar dependiendo de que alguien se acuerde de una variable. Y
        falla al ARRANCAR, no en la primera consulta: un sistema que cree tener
        RLS y no lo tiene es peor que uno que sabe que no.
        """
        if self.es_produccion and not self.database_url_api:
            raise ValueError(
                "DSD_DATABASE_URL_API no está definida. En producción la API "
                "tiene que conectarse con el rol restringido para que las "
                "políticas por renglón se apliquen; créalo con "
                "db/ops/rol_api.sql"
            )
        return self


@lru_cache
def obtener_config() -> Config:
    return Config()
