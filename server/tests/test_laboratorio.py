"""El laboratorio Streamlit se ejecuta de verdad, con datos de verdad.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTO NO ES UNA PRUEBA DE ADORNO
────────────────────────────────────────────────────────────────────────────
Un Streamlit roto **devuelve HTTP 200**. El servidor sirve una página vacía y el
script se ejecuta después, al abrir el websocket: una excepción ahí no aparece en
ningún código de estado, solo en una traza roja dentro del navegador de quien
abrió el laboratorio.

Así que comprobar que "levanta" no comprueba nada. `AppTest` ejecuta el script
completo como lo haría el navegador, y es la única forma de saber que corre.

Lo que se defiende:

1. **Que el script se ejecute sin excepciones** contra el esquema estrella real.
2. **Que la advertencia de frescura aparezca cuando toca**, porque es el único
   mecanismo que evita que alguien decida con cifras incompletas (§0.3).
3. **Que las cifras que muestra sean las del dominio**, no otras: el laboratorio
   importa `app/domain/analitica.py` en vez de llevar su propio SQL, y esta
   prueba es lo que fija que ese import siga resolviendo — incluido el ajuste de
   `sys.path`, que es justo lo que se rompe al mover un archivo.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from app.workers.analitica import refrescar_todo

pytest.importorskip(
    "streamlit",
    reason="el laboratorio es opcional: uv pip install -e '.[dev,analitica]'",
)

import streamlit as st  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

pytestmark = pytest.mark.asyncio

APP = Path(__file__).resolve().parents[2] / "analytics" / "app.py"


def _correr(url: str | None) -> AppTest:
    """Ejecuta el script del laboratorio como lo haría el navegador.

    La URL va por variable de entorno porque es así como la recibe en producción
    —docker-compose se la pasa con el rol de solo lectura—. Inyectarla de otra
    forma no probaría el camino real.

    **Las cachés se limpian antes de cada corrida.** `@st.cache_data(ttl=300)` y
    `@st.cache_resource` son globales del proceso, no de la instancia de
    `AppTest`: sin esto, la segunda prueba vería los resultados de la primera y
    pasaría —o fallaría— por el dato equivocado. En producción esa caché es lo
    correcto; en una suite, es contaminación entre pruebas.
    """
    st.cache_data.clear()
    st.cache_resource.clear()
    os.environ["DSD_ANALITICA_URL"] = url if url is not None else ""
    return AppTest.from_file(str(APP), default_timeout=120).run()


@pytest.fixture
def url_cruda() -> str:
    """La URL en la forma que usa psycopg, sin el `+psycopg` de SQLAlchemy."""
    from tests.conftest import URL_PRUEBAS

    return URL_PRUEBAS.replace("postgresql+psycopg://", "postgresql://")


@pytest.fixture
async def dia_con_ventas(sesion, semilla) -> dict:
    """Una venta y un no-drop: dos visitas, una vendió.

    Son las cifras mínimas con las que el drop size y la efectividad dan números
    distintos entre sí ($500 y 50 %), que es lo que permite afirmar que el
    laboratorio está leyendo la métrica correcta y no la de al lado.
    """
    dispositivo = uuid.uuid4()
    cliente = uuid.uuid4()
    otro = uuid.uuid4()
    producto = uuid.uuid4()
    hoy = date.today()
    ahora = datetime.now(UTC)

    await sesion.execute(
        text(
            # `ultima_sync_push_en` en now() a propósito: un dispositivo recién
            # creado que nunca empujó nada **está rezagado**, y con el valor nulo
            # el caso "todo sincronizado" no se daría nunca. Lo que se quiere
            # probar es la pantalla cuando el mundo está completo.
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, "
            "                          registrado_en, ultima_sync_push_en) "
            "VALUES (:d, :u, 'POCO M5s', 'activo', now(), now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base) "
            "VALUES (:p, 'ATUN-140', 'Atún en agua 140 g', 'PZA')"
        ),
        {"p": producto},
    )
    for identificador, codigo, nombre in (
        (cliente, "C00001", "Abarrotes Doña Mary"),
        (otro, "C00002", "Tienda El Puente"),
    ):
        await sesion.execute(
            text(
                "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, estatus, "
                "                      creado_en, actualizado_en) "
                "VALUES (:c, :cod, :n, :r, 'activo', now(), now())"
            ),
            {"c": identificador, "cod": codigo, "n": nombre, "r": semilla["ruta"]},
        )

    venta_id = uuid.uuid4()
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, ruta_id, almacen_id, tipo,
                                subtotal, total, fecha_dispositivo, fecha_operativa)
            VALUES (:v, :d, 1, 'VEND01-000001', :c, :u, :r, :a, 'contado',
                    500, 500, :ahora, :dia)
            """
        ),
        {
            "v": venta_id,
            "d": dispositivo,
            "c": cliente,
            "u": semilla["vendedor"],
            "r": semilla["ruta"],
            "a": semilla["camion"],
            "ahora": ahora,
            "dia": hoy,
        },
    )
    await sesion.execute(
        text(
            """
            INSERT INTO venta_partidas (id, venta_id, linea, producto_id, unidad_codigo,
                                        factor_unidad, cantidad, cantidad_base,
                                        precio_unitario, importe)
            VALUES (:id, :v, 1, :p, 'PZA', 1, 10, 10, 50.0000, 500)
            """
        ),
        {"id": uuid.uuid4(), "v": venta_id, "p": producto},
    )
    await sesion.execute(
        text(
            """
            INSERT INTO no_drops (id, dispositivo_id, folio_consecutivo, cliente_id,
                                  vendedor_id, ruta_id, motivo_codigo, lat, lng,
                                  fecha_dispositivo, fecha_operativa)
            VALUES (:n, :d, 2, :c, :u, :r, 'CERRADO', 20.6736, -103.3440, :ahora, :dia)
            """
        ),
        {
            "n": uuid.uuid4(),
            "d": dispositivo,
            "c": otro,
            "u": semilla["vendedor"],
            "r": semilla["ruta"],
            "ahora": ahora,
            "dia": hoy,
        },
    )
    await sesion.commit()
    await refrescar_todo(sesion)
    return {"dispositivo": dispositivo, "dia": hoy, "producto": producto}


async def test_el_laboratorio_se_ejecuta_sin_excepciones(url_cruda, dia_con_ventas):
    """La prueba que un HTTP 200 no puede dar.

    Si el import del dominio dejara de resolver, o una consulta tuviera un error
    de sintaxis, el servidor seguiría contestando 200 y el laboratorio mostraría
    una traza roja a quien lo abriera. Aquí falla el CI en su lugar.
    """
    prueba = _correr(url_cruda)
    assert not prueba.exception, [str(e.value) for e in prueba.exception]
    assert prueba.title[0].value == "Laboratorio analítico"
    # Las cinco preguntas que nombra el plan de la Fase 8.
    assert len(prueba.tabs) == 5


async def test_muestra_la_antiguedad_de_los_datos_siempre(url_cruda, dia_con_ventas):
    """Incluso cuando todo está bien.

    Un número sin fecha se trata como la verdad, y éstos son una foto de cuando
    corrió el job. Que la cifra aparezca SIEMPRE es la mitad del mecanismo; la
    otra mitad es la advertencia, que solo sale cuando hace falta.
    """
    prueba = _correr(url_cruda)
    etiquetas = [m.label for m in prueba.metric]
    assert "Datos recalculados" in etiquetas
    assert "Equipos sin sincronizar" in etiquetas
    assert "Operaciones en cuarentena" in etiquetas


async def test_un_equipo_sin_sincronizar_sale_advertido_en_la_pantalla(
    sesion, url_cruda, dia_con_ventas
):
    """El caso que importa, y el que se olvida.

    «Actualizado hace 1 min» suena perfecto. Si en ese momento un teléfono no
    había subido su día, el total de ventas es un **piso** y no un total, y quien
    lo lea tiene que enterarse sin preguntar.
    """
    await sesion.execute(
        text("UPDATE dispositivos SET ultima_sync_push_en = NULL WHERE id = :d"),
        {"d": dia_con_ventas["dispositivo"]},
    )
    await sesion.commit()
    await refrescar_todo(sesion)

    prueba = _correr(url_cruda)
    assert not prueba.exception
    avisos = " ".join(w.value for w in prueba.warning)
    assert "piso y no un total" in avisos
    assert not prueba.success, "no puede decir que las cifras están completas"


async def test_con_todo_sincronizado_lo_dice_en_positivo(url_cruda, dia_con_ventas):
    """La contraparte: cuando sí se puede confiar, también se dice.

    Si solo hablara para advertir, su silencio sería ambiguo — ¿está bien, o no
    pudo comprobarlo?
    """
    prueba = _correr(url_cruda)
    exitos = " ".join(s.value for s in prueba.success)
    assert "cifras de abajo están completas" in exitos


async def test_el_drop_size_que_muestra_es_el_del_dominio(url_cruda, dia_con_ventas):
    """$500 en una de dos visitas: drop size $500, efectividad 50 %.

    El laboratorio no lleva su propio SQL: importa `app/domain/analitica.py`. Esta
    prueba es lo que fija que siga siendo así — si alguien copiara la consulta
    aquí, el número podría coincidir hoy y separarse en la primera corrección.
    """
    prueba = _correr(url_cruda)
    assert not prueba.exception
    metricas = {m.label: m.value for m in prueba.metric}
    assert metricas["Visitas"] == "2"
    assert metricas["Efectividad"] == "50.0%"
    assert metricas["Drop size"] == "$500.00"
    # Importe por visita: $500 / 2 = $250. Es otra métrica, con otro nombre, y
    # confundirlas es el error más común al leer un reporte de DSD.
    assert metricas["Importe por visita"] == "$250.00"


async def test_sin_recalcular_el_laboratorio_lo_dice_en_vez_de_mentir(
    sesion, url_cruda, semilla
):
    """Sin ningún refresh registrado, no puede mostrar ceros como si fueran datos.

    Un laboratorio que presenta «0 ventas» cuando lo que pasa es que nadie
    recalculó es peor que uno que no abre: el cero se lee como información.
    """
    await sesion.execute(text("DELETE FROM analitica_refrescos"))
    await sesion.commit()

    prueba = _correr(url_cruda)
    assert not prueba.exception
    avisos = " ".join(w.value for w in prueba.warning)
    assert "no se ha calculado nunca" in avisos
    assert "refrescar_analitica" in avisos


async def test_sin_la_url_no_arranca_y_dice_donde_se_configura(dia_con_ventas):
    """Un error de configuración, con su remedio al lado.

    «Falta DSD_ANALITICA_URL» a secas obliga a ir a leer el compose. El mensaje
    nombra el script que crea el rol de solo lectura.
    """
    prueba = _correr(None)
    errores = " ".join(e.value for e in prueba.error)
    assert "DSD_ANALITICA_URL" in errores
    assert "rol_analitico.sql" in errores


async def test_la_rotacion_llega_a_su_pestana_con_el_atun(
    sesion, semilla, url_cruda, dia_con_ventas
):
    """La pestaña de rotación no se queda vacía cuando hay movimiento en el libro.

    Es la consulta más compleja del laboratorio —reconstruye la existencia
    sumando el libro mayor hasta cada fecha— así que es la que más fácil se rompe
    sin que nada lo avise.
    """
    await sesion.execute(
        text(
            """
            INSERT INTO movimientos_inventario
              (tipo, almacen_origen_id, almacen_destino_id, producto_id, cantidad,
               documento_tipo, documento_id, fecha_dispositivo, fecha_servidor)
            VALUES ('carga', :bodega, :camion, :p, 240, 'carga', :doc, now(), now())
            """
        ),
        {
            "bodega": semilla["bodega"],
            "camion": semilla["camion"],
            "p": dia_con_ventas["producto"],
            "doc": uuid.uuid4(),
        },
    )
    await sesion.commit()
    await refrescar_todo(sesion)

    prueba = _correr(url_cruda)
    assert not prueba.exception
    tablas = [d.value for d in prueba.dataframe]
    con_sku = [t for t in tablas if "sku" in getattr(t, "columns", [])]
    assert con_sku, "la pestaña de rotación no dibujó ninguna tabla"
    assert any(
        (t["sku"] == "ATUN-140").any()
        and (t["unidades_vendidas"] == Decimal("10.000")).any()
        for t in con_sku
    )
