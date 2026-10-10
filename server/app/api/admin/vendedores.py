"""Los vendedores, y todo lo que cada uno hizo en un periodo.

────────────────────────────────────────────────────────────────────────────
POR QUÉ UNA SOLA LÍNEA DE TIEMPO
────────────────────────────────────────────────────────────────────────────
Lo que hace un vendedor estaba repartido en ocho pantallas: sus ventas en
Ventas, sus abonos en Cobranza, su carga en Cargas, su corte en Corte del día,
su cuenta en Cuenta de vendedores… Para contestar «¿qué hizo Juan el martes?»
había que abrirlas todas y armar el día en la cabeza.

Aquí se juntan en orden de hora, cada renglón con un enlace a su pantalla de
siempre. No hay datos nuevos ni tablas nuevas: es una consulta que lee lo que
ya existe, y por eso no puede contradecir a ninguna de las otras pantallas.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.api.admin.comun import SesionDep, render
from app.api.admin.periodo import PERIODOS, Periodo, periodo_pedido
from app.api.admin.sesion_web import ActorWeb

router = APIRouter(prefix="/panel/vendedores", tags=["panel"], include_in_schema=False)

PERMISO_VER = "ventas.ver_todas"

# El orden es el del día: lo que pasa en la calle primero, lo de la oficina después.
TIPOS: tuple[tuple[str, str], ...] = (
    ("venta", "Ventas"),
    ("merma", "Mermas, devoluciones y cambios"),
    ("no_venta", "Visitas sin venta"),
    ("cliente", "Clientes dados de alta"),
    ("carga", "Cargas"),
    ("devolucion", "Devoluciones a bodega"),
    ("corte", "Cortes del día"),
    ("ajuste", "Ajustes del camión"),
    ("cuenta", "Cuenta del vendedor"),
)

# Más renglones ya no se leen en una pantalla; para eso está el filtro por tipo o
# un periodo más corto, y la pantalla lo dice.
LIMITE = 500

SQL_MOVIMIENTOS = """
WITH movimientos AS (
    SELECT v.fecha_dispositivo AS momento, v.fecha_operativa AS fecha, 'venta' AS tipo,
           v.folio_local AS folio, c.nombre_comercial AS cliente,
           CASE WHEN v.tipo = 'credito' THEN 'A crédito (piloto)'
                WHEN v.forma_pago = 'transferencia' THEN
                  'Por transferencia'
                  || CASE v.pago_estado WHEN 'por_confirmar' THEN ' · por confirmar'
                                        WHEN 'rechazado' THEN ' · no llegó'
                                        ELSE '' END
                ELSE 'En efectivo' END
             || CASE WHEN v.corregida_en IS NOT NULL THEN ' · corregida en la oficina'
                     ELSE '' END AS detalle,
           v.total AS importe, v.estado, v.requiere_revision AS marca,
           '/panel/ventas/' || v.id AS enlace, v.id::text AS ref
      FROM ventas v
      LEFT JOIN clientes c ON c.id = v.cliente_id
     WHERE v.vendedor_id = :v AND v.fecha_operativa BETWEEN :desde AND :hasta


    UNION ALL
    SELECT m.fecha_dispositivo, m.fecha_operativa, 'merma', m.folio_local,
           c.nombre_comercial,
           CASE m.tipo WHEN 'devolucion_cliente' THEN 'Devolución del cliente'
                       WHEN 'cambio' THEN 'Cambio físico'
                       ELSE 'Merma' END
             || ' · ' || COALESCE(mm.nombre, m.motivo_codigo)
             || ' · ' || COALESCE((SELECT trim_scale(sum(d.cantidad_base))::text
                                     FROM merma_detalle d WHERE d.merma_id = m.id), '0')
             || ' pza',
           NULL, m.estado, m.requiere_revision, NULL, m.id::text
      FROM mermas m
      LEFT JOIN clientes c ON c.id = m.cliente_id
      LEFT JOIN motivos_merma mm ON mm.codigo = m.motivo_codigo
     WHERE m.vendedor_id = :v AND m.fecha_operativa BETWEEN :desde AND :hasta

    UNION ALL
    SELECT n.fecha_dispositivo, n.fecha_operativa, 'no_venta', n.folio_consecutivo::text,
           c.nombre_comercial,
           'Visita sin venta · ' || COALESCE(md.nombre, n.motivo_codigo)
             || COALESCE(' · ' || NULLIF(btrim(n.nota), ''), ''),
           NULL, NULL, n.requiere_revision, '/panel/clientes/' || n.cliente_id, n.id::text
      FROM no_drops n
      LEFT JOIN clientes c ON c.id = n.cliente_id
      LEFT JOIN motivos_no_drop md ON md.codigo = n.motivo_codigo
     WHERE n.vendedor_id = :v AND n.fecha_operativa BETWEEN :desde AND :hasta

    UNION ALL
    SELECT c.creado_en, c.creado_en::date, 'cliente', c.codigo, c.nombre_comercial,
           CASE c.origen_alta WHEN 'campo' THEN 'Alta en la calle' ELSE 'Alta' END,
           NULL, c.estatus, c.requiere_revision, '/panel/clientes/' || c.id, c.id::text
      FROM clientes c
     WHERE c.creado_por = :v AND c.creado_en::date BETWEEN :desde AND :hasta

    UNION ALL
    SELECT COALESCE(g.confirmada_en, g.creado_en), g.fecha_operativa, 'carga', g.folio,
           NULL,
           'Carga de ' || (SELECT count(*) FROM carga_detalle d WHERE d.carga_id = g.id)
             || ' producto(s)',
           NULL, replace(g.estado, '_', ' '), g.forzada, '/panel/cargas/' || g.id, g.id::text
      FROM cargas g
     WHERE g.vendedor_id = :v AND g.fecha_operativa BETWEEN :desde AND :hasta

    UNION ALL
    SELECT COALESCE(t.fecha_dispositivo, t.creado_en),
           COALESCE(t.fecha_operativa, t.creado_en::date), 'devolucion', t.folio, NULL,
           'Devolución del camión a la bodega'
             || COALESCE(' · ' || NULLIF(btrim(t.observaciones), ''), ''),
           NULL, t.estado, t.estado = 'rechazado', '/panel/entradas/devolucion/' || t.id,
           t.id::text
      FROM traspasos t
     WHERE t.solicitado_por = :v
       AND COALESCE(t.fecha_operativa, t.creado_en::date) BETWEEN :desde AND :hasta

    UNION ALL
    SELECT COALESCE(l.cerrada_en, l.creado_en), l.fecha_operativa, 'corte', l.folio, NULL,
           'Corte del día'
             || CASE WHEN COALESCE(l.diferencia_efectivo, 0) <> 0
                     THEN ' · diferencia de efectivo' ELSE '' END,
           l.diferencia_efectivo, replace(l.estado, '_', ' '),
           COALESCE(l.diferencia_efectivo, 0) <> 0, '/panel/liquidaciones/' || l.id, l.id::text
      FROM liquidaciones l
     WHERE l.vendedor_id = :v AND l.fecha_operativa BETWEEN :desde AND :hasta

    UNION ALL
    -- Los ajustes son del CAMIÓN, no de una persona: los hace la oficina, y aquí se
    -- muestran los del camión que el vendedor maneja hoy.
    SELECT a.creado_en, a.creado_en::date, 'ajuste', a.folio, NULL,
           p.nombre || ' ' || CASE WHEN a.delta > 0 THEN '+' ELSE '' END
             || trim_scale(a.delta)::text || ' · ' || a.nota,
           NULL, NULL, false,
           '/panel/inventario/' || a.almacen_id || '/' || a.producto_id, a.id::text
      FROM ajustes_camion a
      JOIN productos p ON p.id = a.producto_id
     WHERE a.almacen_id = :camion AND a.creado_en::date BETWEEN :desde AND :hasta

    UNION ALL
    -- En la cuenta, un cargo es lo que el vendedor debe: positivo. Un abono o una
    -- condonación lo baja.
    SELECT q.registrado_en, q.fecha, 'cuenta', NULL, NULL,
           initcap(q.tipo) || ' · ' || q.concepto,
           CASE q.tipo WHEN 'cargo' THEN q.importe ELSE -q.importe END,
           NULL, false, '/panel/vendedores/cuenta/' || q.vendedor_id, q.id::text
      FROM cuenta_vendedor q
     WHERE q.vendedor_id = :v AND q.fecha BETWEEN :desde AND :hasta
)
"""


SQL_LISTA = """
SELECT u.id, u.codigo, u.nombre, u.activo,
       a.nombre AS camion,
       (SELECT string_agg(r.codigo, ', ' ORDER BY r.codigo)
          FROM usuarios_rutas ur JOIN rutas r ON r.id = ur.ruta_id
         WHERE ur.usuario_id = u.id) AS rutas,
       (SELECT count(*) FROM ventas v
         WHERE v.vendedor_id = u.id AND v.estado = 'confirmada'
           AND v.fecha_operativa BETWEEN :desde AND :hasta) AS ventas,
       (SELECT COALESCE(sum(v.total), 0) FROM ventas v
         WHERE v.vendedor_id = u.id AND v.estado = 'confirmada'
           AND v.fecha_operativa BETWEEN :desde AND :hasta) AS importe,
       -- Lo que tiene que entregar en la mano: la transferencia se confirma
       -- contra el banco, no en el corte.
       (SELECT COALESCE(sum(v.total), 0) FROM ventas v
         WHERE v.vendedor_id = u.id AND v.estado = 'confirmada'
           AND v.forma_pago = 'efectivo'
           AND v.fecha_operativa BETWEEN :desde AND :hasta) AS efectivo,
       (SELECT count(*) FROM no_drops n
         WHERE n.vendedor_id = u.id
           AND n.fecha_operativa BETWEEN :desde AND :hasta) AS no_ventas,
       (SELECT count(*) FROM mermas m
         WHERE m.vendedor_id = u.id
           AND m.fecha_operativa BETWEEN :desde AND :hasta) AS mermas,
       (SELECT max(GREATEST(d.ultima_sync_push_en, d.ultima_sync_pull_en))
          FROM dispositivos d WHERE d.usuario_id = u.id) AS ultimo_contacto,
       COALESCE((SELECT saldo FROM v_cuenta_vendedor cv
                  WHERE cv.vendedor_id = u.id), 0) AS saldo_cuenta
  FROM usuarios u
  LEFT JOIN almacenes a ON a.id = u.almacen_id
 WHERE u.rol_codigo = 'vendedor'
   -- Los desactivados van en su propia lista (ADR 0002 §93).
   AND u.activo = :activos
 ORDER BY u.codigo
"""


# ---------------------------------------------------------------------------
# Las consultas, aparte de la pantalla
# ---------------------------------------------------------------------------
# Las usan el panel y la app (`/v1/vendedores`). Viven aquí, una vez, para que el
# teléfono de la oficina y el dashboard digan siempre lo mismo de cada vendedor.


async def lista_de_vendedores(sesion, rango: Periodo, *, activos: bool = True) -> list:
    """Los activos o, con `activos=False`, los desactivados: nunca juntos."""
    return list(
        (
            await sesion.execute(
                text(SQL_LISTA),
                {"desde": rango.inicio, "hasta": rango.fin, "activos": activos},
            )
        ).mappings().all()
    )


async def vendedores_desactivados(sesion) -> int:
    return (
        await sesion.execute(
            text("SELECT count(*) FROM usuarios WHERE rol_codigo = 'vendedor' AND NOT activo")
        )
    ).scalar_one()


async def ficha_del_vendedor(sesion, vendedor_id: uuid.UUID):
    return (
        await sesion.execute(
            text(
                """
                SELECT u.id, u.codigo, u.nombre, u.activo, u.almacen_id,
                       a.nombre AS camion,
                       (SELECT string_agg(r.codigo || ' · ' || r.nombre, ', ' ORDER BY r.codigo)
                          FROM usuarios_rutas ur JOIN rutas r ON r.id = ur.ruta_id
                         WHERE ur.usuario_id = u.id) AS rutas,
                       COALESCE((SELECT saldo FROM v_cuenta_vendedor cv
                                  WHERE cv.vendedor_id = u.id), 0) AS saldo_cuenta
                  FROM usuarios u
                  LEFT JOIN almacenes a ON a.id = u.almacen_id
                 WHERE u.id = :v AND u.rol_codigo = 'vendedor'
                """
            ),
            {"v": vendedor_id},
        )
    ).mappings().first()


async def movimientos_del_vendedor(sesion, vendedor, rango: Periodo, tipo: str = ""):
    """`(resumen por tipo, renglones hasta LIMITE, si se recortó)`."""
    parametros = {
        "v": vendedor["id"],
        "camion": vendedor["almacen_id"],
        "desde": rango.inicio,
        "hasta": rango.fin,
    }
    # Cuántos de cada tipo, SIN el límite: el resumen tiene que decir el total
    # aunque la lista se corte.
    resumen = {
        f["tipo"]: f
        for f in (
            await sesion.execute(
                text(
                    SQL_MOVIMIENTOS
                    # El importe solo de lo que cuenta: una venta cancelada no es
                    # dinero.
                    + "SELECT tipo, count(*) AS cuantos, "
                    "       COALESCE(sum(importe) FILTER ("
                    "         WHERE estado IS NULL OR estado IN ('confirmada', 'confirmado', "
                    "                                            'cerrada', 'cuadrada', "
                    "                                            'con diferencia')), 0) AS importe "
                    "  FROM movimientos GROUP BY tipo"
                ),
                parametros,
            )
        ).mappings()
    }
    filas = (
        await sesion.execute(
            text(
                SQL_MOVIMIENTOS
                + "SELECT * FROM movimientos WHERE (CAST(:tipo AS text) = '' OR tipo = :tipo) "
                "ORDER BY momento DESC NULLS LAST LIMIT :limite"
            ),
            {**parametros, "tipo": tipo, "limite": LIMITE + 1},
        )
    ).mappings().all()
    return resumen, filas[:LIMITE], len(filas) > LIMITE


async def telefonos_del_vendedor(sesion, vendedor_id: uuid.UUID) -> list:
    return list(
        (
            await sesion.execute(
                text(
                    "SELECT id, etiqueta, estado, ultima_sync_push_en, ultima_sync_pull_en, "
                    "       cola_pendiente, cola_reportada_en "
                    "  FROM dispositivos WHERE usuario_id = :v ORDER BY registrado_en DESC"
                ),
                {"v": vendedor_id},
            )
        ).mappings().all()
    )


@router.get("", response_class=HTMLResponse)
async def lista(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    periodo: str = "",
    desde: str = "",
    hasta: str = "",
    ver: str = "",
) -> HTMLResponse:
    actor.exigir(PERMISO_VER)
    rango = await periodo_pedido(sesion, periodo, desde, hasta)
    viendo_desactivados = ver == "desactivados"
    filas = await lista_de_vendedores(sesion, rango, activos=not viendo_desactivados)
    return render(
        peticion,
        "vendedores.html",
        {
            "vendedores": filas,
            "periodo": rango,
            "periodos": PERIODOS,
            "viendo_desactivados": viendo_desactivados,
            "desactivados": await vendedores_desactivados(sesion),
        },
        actor=actor,
        seccion="Vendedores",
    )


@router.get("/{vendedor_id:uuid}", response_class=HTMLResponse)
async def movimientos(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    vendedor_id: uuid.UUID,
    periodo: str = "",
    desde: str = "",
    hasta: str = "",
    tipo: str = "",
) -> HTMLResponse:
    actor.exigir(PERMISO_VER)
    rango = await periodo_pedido(sesion, periodo, desde, hasta)
    if tipo not in dict(TIPOS):
        tipo = ""

    vendedor = await ficha_del_vendedor(sesion, vendedor_id)
    if vendedor is None:
        return render(
            peticion,
            "vendedor_movimientos.html",
            {"vendedor": None, "periodo": rango, "periodos": PERIODOS},
            actor=actor,
            seccion="Vendedores",
        )

    resumen, filas, recortado = await movimientos_del_vendedor(sesion, vendedor, rango, tipo)
    telefonos = await telefonos_del_vendedor(sesion, vendedor_id)

    return render(
        peticion,
        "vendedor_movimientos.html",
        {
            "vendedor": vendedor,
            "movimientos": filas,
            "recortado": recortado,
            "limite": LIMITE,
            "resumen": resumen,
            "tipos": TIPOS,
            "tipo": tipo,
            "telefonos": telefonos,
            "periodo": rango,
            "periodos": PERIODOS,
        },
        actor=actor,
        seccion="Vendedores",
    )
