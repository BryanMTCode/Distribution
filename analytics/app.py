"""Laboratorio analítico.

**Solo lectura, a propósito.** Se conecta con el rol `dsd_analitica`, que no
tiene INSERT, UPDATE ni DELETE (ver `server/db/ops/rol_analitico.sql`): una
consulta exploratoria mal escrita no puede tocar la cartera ni el inventario.

Toda escritura va por la API de FastAPI y el panel de operación. Ver el ADR
0001 para el razonamiento.

Regla de rendimiento: **agregar en SQL, no en Pandas.** Traer un año de ventas
a memoria para agrupar es órdenes de magnitud más lento que un GROUP BY sobre
vistas materializadas. Pandas es la última milla —el modelo, el pronóstico—,
no la capa de agregación.
"""

from __future__ import annotations

import os

import pandas as pd
import psycopg
import streamlit as st

URL = os.environ.get("DSD_ANALITICA_URL", "")

st.set_page_config(page_title="DSD · Laboratorio analítico", page_icon="📊", layout="wide")


@st.cache_resource
def conexion() -> psycopg.Connection:
    if not URL:
        st.error("Falta DSD_ANALITICA_URL.")
        st.stop()
    return psycopg.connect(URL, autocommit=True)


@st.cache_data(ttl=300)
def consultar(sql: str, parametros: tuple = ()) -> pd.DataFrame:
    with conexion().cursor() as cursor:
        cursor.execute(sql, parametros or None)
        columnas = [c.name for c in cursor.description or []]
        return pd.DataFrame(cursor.fetchall(), columns=columnas)


st.title("Laboratorio analítico")
st.caption(
    "Solo lectura. Las escrituras van por el panel de operación. "
    "Fase 8: esquema estrella, cohortes y modelos."
)

# -----------------------------------------------------------------------------
# Salud de sincronización
# -----------------------------------------------------------------------------
# "Tiempo real" es "tiempo real de lo sincronizado" (ARQUITECTURA.md §0.3). Si
# una ruta lleva dos horas sin señal, cualquier número de ventas del día está
# incompleto — y quien lo mira tiene que saberlo.
st.subheader("Salud de sincronización")

try:
    salud = consultar("SELECT * FROM v_salud_sync ORDER BY minutos_sin_sync DESC NULLS FIRST")
except psycopg.Error as e:
    st.warning(f"No se pudo leer v_salud_sync: {e}")
    salud = pd.DataFrame()

if salud.empty:
    st.info("Aún no hay dispositivos activos registrados.")
else:
    criticos = int((salud["estado_sync"].isin(["critico", "nunca"])).sum())
    columnas = st.columns(3)
    columnas[0].metric("Dispositivos activos", len(salud))
    columnas[1].metric("Rezagados o sin sincronizar", criticos)
    columnas[2].metric(
        "Operaciones en cuarentena", int(salud["ops_en_cuarentena"].fillna(0).sum())
    )
    if criticos:
        st.warning(
            f"{criticos} dispositivo(s) sin sincronizar. Las cifras del día están incompletas."
        )
    st.dataframe(salud, use_container_width=True, hide_index=True)

with st.expander("Por qué este panel es de solo lectura"):
    st.markdown(
        """
El panel administrativo no es un dashboard: resolver la cuarentena de sync,
fusionar clientes duplicados, confirmar cargas y cerrar liquidaciones son
escrituras transaccionales críticas.

Streamlit re-ejecuta el script completo en cada interacción, y todo este sistema
está construido alrededor de **no duplicar documentos**. Esas escrituras viven
en el panel de operación (FastAPI + Jinja2 + HTMX), con el mismo RBAC y el mismo
patrón de idempotencia que `/sync/push`.

Aquí se explora. Ver `docs/adr/0001-stack-tecnologico.md`.
"""
    )
