"""Dejar la base en blanco para empezar las pruebas con el catálogo real.

────────────────────────────────────────────────────────────────────────────
QUÉ SE QUEDA Y QUÉ SE VA
────────────────────────────────────────────────────────────────────────────
Pedido de la dirección (octubre 2026): «quiero borrar todo a excepción de los
usuarios y sus teléfonos vinculados», y tener el inventario de su Excel.

**Se quedan** los usuarios —con sus roles y permisos— y sus teléfonos
vinculados con su clave. También lo que el sistema necesita para funcionar y
siembran las migraciones: roles y permisos, unidades, canales, sucursales,
listas de precios, motivos de merma y de no-drop, criterios del piloto.

**Se va** todo lo demás: productos y precios, clientes, rutas, bodegas y
camiones, ventas, cargas, cortes, mermas, entradas y salidas, existencias y su
libro mayor, cuentas, objetivos, la bitácora de sincronización y la auditoría.
Los folios vuelven a empezar en 1. Se crea una **Bodega principal** nueva y ahí
entra la existencia de los artículos del Excel.

────────────────────────────────────────────────────────────────────────────
POR QUÉ TRUNCATE Y NO DELETE
────────────────────────────────────────────────────────────────────────────
El libro mayor y la cuenta del vendedor son append-only por disparador de
renglón (0004, 0039): un DELETE se rechaza. TRUNCATE no dispara los de renglón,
y tampoco los que publican deltas, así que vaciar no llena `change_log` de bajas.

Va SIN cascada a propósito: si una tabla que se queda apuntara a una que se va,
PostgreSQL se niega y no se borra nada. Con CASCADE se la llevaría en silencio.

────────────────────────────────────────────────────────────────────────────
LO QUE VEN LOS TELÉFONOS
────────────────────────────────────────────────────────────────────────────
De `change_log` se quitan los deltas de lo que ya no existe y se dejan los de lo
que se queda (lista de precios, motivos, identidad del equipo): un teléfono que
empieza de cero los necesita. Los artículos nuevos se publican solos al darse
de alta.

Un teléfono que ya tenía datos no se entera por los deltas —TRUNCATE no publica
bajas—, así que cada teléfono vinculado queda marcado para **empezar de cero**
(migración 0052, ADR 0002 §90): en su siguiente sincronización entrega lo que
tenga en la cola, olvida lo de antes —tiendas, artículos, camión, ventas— y
vuelve a bajar todo. No hay que borrarle los datos a la app ni volver a
vincularlo.
"""

from __future__ import annotations

import csv
import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api.admin.entradas import abrir_entrada, agregar_renglon_a_entrada, confirmar_entrada
from app.infra.cierre_del_vendedor import bodega_principal

ARTICULOS = (
    Path(__file__).resolve().parents[2] / "db" / "semillas" / "articulos_distribuciones_se.csv"
)

# Lo que se vacía. Hijas antes que padres solo por legibilidad: TRUNCATE de una
# sola sentencia no depende del orden.
TABLAS_QUE_SE_VACIAN = (
    # Ventas, cobros y lo que cuelga de ellas
    "ventas_cancelaciones", "venta_partidas", "cobros_aplicaciones", "cobros",
    "cuentas_por_cobrar", "ventas", "no_drops",
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
    # Sincronización y sesiones (los teléfonos se quedan; su historial no)
    "sync_cuarentena", "sync_operaciones", "sync_lotes", "sync_bajadas",
    "folios_rangos", "sesiones",
    # Piloto, tablero, analítica y cola
    "piloto_incidencias", "piloto_jornadas", "pilotos", "objetivos_ruta",
    "tablero_dia", "tablero_mes_ruta", "tablero_refrescos", "analitica_refrescos",
    "jobs", "auditoria",
)

# Los deltas que se quedan: los de las tablas que se quedan.
ENTIDADES_QUE_SE_QUEDAN = ("lista_precios", "motivo_merma", "motivo_no_drop", "identidad")

# Las series de folios: con la base vacía, vuelven a empezar en 1.
SECUENCIAS_DE_FOLIO = (
    "seq_codigo_cliente", "seq_folio_ajuste_camion", "seq_folio_carga",
    "seq_folio_entrada", "seq_folio_liquidacion", "seq_folio_salida",
    "seq_folio_traspaso", "seq_folio_venta",
)

PALABRA = "EN BLANCO"


class ArticulosInvalidos(Exception):
    """No se puede poner en blanco, y el mensaje dice por qué. No se borró nada."""


@dataclass(frozen=True)
class Articulo:
    sku: str
    nombre: str
    familia: str
    existencia: Decimal
    precio: Decimal


@dataclass
class Resultado:
    borrado: dict[str, int] = field(default_factory=dict)
    articulos: int = 0
    familias: int = 0
    piezas: Decimal = Decimal(0)
    entrada: str | None = None
    aviso_inventario: str | None = None


def leer_articulos(ruta: Path = ARTICULOS) -> list[Articulo]:
    """Los artículos del archivo, revisados antes de borrar nada."""
    articulos: list[Articulo] = []
    vistos: set[str] = set()
    with ruta.open(encoding="utf-8", newline="") as f:
        for n, fila in enumerate(csv.DictReader(f), start=2):
            sku = "".join((fila.get("sku") or "").split()).upper()
            nombre = " ".join((fila.get("nombre") or "").split())
            familia = " ".join((fila.get("familia") or "").split())
            if not sku or not nombre:
                raise ArticulosInvalidos(f"Renglón {n}: falta el SKU o el nombre.")
            if sku in vistos:
                raise ArticulosInvalidos(f"Renglón {n}: el SKU {sku} viene dos veces.")
            vistos.add(sku)
            try:
                existencia = Decimal((fila.get("existencia") or "0").strip())
                precio = Decimal((fila.get("precio") or "").strip())
            except ArithmeticError as e:
                raise ArticulosInvalidos(f"Renglón {n} ({sku}): número mal escrito.") from e
            if existencia < 0 or existencia != existencia.to_integral_value():
                raise ArticulosInvalidos(
                    f"Renglón {n} ({sku}): la existencia va en piezas enteras."
                )
            if precio <= 0:
                raise ArticulosInvalidos(f"Renglón {n} ({sku}): falta el precio de venta.")
            articulos.append(Articulo(sku, nombre, familia, existencia, precio))
    if not articulos:
        raise ArticulosInvalidos("El archivo no trae ningún artículo.")
    return articulos


async def lo_que_se_borra(sesion) -> dict[str, int]:
    """Cuántos renglones tiene hoy cada cosa que se va, para decirlo antes."""
    cuantos = {}
    for tabla in ("ventas", "clientes", "productos", "rutas", "almacenes", "cargas",
                  "cortes_vendedor", "liquidaciones", "mermas", "entradas",
                  "movimientos_inventario"):
        cuantos[tabla] = (
            await sesion.execute(text(f"SELECT count(*) FROM {tabla}"))
        ).scalar_one()
    return cuantos


async def lo_que_se_queda(sesion) -> dict[str, int]:
    cuantos = {}
    for tabla in ("usuarios", "dispositivos"):
        cuantos[tabla] = (
            await sesion.execute(text(f"SELECT count(*) FROM {tabla}"))
        ).scalar_one()
    return cuantos


async def poner_en_blanco(
    sesion, articulos: list[Articulo], *, quien: uuid.UUID | None
) -> Resultado:
    """Vacía la base, da de alta los artículos y les pone su existencia.

    El borrado y el alta del catálogo van en UNA transacción: o queda la base en
    blanco con sus artículos, o no cambia nada. La existencia entra después como
    una entrada de «Inventario inicial» a la bodega principal, con las funciones
    de Entradas de siempre (libro mayor, existencias, folio `EN-`).
    """
    resultado = Resultado(borrado=await lo_que_se_borra(sesion))

    # TRUNCATE espera a que nadie más esté usando esas tablas. Si algo se quedó
    # con una transacción abierta, mejor fallar con el porqué que colgarse.
    await sesion.execute(text("SET LOCAL lock_timeout = '30s'"))
    try:
        await sesion.execute(
            text(f"TRUNCATE {', '.join(TABLAS_QUE_SE_VACIAN)} RESTART IDENTITY")
        )
    except DBAPIError as e:
        await sesion.rollback()
        raise ArticulosInvalidos(
            "la base está ocupada (¿la API o el worker siguen arriba?). Detenlos con "
            "«docker compose stop api worker» y vuelve a correrlo"
        ) from e
    # Rutas, bodegas y camiones: los usuarios cuelgan de ellos, así que primero
    # se sueltan —eso publica a cada teléfono que ya no tiene camión— y luego se
    # borran con DELETE (TRUNCATE no se puede: `usuarios` las apunta).
    await sesion.execute(
        text("UPDATE usuarios SET almacen_id = NULL, actualizado_en = now() "
             " WHERE almacen_id IS NOT NULL")
    )
    await sesion.execute(text("DELETE FROM usuarios_rutas"))
    await sesion.execute(
        text("DELETE FROM change_log WHERE entidad <> ALL(:quedan) OR ruta_id IS NOT NULL"),
        {"quedan": list(ENTIDADES_QUE_SE_QUEDAN)},
    )
    await sesion.execute(text("DELETE FROM rutas"))
    await sesion.execute(text("DELETE FROM almacenes"))
    # La bodega donde entra el inventario del Excel.
    await sesion.execute(
        text(
            "INSERT INTO almacenes (id, codigo, nombre, tipo, sucursal_id) "
            "VALUES (:id, 'BODEGA_PRINCIPAL', 'Bodega principal', 'bodega', "
            "        (SELECT id FROM sucursales ORDER BY codigo LIMIT 1))"
        ),
        {"id": uuid.uuid4()},
    )
    for secuencia in SECUENCIAS_DE_FOLIO:
        await sesion.execute(text(f"ALTER SEQUENCE {secuencia} RESTART WITH 1"))
    # Los teléfonos se quedan vinculados, sin el rastro de su sincronización, y
    # con la marca de empezar de cero: en su próximo pull olvidan lo de antes.
    await sesion.execute(
        text(
            "UPDATE dispositivos SET ultimo_cursor_pull = 0, ultima_sync_push_en = NULL, "
            "       ultima_sync_pull_en = NULL, cola_pendiente = NULL, "
            "       cola_reportada_en = NULL, empezar_de_cero = true"
        )
    )

    lista = (
        await sesion.execute(
            text("SELECT id FROM listas_precios WHERE activo "
                 " ORDER BY es_default DESC, vigente_desde LIMIT 1")
        )
    ).scalar_one_or_none()
    if lista is None:
        raise ArticulosInvalidos("No hay ninguna lista de precios activa donde poner los precios.")

    familias: dict[str, uuid.UUID] = {}
    for orden, nombre in enumerate(dict.fromkeys(a.familia for a in articulos if a.familia)):
        familias[nombre] = uuid.uuid4()
        await sesion.execute(
            text("INSERT INTO categorias (id, codigo, nombre, orden, activo) "
                 "VALUES (:id, :codigo, :nombre, :orden, true)"),
            {"id": familias[nombre], "codigo": _codigo(nombre), "nombre": nombre,
             "orden": orden + 1},
        )
    for a in articulos:
        producto = uuid.uuid4()
        await sesion.execute(
            text("INSERT INTO productos (id, sku, nombre, categoria_id, unidad_base, tasa_iva) "
                 "VALUES (:id, :sku, :nombre, :familia, 'PZA', 0)"),
            {"id": producto, "sku": a.sku, "nombre": a.nombre[:200],
             "familia": familias.get(a.familia)},
        )
        await sesion.execute(
            text("INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, es_default) "
                 "VALUES (:p, 'PZA', 1, true)"),
            {"p": producto},
        )
        await sesion.execute(
            text("INSERT INTO precios (lista_id, producto_id, unidad_codigo, precio, version) "
                 "VALUES (:l, :p, 'PZA', :precio, 1)"),
            {"l": lista, "p": producto, "precio": a.precio},
        )
    await sesion.execute(
        text("INSERT INTO auditoria (entidad, entidad_id, accion, usuario_id, datos_despues, "
             "                       motivo) "
             "VALUES ('base', :id, 'en_blanco', :quien, CAST(:datos AS jsonb), :motivo)"),
        {
            "id": uuid.uuid4(),
            "quien": quien,
            "datos": json.dumps(
                {"borrado": resultado.borrado, "articulos": len(articulos)}, ensure_ascii=False
            ),
            "motivo": "Base en blanco para empezar las pruebas con el catálogo real.",
        },
    )
    await sesion.commit()
    resultado.articulos = len(articulos)
    resultado.familias = len(familias)

    con_existencia = [a for a in articulos if a.existencia > 0]
    bodega = await bodega_principal(sesion)
    if not con_existencia:
        return resultado
    if bodega is None:
        resultado.aviso_inventario = (
            "No hay ninguna bodega activa: los artículos quedaron sin existencia. "
            "Da de alta la bodega y captura el inventario inicial en Entradas."
        )
        return resultado
    if quien is None:
        resultado.aviso_inventario = (
            "No hay ningún usuario de oficina activo a cuyo nombre registrar el "
            "inventario inicial; captúralo en Entradas."
        )
        return resultado

    entrada = await abrir_entrada(
        sesion,
        almacen_destino_id=str(bodega["id"]),
        motivo="inicial",
        nota="Inventario inicial del archivo de artículos (base en blanco).",
        quien=quien,
    )
    for a in con_existencia:
        await agregar_renglon_a_entrada(
            sesion, entrada, producto=a.sku, unidad_codigo="PZA",
            cantidad=format(a.existencia, "f"),
        )
    await confirmar_entrada(sesion, entrada, quien=quien)
    resultado.entrada = (
        await sesion.execute(text("SELECT folio FROM entradas WHERE id = :e"), {"e": entrada})
    ).scalar_one()
    resultado.piezas = sum((a.existencia for a in con_existencia), Decimal(0))
    resultado.aviso_inventario = f"en {bodega['nombre']}"
    return resultado


async def limpiar_el_laboratorio(sesion) -> str | None:
    """Recalcula las vistas del laboratorio de análisis con la base ya vacía.

    Son vistas materializadas (migración 0020): guardan su propia copia y no se
    enteran del TRUNCATE hasta que se recalculan. Sin esto el laboratorio
    seguiría enseñando las ventas de antes hasta el refresco de la noche.
    Devuelve el problema, si hubo; no detiene nada: la base ya quedó en blanco.
    """
    from app.workers.analitica import refrescar_todo

    try:
        await refrescar_todo(sesion)
    except RuntimeError as e:
        return str(e)
    return None


async def quien_registra(sesion) -> uuid.UUID | None:
    """El usuario de oficina a cuyo nombre queda el inventario inicial."""
    return (
        await sesion.execute(
            text(
                "SELECT id FROM usuarios WHERE activo AND rol_codigo IN ('admin', 'gerente') "
                " ORDER BY rol_codigo = 'admin' DESC, codigo LIMIT 1"
            )
        )
    ).scalar_one_or_none()


def _codigo(nombre: str) -> str:
    sin_acentos = nombre.upper().translate(str.maketrans("ÁÉÍÓÚÜÑ", "AEIOUUN"))
    return "-".join("".join(c if c.isalnum() else " " for c in sin_acentos).split())[:40]

