"""Clientes: donde un prospecto de la calle se vuelve un cliente del negocio.

────────────────────────────────────────────────────────────────────────────
ESTA PANTALLA ES LA MITAD QUE LE FALTABA A LA FASE 2
────────────────────────────────────────────────────────────────────────────
Cuando un vendedor da de alta una tienda en la calle, el servidor la guarda
`estatus = 'prospecto'`, **sin código y sin lista de precios**
(`manejadores.crear_cliente`).

Pero hasta hoy nada podía terminar el trabajo. Un prospecto se quedaba prospecto
para siempre. El alta de campo estaba construida y era, en la práctica, un
formulario que no llevaba a ningún lado.

Aquí se cierra el circuito:

  confirmar   asigna el consecutivo, pone `activo` y le da lista de precios

La operación es de contado (ADR 0002 §81): aquí ya no hay condiciones de
crédito, saldo ni bloqueo. Las columnas viejas se quedan en la base como historia.

────────────────────────────────────────────────────────────────────────────
NADA SE BORRA
────────────────────────────────────────────────────────────────────────────
Un cliente que ya no existe se marca `inactivo`. Tiene ventas, y puede tener
una venta de esta mañana que todavía no ha sincronizado. Borrarlo
mandaría esa venta a cuarentena por una llave foránea: sería violar §0.1 desde la
oficina.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.api.admin.comun import (
    CapturaInvalida,
    SesionDep,
    auditar,
    leer_entero,
    render,
    texto_o_nulo,
)
from app.api.admin.plan_visita import DIAS, plan_de, reemplazar_plan
from app.api.admin.sesion_web import ActorWeb, exigir_csrf
from app.infra.codigo_de_cliente import siguiente_codigo
from app.infra.ubicacion_cliente import UbicacionInvalida, fijar_ubicacion, leer_coordenadas

router = APIRouter(prefix="/panel/clientes", tags=["panel"], include_in_schema=False)

# `clientes.administrar` es "Editar condiciones comerciales" (migración 0009). El
# vendedor no lo tiene; el gerente tampoco, porque gerencia monitorea y no opera.
PERMISO = "clientes.administrar"

ESTATUS = ["prospecto", "activo", "inactivo"]


# ---------------------------------------------------------------------------
# Lista
# ---------------------------------------------------------------------------


# Los días como se trabajan, con el número de la base (`extract(dow)`, 0 domingo).
NOMBRE_DEL_DIA = {1: "Lunes", 2: "Martes", 3: "Miércoles", 4: "Jueves", 5: "Viernes",
                  6: "Sábado", 0: "Domingo"}
ORDEN_DE_DIAS = (1, 2, 3, 4, 5, 6, 0)
# Más renglones ya no se leen en una pantalla: para eso están la ruta y el día.
LIMITE_LISTA = 1000


@router.get("", response_class=HTMLResponse)
async def listar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    q: str = "",
    filtro: str = "",
    ruta: str = "",
    dia: str = "",
    guardado: str = "",
) -> HTMLResponse:
    """Los clientes, agrupados por su ruta y su día de visita (ADR 0002 §122).

    «En el dashboard, que las tiendas estén agrupadas por el día y la ruta a la
    que pertenecen, no todas en general, y que se pueda filtrar por eso.»

    · **Por confirmar** (los prospectos de campo y lo que pide revisión) es una
      lista corta y va primero: abre ahí si hay alguno, porque es lo que espera
      una decisión de la oficina.
    · **Todos** abre con la tabla de rutas por día —cuántas tiendas le tocan a
      cada ruta cada día—, y cada cifra lleva a esas tiendas. Con una ruta o un
      día elegidos, las tiendas salen agrupadas por ruta y, en cada ruta, por
      día, en el orden de su recorrido. **Inactivos** se agrupa igual, de una vez.
    """
    actor.exigir("clientes.ver")

    conteos = (
        await sesion.execute(
            text(
                """
                SELECT
                  count(*) FILTER (WHERE estatus = 'prospecto' OR requiere_revision)
                    AS pendientes,
                  count(*) FILTER (WHERE estatus NOT IN ('inactivo', 'baja')) AS activos
                  FROM clientes
                """
            )
        )
    ).mappings().one()

    if filtro not in ("pendientes", "todos", "inactivos"):
        filtro = "pendientes" if conteos["pendientes"] and not (ruta or dia) else "todos"

    condiciones: list[str] = []
    parametros: dict[str, object] = {}
    if filtro == "pendientes":
        condiciones.append("(c.estatus = 'prospecto' OR c.requiere_revision)")
    elif filtro == "inactivos":
        condiciones.append("c.estatus IN ('inactivo', 'baja')")
    else:
        # `baja` es lo que deja «Eliminar» cuando el cliente tiene historia: para
        # quien usa el panel, eliminado. No se lista entre los activos.
        condiciones.append("c.estatus NOT IN ('inactivo', 'baja')")
    del_estado = list(condiciones)

    ruta_elegida = None
    if ruta == "sin":
        condiciones.append("c.ruta_id IS NULL")
    elif ruta:
        try:
            ruta_elegida = uuid.UUID(ruta)
        except ValueError:
            ruta = ""
        else:
            condiciones.append("c.ruta_id = :ruta")
            parametros["ruta"] = ruta_elegida

    dia_elegido = None
    if dia == "sin":
        condiciones.append(
            "NOT EXISTS (SELECT 1 FROM clientes_frecuencia f WHERE f.cliente_id = c.id)"
        )
    elif dia.isdigit() and int(dia) in NOMBRE_DEL_DIA:
        dia_elegido = int(dia)
        condiciones.append(
            "EXISTS (SELECT 1 FROM clientes_frecuencia f "
            "         WHERE f.cliente_id = c.id AND f.dia_semana = :dia)"
        )
        parametros["dia"] = dia_elegido
    else:
        dia = ""

    busqueda = q.strip()
    if busqueda:
        condiciones.append(
            "(c.nombre_comercial ILIKE :q OR c.codigo ILIKE :q OR c.telefono ILIKE :q)"
        )
        parametros["q"] = f"%{busqueda}%"

    # La tabla de rutas por día: la vista general, sin listar 1,800 tiendas.
    matriz = None
    if filtro == "todos":
        matriz = await rutas_por_dia(sesion, " AND ".join(del_estado))

    # Sin ruta, día ni búsqueda, «Todos» se queda en la tabla: no todas en general.
    # Las por confirmar y las inactivas son pocas: se listan de una vez.
    listar_tiendas = filtro != "todos" or bool(ruta or dia or busqueda)
    filas = []
    recortada = False
    if listar_tiendas:
        filas = [
            dict(f)
            for f in (
                await sesion.execute(
                    text(
                        f"""
                        SELECT c.id, c.codigo, c.nombre_comercial, c.estatus,
                               c.origen_alta, c.requiere_revision, c.revision_motivo,
                               c.calle, c.numero, c.colonia, c.municipio, c.telefono,
                               c.creado_en, c.secuencia, c.ruta_id,
                               (c.lat IS NOT NULL AND c.lng IS NOT NULL) AS con_ubicacion,
                               r.nombre AS ruta, r.codigo AS ruta_codigo,
                               u.nombre AS vendedor, l.nombre AS lista_precios,
                               COALESCE((SELECT array_agg(DISTINCT f.dia_semana)
                                           FROM clientes_frecuencia f
                                          WHERE f.cliente_id = c.id), '{{}}') AS dias
                          FROM clientes c
                          LEFT JOIN rutas r ON r.id = c.ruta_id
                          LEFT JOIN usuarios u ON u.id = r.vendedor_id
                          LEFT JOIN listas_precios l ON l.id = c.lista_precios_id
                         WHERE {" AND ".join(condiciones)}
                         ORDER BY (c.estatus = 'prospecto') DESC, r.codigo NULLS LAST,
                                  c.secuencia NULLS LAST, c.nombre_comercial
                         LIMIT {LIMITE_LISTA + 1}
                        """  # noqa: S608 — las condiciones son constantes del código
                    ),
                    parametros,
                )
            ).mappings().all()
        ]
        recortada = len(filas) > LIMITE_LISTA
        filas = filas[:LIMITE_LISTA]

    return render(
        peticion,
        "clientes.html",
        {
            "filas": filas,
            "grupos": _agrupar(filas, dia_elegido, solo_sin_dia=dia == "sin")
            if filtro != "pendientes" else [],
            "recortada": recortada,
            "limite": LIMITE_LISTA,
            "matriz": matriz,
            "q": busqueda,
            "filtro": filtro,
            "ruta": ruta,
            "dia": dia,
            "rutas": await _rutas(sesion),
            "dias": [(d, NOMBRE_DEL_DIA[d]) for d in ORDEN_DE_DIAS],
            "nombre_del_dia": NOMBRE_DEL_DIA,
            "conteos": conteos,
            "guardado": guardado,
            "puede_editar": actor.puede(PERMISO),
        },
        actor=actor,
        seccion="Clientes",
    )


async def rutas_por_dia(sesion, del_estado: str) -> dict:
    """Cuántas tiendas le tocan a cada ruta cada día, y las que no tienen día."""
    celdas = (
        await sesion.execute(
            text(
                f"""
                SELECT c.ruta_id, f.dia_semana, count(DISTINCT c.id) AS tiendas
                  FROM clientes c
                  LEFT JOIN clientes_frecuencia f ON f.cliente_id = c.id
                 WHERE {del_estado}
                 GROUP BY c.ruta_id, f.dia_semana
                """  # noqa: S608 — la condición es una constante del código
            )
        )
    ).mappings().all()
    totales = dict(
        (
            await sesion.execute(
                text(
                    f"SELECT c.ruta_id, count(*) FROM clientes c WHERE {del_estado} "  # noqa: S608
                    " GROUP BY c.ruta_id"
                )
            )
        ).all()
    )
    rutas = {
        r["id"]: r
        for r in (
            await sesion.execute(
                text(
                    "SELECT r.id, r.codigo, r.nombre, r.activo, u.nombre AS vendedor "
                    "  FROM rutas r LEFT JOIN usuarios u ON u.id = r.vendedor_id"
                )
            )
        ).mappings().all()
    }
    por_ruta: dict = {}
    for c in celdas:
        por_ruta.setdefault(c["ruta_id"], {})[c["dia_semana"]] = c["tiendas"]
    renglones = []
    for ruta_id in sorted(
        por_ruta, key=lambda r: (r is None, rutas[r]["codigo"] if r in rutas else "")
    ):
        r = rutas.get(ruta_id)
        renglones.append({
            "clave": str(ruta_id) if ruta_id else "sin",
            "codigo": r["codigo"] if r else None,
            "nombre": r["nombre"] if r else "Sin ruta",
            "vendedor": r["vendedor"] if r else None,
            "activa": r["activo"] if r else True,
            "por_dia": por_ruta[ruta_id],
            "total": totales.get(ruta_id, 0),
        })
    con_domingo = any(r["por_dia"].get(0) for r in renglones)
    return {
        "renglones": renglones,
        "dias": [d for d in ORDEN_DE_DIAS if d != 0 or con_domingo],
        "total": sum(totales.values()),
        "por_dia": {
            d: sum(r["por_dia"].get(d, 0) for r in renglones) for d in ORDEN_DE_DIAS
        },
        "sin_dia": sum(r["por_dia"].get(None, 0) for r in renglones),
    }


def _agrupar(filas: list[dict], dia: int | None, *, solo_sin_dia: bool) -> list[dict]:
    """Ruta → día → tiendas en el orden del recorrido. Una tienda que toca dos
    días sale en los dos, y dice cuál es el otro."""
    grupos: dict = {}
    for f in filas:
        dias = sorted(f["dias"], key=ORDEN_DE_DIAS.index) or [None]
        for d in dias:
            if (dia is not None and d != dia) or (solo_sin_dia and d is not None):
                continue
            ruta = grupos.setdefault(f["ruta_id"], {
                "clave": str(f["ruta_id"]) if f["ruta_id"] else "sin",
                "codigo": f["ruta_codigo"],
                "nombre": f["ruta"] or "Sin ruta",
                "vendedor": f["vendedor"],
                "dias": {},
                "tiendas": set(),
            })
            ruta["tiendas"].add(f["id"])
            ruta["dias"].setdefault(d, []).append(
                {**f, "otros_dias": [NOMBRE_DEL_DIA[o][:3] for o in dias if o not in (d, None)]}
            )
    resultado = []
    for ruta in sorted(grupos.values(), key=lambda g: (g["codigo"] is None, g["codigo"] or "")):
        resultado.append({
            **ruta,
            "tiendas": len(ruta["tiendas"]),
            "dias": [
                {
                    "dia": d,
                    "titulo": NOMBRE_DEL_DIA[d] if d is not None else "Sin día de visita",
                    "filas": ruta["dias"][d],
                    "sin_ubicacion": sum(1 for f in ruta["dias"][d] if not f["con_ubicacion"]),
                }
                for d in [*ORDEN_DE_DIAS, None]
                if d in ruta["dias"]
            ],
        })
    return resultado


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
            "duplicados": duplicados,
            "rutas": await _rutas(sesion),
            "listas": await _listas(sesion),
            "canales": await _canales(sesion),
            "estatus_posibles": ESTATUS,
            "error": error,
            "guardado": guardado,
            "puede_editar": actor.puede(PERMISO),
            "puede_ubicar": actor.puede("clientes.ubicar"),
            "dias_de_visita": DIAS,
            "plan": (await plan_de(sesion, [cliente_id]))[cliente_id],
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

    1. **El código.** El número que sigue —1934, 1935… (ADR 0002 §122)—, de
       `seq_codigo_cliente` (migración 0014) y no de `max()+1`: dos personas
       confirmando al mismo tiempo obtendrían el mismo número y una vería un
       error de UNIQUE que no significa nada para ella.
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
        # El número que sigue: 1934, 1935… (ADR 0002 §122).
        codigo = await siguiente_codigo(sesion)

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


@router.post("/{cliente_id}/condiciones")
async def guardar_condiciones(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    cliente_id: uuid.UUID,
    lista_precios_id: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """La lista de precios: lo que decide la oficina y lee el teléfono.

    Antes aquí también se decidía el crédito. La operación es de contado (ADR 0002
    §81): la única condición comercial que queda es con qué precios se le vende.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)
    try:
        lista = uuid.UUID(lista_precios_id) if lista_precios_id else None
    except ValueError:
        return _volver(cliente_id, error="Esa lista de precios no existe.")
    await sesion.execute(
        text(
            "UPDATE clientes SET lista_precios_id = :lista, actualizado_en = now() "
            " WHERE id = :id"
        ),
        {"id": cliente_id, "lista": lista},
    )
    await sesion.commit()
    return _volver(cliente_id, guardado="Lista de precios guardada.")


@router.post("/{cliente_id}/ubicacion")
async def guardar_ubicacion(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    cliente_id: uuid.UUID,
    lat: Annotated[str, Form()] = "",
    lng: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Corrige la ubicación a mano (ADR 0002 §84). La del GPS se toma en el teléfono."""
    actor.exigir("clientes.ubicar")
    exigir_csrf(peticion, csrf)
    try:
        la, lo = leer_coordenadas(lat, lng)
        await fijar_ubicacion(
            sesion, cliente_id, lat=la, lng=lo, origen="manual", quien=actor.usuario_id
        )
    except UbicacionInvalida as e:
        return _volver(cliente_id, error=str(e))
    await sesion.commit()
    return _volver(
        cliente_id,
        guardado="Ubicación guardada. Los teléfonos de su ruta la reciben al sincronizar.",
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


# Los documentos que hacen que un cliente NO se pueda borrar de verdad. Todos
# apuntan a `clientes` con una llave que no se borra en cascada, a propósito: una
# venta es un papel que alguien tiene en la mano.
_DOCUMENTOS_DEL_CLIENTE = (
    ("ventas", "venta(s)"),
    ("cobros", "cobro(s)"),
    ("no_drops", "visita(s) sin venta"),
    ("mermas", "devolución(es)"),
    ("cuentas_por_cobrar", "cuenta(s) por cobrar"),
)


@router.post("/{cliente_id}/eliminar")
async def eliminar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    cliente_id: uuid.UUID,
    confirmo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Quita al cliente de la operación. Siempre funciona, y decide qué es seguro.

    ───────────────────────────────────────────────────────────────────────────
    UN BOTÓN, TRES DESENLACES
    ───────────────────────────────────────────────────────────────────────────
    · **Sin ningún documento** —el prospecto mal capturado, el duplicado— se
      BORRA. El disparador publica un `delete` y el teléfono lo da de baja en su
      base local sin tocar nada más.
    · **Con historia** se DA DE BAJA (`estatus = 'baja'`). Para quien usa el
      panel el efecto es el mismo: desaparece de la ruta, de las listas y de
      todos los teléfonos en el siguiente pull. Pero sus ventas y cobros siguen en
      pie, porque borrarlo de verdad obligaría a borrar esos papeles. Se reactiva
      cambiando el estatus en sus datos.
    · **Si todavía debe** no se hace nada y se dice cuánto. Ocultarlo del teléfono
      dejaría al vendedor sin poder cobrarle: la cuenta seguiría abierta en el
      servidor, sin nadie en la calle que la pudiera cerrar.

    La primera versión de «eliminar» de productos negaba el borrado y mandaba a
    quitar la casilla «activo». Para clientes la dirección pidió que el botón
    simplemente funcione, y la distinción se hace aquí en vez de pedírsela a la
    persona.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    cliente = (
        await sesion.execute(
            text(
                "SELECT id, codigo, nombre_comercial, estatus, ruta_id "
                "  FROM clientes WHERE id = :id"
            ),
            {"id": cliente_id},
        )
    ).mappings().first()
    if cliente is None:
        return _a_lista(guardado="Ese cliente ya no existe.")

    if not confirmo:
        return _volver(
            cliente_id,
            error="Marca la casilla de confirmación para eliminar al cliente.",
        )

    debe = (
        await sesion.execute(
            text(
                "SELECT COALESCE(sum(saldo), 0) FROM cuentas_por_cobrar "
                " WHERE cliente_id = :c AND estado IN ('abierta', 'parcial')"
            ),
            {"c": cliente_id},
        )
    ).scalar_one()
    if debe and debe > 0:
        return _volver(
            cliente_id,
            error=(
                f"«{cliente['nombre_comercial']}» todavía debe ${debe:,.2f}. "
                "Si se elimina, desaparece del teléfono del vendedor y ya nadie "
                "puede cobrarle. Cobra o cancela esa deuda primero."
            ),
        )

    historia: list[str] = []
    for tabla, como_se_llama in _DOCUMENTOS_DEL_CLIENTE:
        cuantos = (
            await sesion.execute(
                text(f"SELECT count(*) FROM {tabla} WHERE cliente_id = :c"),  # noqa: S608
                {"c": cliente_id},
            )
        ).scalar_one()
        if cuantos:
            historia.append(f"{cuantos} {como_se_llama}")

    antes = json.dumps(
        {
            "codigo": cliente["codigo"],
            "nombre_comercial": cliente["nombre_comercial"],
            "estatus": cliente["estatus"],
            "ruta_id": str(cliente["ruta_id"]) if cliente["ruta_id"] else None,
        },
        ensure_ascii=False,
    )

    if historia:
        await sesion.execute(
            text(
                "UPDATE clientes SET estatus = 'baja', actualizado_en = now() "
                " WHERE id = :id"
            ),
            {"id": cliente_id},
        )
        accion, mensaje = (
            "dar_de_baja",
            f"«{cliente['nombre_comercial']}» se dio de baja: tiene "
            + ", ".join(historia)
            + ", y borrarlo rompería esos documentos. Ya no aparece en la ruta y "
            "sale de los teléfonos en su siguiente sincronización. Se reactiva "
            "cambiando su estatus.",
        )
    else:
        accion, mensaje = (
            "eliminar",
            f"«{cliente['nombre_comercial']}» se eliminó. Sale de los teléfonos en "
            "su siguiente sincronización.",
        )

    # El antes, ANTES del DELETE: después no hay de dónde leerlo.
    await sesion.execute(
        text(
            """
            INSERT INTO auditoria
              (entidad, entidad_id, accion, usuario_id, datos_antes, ocurrido_en)
            VALUES ('cliente', :id, :accion, :quien, CAST(:antes AS jsonb), now())
            """
        ),
        {"id": cliente_id, "accion": accion, "quien": actor.usuario_id, "antes": antes},
    )
    if not historia:
        await sesion.execute(text("DELETE FROM clientes WHERE id = :id"), {"id": cliente_id})

    await sesion.commit()
    return _a_lista(guardado=mensaje)


def _a_lista(*, guardado: str = "") -> RedirectResponse:
    """A la lista completa, con el mensaje: el cliente eliminado ya no tiene ficha."""
    return RedirectResponse(
        f"/panel/clientes?filtro=todos&guardado={quote(guardado)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{cliente_id}/visita")
async def guardar_visita(peticion: Request, actor: ActorWeb, sesion: SesionDep,
                         cliente_id: uuid.UUID):
    """Los días de visita del cliente, con semanas del mes si las tiene.

    Sin semanas marcadas, cada día es «todas las semanas». Con semanas, cada día
    marcado vale solo en esas semanas: lunes de las semanas 1 y 3 es la ruta
    quincenal. La pantalla de la ruta muestra estos clientes como personalizados,
    porque siete casillas no pueden mostrar este plan sin aplanarlo.
    """
    actor.exigir(PERMISO)
    formulario = await peticion.form()
    exigir_csrf(peticion, str(formulario.get("csrf", "")))
    existe = (
        await sesion.execute(text("SELECT 1 FROM clientes WHERE id = :c"), {"c": cliente_id})
    ).first()
    if existe is None:
        return RedirectResponse("/panel/clientes", status_code=status.HTTP_303_SEE_OTHER)

    dias = [dow for dow, _, _ in DIAS if formulario.get(f"dia_{dow}")]
    semanas = [n for n in (1, 2, 3, 4) if formulario.get(f"semana_{n}")]
    deseado = {(d, s) for d in dias for s in (semanas or [None])}
    agregados, quitados = await reemplazar_plan(sesion, cliente_id, deseado)
    await auditar(
        sesion,
        entidad="cliente",
        entidad_id=cliente_id,
        accion="plan_de_visita",
        quien=actor.usuario_id,
        despues={"dias": dias, "semanas": semanas},
    )
    await sesion.commit()
    if not (agregados or quitados):
        return _volver(cliente_id, guardado="Los días de visita no cambiaron.")
    if not dias:
        return _volver(
            cliente_id,
            guardado="Se quitó del plan: ya no aparece en «hoy te tocan» de ningún día.",
        )
    return _volver(
        cliente_id,
        guardado="Días de visita guardados. El teléfono de la ruta los recibe en su "
        "siguiente sincronización.",
    )


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
