"""Las sincronizaciones entre los teléfonos y el servidor, para confirmar que todo fluye.

────────────────────────────────────────────────────────────────────────────
LA PREGUNTA QUE CONTESTA
────────────────────────────────────────────────────────────────────────────
«¿Está funcionando?» Hasta ahora se contestaba con indicios: el tablero decía
cuántos equipos no sincronizaron hoy, Teléfonos decía la hora del último envío, la
cuarentena decía lo rechazado. Ninguna decía si un teléfono concreto está AL DÍA
—todo subido, todo bajado, nada atorado— ni dejaba ver qué pasó en cada viaje.

Arriba, el veredicto por teléfono, con lo que le falta si le falta algo. Abajo,
la bitácora: cada subida (con lo que el servidor aceptó, ya tenía o rechazó) y
cada bajada (con cuántos cambios y de qué), en orden de hora.

Las subidas salen de `sync_lotes` y `sync_operaciones`, que existen desde la
Fase 2. Las bajadas, de `sync_bajadas` (migración 0043): antes no quedaba
constancia de lo que un teléfono recibió.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.api.admin.comun import SesionDep, render
from app.api.admin.periodo import PERIODOS, periodo_pedido
from app.api.admin.sesion_web import ActorWeb

router = APIRouter(prefix="/panel/sincronizaciones", tags=["panel"], include_in_schema=False)

LIMITE = 500

# Cómo se le dice a una persona lo que bajó. El nombre técnico se queda para el
# sistema: «ajuste_camion» no es algo que alguien diga en voz alta.
ENTIDADES = {
    "producto": "productos",
    "producto_unidad": "presentaciones",
    "precio": "precios",
    "lista_precios": "listas de precios",
    "cliente": "clientes",
    # Del piloto, cuando había crédito (ADR 0002 §81).
    "cartera": "saldos de clientes",
    "carga": "cargas",
    "venta": "ventas",
    "ajuste_camion": "ajustes del camión",
    "traspaso": "devoluciones a bodega",
    "identidad": "camión asignado",
    "motivo_merma": "motivos de merma",
    "motivo_no_drop": "motivos de no-venta",
    "promocion": "promociones",
}

OPERACIONES = {
    "venta.crear": "Venta",
    # Del piloto, cuando había abonos: el servidor ya no los recibe.
    "cobro.crear": "Cobro (piloto)",
    "merma.crear": "Merma, devolución o cambio",
    "no_drop.crear": "Visita sin venta",
    "cliente.crear": "Alta de cliente",
    "traspaso.crear": "Devolución a bodega",
}

# A dónde lleva cada documento aceptado.
ENLACES = {
    "venta.crear": "/panel/ventas/{}",
    "cliente.crear": "/panel/clientes/{}",
    "traspaso.crear": "/panel/entradas/devolucion/{}",
}

# Un teléfono que no ha hablado con el servidor en un día ya no está «al día»,
# aunque lo último que se supo de él estuviera perfecto.
SIN_CONTACTO = timedelta(days=1)


def _desglose(entidades: dict | None) -> str:
    if not entidades:
        return ""
    return ", ".join(
        f"{n} {ENTIDADES.get(e, e)}"
        for e, n in sorted(entidades.items(), key=lambda par: -par[1])
    )


def _nombre_de_operacion(tipo: str) -> str:
    # Un sobre de visita trae varias: «venta.crear+cobro.crear».
    return " + ".join(OPERACIONES.get(t, t) for t in tipo.split("+"))


async def _estado_de_los_telefonos(sesion, usuario: uuid.UUID | None) -> list[dict]:
    filas = (
        await sesion.execute(
            text(
                """
                SELECT d.id, d.etiqueta, d.estado, d.usuario_id,
                       u.nombre AS vendedor, u.codigo,
                       d.ultima_sync_push_en, d.ultima_sync_pull_en,
                       d.cola_pendiente, d.cola_reportada_en, d.ultimo_cursor_pull,
                       (SELECT l.app_version FROM sync_lotes l
                         WHERE l.dispositivo_id = d.id AND l.app_version IS NOT NULL
                         ORDER BY l.recibido_en DESC LIMIT 1) AS app_version,
                       (SELECT count(*) FROM sync_cuarentena c
                         WHERE c.dispositivo_id = d.id AND c.estado = 'pendiente')
                         AS cuarentena,
                       -- Lo que el servidor ya publicó para este teléfono y él
                       -- todavía no trae. Mismo filtro que el pull.
                       (SELECT count(*) FROM change_log cl
                         WHERE cl.cursor > d.ultimo_cursor_pull
                           AND (cl.ruta_id IS NULL OR cl.ruta_id IN (
                                 SELECT ur.ruta_id FROM usuarios_rutas ur
                                  WHERE ur.usuario_id = d.usuario_id))
                           AND (cl.vendedor_id IS NULL OR cl.vendedor_id = d.usuario_id))
                         AS por_bajar
                  FROM dispositivos d
                  JOIN usuarios u ON u.id = d.usuario_id
                 WHERE d.estado IN ('activo', 'suspendido')
                   AND (CAST(:u AS uuid) IS NULL OR d.usuario_id = :u)
                 ORDER BY u.codigo, d.etiqueta
                """
            ),
            {"u": usuario},
        )
    ).mappings().all()

    ahora = datetime.now(UTC)
    telefonos = []
    for f in filas:
        t = dict(f)
        contacto = max(
            (x for x in (f["ultima_sync_push_en"], f["ultima_sync_pull_en"]) if x),
            default=None,
        )
        faltas = []
        if contacto is None:
            faltas.append("nunca ha sincronizado")
        elif ahora - contacto > SIN_CONTACTO:
            faltas.append(f"sin contacto desde el {contacto.strftime('%d/%m %H:%M')}")
        if f["cola_pendiente"]:
            faltas.append(f"{f['cola_pendiente']} operación(es) por subir")
        if f["cuarentena"]:
            faltas.append(f"{f['cuarentena']} rechazada(s) en cuarentena")
        if f["por_bajar"]:
            faltas.append(f"{f['por_bajar']} cambio(s) por bajar")
        t["contacto"] = contacto
        t["faltas"] = faltas
        t["al_dia"] = not faltas
        telefonos.append(t)
    return telefonos


@router.get("", response_class=HTMLResponse)
async def bitacora(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    periodo: str = "",
    desde: str = "",
    hasta: str = "",
    usuario: str = "",
    direccion: str = "",
    problemas: str = "",
) -> HTMLResponse:
    rango = await periodo_pedido(sesion, periodo, desde, hasta)
    try:
        usuario_id = uuid.UUID(usuario) if usuario else None
    except ValueError:
        usuario_id = None
    if direccion not in ("subida", "bajada"):
        direccion = ""
    solo_problemas = problemas == "1"

    parametros = {
        "desde": rango.inicio,
        "hasta": rango.fin,
        "u": usuario_id,
        "dir": direccion,
        "problemas": solo_problemas,
        "limite": LIMITE + 1,
    }
    filas = (
        await sesion.execute(
            text(
                """
                SELECT * FROM (
                    SELECT 'subida' AS direccion, l.recibido_en AS momento,
                           l.id::text AS ref, d.etiqueta AS telefono, u.nombre AS vendedor,
                           l.total_operaciones AS cuantos, l.aceptadas, l.duplicadas,
                           l.rechazadas, l.app_version, l.duracion_ms,
                           NULL::jsonb AS entidades, NULL::bigint AS cursor_desde,
                           NULL::bigint AS cursor_hasta, false AS hay_mas,
                           false AS resincronizar
                      FROM sync_lotes l
                      JOIN dispositivos d ON d.id = l.dispositivo_id
                      LEFT JOIN usuarios u ON u.id = l.usuario_id
                     WHERE l.recibido_en::date BETWEEN :desde AND :hasta
                       AND (CAST(:u AS uuid) IS NULL OR l.usuario_id = :u)
                    UNION ALL
                    SELECT 'bajada', b.ocurrido_en, b.id::text, d.etiqueta, u.nombre,
                           b.cambios, NULL, NULL, NULL, NULL, NULL,
                           b.entidades, b.cursor_desde, b.cursor_hasta, b.hay_mas,
                           b.resincronizar
                      FROM sync_bajadas b
                      JOIN dispositivos d ON d.id = b.dispositivo_id
                      LEFT JOIN usuarios u ON u.id = b.usuario_id
                     WHERE b.ocurrido_en::date BETWEEN :desde AND :hasta
                       AND (CAST(:u AS uuid) IS NULL OR b.usuario_id = :u)
                ) x
                 WHERE (CAST(:dir AS text) = '' OR x.direccion = :dir)
                   AND (NOT :problemas OR COALESCE(x.rechazadas, 0) > 0 OR x.resincronizar)
                 ORDER BY x.momento DESC
                 LIMIT :limite
                """
            ),
            parametros,
        )
    ).mappings().all()

    totales = (
        await sesion.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM sync_lotes l
                    WHERE l.recibido_en::date BETWEEN :desde AND :hasta
                      AND (CAST(:u AS uuid) IS NULL OR l.usuario_id = :u)) AS subidas,
                  (SELECT COALESCE(sum(l.aceptadas), 0) FROM sync_lotes l
                    WHERE l.recibido_en::date BETWEEN :desde AND :hasta
                      AND (CAST(:u AS uuid) IS NULL OR l.usuario_id = :u)) AS aceptadas,
                  (SELECT COALESCE(sum(l.duplicadas), 0) FROM sync_lotes l
                    WHERE l.recibido_en::date BETWEEN :desde AND :hasta
                      AND (CAST(:u AS uuid) IS NULL OR l.usuario_id = :u)) AS duplicadas,
                  (SELECT COALESCE(sum(l.rechazadas), 0) FROM sync_lotes l
                    WHERE l.recibido_en::date BETWEEN :desde AND :hasta
                      AND (CAST(:u AS uuid) IS NULL OR l.usuario_id = :u)) AS rechazadas,
                  (SELECT count(*) FROM sync_bajadas b
                    WHERE b.ocurrido_en::date BETWEEN :desde AND :hasta
                      AND (CAST(:u AS uuid) IS NULL OR b.usuario_id = :u)) AS bajadas,
                  (SELECT COALESCE(sum(b.cambios), 0) FROM sync_bajadas b
                    WHERE b.ocurrido_en::date BETWEEN :desde AND :hasta
                      AND (CAST(:u AS uuid) IS NULL OR b.usuario_id = :u)) AS cambios
                """
            ),
            {"desde": rango.inicio, "hasta": rango.fin, "u": usuario_id},
        )
    ).mappings().one()

    vendedores = (
        await sesion.execute(
            text(
                "SELECT DISTINCT u.id, u.codigo, u.nombre FROM usuarios u "
                "  JOIN dispositivos d ON d.usuario_id = u.id ORDER BY u.codigo"
            )
        )
    ).mappings().all()

    movimientos = []
    for f in filas[:LIMITE]:
        m = dict(f)
        m["desglose"] = _desglose(f["entidades"])
        movimientos.append(m)

    return render(
        peticion,
        "sincronizaciones.html",
        {
            "telefonos": await _estado_de_los_telefonos(sesion, usuario_id),
            "movimientos": movimientos,
            "recortado": len(filas) > LIMITE,
            "limite": LIMITE,
            "totales": totales,
            "vendedores": vendedores,
            "usuario": str(usuario_id) if usuario_id else "",
            "direccion": direccion,
            "problemas": solo_problemas,
            "periodo": rango,
            "periodos": PERIODOS,
        },
        actor=actor,
        seccion="Sincronizaciones",
    )


@router.get("/lote/{lote_id:uuid}", response_class=HTMLResponse)
async def lote(
    peticion: Request, actor: ActorWeb, sesion: SesionDep, lote_id: uuid.UUID
) -> HTMLResponse:
    cabecera = (
        await sesion.execute(
            text(
                """
                SELECT l.*, d.etiqueta AS telefono, u.nombre AS vendedor
                  FROM sync_lotes l
                  JOIN dispositivos d ON d.id = l.dispositivo_id
                  LEFT JOIN usuarios u ON u.id = l.usuario_id
                 WHERE l.id = :l
                """
            ),
            {"l": lote_id},
        )
    ).mappings().first()

    operaciones = []
    if cabecera is not None:
        for f in (
            await sesion.execute(
                text(
                    """
                    SELECT o.operacion_id, o.tipo, o.entidad_id, o.resultado,
                           o.error_codigo, o.error_mensaje, o.procesado_en,
                           (SELECT c.id FROM sync_cuarentena c
                             WHERE c.operacion_id = o.operacion_id
                             ORDER BY c.id DESC LIMIT 1) AS cuarentena_id,
                           (SELECT c.estado FROM sync_cuarentena c
                             WHERE c.operacion_id = o.operacion_id
                             ORDER BY c.id DESC LIMIT 1) AS cuarentena_estado
                      FROM sync_operaciones o
                     WHERE o.lote_id = :l
                     ORDER BY o.procesado_en, o.operacion_id
                    """
                ),
                {"l": lote_id},
            )
        ).mappings():
            o = dict(f)
            o["nombre"] = _nombre_de_operacion(f["tipo"])
            plantilla = ENLACES.get(f["tipo"])
            o["enlace"] = (
                plantilla.format(f["entidad_id"])
                if plantilla and f["entidad_id"] and f["resultado"] != "rechazada"
                else None
            )
            operaciones.append(o)

    return render(
        peticion,
        "sincronizacion_lote.html",
        {"lote": cabecera, "operaciones": operaciones},
        actor=actor,
        seccion="Sincronizaciones",
    )
