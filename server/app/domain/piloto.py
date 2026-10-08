"""Las mediciones del piloto de campo: el papel contra el sistema.

────────────────────────────────────────────────────────────────────────────
POR QUÉ EL PILOTO NECESITA CÓDIGO SI SON DOS SEMANAS DE CALENDARIO
────────────────────────────────────────────────────────────────────────────
Las dos semanas no se pueden acelerar. Lo que sí se puede es decidir, antes de
que empiecen, QUÉ se va a medir y CONTRA QUÉ se va a comparar — porque esas dos
decisiones, tomadas al final, las toma el resultado.

Este módulo tiene las consultas y la evaluación. Los umbrales NO están aquí:
están sembrados en `piloto_criterios` por la migración 0024, con fecha anterior
al primer día del piloto. La separación es deliberada:

    el umbral es una decisión de negocio   → dato, con fecha, auditable
    la medición es código                  → se prueba

────────────────────────────────────────────────────────────────────────────
DOS CIFRAS DEL SISTEMA POR CADA DÍA, Y NO ES REDUNDANCIA
────────────────────────────────────────────────────────────────────────────
Cada jornada guarda lo que el sistema decía AL CAPTURAR (congelado en
`piloto_jornadas`) y se compara además contra lo que dice HOY (calculado aquí).

    papel 23 · al capturar 21 · hoy 23   →  retraso de entrega. §0.3 funcionando.
    papel 23 · al capturar 21 · hoy 21   →  DOS VENTAS QUE NO EXISTEN.

Son hallazgos opuestos y con una sola cifra se ven idénticos. El primero no
detiene nada; el segundo detiene el despliegue.

────────────────────────────────────────────────────────────────────────────
HASTA QUÉ DÍA SE MIDE
────────────────────────────────────────────────────────────────────────────
Dos ventanas, y confundirlas hace que la pantalla grite todos los días:

    operación   inicio .. min(fin, hoy)       lo que ya ocurrió
    cuadre      inicio .. min(fin, ayer)      lo que ya DEBERÍA estar capturado

El papel de hoy se captura mañana en la mañana, con la hoja del vendedor en la
mano. Medir la cobertura de captura incluyendo hoy daría 90% todos los días de
un piloto perfecto, y una alarma que suena siempre es una alarma apagada.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

CENTAVO = Decimal("0.01")
DECIMA = Decimal("0.1")

# Cuándo empieza la "segunda semana". El día 8 del piloto, contando el inicio
# como día 1. Es el corte que convierte la bitácora en una tendencia: lo que
# importa no es si hubo bloqueos —va a haber— sino si se dejaron de repetir.
DIA_DE_LA_SEGUNDA_SEMANA = 7

# Los tres veredictos posibles. 'repetir' existe porque es el resultado más
# probable de un primer piloto honesto, y sin esa opción la única salida del
# "casi" es aprobarlo.
VEREDICTOS = {
    "adelante": "Adelante: se despliega a las demás rutas",
    "repetir": "Repetir: se arregla lo encontrado y se vuelve a pilotar",
    "alto": "Alto: el enfoque no funciona como está",
}


# ===========================================================================
# Lo que el sistema dice de un día
# ===========================================================================
# Una sola consulta para las dos cifras del día (confirmadas y canceladas), y
# por una razón: contar las ventas con `WHERE estado = 'confirmada'` y los
# importes en otra consulta abre la puerta a que las dos lean momentos
# distintos de la base.
#
# La cobranza es el efectivo de las ventas del día: la operación es de contado
# (ADR 0002 §81) y ya no hay abonos. Es lo que el papel del vendedor anota como
# «cobrado» y lo que entrega en el corte.
SQL_SISTEMA_POR_DIA = """
WITH dias AS (
    SELECT generate_series(CAST(:desde AS date), CAST(:hasta AS date),
                       interval '1 day')::date AS fecha
),
v AS (
    SELECT fecha_operativa AS fecha,
           count(*) FILTER (WHERE estado = 'confirmada')              AS documentos,
           count(*) FILTER (WHERE estado = 'cancelada')               AS canceladas,
           COALESCE(sum(total) FILTER (WHERE estado = 'confirmada'), 0) AS importe,
           -- Horas DESPUÉS del cierre del día operativo. Negativo (llegó dentro
           -- de su propio día) se lleva a cero: "menos diez horas de retraso" no
           -- es una cifra que nadie pueda leer.
           --
           -- Se mide contra `fecha_operativa + 1 día` y NO contra
           -- `fecha_dispositivo`, que es el reloj del teléfono —el dato del que
           -- `desfase_reloj_seg` existe para desconfiar—. La resta usa la zona
           -- horaria de la sesión de PostgreSQL, que es justo la que `make
           -- doctor` compara contra la del sistema desde la Fase 7.
           GREATEST(
               COALESCE(max(
                   EXTRACT(EPOCH FROM (
                       fecha_servidor - (fecha_operativa + interval '1 day')
                   )) / 3600
               ), 0), 0) AS retraso_horas
      FROM ventas
     WHERE vendedor_id = :vendedor
       AND fecha_operativa BETWEEN :desde AND :hasta
     GROUP BY fecha_operativa
),
c AS (
    SELECT fecha_operativa AS fecha,
           COALESCE(sum(total) FILTER (WHERE estado = 'confirmada'
                                         AND forma_pago = 'efectivo'), 0) AS cobranza
      FROM ventas
     WHERE vendedor_id = :vendedor
       AND fecha_operativa BETWEEN :desde AND :hasta
     GROUP BY fecha_operativa
),
n AS (
    SELECT fecha_operativa AS fecha, count(*) AS no_drops
      FROM no_drops
     WHERE vendedor_id = :vendedor
       AND fecha_operativa BETWEEN :desde AND :hasta
     GROUP BY fecha_operativa
),
l AS (
    SELECT fecha_operativa AS fecha,
           bool_or(estado = 'cerrada' AND sync_completa) AS liquidada
      FROM liquidaciones
     WHERE vendedor_id = :vendedor
       AND fecha_operativa BETWEEN :desde AND :hasta
     GROUP BY fecha_operativa
)
SELECT d.fecha,
       COALESCE(v.documentos, 0)    AS documentos,
       COALESCE(v.canceladas, 0)    AS canceladas,
       COALESCE(v.importe, 0)       AS importe,
       COALESCE(v.retraso_horas, 0) AS retraso_horas,
       COALESCE(c.cobranza, 0)      AS cobranza,
       COALESCE(n.no_drops, 0)      AS no_drops,
       COALESCE(l.liquidada, false) AS liquidada
  FROM dias d
  LEFT JOIN v ON v.fecha = d.fecha
  LEFT JOIN c ON c.fecha = d.fecha
  LEFT JOIN n ON n.fecha = d.fecha
  LEFT JOIN l ON l.fecha = d.fecha
 ORDER BY d.fecha
"""

# El mismo cálculo para UN día. Es lo que se congela al capturar el cuadre.
#
# Existe aparte en vez de reusar la de arriba con desde = hasta porque esta se
# ejecuta en el camino de escritura del formulario, y ahí la claridad de lo que
# se guarda vale más que ahorrar una consulta.
SQL_SISTEMA_DE_UN_DIA = """
SELECT
  (SELECT count(*) FROM ventas
    WHERE vendedor_id = :vendedor AND fecha_operativa = :fecha
      AND estado = 'confirmada')                                AS documentos,
  (SELECT COALESCE(sum(total), 0) FROM ventas
    WHERE vendedor_id = :vendedor AND fecha_operativa = :fecha
      AND estado = 'confirmada')                                AS importe,
  (SELECT COALESCE(sum(total), 0) FROM ventas
    WHERE vendedor_id = :vendedor AND fecha_operativa = :fecha
      AND estado = 'confirmada' AND forma_pago = 'efectivo')    AS cobranza
"""

# Las jornadas capturadas, con su cifra congelada. Se cruza en Python con
# SQL_SISTEMA_POR_DIA: dos consultas simples y comparables en una prueba, en vez
# de un JOIN de siete CTE que nadie puede leer.
SQL_JORNADAS_CAPTURADAS = """
SELECT j.fecha_operativa AS fecha,
       j.documentos_papel, j.importe_papel, j.cobranza_papel, j.visitas_papel,
       j.documentos_sistema, j.importe_sistema, j.cobranza_sistema,
       j.diferencia_documentos, j.diferencia_importe, j.diferencia_cobranza,
       j.capturado_en, j.observaciones,
       u.nombre AS capturado_por
  FROM piloto_jornadas j
  LEFT JOIN usuarios u ON u.id = j.capturado_por
 WHERE j.piloto_id = :piloto
 ORDER BY j.fecha_operativa
"""

SQL_INCIDENCIAS = """
SELECT i.id, i.fecha_operativa, i.hora, i.categoria, i.severidad,
       i.que_hacia, i.que_paso, i.costo_una_venta, i.hubo_que_usar_papel,
       i.minutos_perdidos, i.resuelta_en, i.resolucion, i.registrado_en,
       u.nombre AS registrado_por
  FROM piloto_incidencias i
  LEFT JOIN usuarios u ON u.id = i.registrado_por
 WHERE i.piloto_id = :piloto
 ORDER BY i.fecha_operativa DESC, i.hora DESC NULLS LAST, i.registrado_en DESC
"""

# La cuarentena se busca por USUARIO y no por dispositivo: durante un piloto el
# teléfono puede cambiarse —se moja, se rompe, se presta otro— y lo que se está
# midiendo es la ruta del vendedor, no el aparato.
SQL_CUARENTENA = """
SELECT count(*) AS cuantas
  FROM sync_cuarentena
 WHERE usuario_id = :vendedor
   AND recibido_en >= CAST(:desde AS date)
   AND recibido_en < (CAST(:hasta AS date) + interval '1 day')
"""

SQL_CRITERIOS = """
SELECT codigo, nombre, pregunta, umbral, unidad, comparacion,
       bloqueante, evaluable, nota, orden
  FROM piloto_criterios
 ORDER BY orden
"""

SQL_PILOTO_ACTIVO = """
SELECT p.id, p.codigo, p.vendedor_id, p.ruta_id, p.inicio, p.fin,
       p.cerrado_en, p.veredicto, p.veredicto_nota, p.creado_en,
       u.nombre AS vendedor, u.codigo AS vendedor_codigo,
       r.codigo AS ruta, r.nombre AS ruta_nombre
  FROM pilotos p
  JOIN usuarios u ON u.id = p.vendedor_id
  JOIN rutas    r ON r.id = p.ruta_id
 WHERE p.activo
"""


# ===========================================================================
# La ventana
# ===========================================================================
@dataclass(frozen=True)
class Ventana:
    """Hasta qué día se mide cada cosa. Ver el encabezado del módulo."""

    inicio: date
    fin: date
    hoy: date

    @property
    def hasta_operacion(self) -> date:
        """Lo que ya ocurrió. Nunca más allá de hoy ni del fin del piloto."""
        return min(self.fin, self.hoy)

    @property
    def hasta_cuadre(self) -> date:
        """Lo que ya debería estar capturado: hasta ayer.

        Puede quedar ANTES del inicio —el primer día del piloto, por la mañana—
        y eso es correcto: todavía no hay nada que la oficina haya podido
        capturar, y las consultas devuelven vacío en vez de reclamar.
        """
        return min(self.fin, self.hoy - timedelta(days=1))

    @property
    def dia_del_piloto(self) -> int:
        """Qué día va, contando el inicio como 1. Cero si no ha empezado."""
        if self.hoy < self.inicio:
            return 0
        return min((self.hasta_operacion - self.inicio).days + 1, self.dias_plan)

    @property
    def dias_plan(self) -> int:
        return (self.fin - self.inicio).days + 1

    @property
    def inicio_segunda_semana(self) -> date:
        return self.inicio + timedelta(days=DIA_DE_LA_SEGUNDA_SEMANA)

    @property
    def segunda_semana_empezo(self) -> bool:
        return self.hasta_operacion >= self.inicio_segunda_semana

    @property
    def termino(self) -> bool:
        return self.hoy > self.fin


# ===========================================================================
# Una medición
# ===========================================================================
@dataclass(frozen=True)
class Medicion:
    """Un criterio con su cifra medida, o sin ella.

    `valor is None` significa «todavía no hay con qué medir», y es un estado
    distinto de cumplir y de no cumplir. Pintarlo verde sería la mentira más
    cómoda de toda la pantalla: un piloto que no empezó cumpliría todo.
    """

    codigo: str
    nombre: str
    pregunta: str
    umbral: Decimal
    unidad: str
    comparacion: str
    bloqueante: bool
    evaluable: bool
    nota: str | None
    valor: Decimal | None
    detalle: str

    @property
    def cumple(self) -> bool | None:
        if not self.evaluable or self.valor is None:
            return None
        if self.comparacion == "maximo":
            return self.valor <= self.umbral
        return self.valor >= self.umbral

    @property
    def semaforo(self) -> str:
        if not self.evaluable:
            return "no_evaluable"
        if self.valor is None:
            return "sin_dato"
        return "bien" if self.cumple else "mal"

    @property
    def valor_texto(self) -> str:
        if not self.evaluable:
            return "no se evalúa"
        if self.valor is None:
            return "sin medir"
        return _con_unidad(self.valor, self.unidad)

    @property
    def umbral_texto(self) -> str:
        palabra = "máximo" if self.comparacion == "maximo" else "mínimo"
        return f"{palabra} {_con_unidad(self.umbral, self.unidad)}"


def _con_unidad(valor: Decimal, unidad: str) -> str:
    if unidad == "porcentaje":
        return f"{valor.quantize(DECIMA)} %"
    if unidad == "pesos":
        return f"${valor.quantize(CENTAVO):,}"
    if unidad == "minutos":
        return f"{valor.quantize(DECIMA)} min"
    if unidad == "horas":
        return f"{valor.quantize(DECIMA)} h"
    return f"{valor.quantize(Decimal('1'))}"


# ===========================================================================
# El veredicto
# ===========================================================================
@dataclass(frozen=True)
class Veredicto:
    """Lo que las mediciones permiten decir hoy. No lo que alguien decidió.

    `pasa` es `True` solo cuando el piloto TERMINÓ y todo lo bloqueante que se
    puede medir está en verde. En curso nunca es `True`, aunque todo vaya bien:
    un piloto de dos semanas aprobado el día tres no midió dos semanas.
    """

    ventana: Ventana
    mediciones: tuple[Medicion, ...]

    @property
    def incumplidos(self) -> tuple[Medicion, ...]:
        return tuple(m for m in self.mediciones if m.bloqueante and m.cumple is False)

    @property
    def sin_medir(self) -> tuple[Medicion, ...]:
        return tuple(
            m for m in self.mediciones
            if m.bloqueante and m.evaluable and m.valor is None
        )

    @property
    def no_evaluables(self) -> tuple[Medicion, ...]:
        return tuple(m for m in self.mediciones if not m.evaluable)

    @property
    def pasa(self) -> bool:
        return (
            self.ventana.termino
            and not self.incumplidos
            and not self.sin_medir
        )

    @property
    def estado(self) -> str:
        if self.incumplidos:
            return "con_fallas"
        if not self.ventana.termino:
            return "en_curso"
        if self.sin_medir:
            return "sin_datos"
        return "limpio"

    @property
    def sugerencia(self) -> str:
        """Qué veredicto sugieren las cifras. La decisión sigue siendo de alguien.

        Deliberadamente NO se guarda sola en `pilotos.veredicto`: un veredicto
        automático le quitaría a una persona la obligación de firmar la decisión
        de poner esto en siete camiones.
        """
        if self.estado == "limpio":
            return "adelante"
        if self.estado == "con_fallas":
            return "repetir"
        return ""


def inicio_por_omision(hoy: date) -> date:
    """El lunes siguiente.

    Un piloto no empieza un jueves: la primera semana es la que trae los
    bloqueos, y partirla en dos fines de semana le da al vendedor cuatro días de
    uso con tres de olvido en medio. Arrancar en lunes también hace que «segunda
    semana» signifique lo que la gente entiende por eso.
    """
    return hoy + timedelta(days=(7 - hoy.weekday()) or 7)


def fin_por_omision(inicio: date) -> date:
    """Dos semanas de calendario, de lunes a domingo: lo que pide el plan."""
    return inicio + timedelta(days=13)


# ===========================================================================
# La evaluación, como función pura
# ===========================================================================
# Recibe lo que ya se leyó de la base y no toca la base. Es lo que permite
# probar los doce criterios con datos armados a mano —incluidos los casos que
# cuesta provocar, como "el papel dice 23 y el sistema sigue en 21"— sin montar
# dos semanas de operación en una prueba.
#
# El diccionario `_MEDIDAS` cierra el otro riesgo: un criterio sembrado en la
# migración sin medición en el código saldría en la pantalla como "sin medir"
# para siempre, y nadie distinguiría eso de "todavía no hay datos". Hay una
# prueba que compara las llaves de este diccionario contra los renglones de la
# tabla, igual que las siete verificaciones de frescura de contratos del CI.


def _suma(filas, clave) -> Decimal:
    return sum((Decimal(str(f[clave])) for f in filas), Decimal("0"))


def _trabajados(dias) -> list:
    """Los días en que hubo operación.

    Un domingo sin ventas no es una jornada que falte capturar: es un domingo.
    Sin esta distinción, el denominador de la cobertura metería los descansos y
    un piloto perfecto se vería al 85%.
    """
    return [d for d in dias if d["documentos"] or Decimal(str(d["cobranza"])) != 0]


def _porcentaje(parte: Decimal, total: Decimal) -> Decimal | None:
    if total == 0:
        return None
    return (parte * 100 / total).quantize(DECIMA)


def _fechas(fechas, *, tope: int = 5) -> str:
    """Las primeras fechas, y DICE cuántas quedaron fuera.

    Cortar la lista en cinco y callarlo deja un detalle que se lee como la lista
    completa: «faltan 9 documentos, en: 05/10, 06/10» invita a revisar dos días
    cuando fueron siete. En el renglón que decide un despliegue, media verdad
    cuesta más que una lista larga.
    """
    mostradas = ", ".join(f"{f:%d/%m}" for f in fechas[:tope])
    sobran = len(fechas) - tope
    return f"{mostradas} y {sobran} más" if sobran > 0 else mostradas


def medir(
    *,
    criterios,
    dias,
    jornadas,
    incidencias,
    cuarentena: int,
    ventana: Ventana,
) -> Veredicto:
    por_fecha = {d["fecha"]: d for d in dias}
    trabajados = _trabajados(dias)

    # Las jornadas capturadas, cruzadas con lo que el sistema dice HOY.
    cruzadas = []
    for j in jornadas:
        hoy = por_fecha.get(j["fecha"])
        cruzadas.append(
            {
                "fecha": j["fecha"],
                "documentos_papel": j["documentos_papel"],
                "importe_papel": Decimal(str(j["importe_papel"])),
                "cobranza_papel": Decimal(str(j["cobranza_papel"])),
                "documentos_congelado": j["documentos_sistema"],
                "documentos_hoy": hoy["documentos"] if hoy else 0,
                "importe_hoy": Decimal(str(hoy["importe"])) if hoy else Decimal("0"),
                "cobranza_hoy": Decimal(str(hoy["cobranza"])) if hoy else Decimal("0"),
            }
        )

    medidas = _MEDIDAS
    resultado = []
    for c in criterios:
        calculo = medidas.get(c["codigo"])
        if not c["evaluable"] or calculo is None:
            valor, detalle = None, (c["nota"] or "")
            if c["evaluable"] and calculo is None:
                detalle = (
                    "Este criterio está sembrado en `piloto_criterios` y no tiene "
                    "medición en `domain/piloto.py`. Es un error del código, no "
                    "una falta de datos."
                )
        else:
            valor, detalle = calculo(
                dias=dias,
                trabajados=trabajados,
                cruzadas=cruzadas,
                incidencias=incidencias,
                cuarentena=cuarentena,
                ventana=ventana,
            )
        resultado.append(
            Medicion(
                codigo=c["codigo"],
                nombre=c["nombre"],
                pregunta=c["pregunta"],
                umbral=Decimal(str(c["umbral"])),
                unidad=c["unidad"],
                comparacion=c["comparacion"],
                bloqueante=c["bloqueante"],
                evaluable=c["evaluable"],
                nota=c["nota"],
                valor=valor,
                detalle=detalle,
            )
        )
    return Veredicto(ventana=ventana, mediciones=tuple(resultado))


# --------------------------------------------------------------------------
# Las doce mediciones
# --------------------------------------------------------------------------
def _cobertura_papel(*, trabajados, cruzadas, ventana, **_):
    """De las jornadas que ya se pudieron capturar, cuántas se capturaron.

    El denominador son los días TRABAJADOS hasta ayer, más cualquier día con
    captura. Ese "más" no es simetría: cubre el caso en que el papel trae una
    jornada y el sistema no tiene ni una venta de ese día, que es la falla más
    grave posible y que sin él no entraría en ninguna cuenta.
    """
    capturadas = {c["fecha"] for c in cruzadas if c["fecha"] <= ventana.hasta_cuadre}
    debidas = {d["fecha"] for d in trabajados if d["fecha"] <= ventana.hasta_cuadre}
    debidas |= capturadas
    if not debidas:
        return None, "Todavía no hay ninguna jornada que se pudiera capturar."
    pct = _porcentaje(Decimal(len(capturadas & debidas)), Decimal(len(debidas)))
    return pct, (
        f"{len(capturadas & debidas)} de {len(debidas)} jornadas con su cuadre "
        f"capturado (hasta el {ventana.hasta_cuadre:%d/%m})."
    )


def _faltantes_definitivos(*, cruzadas, **_):
    """Documentos del papel que HOY siguen sin estar. La falla que no perdona.

    Solo cuenta lo que falta, nunca lo que sobra: si el sistema tiene 24 y el
    papel 23, la resta daría −1 y restaría un faltante real de otro día. Un
    sobrante es otro hallazgo (una venta capturada dos veces) y se ve en la
    columna de diferencias del cuadre.
    """
    if not cruzadas:
        return None, "Sin jornadas capturadas todavía."
    faltan = sum(
        max(0, c["documentos_papel"] - c["documentos_hoy"]) for c in cruzadas
    )
    dias_con_falta = [
        c for c in cruzadas if c["documentos_papel"] > c["documentos_hoy"]
    ]
    if not faltan:
        return Decimal(0), (
            f"Los {sum(c['documentos_papel'] for c in cruzadas)} documentos del "
            f"papel están en el sistema."
        )
    cuales = _fechas([c["fecha"] for c in dias_con_falta])
    return Decimal(faltan), f"Faltan {faltan} documento(s), en: {cuales}."


def _faltantes_al_capturar(*, cruzadas, **_):
    """Lo que faltaba en la mañana. Mide el retraso, no la pérdida."""
    total = Decimal(sum(c["documentos_papel"] for c in cruzadas))
    if total == 0:
        return None, "Sin jornadas capturadas todavía."
    faltaban = sum(
        max(0, c["documentos_papel"] - c["documentos_congelado"]) for c in cruzadas
    )
    llegaron = sum(
        max(0, min(c["documentos_papel"], c["documentos_hoy"]) - c["documentos_congelado"])
        for c in cruzadas
    )
    pct = _porcentaje(Decimal(faltaban), total)
    return pct, (
        f"{faltaban} de {int(total)} documentos no estaban al capturar; "
        f"{llegaron} llegaron después."
    )


def _importe_cuadra(*, cruzadas, **_):
    """Desviación del importe. Se suman VALORES ABSOLUTOS, y es lo que importa.

    Con la resta simple, +$300 un día y −$300 el siguiente dan cero y la
    pantalla diría que cuadra perfecto habiendo dos días descuadrados. El signo
    de cada día se ve en el cuadre; el criterio mide cuánto se movió en total.
    """
    total = _suma(cruzadas, "importe_papel")
    if total == 0:
        return None, "Sin importes de papel capturados todavía."
    desvio = sum(
        (abs(c["importe_hoy"] - c["importe_papel"]) for c in cruzadas), Decimal("0")
    )
    pct = _porcentaje(desvio, total)
    return pct, (
        f"${desvio.quantize(CENTAVO):,} de desvío sobre "
        f"${total.quantize(CENTAVO):,} de papel."
    )


def _cobranza_cuadra(*, cruzadas, **_):
    total = _suma(cruzadas, "cobranza_papel")
    if total == 0:
        return None, "El papel no reporta cobranza en el periodo capturado."
    desvio = sum(
        (abs(c["cobranza_hoy"] - c["cobranza_papel"]) for c in cruzadas), Decimal("0")
    )
    pct = _porcentaje(desvio, total)
    return pct, (
        f"${desvio.quantize(CENTAVO):,} de desvío sobre "
        f"${total.quantize(CENTAVO):,} cobrados en papel."
    )


# Lo que hace que los dos criterios que leen la bitácora no se puedan ganar
# dejándola vacía.
#
# Lo encontró una prueba escrita para otra cosa —que nada se pinte de verde sin
# datos—: con la bitácora vacía, "0 bloqueos en la segunda semana" y "0 minutos
# perdidos" salían en verde. Es decir que la forma más fácil de aprobar el
# piloto era NO REGISTRAR NADA, y de paso la única que no deja rastro.
#
# Un piloto de dos semanas de una app nueva con cero incidencias de cualquier
# tipo no es una app perfecta: es una bitácora que nadie llevó. Siempre hay un
# "el teclado tapa el total". Así que con la bitácora completamente vacía los
# dos criterios quedan SIN MEDIR, que es la verdad.
#
# Basta UNA incidencia de cualquier severidad para que el conteo vuelva a ser
# legible: lo que se está comprobando no es que haya problemas, es que alguien
# está preguntando.
VACIA = (
    "La bitácora está vacía en todo el piloto. Eso no se puede leer como «no "
    "pasó nada»: en dos semanas con una app nueva siempre hay algo, y un cero "
    "aquí mide que nadie preguntó. Registra aunque sea la molestia más chica."
)


def _bloqueos_segunda_semana(*, incidencias, ventana, **_):
    """Bloqueos a partir del día 8. La tendencia, no el total."""
    if not ventana.segunda_semana_empezo:
        return None, (
            f"La segunda semana empieza el "
            f"{ventana.inicio_segunda_semana:%d/%m}."
        )
    if not incidencias:
        return None, VACIA
    tarde = [
        i for i in incidencias
        if i["severidad"] == "bloqueo"
        and i["fecha_operativa"] >= ventana.inicio_segunda_semana
    ]
    temprano = sum(
        1 for i in incidencias
        if i["severidad"] == "bloqueo"
        and i["fecha_operativa"] < ventana.inicio_segunda_semana
    )
    return Decimal(len(tarde)), (
        f"{len(tarde)} bloqueo(s) en la segunda semana, {temprano} en la primera."
    )


def _minutos_por_jornada(*, trabajados, incidencias, **_):
    """Promedio de minutos perdidos por jornada trabajada.

    Se divide entre jornadas TRABAJADAS y no entre incidencias: lo que se quiere
    saber es cuánto le cuesta al vendedor un día con la app, y dividir entre
    incidencias daría un número que mejora cada vez que se reporta una más.
    """
    if not trabajados:
        return None, "Sin jornadas trabajadas todavía."
    if not incidencias:
        return None, VACIA
    minutos = Decimal(sum(i["minutos_perdidos"] for i in incidencias))
    promedio = (minutos / Decimal(len(trabajados))).quantize(DECIMA)
    return promedio, (
        f"{minutos.quantize(Decimal('1'))} minutos en total, entre "
        f"{len(trabajados)} jornada(s)."
    )


def _retraso_entrega(*, dias, **_):
    con_ventas = [d for d in dias if d["documentos"]]
    if not con_ventas:
        return None, "Sin ventas en el sistema todavía."
    peor = max(Decimal(str(d["retraso_horas"])) for d in con_ventas)
    if peor == 0:
        return Decimal(0), "Todas las ventas llegaron dentro de su día operativo."
    cual = max(con_ventas, key=lambda d: Decimal(str(d["retraso_horas"])))
    return peor.quantize(DECIMA), (
        f"La más tardía fue del {cual['fecha']:%d/%m} y llegó "
        f"{peor.quantize(DECIMA)} h después del cierre de su día."
    )


def _cuarentena(*, cuarentena, ventana, **_):
    if not ventana.dia_del_piloto:
        return None, "El piloto no ha empezado."
    return Decimal(cuarentena), (
        "Nada en cuarentena." if not cuarentena
        else f"{cuarentena} operación(es) que el servidor no pudo aplicar."
    )


def _liquidaciones_cerradas(*, trabajados, ventana, **_):
    """Jornadas con liquidación cerrada y cola vacía.

    Se mide hasta AYER por la misma razón que el cuadre de papel: la
    liquidación del día se cierra a la mañana siguiente, cuando el camión
    regresa y se cuenta el retorno.
    """
    debidas = [d for d in trabajados if d["fecha"] <= ventana.hasta_cuadre]
    if not debidas:
        return None, "Todavía no hay jornadas cuya liquidación debiera estar cerrada."
    cerradas = sum(1 for d in debidas if d["liquidada"])
    pct = _porcentaje(Decimal(cerradas), Decimal(len(debidas)))
    abiertas = [d["fecha"] for d in debidas if not d["liquidada"]]
    detalle = f"{cerradas} de {len(debidas)} jornadas con liquidación cerrada"
    if abiertas:
        detalle += f"; sin cerrar: {_fechas(abiertas)}"
    return pct, detalle + "."


def _cancelaciones(*, dias, **_):
    confirmadas = Decimal(sum(d["documentos"] for d in dias))
    canceladas = Decimal(sum(d["canceladas"] for d in dias))
    total = confirmadas + canceladas
    if total == 0:
        return None, "Sin ventas en el sistema todavía."
    pct = _porcentaje(canceladas, total)
    return pct, (
        f"{canceladas.quantize(Decimal('1'))} canceladas de "
        f"{total.quantize(Decimal('1'))} capturadas."
    )


_MEDIDAS = {
    "cobertura_papel": _cobertura_papel,
    "faltantes_definitivos": _faltantes_definitivos,
    "importe_cuadra": _importe_cuadra,
    "cobranza_cuadra": _cobranza_cuadra,
    "bloqueos_segunda_semana": _bloqueos_segunda_semana,
    "minutos_por_jornada": _minutos_por_jornada,
    "retraso_entrega": _retraso_entrega,
    "cuarentena": _cuarentena,
    "liquidaciones_cerradas": _liquidaciones_cerradas,
    "cancelaciones": _cancelaciones,
    "faltantes_al_capturar": _faltantes_al_capturar,
    # `impresion_bluetooth` NO está aquí, y es correcto: la tabla lo marca
    # `evaluable = false`, así que nunca se le pide medición. Si algún día llega
    # la EC-MP200 y se vuelve evaluable, esta ausencia es lo que hará que la
    # prueba de frescura se ponga roja y recuerde que falta implementarla.
}
