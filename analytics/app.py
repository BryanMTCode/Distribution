"""Laboratorio analítico — Fase 8.

────────────────────────────────────────────────────────────────────────────
SOLO LECTURA, A PROPÓSITO
────────────────────────────────────────────────────────────────────────────
Se conecta con el rol `dsd_analitica`, que no tiene INSERT, UPDATE ni DELETE
(`server/db/ops/rol_analitico.sql`): una consulta exploratoria mal escrita no
puede tocar la cartera ni el inventario.

Y hay una razón más fuerte que la prudencia. Streamlit **re-ejecuta el script
completo en cada interacción**, y todo este sistema está construido alrededor de
no duplicar documentos. Un botón que escribiera aquí se dispararía de nuevo al
mover un filtro. Las escrituras viven en el panel de operación, con el mismo
RBAC y el mismo patrón de idempotencia que `/sync/push`.

────────────────────────────────────────────────────────────────────────────
LAS DEFINICIONES NO ESTÁN EN ESTE ARCHIVO
────────────────────────────────────────────────────────────────────────────
Están en `server/app/domain/analitica.py`, y eso es deliberado. El riesgo del
laboratorio no es que una consulta sea lenta: es que **la misma pregunta se
conteste distinto cada vez que alguien la hace**. Con el SQL aquí, la segunda
persona que necesite el drop size lo vuelve a escribir y da otro número.

Aquí solo se dibuja.

────────────────────────────────────────────────────────────────────────────
CADA CIFRA CON SU ANTIGÜEDAD
────────────────────────────────────────────────────────────────────────────
§0.3: "tiempo real" es "tiempo real de lo que ya sincronizó". Las cifras salen
de vistas materializadas, así que son una foto de cuando corrió el job — y si en
ese momento había teléfonos sin subir su día, son un **piso**, no un total. La
barra de arriba lo dice siempre, incluso cuando todo está bien: un número sin
fecha se trata como la verdad.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import psycopg
import streamlit as st

# Las definiciones viven con el dominio del servidor, no aquí. Se importan en vez
# de copiarse: dos copias del mismo SQL se separan en la primera corrección.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from app.domain.analitica import (  # noqa: E402
    AGRUPACIONES,
    FACTOR_PERDIDO,
    FACTOR_RIESGO,
    SQL_CLIENTES_EN_RIESGO,
    SQL_FRECUENCIA_VISITA,
    SQL_FRESCURA,
    SQL_PRODUCTIVIDAD,
    SQL_ROTACION,
    Frescura,
    sql_drop_size,
)

URL = os.environ.get("DSD_ANALITICA_URL", "")

st.set_page_config(
    page_title="DSD · Laboratorio analítico", page_icon="📊", layout="wide"
)


@st.cache_resource
def conexion() -> psycopg.Connection:
    if not URL:
        st.error(
            "Falta `DSD_ANALITICA_URL`. El rol de solo lectura se crea con "
            "`server/db/ops/rol_analitico.sql`."
        )
        st.stop()
    return psycopg.connect(URL, autocommit=True)


@st.cache_data(ttl=300)
def consultar(sql: str, parametros: dict | None = None) -> pd.DataFrame:
    """Ejecuta y devuelve un DataFrame.

    **Agregar en SQL, no en Pandas.** Traer un año de ventas a memoria para
    agrupar es órdenes de magnitud más lento que un GROUP BY sobre las vistas
    materializadas. Pandas es la última milla —el formato, el modelo— no la capa
    de agregación.
    """
    with conexion().cursor() as cursor:
        cursor.execute(sql, parametros or None)
        columnas = [c.name for c in cursor.description or []]
        return pd.DataFrame(cursor.fetchall(), columns=columnas)


def leer_frescura() -> tuple[Frescura | None, pd.DataFrame]:
    try:
        detalle = consultar(SQL_FRESCURA)
    except psycopg.Error:
        return None, pd.DataFrame()
    if detalle.empty:
        return None, detalle
    # La vista MÁS VIEJA manda: el laboratorio es tan fresco como su peor pieza.
    peor = detalle.loc[detalle["segundos_desde"].idxmax()]
    return (
        Frescura(
            minutos=int(peor["segundos_desde"]) // 60,
            equipos_sin_sincronizar=int(peor["equipos_sin_sincronizar"] or 0),
            ops_en_cuarentena=int(peor["ops_en_cuarentena"] or 0),
        ),
        detalle,
    )


def dinero(serie: pd.Series) -> pd.Series:
    return serie.map(lambda v: "—" if pd.isna(v) else f"${v:,.2f}")


def graficable(datos: pd.DataFrame, columnas: list[str]) -> pd.DataFrame:
    """Convierte a float SOLO lo que va a una gráfica.

    PostgreSQL devuelve `numeric` como `Decimal`, y eso es exactamente lo que se
    quiere para el dinero: un float no puede representar 296.10 y de ahí al
    centavo perdido hay un paso. Pero Altair no sabe qué hacer con un `Decimal`
    —avisa «I don't know how to infer vegalite type»— y dibuja el eje como si
    fueran categorías de texto, con lo que la gráfica queda ilegible.
    """
    recortado = datos[columnas].astype(float)
    return recortado


# ===========================================================================
# La barra de estado: de cuándo son estos números
# ===========================================================================
st.title("Laboratorio analítico")

frescura, detalle_frescura = leer_frescura()

if frescura is None:
    st.warning(
        "El esquema estrella no se ha calculado nunca. Encola el job con "
        "`refrescar_analitica` o levanta el worker (`make worker`)."
    )
    st.caption(
        "Sin ese job las vistas existen pero están vacías o viejas, y cualquier "
        "cifra de aquí abajo sería del momento en que se aplicó la migración."
    )
else:
    columnas = st.columns([2, 1, 1])
    columnas[0].metric(
        "Datos recalculados",
        "hace menos de 1 min" if frescura.minutos < 1 else f"hace {frescura.minutos} min",
    )
    columnas[1].metric("Equipos sin sincronizar", frescura.equipos_sin_sincronizar)
    columnas[2].metric("Operaciones en cuarentena", frescura.ops_en_cuarentena)

    if frescura.advertencia:
        st.warning(f"**Lee estas cifras con cuidado.** {frescura.advertencia}")
    else:
        st.success(
            "Todos los equipos sincronizaron y no hay nada en cuarentena: las "
            "cifras de abajo están completas."
        )

    with st.expander("Detalle del último recálculo"):
        st.dataframe(
            detalle_frescura[
                ["vista", "refrescado_en", "renglones", "duracion_ms"]
            ],
            width="stretch",
            hide_index=True,
        )

# ===========================================================================
# Filtros
# ===========================================================================
st.sidebar.header("Periodo")
hoy = date.today()
preset = st.sidebar.radio(
    "Rango",
    ["Últimos 7 días", "Últimos 30 días", "Últimos 90 días", "A medida"],
    index=1,
)
dias = {"Últimos 7 días": 7, "Últimos 30 días": 30, "Últimos 90 días": 90}
if preset == "A medida":
    desde = st.sidebar.date_input("Desde", hoy - timedelta(days=30))
    hasta = st.sidebar.date_input("Hasta", hoy)
else:
    desde, hasta = hoy - timedelta(days=dias[preset] - 1), hoy

if desde > hasta:
    # Se endereza en vez de devolver una pantalla vacía: quien teclea las fechas
    # al revés quiere ver ese periodo, no un error.
    desde, hasta = hasta, desde

st.sidebar.caption(f"Del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}")
rango = {"desde": desde, "hasta": hasta}

pestanas = st.tabs(
    ["Drop size", "Vendedores", "Clientes en riesgo", "Frecuencia", "Rotación"]
)

# ===========================================================================
# Drop size
# ===========================================================================
with pestanas[0]:
    st.subheader("Drop size")
    st.caption(
        "**Importe promedio de una visita que vendió.** Es la palanca más barata "
        "de un DSD: subirlo no cuesta una visita más, cuesta vender mejor en la "
        "que ya se hizo. Una visita es un cliente visitado en un día, no un "
        "documento: dos remisiones al mismo cliente el mismo día son una visita."
    )

    agrupacion = st.radio(
        "Agrupar por", list(AGRUPACIONES), horizontal=True, key="agr_drop"
    )
    datos = consultar(sql_drop_size(agrupacion), rango)

    if datos.empty:
        st.info("No hay visitas registradas en este periodo.")
    else:
        visitas = int(datos["visitas"].sum())
        con_venta = int(datos["visitas_con_venta"].sum())
        importe = float(datos["importe"].sum())
        tarjetas = st.columns(4)
        tarjetas[0].metric("Visitas", f"{visitas:,}")
        tarjetas[1].metric(
            "Efectividad",
            f"{(100 * con_venta / visitas):.1f}%" if visitas else "—",
            help="Visitas que terminaron en venta. Sobre " f"{visitas:,} visitas.",
        )
        tarjetas[2].metric(
            "Drop size",
            f"${(importe / con_venta):,.2f}" if con_venta else "—",
            help="Importe ÷ visitas que vendieron.",
        )
        tarjetas[3].metric(
            "Importe por visita",
            f"${(importe / visitas):,.2f}" if visitas else "—",
            help="Incluye las visitas perdidas. Es drop size × efectividad: otra "
            "métrica, no la misma.",
        )

        st.line_chart(
            graficable(
                datos.set_index("periodo"), ["drop_size", "importe_por_visita"]
            )
        )
        st.dataframe(
            datos.assign(
                importe=dinero(datos["importe"]),
                drop_size=dinero(datos["drop_size"]),
                importe_por_visita=dinero(datos["importe_por_visita"]),
            ),
            width="stretch",
            hide_index=True,
        )

# ===========================================================================
# Productividad por vendedor
# ===========================================================================
with pestanas[1]:
    st.subheader("Productividad por vendedor")
    st.caption(
        "Los promedios diarios van sobre **días trabajados**, no sobre los días "
        "del periodo: un vendedor que estuvo de vacaciones una semana no tiene "
        "por qué salir mal en el promedio. Un día trabajado es un día en que "
        "registró al menos una visita — el único dato que de verdad tenemos."
    )
    datos = consultar(SQL_PRODUCTIVIDAD, rango)

    if datos.empty:
        st.info("Ningún vendedor registró visitas en este periodo.")
    else:
        st.dataframe(
            datos.assign(
                importe=dinero(datos["importe"]),
                drop_size=dinero(datos["drop_size"]),
                importe_por_dia=dinero(datos["importe_por_dia"]),
            ).drop(columns=["vendedor_id"]),
            width="stretch",
            hide_index=True,
        )
        st.bar_chart(graficable(datos.set_index("nombre"), ["drop_size"]))

# ===========================================================================
# Clientes en riesgo
# ===========================================================================
with pestanas[2]:
    st.subheader("Clientes en riesgo de abandono")
    st.caption(
        f"Medido contra la **cadencia propia de cada cliente**, no contra un "
        f"umbral fijo. En riesgo a partir de {FACTOR_RIESGO}× su cadencia; "
        f"perdido a partir de {FACTOR_PERDIDO}×."
    )
    with st.expander("Por qué no un umbral de 30 días"):
        st.markdown(
            """
Un umbral fijo engaña en las dos direcciones:

* Marca como perdido al cliente de barrio que **siempre** compró cada 45 días.
* Deja pasar al que compraba cada semana y lleva 20 días sin aparecer — y ése
  es el que de verdad se está yendo.

Hacen falta al menos 3 compras para tener una cadencia que signifique algo. Con
menos, el cliente es **nuevo** y aparece en su propia categoría en vez de
mezclarse con los que se van: una lista larga de falsos positivos se deja de
leer, y entonces el reporte deja de servir.
"""
        )

    datos = consultar(
        SQL_CLIENTES_EN_RIESGO,
        {"factor_riesgo": FACTOR_RIESGO, "factor_perdido": FACTOR_PERDIDO},
    )

    if datos.empty:
        st.info("Todavía no hay clientes con historial de compra.")
    else:
        conteos = datos["situacion"].value_counts()
        tarjetas = st.columns(4)
        for i, (clave, etiqueta) in enumerate(
            [
                ("en_riesgo", "En riesgo"),
                ("perdido", "Perdidos"),
                ("al_corriente", "Al corriente"),
                ("nuevo", "Nuevos"),
            ]
        ):
            tarjetas[i].metric(etiqueta, int(conteos.get(clave, 0)))

        # Las opciones son las situaciones QUE EXISTEN en los datos, y el default
        # es su intersección con lo que interesa. Pasarle a `multiselect` un
        # default que no está entre las opciones lanza
        # `StreamlitDefaultNotInOptionsError` y **tumba la página entera** — y pasa
        # justo en el caso más común al empezar: una base con pocos clientes donde
        # todavía nadie está en riesgo.
        disponibles = [
            s
            for s in ("en_riesgo", "perdido", "al_corriente", "nuevo")
            if s in conteos.index
        ]
        por_omision = [s for s in ("en_riesgo", "perdido") if s in disponibles]
        situacion = st.multiselect("Mostrar", disponibles, default=por_omision)
        filtrado = datos[datos["situacion"].isin(situacion)] if situacion else datos
        st.dataframe(
            filtrado.assign(
                importe_historico=dinero(filtrado["importe_historico"])
            ).drop(columns=["cliente_id"]),
            width="stretch",
            hide_index=True,
        )

# ===========================================================================
# Frecuencia de visita
# ===========================================================================
with pestanas[3]:
    st.subheader("Frecuencia de visita")
    st.caption(
        "Días entre visitas al mismo cliente, en **mediana**. Mediana y no "
        "promedio: un cliente visitado semanalmente al que un día se le dejó de "
        "ir dos meses tiene un promedio de 14 días que no describe ninguna "
        "semana real."
    )
    datos = consultar(SQL_FRECUENCIA_VISITA, rango)

    if datos.empty:
        st.info(
            "Ningún cliente tiene dos visitas o más en este periodo. Prueba con "
            "un rango más amplio."
        )
    else:
        st.metric(
            "Mediana de la ruta",
            f"{datos['mediana_dias'].median():.1f} días",
            help="La mediana de las medianas por cliente.",
        )
        st.dataframe(
            datos.drop(columns=["cliente_id"]),
            width="stretch",
            hide_index=True,
        )

# ===========================================================================
# Rotación
# ===========================================================================
with pestanas[4]:
    st.subheader("Rotación de producto")
    st.caption(
        "Unidades vendidas contra la existencia promedio del camión, "
        "**reconstruida del libro mayor**. No hay fotos diarias del inventario: "
        "`movimientos_inventario` es append-only, así que la existencia de "
        "cualquier fecha se recalcula sumando. La decisión de que el libro fuera "
        "append-only se tomó por auditoría, y resulta que paga dos veces."
    )
    datos = consultar(SQL_ROTACION, rango)

    if datos.empty:
        st.info("No hay productos activos.")
    else:
        st.info(
            "**Lee la rotación junto a «días con existencia».** Una rotación "
            "altísima sobre un producto que estuvo tres días en el camión no es "
            "éxito de ventas: es desabasto. Sin esa columna, la métrica premia "
            "justo lo que hay que corregir."
        )
        solo_vendidos = st.checkbox("Solo los que se movieron", value=True)
        vista = datos[datos["unidades_vendidas"] > 0] if solo_vendidos else datos
        st.dataframe(
            vista.assign(importe=dinero(vista["importe"])).drop(
                columns=["producto_id"]
            ),
            width="stretch",
            hide_index=True,
        )

# ===========================================================================
with st.expander("Por qué este laboratorio es de solo lectura"):
    st.markdown(
        """
Resolver la cuarentena de sync, fusionar clientes duplicados, confirmar cargas y
cerrar liquidaciones son **escrituras transaccionales críticas**. Streamlit
re-ejecuta el script completo en cada interacción, y este sistema está construido
alrededor de no duplicar documentos: un botón que escribiera aquí se dispararía
de nuevo al mover un filtro.

Esas escrituras viven en el panel de operación (FastAPI + Jinja2), con el mismo
RBAC y el mismo patrón de idempotencia que `/sync/push`.

Ver `docs/adr/0001-stack-tecnologico.md` y `docs/adr/0002-reglas-de-negocio.md`.
"""
    )
