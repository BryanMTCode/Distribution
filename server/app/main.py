"""Ensamblado de la aplicación. Aquí no vive lógica de negocio."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse

from app.api.admin import cargas as panel_cargas
from app.api.admin import clientes as panel_clientes
from app.api.admin import cobranza as panel_cobranza
from app.api.admin import compras as panel_compras
from app.api.admin import cuenta_vendedores as panel_cuenta_vendedores
from app.api.admin import desempeno as panel_desempeno
from app.api.admin import efectividad as panel_efectividad
from app.api.admin import entradas as panel_entradas
from app.api.admin import equipo as panel_equipo
from app.api.admin import equipo_fichas as panel_equipo_fichas
from app.api.admin import equipos as panel_equipos
from app.api.admin import inventario as panel_inventario
from app.api.admin import liquidaciones as panel_liquidaciones
from app.api.admin import motivos as panel_motivos
from app.api.admin import objetivos as panel_objetivos
from app.api.admin import panel
from app.api.admin import piloto as panel_piloto
from app.api.admin import plan_visita as panel_plan_visita
from app.api.admin import productos as panel_productos
from app.api.admin import salidas as panel_salidas
from app.api.admin import ventas as panel_ventas
from app.api.admin.sesion_web import SinSesionWeb
from app.api.middleware import Observabilidad
from app.api.v1 import auth, catalogo, clientes, dispositivos, salud, sync, tablero
from app.core.config import obtener_config
from app.core.db import motor, motor_api
from app.core.observabilidad import iniciar_sentry
from app.core.registro import configurar as configurar_logs


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI):
    """Arranque y apagado.

    Aquí —y no en `crear_app`— se configuran los logs y Sentry, porque los dos
    tocan estado GLOBAL del proceso. `crear_app` se llama una vez por prueba, y
    reemplazar el logging del proceso en cada una se llevaría por delante la
    captura de pytest. El ciclo de vida solo corre cuando algo sirve la app de
    verdad (uvicorn), que es justo cuando esa configuración hace falta.
    """
    cfg = obtener_config()
    configurar_logs(json_salida=cfg.logs_en_json, nivel=cfg.log_nivel)
    iniciar_sentry(cfg)

    log = logging.getLogger("dsd.arranque")
    log.info(
        "API lista",
        extra={
            "entorno": cfg.entorno,
            "version": cfg.version,
            "rls": cfg.rls_activa,
            "metricas": bool(cfg.metricas_token),
        },
    )
    if not cfg.rls_activa:
        # Un aviso y no un error: en desarrollo se trabaja sin el rol
        # restringido a propósito. En producción la configuración ni arranca
        # sin él, así que este camino no existe allá.
        log.warning(
            "sin DSD_DATABASE_URL_API: las políticas por renglón NO se están "
            "aplicando (ver db/ops/rol_api.sql)"
        )
    yield
    await motor.dispose()
    if motor_api is not motor:
        await motor_api.dispose()


def crear_app() -> FastAPI:
    cfg = obtener_config()
    app = FastAPI(
        title="Sistema DSD",
        version="0.1.0",
        description=(
            "API del sistema DSD. El contrato con el dispositivo se genera de aquí; "
            "ver contracts/README.md §3 para el degradado a OpenAPI 3.0."
        ),
        debug=cfg.debug,
        lifespan=ciclo_de_vida,
    )

    # El middleware va ANTES de los routers: envuelve todo, incluidos los
    # errores de validación de FastAPI y las redirecciones del panel.
    app.add_middleware(Observabilidad)

    app.include_router(salud.router)
    app.include_router(auth.router, prefix="/v1")
    app.include_router(dispositivos.router, prefix="/v1")
    app.include_router(catalogo.router, prefix="/v1")
    app.include_router(clientes.router, prefix="/v1")
    app.include_router(sync.router, prefix="/v1")
    app.include_router(tablero.router, prefix="/v1")

    # El panel va sin prefijo de versión: no es un contrato con nadie, es una
    # interfaz. Versionar sus rutas obligaría a mantener la vieja viva cuando
    # cambie una pantalla, que es justo lo que no se quiere de una UI.
    app.include_router(panel.router)
    app.include_router(panel_productos.router)
    app.include_router(panel_clientes.router)
    app.include_router(panel_cargas.router)
    app.include_router(panel_equipo.router)
    app.include_router(panel_equipo_fichas.router)
    app.include_router(panel_entradas.router)
    app.include_router(panel_compras.router)
    app.include_router(panel_salidas.router)
    app.include_router(panel_inventario.router)
    app.include_router(panel_ventas.router)
    app.include_router(panel_liquidaciones.router)
    app.include_router(panel_plan_visita.router)
    app.include_router(panel_cobranza.router)
    app.include_router(panel_cuenta_vendedores.router)
    app.include_router(panel_desempeno.router)
    app.include_router(panel_efectividad.router)
    app.include_router(panel_motivos.router)
    app.include_router(panel_objetivos.router)
    app.include_router(panel_equipos.router)
    app.include_router(panel_piloto.router)

    @app.exception_handler(RequestValidationError)
    async def _validacion(peticion: Request, exc: RequestValidationError):
        """Un 422 de esquema era INVISIBLE en los dos lados, y eso costó una tarde.

        Cuando el sobre que manda el teléfono no encaja en el esquema, FastAPI
        contesta 422 antes de que corra una sola línea del dominio. Consecuencias
        que no se ven:

        · el servidor **no registra nada**: la petición aparece en la bitácora de
          acceso con un 422 y sin motivo;
        · `sync_cuarentena` queda VACÍA, porque nada llegó a procesarse;
        · y el teléfono manda el lote completo a su cuarentena LOCAL, así que el
          vendedor ve «1 con error» y la oficina no encuentra ese error en
          ninguna parte.

        O sea: el único síntoma es una venta que no aparece en el tablero, y el
        motivo existe solo dentro de una respuesta HTTP que nadie guardó. Por eso
        se registra aquí.

        Se anotan las UBICACIONES y los tipos de error, no los valores: el payload
        de una venta trae precios y datos del cliente, y la bitácora del servidor
        no es el lugar de la cartera.
        """
        fallos = [
            {"donde": ".".join(str(p) for p in e.get("loc", ())), "que": e.get("type")}
            for e in exc.errors()
        ]
        logging.getLogger("dsd.validacion").warning(
            "422 en %s %s: %s", peticion.method, peticion.url.path, fallos
        )
        return await request_validation_exception_handler(peticion, exc)

    @app.exception_handler(SinSesionWeb)
    async def _sin_sesion(peticion: Request, _: SinSesionWeb):
        """Sin sesión, al login.

        Un 401 con JSON es correcto para la API y desconcertante en un navegador:
        quien entra por la URL de una pantalla espera ver la pantalla de entrar,
        no un objeto de error. Se conserva a dónde iba para volver después.
        """
        destino = peticion.url.path
        siguiente = f"?volver={destino}" if destino != "/panel" else ""
        return RedirectResponse(
            f"/panel/entrar{siguiente}", status_code=status.HTTP_303_SEE_OTHER
        )

    return app


app = crear_app()
