"""Logs estructurados y el identificador de petición (Fase 9).

────────────────────────────────────────────────────────────────────────────
POR QUÉ JSON Y NO TEXTO
────────────────────────────────────────────────────────────────────────────
El sistema ya tiene logs; lo que no tiene es forma de CONTESTAR PREGUNTAS con
ellos. La pregunta real de un soporte de DSD no es "¿hubo errores?" sino:

    «El vendedor dice que la venta 000142 no llegó. ¿Qué pasó con ella?»

Con `logging` en texto eso son cuatro `grep` y adivinar cuál de las líneas
pertenece a la misma petición, porque ocho camiones sincronizan a la vez y sus
líneas vienen intercaladas. Con JSON y un `peticion_id` común a toda la
petición, es un filtro.

────────────────────────────────────────────────────────────────────────────
LO QUE NUNCA ENTRA EN UN LOG
────────────────────────────────────────────────────────────────────────────
Es la parte que importa más, y la que un `logger.info(payload)` descuidado
rompe en una línea:

· **El contenido de un sobre.** Un lote de sincronización trae nombres de
  clientes, importes y saldos. Un log con eso dentro es una copia de la cartera
  en texto plano, rotando en disco, que nadie cifra y que se manda por correo
  cuando hay que depurar algo.
· **Tokens, contraseñas y hashes.** El `Authorization` se redacta siempre.
· **Coordenadas.** Un log de lat/lng es un historial de dónde estuvo una
  persona. Si hace falta para depurar, está en la tabla del documento, con su
  control de acceso.

De lo que SÍ se registra —qué operación, de qué equipo, con qué resultado—
alcanza para contestar la pregunta de arriba sin tener el dato dentro.

────────────────────────────────────────────────────────────────────────────
EN DESARROLLO, TEXTO
────────────────────────────────────────────────────────────────────────────
JSON en una terminal es ilegible, y un formato que nadie lee es un formato que
se deja de mirar. En producción JSON (lo consume el agregador), en desarrollo
una línea por log con el `peticion_id` corto al frente.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Any

# El contexto de la petición en curso.
#
# `ContextVar` y no una variable global: con asyncio hay decenas de peticiones
# intercaladas en el mismo hilo, y una global haría que el log de una venta
# saliera con el `peticion_id` de otra — que es peor que no tenerlo, porque
# manda a investigar la petición equivocada.
# `default=None` y no `default={}`: un diccionario como valor por omisión de un
# ContextVar es UNO para todo el proceso, así que cualquier escritura descuidada
# sobre él se filtraría a todas las peticiones. `contexto()` devuelve uno vacío.
_peticion_actual: ContextVar[dict[str, Any] | None] = ContextVar(
    "peticion_actual", default=None
)


def contexto() -> dict[str, Any]:
    """El contexto de la petición en curso. Vacío fuera de una petición."""
    return _peticion_actual.get() or {}


def fijar_contexto(datos: dict[str, Any]) -> None:
    _peticion_actual.set(dict(datos))


def ampliar_contexto(**datos: Any) -> None:
    """Agrega campos al contexto actual. Lo usa la autenticación.

    El `peticion_id` se asigna antes de saber quién manda la petición —el
    middleware corre antes de resolver el token—, así que el usuario y el equipo
    se suman después. Sin esto, la línea del request no diría de quién era.
    """
    _peticion_actual.set({**contexto(), **datos})

# Campos que `logging` pone en cada registro y que no aportan nada en JSON.
_RUIDO = frozenset(
    {
        "args", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "module", "msecs", "message", "msg",
        "name", "pathname", "process", "processName", "relativeCreated",
        "stack_info", "thread", "threadName", "taskName",
    }
)

# Cabeceras y claves cuyo valor NO se escribe nunca, ni truncado. Un token
# truncado sigue siendo media llave, y además invita a creer que se puede
# publicar el log.
CLAVES_SECRETAS = frozenset(
    {
        "authorization", "cookie", "set-cookie", "password", "contrasena",
        "contraseña", "password_hash", "token", "access_token", "refresh_token",
        "refresh_token_hash", "jwt_secreto", "csrf", "payload", "datos",
        "llave", "secreto",
    }
)

REDACTADO = "«redactado»"


def redactar(datos: dict[str, Any]) -> dict[str, Any]:
    """Quita los valores secretos de un diccionario, un nivel de profundidad.

    Un nivel basta para lo que se registra aquí y evita una recursión sobre
    estructuras que podrían venir de fuera. Lo que no se puede redactar con
    seguridad —un payload completo— simplemente no se pasa al log.
    """
    return {
        clave: (REDACTADO if clave.lower() in CLAVES_SECRETAS else valor)
        for clave, valor in datos.items()
    }


class FormatoJson(logging.Formatter):
    """Una línea JSON por registro, con el contexto de la petición dentro."""

    def format(self, registro: logging.LogRecord) -> str:
        cuerpo: dict[str, Any] = {
            "ts": self.formatTime(registro, "%Y-%m-%dT%H:%M:%S%z"),
            "nivel": registro.levelname,
            "origen": registro.name,
            "mensaje": registro.getMessage(),
        }
        cuerpo.update(redactar(contexto()))

        # `logger.info("...", extra={...})` llega aquí como atributos sueltos.
        extras = {
            clave: valor
            for clave, valor in registro.__dict__.items()
            if clave not in _RUIDO and not clave.startswith("_")
        }
        cuerpo.update(redactar(extras))

        if registro.exc_info:
            # La traza va en una sola cadena: un arreglo de líneas obliga a
            # recomponerla para leerla, y se lee más veces que se consulta.
            cuerpo["excepcion"] = self.formatException(registro.exc_info)

        return json.dumps(cuerpo, ensure_ascii=False, default=str)


class FormatoLegible(logging.Formatter):
    """Para la terminal. El `peticion_id` corto al frente, para poder seguirlo."""

    def format(self, registro: logging.LogRecord) -> str:
        marca = contexto().get("peticion_id", "")
        prefijo = f"[{marca[:8]}] " if marca else ""
        base = (
            f"{self.formatTime(registro, '%H:%M:%S')} "
            f"{registro.levelname:<7} {prefijo}{registro.name}: "
            f"{registro.getMessage()}"
        )
        if registro.exc_info:
            base += "\n" + self.formatException(registro.exc_info)
        return base


def configurar(*, json_salida: bool, nivel: str = "INFO") -> None:
    """Deja un solo manejador en la raíz, con el formato elegido.

    Se reemplaza en vez de agregar: uvicorn instala el suyo, y sumar otro
    duplica cada línea — que en un incidente hace creer que algo pasó dos veces.
    """
    raiz = logging.getLogger()
    for manejador in list(raiz.handlers):
        raiz.removeHandler(manejador)

    manejador = logging.StreamHandler(sys.stdout)
    manejador.setFormatter(FormatoJson() if json_salida else FormatoLegible())
    raiz.addHandler(manejador)
    raiz.setLevel(nivel)

    # El access log de uvicorn duplica lo que ya registra el middleware, con
    # menos información y sin el `peticion_id`. Se apaga a propósito.
    logging.getLogger("uvicorn.access").disabled = True
    for ruidoso in ("uvicorn.error", "sqlalchemy.engine"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)


def nuevo_id_de_peticion() -> str:
    """Un identificador corto, suficiente para correlacionar un día de logs."""
    return uuid.uuid4().hex[:16]
