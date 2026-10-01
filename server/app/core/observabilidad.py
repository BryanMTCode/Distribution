"""Sentry, opcional y con filtro de salida (Fase 9).

────────────────────────────────────────────────────────────────────────────
POR QUÉ OPCIONAL Y APAGADO POR OMISIÓN
────────────────────────────────────────────────────────────────────────────
Sentry manda los errores **a un servidor de un tercero**. En un sistema que
mueve la cartera de clientes de un negocio real, eso es una decisión que se
toma a propósito, no un valor por omisión que viene activado.

Sin `DSD_SENTRY_DSN`, este módulo no hace nada y la dependencia ni se importa:
el paquete vive en el extra `observabilidad` de `pyproject.toml`, así que una
instalación normal no lo trae.

────────────────────────────────────────────────────────────────────────────
EL FILTRO DE SALIDA NO ES OPCIONAL
────────────────────────────────────────────────────────────────────────────
Aquí está la razón principal de que este módulo exista en vez de tres líneas en
`main.py`. Sentry, por omisión, adjunta a cada error:

· el **cuerpo de la petición** —que en `/v1/sync/push` es la cartera entera—;
· las **cabeceras**, con el `Authorization` dentro;
· las **variables locales** de cada marco de la traza, que en `procesar_lote`
  incluyen el sobre completo.

Un error en producción se convertiría en una copia de datos de clientes en un
servicio externo. Por eso:

· `send_default_pii=False` y `request_bodies="never"`;
· `with_locals=False` en la integración de errores;
· y un `before_send` propio que redacta cabeceras y recorta lo que quede, porque
  las dos opciones de arriba son de Sentry y pueden cambiar de nombre o de
  comportamiento entre versiones. El filtro propio es el que no depende de eso.

Lo que sí viaja es lo útil: el tipo de excepción, la traza sin valores, la ruta
(como plantilla) y el `peticion_id`, que es lo que permite ir al log del
servidor local y ahí sí ver el detalle, con su control de acceso.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import Config
from app.core.registro import CLAVES_SECRETAS, REDACTADO, contexto

log = logging.getLogger("dsd.observabilidad")

__all__ = ["iniciar_sentry", "limpiar_evento"]


def limpiar_evento(evento: dict[str, Any], _pista: Any = None) -> dict[str, Any]:
    """`before_send`: quita de un evento lo que no debe salir de la oficina.

    Se escribe como función pura y se prueba por separado: es la última línea
    entre un error de producción y una fuga, y no se puede verificar «mirando el
    panel de Sentry a ver qué llegó».
    """
    peticion = evento.get("request")
    if isinstance(peticion, dict):
        # El cuerpo, fuera entero. No hay forma segura de redactar un payload de
        # sincronización: cada campo es un dato de un cliente.
        peticion.pop("data", None)
        peticion.pop("cookies", None)
        cabeceras = peticion.get("headers")
        if isinstance(cabeceras, dict):
            peticion["headers"] = {
                nombre: (REDACTADO if nombre.lower() in CLAVES_SECRETAS else valor)
                for nombre, valor in cabeceras.items()
            }
        # La query string puede traer una fecha o un cursor; también puede traer
        # un token si alguien lo pegó en una URL. Se redacta igual.
        if "query_string" in peticion:
            peticion["query_string"] = REDACTADO

    # Variables locales de cada marco: ahí vive el sobre.
    for excepcion in (evento.get("exception") or {}).get("values") or []:
        for marco in (excepcion.get("stacktrace") or {}).get("frames") or []:
            marco.pop("vars", None)

    # El contexto propio sí se manda, ya redactado por `registro.redactar`.
    etiquetas = evento.setdefault("tags", {})
    if (identificador := contexto().get("peticion_id")) is not None:
        etiquetas["peticion_id"] = identificador

    # Nunca se manda el usuario con nombre: basta su id para correlacionar, y el
    # nombre de una persona en un servicio externo no hace falta para depurar.
    evento.pop("user", None)
    return evento


def iniciar_sentry(cfg: Config) -> bool:
    """Arranca Sentry si hay DSN. Devuelve si quedó activo.

    No revienta si falta el paquete: el extra no está instalado en la imagen
    normal, y que la API no arranque por eso sería cambiar un servicio de
    diagnóstico por una caída.
    """
    if not cfg.sentry_dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        log.warning(
            "DSD_SENTRY_DSN está puesto pero sentry-sdk no está instalado; "
            "instala el extra: uv pip install -e '.[observabilidad]'"
        )
        return False

    sentry_sdk.init(
        dsn=cfg.sentry_dsn,
        environment=cfg.entorno,
        release=cfg.version,
        # Muestreo de trazas en cero: el valor de Sentry aquí son los ERRORES.
        # Las trazas de rendimiento ya las da `/metrics`, sin salir de la red de
        # la oficina y sin costo por volumen.
        traces_sample_rate=0.0,
        send_default_pii=False,
        max_request_body_size="never",
        include_local_variables=False,
        before_send=limpiar_evento,
        integrations=[
            # Los WARNING se registran como migas de pan y solo los ERROR crean
            # un evento. Al revés, un día con muchas ventas marcadas para
            # revisión —que son WARNING normales— abriría cientos de incidentes.
            LoggingIntegration(level=logging.WARNING, event_level=logging.ERROR),
        ],
    )
    log.info("Sentry activo (entorno %s)", cfg.entorno)
    return True
