"""Métricas operativas en formato Prometheus (Fase 9).

────────────────────────────────────────────────────────────────────────────
POR QUÉ A MANO Y SIN DEPENDENCIA
────────────────────────────────────────────────────────────────────────────
El formato de texto de Prometheus son tres reglas y un salto de línea. Las
métricas que este sistema necesita son nueve, y seis de ellas son consultas a
PostgreSQL que una biblioteca de instrumentación no sabría hacer sola. Añadir
`prometheus-client` traería un registro global, un recolector de multiproceso y
una dependencia más que actualizar, para ahorrar cuarenta líneas.

────────────────────────────────────────────────────────────────────────────
LO QUE NO SE EXPONE AQUÍ, Y POR QUÉ
────────────────────────────────────────────────────────────────────────────
**Nada de dinero.** Ni la venta del día, ni la cartera, ni el ticket promedio.
Es tentador —ya están calculadas en `tablero_dia`— y sería un error: un endpoint
de monitoreo termina scrapeado por un agente, guardado en una serie temporal y
graficado en un tablero que nadie protege con el mismo cuidado que el panel. La
facturación diaria de la empresa no viaja por ahí.

Lo que sí se expone es SALUD: profundidad de colas, equipos rezagados,
operaciones en cuarentena, latencia y errores. Son los números que contestan
«¿está el sistema funcionando?», que es la pregunta que hace un monitor.

────────────────────────────────────────────────────────────────────────────
APAGADO POR OMISIÓN
────────────────────────────────────────────────────────────────────────────
Sin `DSD_METRICAS_TOKEN`, el endpoint **no existe** (404, no 401). Un 401
confirma que hay algo ahí; un 404 no dice nada. Y apagado por omisión significa
que nadie lo publica sin querer al desplegar.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Cortes del histograma de latencia, en segundos.
#
# Elegidos por lo que significan aquí y no por costumbre: 100 ms es una pantalla
# del panel que se siente instantánea, 1 s es el techo de un pull de catálogo en
# la calle, y 5 s es donde un lote grande empieza a parecer colgado desde el
# teléfono. Los cortes de más arriba existen para ver la cola de lo lento.
CORTES_SEGUNDOS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


@dataclass
class Registro:
    """Contadores del proceso. Se reinician al reiniciar la API, a propósito.

    Un contador de Prometheus que persiste entre reinicios necesita
    almacenamiento y un cuidado que no vale aquí: el agente scrapea cada quince
    segundos y `rate()` maneja el reinicio solo.
    """

    peticiones: Counter[tuple[str, str, int]] = field(default_factory=Counter)
    latencia: Counter[tuple[str, int]] = field(default_factory=Counter)
    latencia_suma: Counter[str] = field(default_factory=Counter)
    errores_no_manejados: Counter[str] = field(default_factory=Counter)
    arranque: float = field(default_factory=time.monotonic)

    # Las peticiones llegan concurrentes; `Counter` no es atómico.
    _candado: threading.Lock = field(default_factory=threading.Lock)

    def anotar(self, metodo: str, ruta: str, estado: int, segundos: float) -> None:
        with self._candado:
            self.peticiones[(metodo, ruta, estado)] += 1
            self.latencia_suma[ruta] += segundos
            # Acumulativo, como lo quiere Prometheus: cada corte cuenta todo lo
            # que cayó por debajo de él.
            for indice, corte in enumerate(CORTES_SEGUNDOS):
                if segundos <= corte:
                    self.latencia[(ruta, indice)] += 1
            self.latencia[(ruta, len(CORTES_SEGUNDOS))] += 1   # +Inf

    def anotar_error(self, ruta: str) -> None:
        with self._candado:
            self.errores_no_manejados[ruta] += 1


registro = Registro()


def _etiqueta(valor: str) -> str:
    """Escapa una etiqueta. Una ruta con una comilla rompería el formato."""
    return valor.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _renglon(nombre: str, valor: float | int, **etiquetas: str) -> str:
    if etiquetas:
        pares = ",".join(f'{k}="{_etiqueta(v)}"' for k, v in etiquetas.items())
        return f"{nombre}{{{pares}}} {valor}"
    return f"{nombre} {valor}"


async def _gauges_de_la_base(sesion: AsyncSession) -> list[str]:
    """Los números que solo PostgreSQL sabe.

    Cada uno contesta una pregunta de operación concreta:

    · `dsd_jobs_pendientes` creciendo = el worker está caído o atorado. Es el
      que hay que vigilar: si crece, el tablero se congela y la analítica deja
      de refrescarse, sin que nada más falle.
    · `dsd_jobs_fallidos` > 0 = algo se reintentó hasta rendirse.
    · `dsd_cuarentena_pendiente` = dinero que ocurrió en la calle y no está en
      ninguna cifra hasta que alguien lo revise.
    · `dsd_equipos_rezagados` = teléfonos que no han subido hoy. Cada uno vuelve
      las cifras del día un piso y no un total (§0.3).
    · `dsd_cola_reportada` = sobres que los equipos dijeron tener pendientes.
    """
    filas = (
        await sesion.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM jobs WHERE estado = 'pendiente')     AS jobs_pendientes,
                  (SELECT count(*) FROM jobs WHERE estado = 'ejecutando')    AS jobs_ejecutando,
                  (SELECT count(*) FROM jobs WHERE estado = 'fallido')       AS jobs_fallidos,
                  (SELECT count(*) FROM sync_cuarentena
                    WHERE estado = 'pendiente')                              AS cuarentena,
                  (SELECT count(*) FROM dispositivos
                    WHERE estado = 'activo')                                 AS equipos_activos,
                  (SELECT count(*) FROM dispositivos
                    WHERE estado = 'activo'
                      AND (ultima_sync_push_en IS NULL
                           OR ultima_sync_push_en < CURRENT_DATE))           AS equipos_rezagados,
                  (SELECT COALESCE(sum(cola_pendiente), 0) FROM dispositivos
                    WHERE estado = 'activo'
                      AND cola_reportada_en >= CURRENT_DATE)                 AS cola_reportada,
                  (SELECT COALESCE(
                            EXTRACT(EPOCH FROM (now() - max(calculado_en))), -1)
                     FROM tablero_refrescos)                                 AS tablero_edad_seg,
                  (SELECT COALESCE(
                            EXTRACT(EPOCH FROM (now() - min(refrescado_en))), -1)
                     FROM analitica_refrescos)                               AS analitica_edad_seg
                """
            )
        )
    ).mappings().one()

    lineas = [
        "# HELP dsd_jobs_pendientes Trabajos en la cola esperando a un worker.",
        "# TYPE dsd_jobs_pendientes gauge",
        _renglon("dsd_jobs_pendientes", filas["jobs_pendientes"]),
        "# HELP dsd_jobs_ejecutando Trabajos tomados por un worker ahora mismo.",
        "# TYPE dsd_jobs_ejecutando gauge",
        _renglon("dsd_jobs_ejecutando", filas["jobs_ejecutando"]),
        "# HELP dsd_jobs_fallidos Trabajos que agotaron sus reintentos.",
        "# TYPE dsd_jobs_fallidos gauge",
        _renglon("dsd_jobs_fallidos", filas["jobs_fallidos"]),
        "# HELP dsd_cuarentena_pendiente Operaciones rechazadas esperando revisión humana.",
        "# TYPE dsd_cuarentena_pendiente gauge",
        _renglon("dsd_cuarentena_pendiente", filas["cuarentena"]),
        "# HELP dsd_equipos_activos Dispositivos registrados y no revocados.",
        "# TYPE dsd_equipos_activos gauge",
        _renglon("dsd_equipos_activos", filas["equipos_activos"]),
        "# HELP dsd_equipos_rezagados Equipos activos que no han hecho push hoy.",
        "# TYPE dsd_equipos_rezagados gauge",
        _renglon("dsd_equipos_rezagados", filas["equipos_rezagados"]),
        "# HELP dsd_cola_reportada Sobres que los equipos reportaron tener pendientes.",
        "# TYPE dsd_cola_reportada gauge",
        _renglon("dsd_cola_reportada", filas["cola_reportada"]),
        # -1 significa «nunca se ha calculado», que no es lo mismo que «viejo».
        # Un 0 ahí haría ver el tablero como recién hecho cuando no existe.
        "# HELP dsd_tablero_edad_segundos Antigüedad del tablero; -1 si nunca se calculó.",
        "# TYPE dsd_tablero_edad_segundos gauge",
        _renglon("dsd_tablero_edad_segundos", int(filas["tablero_edad_seg"])),
        "# HELP dsd_analitica_edad_segundos Antigüedad de la vista más vieja; -1 si ninguna.",
        "# TYPE dsd_analitica_edad_segundos gauge",
        _renglon("dsd_analitica_edad_segundos", int(filas["analitica_edad_seg"])),
    ]
    return lineas


async def exponer(sesion: AsyncSession) -> str:
    """El cuerpo completo del endpoint."""
    with registro._candado:  # noqa: SLF001 — es su propio módulo
        peticiones = dict(registro.peticiones)
        latencia = dict(registro.latencia)
        suma = dict(registro.latencia_suma)
        errores = dict(registro.errores_no_manejados)
        arriba = time.monotonic() - registro.arranque

    lineas = [
        "# HELP dsd_proceso_segundos Segundos desde que arrancó este proceso de la API.",
        "# TYPE dsd_proceso_segundos gauge",
        _renglon("dsd_proceso_segundos", int(arriba)),
        "# HELP dsd_peticiones_total Peticiones HTTP atendidas.",
        "# TYPE dsd_peticiones_total counter",
    ]
    for (metodo, ruta, estado), cuantas in sorted(peticiones.items()):
        lineas.append(
            _renglon(
                "dsd_peticiones_total", cuantas,
                metodo=metodo, ruta=ruta, estado=str(estado),
            )
        )

    lineas += [
        "# HELP dsd_errores_no_manejados_total Excepciones que llegaron al middleware.",
        "# TYPE dsd_errores_no_manejados_total counter",
    ]
    for ruta, cuantas in sorted(errores.items()):
        lineas.append(_renglon("dsd_errores_no_manejados_total", cuantas, ruta=ruta))

    lineas += [
        "# HELP dsd_latencia_segundos Latencia de las peticiones por ruta.",
        "# TYPE dsd_latencia_segundos histogram",
    ]
    rutas = sorted({ruta for ruta, _ in latencia})
    for ruta in rutas:
        for indice, corte in enumerate(CORTES_SEGUNDOS):
            lineas.append(
                _renglon(
                    "dsd_latencia_segundos_bucket",
                    latencia.get((ruta, indice), 0),
                    ruta=ruta, le=str(corte),
                )
            )
        total = latencia.get((ruta, len(CORTES_SEGUNDOS)), 0)
        lineas.append(
            _renglon("dsd_latencia_segundos_bucket", total, ruta=ruta, le="+Inf")
        )
        lineas.append(_renglon("dsd_latencia_segundos_count", total, ruta=ruta))
        lineas.append(
            _renglon("dsd_latencia_segundos_sum", round(suma.get(ruta, 0.0), 6), ruta=ruta)
        )

    lineas += await _gauges_de_la_base(sesion)
    return "\n".join(lineas) + "\n"
