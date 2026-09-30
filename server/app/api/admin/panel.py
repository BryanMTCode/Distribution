"""Panel de operación: HTML renderizado en el servidor.

────────────────────────────────────────────────────────────────────────────
QUÉ ES Y QUÉ NO ES ESTE PANEL
────────────────────────────────────────────────────────────────────────────
Es la herramienta de la oficina para **operar**: capturar catálogo y precios,
atender lo que el motor de sincronización rechazó, y revisar las ventas que
entraron marcadas.

No es un dashboard de análisis. Los números que muestra son operativos —cuántas
cosas necesitan atención ahora—, no indicadores de negocio. El análisis profundo
va en Streamlit sobre modelos de lectura (Fase 8), y mezclarlos terminaría en
consultas analíticas pesadas corriendo sobre las tablas transaccionales mientras
un vendedor sincroniza.

────────────────────────────────────────────────────────────────────────────
POR QUÉ SERVIDOR Y NO UNA SPA
────────────────────────────────────────────────────────────────────────────
Cinco personas en una oficina, sobre red local. Una SPA agregaría un proceso de
compilación, un segundo lenguaje y un estado duplicado en el navegador para
resolver un problema que no existe. Formularios y recargas completas son
instantáneos en LAN, y funcionan el día que alguien entre desde una computadora
vieja.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.api.admin.comun import SesionDep, dinero, render
from app.api.admin.sesion_web import (
    ActorWeb,
    abrir_sesion,
    cerrar_sesion,
    exigir_csrf,
)
from app.core.seguridad import verificar_password
from app.infra.models import Usuario

# `include_in_schema=False`: el panel NO entra al OpenAPI.
#
# De `contracts/openapi.json` sale el cliente Dart del dispositivo. Meter ahí
# rutas que devuelven HTML para una oficina ensuciaría el contrato del teléfono y
# haría que cada pantalla nueva del panel apareciera como un cambio de contrato
# en el diff. El panel es una interfaz, no una API.
#
# Las pantallas de catálogo y de clientes viven en `productos.py` y `clientes.py`,
# con su propio router. El andamio que comparten las tres (la navegación, el
# `render`, los lectores de campos numéricos) está en `comun.py`.
router = APIRouter(prefix="/panel", tags=["panel"], include_in_schema=False)


# ---------------------------------------------------------------------------
# Entrar y salir
# ---------------------------------------------------------------------------


@router.get("/entrar", response_class=HTMLResponse)
async def pantalla_entrar(peticion: Request) -> HTMLResponse:
    return render(peticion, "entrar.html", {"error": None})


@router.post("/entrar")
async def procesar_entrar(
    peticion: Request,
    sesion: SesionDep,
    codigo: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    usuario = (
        await sesion.execute(
            text("SELECT * FROM usuarios WHERE codigo = :c"), {"c": codigo.strip()}
        )
    ).mappings().first()

    # El mensaje es el MISMO para usuario inexistente, contraseña incorrecta y
    # usuario inactivo. Distinguirlos confirmaría qué códigos de empleado existen,
    # y el código de empleado es lo primero que alguien adivina.
    generico = "Código o contraseña incorrectos."

    if usuario is None or not usuario["activo"]:
        # Se verifica un hash de todas formas: responder de inmediato cuando el
        # usuario no existe delata su ausencia por el tiempo de respuesta.
        verificar_password(password, _HASH_SEÑUELO)
        return render(peticion, "entrar.html", {"error": generico})

    if not verificar_password(password, usuario["password_hash"]):
        return render(peticion, "entrar.html", {"error": generico})

    if usuario["rol_codigo"] == "vendedor":
        # El vendedor tiene su app; el panel es de oficina. Dejarlo entrar aquí le
        # daría pantallas de captura que no le corresponden.
        return render(
            peticion,
            "entrar.html",
            {"error": "Tu usuario es de ruta: entra por la app del teléfono."},
        )

    modelo = await sesion.get(Usuario, usuario["id"])
    respuesta = RedirectResponse("/panel", status_code=status.HTTP_303_SEE_OTHER)
    await abrir_sesion(
        sesion,
        modelo,
        respuesta,
        ip=peticion.client.host if peticion.client else None,
    )
    return respuesta


# Hash real de una contraseña que nadie usa, para gastar el mismo tiempo cuando el
# usuario no existe. Se genera al importar el módulo.
def _generar_señuelo() -> str:
    from app.core.seguridad import hashear_password

    return hashear_password("no-existe-este-usuario")


_HASH_SEÑUELO = _generar_señuelo()


@router.get("/salir")
async def salir(peticion: Request, sesion: SesionDep):
    respuesta = RedirectResponse("/panel/entrar", status_code=status.HTTP_303_SEE_OTHER)
    await cerrar_sesion(sesion, peticion, respuesta)
    return respuesta


# ---------------------------------------------------------------------------
# Tablero
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
async def tablero(peticion: Request, actor: ActorWeb, sesion: SesionDep) -> HTMLResponse:
    """Los indicadores que dicen qué necesita atención hoy.

    Cada uno es una consulta corta con índice. El tablero se abre cien veces al
    día: una consulta que recorra `ventas` completa lo volvería lento justo cuando
    el volumen crezca, que es cuando más se necesita.
    """
    fila = (
        await sesion.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM sync_cuarentena WHERE estado = 'pendiente')
                    AS cuarentena,
                  (SELECT count(*) FROM ventas
                    WHERE requiere_revision AND estado = 'confirmada') AS en_revision,
                  (SELECT count(*) FROM dispositivos WHERE estado = 'activo')
                    AS equipos_activos,
                  (SELECT count(*) FROM dispositivos
                    WHERE estado = 'activo'
                      AND (ultima_sync_push_en IS NULL
                           OR ultima_sync_push_en < CURRENT_DATE)) AS equipos_rezagados,
                  (SELECT count(*) FROM ventas
                    WHERE fecha_operativa = CURRENT_DATE AND estado = 'confirmada')
                    AS ventas_hoy,
                  (SELECT COALESCE(sum(total), 0) FROM ventas
                    WHERE fecha_operativa = CURRENT_DATE AND estado = 'confirmada')
                    AS importe_hoy,
                  (SELECT count(*) FROM productos WHERE activo) AS productos,
                  (SELECT count(*) FROM clientes WHERE estatus <> 'inactivo') AS clientes,
                  (SELECT count(*) FROM clientes WHERE estatus = 'prospecto')
                    AS prospectos,
                  (SELECT count(*) FROM productos p
                    WHERE p.activo
                      AND NOT EXISTS (
                        SELECT 1 FROM precios pr
                          JOIN listas_precios l ON l.id = pr.lista_id AND l.es_default
                         WHERE pr.producto_id = p.id)) AS sin_precio
                """
            )
        )
    ).mappings().one()

    indicadores = dict(fila)
    indicadores["importe_hoy"] = dinero(fila["importe_hoy"])

    return render(
        peticion,
        "tablero.html",
        {"indicadores": indicadores},
        actor=actor,
        seccion="Tablero",
    )


# ---------------------------------------------------------------------------
# Cuarentena
# ---------------------------------------------------------------------------


@router.get("/cuarentena", response_class=HTMLResponse)
async def cuarentena(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    estado: str = "pendiente",
) -> HTMLResponse:
    """Lo que el servidor rechazó.

    Es la pantalla más importante del panel, y la que no existía: hasta ahora una
    operación rechazada quedaba en una tabla que nadie miraba. El vendedor ya
    entregó la mercancía, así que cada fila aquí es un descuadre esperando.
    """
    if estado not in ("pendiente", "reprocesada", "descartada"):
        estado = "pendiente"

    filas = (
        await sesion.execute(
            text(
                """
                SELECT c.id, c.operacion_id, c.tipo, c.error_codigo, c.error_mensaje,
                       c.estado, c.recibido_en, c.nota_resolucion,
                       d.etiqueta AS equipo, u.nombre AS vendedor
                  FROM sync_cuarentena c
                  JOIN dispositivos d ON d.id = c.dispositivo_id
                  LEFT JOIN usuarios u ON u.id = c.usuario_id
                 WHERE c.estado = :estado
                 ORDER BY c.recibido_en DESC
                 LIMIT 200
                """
            ),
            {"estado": estado},
        )
    ).mappings().all()

    conteos = (
        await sesion.execute(
            text("SELECT estado, count(*) AS n FROM sync_cuarentena GROUP BY estado")
        )
    ).mappings().all()

    return render(
        peticion,
        "cuarentena.html",
        {
            "filas": filas,
            "estado": estado,
            "conteos": {c["estado"]: c["n"] for c in conteos},
        },
        actor=actor,
        seccion="Cuarentena",
    )


@router.get("/cuarentena/{id_fila}", response_class=HTMLResponse)
async def cuarentena_detalle(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    id_fila: int,
) -> HTMLResponse:
    """El payload completo, tal como llegó.

    Se muestra íntegro a propósito: para decidir qué hacer con una operación
    rechazada hay que ver exactamente qué mandó el equipo, no un resumen.
    """
    import json

    fila = (
        await sesion.execute(
            text(
                """
                SELECT c.*, d.etiqueta AS equipo, u.nombre AS vendedor
                  FROM sync_cuarentena c
                  JOIN dispositivos d ON d.id = c.dispositivo_id
                  LEFT JOIN usuarios u ON u.id = c.usuario_id
                 WHERE c.id = :id
                """
            ),
            {"id": id_fila},
        )
    ).mappings().first()

    if fila is None:
        return RedirectResponse("/panel/cuarentena", status_code=status.HTTP_303_SEE_OTHER)

    return render(
        peticion,
        "cuarentena_detalle.html",
        {
            "fila": fila,
            "payload": json.dumps(fila["payload"], indent=2, ensure_ascii=False),
        },
        actor=actor,
        seccion="Cuarentena",
    )


@router.post("/cuarentena/{id_fila}/descartar")
async def descartar_cuarentena(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    id_fila: int,
    nota: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Marca la operación como atendida fuera del sistema.

    **No la borra.** El payload se conserva para siempre: es la única evidencia de
    una venta que existió en la calle y nunca entró. La nota es obligatoria porque
    "descartada" sin explicación es peor que pendiente — dentro de un mes nadie
    sabrá si se corrigió a mano o se perdió.
    """
    exigir_csrf(peticion, csrf)
    actor.exigir("sync.cuarentena")

    if not nota.strip():
        return RedirectResponse(
            f"/panel/cuarentena/{id_fila}?error=nota",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    await sesion.execute(
        text(
            "UPDATE sync_cuarentena "
            "   SET estado = 'descartada', resuelto_por = :quien, resuelto_en = now(), "
            "       nota_resolucion = :nota "
            " WHERE id = :id AND estado = 'pendiente'"
        ),
        {"id": id_fila, "quien": actor.usuario_id, "nota": nota.strip()},
    )
    await sesion.commit()
    return RedirectResponse("/panel/cuarentena", status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# Ventas en revisión
# ---------------------------------------------------------------------------

# Cómo se le explica a la oficina cada motivo. El código es para el sistema; esto
# es para la persona que tiene que decidir qué hacer.
EXPLICACION_MOTIVOS = {
    "excede_limite_credito": "Pasó su límite de crédito",
    "cliente_bloqueado": "El cliente estaba bloqueado",
    "precio_desactualizado": "Vendió con un precio que ya cambió",
    "sin_lista_de_precios": "No trae lista de precios",
    "fuera_de_geocerca": "Lejos del domicilio registrado",
    "sin_ubicacion": "Sin GPS al vender",
    "reloj_desfasado": "El reloj del equipo está desfasado",
}


@router.get("/ventas", response_class=HTMLResponse)
async def ventas(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    solo_revision: int = 1,
) -> HTMLResponse:
    """Las ventas recibidas, con las marcadas primero.

    El principio §0.1 dice que el servidor nunca rechaza una venta offline: la
    marca. **Esta pantalla es la otra mitad de ese principio.** Marcar sin que
    nadie mire convierte la bandera en ruido, y entonces el principio se vuelve
    una excusa para no validar.
    """
    filtro = "WHERE v.requiere_revision AND v.estado = 'confirmada'" if solo_revision else ""

    filas = (
        await sesion.execute(
            text(
                f"""
                SELECT v.id, v.folio_local, v.folio_servidor, v.total, v.tipo,
                       v.fecha_operativa, v.fecha_dispositivo, v.requiere_revision,
                       v.revision_motivos, v.distancia_cliente_m, v.desfase_reloj_seg,
                       c.nombre_comercial AS cliente, u.nombre AS vendedor
                  FROM ventas v
                  JOIN clientes c ON c.id = v.cliente_id
                  JOIN usuarios u ON u.id = v.vendedor_id
                  {filtro}
                 ORDER BY v.requiere_revision DESC, v.fecha_servidor DESC
                 LIMIT 200
                """  # noqa: S608 — `filtro` es una constante del código, no entrada
            )
        )
    ).mappings().all()

    return render(
        peticion,
        "ventas.html",
        {
            "filas": filas,
            "solo_revision": bool(solo_revision),
            "explicacion": EXPLICACION_MOTIVOS,
        },
        actor=actor,
        seccion="Ventas",
    )
