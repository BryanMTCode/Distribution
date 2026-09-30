"""Clientes: donde un prospecto de la calle se vuelve un cliente del negocio.

────────────────────────────────────────────────────────────────────────────
ESTA PANTALLA ES LA MITAD QUE LE FALTABA A LA FASE 2
────────────────────────────────────────────────────────────────────────────
Cuando un vendedor da de alta una tienda en la calle, el servidor la guarda
`estatus = 'prospecto'`, **sin código, sin lista de precios y con límite de
crédito en cero** (`manejadores.crear_cliente`). Eso es correcto y es a
propósito: aceptar una línea de crédito propuesta desde el teléfono sería dejar
que el vendedor se autorice su propia cartera.

Pero hasta hoy nada podía terminar el trabajo. Un prospecto se quedaba prospecto
para siempre. El alta de campo estaba construida y era, en la práctica, un
formulario que no llevaba a ningún lado.

Aquí se cierra el circuito:

  confirmar   asigna el consecutivo, pone `activo` y le da lista de precios
  condiciones decide crédito, límite y días — decisión de oficina, nunca de ruta
  bloquear    corta la venta a crédito sin borrar nada

────────────────────────────────────────────────────────────────────────────
EL SALDO NO SE EDITA
────────────────────────────────────────────────────────────────────────────
No hay campo de saldo en ninguna de estas pantallas, y no es un olvido. El saldo
sale de `cuentas_por_cobrar` a través de `v_cartera_cliente`: es la suma de lo
que se le facturó menos lo que pagó. Un campo editable de saldo sería una segunda
verdad que tarde o temprano contradice a la primera, y entonces nadie sabe cuál
de las dos cobrar. Se corrige con un cargo o un pago, que dejan rastro.

────────────────────────────────────────────────────────────────────────────
NADA SE BORRA
────────────────────────────────────────────────────────────────────────────
Un cliente que ya no existe se marca `inactivo`. Tiene ventas, tiene cartera, y
puede tener una venta de esta mañana que todavía no ha sincronizado. Borrarlo
mandaría esa venta a cuarentena por una llave foránea: sería violar §0.1 desde la
oficina.
"""

from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.api.admin.comun import (
    CapturaInvalida,
    SesionDep,
    dinero,
    leer_dinero,
    leer_entero,
    render,
    texto_o_nulo,
)
from app.api.admin.sesion_web import ActorWeb, exigir_csrf

router = APIRouter(prefix="/panel/clientes", tags=["panel"], include_in_schema=False)

# `clientes.administrar` es "Editar condiciones comerciales" (migración 0009). El
# vendedor no lo tiene; el gerente tampoco, porque gerencia monitorea y no opera.
PERMISO = "clientes.administrar"

# Un plazo de crédito no pasa de un mes en este negocio: la mercancía se vende
# antes. Un número más grande casi siempre es un dedazo.
DIAS_CREDITO_MAXIMO = 90
ESTATUS = ["prospecto", "activo", "inactivo"]


# ---------------------------------------------------------------------------
# Lista
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
async def listar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    q: str = "",
    filtro: str = "pendientes",
) -> HTMLResponse:
    """Los clientes, con los prospectos de campo primero.

    `filtro=pendientes` es la vista por omisión a propósito: son los que están
    esperando una decisión de la oficina. Abrir en "todos" escondería el trabajo
    pendiente entre trescientos clientes que ya están resueltos.
    """
    actor.exigir("clientes.ver")

    if filtro not in ("pendientes", "todos", "bloqueados", "inactivos"):
        filtro = "pendientes"

    condiciones: list[str] = []
    parametros: dict[str, object] = {}

    if filtro == "pendientes":
        condiciones.append("(c.estatus = 'prospecto' OR c.requiere_revision)")
    elif filtro == "bloqueados":
        condiciones.append("c.bloqueado")
    elif filtro == "inactivos":
        condiciones.append("c.estatus = 'inactivo'")
    else:
        condiciones.append("c.estatus <> 'inactivo'")

    busqueda = q.strip()
    if busqueda:
        condiciones.append(
            "(c.nombre_comercial ILIKE :q OR c.codigo ILIKE :q OR c.telefono ILIKE :q)"
        )
        parametros["q"] = f"%{busqueda}%"

    filas = (
        await sesion.execute(
            text(
                f"""
                SELECT c.id, c.codigo, c.nombre_comercial, c.estatus, c.bloqueado,
                       c.origen_alta, c.requiere_revision, c.revision_motivo,
                       c.colonia, c.municipio, c.telefono, c.creado_en,
                       c.permite_credito, c.limite_credito,
                       r.nombre AS ruta, r.codigo AS ruta_codigo,
                       l.nombre AS lista_precios,
                       COALESCE(cart.saldo, 0) AS saldo,
                       COALESCE(cart.facturas_vencidas, 0) AS vencidas
                  FROM clientes c
                  LEFT JOIN rutas r ON r.id = c.ruta_id
                  LEFT JOIN listas_precios l ON l.id = c.lista_precios_id
                  LEFT JOIN v_cartera_cliente cart ON cart.cliente_id = c.id
                 WHERE {" AND ".join(condiciones)}
                 ORDER BY (c.estatus = 'prospecto') DESC,
                          c.requiere_revision DESC,
                          c.creado_en DESC
                 LIMIT 300
                """  # noqa: S608 — las condiciones son constantes del código
            ),
            parametros,
        )
    ).mappings().all()

    conteos = (
        await sesion.execute(
            text(
                """
                SELECT
                  count(*) FILTER (WHERE estatus = 'prospecto' OR requiere_revision)
                    AS pendientes,
                  count(*) FILTER (WHERE bloqueado) AS bloqueados,
                  count(*) FILTER (WHERE estatus <> 'inactivo') AS activos
                  FROM clientes
                """
            )
        )
    ).mappings().one()

    return render(
        peticion,
        "clientes.html",
        {
            "filas": filas,
            "q": busqueda,
            "filtro": filtro,
            "conteos": conteos,
            "puede_editar": actor.puede(PERMISO),
        },
        actor=actor,
        seccion="Clientes",
    )


# ---------------------------------------------------------------------------
# Detalle
# ---------------------------------------------------------------------------


@router.get("/{cliente_id}", response_class=HTMLResponse)
async def detalle(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    cliente_id: uuid.UUID,
    error: str = "",
    guardado: str = "",
) -> HTMLResponse:
    actor.exigir("clientes.ver")

    cliente = (
        await sesion.execute(
            text(
                "SELECT c.*, r.nombre AS ruta_nombre, u.nombre AS alta_por, "
                "       d.etiqueta AS alta_equipo "
                "  FROM clientes c "
                "  LEFT JOIN rutas r ON r.id = c.ruta_id "
                "  LEFT JOIN usuarios u ON u.id = c.creado_por "
                "  LEFT JOIN dispositivos d ON d.id = c.dispositivo_id "
                " WHERE c.id = :id"
            ),
            {"id": cliente_id},
        )
    ).mappings().first()
    if cliente is None:
        return RedirectResponse("/panel/clientes", status_code=status.HTTP_303_SEE_OTHER)

    cartera = (
        await sesion.execute(
            text("SELECT * FROM v_cartera_cliente WHERE cliente_id = :id"),
            {"id": cliente_id},
        )
    ).mappings().first()

    # Posibles duplicados: dos vendedores pueden dar de alta la misma tiendita el
    # mismo día. NO se fusionan por heurística (0003); se muestran aquí para que
    # alguien decida.
    duplicados = (
        await sesion.execute(
            text(
                """
                SELECT d.id, d.score, d.distancia_m,
                       otro.id AS otro_id, otro.nombre_comercial AS otro_nombre,
                       otro.codigo AS otro_codigo
                  FROM clientes_posibles_duplicados d
                  JOIN clientes otro
                       ON otro.id = CASE WHEN d.cliente_id = :id
                                         THEN d.candidato_id ELSE d.cliente_id END
                 WHERE (d.cliente_id = :id OR d.candidato_id = :id)
                   AND d.estado = 'pendiente'
                """
            ),
            {"id": cliente_id},
        )
    ).mappings().all()

    return render(
        peticion,
        "cliente_detalle.html",
        {
            "cliente": cliente,
            "cartera": cartera,
            "duplicados": duplicados,
            "rutas": await _rutas(sesion),
            "listas": await _listas(sesion),
            "canales": await _canales(sesion),
            "estatus_posibles": ESTATUS,
            "dias_maximo": DIAS_CREDITO_MAXIMO,
            "error": error,
            "guardado": guardado,
            "puede_editar": actor.puede(PERMISO),
            "saldo_texto": dinero(cartera["saldo"] if cartera else 0),
            "disponible_texto": dinero(cartera["disponible"] if cartera else 0),
        },
        actor=actor,
        seccion="Clientes",
    )


@router.post("/{cliente_id}/confirmar")
async def confirmar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    cliente_id: uuid.UUID,
    lista_precios_id: Annotated[str, Form()] = "",
    ruta_id: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Convierte un prospecto de campo en cliente del negocio.

    Tres cosas, en una transacción:

    1. **El código.** Sale de `seq_codigo_cliente` (migración 0014), no de
       `max()+1`: dos personas confirmando al mismo tiempo obtendrían el mismo
       número y una vería un error de UNIQUE que no significa nada para ella.
    2. **La lista de precios.** Sin lista, cada venta a este cliente entra marcada
       `sin_lista_de_precios`. Es el motivo de revisión más fácil de evitar y el
       que más ruido hace si se deja pasar.
    3. **El estatus.** Pasa a `activo` y se limpia `requiere_revision`: la
       revisión ya la hizo una persona, que es exactamente lo que la bandera
       pedía.

    El crédito **no** se toca aquí. Confirmar que un negocio existe y decidir
    cuánto se le presta son dos juicios distintos, y juntarlos en un botón hace
    que el segundo se tome sin pensarlo.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    cliente = (
        await sesion.execute(
            text("SELECT codigo, estatus FROM clientes WHERE id = :id"), {"id": cliente_id}
        )
    ).mappings().first()
    if cliente is None:
        return RedirectResponse("/panel/clientes", status_code=status.HTTP_303_SEE_OTHER)

    if not lista_precios_id:
        return _volver(
            cliente_id,
            error="Elige la lista de precios: sin ella cada venta entra marcada.",
        )

    # El código solo se genera si no tiene. Confirmar dos veces —o corregir una
    # revisión— no debe gastar un consecutivo ni cambiarle el código a un cliente
    # que la ruta ya conoce por ese número.
    codigo = cliente["codigo"]
    if not codigo:
        consecutivo = (
            await sesion.execute(text("SELECT nextval('seq_codigo_cliente')"))
        ).scalar_one()
        codigo = f"C{consecutivo:05d}"

    await sesion.execute(
        text(
            """
            UPDATE clientes
               SET codigo = :codigo,
                   estatus = 'activo',
                   lista_precios_id = :lista,
                   ruta_id = COALESCE(:ruta, ruta_id),
                   requiere_revision = false,
                   revision_motivo = NULL,
                   actualizado_en = now()
             WHERE id = :id
            """
        ),
        {
            "id": cliente_id,
            "codigo": codigo,
            "lista": uuid.UUID(lista_precios_id),
            "ruta": uuid.UUID(ruta_id) if ruta_id else None,
        },
    )
    await sesion.commit()
    return _volver(cliente_id, guardado=f"Confirmado como {codigo}.")


@router.post("/{cliente_id}/datos")
async def guardar_datos(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    cliente_id: uuid.UUID,
    nombre_comercial: Annotated[str, Form()] = "",
    razon_social: Annotated[str, Form()] = "",
    rfc: Annotated[str, Form()] = "",
    canal_codigo: Annotated[str, Form()] = "",
    contacto_nombre: Annotated[str, Form()] = "",
    telefono: Annotated[str, Form()] = "",
    calle: Annotated[str, Form()] = "",
    numero: Annotated[str, Form()] = "",
    colonia: Annotated[str, Form()] = "",
    municipio: Annotated[str, Form()] = "",
    estado: Annotated[str, Form()] = "",
    codigo_postal: Annotated[str, Form()] = "",
    referencias: Annotated[str, Form()] = "",
    ruta_id: Annotated[str, Form()] = "",
    secuencia: Annotated[str, Form()] = "",
    estatus: Annotated[str, Form()] = "activo",
    csrf: Annotated[str, Form()] = "",
):
    """Los datos que la oficina corrige del alta de campo.

    La georreferencia **no se edita aquí**. La capturó el vendedor parado en la
    banqueta del negocio, con su precisión y su origen (`gps` o `manual`)
    guardados: es el mejor dato que va a existir de ese domicilio. Corregirla
    desde una computadora que está a quince kilómetros sería cambiar un dato
    medido por uno supuesto, y encima rompería la distancia que marca las ventas
    fuera de geocerca.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    if not nombre_comercial.strip():
        return _volver(cliente_id, error="El nombre del negocio no puede quedar vacío.")
    if estatus not in ESTATUS:
        return _volver(cliente_id, error="Estatus inválido.")

    try:
        orden = leer_entero(secuencia, campo="El orden de visita", maximo=9999)
    except CapturaInvalida as e:
        return _volver(cliente_id, error=str(e))

    await sesion.execute(
        text(
            """
            UPDATE clientes
               SET nombre_comercial = :nombre, razon_social = :razon, rfc = :rfc,
                   canal_codigo = :canal, contacto_nombre = :contacto,
                   telefono = :telefono, calle = :calle, numero = :numero,
                   colonia = :colonia, municipio = :municipio, estado = :estado,
                   codigo_postal = :cp, referencias = :referencias,
                   ruta_id = :ruta, secuencia = :secuencia, estatus = :estatus,
                   actualizado_en = now()
             WHERE id = :id
            """
        ),
        {
            "id": cliente_id,
            "nombre": nombre_comercial.strip()[:200],
            "razon": texto_o_nulo(razon_social),
            "rfc": texto_o_nulo(rfc, maximo=13),
            "canal": texto_o_nulo(canal_codigo, maximo=20),
            "contacto": texto_o_nulo(contacto_nombre),
            "telefono": texto_o_nulo(telefono, maximo=20),
            "calle": texto_o_nulo(calle),
            "numero": texto_o_nulo(numero, maximo=20),
            "colonia": texto_o_nulo(colonia),
            "municipio": texto_o_nulo(municipio),
            "estado": texto_o_nulo(estado),
            "cp": texto_o_nulo(codigo_postal, maximo=10),
            "referencias": texto_o_nulo(referencias, maximo=500),
            "ruta": uuid.UUID(ruta_id) if ruta_id else None,
            "secuencia": orden or None,
            "estatus": estatus,
        },
    )
    await sesion.commit()
    return _volver(cliente_id, guardado="Datos guardados.")


@router.post("/{cliente_id}/credito")
async def guardar_credito(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    cliente_id: uuid.UUID,
    lista_precios_id: Annotated[str, Form()] = "",
    permite_credito: Annotated[str, Form()] = "",
    limite_credito: Annotated[str, Form()] = "",
    dias_credito: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Las condiciones comerciales: lo que decide la oficina y lee el teléfono.

    ────────────────────────────────────────────────────────────────────────
    BAJAR UN LÍMITE NO BORRA LO QUE YA SE DEBE
    ────────────────────────────────────────────────────────────────────────
    Si un cliente debe $2,000 y su límite baja a $1,000, el saldo sigue siendo
    $2,000: lo que cambia es que ya no puede llevar más. `v_cartera_cliente`
    calcula `disponible` como `max(límite − saldo, 0)`, así que el disponible se
    va a cero y la venta a crédito se corta sola en el teléfono. La pantalla lo
    dice cuando el límite nuevo queda por debajo del saldo, porque quien lo
    escribe casi siempre cree que está perdonando la deuda.

    Y lo que ya salió a la calle esta mañana no se entera hasta que sincronice.
    §0.1: si el vendedor cobra a crédito con el límite viejo, la venta entra y se
    marca `excede_limite_credito`. No se rechaza — la mercancía ya la tiene el
    cliente.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    try:
        limite = leer_dinero(limite_credito, campo="El límite de crédito")
        dias = leer_entero(dias_credito, campo="Los días de crédito",
                           maximo=DIAS_CREDITO_MAXIMO)
    except CapturaInvalida as e:
        return _volver(cliente_id, error=str(e))

    da_credito = bool(permite_credito)
    if da_credito and limite <= 0:
        return _volver(
            cliente_id,
            error="Con crédito autorizado y límite en cero, el teléfono no va a "
            "dejar vender a crédito. Pon el límite o quita el crédito.",
        )

    await sesion.execute(
        text(
            """
            UPDATE clientes
               SET lista_precios_id = :lista,
                   permite_credito = :permite,
                   limite_credito = :limite,
                   dias_credito = :dias,
                   actualizado_en = now()
             WHERE id = :id
            """
        ),
        {
            "id": cliente_id,
            "lista": uuid.UUID(lista_precios_id) if lista_precios_id else None,
            "permite": da_credito,
            "limite": limite,
            "dias": dias,
        },
    )
    await sesion.commit()

    saldo = (
        await sesion.execute(
            text("SELECT saldo FROM v_cartera_cliente WHERE cliente_id = :id"),
            {"id": cliente_id},
        )
    ).scalar_one_or_none() or 0

    aviso = "Condiciones guardadas."
    if da_credito and limite < saldo:
        aviso += (
            f" Ojo: debe {dinero(saldo)} y el límite nuevo es {dinero(limite)}, "
            "así que su disponible queda en cero y no va a poder llevar más a "
            "crédito hasta que abone. La deuda no cambió."
        )
    return _volver(cliente_id, guardado=aviso)


@router.post("/{cliente_id}/bloqueo")
async def cambiar_bloqueo(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    cliente_id: uuid.UUID,
    bloquear: Annotated[str, Form()] = "",
    motivo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Bloquea o desbloquea la venta a crédito.

    El bloqueo pide motivo, y por la misma razón que la nota de la cuarentena:
    dentro de un mes el vendedor va a preguntar por qué no le puede vender a esa
    tienda, y "bloqueado" sin más no le sirve a nadie. El motivo viaja al teléfono
    en el delta del cliente.

    **No impide la venta de contado.** El cliente puede seguir comprando pagando
    en el momento; lo que se corta es el préstamo. Bloquear un negocio por
    completo sería una decisión distinta, y se toma marcándolo `inactivo`.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    quiere_bloquear = bool(bloquear)
    if quiere_bloquear and not motivo.strip():
        return _volver(cliente_id, error="Escribe por qué se bloquea. El vendedor va a preguntar.")

    await sesion.execute(
        text(
            "UPDATE clientes SET bloqueado = :b, bloqueo_motivo = :m, "
            "       actualizado_en = now() WHERE id = :id"
        ),
        {
            "id": cliente_id,
            "b": quiere_bloquear,
            "m": texto_o_nulo(motivo, maximo=300) if quiere_bloquear else None,
        },
    )
    await sesion.commit()
    return _volver(
        cliente_id,
        guardado="Bloqueado para crédito." if quiere_bloquear else "Desbloqueado.",
    )


@router.post("/{cliente_id}/duplicado/{fila}")
async def resolver_duplicado(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    cliente_id: uuid.UUID,
    fila: int,
    csrf: Annotated[str, Form()] = "",
):
    """Descarta un posible duplicado: son dos negocios distintos.

    Solo descarta. **Fusionar no se hace desde aquí** y no es una omisión: fusionar
    dos clientes significa mover ventas, cuentas por cobrar y cobranza de un UUID
    a otro, y cualquiera de esos renglones puede estar en un teléfono que todavía
    no sincroniza. Es una operación que necesita su propio diseño, no un botón al
    lado de una lista.
    """
    actor.exigir("clientes.fusionar")
    exigir_csrf(peticion, csrf)

    await sesion.execute(
        text(
            "UPDATE clientes_posibles_duplicados "
            "   SET estado = 'descartado', resuelto_por = :quien, resuelto_en = now() "
            " WHERE id = :fila AND estado = 'pendiente'"
        ),
        {"fila": fila, "quien": actor.usuario_id},
    )
    await sesion.commit()
    return _volver(cliente_id, guardado="Marcado como negocios distintos.")


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------


def _volver(cliente_id: uuid.UUID, *, error: str = "", guardado: str = ""):
    """Redirect-después-de-POST: recargar no debe reenviar el formulario."""
    if error:
        return RedirectResponse(
            f"/panel/clientes/{cliente_id}?error={quote(error)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        f"/panel/clientes/{cliente_id}?guardado={quote(guardado)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def _rutas(sesion) -> list[dict]:
    return (
        await sesion.execute(
            text(
                "SELECT r.id, r.codigo, r.nombre, u.nombre AS vendedor "
                "  FROM rutas r LEFT JOIN usuarios u ON u.id = r.vendedor_id "
                " WHERE r.activo ORDER BY r.codigo"
            )
        )
    ).mappings().all()


async def _listas(sesion) -> list[dict]:
    return (
        await sesion.execute(
            text(
                "SELECT id, codigo, nombre, es_default FROM listas_precios "
                " WHERE activo ORDER BY es_default DESC, nombre"
            )
        )
    ).mappings().all()


async def _canales(sesion) -> list[dict]:
    return (
        await sesion.execute(
            text("SELECT codigo, nombre FROM canales ORDER BY nombre")
        )
    ).mappings().all()
