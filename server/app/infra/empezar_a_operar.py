"""Empezar a operar: se van los usuarios, la operación queda en cero (ADR 0002 §131).

Pedido de la dirección (octubre 2026), en tres tiempos: «quiero formatear toda
la base como si no hubiera nada, porque ya empezaré a operar… solo dejaré vivo
el usuario ADMIN01»; luego, «que se eliminen los usuarios solamente, lo demás en
0, el almacén… pero los artículos, tiendas y eso se quedan»; y a la pregunta de
cómo: **todos de una vez**, porque el botón «Eliminar» del panel se niega con
quien tiene ventas o cargas firmadas.

────────────────────────────────────────────────────────────────────────────
QUÉ SE QUEDA Y QUÉ SE VA
────────────────────────────────────────────────────────────────────────────
**Se quedan:**
  · ADMIN01 (o el administrador que se nombre), con sus permisos y teléfonos;
  · los artículos con sus unidades, precios, costos y familias, las marcas, las
    promociones y los proveedores;
  · las tiendas, con su ubicación, su ruta y su plan de visita;
  · las rutas (sin titular) y las bodegas (vacías);
  · los objetivos de las rutas;
  · lo que siembran las migraciones (roles, unidades, canales, sucursales,
    listas de precios, motivos…) y las marcas de la Revisión de la app.

**Se van:**
  · los demás usuarios con sus teléfonos;
  · los camiones: un camión no existe sin responsable, y el panel le crea el
    suyo a cada vendedor nuevo;
  · toda la operación: ventas, cobros, cargas, cortes, mermas, entradas,
    salidas, traspasos, cuentas de los vendedores, existencias y libro mayor,
    el tablero, el piloto, la bitácora de sincronización, las sesiones y la
    auditoría. Los folios vuelven a empezar en 1.

Con eso nadie tiene nada firmado, y el botón «Eliminar» del panel funciona con
cualquier usuario que se dé de alta después y no haya vendido.

────────────────────────────────────────────────────────────────────────────
LO QUE VEN LOS TELÉFONOS
────────────────────────────────────────────────────────────────────────────
Un teléfono recién vinculado baja todo del `change_log` desde el cursor 0. Se
quedan los deltas del catálogo (artículos, unidades, precios, familias,
promociones, listas y motivos). Los de las tiendas se tiran y se **vuelven a
publicar al día**: el de antes podía decir quién la dio de alta desde qué
teléfono, o una visita reprogramada que ya no existe. La cartera, las cargas y
todo lo demás de la operación se tira.

────────────────────────────────────────────────────────────────────────────
NINGUNA TABLA SIN DECIDIR
────────────────────────────────────────────────────────────────────────────
Cada tabla del esquema está en una de las tres listas de abajo, y una prueba lo
revisa contra la base: una migración que agregue una tabla hace fallar la
prueba hasta que alguien decida qué pasa con ella.

TRUNCATE y no DELETE, como en `base-en-blanco` (§90): el libro mayor y la cuenta
del vendedor rechazan DELETE por disparador, y TRUNCATE no llena `change_log` de
bajas. Sin CASCADE: si una tabla que se queda apuntara a una que se vacía,
PostgreSQL se niega y no se borra nada.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infra.base_en_blanco import SECUENCIAS_DE_FOLIO

PALABRA = "OPERAR"
QUEDA_POR_OMISION = "ADMIN01"

# Se quedan como están.
SE_QUEDAN = (
    "alembic_version", "spatial_ref_sys",
    "roles", "permisos", "roles_permisos",
    "unidades_medida", "canales", "sucursales", "listas_precios",
    "motivos_merma", "motivos_no_drop", "piloto_criterios",
    "dim_tiempo", "sync_retencion",
    # Las marcas de la Revisión de la app (§112): son de la app, no del negocio.
    "revision_app",
    # El catálogo.
    "productos", "producto_unidades", "precios", "producto_costos",
    "categorias", "marcas", "promociones",
    # El plan de visita de las tiendas.
    "clientes_frecuencia",
)

# Se quedan, quitándoles lo que era de los usuarios que se van.
SE_QUEDAN_Y_SE_LIMPIAN = (
    "usuarios", "usuarios_permisos", "dispositivos",   # solo los del administrador
    "change_log",                                       # el catálogo, y las tiendas al día
    "clientes",                                         # sin quién las dio de alta
    "rutas",                                            # sin titular
    "almacenes",                                        # sin camiones ni responsables
    "proveedores",                                      # sin quién los capturó
    "objetivos", "objetivos_diarios", "objetivos_ruta",  # sin quién los fijó
)

SE_VACIAN = (
    # Ventas, cobros y lo que cuelga de ellas
    "venta_mayoreo_partidas", "ventas_mayoreo",
    "ventas_cancelaciones", "venta_partidas", "cobros_aplicaciones", "cobros",
    "cuentas_por_cobrar", "ventas", "no_drops", "anulaciones",
    # El cierre del vendedor y el corte
    "solicitud_carga_detalle", "solicitudes_carga", "corte_vendedor_conteo",
    "cortes_vendedor", "liquidacion_detalle", "liquidaciones", "cuenta_vendedor",
    # Movimiento de mercancía, y lo que se le debe al proveedor por ella
    "carga_detalle", "cargas", "merma_detalle", "mermas", "traspaso_detalle",
    "traspasos", "ajustes_camion", "salida_detalle", "salidas",
    "pagos_proveedor", "cuentas_por_pagar", "entrada_detalle", "entradas",
    "existencias", "movimientos_inventario",
    # Las revisiones de tiendas duplicadas (las resolvía un usuario)
    "clientes_posibles_duplicados",
    # Rutas de los usuarios, y los códigos apartados (§132)
    "usuarios_rutas", "usuarios_codigos_anteriores",
    # Sincronización y sesiones
    "sync_cuarentena", "sync_operaciones", "sync_lotes", "sync_bajadas",
    "folios_rangos", "sesiones",
    # Piloto, tablero, analítica, cola y auditoría
    "piloto_incidencias", "piloto_jornadas", "pilotos",
    "tablero_dia", "tablero_mes_ruta", "tablero_refrescos", "analitica_refrescos",
    "jobs", "auditoria",
)

# Los deltas que se quedan: los del catálogo. Las tiendas se republican.
DELTAS_QUE_SE_QUEDAN = (
    "producto", "producto_unidad", "precio", "categoria", "promocion",
    "lista_precios", "motivo_merma", "motivo_no_drop",
)


class NoSePuede(Exception):
    """No se puede, y el mensaje dice por qué. No se borró nada."""


@dataclass(frozen=True)
class QuienSeQueda:
    id: uuid.UUID
    codigo: str
    nombre: str
    telefonos: int


@dataclass
class Plan:
    se_queda: QuienSeQueda
    usuarios_que_se_van: list[tuple[str, str]] = field(default_factory=list)
    telefonos_que_se_van: int = 0
    camiones: list[str] = field(default_factory=list)
    se_van: dict[str, int] = field(default_factory=dict)
    se_quedan: dict[str, int] = field(default_factory=dict)
    tiendas_con_ubicacion: int = 0


# Lo que se cuenta para decirlo antes, en palabras: (consulta, cómo se dice).
LO_QUE_SE_VA = (
    ("SELECT count(*) FROM ventas", "ventas"),
    ("SELECT count(*) FROM cobros", "cobros"),
    ("SELECT count(*) FROM cargas", "cargas"),
    ("SELECT count(*) FROM liquidaciones", "cortes"),
    ("SELECT count(*) FROM mermas", "mermas"),
    ("SELECT count(*) FROM entradas", "entradas de mercancía"),
    ("SELECT count(*) FROM salidas", "salidas"),
    ("SELECT count(*) FROM movimientos_inventario", "movimientos del almacén"),
    ("SELECT count(*) FROM existencias WHERE cantidad <> 0",
     "existencias que quedan en cero"),
)
LO_QUE_SE_QUEDA = (
    ("SELECT count(*) FROM productos", "artículos"),
    ("SELECT count(*) FROM clientes", "tiendas"),
    ("SELECT count(*) FROM rutas", "rutas"),
    ("SELECT count(*) FROM almacenes WHERE tipo <> 'camion'", "bodegas"),
)


async def planear(sesion, codigo: str = QUEDA_POR_OMISION) -> Plan:
    """Quién se queda y qué se va. No cambia nada."""
    clave = codigo.strip().upper()
    fila = (
        await sesion.execute(
            text("SELECT id, codigo, nombre, rol_codigo, activo FROM usuarios "
                 " WHERE codigo = :c"),
            {"c": clave},
        )
    ).mappings().first()
    if fila is None:
        raise NoSePuede(f"no hay ningún usuario con el código {clave}")
    if fila["rol_codigo"] != "admin" or not fila["activo"]:
        raise NoSePuede(
            f"{clave} no es un administrador activo: quedaría una base a la que nadie "
            "puede entrar a dar de alta lo demás"
        )
    u = {"u": fila["id"]}
    telefonos = (
        await sesion.execute(
            text("SELECT count(*) FROM dispositivos WHERE usuario_id = :u"), u
        )
    ).scalar_one()
    plan = Plan(QuienSeQueda(fila["id"], fila["codigo"], fila["nombre"], telefonos))
    plan.usuarios_que_se_van = [
        (r.codigo, r.nombre)
        for r in await sesion.execute(
            text("SELECT codigo, nombre FROM usuarios WHERE id <> :u ORDER BY codigo"), u
        )
    ]
    plan.telefonos_que_se_van = (
        await sesion.execute(
            text("SELECT count(*) FROM dispositivos WHERE usuario_id <> :u"), u
        )
    ).scalar_one()
    plan.camiones = list(
        (await sesion.execute(
            text("SELECT codigo FROM almacenes WHERE tipo = 'camion' ORDER BY codigo")
        )).scalars()
    )
    for consulta, nombre in LO_QUE_SE_VA:
        plan.se_van[nombre] = (await sesion.execute(text(consulta))).scalar_one()
    for consulta, nombre in LO_QUE_SE_QUEDA:
        plan.se_quedan[nombre] = (await sesion.execute(text(consulta))).scalar_one()
    plan.tiendas_con_ubicacion = (
        await sesion.execute(
            text("SELECT count(*) FROM clientes WHERE lat IS NOT NULL AND lng IS NOT NULL")
        )
    ).scalar_one()
    return plan


async def aplicar(sesion, plan: Plan) -> None:
    """Todo en UNA transacción: o queda así, o no cambia nada."""
    u = {"u": plan.se_queda.id}
    # TRUNCATE espera a que nadie más use esas tablas. Si la API o el worker
    # siguen arriba, mejor fallar con el porqué que colgarse.
    await sesion.execute(text("SET LOCAL lock_timeout = '30s'"))
    try:
        await sesion.execute(text(f"TRUNCATE {', '.join(SE_VACIAN)} RESTART IDENTITY"))
    except DBAPIError as e:
        await sesion.rollback()
        raise NoSePuede(
            "la base está ocupada (¿la API o el worker siguen arriba?). Detenlos con "
            "«docker compose stop api worker» y vuelve a correrlo"
        ) from e

    # Los camiones se sueltan y se van; las bodegas se quedan sin responsable.
    await sesion.execute(
        text("UPDATE usuarios SET almacen_id = NULL, actualizado_en = now() "
             " WHERE almacen_id IS NOT NULL")
    )
    await sesion.execute(text("DELETE FROM almacenes WHERE tipo = 'camion'"))
    await sesion.execute(
        text("UPDATE almacenes SET responsable_id = NULL "
             " WHERE responsable_id IS NOT NULL AND responsable_id <> :u"), u
    )
    # Donde los que se van eran «el titular» o «quién lo capturó»: sin nombre.
    for tabla, columna in (
        ("rutas", "vendedor_id"),
        ("proveedores", "creado_por"),
        ("objetivos", "fijado_por"),
        ("objetivos_diarios", "fijado_por"),
        ("objetivos_ruta", "fijado_por"),
    ):
        await sesion.execute(
            text(f"UPDATE {tabla} SET {columna} = NULL "  # noqa: S608
                 f" WHERE {columna} IS NOT NULL AND {columna} <> :u"),
            u,
        )

    # Del change_log, el catálogo; lo demás se tira (las tiendas, para
    # republicarlas al día justo abajo).
    await sesion.execute(
        text("DELETE FROM change_log "
             " WHERE NOT (entidad = ANY(:quedan) AND ruta_id IS NULL "
             "            AND vendedor_id IS NULL) "
             "   AND NOT (entidad = 'identidad' AND vendedor_id = :u)"),
        {"quedan": list(DELTAS_QUE_SE_QUEDAN), **u},
    )
    # Las tiendas: sin quién ni desde qué teléfono las dieron de alta, sin
    # visitas reprogramadas. El UPDATE publica cada una de nuevo, al día.
    await sesion.execute(
        text(
            "UPDATE clientes SET "
            "  creado_por = CASE WHEN creado_por = :u THEN creado_por END, "
            "  dispositivo_id = CASE WHEN dispositivo_id IN "
            "      (SELECT id FROM dispositivos WHERE usuario_id = :u) "
            "    THEN dispositivo_id END, "
            "  visita_reprogramada = NULL, actualizado_en = now()"
        ),
        u,
    )

    # Los demás usuarios y sus teléfonos.
    await sesion.execute(
        text("UPDATE dispositivos SET borrado_ordenado_por = NULL "
             " WHERE borrado_ordenado_por IS NOT NULL AND borrado_ordenado_por <> :u"), u
    )
    await sesion.execute(text("DELETE FROM dispositivos WHERE usuario_id <> :u"), u)
    await sesion.execute(text("DELETE FROM usuarios_permisos WHERE usuario_id <> :u"), u)
    await sesion.execute(text("DELETE FROM usuarios WHERE id <> :u"), u)

    # Una bodega donde capturar el inventario real, si no quedó ninguna.
    hay_bodega = (
        await sesion.execute(text("SELECT 1 FROM almacenes WHERE tipo = 'bodega' AND activo"))
    ).first()
    if hay_bodega is None:
        await sesion.execute(
            text(
                "INSERT INTO almacenes (id, codigo, nombre, tipo, sucursal_id) "
                "VALUES (:id, 'BODEGA_PRINCIPAL', 'Bodega principal', 'bodega', "
                "        (SELECT id FROM sucursales ORDER BY codigo LIMIT 1))"
            ),
            {"id": uuid.uuid4()},
        )
    # Los folios vuelven a 1. Los códigos de tienda NO: las tiendas se quedan, y
    # la siguiente sigue después de la última.
    for secuencia in SECUENCIAS_DE_FOLIO:
        if secuencia != "seq_codigo_cliente":
            await sesion.execute(text(f"ALTER SEQUENCE {secuencia} RESTART WITH 1"))
    # Los teléfonos del administrador se quedan vinculados, sin el rastro de su
    # sincronización y con la marca de empezar de cero (§90).
    await sesion.execute(
        text(
            "UPDATE dispositivos SET ultimo_cursor_pull = 0, ultima_sync_push_en = NULL, "
            "       ultima_sync_pull_en = NULL, cola_pendiente = NULL, "
            "       cola_reportada_en = NULL, empezar_de_cero = true"
        )
    )
    await sesion.execute(
        text("INSERT INTO auditoria (entidad, entidad_id, accion, usuario_id, datos_despues, "
             "                       motivo) "
             "VALUES ('base', :id, 'para_operar', :u, CAST(:datos AS jsonb), :motivo)"),
        {
            "id": uuid.uuid4(),
            **u,
            "datos": json.dumps(
                {
                    "se_van": plan.se_van,
                    "usuarios": [c for c, _ in plan.usuarios_que_se_van],
                    "telefonos": plan.telefonos_que_se_van,
                    "camiones": plan.camiones,
                    "se_queda": plan.se_queda.codigo,
                },
                ensure_ascii=False,
            ),
            "motivo": "Empezar a operar: se van los usuarios y la operación queda en cero.",
        },
    )
    await sesion.commit()
