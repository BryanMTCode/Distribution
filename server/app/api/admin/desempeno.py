"""Desempeño del día: cómo va la operación AHORA, persona por persona.

────────────────────────────────────────────────────────────────────────────
QUÉ CONTESTA, Y POR QUÉ NO LO CONTESTABA NINGUNA PANTALLA
────────────────────────────────────────────────────────────────────────────
El panel ya tenía las tres mitades del problema y ninguna entera:

· El **Tablero** cuenta lo que necesita atención —cuarentena, revisiones,
  rezagos—. Es global y no dice quién.
· **Efectividad** abre en siete días a propósito, porque un porcentaje sobre
  veinte visitas es ruido. No sirve para la mañana en curso.
· El tablero del **teléfono** del gerente tiene las seis cifras del día y un
  ranking, pero en una pantalla de seis pulgadas y sin la columna que convierte
  una cifra en una llamada telefónica.

Lo que falta es la junta de las once de la mañana: **qué lleva cada vendedor
hoy, contra qué, y a quién hay que llamar.**

────────────────────────────────────────────────────────────────────────────
LA COMPARACIÓN ES CONTRA EL MISMO DÍA DE LA SEMANA
────────────────────────────────────────────────────────────────────────────
Es la decisión que hace que la pantalla signifique algo, y es propia de un DSD:
la ruta visita a los mismos clientes cada martes. «Hoy vendiste menos que ayer»
mide qué clientes tocaban; «hoy vendiste menos que tus últimos cuatro martes»
mide cómo trabajaste. La definición y su aritmética están en `domain/tablero.py`
—`dias_de_referencia`, `Referencia`— junto a la regla que la hace honesta: los
días que no trabajó no entran al promedio.

Sin referencia no se dibuja flecha. Con un solo martes de historia no hay
promedio, hay una anécdota, y poner una flecha sobre eso es pedirle
explicaciones a alguien por un número inventado.

────────────────────────────────────────────────────────────────────────────
LA COLUMNA QUE VUELVE ACCIONABLE AL TABLERO
────────────────────────────────────────────────────────────────────────────
El último push de cada teléfono, en el renglón de su dueño. El tablero del
celular dice «2 equipos sin sincronizar» y con eso no se puede hacer nada: no se
sabe a quién llamar. Aquí el cero de un vendedor se lee **junto a la razón del
cero**, y son razones muy distintas: «no ha vendido» y «no ha sincronizado desde
las 9:40» piden dos llamadas diferentes.

Es §0.3 llevado hasta su consecuencia: si las cifras del día son un piso y no un
total, lo que importa no es solo cuánto falta, sino **de quién** falta.

────────────────────────────────────────────────────────────────────────────
TODO SALE DE `tablero_dia`
────────────────────────────────────────────────────────────────────────────
Ninguna consulta de aquí agrega sobre `ventas`. Es la misma regla que la del
endpoint del teléfono y por la misma razón (migración 0021): esta pantalla se
abre y se recarga toda la mañana, y si barriera las tablas de operación, tres
gerentes con ella abierta harían que la SINCRONIZACIÓN de los camiones fuera
lenta. El vendedor esperando en la calle por una pantalla de oficina es
exactamente al revés de lo que importa.

El precio es que la pantalla muestra lo que el worker ya recalculó, y por eso la
frescura va **arriba de la primera cifra** y no en una nota al pie.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.api.admin.comun import SesionDep, dinero, render
from app.api.admin.sesion_web import ActorWeb
from app.domain.tablero import (
    DIAS_DE_REFERENCIA,
    HORA_EN_QUE_EL_SILENCIO_YA_NO_SE_EXPLICA,
    SQL_DIA_POR_VENDEDOR,
    SQL_ESTADO_DEL_MUNDO,
    SQL_REFERENCIA_DEL_DIA,
    SQL_REFERENCIA_POR_VENDEDOR,
    SQL_RESUMEN_DIA,
    SQL_RUTAS_POR_VENDEDOR,
    SQL_SINCRONIA_POR_VENDEDOR,
    Frescura,
    Referencia,
    dias_de_referencia,
    drop_size,
    porcentaje,
)

router = APIRouter(prefix="/panel/desempeno", tags=["panel"], include_in_schema=False)

# El mismo permiso que el tablero del teléfono: es la misma información, leída en
# otra pantalla. Un permiso nuevo haría que el día que alguien revise quién puede
# ver la venta de todas las rutas tuviera que encontrar dos.
PERMISO = "tablero.ver"


# Los días de la semana, en español y sin depender del locale.
#
# `strftime('%A')` usa el locale del proceso, y en el contenedor es el C: la
# primera versión de esta pantalla decía «3 por debajo de sus tuesdays». Ninguna
# prueba lo vio porque ninguna buscaba la palabra; lo vio la primera captura de
# pantalla.
DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
DIAS_PLURAL = (
    "lunes", "martes", "miércoles", "jueves", "viernes", "sábados", "domingos",
)


def _fecha(crudo: str) -> date:
    """El día que se está mirando. Hoy por omisión, y nunca el futuro.

    Una fecha ilegible cae a hoy en vez de devolver un error: quien la teclea mal
    quiere ver un día, no una pantalla de disculpas. Y el futuro se recorta porque
    `tablero_dia` no tiene renglones de mañana y la pantalla se vería igual que
    una en la que el worker se cayó — dos cosas que no deben parecerse.
    """
    hoy = date.today()
    try:
        elegida = date.fromisoformat(crudo) if crudo else hoy
    except ValueError:
        return hoy
    return min(elegida, hoy)


@router.get("", response_class=HTMLResponse)
async def listar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    fecha: str = "",
) -> HTMLResponse:
    actor.exigir(PERMISO)

    dia = _fecha(fecha)
    es_hoy = dia == date.today()
    referencias = dias_de_referencia(dia)

    resumen = (
        await sesion.execute(text(SQL_RESUMEN_DIA), {"fecha": dia})
    ).mappings().one()
    crudos = (
        await sesion.execute(text(SQL_DIA_POR_VENDEDOR), {"fecha": dia})
    ).mappings().all()
    ref_dia = (
        await sesion.execute(text(SQL_REFERENCIA_DEL_DIA), {"dias": referencias})
    ).mappings().one()
    ref_vendedor = {
        f["vendedor_id"]: f
        for f in (
            await sesion.execute(
                text(SQL_REFERENCIA_POR_VENDEDOR), {"dias": referencias}
            )
        ).mappings().all()
    }
    sincronia = {
        f["usuario_id"]: f
        for f in (
            await sesion.execute(text(SQL_SINCRONIA_POR_VENDEDOR))
        ).mappings().all()
    }
    rutas = {
        f["usuario_id"]: f["rutas"]
        for f in (
            await sesion.execute(text(SQL_RUTAS_POR_VENDEDOR))
        ).mappings().all()
    }
    mundo = (await sesion.execute(text(SQL_ESTADO_DEL_MUNDO))).mappings().one()

    # ------------------------------------------------------------------
    # La frescura, que va antes que la primera cifra.
    # ------------------------------------------------------------------
    # `calculado_en` sale del MÍNIMO de los renglones del día y no de
    # `tablero_refrescos`: lo que importa es la antigüedad de lo que se está
    # mirando. El refresco global pudo correr hace un minuto y no haber tocado
    # este día, y entonces «hace 1 min» sería verdad del worker y mentira de la
    # pantalla.
    calculado_en = resumen["calculado_en"]
    minutos = (
        int((datetime.now(UTC) - calculado_en).total_seconds() // 60)
        if calculado_en is not None
        else None
    )
    frescura = (
        Frescura(
            minutos=minutos,
            equipos_sin_sincronizar=mundo["equipos_sin_sincronizar"],
            cola_reportada=mundo["cola_reportada"],
            ops_en_cuarentena=mundo["ops_en_cuarentena"],
        )
        if minutos is not None and es_hoy
        else None
    )

    # ------------------------------------------------------------------
    # Los renglones.
    # ------------------------------------------------------------------
    renglones = []
    for d in crudos:
        venta = Decimal(d["venta_total"])
        ref = ref_vendedor.get(d["vendedor_id"])
        referencia = Referencia(
            promedio=Decimal(ref["venta_promedio"]) if ref else Decimal("0"),
            dias=int(ref["dias"]) if ref else 0,
        )
        equipo = sincronia.get(d["vendedor_id"])
        ultimo_push = equipo["ultimo_push"] if equipo else None
        # Un vendedor cuyo día entero está en su teléfono no es «un vendedor que
        # no vendió». Es la distinción que esta pantalla existe para hacer.
        sin_sincronizar = es_hoy and (
            ultimo_push is None or ultimo_push.date() < dia
        )
        sin_actividad = (
            not d["venta_total"] and not d["visitas"] and not d["cobrado_total"]
        )

        renglones.append(
            {
                "vendedor_id": d["vendedor_id"],
                "codigo": d["codigo"],
                "nombre": d["nombre"],
                "rutas": rutas.get(d["vendedor_id"]) or "—",
                "venta": dinero(venta),
                "venta_cruda": venta,
                "documentos": d["documentos_venta"],
                "visitas": d["visitas"],
                "visitas_con_venta": d["visitas_con_venta"],
                "efectividad": porcentaje(d["visitas_con_venta"], d["visitas"]),
                "drop_size": drop_size(venta, d["visitas_con_venta"]),
                "no_drops": d["no_drops"],
                "no_drops_nuestros": d["no_drops_nuestros"],
                "cobrado": dinero(d["cobrado_total"]),
                "efectivo": dinero(d["cobrado_efectivo"]),
                "mermas_documentos": d["mermas_documentos"],
                "promedio": dinero(referencia.promedio) if referencia.suficiente else None,
                "dias_de_referencia": referencia.dias,
                "variacion": referencia.variacion(venta),
                "lectura": referencia.lectura(venta, incompleta=sin_sincronizar),
                "ultimo_push": ultimo_push,
                "cola_reportada": int(equipo["cola_reportada"]) if equipo else 0,
                "equipos": int(equipo["equipos"]) if equipo else 0,
                "sin_sincronizar": sin_sincronizar,
                "sin_actividad": sin_actividad,
            }
        )

    # ------------------------------------------------------------------
    # Lo que hay que mover hoy.
    # ------------------------------------------------------------------
    # Tres listas, y cada una pide una acción distinta. Juntarlas en un «3
    # vendedores con problemas» obligaría a abrir la tabla para saber qué hacer,
    # que es justo el trabajo que esta sección ahorra.
    #
    # El silencio solo se señala a partir de cierta hora (ver la constante): a las
    # siete de la mañana todos los renglones están en cero y un aviso que sale
    # todos los días a esa hora enseña a ignorar los avisos.
    ya_es_hora = (
        not es_hoy or datetime.now().hour >= HORA_EN_QUE_EL_SILENCIO_YA_NO_SE_EXPLICA
    )
    sin_arrancar = [
        r for r in renglones if r["sin_actividad"] and not r["sin_sincronizar"]
    ] if ya_es_hora else []
    sin_sincronizar = [r for r in renglones if r["sin_sincronizar"]]
    # «Por debajo» excluye a los que ya están en otro aviso. El que no sincronizó
    # ya sale con lectura `incompleta` y nunca llega aquí; el que sincronizó y no
    # hizo nada está -100% abajo, que es verdad y no agrega nada: su aviso del
    # silencio ya lo dice con más fuerza. Repetirlo en dos listas obliga a quien
    # lee a reconciliarlas, que es el trabajo que esta sección ahorra.
    por_debajo = [
        r for r in renglones if r["lectura"] == "abajo" and not r["sin_actividad"]
    ]

    referencia_total = Referencia(
        promedio=Decimal(ref_dia["venta_promedio"]),
        dias=int(ref_dia["dias"]),
    )
    venta_total = Decimal(resumen["venta_total"])

    return render(
        peticion,
        "desempeno.html",
        {
            "dia": dia,
            "nombre_dia": DIAS[dia.weekday()],
            "nombre_dias": DIAS_PLURAL[dia.weekday()],
            "es_hoy": es_hoy,
            "ayer": dia - timedelta(days=1),
            "manana": dia + timedelta(days=1) if not es_hoy else None,
            "hoy": date.today(),
            "frescura": frescura,
            "sin_calcular": resumen["renglones"] == 0,
            "resumen": {
                **dict(resumen),
                "venta_total": dinero(resumen["venta_total"]),
                "venta_contado": dinero(resumen["venta_contado"]),
                "venta_credito": dinero(resumen["venta_credito"]),
                "cobrado_total": dinero(resumen["cobrado_total"]),
                "cobrado_efectivo": dinero(resumen["cobrado_efectivo"]),
                "efectividad": porcentaje(
                    resumen["visitas_con_venta"], resumen["visitas"]
                ),
                "drop_size": drop_size(venta_total, resumen["visitas_con_venta"]),
                # El efectivo que la operación tiene que recibir hoy: las ventas
                # de contado más los cobros en efectivo. Misma cuenta que el
                # arqueo de la liquidación — si no coincidieran, esta pantalla
                # prometería un número y la liquidación cobraría otro.
                "efectivo_a_entregar": dinero(
                    Decimal(resumen["venta_contado"])
                    + Decimal(resumen["cobrado_efectivo"])
                ),
            },
            "referencia": {
                "promedio": dinero(referencia_total.promedio),
                "dias": referencia_total.dias,
                "suficiente": referencia_total.suficiente,
                "variacion": referencia_total.variacion(venta_total),
                # El total de hoy es un piso si a alguien le falta el día. Leerlo
                # como «abajo» pintaría de ámbar una caída que puede no existir.
                "lectura": referencia_total.lectura(
                    venta_total, incompleta=bool(sin_sincronizar)
                ),
                "faltan": len(sin_sincronizar),
                "cuantos": DIAS_DE_REFERENCIA,
            },
            "renglones": renglones,
            "sin_arrancar": sin_arrancar,
            "sin_sincronizar_lista": sin_sincronizar,
            "por_debajo": por_debajo,
            "ya_es_hora": ya_es_hora,
        },
        actor=actor,
        seccion="Desempeño",
    )
