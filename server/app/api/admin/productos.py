"""Catálogo y precios: la pantalla por la que el negocio real entra al sistema.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTA PANTALLA ES LA FASE 1 Y NO UN ADORNO
────────────────────────────────────────────────────────────────────────────
Hasta hoy los productos y sus precios llegaban al teléfono de dos formas: una
migración de semilla, o el Modo Demo. Ninguna de las dos sirve para operar. Esta
es la primera pieza por la que **un producto que existe en la bodega puede
existir en el sistema**, con su caja, su factor y su precio, y llegar al camión.

────────────────────────────────────────────────────────────────────────────
NO HACE FALTA ESCRIBIR NINGÚN DELTA
────────────────────────────────────────────────────────────────────────────
Los `INSERT`/`UPDATE` de aquí **no publican nada a mano**: los disparadores de la
migración 0010 escriben en `change_log` por cada cambio en `productos`,
`producto_unidades`, `precios` y `listas_precios`. El teléfono lo recoge en su
siguiente `pull`.

Esto es deliberado y es la razón de que los disparadores estén en la base y no en
el código: si publicar el delta fuera responsabilidad de quien escribe, cada
pantalla nueva tendría que acordarse, y la que se olvide produce un catálogo que
la oficina ve y el camión no. Un `UPDATE` hecho a mano por psql también publica.

────────────────────────────────────────────────────────────────────────────
LO QUE NO SE BORRA
────────────────────────────────────────────────────────────────────────────
Un producto **nunca se borra**: se desactiva. Hay ventas viejas que lo
referencian, y hay ventas de hoy que todavía no sincronizan y vienen con su
`producto_id`. Borrarlo mandaría esas ventas a cuarentena por una llave foránea
—violando §0.1 por un renglón de catálogo— o las dejaría sin poder explicar qué
se vendió.

────────────────────────────────────────────────────────────────────────────
CAMBIAR UN PRECIO TIENE UNA CONSECUENCIA QUE LA PANTALLA DICE EN VOZ ALTA
────────────────────────────────────────────────────────────────────────────
Los equipos que ya salieron traen la lista de esta mañana. Si el precio cambia a
mediodía, las ventas que hagan con el precio viejo entran marcadas como
`precio_desactualizado` —correctamente: el servidor respeta el importe del papel
que firmó el cliente (§0.1) y solo levanta la mano—. La pantalla lo advierte
antes de guardar, porque quien captura precios tiene que poder decidir si lo hace
ahora o al cierre del día.
"""

from __future__ import annotations

import json
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.api.admin.comun import (
    DIEZMILESIMA,
    CapturaInvalida,
    SesionDep,
    agrupar_por_familia,
    borrar_si_nadie_lo_usa,
    leer_factor,
    leer_precio,
    precio_corto,
    render,
    sin_decimales,
    texto_o_nulo,
)
from app.api.admin.sesion_web import ActorWeb, exigir_csrf

router = APIRouter(prefix="/panel/productos", tags=["panel"], include_in_schema=False)

# Quien captura catálogo necesita este permiso. `gerente` NO lo tiene a propósito
# (migración 0009: "Gerencia es de SOLO LECTURA sobre la operación"), así que ve
# los precios y no los cambia. La plantilla esconde los formularios y cada POST
# lo exige de nuevo: la UI oculta, el servidor prohíbe.
PERMISO = "catalogo.administrar"

# Las dos tasas que existen en abarrotes. Un campo libre de IVA invita a escribir
# 16 donde va 0.16, y el error se ve hasta la declaración.
TASAS_IVA = [(Decimal("0.0000"), "Exento (0%)"), (Decimal("0.1600"), "16%")]


# ---------------------------------------------------------------------------
# Lista
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
async def listar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    q: str = "",
    filtro: str = "todos",
    familia: str = "",
    guardado: str = "",
    error: str = "",
) -> HTMLResponse:
    """El catálogo, agrupado por familia, con lo que el vendedor no puede vender.

    `filtro=sin_precio` es el que importa: un producto sin precio en la lista por
    omisión **no le aparece al vendedor**. No falla, no avisa: simplemente no
    está. Es la clase de problema que se descubre cuando un cliente lo pide.
    """
    actor.exigir("catalogo.ver")

    if filtro not in ("todos", "sin_precio", "inactivos"):
        filtro = "todos"

    condiciones = ["p.activo"] if filtro != "inactivos" else ["NOT p.activo"]
    parametros: dict[str, object] = {}

    busqueda = q.strip()
    if busqueda:
        condiciones.append("(p.nombre ILIKE :q OR p.sku ILIKE :q OR p.codigo_barras = :exacto)")
        parametros["q"] = f"%{busqueda}%"
        parametros["exacto"] = busqueda

    if familia == "sin":
        condiciones.append("p.categoria_id IS NULL")
    elif familia:
        try:
            parametros["familia"] = uuid.UUID(familia)
            condiciones.append("p.categoria_id = :familia")
        except ValueError:
            familia = ""

    if filtro == "sin_precio":
        condiciones.append(
            "NOT EXISTS (SELECT 1 FROM precios pr "
            "              JOIN listas_precios l ON l.id = pr.lista_id AND l.es_default "
            "             WHERE pr.producto_id = p.id)"
        )

    filas = (
        await sesion.execute(
            text(
                f"""
                SELECT p.id, p.sku, p.nombre, p.unidad_base, p.activo,
                       p.codigo_barras, p.tasa_iva, cat.nombre AS categoria,
                       cat.id AS familia_id, cat.nombre AS familia,
                       COALESCE(pres.presentaciones, '[]'::json) AS presentaciones
                  FROM productos p
                  LEFT JOIN categorias cat ON cat.id = p.categoria_id
                  LEFT JOIN LATERAL (
                        SELECT json_agg(
                                 json_build_object(
                                   'unidad', u.unidad_codigo,
                                   'factor', u.factor::text,
                                   'precio', pr.precio::text)
                                 ORDER BY u.factor DESC
                               ) AS presentaciones
                          FROM producto_unidades u
                          LEFT JOIN precios pr
                                 ON pr.producto_id = u.producto_id
                                AND pr.unidad_codigo = u.unidad_codigo
                                AND pr.lista_id = (SELECT id FROM listas_precios
                                                    WHERE es_default LIMIT 1)
                         WHERE u.producto_id = p.id AND u.activo
                  ) pres ON true
                 WHERE {" AND ".join(condiciones)}
                 -- Por familia, en el orden en que se acomodaron (el de la hoja
                 -- de la dirección), y dentro de cada una por clave.
                 ORDER BY (cat.id IS NULL), cat.orden, cat.nombre, p.sku
                 LIMIT 300
                """  # noqa: S608 — las condiciones son constantes del código
            ),
            parametros,
        )
    ).mappings().all()

    return render(
        peticion,
        "productos.html",
        {
            "filas": filas,
            "grupos": agrupar_por_familia(filas),
            "familias": await _familias(sesion),
            "familia": familia,
            "q": busqueda,
            "filtro": filtro,
            "puede_editar": actor.puede(PERMISO),
            "guardado": guardado,
            "error": error,
        },
        actor=actor,
        seccion="Productos",
    )


# ---------------------------------------------------------------------------
# Familias: cómo se agrupan los artículos (ADR 0002 §89)
# ---------------------------------------------------------------------------
# Son las `categorias` de la base; en pantalla se llaman «familias», como en la
# hoja de la dirección («FAMILIA GANADOR MININO»). El orden es el de la hoja:
# una familia nueva va al final.


async def _familias(sesion) -> list[dict]:
    return [
        dict(f)
        for f in (
            await sesion.execute(
                text(
                    "SELECT c.id, c.nombre, c.orden, "
                    "       (SELECT count(*) FROM productos p "
                    "         WHERE p.categoria_id = c.id AND p.activo) AS articulos "
                    "  FROM categorias c WHERE c.activo ORDER BY c.orden, c.nombre"
                )
            )
        ).mappings()
    ]


def _a_lista(*, guardado: str = "", error: str = "") -> RedirectResponse:
    destino = "/panel/productos"
    if guardado:
        destino += f"?guardado={quote(guardado)}"
    elif error:
        destino += f"?error={quote(error)}"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


def _codigo_de_familia(nombre: str) -> str:
    sin_acentos = nombre.upper().translate(str.maketrans("ÁÉÍÓÚÜÑ", "AEIOUUN"))
    base = "-".join("".join(c if c.isalnum() else " " for c in sin_acentos).split())
    return (base or "FAMILIA")[:32]


@router.post("/familias")
async def crear_familia(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    nombre: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)
    limpio = " ".join(nombre.split())[:80]
    if not limpio:
        return _a_lista(error="Escribe el nombre de la familia: «Botanas Javi».")
    ya = (
        await sesion.execute(
            text("SELECT 1 FROM categorias WHERE lower(nombre) = lower(:n)"), {"n": limpio}
        )
    ).first()
    if ya is not None:
        return _a_lista(error=f"Ya hay una familia «{limpio}».")
    codigo = _codigo_de_familia(limpio)
    if (
        await sesion.execute(text("SELECT 1 FROM categorias WHERE codigo = :c"), {"c": codigo})
    ).first() is not None:
        codigo = f"{codigo[:27]}-{uuid.uuid4().hex[:4].upper()}"
    await sesion.execute(
        text(
            "INSERT INTO categorias (id, codigo, nombre, orden, activo) "
            "VALUES (:id, :codigo, :nombre, "
            "        (SELECT COALESCE(max(orden), 0) + 1 FROM categorias), true)"
        ),
        {"id": uuid.uuid4(), "codigo": codigo, "nombre": limpio},
    )
    await sesion.commit()
    return _a_lista(
        guardado=f"Familia «{limpio}» creada. Para meterle artículos, cámbialos de familia "
        "en su ficha."
    )


@router.post("/familias/{familia_id}")
async def renombrar_familia(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    familia_id: uuid.UUID,
    nombre: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)
    limpio = " ".join(nombre.split())[:80]
    if not limpio:
        return _a_lista(error="La familia necesita un nombre.")
    hecho = await sesion.execute(
        text("UPDATE categorias SET nombre = :n WHERE id = :f"), {"n": limpio, "f": familia_id}
    )
    if hecho.rowcount == 0:
        return _a_lista(error="Esa familia no existe.")
    await sesion.commit()
    return _a_lista(guardado=f"La familia ahora se llama «{limpio}».")


@router.post("/familias/{familia_id}/eliminar")
async def eliminar_familia(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    familia_id: uuid.UUID,
    csrf: Annotated[str, Form()] = "",
):
    """Borra la familia. Sus artículos NO se borran: quedan «Sin familia»."""
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)
    nombre = (
        await sesion.execute(text("SELECT nombre FROM categorias WHERE id = :f"), {"f": familia_id})
    ).scalar_one_or_none()
    if nombre is None:
        return _a_lista(error="Esa familia no existe.")
    sueltos = (
        await sesion.execute(
            text("UPDATE productos SET categoria_id = NULL, actualizado_en = now() "
                 " WHERE categoria_id = :f"),
            {"f": familia_id},
        )
    ).rowcount
    borrada = await borrar_si_nadie_lo_usa(
        sesion, "DELETE FROM categorias WHERE id = :f", {"f": familia_id}
    )
    if not borrada:
        await sesion.rollback()
        return _a_lista(
            error=f"«{nombre}» no se puede borrar: una promoción o una subfamilia la usa."
        )
    await sesion.commit()
    return _a_lista(
        guardado=f"Familia «{nombre}» borrada."
        + (f" Sus {sueltos} artículo(s) quedaron «Sin familia»." if sueltos else "")
    )


# ---------------------------------------------------------------------------
# Alta
# ---------------------------------------------------------------------------


@router.get("/nuevo", response_class=HTMLResponse)
async def pantalla_nuevo(
    peticion: Request, actor: ActorWeb, sesion: SesionDep
) -> HTMLResponse:
    actor.exigir(PERMISO)
    return render(
        peticion,
        "producto_nuevo.html",
        {
            "unidades": await _unidades(sesion),
            "categorias": await _categorias(sesion),
            "tasas": TASAS_IVA,
            "error": None,
            "valores": {},
        },
        actor=actor,
        seccion="Productos",
    )


@router.post("/nuevo")
async def crear(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    sku: Annotated[str, Form()] = "",
    nombre: Annotated[str, Form()] = "",
    unidad_base: Annotated[str, Form()] = "",
    codigo_barras: Annotated[str, Form()] = "",
    tasa_iva: Annotated[str, Form()] = "0.0000",
    categoria_id: Annotated[str, Form()] = "",
    factor_caja: Annotated[str, Form()] = "",
    unidad_caja: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Da de alta el producto **con su presentación base en la misma transacción**.

    Un producto sin renglón en `producto_unidades` no se puede poner en ningún
    precio: la llave foránea de `precios` apunta al par (producto, unidad). Si el
    alta dejara ese renglón para después, cada producto nuevo pasaría por un
    estado en el que no se puede vender y nadie sabría por qué.

    Por eso la unidad base entra siempre con factor 1 —una pieza son una pieza— y
    marcada como la presentación por omisión.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    valores = {
        "sku": sku.strip().upper(),
        "nombre": nombre.strip(),
        "unidad_base": unidad_base,
        "codigo_barras": codigo_barras.strip(),
        "tasa_iva": tasa_iva,
        "categoria_id": categoria_id,
        "factor_caja": factor_caja.strip(),
        "unidad_caja": unidad_caja,
    }

    async def con_error(mensaje: str):
        return render(
            peticion,
            "producto_nuevo.html",
            {
                "unidades": await _unidades(sesion),
                "categorias": await _categorias(sesion),
                "tasas": TASAS_IVA,
                "error": mensaje,
                "valores": valores,
            },
            actor=actor,
            seccion="Productos",
        )

    if not valores["sku"]:
        return await con_error("Falta el SKU: es con lo que se busca el producto.")
    if not valores["nombre"]:
        return await con_error("Falta el nombre. Es el que se imprime en el ticket.")

    unidades = {u["codigo"] for u in await _unidades(sesion)}
    if valores["unidad_base"] not in unidades:
        return await con_error("Elige la unidad base del producto.")

    try:
        iva = _tasa(valores["tasa_iva"])
        # Presentación adicional: opcional, pero si viene una parte tienen que
        # venir las dos. Una caja sin factor no se puede convertir a piezas.
        factor = None
        if valores["unidad_caja"] or valores["factor_caja"]:
            if valores["unidad_caja"] == valores["unidad_base"]:
                raise CapturaInvalida(
                    "La presentación adicional tiene que ser distinta de la unidad base."
                )
            if valores["unidad_caja"] not in unidades:
                raise CapturaInvalida("Elige la unidad de la presentación adicional.")
            factor = leer_factor(valores["factor_caja"])
    except CapturaInvalida as e:
        return await con_error(str(e))

    existe = (
        await sesion.execute(
            text("SELECT nombre FROM productos WHERE sku = :s"), {"s": valores["sku"]}
        )
    ).scalar_one_or_none()
    if existe is not None:
        return await con_error(f"El SKU {valores['sku']} ya lo tiene «{existe}».")

    producto_id = uuid.uuid4()
    await sesion.execute(
        text(
            """
            INSERT INTO productos (id, sku, codigo_barras, nombre, categoria_id,
                                   unidad_base, tasa_iva)
            VALUES (:id, :sku, :barras, :nombre, :categoria, :unidad, :iva)
            """
        ),
        {
            "id": producto_id,
            "sku": valores["sku"],
            "barras": texto_o_nulo(valores["codigo_barras"], maximo=40),
            "nombre": valores["nombre"][:200],
            "categoria": uuid.UUID(valores["categoria_id"]) if valores["categoria_id"] else None,
            "unidad": valores["unidad_base"],
            "iva": iva,
        },
    )
    await sesion.execute(
        text(
            "INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, es_default) "
            "VALUES (:p, :u, 1, true)"
        ),
        {"p": producto_id, "u": valores["unidad_base"]},
    )
    if factor is not None:
        await sesion.execute(
            text(
                "INSERT INTO producto_unidades (producto_id, unidad_codigo, factor) "
                "VALUES (:p, :u, :f)"
            ),
            {"p": producto_id, "u": valores["unidad_caja"], "f": factor},
        )
    await sesion.commit()

    # Directo al detalle: el producto todavía no tiene precio y no se puede
    # vender. Mandar a la lista dejaría el trabajo a medias sin decirlo.
    return RedirectResponse(
        f"/panel/productos/{producto_id}?nuevo=1", status_code=status.HTTP_303_SEE_OTHER
    )


# ---------------------------------------------------------------------------
# Detalle: datos, presentaciones y precios
# ---------------------------------------------------------------------------


@router.get("/{producto_id}", response_class=HTMLResponse)
async def detalle(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    producto_id: uuid.UUID,
    error: str = "",
    guardado: str = "",
    nuevo: str = "",
) -> HTMLResponse:
    actor.exigir("catalogo.ver")
    contexto = await _contexto_detalle(sesion, producto_id)
    if contexto is None:
        return RedirectResponse("/panel/productos", status_code=status.HTTP_303_SEE_OTHER)

    return render(
        peticion,
        "producto_detalle.html",
        {
            **contexto,
            "unidades": await _unidades(sesion),
            "categorias": await _categorias(sesion),
            "tasas": TASAS_IVA,
            "error": error,
            "guardado": guardado,
            "recien_creado": bool(nuevo),
            "puede_editar": actor.puede(PERMISO),
        },
        actor=actor,
        seccion="Productos",
    )


@router.post("/{producto_id}")
async def guardar_datos(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    producto_id: uuid.UUID,
    sku: Annotated[str, Form()] = "",
    nombre: Annotated[str, Form()] = "",
    codigo_barras: Annotated[str, Form()] = "",
    tasa_iva: Annotated[str, Form()] = "0.0000",
    categoria_id: Annotated[str, Form()] = "",
    activo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Edita los datos del producto, el SKU incluido.

    ───────────────────────────────────────────────────────────────────────────
    EL SKU SÍ SE EDITA; LA UNIDAD BASE NO, Y NO ES LA MISMA CLASE DE NEGATIVA
    ───────────────────────────────────────────────────────────────────────────
    El SKU no se editaba «porque es con lo que la bodega y el vendedor lo
    identifican». Eso es una razón operativa, no de integridad: ninguna tabla
    apunta al SKU —todas apuntan al `id`—, así que cambiarlo no rompe nada. Y la
    razón operativa se vuelve en contra el día que el SKU está mal escrito: deja
    de identificar y no se puede arreglar. La dirección pidió poder cambiarlo
    (octubre 2026), así que se puede, con su unicidad validada y su cambio
    asentado en `auditoria`.

    La unidad base sigue sin editarse, y ésa sí es de integridad: TODO el
    inventario y TODAS las partidas de venta están expresados en ella. Cambiarla
    convertiría en piezas lo que se contó en cajas sin tocar un solo número —el
    camión diría 240 y querría decir otra cosa—. Para eso se da de baja el producto
    y se crea el correcto.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    if not nombre.strip():
        return _volver(producto_id, error="El nombre no puede quedar vacío.")
    clave = sku.strip().upper()
    if not clave:
        return _volver(producto_id, error="El SKU no puede quedar vacío.")
    try:
        iva = _tasa(tasa_iva)
    except CapturaInvalida as e:
        return _volver(producto_id, error=str(e))

    antes = (
        await sesion.execute(
            text("SELECT sku, nombre FROM productos WHERE id = :id"),
            {"id": producto_id},
        )
    ).mappings().first()
    if antes is None:
        return RedirectResponse("/panel/productos", status_code=303)

    # La unicidad se comprueba antes de escribir para poder decir de quién es el
    # SKU. El UNIQUE de la base lo impediría igual, pero su error no le dice a
    # nadie que el código ya lo tiene el atún.
    if clave != antes["sku"]:
        dueno = (
            await sesion.execute(
                text(
                    "SELECT nombre FROM productos WHERE upper(sku) = :s AND id <> :id"
                ),
                {"s": clave, "id": producto_id},
            )
        ).scalar_one_or_none()
        if dueno is not None:
            return _volver(
                producto_id,
                error=f"El SKU «{clave}» ya es de «{dueno}». Dos productos con el "
                "mismo código son dos productos que la bodega no puede distinguir.",
            )

    await sesion.execute(
        text(
            """
            UPDATE productos
               SET sku = :sku, nombre = :nombre, codigo_barras = :barras,
                   tasa_iva = :iva, categoria_id = :categoria, activo = :activo,
                   actualizado_en = now()
             WHERE id = :id
            """
        ),
        {
            "id": producto_id,
            "sku": clave[:40],
            "nombre": nombre.strip()[:200],
            "barras": texto_o_nulo(codigo_barras, maximo=40),
            "iva": iva,
            "categoria": uuid.UUID(categoria_id) if categoria_id else None,
            "activo": bool(activo),
        },
    )

    # El cambio de SKU se asienta aparte: es el único dato del producto que alguien
    # usa para buscarlo en papel, y el día que la bodega no encuentre «SOPA-70» hay
    # que poder saber en qué se convirtió.
    if clave != antes["sku"]:
        await sesion.execute(
            text(
                """
                INSERT INTO auditoria
                  (entidad, entidad_id, accion, usuario_id, datos_antes,
                   datos_despues, ocurrido_en)
                VALUES ('producto', :id, 'cambiar_sku', :quien,
                        CAST(:antes AS jsonb), CAST(:despues AS jsonb), now())
                """
            ),
            {
                "id": producto_id,
                "quien": actor.usuario_id,
                "antes": json.dumps({"sku": antes["sku"]}, ensure_ascii=False),
                "despues": json.dumps({"sku": clave[:40]}, ensure_ascii=False),
            },
        )

    await sesion.commit()
    aviso = "Datos guardados."
    if clave != antes["sku"]:
        aviso += (
            f" El SKU pasó de «{antes['sku']}» a «{clave}»: el teléfono lo recibe "
            "en la siguiente sincronización."
        )
    return _volver(producto_id, guardado=aviso)


# ---------------------------------------------------------------------------
# Eliminar un producto
# ---------------------------------------------------------------------------
# Las tablas que, si tienen un solo renglón, impiden el borrado. Son los
# DOCUMENTOS: una venta, una carga, un conteo, un asiento del libro mayor.
#
# Están enumeradas a mano y no sacadas de `pg_constraint` a propósito. Sacarlas de
# la base incluiría las de configuración —precios, presentaciones— que sí se van
# con el producto, y el día que alguien agregue una tabla nueva es mejor que el
# borrado falle con un error de llave foránea que explique dónde, que descubrir
# meses después que se borró un producto que estaba en un documento.
_DOCUMENTOS_QUE_IMPIDEN = (
    ("venta_partidas", "ventas"),
    ("carga_detalle", "cargas"),
    ("merma_detalle", "mermas o devoluciones"),
    ("entrada_detalle", "entradas de bodega"),
    ("salida_detalle", "salidas de bodega"),
    ("liquidacion_detalle", "liquidaciones"),
    ("traspaso_detalle", "traspasos"),
    ("ajustes_camion", "ajustes de camión"),
    ("movimientos_inventario", "movimientos de inventario"),
    ("producto_costos", "costos registrados"),
)

# Lo que se va CON el producto: su configuración. No son hechos, son parámetros —
# cuánto mide una caja, a cuánto se vende en cada lista—, y sin el producto no
# significan nada.
_CONFIGURACION_QUE_SE_VA = (
    "promociones",
    "precios",
    "producto_unidades",
    "existencias",
)


@router.post("/{producto_id}/eliminar")
async def eliminar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    producto_id: uuid.UUID,
    confirmo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Borra un producto que NUNCA se usó. Si se usó, se da de baja.

    ───────────────────────────────────────────────────────────────────────────
    LAS DOS COSAS QUE LA GENTE LLAMA «ELIMINAR», Y SOLO UNA SE PUEDE
    ───────────────────────────────────────────────────────────────────────────
    **Dar de baja** (`activo = false`) es lo que se quiere el 99 % de las veces: el
    producto deja de aparecer en el catálogo del teléfono y en los formularios de
    captura, y toda su historia sigue en pie. Ya existe, es la casilla «activo» de
    los datos, y es reversible.

    **Borrar de verdad** solo tiene sentido para el producto que se dio de alta por
    error y nunca se usó: el SKU equivocado, el duplicado que alguien capturó dos
    veces. Y solo entonces se puede, porque un producto que aparece en una venta no
    se puede borrar sin romper esa venta — y una venta es un papel que un cliente
    tiene en la mano.

    Así que esto comprueba cada documento antes de borrar y, cuando encuentra uno,
    **dice cuál y manda a dar de baja**. No ofrece un borrado en cascada: la
    cascada aquí significaría borrar ventas.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    producto = (
        await sesion.execute(
            text("SELECT id, sku, nombre, activo FROM productos WHERE id = :id"),
            {"id": producto_id},
        )
    ).mappings().first()
    if producto is None:
        return RedirectResponse("/panel/productos", status_code=303)

    if not confirmo:
        return _volver(
            producto_id,
            error="Marca la casilla de confirmación: un borrado no se deshace.",
        )

    # La existencia PRIMERO. Un producto con saldo en algún almacén no es un error
    # de captura: es mercancía que alguien tiene en un anaquel o arriba de un
    # camión. Y darlo de baja tampoco sirve: desaparecería del catálogo del
    # teléfono y el vendedor ya no podría vender lo que trae.
    con_saldo = (
        await sesion.execute(
            text(
                "SELECT a.nombre, e.cantidad FROM existencias e "
                "  JOIN almacenes a ON a.id = e.almacen_id "
                " WHERE e.producto_id = :p AND e.cantidad <> 0 LIMIT 1"
            ),
            {"p": producto_id},
        )
    ).mappings().first()
    if con_saldo is not None:
        return _volver(
            producto_id,
            error=(
                f"Hay {con_saldo['cantidad']} en {con_saldo['nombre']}. Un producto "
                "con existencia no es un error de captura: es mercancía que alguien "
                "tiene, y si se quita del catálogo el vendedor ya no puede venderla. "
                "Dale salida o ajústala primero."
            ),
        )

    # Los documentos, uno por uno, para poder nombrarlos.
    historia: list[str] = []
    for tabla, como_se_llama in _DOCUMENTOS_QUE_IMPIDEN:
        cuantos = (
            await sesion.execute(
                text(f"SELECT count(*) FROM {tabla} WHERE producto_id = :p"),  # noqa: S608
                {"p": producto_id},
            )
        ).scalar_one()
        if cuantos:
            historia.append(f"{cuantos} {como_se_llama}")

    # Con historia, «eliminar» DA DE BAJA en vez de negarse (octubre 2026: la
    # dirección pidió que el botón funcione). Para quien usa el panel el efecto es
    # el que buscaba —sale del catálogo del teléfono y de los formularios— y sus
    # ventas siguen en pie. Antes la pantalla se negaba y mandaba a quitar la
    # casilla «activo» a mano: la misma operación, con un paso de más.
    if historia:
        await sesion.execute(
            text(
                """
                INSERT INTO auditoria
                  (entidad, entidad_id, accion, usuario_id, datos_antes, ocurrido_en)
                VALUES ('producto', :id, 'dar_de_baja', :quien,
                        CAST(:antes AS jsonb), now())
                """
            ),
            {
                "id": producto_id,
                "quien": actor.usuario_id,
                "antes": json.dumps(
                    {
                        "sku": producto["sku"],
                        "nombre": producto["nombre"],
                        "activo": producto["activo"],
                    },
                    ensure_ascii=False,
                ),
            },
        )
        await sesion.execute(
            text(
                "UPDATE productos SET activo = false, actualizado_en = now() "
                " WHERE id = :id"
            ),
            {"id": producto_id},
        )
        await sesion.commit()
        aviso = (
            f"«{producto['nombre']}» se dio de baja: aparece en "
            + ", ".join(historia)
            + ", y borrarlo rompería esos documentos. Ya no está en el catálogo del "
            "teléfono ni en los formularios. Se reactiva marcando «activo» en sus datos."
        )
        return RedirectResponse(
            f"/panel/productos?guardado={quote(aviso)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # El antes, ANTES: después del DELETE no hay de dónde leerlo.
    await sesion.execute(
        text(
            """
            INSERT INTO auditoria
              (entidad, entidad_id, accion, usuario_id, datos_antes, ocurrido_en)
            VALUES ('producto', :id, 'eliminar', :quien, CAST(:antes AS jsonb), now())
            """
        ),
        {
            "id": producto_id,
            "quien": actor.usuario_id,
            "antes": json.dumps(
                {"sku": producto["sku"], "nombre": producto["nombre"]},
                ensure_ascii=False,
            ),
        },
    )

    for tabla in _CONFIGURACION_QUE_SE_VA:
        await sesion.execute(
            text(f"DELETE FROM {tabla} WHERE producto_id = :p"),  # noqa: S608
            {"p": producto_id},
        )
    await sesion.execute(
        text("DELETE FROM productos WHERE id = :id"), {"id": producto_id}
    )
    await sesion.commit()

    # El teléfono NO lo borra: lo desactiva. Está así desde el principio en el
    # aplicador de deltas, con su razón —«un producto retirado puede seguir
    # apareciendo en ventas ya hechas que aún no sincronizan»—, y es lo correcto:
    # un DELETE local contra la llave foránea de `venta_partidas` abortaría la tanda
    # entera y el dispositivo no volvería a sincronizar nunca.
    # A la lista, no al detalle: el producto ya no existe y su pantalla daría 404.
    aviso = (
        f"«{producto['nombre']}» ({producto['sku']}) se borró. En los teléfonos "
        "queda desactivado, no borrado: una venta que todavía no sincroniza puede "
        "apuntarle."
    )
    return RedirectResponse(
        f"/panel/productos?guardado={quote(aviso)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{producto_id}/presentaciones")
async def agregar_presentacion(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    producto_id: uuid.UUID,
    unidad_codigo: Annotated[str, Form()] = "",
    factor: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Agrega o corrige una presentación: "1 CAJA = 24 PZA".

    Corregir el factor de una presentación **no cambia ninguna venta ya hecha**:
    `venta_partidas` congela `factor_unidad` en cada renglón justo para esto. La
    caja que salió del camión ayer siguió siendo de 24 aunque hoy se corrija a 12.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    producto = (
        await sesion.execute(
            text("SELECT unidad_base FROM productos WHERE id = :id"), {"id": producto_id}
        )
    ).mappings().first()
    if producto is None:
        return RedirectResponse("/panel/productos", status_code=status.HTTP_303_SEE_OTHER)

    if unidad_codigo == producto["unidad_base"]:
        return _volver(
            producto_id,
            error="La unidad base siempre vale 1 y no se cambia: una pieza es una pieza.",
        )
    if unidad_codigo not in {u["codigo"] for u in await _unidades(sesion)}:
        return _volver(producto_id, error="Elige una unidad de medida válida.")

    try:
        valor = leer_factor(factor)
    except CapturaInvalida as e:
        return _volver(producto_id, error=str(e))

    await sesion.execute(
        text(
            """
            INSERT INTO producto_unidades (producto_id, unidad_codigo, factor)
            VALUES (:p, :u, :f)
            ON CONFLICT (producto_id, unidad_codigo)
              DO UPDATE SET factor = excluded.factor, activo = true
            """
        ),
        {"p": producto_id, "u": unidad_codigo, "f": valor},
    )
    await sesion.commit()
    return _volver(
        producto_id,
        guardado=f"1 {unidad_codigo} = {sin_decimales(valor)} {producto['unidad_base']}.",
    )


@router.post("/{producto_id}/precios")
async def guardar_precios(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    producto_id: uuid.UUID,
):
    """Captura los precios de una lista, todas las presentaciones de un golpe.

    Los campos vienen como `precio_PZA`, `precio_CAJA`… así que se leen del
    formulario crudo. Un campo vacío significa **no lo cambies**, no "bórralo":
    borrar un precio deja al producto invisible en esa presentación, y eso tiene
    que ser una decisión explícita, no el resultado de no escribir nada.

    `version` sube en cada cambio. El dispositivo la manda de vuelta en la venta,
    y con ella la oficina puede saber con qué lista se cobró sin comparar
    importes uno por uno.
    """
    actor.exigir(PERMISO)
    formulario = await peticion.form()
    exigir_csrf(peticion, str(formulario.get("csrf") or ""))

    try:
        lista_id = uuid.UUID(str(formulario.get("lista_id") or ""))
    except ValueError:
        return _volver(producto_id, error="Falta la lista de precios.")

    presentaciones = (
        await sesion.execute(
            text(
                "SELECT unidad_codigo FROM producto_unidades "
                " WHERE producto_id = :p AND activo"
            ),
            {"p": producto_id},
        )
    ).scalars().all()

    guardados: list[str] = []
    for unidad in presentaciones:
        crudo = formulario.get(f"precio_{unidad}")
        if crudo is None or not str(crudo).strip():
            continue
        try:
            precio = leer_precio(str(crudo), campo=f"El precio de {unidad}")
        except CapturaInvalida as e:
            return _volver(producto_id, error=str(e))

        await sesion.execute(
            text(
                """
                INSERT INTO precios (lista_id, producto_id, unidad_codigo, precio,
                                     version, actualizado_en)
                VALUES (:l, :p, :u, :precio, 1, now())
                ON CONFLICT (lista_id, producto_id, unidad_codigo) DO UPDATE
                   SET precio = excluded.precio,
                       version = precios.version + 1,
                       actualizado_en = now()
                 WHERE precios.precio <> excluded.precio
                """
            ),
            {"l": lista_id, "p": producto_id, "u": unidad, "precio": precio},
        )
        guardados.append(f"{unidad} ${precio_corto(precio)}")

    if not guardados:
        return _volver(producto_id, error="No escribiste ningún precio.")

    await sesion.commit()
    return _volver(
        producto_id,
        guardado=(
            "Precios guardados: "
            + ", ".join(guardados)
            + ". Los equipos que ya salieron traen el precio anterior hasta que "
            "sincronicen; sus ventas de hoy van a entrar marcadas."
        ),
    )


@router.post("/{producto_id}/precios/quitar")
async def quitar_precio(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    producto_id: uuid.UUID,
    lista_id: Annotated[str, Form()] = "",
    unidad_codigo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Quita el precio de una presentación en una lista.

    Es una acción aparte y con su propio botón porque la consecuencia es fuerte:
    **el vendedor deja de poder ofrecer esa presentación**. No falla, no avisa; se
    le desaparece del catálogo. Dejar que eso pase por vaciar un campo sería una
    trampa.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)
    try:
        lista = uuid.UUID(lista_id)
    except ValueError:
        return _volver(producto_id, error="Falta la lista de precios.")

    await sesion.execute(
        text(
            "DELETE FROM precios "
            " WHERE lista_id = :l AND producto_id = :p AND unidad_codigo = :u"
        ),
        {"l": lista, "p": producto_id, "u": unidad_codigo},
    )
    await sesion.commit()
    return _volver(
        producto_id,
        guardado=f"Se quitó el precio de {unidad_codigo}: el vendedor ya no la verá.",
    )


@router.post("/{producto_id}/precios/derivar")
async def derivar_precio(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    producto_id: uuid.UUID,
    lista_id: Annotated[str, Form()] = "",
    hacia: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Saca el precio de una presentación dividiendo el de otra.

    ────────────────────────────────────────────────────────────────────────
    ESTE BOTÓN EXISTE POR UNA DIVISIÓN QUE NO CIERRA
    ────────────────────────────────────────────────────────────────────────
    La caja de 24 vale $296.00. La pieza, entonces, vale 296 ÷ 24 = **12.3333…**,
    que no es un número de dos decimales. Quien lo calcula en una hoja escribe
    12.33, y a partir de ahí cada caja vendida por pieza cobra $295.92: ocho
    centavos menos, veinticuatro veces al día, todos los días.

    Por eso `precios.precio` tiene cuatro decimales y por eso esta división se
    hace aquí, con `Decimal` y redondeo medio-arriba a la diezmilésima: 12.3333 ×
    24 = 295.9992, que redondeado **una sola vez al final** da los $296.00 de la
    caja. La aritmética está en `app/domain/importes.py` y la comparte el
    teléfono.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)
    try:
        lista = uuid.UUID(lista_id)
    except ValueError:
        return _volver(producto_id, error="Falta la lista de precios.")

    filas = (
        await sesion.execute(
            text(
                """
                SELECT u.unidad_codigo, u.factor, pr.precio
                  FROM producto_unidades u
                  LEFT JOIN precios pr ON pr.producto_id = u.producto_id
                       AND pr.unidad_codigo = u.unidad_codigo AND pr.lista_id = :l
                 WHERE u.producto_id = :p AND u.activo
                """
            ),
            {"l": lista, "p": producto_id},
        )
    ).mappings().all()

    destino = next((f for f in filas if f["unidad_codigo"] == hacia), None)
    if destino is None:
        return _volver(producto_id, error="Esa presentación no existe en el producto.")

    # De dónde derivar lo decide el servidor con **la misma función** que dibujó la
    # sugerencia en la pantalla. El formulario no manda el origen a propósito: si
    # lo mandara, el botón podría guardar una división distinta de la que la
    # persona leyó antes de tocarlo.
    conocidos = [(f, f) for f in filas if f["precio"] is not None]
    sugerencia = _sugerir(destino, conocidos)
    if sugerencia is None:
        return _volver(
            producto_id,
            error=f"No hay otra presentación con precio de la cual sacar {hacia}.",
        )
    nuevo = sugerencia["valor"]
    if nuevo <= 0:
        return _volver(producto_id, error="La división da cero: revisa los factores.")

    await sesion.execute(
        text(
            """
            INSERT INTO precios (lista_id, producto_id, unidad_codigo, precio,
                                 version, actualizado_en)
            VALUES (:l, :p, :u, :precio, 1, now())
            ON CONFLICT (lista_id, producto_id, unidad_codigo) DO UPDATE
               SET precio = excluded.precio,
                   version = precios.version + 1,
                   actualizado_en = now()
             WHERE precios.precio <> excluded.precio
            """
        ),
        {"l": lista, "p": producto_id, "u": hacia, "precio": nuevo},
    )
    await sesion.commit()
    return _volver(
        producto_id,
        guardado=(
            f"{hacia} = ${precio_corto(nuevo)}, sacado de "
            f"${precio_corto(sugerencia['precio_origen'])} de {sugerencia['desde']} "
            f"entre {sin_decimales(sugerencia['factor_origen'])}."
        ),
    )


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------


def _volver(producto_id: uuid.UUID, *, error: str = "", guardado: str = ""):
    """Redirige al detalle con el mensaje en la URL.

    Redirect-después-de-POST: sin él, recargar la página reenvía el formulario y
    un precio se captura dos veces. El mensaje viaja en la query porque el panel
    no tiene sesión de mensajes y no vale la pena inventarla para esto.
    """
    if error:
        destino = f"/panel/productos/{producto_id}?error={quote(error)}"
    elif guardado:
        destino = f"/panel/productos/{producto_id}?guardado={quote(guardado)}"
    else:
        destino = f"/panel/productos/{producto_id}"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


def _tasa(valor: str) -> Decimal:
    for tasa, _ in TASAS_IVA:
        if str(tasa) == valor:
            return tasa
    raise CapturaInvalida("El IVA solo puede ser exento o 16%.")


async def _unidades(sesion) -> list[dict]:
    return (
        await sesion.execute(
            text("SELECT codigo, nombre FROM unidades_medida ORDER BY codigo")
        )
    ).mappings().all()


async def _categorias(sesion) -> list[dict]:
    return (
        await sesion.execute(
            text("SELECT id, nombre FROM categorias WHERE activo ORDER BY orden, nombre")
        )
    ).mappings().all()


async def _contexto_detalle(sesion, producto_id: uuid.UUID) -> dict | None:
    """El producto con sus presentaciones y el precio de cada lista.

    Se arma con dos consultas y se cruza en Python en vez de pedirle a SQL una
    tabla cruzada: son dos listas de menos de diez renglones cada una, y el SQL
    que las cruza no lo entendería nadie dentro de seis meses.
    """
    producto = (
        await sesion.execute(
            text(
                "SELECT p.*, cat.nombre AS categoria "
                "  FROM productos p "
                "  LEFT JOIN categorias cat ON cat.id = p.categoria_id "
                " WHERE p.id = :id"
            ),
            {"id": producto_id},
        )
    ).mappings().first()
    if producto is None:
        return None

    presentaciones = (
        await sesion.execute(
            text(
                "SELECT unidad_codigo, factor, es_default FROM producto_unidades "
                " WHERE producto_id = :p AND activo ORDER BY factor"
            ),
            {"p": producto_id},
        )
    ).mappings().all()

    listas = (
        await sesion.execute(
            text(
                "SELECT id, codigo, nombre, es_default FROM listas_precios "
                " WHERE activo ORDER BY es_default DESC, nombre"
            )
        )
    ).mappings().all()

    precios = (
        await sesion.execute(
            text(
                "SELECT lista_id, unidad_codigo, precio, version, actualizado_en "
                "  FROM precios WHERE producto_id = :p"
            ),
            {"p": producto_id},
        )
    ).mappings().all()
    por_llave = {(str(p["lista_id"]), p["unidad_codigo"]): p for p in precios}

    # Una tabla por lista: renglón por presentación, con su precio si lo tiene y
    # con la sugerencia calculada desde otra presentación si no.
    tablas = []
    for lista in listas:
        renglones = []
        conocidos = [
            (pres, por_llave[(str(lista["id"]), pres["unidad_codigo"])])
            for pres in presentaciones
            if (str(lista["id"]), pres["unidad_codigo"]) in por_llave
        ]
        for pres in presentaciones:
            actual = por_llave.get((str(lista["id"]), pres["unidad_codigo"]))
            renglones.append(
                {
                    "unidad": pres["unidad_codigo"],
                    "factor": Decimal(pres["factor"]),
                    "es_default": pres["es_default"],
                    "precio": Decimal(actual["precio"]) if actual else None,
                    "version": actual["version"] if actual else None,
                    "actualizado_en": actual["actualizado_en"] if actual else None,
                    "sugerencia": _sugerir(pres, conocidos),
                }
            )
        tablas.append({"lista": lista, "renglones": renglones})

    return {"producto": producto, "presentaciones": presentaciones, "tablas": tablas}


def _sugerir(presentacion, conocidos: list) -> dict | None:
    """De qué otra presentación se puede derivar el precio de ésta, y cuánto da.

    Se prefiere la presentación más grande con precio: el precio que la oficina
    negocia es el de la caja, y de ahí baja a la pieza. Derivar al revés
    multiplica el redondeo de la pieza por 24.
    """
    if not conocidos:
        return None
    origen, precio = max(conocidos, key=lambda par: Decimal(par[0]["factor"]))
    if origen["unidad_codigo"] == presentacion["unidad_codigo"]:
        return None
    proporcion = Decimal(presentacion["factor"]) / Decimal(origen["factor"])
    valor = (Decimal(precio["precio"]) * proporcion).quantize(
        DIEZMILESIMA, rounding=ROUND_HALF_UP
    )
    return {
        "desde": origen["unidad_codigo"],
        "precio_origen": Decimal(precio["precio"]),
        "factor_origen": Decimal(origen["factor"]),
        "valor": valor,
    }
