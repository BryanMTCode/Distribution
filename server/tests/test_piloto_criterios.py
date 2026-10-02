"""Los criterios del piloto, medidos como funciones puras.

Sin base de datos y sin HTTP: `medir()` recibe lo que ya se leyó y devuelve las
doce mediciones. Es lo que permite escribir los casos que cuesta provocar —"el
papel dice 23 y el sistema sigue en 21 mañana"— sin montar dos semanas de
operación. Las consultas y las pantallas se prueban en `test_piloto.py`.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
El piloto es el único paso del plan que puede invalidar una decisión de diseño,
y dura dos semanas que no se pueden repetir: si el instrumento mide mal, el
error no se descubre hasta que ya se compraron los teléfonos.

Las cinco distinciones que sostienen todo lo demás:

1. **Retraso no es pérdida.** "El papel dice 23, el sistema 21" significa dos
   cosas opuestas según si el sistema sigue en 21 mañana. Con una sola cifra se
   ven idénticas.
2. **Sin medir no es cumplir.** Un criterio sin datos tiene que verse distinto
   de uno en verde, o un piloto que no empezó aprueba todo.
3. **Los valores absolutos no se cancelan.** +$300 un día y −$300 el siguiente
   no son "cuadra perfecto": son dos días descuadrados.
4. **La ventana de cuadre va hasta AYER.** El papel de hoy se captura mañana, y
   medir la cobertura incluyendo hoy haría que un piloto perfecto se viera al 90%
   todos los días.
5. **Los domingos no son jornadas que falten.** Un día sin operación no entra en
   ningún denominador.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.domain.piloto import (
    Medicion,
    Ventana,
    fin_por_omision,
    inicio_por_omision,
    medir,
)

HOY = date(2026, 10, 15)          # un jueves
INICIO = date(2026, 10, 5)        # el lunes anterior
FIN = date(2026, 10, 18)          # el domingo de la segunda semana


# ===========================================================================
# Andamios
# ===========================================================================
def ventana(hoy: date = HOY, inicio: date = INICIO, fin: date = FIN) -> Ventana:
    return Ventana(inicio=inicio, fin=fin, hoy=hoy)


def dia(
    fecha: date,
    *,
    documentos: int = 0,
    importe: str = "0",
    cobranza: str = "0",
    canceladas: int = 0,
    retraso: str = "0",
    no_drops: int = 0,
    liquidada: bool = True,
) -> dict:
    return {
        "fecha": fecha,
        "documentos": documentos,
        "canceladas": canceladas,
        "importe": Decimal(importe),
        "retraso_horas": Decimal(retraso),
        "cobranza": Decimal(cobranza),
        "no_drops": no_drops,
        "liquidada": liquidada,
    }


def jornada(
    fecha: date,
    *,
    documentos_papel: int,
    importe_papel: str = "0",
    cobranza_papel: str = "0",
    documentos_sistema: int | None = None,
) -> dict:
    return {
        "fecha": fecha,
        "documentos_papel": documentos_papel,
        "importe_papel": Decimal(importe_papel),
        "cobranza_papel": Decimal(cobranza_papel),
        "documentos_sistema": (
            documentos_papel if documentos_sistema is None else documentos_sistema
        ),
        "importe_sistema": Decimal(importe_papel),
        "cobranza_sistema": Decimal(cobranza_papel),
    }


def incidencia(
    fecha: date,
    *,
    severidad: str = "estorbo",
    minutos: int = 0,
    categoria: str = "app",
    resuelta: bool = False,
) -> dict:
    return {
        "fecha_operativa": fecha,
        "severidad": severidad,
        "categoria": categoria,
        "minutos_perdidos": minutos,
        "resuelta_en": datetime.now(UTC) if resuelta else None,
        "costo_una_venta": False,
    }


CRITERIOS_FALSOS = [
    {
        "codigo": codigo,
        "nombre": codigo,
        "pregunta": "¿?",
        "umbral": Decimal(umbral),
        "unidad": unidad,
        "comparacion": comparacion,
        "bloqueante": True,
        "evaluable": True,
        "nota": None,
        "orden": i,
    }
    for i, (codigo, umbral, unidad, comparacion) in enumerate(
        [
            ("cobertura_papel", "100", "porcentaje", "minimo"),
            ("faltantes_definitivos", "0", "cuenta", "maximo"),
            ("importe_cuadra", "0.5", "porcentaje", "maximo"),
            ("cobranza_cuadra", "0.5", "porcentaje", "maximo"),
            ("bloqueos_segunda_semana", "0", "cuenta", "maximo"),
            ("minutos_por_jornada", "15", "minutos", "maximo"),
            ("retraso_entrega", "24", "horas", "maximo"),
            ("cuarentena", "0", "cuenta", "maximo"),
            ("liquidaciones_cerradas", "100", "porcentaje", "minimo"),
            ("cancelaciones", "5", "porcentaje", "maximo"),
            ("faltantes_al_capturar", "10", "porcentaje", "maximo"),
        ]
    )
]


def evaluar(
    *,
    dias=(),
    jornadas=(),
    incidencias=(),
    cuarentena: int = 0,
    v: Ventana | None = None,
    criterios=None,
):
    return medir(
        criterios=criterios if criterios is not None else CRITERIOS_FALSOS,
        dias=list(dias),
        jornadas=list(jornadas),
        incidencias=list(incidencias),
        cuarentena=cuarentena,
        ventana=v or ventana(),
    )


def de(veredicto, codigo: str) -> Medicion:
    return next(m for m in veredicto.mediciones if m.codigo == codigo)


# ===========================================================================
# La ventana: hasta qué día se mide cada cosa
# ===========================================================================
def test_la_ventana_de_cuadre_llega_hasta_ayer():
    """El papel de hoy se captura mañana, con la jornada cerrada.

    Si la cobertura midiera hasta hoy, un piloto impecable se vería incompleto
    todos los días, y una alarma que suena siempre es una alarma apagada.
    """
    v = ventana()
    assert v.hasta_operacion == HOY
    assert v.hasta_cuadre == HOY - timedelta(days=1)


def test_el_primer_dia_no_hay_nada_que_cuadrar():
    """La ventana de cuadre queda ANTES del inicio, y eso es correcto."""
    v = ventana(hoy=INICIO)
    assert v.hasta_cuadre < v.inicio
    assert v.dia_del_piloto == 1


def test_antes_de_empezar_el_piloto_va_en_el_dia_cero():
    v = ventana(hoy=INICIO - timedelta(days=3))
    assert v.dia_del_piloto == 0
    assert not v.segunda_semana_empezo
    assert not v.termino


def test_el_dia_del_piloto_no_pasa_del_plan():
    """Un piloto que se dejó correr tres días más sigue siendo de 14 días.

    Sin el tope, la pantalla diría "día 17 de 14", que es la clase de cifra que
    hace que alguien deje de creerle al resto.
    """
    v = ventana(hoy=FIN + timedelta(days=3))
    assert v.dia_del_piloto == v.dias_plan == 14
    assert v.termino


def test_la_segunda_semana_empieza_el_dia_ocho():
    assert ventana().inicio_segunda_semana == INICIO + timedelta(days=7)
    assert not ventana(hoy=INICIO + timedelta(days=6)).segunda_semana_empezo
    assert ventana(hoy=INICIO + timedelta(days=7)).segunda_semana_empezo


def test_el_inicio_por_omision_es_el_lunes_siguiente():
    """Nunca hoy, y nunca un jueves.

    La primera semana es la que trae los bloqueos, y partirla con un fin de
    semana en medio le da al vendedor cuatro días de uso con tres de olvido.
    """
    for hoy in (date(2026, 10, 1), date(2026, 10, 5), date(2026, 10, 11)):
        arranca = inicio_por_omision(hoy)
        assert arranca.weekday() == 0, arranca
        assert arranca > hoy
    assert fin_por_omision(date(2026, 10, 5)) == date(2026, 10, 18)


# ===========================================================================
# 1. Retraso no es pérdida: la distinción que justifica el módulo
# ===========================================================================
def test_lo_que_faltaba_al_capturar_y_llego_despues_no_es_una_venta_perdida():
    """Papel 23, congelado 21, hoy 23: el teléfono sincronizó a mediodía.

    Es §0.3 funcionando como debe. `faltantes_definitivos` tiene que quedar en
    cero y solo `faltantes_al_capturar` acusar el retraso.
    """
    v = evaluar(
        dias=[dia(INICIO, documentos=23, importe="5000")],
        jornadas=[
            jornada(INICIO, documentos_papel=23, importe_papel="5000",
                    documentos_sistema=21)
        ],
    )
    assert de(v, "faltantes_definitivos").valor == 0
    assert de(v, "faltantes_definitivos").cumple is True
    retraso = de(v, "faltantes_al_capturar")
    assert retraso.valor == Decimal("8.7")      # 2 de 23
    assert "2 de 23" in retraso.detalle
    assert "2 llegaron después" in retraso.detalle


def test_lo_que_sigue_faltando_hoy_si_es_una_venta_perdida():
    """Papel 23, congelado 21, hoy 21: dos ventas que no existen.

    Mismo dato de entrada que la prueba anterior salvo el "hoy", y el veredicto
    es el opuesto. Es la razón de guardar las dos cifras.
    """
    v = evaluar(
        dias=[dia(INICIO, documentos=21, importe="4500")],
        jornadas=[
            jornada(INICIO, documentos_papel=23, importe_papel="5000",
                    documentos_sistema=21)
        ],
    )
    faltan = de(v, "faltantes_definitivos")
    assert faltan.valor == 2
    assert faltan.cumple is False
    assert "05/10" in faltan.detalle


def test_la_lista_de_dias_dice_cuantos_quedaron_fuera():
    """Cortarla en cinco y callarlo invita a revisar dos días cuando fueron ocho."""
    dias, jornadas = [], []
    for i in range(8):
        f = INICIO + timedelta(days=i)
        dias.append(dia(f, documentos=9, importe="900"))
        jornadas.append(jornada(f, documentos_papel=10, importe_papel="1000"))
    faltan = de(evaluar(dias=dias, jornadas=jornadas), "faltantes_definitivos")
    assert faltan.valor == 8
    assert "y 3 más" in faltan.detalle


def test_un_sobrante_no_tapa_un_faltante_de_otro_dia():
    """El lunes sobra una y el martes falta una: el faltante sigue siendo uno.

    Con una resta simple los dos días se cancelarían y el criterio diría cero,
    escondiendo exactamente lo que vino a buscar.
    """
    v = evaluar(
        dias=[
            dia(INICIO, documentos=11, importe="1000"),
            dia(INICIO + timedelta(days=1), documentos=9, importe="1000"),
        ],
        jornadas=[
            jornada(INICIO, documentos_papel=10, importe_papel="1000"),
            jornada(INICIO + timedelta(days=1), documentos_papel=10,
                    importe_papel="1000"),
        ],
    )
    assert de(v, "faltantes_definitivos").valor == 1


# ===========================================================================
# 2. Sin medir no es cumplir
# ===========================================================================
def test_un_criterio_sin_datos_no_se_pinta_de_verde():
    """Es la mentira más cómoda que podría tener la pantalla.

    Se mide con el piloto SIN EMPEZAR, que es el único estado en que ninguna de
    las doce cifras puede existir. Con el piloto en curso, los criterios que el
    sistema mide de sí mismo —la cuarentena— sí pueden leer cero honestamente:
    esa tabla se llena sola. Los que dependen de que una persona escriba algo,
    no; de ahí la prueba que sigue.
    """
    v = evaluar(v=ventana(hoy=INICIO - timedelta(days=1)))
    for m in v.mediciones:
        assert m.valor is None, m.codigo
        assert m.cumple is None, m.codigo
        assert m.semaforo == "sin_dato", m.codigo
        assert m.valor_texto == "sin medir"


def test_la_bitacora_vacia_no_aprueba_los_criterios_que_la_leen():
    """Dos semanas de una app nueva sin UNA incidencia es un registro sin llevar.

    Con jornadas trabajadas y la segunda semana empezada, los dos criterios que
    leen la bitácora tendrían datos para contar cero. Y cero ahí no mide la app:
    mide que nadie preguntó.
    """
    dias = [dia(INICIO + timedelta(days=i), documentos=10, importe="1000")
            for i in range(11)]
    v = evaluar(dias=dias, incidencias=[])
    for codigo in ("bloqueos_segunda_semana", "minutos_por_jornada"):
        m = de(v, codigo)
        assert m.valor is None, codigo
        assert m.semaforo == "sin_dato", codigo
        assert "nadie preguntó" in m.detalle


def test_una_sola_incidencia_vuelve_legible_el_conteo():
    """No hace falta que haya problemas: hace falta que alguien esté preguntando."""
    dias = [dia(INICIO + timedelta(days=i), documentos=10, importe="1000")
            for i in range(11)]
    v = evaluar(
        dias=dias,
        incidencias=[incidencia(INICIO, severidad="molestia", minutos=0)],
    )
    assert de(v, "bloqueos_segunda_semana").valor == 0
    assert de(v, "bloqueos_segunda_semana").cumple is True
    assert de(v, "minutos_por_jornada").valor == Decimal("0.0")


def test_un_piloto_en_curso_nunca_pasa_aunque_todo_vaya_bien():
    """Un piloto de dos semanas aprobado el día tres no midió dos semanas."""
    v = evaluar(
        dias=[dia(INICIO + timedelta(days=i), documentos=10, importe="1000")
              for i in range(11)],
        jornadas=[jornada(INICIO + timedelta(days=i), documentos_papel=10,
                          importe_papel="1000") for i in range(10)],
    )
    assert not v.incumplidos
    assert v.estado == "en_curso"
    assert v.pasa is False


def test_terminado_con_criterios_sin_medir_tampoco_pasa():
    """Un piloto con huecos en la medición no aprueba por omisión."""
    v = evaluar(v=ventana(hoy=FIN + timedelta(days=1)))
    assert v.ventana.termino
    assert v.sin_medir
    assert v.estado == "sin_datos"
    assert v.pasa is False
    assert v.sugerencia == ""


def test_terminado_y_todo_en_verde_pasa_y_sugiere_adelante():
    dias = [dia(INICIO + timedelta(days=i), documentos=10, importe="1000",
                cobranza="200") for i in range(14)]
    jornadas = [jornada(INICIO + timedelta(days=i), documentos_papel=10,
                        importe_papel="1000", cobranza_papel="200")
                for i in range(14)]
    # Una incidencia: sin NINGUNA, los dos criterios que leen la bitácora
    # quedan sin medir a propósito (ver `VACIA` en domain/piloto.py).
    v = evaluar(dias=dias, jornadas=jornadas,
                incidencias=[incidencia(INICIO, severidad="molestia")],
                v=ventana(hoy=FIN + timedelta(days=1)))
    assert v.estado == "limpio", [
        (m.codigo, m.valor, m.detalle) for m in v.mediciones if m.cumple is not True
    ]
    assert v.pasa is True
    assert v.sugerencia == "adelante"


def test_un_criterio_no_evaluable_no_detiene_ni_aprueba_nada():
    """La impresora: no se prueba en este piloto y se dice en voz alta.

    Si un criterio sin medición posible contara como incumplido, el piloto no
    podría pasar nunca; si contara como cumplido, se volvería un supuesto. Ni
    una ni otra: sale aparte.
    """
    criterios = [
        *CRITERIOS_FALSOS,
        {
            "codigo": "impresion_bluetooth",
            "nombre": "El ticket impreso",
            "pregunta": "¿?",
            "umbral": Decimal(0),
            "unidad": "cuenta",
            "comparacion": "maximo",
            "bloqueante": False,
            "evaluable": False,
            "nota": "Este piloto no lo prueba: falta la EC-MP200.",
            "orden": 99,
        },
    ]
    dias = [dia(INICIO + timedelta(days=i), documentos=10, importe="1000",
                cobranza="200") for i in range(14)]
    jornadas = [jornada(INICIO + timedelta(days=i), documentos_papel=10,
                        importe_papel="1000", cobranza_papel="200")
                for i in range(14)]
    v = evaluar(dias=dias, jornadas=jornadas, criterios=criterios,
                incidencias=[incidencia(INICIO, severidad="molestia")],
                v=ventana(hoy=FIN + timedelta(days=1)))
    impresora = de(v, "impresion_bluetooth")
    assert impresora.semaforo == "no_evaluable"
    assert impresora.cumple is None
    assert impresora.valor_texto == "no se evalúa"
    assert "EC-MP200" in impresora.detalle
    assert v.no_evaluables == (impresora,)
    assert v.pasa is True


def test_un_criterio_sembrado_sin_medicion_se_denuncia_como_error_de_codigo():
    """No se puede confundir con "todavía no hay datos".

    Es el riesgo real de tener los umbrales en una tabla: alguien agrega un
    renglón en una migración y la pantalla lo muestra "sin medir" para siempre.
    """
    v = evaluar(criterios=[{
        "codigo": "inventado",
        "nombre": "Inventado",
        "pregunta": "¿?",
        "umbral": Decimal(1),
        "unidad": "cuenta",
        "comparacion": "maximo",
        "bloqueante": True,
        "evaluable": True,
        "nota": None,
        "orden": 1,
    }])
    assert de(v, "inventado").valor is None
    assert "error del código" in de(v, "inventado").detalle


# ===========================================================================
# 3. Los valores absolutos no se cancelan
# ===========================================================================
def test_dos_dias_descuadrados_en_sentidos_opuestos_no_cuadran():
    """+$300 el lunes y −$300 el martes no son cero: son dos días descuadrados."""
    v = evaluar(
        dias=[
            dia(INICIO, documentos=10, importe="1300"),
            dia(INICIO + timedelta(days=1), documentos=10, importe="700"),
        ],
        jornadas=[
            jornada(INICIO, documentos_papel=10, importe_papel="1000"),
            jornada(INICIO + timedelta(days=1), documentos_papel=10,
                    importe_papel="1000"),
        ],
    )
    importe = de(v, "importe_cuadra")
    assert importe.valor == Decimal("30.0")   # 600 de desvío sobre 2000
    assert importe.cumple is False


def test_medio_punto_de_desvio_se_tolera():
    """Cubre el redondeo y algún error de dedo al capturar, no más."""
    v = evaluar(
        dias=[dia(INICIO, documentos=10, importe="10004")],
        jornadas=[jornada(INICIO, documentos_papel=10, importe_papel="10000")],
    )
    assert de(v, "importe_cuadra").valor == Decimal("0.0")
    assert de(v, "importe_cuadra").cumple is True


def test_la_cobranza_se_mide_aparte_del_importe():
    """Es otro problema: la cobranza es efectivo en la mano."""
    v = evaluar(
        dias=[dia(INICIO, documentos=10, importe="1000", cobranza="700")],
        jornadas=[jornada(INICIO, documentos_papel=10, importe_papel="1000",
                          cobranza_papel="500")],
    )
    assert de(v, "importe_cuadra").cumple is True
    assert de(v, "cobranza_cuadra").cumple is False
    assert de(v, "cobranza_cuadra").valor == Decimal("40.0")


def test_sin_cobranza_en_el_papel_el_criterio_queda_sin_medir():
    """Cero cobranza capturada no es "cuadró perfecto"."""
    v = evaluar(
        dias=[dia(INICIO, documentos=10, importe="1000")],
        jornadas=[jornada(INICIO, documentos_papel=10, importe_papel="1000")],
    )
    assert de(v, "cobranza_cuadra").valor is None


# ===========================================================================
# 4 y 5. Los denominadores
# ===========================================================================
def test_un_dia_sin_operacion_no_es_una_jornada_que_falte_capturar():
    """El domingo no entra en ningún denominador."""
    lunes, domingo = INICIO, INICIO + timedelta(days=6)
    v = evaluar(
        dias=[dia(lunes, documentos=10, importe="1000"), dia(domingo)],
        jornadas=[jornada(lunes, documentos_papel=10, importe_papel="1000")],
    )
    cobertura = de(v, "cobertura_papel")
    assert cobertura.valor == Decimal("100.0")
    assert "1 de 1 jornadas" in cobertura.detalle


def test_la_jornada_de_hoy_no_cuenta_como_sin_capturar():
    """Todavía no se puede capturar: el vendedor está en la calle."""
    v = evaluar(
        dias=[
            dia(HOY - timedelta(days=1), documentos=10, importe="1000"),
            dia(HOY, documentos=4, importe="400"),
        ],
        jornadas=[jornada(HOY - timedelta(days=1), documentos_papel=10,
                          importe_papel="1000")],
    )
    assert de(v, "cobertura_papel").valor == Decimal("100.0")


def test_un_dia_que_el_papel_trae_y_el_sistema_no_entra_en_la_cobertura():
    """La falla más grave posible: el papel tiene la jornada y el sistema nada.

    Sin sumar los días capturados al denominador, un día con cero ventas en el
    sistema no sería "jornada trabajada" y la cobertura saldría perfecta justo
    cuando se perdió un día completo.
    """
    perdido = INICIO + timedelta(days=1)
    v = evaluar(
        dias=[dia(INICIO, documentos=10, importe="1000"), dia(perdido)],
        jornadas=[
            jornada(INICIO, documentos_papel=10, importe_papel="1000"),
            jornada(perdido, documentos_papel=9, importe_papel="900"),
        ],
    )
    assert de(v, "cobertura_papel").valor == Decimal("100.0")
    assert de(v, "faltantes_definitivos").valor == 9


def test_la_cobertura_acusa_el_dia_que_nadie_capturo():
    v = evaluar(
        dias=[dia(INICIO + timedelta(days=i), documentos=10, importe="1000")
              for i in range(4)],
        jornadas=[jornada(INICIO + timedelta(days=i), documentos_papel=10,
                          importe_papel="1000") for i in range(3)],
    )
    cobertura = de(v, "cobertura_papel")
    assert cobertura.valor == Decimal("75.0")
    assert cobertura.cumple is False


def test_los_minutos_se_dividen_entre_jornadas_y_no_entre_incidencias():
    """Dividir entre incidencias daría un número que MEJORA al reportar más."""
    dias = [dia(INICIO + timedelta(days=i), documentos=10, importe="1000")
            for i in range(4)]
    v = evaluar(
        dias=dias,
        incidencias=[
            incidencia(INICIO, minutos=40),
            incidencia(INICIO + timedelta(days=1), minutos=40),
        ],
    )
    minutos = de(v, "minutos_por_jornada")
    assert minutos.valor == Decimal("20.0")   # 80 entre 4 jornadas
    assert minutos.cumple is False
    assert "entre 4 jornada(s)" in minutos.detalle


def test_la_liquidacion_se_mide_hasta_ayer_igual_que_el_papel():
    """La del día se cierra a la mañana siguiente, cuando regresa el camión."""
    v = evaluar(
        dias=[
            dia(HOY - timedelta(days=1), documentos=10, importe="1000",
                liquidada=True),
            dia(HOY, documentos=8, importe="800", liquidada=False),
        ],
    )
    liq = de(v, "liquidaciones_cerradas")
    assert liq.valor == Decimal("100.0")
    assert "1 de 1" in liq.detalle


def test_una_jornada_sin_liquidar_sale_con_su_fecha():
    sin_cerrar = INICIO + timedelta(days=1)
    v = evaluar(
        dias=[
            dia(INICIO, documentos=10, importe="1000", liquidada=True),
            dia(sin_cerrar, documentos=10, importe="1000", liquidada=False),
        ],
    )
    liq = de(v, "liquidaciones_cerradas")
    assert liq.valor == Decimal("50.0")
    assert "06/10" in liq.detalle


# ===========================================================================
# Los criterios que miden tendencia
# ===========================================================================
def test_los_bloqueos_de_la_primera_semana_no_cuentan_contra_el_piloto():
    """La primera semana VA a tener bloqueos: para eso es el piloto.

    Un umbral de cero sobre las dos semanas haría fracasar al piloto que
    funcionó. Lo que decide es si se dejaron de repetir.
    """
    v = evaluar(
        dias=[dia(INICIO + timedelta(days=i), documentos=10, importe="1000")
              for i in range(11)],
        incidencias=[
            incidencia(INICIO, severidad="bloqueo"),
            incidencia(INICIO + timedelta(days=2), severidad="bloqueo"),
            incidencia(INICIO + timedelta(days=3), severidad="bloqueo"),
        ],
    )
    bloqueos = de(v, "bloqueos_segunda_semana")
    assert bloqueos.valor == 0
    assert bloqueos.cumple is True
    assert "0 bloqueo(s) en la segunda semana, 3 en la primera" in bloqueos.detalle


def test_un_bloqueo_en_la_segunda_semana_si_detiene_el_despliegue():
    v = evaluar(
        incidencias=[incidencia(INICIO + timedelta(days=9), severidad="bloqueo")],
    )
    assert de(v, "bloqueos_segunda_semana").valor == 1
    assert de(v, "bloqueos_segunda_semana").cumple is False


def test_antes_del_dia_ocho_el_criterio_de_bloqueos_no_se_puede_medir():
    """Y no "cumple": la segunda semana todavía no existe."""
    v = evaluar(
        incidencias=[incidencia(INICIO, severidad="bloqueo")],
        v=ventana(hoy=INICIO + timedelta(days=3)),
    )
    bloqueos = de(v, "bloqueos_segunda_semana")
    assert bloqueos.valor is None
    assert bloqueos.semaforo == "sin_dato"
    assert "12/10" in bloqueos.detalle


def test_el_retraso_se_cuenta_desde_el_cierre_del_dia_operativo():
    """Cero significa que todo llegó dentro de su propio día."""
    v = evaluar(dias=[
        dia(INICIO, documentos=10, importe="1000", retraso="0"),
        dia(INICIO + timedelta(days=1), documentos=10, importe="1000", retraso="6.5"),
    ])
    retraso = de(v, "retraso_entrega")
    assert retraso.valor == Decimal("6.5")
    assert retraso.cumple is True
    assert "06/10" in retraso.detalle


def test_una_venta_que_llego_un_dia_tarde_no_cumple():
    v = evaluar(dias=[dia(INICIO, documentos=10, importe="1000", retraso="30")])
    assert de(v, "retraso_entrega").cumple is False


def test_sin_ventas_no_hay_retraso_que_medir():
    v = evaluar(dias=[dia(INICIO)])
    assert de(v, "retraso_entrega").valor is None


def test_la_cuarentena_se_mide_desde_el_primer_dia():
    assert de(evaluar(cuarentena=0), "cuarentena").valor == 0
    assert de(evaluar(cuarentena=0), "cuarentena").detalle == "Nada en cuarentena."
    sucio = de(evaluar(cuarentena=2), "cuarentena")
    assert sucio.valor == 2
    assert sucio.cumple is False


def test_antes_de_empezar_no_hay_cuarentena_que_medir():
    v = evaluar(cuarentena=0, v=ventana(hoy=INICIO - timedelta(days=1)))
    assert de(v, "cuarentena").valor is None


def test_las_cancelaciones_son_informativas_y_no_detienen_nada():
    v = evaluar(dias=[dia(INICIO, documentos=8, importe="800", canceladas=2)])
    cancel = de(v, "cancelaciones")
    assert cancel.valor == Decimal("20.0")
    assert cancel.cumple is False
    # El umbral está incumplido y el veredicto NO lo cuenta: es informativo.
    criterios = [dict(c, bloqueante=(c["codigo"] != "cancelaciones"))
                 for c in CRITERIOS_FALSOS]
    v2 = evaluar(dias=[dia(INICIO, documentos=8, importe="800", canceladas=2)],
                 criterios=criterios)
    assert all(m.codigo != "cancelaciones" for m in v2.incumplidos)


# ===========================================================================
# Cómo se lee una medición
# ===========================================================================
def test_el_texto_del_umbral_dice_si_es_maximo_o_minimo():
    v = evaluar(dias=[dia(INICIO, documentos=10, importe="1000")],
                jornadas=[jornada(INICIO, documentos_papel=10, importe_papel="1000")])
    assert de(v, "cobertura_papel").umbral_texto == "mínimo 100.0 %"
    assert de(v, "faltantes_definitivos").umbral_texto == "máximo 0"
    assert de(v, "retraso_entrega").umbral_texto == "máximo 24.0 h"
    assert de(v, "minutos_por_jornada").umbral_texto == "máximo 15.0 min"
