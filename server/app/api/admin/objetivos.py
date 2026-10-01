"""Objetivos mensuales por ruta: contra qué se mide el tablero.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTA PANTALLA EXISTE ANTES QUE LA TARJETA QUE LA USA
────────────────────────────────────────────────────────────────────────────
La Fase 6 dejó una lección: se construyó la captura de motivos de merma y
no-drop, y nada publicaba los catálogos al dispositivo. La pantalla estaba
perfecta y no servía para nada, porque el dato no llegaba.

La tarjeta "avance vs objetivo" del tablero tiene exactamente el mismo riesgo:
sin un lugar donde fijar el objetivo, la tarjeta se queda en blanco para
siempre y nadie sabe si es un error o si falta capturar algo. Así que el
objetivo se captura aquí, y el tablero dice explícitamente "sin objetivo" en
las rutas que no lo tienen en vez de mostrar una barra vacía.

────────────────────────────────────────────────────────────────────────────
POR QUÉ MENSUAL Y NO DIARIO
────────────────────────────────────────────────────────────────────────────
Un objetivo diario obligaría a mantener un calendario de días hábiles por ruta
—y a decidir qué pasa con un puente, o con el día que el camión estuvo en el
taller—. Nadie mantiene eso, y un objetivo que nadie mantiene es peor que
ninguno: el tablero pintaría rojo todos los domingos y en dos semanas la gente
dejaría de mirar el color.

El objetivo es del MES, y el tablero prorratea por días naturales
transcurridos, diciendo que lo hace (ver `domain/tablero.Avance`).

────────────────────────────────────────────────────────────────────────────
POR QUÉ SE COPIA EL MES ANTERIOR CON UN BOTÓN
────────────────────────────────────────────────────────────────────────────
Porque si fijar los objetivos de ocho rutas cuesta ocho capturas cada mes, el
mes que haya prisa no se van a fijar, y el tablero va a mentir todo ese mes
mostrando "sin objetivo" en rutas que sí tienen una meta en la cabeza de
alguien. Copiar y ajustar es el flujo real.
"""

from __future__ import annotations

import calendar
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated

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
from app.domain.tablero import inicio_de_mes

router = APIRouter(prefix="/panel/objetivos", tags=["panel"], include_in_schema=False)

PERMISO = "objetivos.administrar"

# Lo máximo que se acepta como objetivo mensual de una ruta. No es una regla de
# negocio: es un filtro de error de dedo. Un objetivo de $50,000,000 teclea un
# cero de más y deja la barra de avance en 0.1% todo el mes.
OBJETIVO_MAXIMO = Decimal("50000000")

MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def _periodo(texto: str) -> date:
    """Lee el mes del formulario (`YYYY-MM`). Lo inválido cae en el mes actual.

    Un mes mal escrito no es un error que valga la pena mostrar: lo que la
    persona quiere ver es un mes, y el mes en curso es la respuesta útil.
    """
    hoy = inicio_de_mes(date.today())
    if not texto:
        return hoy
    try:
        anio, mes = texto.split("-")[:2]
        return date(int(anio), int(mes), 1)
    except (ValueError, TypeError):
        return hoy


def _nombre_periodo(periodo: date) -> str:
    return f"{MESES[periodo.month - 1]} de {periodo.year}"


def _mes_anterior(periodo: date) -> date:
    return inicio_de_mes(periodo.replace(day=1) - timedelta(days=1))


@router.get("", response_class=HTMLResponse)
async def listar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    mes: str = "",
    aviso: str = "",
    error: str = "",
) -> HTMLResponse:
    """Las rutas activas con su objetivo del mes y lo que llevan vendido.

    El avance se muestra aquí mismo, y no es un adorno: fijar un objetivo sin
    ver qué pasó el mes pasado es adivinar. La cifra sale de `tablero_mes_ruta`,
    el mismo modelo de lectura que alimenta el tablero del gerente, así que las
    dos pantallas no pueden discrepar.
    """
    actor.exigir("tablero.ver")
    periodo = _periodo(mes)
    anterior = _mes_anterior(periodo)

    filas = (
        await sesion.execute(
            text(
                """
                SELECT r.id AS ruta_id, r.codigo, r.nombre,
                       o.objetivo_venta, o.objetivo_visitas, o.nota,
                       o.actualizado_en,
                       u.nombre AS fijado_por,
                       COALESCE(m.venta_mes, 0)   AS venta_mes,
                       COALESCE(m.visitas_mes, 0) AS visitas_mes,
                       COALESCE(p.venta_mes, 0)   AS venta_anterior,
                       ant.objetivo_venta         AS objetivo_anterior
                  FROM rutas r
                  LEFT JOIN objetivos_ruta   o   ON o.ruta_id = r.id AND o.periodo = :periodo
                  LEFT JOIN usuarios         u   ON u.id = o.fijado_por
                  LEFT JOIN tablero_mes_ruta m   ON m.ruta_id = r.id AND m.periodo = :periodo
                  LEFT JOIN tablero_mes_ruta p   ON p.ruta_id = r.id AND p.periodo = :anterior
                  LEFT JOIN objetivos_ruta   ant ON ant.ruta_id = r.id
                                                AND ant.periodo = :anterior
                 WHERE r.activo
                 ORDER BY r.codigo
                """
            ),
            {"periodo": periodo, "anterior": anterior},
        )
    ).mappings().all()

    dias_del_mes = calendar.monthrange(periodo.year, periodo.month)[1]
    hoy = date.today()
    # El prorrateo solo tiene sentido en el mes en curso. En un mes terminado el
    # esperado es 100%, y en uno futuro es 0: mostrar "esperado 45%" de un mes
    # que ya pasó invitaría a felicitar a una ruta que cerró en 60%.
    if inicio_de_mes(hoy) == periodo:
        dia_del_mes = hoy.day
    elif periodo < inicio_de_mes(hoy):
        dia_del_mes = dias_del_mes
    else:
        dia_del_mes = 0

    return render(
        peticion,
        "objetivos.html",
        {
            "filas": filas,
            "periodo": periodo.strftime("%Y-%m"),
            "nombre_periodo": _nombre_periodo(periodo),
            "nombre_anterior": _nombre_periodo(anterior),
            "dia_del_mes": dia_del_mes,
            "dias_del_mes": dias_del_mes,
            "esperado": (
                (Decimal(dia_del_mes) * 100 / Decimal(dias_del_mes)).quantize(Decimal("0.1"))
            ),
            "puede_editar": actor.puede(PERMISO),
            "total_objetivo": sum(
                Decimal(f["objetivo_venta"] or 0) for f in filas
            ),
            "total_venta": sum(Decimal(f["venta_mes"]) for f in filas),
            "sin_objetivo": sum(1 for f in filas if f["objetivo_venta"] is None),
            "aviso": aviso,
            "error": error,
        },
        actor=actor,
        seccion="Objetivos",
    )


@router.post("/fijar")
async def fijar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    ruta_id: Annotated[uuid.UUID, Form()],
    csrf: str = Form(""),
    mes: str = Form(""),
    objetivo_venta: str = Form(""),
    objetivo_visitas: str = Form(""),
    nota: str = Form(""),
) -> RedirectResponse:
    """Fija o cambia el objetivo de una ruta.

    Un objetivo en blanco BORRA el renglón en vez de guardar cero. Son dos cosas
    distintas y el tablero las muestra distinto: "sin objetivo" (nadie le puso
    meta) frente a "objetivo $0" (que daría 100% de avance con la primera venta).
    """
    exigir_csrf(peticion, csrf)
    actor.exigir(PERMISO)
    periodo = _periodo(mes)

    try:
        monto = leer_dinero(objetivo_venta, campo="El objetivo de venta")
        visitas = leer_entero(objetivo_visitas, campo="El objetivo de visitas", maximo=100000)
    except CapturaInvalida as e:
        return RedirectResponse(
            f"/panel/objetivos?mes={periodo:%Y-%m}&error={e}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if monto > OBJETIVO_MAXIMO:
        return RedirectResponse(
            f"/panel/objetivos?mes={periodo:%Y-%m}"
            f"&error=Un objetivo de {monto:,.2f} parece un error de dedo.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if monto == 0:
        await sesion.execute(
            text("DELETE FROM objetivos_ruta WHERE ruta_id = :r AND periodo = :p"),
            {"r": ruta_id, "p": periodo},
        )
        await sesion.commit()
        return RedirectResponse(
            f"/panel/objetivos?mes={periodo:%Y-%m}&aviso=Objetivo quitado.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    await sesion.execute(
        text(
            """
            INSERT INTO objetivos_ruta
                (ruta_id, periodo, objetivo_venta, objetivo_visitas, nota, fijado_por)
            VALUES (:r, :p, :monto, :visitas, :nota, :quien)
            ON CONFLICT (ruta_id, periodo) DO UPDATE SET
                objetivo_venta  = excluded.objetivo_venta,
                objetivo_visitas = excluded.objetivo_visitas,
                nota            = excluded.nota,
                fijado_por      = excluded.fijado_por,
                actualizado_en  = now()
            """
        ),
        {
            "r": ruta_id,
            "p": periodo,
            "monto": monto,
            # Cero visitas es "no hay meta de visitas", no "la meta es cero".
            "visitas": visitas or None,
            "nota": texto_o_nulo(nota),
            "quien": actor.usuario_id,
        },
    )
    await sesion.commit()
    return RedirectResponse(
        f"/panel/objetivos?mes={periodo:%Y-%m}&aviso=Objetivo guardado.",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/copiar")
async def copiar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    csrf: str = Form(""),
    mes: str = Form(""),
) -> RedirectResponse:
    """Copia los objetivos del mes anterior a este, sin pisar los ya fijados.

    `ON CONFLICT DO NOTHING` y no `DO UPDATE`: quien ya ajustó una ruta a mano
    este mes no quiere que un clic en "copiar" le devuelva la cifra del mes
    pasado. El botón sirve para rellenar los huecos, no para reiniciar.
    """
    exigir_csrf(peticion, csrf)
    actor.exigir(PERMISO)
    periodo = _periodo(mes)
    anterior = _mes_anterior(periodo)

    copiados = (
        await sesion.execute(
            text(
                """
                INSERT INTO objetivos_ruta
                    (ruta_id, periodo, objetivo_venta, objetivo_visitas, nota, fijado_por)
                SELECT o.ruta_id, :periodo, o.objetivo_venta, o.objetivo_visitas,
                       o.nota, :quien
                  FROM objetivos_ruta o
                  JOIN rutas r ON r.id = o.ruta_id AND r.activo
                 WHERE o.periodo = :anterior
                ON CONFLICT (ruta_id, periodo) DO NOTHING
                RETURNING ruta_id
                """
            ),
            {"periodo": periodo, "anterior": anterior, "quien": actor.usuario_id},
        )
    ).scalars().all()
    await sesion.commit()

    aviso = (
        f"Se copiaron {len(copiados)} objetivo(s) de {_nombre_periodo(anterior)}."
        if copiados
        else f"No había objetivos en {_nombre_periodo(anterior)} que copiar."
    )
    return RedirectResponse(
        f"/panel/objetivos?mes={periodo:%Y-%m}&aviso={aviso}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
