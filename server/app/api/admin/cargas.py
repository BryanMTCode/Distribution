"""La carga del camión: lo único que faltaba para que un camión salga de verdad.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTA PANTALLA CIERRA EL CICLO
────────────────────────────────────────────────────────────────────────────
Hasta hoy el `existencias_camion` del teléfono solo se podía sembrar en Modo
Demo. La venta estaba construida, el carrito descontaba inventario, el ticket
salía — sobre mercancía inventada. Esta pantalla es por donde entra la real.

Y es el paso físico del día: a las seis de la mañana alguien en la bodega cuenta
cajas, las sube al camión, y de eso tiene que quedar un registro que cuadre al
cierre.

────────────────────────────────────────────────────────────────────────────
LAS TRES COSAS QUE PASAN AL CONFIRMAR, EN UNA SOLA TRANSACCIÓN
────────────────────────────────────────────────────────────────────────────
1. **El movimiento en el libro mayor** (`movimientos_inventario`, tipo `carga`,
   bodega → camión). Es append-only por disparador: un error se corrige con un
   documento compensatorio, nunca editando el historial.
2. **La caché de existencias**: se resta de la bodega y se suma al camión.
3. **El estado de la carga** pasa a `confirmada`, y con ese UPDATE el disparador
   de la migración 0015 publica el delta **con su detalle**.

Las tres o ninguna. Si el movimiento entrara y la existencia no, el libro mayor y
la caché divergirían desde el primer día y el job de reconciliación nocturno
estaría corrigiendo un error que se puede no cometer.

────────────────────────────────────────────────────────────────────────────
LA CONVERSIÓN A UNIDAD BASE SE RESUELVE AL CAPTURAR
────────────────────────────────────────────────────────────────────────────
Quien carga el camión cuenta **cajas**, porque es lo que levanta con las manos.
El inventario se lleva en **unidad base**. La multiplicación ocurre aquí, una
vez, y lo que se guarda en `carga_detalle` y en el movimiento son unidades base.

Es la regla del §2.2 ("SIEMPRE en unidad base; la conversión se resuelve al
capturar, nunca aquí") y es lo que evita el descuadre clásico: la mitad del
sistema contando cajas y la otra mitad piezas.

────────────────────────────────────────────────────────────────────────────
POR QUÉ SE PERMITE DEJAR LA BODEGA EN NEGATIVO
────────────────────────────────────────────────────────────────────────────
Si la bodega marca 8 cajas y el almacenista está subiendo 10 al camión, **el
sistema está mal, no el mundo**. Rechazar la carga significaría que el camión
sale con mercancía que el sistema no registró — que es infinitamente peor que un
número negativo en una caché.

Es el §0.1 aplicado dentro de la oficina, y es la razón por la que `existencias`
no tiene `CHECK (cantidad >= 0)` y sí tiene un índice para encontrar los
negativos. La pantalla lo advierte con el número exacto y deja pasar.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.api.admin.comun import (
    CapturaInvalida,
    SesionDep,
    render,
    sin_decimales,
    texto_o_nulo,
)
from app.api.admin.sesion_web import ActorWeb, exigir_csrf
from app.domain.importes import CantidadInvalida, cantidad_base

router = APIRouter(prefix="/panel/cargas", tags=["panel"], include_in_schema=False)

# `inventario.cargar` ya existe desde la migración 0009 y lo tiene el supervisor.
# El vendedor no: cargarse su propio camión sería firmar su propia entrega.
PERMISO = "inventario.cargar"

# Estados en los que la carga todavía se puede editar. Uno solo, y es a propósito:
# lo que ya salió de la bodega se corrige con un traspaso o un ajuste.
EDITABLE = ("borrador",)


# ---------------------------------------------------------------------------
# Lista
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
async def listar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    filtro: str = "abiertas",
    error: str = "",
    guardado: str = "",
) -> HTMLResponse:
    actor.exigir("inventario.ver")

    if filtro not in ("abiertas", "hoy", "todas"):
        filtro = "abiertas"

    condicion = {
        "abiertas": "c.estado IN ('borrador','confirmada','en_ruta')",
        "hoy": "c.fecha_operativa = CURRENT_DATE",
        "todas": "true",
    }[filtro]

    filas = (
        await sesion.execute(
            text(
                f"""
                SELECT c.id, c.folio, c.estado, c.fecha_operativa, c.version,
                       c.confirmada_en,
                       u.nombre AS vendedor, u.codigo AS vendedor_codigo,
                       a.nombre AS camion, r.codigo AS ruta,
                       COALESCE(d.renglones, 0) AS renglones,
                       COALESCE(d.piezas, 0) AS piezas
                  FROM cargas c
                  JOIN usuarios u ON u.id = c.vendedor_id
                  JOIN almacenes a ON a.id = c.almacen_destino_id
                  LEFT JOIN rutas r ON r.id = c.ruta_id
                  LEFT JOIN LATERAL (
                        SELECT count(*) AS renglones, sum(cantidad) AS piezas
                          FROM carga_detalle WHERE carga_id = c.id
                  ) d ON true
                 WHERE {condicion}
                 ORDER BY c.fecha_operativa DESC, c.folio DESC
                 LIMIT 200
                """  # noqa: S608 — `condicion` es una constante del código
            )
        )
    ).mappings().all()

    return render(
        peticion,
        "cargas.html",
        {
            "filas": filas,
            "filtro": filtro,
            "puede_editar": actor.puede(PERMISO),
            "vendedores": await _vendedores(sesion) if actor.puede(PERMISO) else [],
            "bodegas": await _bodegas(sesion) if actor.puede(PERMISO) else [],
            "hoy": date.today().isoformat(),
            "error": error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Cargas",
    )


# ---------------------------------------------------------------------------
# Abrir el borrador
# ---------------------------------------------------------------------------


@router.post("/nueva")
async def crear(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    vendedor_id: Annotated[str, Form()] = "",
    almacen_origen_id: Annotated[str, Form()] = "",
    fecha_operativa: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Abre la carga en `borrador`.

    El camión **no se elige**: es el almacén del vendedor (`usuarios.almacen_id`).
    Un desplegable de almacén destino permitiría cargarle el camión de otro, y el
    dueño exclusivo del almacén es la garantía sobre la que descansa todo el
    modelo offline (§0.2): sin ella vuelven los conflictos de concurrencia que
    este diseño existe para no tener.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    try:
        vendedor = uuid.UUID(vendedor_id)
        origen = uuid.UUID(almacen_origen_id)
        dia = date.fromisoformat(fecha_operativa) if fecha_operativa else date.today()
    except ValueError:
        return _a_lista(error="Faltan el vendedor, la bodega o la fecha.")

    fila = (
        await sesion.execute(
            text(
                "SELECT u.almacen_id, u.nombre, a.tipo "
                "  FROM usuarios u LEFT JOIN almacenes a ON a.id = u.almacen_id "
                " WHERE u.id = :v AND u.activo"
            ),
            {"v": vendedor},
        )
    ).mappings().first()

    if fila is None:
        return _a_lista(error="Ese vendedor no existe o está inactivo.")
    if fila["almacen_id"] is None or fila["tipo"] != "camion":
        return _a_lista(
            error=f"{fila['nombre']} no tiene camión asignado. "
            "Sin almacén propio no hay a dónde cargarle."
        )

    # Un vendedor no puede traer dos cargas abiertas el mismo día: el índice
    # `uq_carga_vendedor_dia` lo impide, pero el mensaje que da PostgreSQL no le
    # dice nada a quien está en la bodega a las seis de la mañana.
    abierta = (
        await sesion.execute(
            text(
                "SELECT id, folio, estado FROM cargas "
                " WHERE vendedor_id = :v AND fecha_operativa = :d "
                "   AND estado <> 'cancelada'"
            ),
            {"v": vendedor, "d": dia},
        )
    ).mappings().first()
    if abierta is not None:
        aviso = f"{fila['nombre']} ya trae la carga {abierta['folio']} de ese día."
        return RedirectResponse(
            f"/panel/cargas/{abierta['id']}?error={quote(aviso)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    consecutivo = (
        await sesion.execute(text("SELECT nextval('seq_folio_carga')"))
    ).scalar_one()

    # La ruta del vendedor se congela en la carga: es la que el delta usa para
    # decidir a qué dispositivo le importa.
    ruta = (
        await sesion.execute(
            text("SELECT ruta_id FROM usuarios_rutas WHERE usuario_id = :v LIMIT 1"),
            {"v": vendedor},
        )
    ).scalar_one_or_none()

    carga_id = uuid.uuid4()
    await sesion.execute(
        text(
            """
            INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id,
                                vendedor_id, ruta_id, fecha_operativa, estado)
            VALUES (:id, :folio, :origen, :destino, :v, :r, :d, 'borrador')
            """
        ),
        {
            "id": carga_id,
            "folio": f"CG-{consecutivo:06d}",
            "origen": origen,
            "destino": fila["almacen_id"],
            "v": vendedor,
            "r": ruta,
            "d": dia,
        },
    )
    await sesion.commit()
    return RedirectResponse(
        f"/panel/cargas/{carga_id}", status_code=status.HTTP_303_SEE_OTHER
    )


# ---------------------------------------------------------------------------
# Detalle
# ---------------------------------------------------------------------------


@router.get("/{carga_id}", response_class=HTMLResponse)
async def detalle(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    carga_id: uuid.UUID,
    error: str = "",
    guardado: str = "",
) -> HTMLResponse:
    actor.exigir("inventario.ver")

    carga = (
        await sesion.execute(
            text(
                "SELECT c.*, u.nombre AS vendedor, u.codigo AS vendedor_codigo, "
                "       a.nombre AS camion, o.nombre AS bodega, r.codigo AS ruta, "
                "       q.nombre AS confirmada_por_nombre "
                "  FROM cargas c "
                "  JOIN usuarios u ON u.id = c.vendedor_id "
                "  JOIN almacenes a ON a.id = c.almacen_destino_id "
                "  JOIN almacenes o ON o.id = c.almacen_origen_id "
                "  LEFT JOIN rutas r ON r.id = c.ruta_id "
                "  LEFT JOIN usuarios q ON q.id = c.confirmada_por "
                " WHERE c.id = :id"
            ),
            {"id": carga_id},
        )
    ).mappings().first()
    if carga is None:
        return RedirectResponse("/panel/cargas", status_code=status.HTTP_303_SEE_OTHER)

    # Cada renglón con la existencia de la bodega al lado: quien captura tiene que
    # poder ver si lo que está subiendo al camión existe en el sistema antes de
    # confirmar, no después.
    renglones = (
        await sesion.execute(
            text(
                """
                SELECT d.id, d.producto_id, d.cantidad, d.lote, d.caducidad,
                       p.nombre, p.sku, p.unidad_base,
                       COALESCE(e.cantidad, 0) AS en_bodega,
                       pu.presentaciones
                  FROM carga_detalle d
                  JOIN productos p ON p.id = d.producto_id
                  LEFT JOIN existencias e ON e.producto_id = d.producto_id
                       AND e.almacen_id = :bodega
                  LEFT JOIN LATERAL (
                        SELECT json_agg(json_build_object(
                                 'unidad', u.unidad_codigo, 'factor', u.factor::text)
                               ORDER BY u.factor DESC) AS presentaciones
                          FROM producto_unidades u
                         WHERE u.producto_id = d.producto_id AND u.activo
                  ) pu ON true
                 WHERE d.carga_id = :id
                 ORDER BY p.nombre
                """
            ),
            {"id": carga_id, "bodega": carga["almacen_origen_id"]},
        )
    ).mappings().all()

    faltan = [r for r in renglones if Decimal(r["cantidad"]) > Decimal(r["en_bodega"])]

    return render(
        peticion,
        "carga_detalle.html",
        {
            "carga": carga,
            "renglones": renglones,
            "faltan": faltan,
            "total_piezas": sum((Decimal(r["cantidad"]) for r in renglones), Decimal(0)),
            "editable": carga["estado"] in EDITABLE,
            "puede_editar": actor.puede(PERMISO),
            "error": error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Cargas",
    )


@router.post("/{carga_id}/renglon")
async def agregar_renglon(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    carga_id: uuid.UUID,
    producto: Annotated[str, Form()] = "",
    unidad_codigo: Annotated[str, Form()] = "",
    cantidad: Annotated[str, Form()] = "",
    lote: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Agrega un renglón. La conversión a unidad base ocurre aquí.

    `producto` acepta el SKU o el código de barras: quien está en la bodega tiene
    un lector en la mano o el SKU en la caja, no un UUID.

    Si el producto ya está en la carga con el mismo lote, **se suma**. Es lo que
    espera alguien que captura de dos tarimas distintas del mismo producto;
    reemplazar sería perder la primera captura sin avisar.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    carga = (
        await sesion.execute(
            text("SELECT estado FROM cargas WHERE id = :id"), {"id": carga_id}
        )
    ).mappings().first()
    if carga is None:
        return RedirectResponse("/panel/cargas", status_code=status.HTTP_303_SEE_OTHER)
    if carga["estado"] not in EDITABLE:
        return _volver(
            carga_id,
            error="Esta carga ya salió de la bodega. Se corrige con un traspaso o "
            "un ajuste, no editándola.",
        )

    clave = producto.strip()
    fila = (
        await sesion.execute(
            text(
                "SELECT id, nombre, unidad_base FROM productos "
                " WHERE activo AND (upper(sku) = upper(:c) OR codigo_barras = :c)"
            ),
            {"c": clave},
        )
    ).mappings().first()
    if fila is None:
        return _volver(carga_id, error=f"No hay producto activo con clave «{clave}».")

    presentacion = (
        await sesion.execute(
            text(
                "SELECT factor FROM producto_unidades "
                " WHERE producto_id = :p AND unidad_codigo = :u AND activo"
            ),
            {"p": fila["id"], "u": unidad_codigo},
        )
    ).mappings().first()
    if presentacion is None:
        return _volver(
            carga_id,
            error=f"{fila['nombre']} no tiene la presentación {unidad_codigo}.",
        )

    try:
        cuantas = _leer_bultos(cantidad)
        # La multiplicación, una vez, aquí. `cantidad_base` es la misma función
        # que usa el teléfono al armar una partida: la conversión caja→pieza se
        # escribe en un solo lugar del sistema.
        en_base = cantidad_base(cuantas, Decimal(presentacion["factor"]))
    except (CapturaInvalida, CantidadInvalida) as e:
        return _volver(carga_id, error=str(e))

    await sesion.execute(
        text(
            """
            INSERT INTO carga_detalle (id, carga_id, producto_id, cantidad, lote)
            VALUES (:id, :c, :p, :cant, :lote)
            ON CONFLICT (carga_id, producto_id, COALESCE(lote, ''))
              DO UPDATE SET cantidad = carga_detalle.cantidad + excluded.cantidad
            """
        ),
        {
            "id": uuid.uuid4(),
            "c": carga_id,
            "p": fila["id"],
            "cant": en_base,
            "lote": texto_o_nulo(lote, maximo=40),
        },
    )
    await sesion.commit()
    return _volver(
        carga_id,
        guardado=(
            f"{fila['nombre']}: {sin_decimales(cuantas)} {unidad_codigo} "
            f"= {sin_decimales(en_base)} {fila['unidad_base']}."
        ),
    )


@router.post("/{carga_id}/renglon/{renglon_id}/quitar")
async def quitar_renglon(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    carga_id: uuid.UUID,
    renglon_id: uuid.UUID,
    csrf: Annotated[str, Form()] = "",
):
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    estado = (
        await sesion.execute(
            text("SELECT estado FROM cargas WHERE id = :id"), {"id": carga_id}
        )
    ).scalar_one_or_none()
    if estado not in EDITABLE:
        return _volver(carga_id, error="Esta carga ya salió de la bodega.")

    await sesion.execute(
        text("DELETE FROM carga_detalle WHERE id = :r AND carga_id = :c"),
        {"r": renglon_id, "c": carga_id},
    )
    await sesion.commit()
    return _volver(carga_id, guardado="Renglón quitado.")


# ---------------------------------------------------------------------------
# Confirmar: el momento en que la mercancía cambia de dueño
# ---------------------------------------------------------------------------


@router.post("/{carga_id}/confirmar")
async def confirmar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    carga_id: uuid.UUID,
    csrf: Annotated[str, Form()] = "",
):
    """Mueve el inventario y publica el delta. Todo o nada.

    Después de esto:

    - el libro mayor tiene el movimiento (append-only, no se puede deshacer
      editando);
    - la bodega tiene menos y el camión tiene más;
    - el teléfono del vendedor recibirá la carga con su detalle en el siguiente
      `pull`, y con eso llenará `existencias_camion`.

    Es **idempotente por estado**: confirmar dos veces no mueve el inventario dos
    veces. Sin eso, un doble clic en una pantalla lenta duplicaría la carga del
    día, y el faltante aparecería en la liquidación como si el vendedor se
    hubiera llevado el doble.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    carga = (
        await sesion.execute(
            text(
                "SELECT id, folio, estado, almacen_origen_id, almacen_destino_id, "
                "       vendedor_id, fecha_operativa "
                "  FROM cargas WHERE id = :id FOR UPDATE"
            ),
            {"id": carga_id},
        )
    ).mappings().first()
    if carga is None:
        return RedirectResponse("/panel/cargas", status_code=status.HTTP_303_SEE_OTHER)
    if carga["estado"] != "borrador":
        return _volver(carga_id, error=f"Esta carga ya está {carga['estado']}.")

    renglones = (
        await sesion.execute(
            text(
                "SELECT producto_id, cantidad, lote, caducidad "
                "  FROM carga_detalle WHERE carga_id = :c"
            ),
            {"c": carga_id},
        )
    ).mappings().all()
    if not renglones:
        return _volver(
            carga_id, error="No puedes confirmar una carga sin un solo renglón."
        )

    ahora = datetime.now(UTC)
    for r in renglones:
        await sesion.execute(
            text(
                """
                INSERT INTO movimientos_inventario
                  (tipo, almacen_origen_id, almacen_destino_id, producto_id,
                   cantidad, lote, caducidad, documento_tipo, documento_id,
                   usuario_id, fecha_servidor)
                VALUES ('carga', :origen, :destino, :p, :cant, :lote, :cad,
                        'carga', :doc, :quien, :ahora)
                """
            ),
            {
                "origen": carga["almacen_origen_id"],
                "destino": carga["almacen_destino_id"],
                "p": r["producto_id"],
                "cant": r["cantidad"],
                "lote": r["lote"],
                "cad": r["caducidad"],
                "doc": carga_id,
                "quien": actor.usuario_id,
                "ahora": ahora,
            },
        )
        # La caché, en la misma transacción que el movimiento. Si divergieran, el
        # job de reconciliación nocturno estaría arreglando un error evitable.
        await sesion.execute(
            text(
                """
                INSERT INTO existencias (almacen_id, producto_id, cantidad, actualizado_en)
                VALUES (:a, :p, (0 - :cant), :ahora)
                ON CONFLICT (almacen_id, producto_id) DO UPDATE
                   SET cantidad = existencias.cantidad - :cant, actualizado_en = :ahora
                """
            ),
            {
                "a": carga["almacen_origen_id"],
                "p": r["producto_id"],
                "cant": r["cantidad"],
                "ahora": ahora,
            },
        )
        await sesion.execute(
            text(
                """
                INSERT INTO existencias (almacen_id, producto_id, cantidad, actualizado_en)
                VALUES (:a, :p, :cant, :ahora)
                ON CONFLICT (almacen_id, producto_id) DO UPDATE
                   SET cantidad = existencias.cantidad + :cant, actualizado_en = :ahora
                """
            ),
            {
                "a": carga["almacen_destino_id"],
                "p": r["producto_id"],
                "cant": r["cantidad"],
                "ahora": ahora,
            },
        )

    # Este UPDATE es el que dispara el delta (migración 0015), ya con el detalle
    # completo en la misma transacción.
    await sesion.execute(
        text(
            "UPDATE cargas SET estado = 'confirmada', confirmada_en = :ahora, "
            "       confirmada_por = :quien WHERE id = :id"
        ),
        {"id": carga_id, "ahora": ahora, "quien": actor.usuario_id},
    )
    await sesion.commit()

    negativos = (
        await sesion.execute(
            text(
                "SELECT count(*) FROM existencias "
                " WHERE almacen_id = :a AND cantidad < 0"
            ),
            {"a": carga["almacen_origen_id"]},
        )
    ).scalar_one()

    aviso = (
        f"Carga {carga['folio']} confirmada: {len(renglones)} renglones. "
        "El teléfono del vendedor la recibe en su siguiente sincronización."
    )
    if negativos:
        aviso += (
            f" Ojo: la bodega quedó con {negativos} producto(s) en negativo. "
            "No se rechazó porque la mercancía ya está en el camión: es el conteo "
            "de la bodega el que hay que revisar."
        )
    return _volver(carga_id, guardado=aviso)


@router.post("/{carga_id}/cancelar")
async def cancelar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    carga_id: uuid.UUID,
    csrf: Annotated[str, Form()] = "",
):
    """Cancela una carga que todavía es borrador.

    **Una carga confirmada no se cancela desde aquí.** La mercancía ya está
    físicamente en el camión, y puede haber ventas hechas contra ella que aún no
    sincronizan. Devolverla es un `retorno`, que es el documento de la
    liquidación — no una cancelación que finge que el día no pasó.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    estado = (
        await sesion.execute(
            text("SELECT estado FROM cargas WHERE id = :id"), {"id": carga_id}
        )
    ).scalar_one_or_none()
    if estado is None:
        return RedirectResponse("/panel/cargas", status_code=status.HTTP_303_SEE_OTHER)
    if estado != "borrador":
        return _volver(
            carga_id,
            error="Ya está confirmada: la mercancía está en el camión. Lo que "
            "regresa se registra como retorno en la liquidación.",
        )

    await sesion.execute(
        text("UPDATE cargas SET estado = 'cancelada' WHERE id = :id"), {"id": carga_id}
    )
    await sesion.commit()
    return _a_lista(guardado="Carga cancelada.")


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------


def _leer_bultos(texto: str | None) -> Decimal:
    """Cuántos bultos de esa presentación se subieron.

    Entero a propósito: nadie sube media caja a un camión, y ningún producto se
    vende a granel (ADR 0002 §2). Un `2.5` aquí es un dedazo, y si se aceptara,
    `cantidad_base` lo convertiría en 60 piezas con cara de dato bueno.
    """
    crudo = (texto or "").strip().replace(",", "")
    if not crudo:
        raise CapturaInvalida("Falta cuántos bultos se subieron.")
    try:
        valor = Decimal(crudo)
    except ArithmeticError as e:
        raise CapturaInvalida(f"«{texto}» no es una cantidad.") from e
    if not valor.is_finite():
        raise CapturaInvalida(f"«{texto}» no es una cantidad.")
    if valor <= 0:
        raise CapturaInvalida("La cantidad tiene que ser mayor que cero.")
    if valor != valor.to_integral_value():
        raise CapturaInvalida(
            f"Se suben bultos completos, no {valor}. Nadie carga media caja."
        )
    if valor > 100000:
        raise CapturaInvalida(f"{valor} bultos parece un error de dedo.")
    return valor


def _volver(carga_id: uuid.UUID, *, error: str = "", guardado: str = ""):
    destino = f"/panel/cargas/{carga_id}"
    if error:
        destino += f"?error={quote(error)}"
    elif guardado:
        destino += f"?guardado={quote(guardado)}"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


def _a_lista(*, error: str = "", guardado: str = ""):
    destino = "/panel/cargas"
    if error:
        destino += f"?error={quote(error)}"
    elif guardado:
        destino += f"?guardado={quote(guardado)}"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


async def _vendedores(sesion) -> list[dict]:
    """Los vendedores con camión. Sin almacén propio no hay a dónde cargar."""
    return (
        await sesion.execute(
            text(
                "SELECT u.id, u.codigo, u.nombre, a.nombre AS camion "
                "  FROM usuarios u "
                "  JOIN almacenes a ON a.id = u.almacen_id AND a.tipo = 'camion' "
                " WHERE u.activo AND u.rol_codigo = 'vendedor' "
                " ORDER BY u.codigo"
            )
        )
    ).mappings().all()


async def _bodegas(sesion) -> list[dict]:
    return (
        await sesion.execute(
            text(
                "SELECT id, codigo, nombre FROM almacenes "
                " WHERE activo AND tipo = 'bodega' ORDER BY nombre"
            )
        )
    ).mappings().all()
