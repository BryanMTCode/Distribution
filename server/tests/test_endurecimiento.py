"""Fase 9: logs, métricas y el filtro de salida de Sentry.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
Las tres piezas de observabilidad comparten un riesgo que no se nota: **sacan
datos del sistema**. Un log rota en disco y se manda por correo para depurar;
una métrica se scrapea y se guarda en una serie temporal; un evento de Sentry
viaja a un servidor de un tercero. Si en alguno de los tres se cuela la
cartera, el dato salió y no vuelve.

Así que lo que se prueba no es que registren: es que **no registran lo que no
deben**, y que están apagados por omisión donde corresponde.
"""

from __future__ import annotations

import json
import logging
import uuid

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR

# Sin `pytestmark`: `asyncio_mode = "auto"` ya marca las async, y la marca de
# módulo haría que las síncronas de aquí —el formato del log y el filtro de
# Sentry, que no tocan la base— avisaran que están marcadas sin ser corrutinas.


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------
def _linea_json(registro: logging.LogRecord) -> dict:
    from app.core.registro import FormatoJson

    return json.loads(FormatoJson().format(registro))


def _registro(mensaje: str, **extra) -> logging.LogRecord:
    hecho = logging.LogRecord(
        name="dsd.prueba",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=mensaje,
        args=(),
        exc_info=None,
    )
    for clave, valor in extra.items():
        setattr(hecho, clave, valor)
    return hecho


def test_el_log_json_lleva_el_contexto_de_la_peticion():
    from app.core.registro import fijar_contexto

    fijar_contexto({"peticion_id": "abc123def456", "usuario_id": "u-1", "rol": "vendedor"})
    try:
        cuerpo = _linea_json(_registro("venta recibida"))
    finally:
        fijar_contexto({})

    assert cuerpo["peticion_id"] == "abc123def456"
    assert cuerpo["usuario_id"] == "u-1"
    assert cuerpo["mensaje"] == "venta recibida"
    assert cuerpo["nivel"] == "INFO"


@pytest.mark.parametrize(
    "clave",
    ["authorization", "cookie", "password", "password_hash", "token", "refresh_token",
     "payload", "datos", "csrf", "jwt_secreto"],
)
def test_el_log_nunca_escribe_un_valor_secreto(clave):
    """Es la prueba que justifica que exista `registro.py`.

    Un lote de sincronización trae nombres de clientes, importes y saldos. Un
    log con el payload dentro es una copia de la cartera en texto plano,
    rotando en disco, que nadie cifra y que se manda por correo cuando hay que
    depurar algo.
    """
    cuerpo = _linea_json(_registro("algo pasó", **{clave: "SECRETO-QUE-NO-DEBE-SALIR"}))
    assert cuerpo[clave] == "«redactado»"
    assert "SECRETO-QUE-NO-DEBE-SALIR" not in json.dumps(cuerpo)


def test_el_log_si_escribe_lo_util():
    """Redactar de más deja un log que no sirve para nada.

    Lo que tiene que quedar es con qué operación, de qué equipo y con qué
    resultado — suficiente para contestar «¿qué pasó con la venta 000142?» sin
    tener el dato del cliente dentro.
    """
    cuerpo = _linea_json(
        _registro(
            "lote procesado",
            folio="VEND01-000142",
            dispositivo_id="d-1",
            aceptadas=3,
            duracion_ms=84.2,
        )
    )
    assert cuerpo["folio"] == "VEND01-000142"
    assert cuerpo["aceptadas"] == 3
    assert cuerpo["duracion_ms"] == 84.2


def test_una_excepcion_deja_su_traza_en_una_sola_cadena():
    """Un arreglo de líneas obliga a recomponer la traza para leerla."""
    try:
        raise ValueError("algo explotó")
    except ValueError:
        import sys

        hecho = _registro("falló")
        hecho.exc_info = sys.exc_info()
        cuerpo = _linea_json(hecho)

    assert isinstance(cuerpo["excepcion"], str)
    assert "ValueError" in cuerpo["excepcion"]


def test_el_contexto_no_se_filtra_entre_peticiones():
    """`ContextVar` con `default=None`, no con un diccionario compartido.

    Un diccionario como valor por omisión es UNO para todo el proceso: una
    escritura descuidada se vería en todas las peticiones, y el log de una
    venta saldría con el `peticion_id` de otra. Mandaría a investigar la
    petición equivocada, que es peor que no tener identificador.
    """
    from app.core.registro import ampliar_contexto, contexto, fijar_contexto

    fijar_contexto({})
    assert contexto() == {}
    ampliar_contexto(peticion_id="uno")
    assert contexto()["peticion_id"] == "uno"
    fijar_contexto({})
    assert contexto() == {}


# ---------------------------------------------------------------------------
# El identificador de petición
# ---------------------------------------------------------------------------
async def test_cada_respuesta_trae_su_identificador(cliente, semilla):
    respuesta = await cliente.get("/salud")
    assert respuesta.headers.get("X-Peticion-Id")
    assert len(respuesta.headers["X-Peticion-Id"]) == 16


async def test_se_respeta_el_identificador_que_viene_de_fuera(cliente, semilla):
    """Caddy o el túnel pueden poner uno; conservarlo permite cruzar sus logs."""
    respuesta = await cliente.get(
        "/salud", headers={"X-Peticion-Id": "del-proxy-123"}
    )
    assert respuesta.headers["X-Peticion-Id"] == "del-proxy-123"


async def test_un_identificador_absurdamente_largo_se_recorta(cliente, semilla):
    """Una cabecera de 8 KB no debe acabar en cada línea del log."""
    respuesta = await cliente.get("/salud", headers={"X-Peticion-Id": "x" * 500})
    assert len(respuesta.headers["X-Peticion-Id"]) == 64


# ---------------------------------------------------------------------------
# Salud
# ---------------------------------------------------------------------------
async def test_salud_reporta_si_rls_esta_en_uso(cliente, semilla):
    """La diferencia entre «RLS escrito» y «RLS en uso» es una variable sin poner.

    No se nota mirando la base —las políticas están ahí, el rol dueño las
    salta— así que tiene que decirlo un endpoint, o nadie lo comprueba después
    de desplegar.
    """
    cuerpo = (await cliente.get("/salud")).json()
    assert "rls" in cuerpo
    assert isinstance(cuerpo["rls"], bool)
    assert cuerpo["version"]


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------
async def test_sin_token_configurado_el_endpoint_no_existe(cliente, semilla):
    """404 y no 401: un 401 confirma que hay métricas ahí.

    Y apagado por omisión significa que nadie lo publica sin querer al
    desplegar.
    """
    assert (await cliente.get("/metrics")).status_code == 404


async def test_con_token_mal_tambien_404(cliente, semilla, monkeypatch):
    """Si devolviera 401, el endpoint sería enumerable probando tokens."""
    from app.core.config import Config, obtener_config

    obtener_config.cache_clear()
    monkeypatch.setenv("DSD_METRICAS_TOKEN", "el-token-bueno")
    try:
        assert isinstance(Config(), Config)
        respuesta = await cliente.get(
            "/metrics", headers={"Authorization": "Bearer el-token-malo"}
        )
        assert respuesta.status_code == 404
    finally:
        obtener_config.cache_clear()


async def test_las_metricas_exponen_salud_y_no_dinero(cliente, semilla, monkeypatch):
    """La prueba central del endpoint.

    Es tentador exponer la venta del día —ya está calculada en `tablero_dia`— y
    sería un error: un endpoint de monitoreo termina scrapeado por un agente,
    guardado en una serie temporal y graficado en un tablero que nadie protege
    con el mismo cuidado que el panel. La facturación de la empresa no viaja
    por ahí.
    """
    from app.core.config import obtener_config

    # Una petición cualquiera ANTES de pedir las métricas.
    #
    # El histograma de latencia se llena al TERMINAR cada petición, así que en un
    # proceso donde `/metrics` es la primera llamada sale vacío. Sin esta línea
    # la prueba pasa o falla según qué corrió antes, que es la clase de prueba
    # que enseña a reintentar en vez de a arreglar.
    await cliente.get("/salud")

    obtener_config.cache_clear()
    monkeypatch.setenv("DSD_METRICAS_TOKEN", "token-de-prueba")
    try:
        respuesta = await cliente.get(
            "/metrics", headers={"Authorization": "Bearer token-de-prueba"}
        )
        assert respuesta.status_code == 200, respuesta.text
        cuerpo = respuesta.text
    finally:
        obtener_config.cache_clear()

    # Salud: sí.
    for metrica in (
        "dsd_jobs_pendientes",
        "dsd_jobs_fallidos",
        "dsd_cuarentena_pendiente",
        "dsd_equipos_rezagados",
        "dsd_cola_reportada",
        "dsd_tablero_edad_segundos",
        "dsd_peticiones_total",
        "dsd_latencia_segundos_bucket",
    ):
        assert metrica in cuerpo, f"falta {metrica}"

    # Dinero: no.
    #
    # Se revisan los NOMBRES de las métricas y no el cuerpo entero, y la
    # diferencia la encontró esta misma prueba fallando: las etiquetas de ruta
    # incluyen rutas del panel como `/panel/ventas`, así que un `"venta" not in
    # cuerpo` daba un falso positivo por el nombre de una PANTALLA. La regla que
    # importa es que ninguna métrica EXPONGA un importe, no que la palabra no
    # aparezca en ningún lado.
    nombres = {
        linea.split("{")[0].split(" ")[0]
        for linea in cuerpo.splitlines()
        if linea and not linea.startswith("#")
    }
    for prohibida in ("venta", "importe", "cartera", "cobrado", "saldo", "ticket"):
        culpables = [n for n in nombres if prohibida in n]
        assert not culpables, f"estas métricas exponen dinero: {culpables}"


async def test_la_antiguedad_nunca_calculada_es_menos_uno(
    cliente, semilla, monkeypatch
):
    """-1 y no 0: un cero haría ver el tablero como recién hecho cuando no existe."""
    from app.core.config import obtener_config

    obtener_config.cache_clear()
    monkeypatch.setenv("DSD_METRICAS_TOKEN", "token-de-prueba")
    try:
        cuerpo = (
            await cliente.get(
                "/metrics", headers={"Authorization": "Bearer token-de-prueba"}
            )
        ).text
    finally:
        obtener_config.cache_clear()

    renglon = next(
        linea for linea in cuerpo.splitlines()
        if linea.startswith("dsd_tablero_edad_segundos ")
    )
    assert renglon.split()[-1] == "-1"


async def test_la_ruta_se_etiqueta_con_la_plantilla_no_con_la_url(
    cliente, sesion, semilla, monkeypatch
):
    """Un UUID de cliente en una etiqueta de métrica es un dato de negocio
    metido en el sistema de monitoreo. Y explotaría la cardinalidad."""
    from app.core.config import obtener_config

    cliente_id = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, "
            "                      creado_en, actualizado_en) "
            "VALUES (:c, 'MET01', 'Para métricas', :r, now(), now())"
        ),
        {"c": cliente_id, "r": semilla["ruta"]},
    )
    await sesion.commit()

    entrada = await cliente.post(
        "/v1/auth/login", json={"codigo": "ADMIN01", "password": PASSWORD_VENDEDOR}
    )
    cab = {"Authorization": f"Bearer {entrada.json()['access_token']}"}
    await cliente.get(f"/v1/clientes/{cliente_id}/cartera", headers=cab)

    obtener_config.cache_clear()
    monkeypatch.setenv("DSD_METRICAS_TOKEN", "token-de-prueba")
    try:
        cuerpo = (
            await cliente.get(
                "/metrics", headers={"Authorization": "Bearer token-de-prueba"}
            )
        ).text
    finally:
        obtener_config.cache_clear()

    assert "{cliente_id}" in cuerpo
    assert str(cliente_id) not in cuerpo


# ---------------------------------------------------------------------------
# El filtro de salida de Sentry
# ---------------------------------------------------------------------------
def test_sentry_no_manda_el_cuerpo_de_la_peticion():
    """Es la última línea entre un error de producción y una fuga.

    No se puede verificar «mirando el panel de Sentry a ver qué llegó»: cuando
    se mira, el dato ya salió.
    """
    from app.core.observabilidad import limpiar_evento

    evento = limpiar_evento(
        {
            "request": {
                "url": "https://api/v1/sync/push",
                "data": {"sobres": [{"cliente": "Doña Mary", "total": "1250.00"}]},
                "cookies": {"dsd_panel": "token-de-sesion"},
                "headers": {
                    "Authorization": "Bearer el-token",
                    "User-Agent": "Dart/3.5",
                },
                "query_string": "fecha=2026-10-01",
            }
        }
    )
    peticion = evento["request"]
    assert "data" not in peticion
    assert "cookies" not in peticion
    assert peticion["headers"]["Authorization"] == "«redactado»"
    # Lo que no es secreto se conserva: sin el User-Agent no se puede saber qué
    # versión de la app falló.
    assert peticion["headers"]["User-Agent"] == "Dart/3.5"
    assert peticion["query_string"] == "«redactado»"
    assert "Doña Mary" not in json.dumps(evento)


def test_sentry_no_manda_las_variables_locales():
    """En `procesar_lote`, las locales incluyen el sobre completo."""
    from app.core.observabilidad import limpiar_evento

    evento = limpiar_evento(
        {
            "exception": {
                "values": [
                    {
                        "type": "ValueError",
                        "stacktrace": {
                            "frames": [
                                {
                                    "function": "procesar_lote",
                                    "vars": {
                                        "sobre": "{'cliente': 'Doña Mary'}",
                                        "token": "Bearer x",
                                    },
                                }
                            ]
                        },
                    }
                ]
            }
        }
    )
    marco = evento["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert "vars" not in marco
    # Y la traza sigue sirviendo: la función y el tipo de error se conservan.
    assert marco["function"] == "procesar_lote"
    assert evento["exception"]["values"][0]["type"] == "ValueError"
    assert "Doña Mary" not in json.dumps(evento)


def test_sentry_no_manda_el_nombre_de_la_persona():
    """Basta el id para correlacionar; el nombre no hace falta para depurar."""
    from app.core.observabilidad import limpiar_evento

    evento = limpiar_evento({"user": {"id": "u-1", "username": "Juan Pérez"}})
    assert "user" not in evento


def test_sentry_si_manda_el_identificador_de_peticion():
    """Es lo que permite ir del evento al log del servidor local, que sí tiene
    el detalle y su control de acceso."""
    from app.core.observabilidad import limpiar_evento
    from app.core.registro import fijar_contexto

    fijar_contexto({"peticion_id": "abc123"})
    try:
        evento = limpiar_evento({})
    finally:
        fijar_contexto({})
    assert evento["tags"]["peticion_id"] == "abc123"


def test_sentry_esta_apagado_sin_dsn():
    """Manda errores a un tercero: es una decisión explícita, no un default."""
    from app.core.config import Config
    from app.core.observabilidad import iniciar_sentry

    cfg = Config(jwt_secreto="x" * 40, sentry_dsn="")
    assert iniciar_sentry(cfg) is False
