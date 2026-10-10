"""El piloto de campo: el cuadre contra el papel y la bitácora.

────────────────────────────────────────────────────────────────────────────
ESTA PANTALLA ES LA QUE HACE QUE EL PAPEL EN PARALELO VALGA ALGO
────────────────────────────────────────────────────────────────────────────
El plan pide dos semanas con el proceso de papel corriendo junto a la app. El
papel no es un respaldo por si la app falla —para eso está el outbox— es el
PATRÓN DE MEDIDA: lo único que puede atrapar la venta que ocurrió y nunca entró
al sistema. Y una venta que no entró no aparece en ninguna métrica, ningún log
ni ningún tablero, porque todos leen lo que sí entró.

Si nadie captura el papel y nadie compara, el vendedor hizo doble trabajo
durante dos semanas para producir una anécdota.

────────────────────────────────────────────────────────────────────────────
LA CAPTURA ES DE CINCO CAMPOS, Y ESO NO ES PEREZA
────────────────────────────────────────────────────────────────────────────
Documentos, importe, cobranza y visitas. Se tecleó con el papel en la mano, a
las ocho de la mañana, antes de que salga el camión, por la persona que tiene
diez cosas más que hacer.

Un formulario que pidiera el detalle por cliente sería más exacto y no se
llenaría más allá del día tres — y un piloto sin captura a partir del día tres
no mide nada (de ahí que `cobertura_papel` sea el primer criterio y sea
bloqueante). Cuatro cifras diarias sostenidas catorce días valen
infinitamente más que el detalle perfecto de tres días.

────────────────────────────────────────────────────────────────────────────
POR QUÉ LA INCIDENCIA SE CAPTURA AQUÍ Y NO EN LA APP DEL VENDEDOR
────────────────────────────────────────────────────────────────────────────
Era tentador: una pantalla de "reportar problema" en el teléfono, con su
operación en el outbox, y el vendedor registrando en el momento.

Y habría sido un error. **No se le agregan funciones a la app que se está
poniendo a prueba.** Esa pantalla sería código nuevo sin piloto dentro del
piloto, con su propio camino de sincronización que puede fallar — y si falla,
se pierden justo los reportes de las fallas. Peor: la incidencia más importante
que puede ocurrir es «la app no abrió», y en ese escenario ninguna pantalla de
la app puede reportarla.

El canal es el que ya existe y no depende de nosotros: el vendedor habla por
teléfono y la oficina teclea aquí, con la hora. Vale más una bitácora de papel
que funciona que una digital que comparte el destino de lo que vigila.
"""

from __future__ import annotations

import uuid
from datetime import date, time, timedelta
from decimal import Decimal
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from starlette import status

from app.api.admin.comun import (
    CapturaInvalida,
    SesionDep,
    leer_dinero,
    leer_entero,
    render,
    texto_o_nulo,
)
from app.api.admin.sesion_web import ActorWeb, exigir_csrf
from app.domain.piloto import (
    SQL_CRITERIOS,
    SQL_CUARENTENA,
    SQL_INCIDENCIAS,
    SQL_JORNADAS_CAPTURADAS,
    SQL_PILOTO_ACTIVO,
    SQL_SISTEMA_DE_UN_DIA,
    SQL_SISTEMA_POR_DIA,
    VEREDICTOS,
    Ventana,
    fin_por_omision,
    inicio_por_omision,
    medir,
)

router = APIRouter(prefix="/panel/piloto", tags=["panel"], include_in_schema=False)

PERMISO = "piloto.administrar"

CATEGORIAS = {
    "app": "La app (se cerró, no dejó, se vio mal)",
    "sincronizacion": "La sincronización (no subió, no bajó, tardó)",
    "impresora": "La impresora o el ticket",
    "datos": "Los datos (precio, cliente o producto mal)",
    "proceso": "El proceso (la app no encaja con cómo se trabaja)",
    "equipo": "El teléfono (batería, pantalla, señal)",
}

SEVERIDADES = {
    "bloqueo": "Bloqueo — no se pudo hacer la operación",
    "estorbo": "Estorbo — se pudo, con trabajo o con un rodeo",
    "molestia": "Molestia — se pudo; está mal pero no costó nada",
}

# Topes de error de dedo, no reglas de negocio. Un piloto de una ruta no hace
# 500 documentos en un día; un 5000 teclado por accidente dejaría el criterio de
# faltantes en rojo toda la semana sin que nadie entendiera por qué.
MAXIMO_DOCUMENTOS_DIA = 500
MAXIMO_VISITAS_DIA = 500


def _redirigir(destino: str, **params) -> RedirectResponse:
    """Un 303 con el aviso en la cola, escapado.

    `quote` no es un detalle: los avisos de esta pantalla traen comas, acentos y
    rayas, y uno con un `&` partiría la cola en dos parámetros y mostraría media
    frase.
    """
    cola = "&".join(f"{k}={quote(str(v))}" for k, v in params.items() if v)
    return RedirectResponse(
        f"{destino}?{cola}" if cola else destino,
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def _activo(sesion):
    return (await sesion.execute(text(SQL_PILOTO_ACTIVO))).mappings().first()


def _fecha(texto: str, omision: date) -> date:
    try:
        return date.fromisoformat(texto) if texto else omision
    except ValueError:
        return omision


async def _leer_todo(sesion, piloto, hoy: date):
    """Las cinco lecturas que necesita la evaluación, con su ventana."""
    ventana = Ventana(inicio=piloto["inicio"], fin=piloto["fin"], hoy=hoy)
    parametros = {
        "vendedor": piloto["vendedor_id"],
        "desde": ventana.inicio,
        "hasta": ventana.hasta_operacion,
    }
    # El piloto que no ha empezado no tiene días: `generate_series` con el fin
    # antes del inicio devuelve vacío sola, sin caso especial.
    dias = (
        (await sesion.execute(text(SQL_SISTEMA_POR_DIA), parametros)).mappings().all()
        if ventana.dia_del_piloto
        else []
    )
    jornadas = (
        await sesion.execute(text(SQL_JORNADAS_CAPTURADAS), {"piloto": piloto["id"]})
    ).mappings().all()
    incidencias = (
        await sesion.execute(text(SQL_INCIDENCIAS), {"piloto": piloto["id"]})
    ).mappings().all()
    cuarentena = (
        (await sesion.execute(text(SQL_CUARENTENA), parametros)).scalar()
        if ventana.dia_del_piloto
        else 0
    ) or 0
    criterios = (await sesion.execute(text(SQL_CRITERIOS))).mappings().all()
    return ventana, dias, jornadas, incidencias, cuarentena, criterios


# ===========================================================================
# El cuadre
# ===========================================================================
@router.get("", response_class=HTMLResponse)
async def ver(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    dia: str = "",
    aviso: str = "",
    error: str = "",
) -> HTMLResponse:
    """El veredicto arriba y el cuadre día por día abajo.

    El veredicto va PRIMERO a propósito. La tabla de cuadre es catorce
    renglones de cifras y quien la abre a diario deja de leerla; la pregunta que
    de verdad se contesta aquí es "¿esto se puede desplegar?", y tiene que estar
    visible sin desplazarse.
    """
    actor.exigir(PERMISO)
    hoy = date.today()
    piloto = await _activo(sesion)

    if piloto is None:
        # Sin piloto no hay pantalla que mostrar, pero sí hay una que ofrecer:
        # el formulario para definirlo, con el lunes siguiente ya puesto.
        inicio = inicio_por_omision(hoy)
        return render(
            peticion,
            "piloto_definir.html",
            {
                "inicio": inicio.isoformat(),
                "fin": fin_por_omision(inicio).isoformat(),
                "vendedores": (
                    await sesion.execute(
                        text(
                            """
                            SELECT u.id, u.codigo, u.nombre,
                                   COALESCE(r.rutas, '') AS rutas
                              FROM usuarios u
                              LEFT JOIN LATERAL (
                                    SELECT string_agg(ru.codigo, ', '
                                                      ORDER BY ru.codigo) AS rutas
                                      FROM usuarios_rutas ur
                                      JOIN rutas ru ON ru.id = ur.ruta_id
                                     WHERE ur.usuario_id = u.id
                              ) r ON true
                             WHERE u.activo AND u.rol_codigo = 'vendedor'
                             ORDER BY u.codigo
                            """
                        )
                    )
                ).mappings().all(),
                "rutas": (
                    await sesion.execute(
                        text("SELECT id, codigo, nombre FROM rutas WHERE activo "
                             "ORDER BY codigo")
                    )
                ).mappings().all(),
                "cerrados": (
                    await sesion.execute(
                        text(
                            """
                            SELECT p.codigo, p.inicio, p.fin, p.veredicto,
                                   p.cerrado_en, u.nombre AS vendedor
                              FROM pilotos p
                              JOIN usuarios u ON u.id = p.vendedor_id
                             WHERE NOT p.activo
                             ORDER BY p.inicio DESC
                            """
                        )
                    )
                ).mappings().all(),
                "veredictos": VEREDICTOS,
                "aviso": aviso,
                "error": error,
            },
            actor=actor,
            seccion="Desempeño",
        )

    ventana, dias, jornadas, incidencias, cuarentena, criterios = await _leer_todo(
        sesion, piloto, hoy
    )
    veredicto = medir(
        criterios=criterios,
        dias=dias,
        jornadas=jornadas,
        incidencias=incidencias,
        cuarentena=cuarentena,
        ventana=ventana,
    )

    # El renglón por día: el sistema de hoy junto al papel, si ya se capturó.
    capturadas = {j["fecha"]: j for j in jornadas}
    por_dia = [
        {**d, "papel": capturadas.get(d["fecha"]),
         "incidencias": sum(
             1 for i in incidencias if i["fecha_operativa"] == d["fecha"]
         )}
        for d in dias
    ]

    # Qué día ofrece el formulario: el primero trabajado, sin capturar y ya
    # vencido. Es el que la oficina viene a teclear; preseleccionar "hoy" haría
    # que capturara el día que todavía no termina.
    pendientes = [
        d["fecha"] for d in dias
        if d["fecha"] <= ventana.hasta_cuadre
        and d["fecha"] not in capturadas
        and (d["documentos"] or Decimal(str(d["cobranza"])) != 0)
    ]
    elegido = _fecha(dia, pendientes[0] if pendientes else ventana.hasta_cuadre)
    sistema = (
        await sesion.execute(
            text(SQL_SISTEMA_DE_UN_DIA),
            {"vendedor": piloto["vendedor_id"], "fecha": elegido},
        )
    ).mappings().first()

    return render(
        peticion,
        "piloto.html",
        {
            "piloto": piloto,
            "ventana": ventana,
            # El día en que se podrá capturar el primer cuadre: la jornada del
            # primer día, cerrada. Se calcula aquí y no en la plantilla porque
            # Jinja no tiene aritmética de fechas sin meterle un filtro.
            "primer_cuadre": piloto["inicio"] + timedelta(days=1),
            "veredicto": veredicto,
            "por_dia": por_dia,
            "pendientes": pendientes,
            "dia": elegido.isoformat(),
            "dia_capturado": capturadas.get(elegido),
            "sistema": sistema,
            "incidencias_abiertas": sum(
                1 for i in incidencias if i["resuelta_en"] is None
            ),
            "bloqueos": sum(1 for i in incidencias if i["severidad"] == "bloqueo"),
            "veredictos": VEREDICTOS,
            "aviso": aviso,
            "error": error,
        },
        actor=actor,
        seccion="Desempeño",
    )


@router.post("/definir")
async def definir(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    vendedor_id: Annotated[uuid.UUID, Form()],
    ruta_id: Annotated[uuid.UUID, Form()],
    csrf: str = Form(""),
    codigo: str = Form(""),
    inicio: str = Form(""),
    fin: str = Form(""),
) -> RedirectResponse:
    """Define el piloto activo. Uno a la vez, por índice parcial en la tabla."""
    exigir_csrf(peticion, csrf)
    actor.exigir(PERMISO)

    hoy = date.today()
    arranca = _fecha(inicio, inicio_por_omision(hoy))
    termina = _fecha(fin, fin_por_omision(arranca))
    clave = (codigo or f"f3-{arranca:%y%m%d}").strip().lower()

    if termina < arranca + timedelta(days=6):
        return _redirigir(
            "/panel/piloto",
            error="Un piloto de menos de una semana no tiene segunda semana "
                  "contra la que comparar la primera.",
        )

    await sesion.execute(
        text(
            """
            INSERT INTO pilotos
                (codigo, vendedor_id, ruta_id, inicio, fin, creado_por)
            VALUES (:codigo, :vendedor, :ruta, :inicio, :fin, :quien)
            """
        ),
        {
            "codigo": clave,
            "vendedor": vendedor_id,
            "ruta": ruta_id,
            "inicio": arranca,
            "fin": termina,
            "quien": actor.usuario_id,
        },
    )
    await sesion.commit()
    return _redirigir("/panel/piloto", aviso=f"Piloto {clave} definido.")


@router.post("/jornada")
async def capturar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    csrf: str = Form(""),
    dia: str = Form(""),
    documentos: str = Form(""),
    importe: str = Form(""),
    cobranza: str = Form(""),
    visitas: str = Form(""),
    observaciones: str = Form(""),
) -> RedirectResponse:
    """Guarda el cuadre de un día, congelando lo que el sistema dice AHORA.

    Las cifras del sistema NO vienen del formulario: se vuelven a leer aquí. Si
    vinieran de la pantalla, el cuadre guardaría lo que el sistema decía cuando
    se CARGÓ la página, que pueden ser veinte minutos y una sincronización
    antes — y la diferencia congelada mediría el tiempo que tardó alguien en
    teclear.
    """
    exigir_csrf(peticion, csrf)
    actor.exigir(PERMISO)
    piloto = await _activo(sesion)
    if piloto is None:
        return _redirigir("/panel/piloto", error="No hay un piloto activo.")

    fecha = _fecha(dia, date.today() - timedelta(days=1))
    if not (piloto["inicio"] <= fecha <= piloto["fin"]):
        return _redirigir(
            "/panel/piloto",
            error=f"El {fecha:%d/%m} no está dentro del piloto "
                  f"({piloto['inicio']:%d/%m} al {piloto['fin']:%d/%m}).",
        )
    if fecha >= date.today():
        # Capturar el papel de hoy sería capturar una jornada a medias: el
        # vendedor todavía está en la calle y el cuadre quedaría congelado
        # contra un día incompleto, con un faltante que no es un faltante.
        return _redirigir(
            "/panel/piloto",
            error="El cuadre de un día se captura al día siguiente, con la "
                  "jornada cerrada.",
        )

    try:
        docs = leer_entero(documentos, campo="Los documentos del papel",
                           maximo=MAXIMO_DOCUMENTOS_DIA)
        monto = leer_dinero(importe, campo="El importe del papel")
        cobrado = leer_dinero(cobranza, campo="La cobranza del papel")
        vistos = leer_entero(visitas, campo="Las visitas del papel",
                             maximo=MAXIMO_VISITAS_DIA)
    except CapturaInvalida as e:
        return _redirigir("/panel/piloto", dia=fecha.isoformat(), error=str(e))

    if docs and monto == 0:
        return _redirigir(
            "/panel/piloto", dia=fecha.isoformat(),
            error=f"{docs} documento(s) por $0.00 no puede ser: si el papel "
                  "trae ventas, trae importe.",
        )
    if monto and not docs:
        # Al revés también. Y la cobranza SÍ puede venir sin documentos —un día
        # de pasar a cobrar sin vender es normal— así que solo se revisa el
        # importe de venta.
        return _redirigir(
            "/panel/piloto", dia=fecha.isoformat(),
            error=f"${monto:,.2f} de venta con 0 documentos no puede ser: "
                  "ese importe salió de alguna nota.",
        )

    sistema = (
        await sesion.execute(
            text(SQL_SISTEMA_DE_UN_DIA),
            {"vendedor": piloto["vendedor_id"], "fecha": fecha},
        )
    ).mappings().one()

    await sesion.execute(
        text(
            """
            INSERT INTO piloto_jornadas
                (piloto_id, fecha_operativa,
                 documentos_papel, importe_papel, cobranza_papel, visitas_papel,
                 documentos_sistema, importe_sistema, cobranza_sistema,
                 capturado_por, observaciones)
            VALUES (:piloto, :fecha, :docs, :monto, :cobrado, :vistos,
                    :s_docs, :s_monto, :s_cobrado, :quien, :obs)
            ON CONFLICT (piloto_id, fecha_operativa) DO UPDATE SET
                documentos_papel   = excluded.documentos_papel,
                importe_papel      = excluded.importe_papel,
                cobranza_papel     = excluded.cobranza_papel,
                visitas_papel      = excluded.visitas_papel,
                documentos_sistema = excluded.documentos_sistema,
                importe_sistema    = excluded.importe_sistema,
                cobranza_sistema   = excluded.cobranza_sistema,
                capturado_en       = now(),
                capturado_por      = excluded.capturado_por,
                observaciones      = excluded.observaciones
            """
        ),
        {
            "piloto": piloto["id"],
            "fecha": fecha,
            "docs": docs,
            "monto": monto,
            "cobrado": cobrado,
            # Cero visitas es "el papel no las trae", no "no visitó a nadie":
            # ningún día de ruta tiene cero visitas.
            "vistos": vistos or None,
            "s_docs": sistema["documentos"],
            "s_monto": sistema["importe"],
            "s_cobrado": sistema["cobranza"],
            "quien": actor.usuario_id,
            "obs": texto_o_nulo(observaciones, maximo=500),
        },
    )
    await sesion.commit()

    diferencia = sistema["documentos"] - docs
    if diferencia < 0:
        aviso = (
            f"Cuadre del {fecha:%d/%m} guardado. OJO: al sistema le faltan "
            f"{-diferencia} documento(s) — si siguen faltando mañana, es una "
            "venta perdida y no un retraso."
        )
    elif diferencia > 0:
        aviso = (
            f"Cuadre del {fecha:%d/%m} guardado. El sistema tiene {diferencia} "
            "documento(s) más que el papel: revisa si una venta se capturó dos "
            "veces o si falta una nota en el papel."
        )
    else:
        aviso = f"Cuadre del {fecha:%d/%m} guardado: los documentos cuadran."
    return _redirigir("/panel/piloto", aviso=aviso)


@router.post("/cerrar")
async def cerrar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    csrf: str = Form(""),
    veredicto: str = Form(""),
    nota: str = Form(""),
) -> RedirectResponse:
    """Cierra el piloto con un veredicto escrito y firmado por alguien.

    El sistema SUGIERE el veredicto a partir de los criterios y no lo dicta.
    Poner esto en siete camiones es una decisión de negocio, y una decisión que
    nadie firmó es una que nadie sostiene cuando algo salga mal en la ruta 4.
    """
    exigir_csrf(peticion, csrf)
    actor.exigir(PERMISO)
    piloto = await _activo(sesion)
    if piloto is None:
        return _redirigir("/panel/piloto", error="No hay un piloto activo.")
    if veredicto not in VEREDICTOS:
        return _redirigir("/panel/piloto", error="Falta elegir el veredicto.")
    if not (nota or "").strip():
        # Un veredicto sin razón escrita es un botón, no una decisión: a los tres
        # meses nadie recuerda por qué se dijo "repetir".
        return _redirigir(
            "/panel/piloto",
            error="Escribe en qué se basa el veredicto: es lo que se va a leer "
                  "dentro de tres meses.",
        )

    await sesion.execute(
        text(
            """
            UPDATE pilotos
               SET activo = false, cerrado_en = now(),
                   veredicto = :v, veredicto_nota = :nota
             WHERE id = :id
            """
        ),
        {"id": piloto["id"], "v": veredicto, "nota": nota.strip()[:2000]},
    )
    await sesion.commit()
    return _redirigir(
        "/panel/piloto", aviso=f"Piloto cerrado: {VEREDICTOS[veredicto]}."
    )


# ===========================================================================
# La bitácora
# ===========================================================================
@router.get("/incidencias", response_class=HTMLResponse)
async def ver_incidencias(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    aviso: str = "",
    error: str = "",
) -> HTMLResponse:
    actor.exigir(PERMISO)
    piloto = await _activo(sesion)
    if piloto is None:
        return _redirigir("/panel/piloto", error="No hay un piloto activo.")

    incidencias = (
        await sesion.execute(text(SQL_INCIDENCIAS), {"piloto": piloto["id"]})
    ).mappings().all()
    ventana = Ventana(inicio=piloto["inicio"], fin=piloto["fin"], hoy=date.today())

    por_categoria: dict[str, int] = {}
    for i in incidencias:
        por_categoria[i["categoria"]] = por_categoria.get(i["categoria"], 0) + 1

    return render(
        peticion,
        "piloto_incidencias.html",
        {
            "piloto": piloto,
            "ventana": ventana,
            "incidencias": incidencias,
            "categorias": CATEGORIAS,
            "severidades": SEVERIDADES,
            "por_categoria": por_categoria,
            "hoy": date.today().isoformat(),
            "bloqueos": sum(1 for i in incidencias if i["severidad"] == "bloqueo"),
            "abiertas": sum(1 for i in incidencias if i["resuelta_en"] is None),
            "minutos": sum(i["minutos_perdidos"] for i in incidencias),
            "costaron_venta": sum(1 for i in incidencias if i["costo_una_venta"]),
            "aviso": aviso,
            "error": error,
        },
        actor=actor,
        seccion="Desempeño",
    )


@router.post("/incidencia")
async def registrar_incidencia(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    csrf: str = Form(""),
    dia: str = Form(""),
    hora: str = Form(""),
    categoria: str = Form(""),
    severidad: str = Form(""),
    que_hacia: str = Form(""),
    que_paso: str = Form(""),
    costo_una_venta: str = Form(""),
    hubo_que_usar_papel: str = Form(""),
    minutos: str = Form(""),
) -> RedirectResponse:
    """Registra una incidencia. El texto es obligatorio; la hora, no.

    Exigir la hora haría que a las seis de la tarde alguien la inventara, y una
    hora inventada manda a buscar en el log de la Fase 9 a la hora equivocada —
    peor que no tenerla.
    """
    exigir_csrf(peticion, csrf)
    actor.exigir(PERMISO)
    piloto = await _activo(sesion)
    if piloto is None:
        return _redirigir("/panel/piloto", error="No hay un piloto activo.")

    if categoria not in CATEGORIAS or severidad not in SEVERIDADES:
        return _redirigir(
            "/panel/piloto/incidencias", error="Falta la categoría o la severidad."
        )
    hacia = (que_hacia or "").strip()
    paso = (que_paso or "").strip()
    if not hacia or not paso:
        return _redirigir(
            "/panel/piloto/incidencias",
            error="Hace falta qué estaba haciendo y qué pasó: sin eso, la "
                  "incidencia no se puede reproducir ni arreglar.",
        )

    try:
        perdidos = leer_entero(minutos, campo="Los minutos perdidos", maximo=600)
    except CapturaInvalida as e:
        return _redirigir("/panel/piloto/incidencias", error=str(e))

    momento: time | None = None
    if (hora or "").strip():
        try:
            momento = time.fromisoformat(hora.strip())
        except ValueError:
            return _redirigir(
                "/panel/piloto/incidencias",
                error=f"«{hora}» no es una hora. Déjala en blanco si no se sabe.",
            )

    await sesion.execute(
        text(
            """
            INSERT INTO piloto_incidencias
                (piloto_id, fecha_operativa, hora, categoria, severidad,
                 que_hacia, que_paso, costo_una_venta, hubo_que_usar_papel,
                 minutos_perdidos, registrado_por)
            VALUES (:piloto, :fecha, :hora, :categoria, :severidad,
                    :hacia, :paso, :costo, :papel, :minutos, :quien)
            """
        ),
        {
            "piloto": piloto["id"],
            "fecha": _fecha(dia, date.today()),
            "hora": momento,
            "categoria": categoria,
            "severidad": severidad,
            "hacia": hacia[:1000],
            "paso": paso[:2000],
            "costo": bool(costo_una_venta),
            "papel": bool(hubo_que_usar_papel),
            "minutos": perdidos,
            "quien": actor.usuario_id,
        },
    )
    await sesion.commit()
    return _redirigir("/panel/piloto/incidencias", aviso="Incidencia registrada.")


@router.post("/incidencia/{incidencia_id}/resolver")
async def resolver(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    incidencia_id: uuid.UUID,
    csrf: str = Form(""),
    resolucion: str = Form(""),
) -> RedirectResponse:
    """Cierra una incidencia con cómo se resolvió.

    Sin el cómo no se cierra, y no es burocracia: la mitad del valor del piloto
    está en la lista de "esto pasó y así se arregló", que es lo que se lee antes
    de poner la segunda ruta.
    """
    exigir_csrf(peticion, csrf)
    actor.exigir(PERMISO)
    como = (resolucion or "").strip()
    if not como:
        return _redirigir(
            "/panel/piloto/incidencias",
            error="Escribe cómo se resolvió: es lo que se va a leer antes de "
                  "poner la segunda ruta.",
        )
    resultado = await sesion.execute(
        text(
            """
            UPDATE piloto_incidencias
               SET resuelta_en = now(), resolucion = :como
             WHERE id = :id AND resuelta_en IS NULL
            """
        ),
        {"id": incidencia_id, "como": como[:2000]},
    )
    await sesion.commit()
    if not resultado.rowcount:
        return _redirigir(
            "/panel/piloto/incidencias",
            error="Esa incidencia ya estaba resuelta o no existe.",
        )
    return _redirigir("/panel/piloto/incidencias", aviso="Incidencia resuelta.")
