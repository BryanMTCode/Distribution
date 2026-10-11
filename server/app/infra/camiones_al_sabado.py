"""Dejar los camiones de las rutas 1101–1106 como quedaron el sábado 10.

────────────────────────────────────────────────────────────────────────────
DE DÓNDE SALE
────────────────────────────────────────────────────────────────────────────
Pedido de la dirección (octubre 2026, ADR 0002 §99), con su Excel de una hoja
por ruta: «vas a dejar los datos según lo que viene en el excel». Por cada
artículo la hoja trae lo que el camión tenía el sábado 10 (INVENTARIO), la carga
que se le hizo en la tarde (PIEZAS), lo que quedó (TOTAL) y el PRECIO, que es el
verdadero: «modifícalos en dado caso porque estos precios son los verdaderos».

El Excel ya pasado a limpio está en `db/semillas/camiones_2026-10-10.csv`: un
renglón por ruta y artículo, con el nombre tal como venía en la hoja y el SKU
del catálogo al que corresponde (los nombres no coinciden: «MIINO 15 KILOS» es
el costal Minino S-108, «ANDY MAX» es la Candy box F-100…). Siete artículos de
la hoja no estaban en el catálogo; se dan de alta con los datos de
`articulos_distribuciones_se.csv`, donde ya se agregaron.

────────────────────────────────────────────────────────────────────────────
QUÉ HACE
────────────────────────────────────────────────────────────────────────────
Todo en UNA transacción —o queda todo, o nada—:

1. Revisa que existan los seis vendedores (VEND1101…VEND1106). Si falta uno no
   toca nada y dice cuál.
2. Da de alta los artículos que falten, con su familia.
3. Pone en la lista de precios general el precio de la hoja, en pieza. Si un
   artículo viene con dos precios en hojas distintas, gana el que más se repite
   y se avisa cuál fue la excepción.
4. Le da camión al vendedor que no lo tenga, igual que el alta del panel.
5. Deja cada artículo del camión en el TOTAL de la hoja con un ajuste de conteo
   (folio AC-), con la cuenta en la nota: «inventario 4 + carga 6 = 10». Lo que
   el camión tenga y no venga en la hoja queda en cero.

Correrlo dos veces no duplica nada: el ajuste es «lo que hay contra lo que debe
haber», y si ya cuadra no se escribe.

────────────────────────────────────────────────────────────────────────────
POR QUÉ LA BODEGA NO SE TOCA
────────────────────────────────────────────────────────────────────────────
La carga de la tarde salió de la bodega, pero el inventario de la bodega que se
cargó con la base en blanco ya era el de DESPUÉS de cargar: traía cero salsas y
los camiones se llevaron 96 esa tarde. Descontarla otra vez la dejaría negativa.
Por eso la carga no es una carga del sistema (no hay folio CA-): es parte del
estado del camión al cierre del sábado.

────────────────────────────────────────────────────────────────────────────
LO QUE VE EL TELÉFONO
────────────────────────────────────────────────────────────────────────────
Cada ajuste viaja al teléfono del vendedor como diferencia firmada (migración
0032) y el camión nuevo como cambio de identidad (0035): en su siguiente
sincronización el teléfono queda igual que el servidor, sin borrar nada.
"""

from __future__ import annotations

import csv
import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from app.infra.ajuste_de_existencia import existencia_con_candado, registrar_ajuste
from app.infra.base_en_blanco import Articulo, leer_articulos

CAMIONES = (
    Path(__file__).resolve().parents[2] / "db" / "semillas" / "camiones_2026-10-10.csv"
)

# El momento al que corresponde la hoja. Lo que el camión haya movido después
# de esa medianoche se avisa: el ajuste lo deja igual al Excel de todos modos.
FECHA = "sábado 10 de octubre de 2026"
DESPUES_DEL_SABADO = "2026-10-11 00:00"
ZONA = "America/Mazatlan"

PALABRA = "CAMIONES"

# Así empieza la nota de cada ajuste: es lo que distingue los de este comando de
# los demás al revisar qué movió el camión después del sábado.
INICIO_DE_NOTA = f"Estado del camión al {FECHA}"


class HojaInvalida(Exception):
    """No se cargó nada, y el mensaje dice por qué."""


@dataclass(frozen=True)
class Renglon:
    ruta: str
    vendedor: str
    producto_excel: str
    sku: str
    inventario: Decimal
    piezas: Decimal
    total: Decimal
    precio: Decimal


@dataclass(frozen=True)
class CambioDePrecio:
    sku: str
    nombre: str
    antes: Decimal | None
    despues: Decimal


@dataclass
class Plan:
    """Lo que se haría, para decirlo antes de hacerlo."""

    faltan_vendedores: list[str] = field(default_factory=list)
    no_son_vendedores: list[str] = field(default_factory=list)
    articulos_nuevos: list[Articulo] = field(default_factory=list)
    precios: list[CambioDePrecio] = field(default_factory=list)
    precios_distintos: list[str] = field(default_factory=list)
    camiones_nuevos: list[str] = field(default_factory=list)
    # Por vendedor: cuántos artículos cambian, y las piezas y el dinero al final.
    ajustes: dict[str, int] = field(default_factory=dict)
    piezas: dict[str, Decimal] = field(default_factory=dict)
    valor: dict[str, Decimal] = field(default_factory=dict)
    movidos_despues: dict[str, int] = field(default_factory=dict)

    @property
    def se_puede(self) -> bool:
        return not self.faltan_vendedores and not self.no_son_vendedores


@dataclass
class Resultado:
    articulos_nuevos: int = 0
    precios: int = 0
    camiones_nuevos: int = 0
    ajustes: int = 0
    folios: list[str] = field(default_factory=list)


def leer_camiones(ruta: Path = CAMIONES) -> list[Renglon]:
    """Los renglones de la hoja, revisados antes de tocar nada."""
    renglones: list[Renglon] = []
    vistos: set[tuple[str, str]] = set()
    with ruta.open(encoding="utf-8", newline="") as f:
        for n, fila in enumerate(csv.DictReader(f), start=2):
            vendedor = (fila.get("vendedor") or "").strip().upper()
            sku = "".join((fila.get("sku") or "").split()).upper()
            if not vendedor or not sku:
                raise HojaInvalida(f"Renglón {n}: falta el vendedor o el SKU.")
            if (vendedor, sku) in vistos:
                raise HojaInvalida(f"Renglón {n}: {sku} viene dos veces para {vendedor}.")
            vistos.add((vendedor, sku))
            try:
                numeros = {
                    c: Decimal((fila.get(c) or "").strip())
                    for c in ("inventario", "piezas", "total", "precio")
                }
            except ArithmeticError as e:
                raise HojaInvalida(f"Renglón {n} ({vendedor} {sku}): número mal escrito.") from e
            if any(numeros[c] < 0 for c in ("inventario", "piezas", "total")):
                raise HojaInvalida(f"Renglón {n} ({vendedor} {sku}): hay una cantidad negativa.")
            # La cuenta de la hoja tiene que cuadrar: si no, no se sabe cuál de
            # las tres cifras está mal y no se adivina.
            if numeros["inventario"] + numeros["piezas"] != numeros["total"]:
                raise HojaInvalida(
                    f"Renglón {n} ({vendedor} {sku}): inventario {numeros['inventario']} + "
                    f"carga {numeros['piezas']} no da el total {numeros['total']}."
                )
            if numeros["precio"] <= 0:
                raise HojaInvalida(f"Renglón {n} ({vendedor} {sku}): falta el precio.")
            renglones.append(
                Renglon(
                    ruta=(fila.get("ruta") or "").strip(),
                    vendedor=vendedor,
                    producto_excel=" ".join((fila.get("producto_excel") or "").split()),
                    sku=sku,
                    **numeros,
                )
            )
    if not renglones:
        raise HojaInvalida("El archivo no trae ningún renglón.")
    return renglones


def precio_de_cada_articulo(renglones: list[Renglon]) -> tuple[dict[str, Decimal], list[str]]:
    """El precio de cada SKU —el que más se repite entre las hojas— y los avisos
    de las hojas que traen otro."""
    por_sku: dict[str, Counter] = {}
    for r in renglones:
        por_sku.setdefault(r.sku, Counter())[r.precio] += 1
    precios: dict[str, Decimal] = {}
    avisos: list[str] = []
    for sku, cuenta in por_sku.items():
        # A igual número de hojas, el precio más alto: es el de la lista, y el
        # otro el descuento de una ruta.
        precio = max(cuenta, key=lambda p: (cuenta[p], p))
        precios[sku] = precio
        for r in renglones:
            if r.sku == sku and r.precio != precio:
                avisos.append(
                    f"{sku} ({r.producto_excel}) viene a ${r.precio} en la ruta {r.ruta}; "
                    f"queda a ${precio}, como en las demás"
                )
    return precios, avisos


def vendedores_de(renglones: list[Renglon]) -> list[str]:
    return list(dict.fromkeys(r.vendedor for r in renglones))


async def planear(
    sesion, renglones: list[Renglon], articulos: list[Articulo] | None = None
) -> Plan:
    """Lo que haría `aplicar` con la base como está ahora. No escribe nada."""
    articulos = articulos if articulos is not None else leer_articulos()
    plan = Plan()
    usuarios = await _vendedores(sesion, vendedores_de(renglones))
    for codigo in vendedores_de(renglones):
        u = usuarios.get(codigo)
        if u is None:
            plan.faltan_vendedores.append(codigo)
        elif u["rol_codigo"] != "vendedor" or not u["activo"]:
            plan.no_son_vendedores.append(codigo)

    precios, plan.precios_distintos = precio_de_cada_articulo(renglones)
    del_catalogo = await _productos(sesion, list(precios))
    del_archivo = {a.sku: a for a in articulos}
    for sku in precios:
        if sku in del_catalogo:
            continue
        if sku not in del_archivo:
            raise HojaInvalida(
                f"{sku} no está en el catálogo ni en el archivo de artículos: agrégalo a "
                "articulos_distribuciones_se.csv con su nombre y su familia."
            )
        plan.articulos_nuevos.append(del_archivo[sku])
    for sku, precio in precios.items():
        p = del_catalogo.get(sku)
        if p is None:
            continue
        if p["precio"] != precio:
            plan.precios.append(CambioDePrecio(sku, p["nombre"], p["precio"], precio))

    for codigo in vendedores_de(renglones):
        u = usuarios.get(codigo)
        hoja = [r for r in renglones if r.vendedor == codigo]
        plan.piezas[codigo] = sum((r.total for r in hoja), Decimal(0))
        plan.valor[codigo] = sum((r.total * precios[r.sku] for r in hoja), Decimal(0))
        camion = u["almacen_id"] if u and u["almacen_tipo"] == "camion" else None
        if u is not None and camion is None:
            plan.camiones_nuevos.append(codigo)
        hoy = await _existencias(sesion, camion) if camion else {}
        cambian = sum(1 for r in hoja if hoy.get(r.sku, Decimal(0)) != r.total)
        en_la_hoja = {r.sku for r in hoja}
        cambian += sum(1 for sku, c in hoy.items() if sku not in en_la_hoja and c != 0)
        plan.ajustes[codigo] = cambian
        if camion:
            plan.movidos_despues[codigo] = await _movimientos_despues(sesion, camion)
    return plan


async def aplicar(
    sesion,
    renglones: list[Renglon],
    *,
    quien: uuid.UUID | None,
    articulos: list[Articulo] | None = None,
) -> Resultado:
    """Deja los camiones como la hoja, en una sola transacción, y hace commit."""
    articulos = articulos if articulos is not None else leer_articulos()
    if quien is None:
        raise HojaInvalida(
            "No hay ningún usuario de oficina activo (admin o gerente) a cuyo nombre "
            "registrar los ajustes."
        )
    plan = await planear(sesion, renglones, articulos)
    if plan.faltan_vendedores:
        raise HojaInvalida(
            "No existen estos vendedores: " + ", ".join(plan.faltan_vendedores)
            + ". Dalos de alta en Usuarios y rutas con esa clave."
        )
    if plan.no_son_vendedores:
        raise HojaInvalida(
            "Estos usuarios no son vendedores activos: " + ", ".join(plan.no_son_vendedores)
            + ". Un camión solo lo trae un vendedor activo."
        )

    resultado = Resultado()
    lista = (
        await sesion.execute(
            text("SELECT id FROM listas_precios WHERE activo "
                 " ORDER BY es_default DESC, vigente_desde LIMIT 1")
        )
    ).scalar_one_or_none()
    if lista is None:
        raise HojaInvalida("No hay ninguna lista de precios activa donde poner los precios.")
    precios, _ = precio_de_cada_articulo(renglones)

    for a in plan.articulos_nuevos:
        await _dar_de_alta(sesion, a, lista=lista, precio=precios[a.sku])
        resultado.articulos_nuevos += 1
    for cambio in plan.precios:
        await sesion.execute(
            text(
                """
                INSERT INTO precios (lista_id, producto_id, unidad_codigo, precio,
                                     version, actualizado_en)
                SELECT :l, id, 'PZA', :precio, 1, now() FROM productos WHERE sku = :sku
                ON CONFLICT (lista_id, producto_id, unidad_codigo) DO UPDATE
                   SET precio = excluded.precio,
                       version = precios.version + 1,
                       actualizado_en = now()
                 WHERE precios.precio <> excluded.precio
                """
            ),
            {"l": lista, "sku": cambio.sku, "precio": cambio.despues},
        )
        resultado.precios += 1

    productos = {
        sku: p["id"] for sku, p in (await _productos(sesion, list(precios))).items()
    }
    usuarios = await _vendedores(sesion, vendedores_de(renglones))
    for codigo in vendedores_de(renglones):
        u = usuarios[codigo]
        camion = u["almacen_id"] if u["almacen_tipo"] == "camion" else None
        if camion is None:
            camion = await _darle_camion(sesion, u)
            resultado.camiones_nuevos += 1
        hoja = {r.sku: r for r in renglones if r.vendedor == codigo}
        # Primero lo de la hoja, en su orden; luego lo que sobra en el camión.
        objetivo: list[tuple[uuid.UUID, Decimal, str]] = [
            (
                productos[r.sku],
                r.total,
                f"{INICIO_DE_NOTA} (Excel de cargas, ruta {r.ruta}, «{r.producto_excel}»): "
                f"inventario {_n(r.inventario)} + carga de la tarde {_n(r.piezas)} = "
                f"{_n(r.total)}.",
            )
            for r in hoja.values()
        ]
        for sku, cantidad in (await _existencias(sesion, camion)).items():
            if sku not in hoja and cantidad != 0:
                objetivo.append(
                    (
                        await _producto_id(sesion, sku),
                        Decimal(0),
                        f"{INICIO_DE_NOTA}: no viene en la hoja de la ruta, queda en 0.",
                    )
                )
        for producto_id, total, nota in objetivo:
            actual = await existencia_con_candado(sesion, camion, producto_id)
            delta = total - actual
            if delta == 0:
                continue
            folio = await registrar_ajuste(
                sesion,
                almacen_id=camion,
                producto_id=producto_id,
                es_camion=True,
                tipo="conteo",
                actual=actual,
                delta=delta,
                contado=total,
                nota=nota,
                quien=quien,
            )
            resultado.ajustes += 1
            resultado.folios.append(folio)

    await sesion.execute(
        text("INSERT INTO auditoria (entidad, entidad_id, accion, usuario_id, datos_despues, "
             "                       motivo) "
             "VALUES ('camiones', :id, 'al_sabado', :quien, CAST(:datos AS jsonb), :motivo)"),
        {
            "id": uuid.uuid4(),
            "quien": quien,
            "datos": json.dumps(
                {
                    "vendedores": vendedores_de(renglones),
                    "articulos_nuevos": [a.sku for a in plan.articulos_nuevos],
                    "precios": [
                        {"sku": c.sku, "antes": None if c.antes is None else str(c.antes),
                         "despues": str(c.despues)}
                        for c in plan.precios
                    ],
                    "ajustes": resultado.ajustes,
                },
                ensure_ascii=False,
            ),
            "motivo": f"Camiones de las rutas al {FECHA}, del Excel de cargas.",
        },
    )
    await sesion.commit()
    return resultado


async def _vendedores(sesion, codigos: list[str]) -> dict[str, dict]:
    filas = (
        await sesion.execute(
            text(
                "SELECT u.id, u.codigo, u.nombre, u.rol_codigo, u.activo, u.sucursal_id, "
                "       u.almacen_id, a.tipo AS almacen_tipo "
                "  FROM usuarios u LEFT JOIN almacenes a ON a.id = u.almacen_id "
                " WHERE upper(u.codigo) = ANY(:c)"
            ),
            {"c": codigos},
        )
    ).mappings().all()
    return {f["codigo"].upper(): dict(f) for f in filas}


async def _productos(sesion, skus: list[str]) -> dict[str, dict]:
    """Los productos que ya existen, con su precio en pieza de la lista general:
    la misma donde `aplicar` escribe."""
    filas = (
        await sesion.execute(
            text(
                """
                SELECT p.id, p.sku, p.nombre,
                       (SELECT pr.precio FROM precios pr
                         WHERE pr.producto_id = p.id AND pr.unidad_codigo = 'PZA'
                           AND pr.lista_id = (
                                 SELECT id FROM listas_precios WHERE activo
                                  ORDER BY es_default DESC, vigente_desde LIMIT 1)
                       ) AS precio
                  FROM productos p
                 WHERE p.sku = ANY(:s)
                """
            ),
            {"s": skus},
        )
    ).mappings().all()
    return {f["sku"]: dict(f) for f in filas}


async def _producto_id(sesion, sku: str) -> uuid.UUID:
    return (
        await sesion.execute(text("SELECT id FROM productos WHERE sku = :s"), {"s": sku})
    ).scalar_one()


async def _existencias(sesion, almacen_id) -> dict[str, Decimal]:
    filas = (
        await sesion.execute(
            text(
                "SELECT p.sku, e.cantidad FROM existencias e "
                "  JOIN productos p ON p.id = e.producto_id "
                " WHERE e.almacen_id = :a"
            ),
            {"a": almacen_id},
        )
    ).all()
    return {sku: Decimal(cantidad) for sku, cantidad in filas}


async def _movimientos_despues(sesion, almacen_id) -> int:
    """Lo que movió el camión después del sábado, sin contar los ajustes de este
    mismo comando: ventas o cargas que el ajuste va a pisar."""
    return (
        await sesion.execute(
            text(
                """
                SELECT count(*) FROM movimientos_inventario m
                 WHERE (m.almacen_origen_id = :a OR m.almacen_destino_id = :a)
                   AND m.fecha_servidor >= (CAST(:desde AS timestamp) AT TIME ZONE :zona)
                   AND NOT EXISTS (
                         SELECT 1 FROM ajustes_camion aj
                          WHERE m.documento_tipo = 'ajuste_camion'
                            AND aj.id = m.documento_id
                            AND aj.nota LIKE :nota)
                """
            ),
            {"a": almacen_id, "desde": DESPUES_DEL_SABADO, "zona": ZONA,
             "nota": INICIO_DE_NOTA + "%"},
        )
    ).scalar_one()


async def _dar_de_alta(sesion, a: Articulo, *, lista, precio: Decimal) -> None:
    familia = None
    if a.familia:
        familia = (
            await sesion.execute(
                text("SELECT id FROM categorias WHERE lower(nombre) = lower(:n)"),
                {"n": a.familia},
            )
        ).scalar_one_or_none()
        if familia is None:
            familia = uuid.uuid4()
            codigo = _codigo(a.familia)[:32]
            if (
                await sesion.execute(
                    text("SELECT 1 FROM categorias WHERE codigo = :c"), {"c": codigo}
                )
            ).first() is not None:
                codigo = f"{codigo[:27]}-{uuid.uuid4().hex[:4].upper()}"
            await sesion.execute(
                text(
                    "INSERT INTO categorias (id, codigo, nombre, orden, activo) "
                    "VALUES (:id, :codigo, :nombre, "
                    "        (SELECT COALESCE(max(orden), 0) + 1 FROM categorias), true)"
                ),
                {"id": familia, "codigo": codigo, "nombre": a.familia},
            )
    producto = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO productos (id, sku, nombre, categoria_id, unidad_base, tasa_iva) "
             "VALUES (:id, :sku, :nombre, :familia, 'PZA', 0)"),
        {"id": producto, "sku": a.sku, "nombre": a.nombre[:200], "familia": familia},
    )
    await sesion.execute(
        text("INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, es_default) "
             "VALUES (:p, 'PZA', 1, true)"),
        {"p": producto},
    )
    await sesion.execute(
        text("INSERT INTO precios (lista_id, producto_id, unidad_codigo, precio, version) "
             "VALUES (:l, :p, 'PZA', :precio, 1)"),
        {"l": lista, "p": producto, "precio": precio},
    )


async def _darle_camion(sesion, u: dict) -> uuid.UUID:
    """El camión del vendedor, como lo crea el alta del panel (`CAMION_<clave>`).
    Si ya hay uno con esa clave a su nombre, se le vuelve a asignar."""
    codigo = f"CAMION_{u['codigo']}"
    ya = (
        await sesion.execute(
            text("SELECT id, tipo, responsable_id FROM almacenes WHERE codigo = :c"),
            {"c": codigo},
        )
    ).mappings().first()
    if ya is not None and ya["tipo"] == "camion" and ya["responsable_id"] == u["id"]:
        camion = ya["id"]
        await sesion.execute(
            text("UPDATE almacenes SET activo = true WHERE id = :a"), {"a": camion}
        )
    else:
        if ya is not None:
            codigo = f"{codigo}_{uuid.uuid4().hex[:4].upper()}"
        camion = uuid.uuid4()
        await sesion.execute(
            text(
                "INSERT INTO almacenes (id, codigo, nombre, tipo, sucursal_id, responsable_id) "
                "VALUES (:id, :c, :n, 'camion', "
                "        COALESCE(:s, (SELECT id FROM sucursales ORDER BY codigo LIMIT 1)), :u)"
            ),
            {"id": camion, "c": codigo, "n": f"Camión de {u['nombre']}"[:120],
             "s": u["sucursal_id"], "u": u["id"]},
        )
    await sesion.execute(
        text("UPDATE usuarios SET almacen_id = :a, actualizado_en = now() WHERE id = :u"),
        {"a": camion, "u": u["id"]},
    )
    return camion


def _n(cantidad: Decimal) -> str:
    """Una cantidad sin ceros de más: 10, no 10.000."""
    return format(cantidad.normalize(), "f") if cantidad else "0"


def _codigo(nombre: str) -> str:
    sin_acentos = nombre.upper().translate(str.maketrans("ÁÉÍÓÚÜÑ", "AEIOUUN"))
    return "-".join("".join(c if c.isalnum() else " " for c in sin_acentos).split())[:40]
