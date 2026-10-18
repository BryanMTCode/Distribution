"""La base en blanco para empezar a operar: solo queda un administrador (ADR 0002 §131).

Pedido de la dirección (octubre 2026): «quiero formatear toda la base de datos
como si no hubiera nada, porque ya empezaré a operar… Solo dejaré vivo el
usuario ADMIN01». A la pregunta de qué se queda: **solo ese usuario y sus
teléfonos**; el inventario **en cero**, para capturar el real.

────────────────────────────────────────────────────────────────────────────
QUÉ SE QUEDA Y QUÉ SE VA
────────────────────────────────────────────────────────────────────────────
**Se queda** el administrador que se nombra (ADMIN01), con sus permisos y sus
teléfonos vinculados, y lo que el sistema necesita para funcionar y siembran las
migraciones: roles y permisos, unidades, canales, sucursales, listas de precios
(sin precios), motivos de merma y de no-drop, criterios del piloto, el
calendario de la analítica, y las marcas de la Revisión de la app.

**Se va todo lo demás**: los demás usuarios con sus teléfonos, artículos y
precios, familias, clientes, rutas, bodegas y camiones, proveedores, ventas,
cobros, cargas, cortes, mermas, entradas y salidas, existencias y su libro
mayor, cuentas de los vendedores, objetivos, el tablero, la bitácora de
sincronización, las sesiones y la auditoría. Los folios vuelven a empezar en 1 y
los códigos apartados de usuarios (§132) se liberan: sin folios no hay qué
proteger.

Queda una **Bodega principal vacía**, para capturar el inventario real en
Almacén → Entradas → «Inventario inicial».

A diferencia de `base-en-blanco` (§90, la de las pruebas), aquí no se carga
ningún artículo: el catálogo se da de alta en el panel, o con sus comandos.

────────────────────────────────────────────────────────────────────────────
NINGUNA TABLA SIN DECIDIR
────────────────────────────────────────────────────────────────────────────
Cada tabla del esquema está en una de las cuatro listas de abajo, y una prueba
lo revisa contra la base: una migración que agregue una tabla hace fallar la
prueba hasta que alguien decida si se queda o se va. Así este comando no se
queda viejo en silencio; en `base-en-blanco` nada avisaba de una tabla nueva.

TRUNCATE y no DELETE por lo mismo que allá: el libro mayor y la cuenta del
vendedor rechazan DELETE por disparador, y TRUNCATE no llena `change_log` de
bajas. Sin CASCADE: si una tabla que se queda apuntara a una que se va,
PostgreSQL se niega y no se borra nada.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infra.base_en_blanco import ENTIDADES_QUE_SE_QUEDAN, SECUENCIAS_DE_FOLIO
from app.infra.codigo_de_cliente import PRIMER_CODIGO_NUEVO

PALABRA = "OPERAR"
QUEDA_POR_OMISION = "ADMIN01"

# Se quedan enteras: las siembran las migraciones, o no son de la operación.
SE_QUEDAN = (
    "alembic_version", "spatial_ref_sys",
    "roles", "permisos", "roles_permisos",
    "unidades_medida", "canales", "sucursales", "listas_precios",
    "motivos_merma", "motivos_no_drop", "piloto_criterios",
    "dim_tiempo", "sync_retencion",
    # Las marcas de la Revisión de la app (§112): son de la app, no del negocio.
    "revision_app",
)

# Se quedan solo en lo que es del administrador que se queda.
SE_QUEDAN_EN_PARTE = ("usuarios", "usuarios_permisos", "dispositivos", "change_log")

# Se borran con DELETE porque una tabla que se queda las apunta (`usuarios` a
# su camión, `change_log` a la ruta): primero se sueltan y luego se borran.
SE_BORRAN_A_MANO = ("rutas", "almacenes")

SE_VACIAN = (
    # Ventas, cobros y lo que cuelga de ellas
    "venta_mayoreo_partidas", "ventas_mayoreo",
    "ventas_cancelaciones", "venta_partidas", "cobros_aplicaciones", "cobros",
    "cuentas_por_cobrar", "ventas", "no_drops", "anulaciones",
    # El cierre del vendedor y el corte
    "solicitud_carga_detalle", "solicitudes_carga", "corte_vendedor_conteo",
    "cortes_vendedor", "liquidacion_detalle", "liquidaciones", "cuenta_vendedor",
    # Movimiento de mercancía
    "carga_detalle", "cargas", "merma_detalle", "mermas", "traspaso_detalle",
    "traspasos", "ajustes_camion", "salida_detalle", "salidas",
    "pagos_proveedor", "cuentas_por_pagar", "entrada_detalle", "entradas",
    "existencias", "movimientos_inventario",
    # Catálogo
    "promociones", "precios", "producto_costos", "producto_unidades", "productos",
    "categorias", "marcas", "proveedores",
    "clientes_posibles_duplicados", "clientes_frecuencia", "clientes",
    # Rutas de los usuarios, y los códigos apartados (§132)
    "usuarios_rutas", "usuarios_codigos_anteriores",
    # Sincronización y sesiones
    "sync_cuarentena", "sync_operaciones", "sync_lotes", "sync_bajadas",
    "folios_rangos", "sesiones",
    # Piloto, objetivos, tablero, analítica, cola y auditoría
    "piloto_incidencias", "piloto_jornadas", "pilotos", "objetivos_ruta", "objetivos",
    "objetivos_diarios",
    "tablero_dia", "tablero_mes_ruta", "tablero_refrescos", "analitica_refrescos",
    "jobs", "auditoria",
)


class NoSePuede(Exception):
    """No se puede poner en blanco, y el mensaje dice por qué. No se borró nada."""


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
    cuantos: dict[str, int] = field(default_factory=dict)
    # Las ubicaciones que capturaron los vendedores viven en las tiendas: se van
    # con ellas, y el Excel de rutas no las trae. Se dice antes.
    tiendas_con_ubicacion: int = 0


# Lo que se cuenta para decirlo antes, en palabras.
LO_QUE_SE_CUENTA = (
    ("ventas", "ventas"), ("cobros", "cobros"), ("cargas", "cargas"),
    ("liquidaciones", "cortes"), ("mermas", "mermas"), ("entradas", "entradas"),
    ("salidas", "salidas"), ("productos", "artículos"), ("clientes", "tiendas"),
    ("rutas", "rutas"), ("almacenes", "bodegas y camiones"),
    ("movimientos_inventario", "movimientos del almacén"),
)


async def planear(sesion, codigo: str = QUEDA_POR_OMISION) -> Plan:
    """Quién se queda y cuánto se va. No cambia nada."""
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
    telefonos = (
        await sesion.execute(
            text("SELECT count(*) FROM dispositivos WHERE usuario_id = :u"),
            {"u": fila["id"]},
        )
    ).scalar_one()
    plan = Plan(QuienSeQueda(fila["id"], fila["codigo"], fila["nombre"], telefonos))
    plan.usuarios_que_se_van = [
        (r.codigo, r.nombre)
        for r in (
            await sesion.execute(
                text("SELECT codigo, nombre FROM usuarios WHERE id <> :u ORDER BY codigo"),
                {"u": fila["id"]},
            )
        )
    ]
    plan.telefonos_que_se_van = (
        await sesion.execute(
            text("SELECT count(*) FROM dispositivos WHERE usuario_id <> :u"),
            {"u": fila["id"]},
        )
    ).scalar_one()
    for tabla, _ in LO_QUE_SE_CUENTA:
        plan.cuantos[tabla] = (
            await sesion.execute(text(f"SELECT count(*) FROM {tabla}"))  # noqa: S608
        ).scalar_one()
    plan.tiendas_con_ubicacion = (
        await sesion.execute(
            text("SELECT count(*) FROM clientes WHERE lat IS NOT NULL AND lng IS NOT NULL")
        )
    ).scalar_one()
    return plan


async def aplicar(sesion, plan: Plan) -> None:
    """Pone la base en blanco. Todo en UNA transacción: o queda así, o no cambia nada."""
    queda = plan.se_queda.id
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

    # Los camiones se sueltan antes de borrarse; eso publica deltas de identidad,
    # que se limpian en el paso siguiente junto con todo lo que ya no existe.
    await sesion.execute(
        text("UPDATE usuarios SET almacen_id = NULL, actualizado_en = now() "
             " WHERE almacen_id IS NOT NULL")
    )
    await sesion.execute(
        text("DELETE FROM change_log "
             " WHERE NOT (entidad = ANY(:quedan) AND ruta_id IS NULL "
             "            AND (vendedor_id IS NULL OR vendedor_id = :u))"),
        {"quedan": list(ENTIDADES_QUE_SE_QUEDAN), "u": queda},
    )
    await sesion.execute(text("DELETE FROM rutas"))
    await sesion.execute(text("DELETE FROM almacenes"))

    # Los demás usuarios y sus teléfonos.
    await sesion.execute(
        text("UPDATE dispositivos SET borrado_ordenado_por = NULL "
             " WHERE borrado_ordenado_por IS NOT NULL AND borrado_ordenado_por <> :u"),
        {"u": queda},
    )
    await sesion.execute(text("DELETE FROM dispositivos WHERE usuario_id <> :u"), {"u": queda})
    await sesion.execute(
        text("DELETE FROM usuarios_permisos WHERE usuario_id <> :u"), {"u": queda}
    )
    await sesion.execute(text("DELETE FROM usuarios WHERE id <> :u"), {"u": queda})

    # La bodega vacía donde entra el inventario real.
    await sesion.execute(
        text(
            "INSERT INTO almacenes (id, codigo, nombre, tipo, sucursal_id) "
            "VALUES (:id, 'BODEGA_PRINCIPAL', 'Bodega principal', 'bodega', "
            "        (SELECT id FROM sucursales ORDER BY codigo LIMIT 1))"
        ),
        {"id": uuid.uuid4()},
    )
    for secuencia in SECUENCIAS_DE_FOLIO:
        # Los códigos de tienda nuevos siguen en 1934: del 100 al 1933 quedan
        # libres para las tiendas de los Excel de rutas, si se vuelven a cargar
        # con su comando (ADR 0002 §122).
        inicio = PRIMER_CODIGO_NUEVO if secuencia == "seq_codigo_cliente" else 1
        await sesion.execute(text(f"ALTER SEQUENCE {secuencia} RESTART WITH {inicio}"))
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
             "VALUES ('base', :id, 'para_operar', :quien, CAST(:datos AS jsonb), :motivo)"),
        {
            "id": uuid.uuid4(),
            "quien": queda,
            "datos": json.dumps(
                {
                    "borrado": plan.cuantos,
                    "usuarios": [c for c, _ in plan.usuarios_que_se_van],
                    "telefonos": plan.telefonos_que_se_van,
                    "se_queda": plan.se_queda.codigo,
                },
                ensure_ascii=False,
            ),
            "motivo": "Base en blanco para empezar a operar.",
        },
    )
    await sesion.commit()
