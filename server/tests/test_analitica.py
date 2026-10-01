"""Fase 8 · El esquema estrella y las definiciones del laboratorio.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
Un número mal calculado en el laboratorio no falla: **sale, se ve razonable, y
alguien decide con él.** No hay excepción, no hay CI rojo, no hay descuadre. Por
eso cada métrica aquí se prueba con un escenario donde la definición correcta y
la equivocada dan números DISTINTOS — si dieran lo mismo, la prueba no probaría
nada.

Las cuatro que más fácil se calculan mal:

1. **Drop size contando documentos.** Dos remisiones al mismo cliente el mismo
   día son UNA visita. Contar documentos da $500 donde la respuesta es $750, e
   infla la efectividad del 50 % al 60 %.
2. **Promediar sobre los días que hubo venta**, no sobre los días trabajados.
3. **Un umbral fijo de abandono.** Marca como perdido al cliente que siempre
   compró cada 45 días, y deja pasar al que compraba cada semana y lleva 20 sin
   aparecer — que es el que de verdad se está yendo.
4. **Reconstruir la existencia desde cero.** Si el acumulado del libro mayor
   empieza en el primer día del rango, un reporte de la segunda quincena da
   existencias negativas.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.domain.analitica import (
    AGRUPACIONES,
    FACTOR_PERDIDO,
    FACTOR_RIESGO,
    SQL_CLIENTES_EN_RIESGO,
    SQL_FRECUENCIA_VISITA,
    SQL_PRODUCTIVIDAD,
    SQL_ROTACION,
    sql_drop_size,
)
from app.workers.analitica import VISTAS, refrescar_todo, refrescar_una

pytestmark = pytest.mark.asyncio


async def _refrescar(sesion) -> None:
    """Recalcula el esquema estrella, que es lo que hace el job del worker."""
    await refrescar_todo(sesion)


async def _consultar(sesion, sql: str, **parametros) -> list[dict]:
    """Ejecuta una consulta del laboratorio.

    Las consultas usan `%(nombre)s` porque las ejecuta psycopg desde Streamlit;
    aquí se traducen a `:nombre` para SQLAlchemy. La traducción es mecánica y
    vive solo en las pruebas: así el módulo de dominio guarda el SQL **tal como
    lo ejecuta el laboratorio**, y no una variante que solo las pruebas ven.
    """
    import re

    convertido = re.sub(r"%\((\w+)\)s", r":\1", sql)
    filas = (await sesion.execute(text(convertido), parametros)).mappings().all()
    return [dict(f) for f in filas]


@pytest.fixture
async def dia_de_ruta(sesion, semilla) -> dict:
    """Cuatro visitas: dos vendieron, dos no. Y un pedido PARTIDO EN DOS.

    Las cifras están elegidas para que contar documentos y contar visitas den
    resultados distintos:

        contando visitas      4 visitas, 2 con venta, $1,500 → drop $750, 50 %
        contando documentos   5 documentos, 3 ventas, $1,500 → drop $500, 60 %

    Si la prueba pasara con los dos, no estaría probando la definición.
    """
    dispositivo = uuid.uuid4()
    hoy = date.today()
    ahora = datetime.now(UTC)
    producto = uuid.uuid4()
    clientes: dict[str, uuid.UUID] = {}

    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'POCO M5s de Juan', 'activo', now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva) "
            "VALUES (:p, 'ATUN-140', 'Atún en agua 140 g', 'PZA', 0.0000)"
        ),
        {"p": producto},
    )
    await sesion.execute(
        text(
            "INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, es_default) "
            "VALUES (:p, 'PZA', 1, true), (:p, 'CAJA', 24, false)"
        ),
        {"p": producto},
    )
    for i, nombre in enumerate(
        ["Doña Mary", "El Puente", "La Esquina", "Los Pinos"], start=1
    ):
        identificador = uuid.uuid4()
        await sesion.execute(
            text(
                "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, estatus, "
                "                      creado_en, actualizado_en) "
                "VALUES (:c, :cod, :n, :r, 'activo', now(), now())"
            ),
            {"c": identificador, "cod": f"C{i:05d}", "n": nombre, "r": semilla["ruta"]},
        )
        clientes[nombre] = identificador

    folio = [500]

    async def venta(a_quien, total, dia=None, cantidad=Decimal("10.000")):
        folio[0] += 1
        venta_id = uuid.uuid4()
        await sesion.execute(
            text(
                """
                INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                    cliente_id, vendedor_id, ruta_id, almacen_id, tipo,
                                    subtotal, total, fecha_dispositivo, fecha_operativa)
                VALUES (:v, :d, :folio, :folio_local, :c, :u, :r, :a, 'contado',
                        :total, :total, :ahora, :dia)
                """
            ),
            {
                "v": venta_id,
                "d": dispositivo,
                "folio": folio[0],
                "folio_local": f"VEND01-{folio[0]:06d}",
                "c": a_quien,
                "u": semilla["vendedor"],
                "r": semilla["ruta"],
                "a": semilla["camion"],
                "total": total,
                "ahora": ahora,
                "dia": dia or hoy,
            },
        )
        await sesion.execute(
            text(
                """
                INSERT INTO venta_partidas (id, venta_id, linea, producto_id,
                                            unidad_codigo, factor_unidad, cantidad,
                                            cantidad_base, precio_unitario, importe)
                VALUES (:id, :v, 1, :p, 'PZA', 1, :cant, :cant, :precio, :total)
                """
            ),
            {
                "id": uuid.uuid4(),
                "v": venta_id,
                "p": producto,
                "cant": cantidad,
                "precio": (Decimal(total) / cantidad).quantize(Decimal("0.0001")),
                "total": total,
            },
        )
        return venta_id

    async def no_drop(a_quien, motivo, dia=None):
        folio[0] += 1
        await sesion.execute(
            text(
                """
                INSERT INTO no_drops (id, dispositivo_id, folio_consecutivo, cliente_id,
                                      vendedor_id, ruta_id, motivo_codigo, lat, lng,
                                      fecha_dispositivo, fecha_operativa)
                VALUES (:n, :d, :folio, :c, :u, :r, :m, 20.6736, -103.3440, :ahora, :dia)
                """
            ),
            {
                "n": uuid.uuid4(),
                "d": dispositivo,
                "folio": folio[0],
                "c": a_quien,
                "u": semilla["vendedor"],
                "r": semilla["ruta"],
                "m": motivo,
                "ahora": ahora,
                "dia": dia or hoy,
            },
        )

    # El pedido PARTIDO: dos remisiones al mismo cliente el mismo día.
    await venta(clientes["Doña Mary"], Decimal("300.00"))
    await venta(clientes["Doña Mary"], Decimal("200.00"))
    await venta(clientes["El Puente"], Decimal("1000.00"))
    await no_drop(clientes["La Esquina"], "CERRADO")
    await no_drop(clientes["Los Pinos"], "AGOTADO_EN_CAMION")
    await sesion.commit()

    return {
        "dispositivo": dispositivo,
        "clientes": clientes,
        "producto": producto,
        "dia": hoy,
        "vendedor": semilla["vendedor"],
        "venta": venta,
        "no_drop": no_drop,
    }


# ---------------------------------------------------------------------------
# El grano de fact_visitas, del que depende todo lo demás
# ---------------------------------------------------------------------------


async def test_una_visita_es_un_cliente_dia_no_un_documento(sesion, dia_de_ruta):
    """El pedido partido en dos remisiones produce UN renglón, con el total sumado.

    Es la definición de la que cuelgan el drop size y la efectividad. Si
    `fact_visitas` tuviera dos renglones para Doña Mary, el índice único del grano
    habría fallado al refrescar — y por eso ese índice no es decorativo.
    """
    await _refrescar(sesion)

    filas = await _consultar(
        sesion,
        "SELECT * FROM fact_visitas WHERE fecha = %(dia)s ORDER BY importe DESC",
        dia=dia_de_ruta["dia"],
    )
    assert len(filas) == 4, "cuatro clientes visitados, no cinco documentos"

    mary = next(f for f in filas if f["importe"] == Decimal("500.00"))
    assert mary["vendio"] is True
    assert mary["documentos_venta"] == 2, "dos remisiones, una visita"

    assert sum(1 for f in filas if f["vendio"]) == 2
    assert sum(f["importe"] for f in filas) == Decimal("1500.00")


async def test_el_motivo_del_no_drop_se_conserva_en_la_visita(sesion, dia_de_ruta):
    await _refrescar(sesion)
    filas = await _consultar(
        sesion,
        "SELECT motivos_no_drop FROM fact_visitas "
        " WHERE fecha = %(dia)s AND NOT vendio ORDER BY 1",
        dia=dia_de_ruta["dia"],
    )
    motivos = sorted(m for f in filas for m in f["motivos_no_drop"])
    assert motivos == ["AGOTADO_EN_CAMION", "CERRADO"]


async def test_una_visita_con_no_drop_y_venta_el_mismo_dia_cuenta_como_vendida(
    sesion, dia_de_ruta
):
    """Se volvió a pasar y a la segunda compró: el desenlace fue la venta.

    Pero el motivo del primer intento se conserva, porque la causa de que no
    compró a la primera sigue siendo información.
    """
    await dia_de_ruta["no_drop"](dia_de_ruta["clientes"]["El Puente"], "CERRADO")
    await sesion.commit()
    await _refrescar(sesion)

    fila = (
        await _consultar(
            sesion,
            "SELECT * FROM fact_visitas "
            " WHERE cliente_id = %(c)s AND fecha = %(dia)s",
            c=dia_de_ruta["clientes"]["El Puente"],
            dia=dia_de_ruta["dia"],
        )
    )[0]
    assert fila["vendio"] is True
    assert fila["documentos_no_drop"] == 1
    assert fila["motivos_no_drop"] == ["CERRADO"]


# ---------------------------------------------------------------------------
# Drop size — la métrica central, y la que más fácil sale mal
# ---------------------------------------------------------------------------


async def test_el_drop_size_divide_entre_visitas_que_vendieron(sesion, dia_de_ruta):
    """$1,500 en 2 visitas con venta = $750. Contando documentos daría $500.

    Y la efectividad sale 50 %, no 60 %. Las dos aserciones negativas están a
    propósito: son el número que daría la definición equivocada, y verlo escrito
    es lo que hace que esta prueba sirva de algo.
    """
    await _refrescar(sesion)
    fila = (
        await _consultar(
            sesion,
            sql_drop_size("dia"),
            desde=dia_de_ruta["dia"],
            hasta=dia_de_ruta["dia"],
        )
    )[0]

    assert fila["visitas"] == 4
    assert fila["visitas_con_venta"] == 2
    assert fila["importe"] == Decimal("1500.00")
    assert fila["drop_size"] == Decimal("750.00")
    assert fila["drop_size"] != Decimal("500.00"), "está contando documentos"
    assert fila["efectividad_pct"] == Decimal("50.0")
    assert fila["efectividad_pct"] != Decimal("60.0"), "está contando documentos"


async def test_el_importe_por_visita_es_otra_metrica_y_se_llama_distinto(
    sesion, dia_de_ruta
):
    """$1,500 / 4 visitas = $375. Es drop_size × efectividad, y no es el drop size.

    Las dos se devuelven con nombres distintos justamente para que nadie tenga
    que adivinar cuál está mirando.
    """
    await _refrescar(sesion)
    fila = (
        await _consultar(
            sesion, sql_drop_size("dia"),
            desde=dia_de_ruta["dia"], hasta=dia_de_ruta["dia"],
        )
    )[0]
    assert fila["importe_por_visita"] == Decimal("375.00")
    assert fila["drop_size"] == Decimal("750.00")


async def test_las_tres_agrupaciones_dan_el_mismo_total(sesion, dia_de_ruta):
    """Día, semana y mes son cortes distintos del mismo importe.

    Si no coincidieran, alguna agrupación estaría perdiendo o duplicando
    renglones — y el síntoma sería un reporte mensual que no cuadra con la suma
    de sus semanas, que es de los errores más difíciles de perseguir.
    """
    await _refrescar(sesion)
    totales = {}
    for agrupacion in AGRUPACIONES:
        filas = await _consultar(
            sesion, sql_drop_size(agrupacion),
            desde=dia_de_ruta["dia"] - timedelta(days=40),
            hasta=dia_de_ruta["dia"],
        )
        totales[agrupacion] = sum(f["importe"] for f in filas)
    assert len(set(totales.values())) == 1, totales


async def test_una_agrupacion_inventada_se_rechaza(sesion):
    """La agrupación entra al SQL por interpolación: sin lista blanca, es inyección.

    No se puede parametrizar un GROUP BY, así que la única defensa es validar
    contra los valores conocidos.
    """
    with pytest.raises(ValueError, match="agrupación inválida"):
        sql_drop_size("fecha; DROP TABLE ventas--")


# ---------------------------------------------------------------------------
# Productividad
# ---------------------------------------------------------------------------


async def test_la_productividad_promedia_sobre_dias_trabajados(sesion, dia_de_ruta):
    """Un vendedor de vacaciones no debe salir mal en el promedio diario.

    Dos días con visitas y un rango de un mes: el promedio es sobre 2, no sobre
    30. Si fuera sobre los días del rango, cualquier reporte mensual haría ver a
    todos los vendedores como si trabajaran un décimo de lo que trabajan.
    """
    ayer = dia_de_ruta["dia"] - timedelta(days=1)
    await dia_de_ruta["venta"](
        dia_de_ruta["clientes"]["La Esquina"], Decimal("500.00"), dia=ayer
    )
    await sesion.commit()
    await _refrescar(sesion)

    fila = (
        await _consultar(
            sesion, SQL_PRODUCTIVIDAD,
            desde=dia_de_ruta["dia"] - timedelta(days=30),
            hasta=dia_de_ruta["dia"],
        )
    )[0]
    assert fila["dias_trabajados"] == 2
    assert fila["visitas"] == 5           # 4 de hoy + 1 de ayer
    assert fila["ventas"] == 3
    assert fila["importe"] == Decimal("2000.00")
    # $2,000 entre 2 días trabajados. Entre 31 días daría $64.52.
    assert fila["importe_por_dia"] == Decimal("1000.00")
    assert fila["visitas_por_dia"] == Decimal("2.5")


async def test_la_productividad_trae_el_nombre_y_las_rutas_del_vendedor(
    sesion, dia_de_ruta
):
    await _refrescar(sesion)
    fila = (
        await _consultar(
            sesion, SQL_PRODUCTIVIDAD,
            desde=dia_de_ruta["dia"], hasta=dia_de_ruta["dia"],
        )
    )[0]
    assert fila["nombre"] == "Juan Pérez"
    assert fila["codigo"] == "VEND01"
    # Las rutas salen de `usuarios_rutas`, que es lo que decide qué datos viajan
    # al teléfono — no de `rutas.vendedor_id`.
    assert "R04" in fila["rutas"]


# ---------------------------------------------------------------------------
# Frecuencia de visita
# ---------------------------------------------------------------------------


async def test_la_frecuencia_usa_la_mediana_no_el_promedio(sesion, dia_de_ruta):
    """Visitas cada 7, 7 y 60 días: la mediana es 7, el promedio 24.7.

    El promedio describe un cliente que no existe. La mediana aguanta el hueco de
    las vacaciones, que es exactamente el caso para el que se eligió.
    """
    cliente = dia_de_ruta["clientes"]["La Esquina"]
    hoy = dia_de_ruta["dia"]
    for dias_atras in (74, 67, 60):
        await dia_de_ruta["venta"](cliente, Decimal("100.00"), dia=hoy - timedelta(days=dias_atras))
    await sesion.commit()
    await _refrescar(sesion)

    fila = (
        await _consultar(
            sesion, SQL_FRECUENCIA_VISITA,
            desde=hoy - timedelta(days=90), hasta=hoy,
        )
    )
    esquina = next(f for f in fila if f["cliente_id"] == cliente)
    # Intervalos: 7, 7 y 60 días (los tres de atrás más el no-drop de hoy).
    assert esquina["intervalos"] == 3
    assert esquina["mediana_dias"] == Decimal("7.0")
    assert esquina["maximo_dias"] == 60


# ---------------------------------------------------------------------------
# Clientes en riesgo — por qué un umbral fijo engaña en las dos direcciones
# ---------------------------------------------------------------------------


async def test_el_riesgo_se_mide_contra_la_cadencia_propia_del_cliente(
    sesion, dia_de_ruta
):
    """La prueba que justifica toda la complejidad de esta métrica.

    Dos clientes, los dos con 20 días sin comprar:

      · uno compraba cada 7 días  → 2.9 veces su cadencia → EN RIESGO
      · otro compraba cada 45     → 0.4 veces su cadencia → AL CORRIENTE

    Un umbral fijo de 30 días diría que los dos están bien, y el primero es el
    que de verdad se está yendo. Un umbral de 15 diría que los dos están mal, y
    el segundo está comprando como siempre.
    """
    hoy = dia_de_ruta["dia"]
    semanal = dia_de_ruta["clientes"]["La Esquina"]
    mensual = dia_de_ruta["clientes"]["Los Pinos"]

    # Semanal: compras cada 7 días, la última hace 20.
    for dias in (41, 34, 27, 20):
        await dia_de_ruta["venta"](semanal, Decimal("100.00"), dia=hoy - timedelta(days=dias))
    # Mensual-y-medio: compras cada 45 días, la última también hace 20.
    for dias in (110, 65, 20):
        await dia_de_ruta["venta"](mensual, Decimal("100.00"), dia=hoy - timedelta(days=dias))
    await sesion.commit()
    await _refrescar(sesion)

    filas = {
        f["cliente_id"]: f
        for f in await _consultar(
            sesion, SQL_CLIENTES_EN_RIESGO,
            factor_riesgo=FACTOR_RIESGO, factor_perdido=FACTOR_PERDIDO,
        )
    }

    a = filas[semanal]
    assert a["cadencia_dias"] == Decimal("7.0")
    assert a["dias_sin_comprar"] == 20
    assert a["situacion"] == "en_riesgo", "el que compraba cada semana se está yendo"

    b = filas[mensual]
    assert b["cadencia_dias"] == Decimal("45.0")
    assert b["dias_sin_comprar"] == 20
    assert b["situacion"] == "al_corriente", "éste compra como siempre"


async def test_un_cliente_nuevo_no_se_mezcla_con_los_que_se_van(sesion, dia_de_ruta):
    """Con menos de tres compras no hay cadencia de la que desviarse.

    Meterlo en «en riesgo» llenaría la lista de clientes recién dados de alta, y
    una lista larga de falsos positivos se deja de leer.
    """
    await _refrescar(sesion)
    filas = await _consultar(
        sesion, SQL_CLIENTES_EN_RIESGO,
        factor_riesgo=FACTOR_RIESGO, factor_perdido=FACTOR_PERDIDO,
    )
    # Doña Mary y El Puente tienen una sola compra cada uno.
    nuevos = [f for f in filas if f["situacion"] == "nuevo"]
    assert len(nuevos) == 2
    assert all(f["compras"] < 3 for f in nuevos)


async def test_al_triple_de_su_cadencia_el_cliente_ya_se_fue(sesion, dia_de_ruta):
    hoy = dia_de_ruta["dia"]
    cliente = dia_de_ruta["clientes"]["La Esquina"]
    # Cadencia de 7 días, última compra hace 30: 4.3 veces su cadencia.
    for dias in (51, 44, 37, 30):
        await dia_de_ruta["venta"](cliente, Decimal("100.00"), dia=hoy - timedelta(days=dias))
    await sesion.commit()
    await _refrescar(sesion)

    filas = {
        f["cliente_id"]: f
        for f in await _consultar(
            sesion, SQL_CLIENTES_EN_RIESGO,
            factor_riesgo=FACTOR_RIESGO, factor_perdido=FACTOR_PERDIDO,
        )
    }
    assert filas[cliente]["situacion"] == "perdido"


# ---------------------------------------------------------------------------
# Rotación — y la existencia reconstruida del libro mayor
# ---------------------------------------------------------------------------


async def test_la_rotacion_reconstruye_la_existencia_del_libro_mayor(
    sesion, semilla, dia_de_ruta
):
    """No hay fotos diarias del inventario, y aun así la rotación es calculable.

    `existencias` es una caché del valor ACTUAL: no sirve para el pasado. Lo que
    sí hay es `movimientos_inventario`, append-only por disparador, así que la
    existencia de cualquier fecha se reconstruye sumando. La decisión de que el
    libro fuera append-only se tomó por auditoría, y resulta que paga dos veces.
    """
    hoy = dia_de_ruta["dia"]
    # Una carga de 240 piezas al camión, hace diez días.
    await sesion.execute(
        text(
            """
            INSERT INTO movimientos_inventario
              (tipo, almacen_origen_id, almacen_destino_id, producto_id, cantidad,
               documento_tipo, documento_id, fecha_dispositivo, fecha_servidor)
            VALUES ('carga', :bodega, :camion, :p, 240, 'carga', :doc, :cuando, :cuando)
            """
        ),
        {
            "bodega": semilla["bodega"],
            "camion": semilla["camion"],
            "p": dia_de_ruta["producto"],
            "doc": uuid.uuid4(),
            "cuando": datetime.now(UTC) - timedelta(days=10),
        },
    )
    await sesion.commit()
    await _refrescar(sesion)

    filas = await _consultar(
        sesion, SQL_ROTACION, desde=hoy - timedelta(days=9), hasta=hoy
    )
    atun = next(f for f in filas if f["sku"] == "ATUN-140")

    # 30 piezas vendidas (3 ventas × 10) sobre una existencia promedio de 240.
    assert atun["unidades_vendidas"] == Decimal("30.000")
    assert atun["existencia_promedio"] == Decimal("240.000")
    assert atun["rotacion"] == Decimal("0.13")
    assert atun["dias_con_existencia"] == 10


async def test_la_existencia_reconstruida_no_empieza_en_cero_a_media_quincena(
    sesion, semilla, dia_de_ruta
):
    """El error que daría existencias negativas en cualquier reporte parcial.

    La carga entró hace diez días. Si el acumulado del libro mayor empezara en el
    primer día del rango consultado, un reporte de los últimos TRES días vería
    solo las ventas —sin la carga que las precedió— y la existencia saldría
    negativa. Por eso el acumulado es `m.fecha <= t.fecha` sobre todo el libro, y
    no una función de ventana limitada al periodo.
    """
    hoy = dia_de_ruta["dia"]
    await sesion.execute(
        text(
            """
            INSERT INTO movimientos_inventario
              (tipo, almacen_origen_id, almacen_destino_id, producto_id, cantidad,
               documento_tipo, documento_id, fecha_dispositivo, fecha_servidor)
            VALUES ('carga', :bodega, :camion, :p, 240, 'carga', :doc, :cuando, :cuando)
            """
        ),
        {
            "bodega": semilla["bodega"],
            "camion": semilla["camion"],
            "p": dia_de_ruta["producto"],
            "doc": uuid.uuid4(),
            "cuando": datetime.now(UTC) - timedelta(days=10),
        },
    )
    await sesion.commit()
    await _refrescar(sesion)

    filas = await _consultar(
        sesion, SQL_ROTACION, desde=hoy - timedelta(days=2), hasta=hoy
    )
    atun = next(f for f in filas if f["sku"] == "ATUN-140")
    assert atun["existencia_promedio"] > 0, "el acumulado arrancó en cero"
    assert atun["existencia_promedio"] == Decimal("240.000")


async def test_el_producto_sin_ventas_aparece_con_cero_no_desaparece(
    sesion, dia_de_ruta
):
    """Un producto que no se vendió es información, y es la que se busca.

    Si el LEFT JOIN fuera INNER, el reporte de rotación mostraría solo lo que se
    vende — justo al revés de para qué se consulta.
    """
    otro = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base) "
            "VALUES (:p, 'GALLETA-100', 'Galletas 100 g', 'PZA')"
        ),
        {"p": otro},
    )
    await sesion.commit()
    await _refrescar(sesion)

    filas = await _consultar(
        sesion, SQL_ROTACION,
        desde=dia_de_ruta["dia"], hasta=dia_de_ruta["dia"],
    )
    galleta = next(f for f in filas if f["sku"] == "GALLETA-100")
    assert galleta["unidades_vendidas"] == 0
    assert galleta["rotacion"] is None, "sin existencia no hay rotación que calcular"


async def test_la_cantidad_va_en_unidad_base_y_tambien_en_cajas(sesion, dia_de_ruta):
    """30 piezas de un atún de 24 por caja son 1.25 cajas.

    Las dos cifras: la unidad base es la que se puede sumar, y la caja es como lo
    piensa quien compra.
    """
    await _refrescar(sesion)
    filas = await _consultar(
        sesion, SQL_ROTACION,
        desde=dia_de_ruta["dia"], hasta=dia_de_ruta["dia"],
    )
    atun = next(f for f in filas if f["sku"] == "ATUN-140")
    assert atun["unidades_vendidas"] == Decimal("30.000")
    assert atun["en_unidad_mayor"] == Decimal("1.25")
    assert atun["unidad_mayor"] == "CAJA"


# ---------------------------------------------------------------------------
# El job de refresco
# ---------------------------------------------------------------------------


async def test_refrescar_registra_cuando_y_con_que_mundo(sesion, dia_de_ruta):
    """El registro es lo que hace honesto al laboratorio (§0.3).

    Sin él, una cifra de ayer se muestra igual que una de ahora, y nadie puede
    saber cuál está leyendo.
    """
    await _refrescar(sesion)
    filas = await _consultar(sesion, "SELECT * FROM analitica_refrescos ORDER BY vista")
    assert {f["vista"] for f in filas} == set(VISTAS)
    for f in filas:
        assert f["refrescado_en"] is not None
        assert f["duracion_ms"] >= 0
        assert f["renglones"] >= 0
        # La foto del mundo: cuántos equipos no habían sincronizado.
        assert f["equipos_sin_sincronizar"] is not None
        assert f["ops_en_cuarentena"] is not None


async def test_refrescar_dos_veces_no_duplica_nada(sesion, dia_de_ruta):
    """El job se reintenta: tiene que ser idempotente como todo lo demás."""
    await _refrescar(sesion)
    await _refrescar(sesion)

    visitas = await _consultar(
        sesion, "SELECT count(*) AS n FROM fact_visitas WHERE fecha = %(dia)s",
        dia=dia_de_ruta["dia"],
    )
    assert visitas[0]["n"] == 4
    registros = await _consultar(
        sesion, "SELECT count(*) AS n FROM analitica_refrescos"
    )
    assert registros[0]["n"] == len(VISTAS)


async def test_el_refresh_es_concurrente_y_no_bloquea_al_laboratorio(
    sesion, dia_de_ruta
):
    """`CONCURRENTLY` o el laboratorio se congela cada vez que corre el job.

    Sin él, el refresh toma un ACCESS EXCLUSIVE y cualquiera que esté consultando
    se queda esperando. Esta prueba fija que el camino normal es el concurrente.
    """
    await _refrescar(sesion)   # deja todas pobladas
    resultado = await refrescar_una(sesion, "fact_visitas")
    await sesion.commit()
    assert resultado["concurrente"] is True


async def test_una_vista_sin_poblar_se_refresca_sin_concurrently(sesion, dia_de_ruta):
    """PostgreSQL rechaza CONCURRENTLY sobre una vista nunca poblada.

    La migración las crea CON DATOS, así que esto solo pasa si alguien las recreó
    a mano. El job tiene que funcionar igual — fallar ahí dejaría el laboratorio
    congelado y el mensaje de error no diría por qué.
    """
    await sesion.execute(text("REFRESH MATERIALIZED VIEW fact_visitas WITH NO DATA"))
    await sesion.commit()

    resultado = await refrescar_una(sesion, "fact_visitas")
    await sesion.commit()
    assert resultado["concurrente"] is False
    assert resultado["renglones"] == 4


async def test_una_vista_inventada_no_llega_al_sql(sesion):
    """El nombre de la vista entra por interpolación: sin lista blanca, el payload
    de un job sería inyección de SQL."""
    with pytest.raises(ValueError, match="vista desconocida"):
        await refrescar_una(sesion, "fact_visitas; DROP TABLE ventas--")


async def test_el_refresh_sigue_aunque_una_vista_falle(sesion, dia_de_ruta):
    """Media actualización sirve más que ninguna.

    Si se detuviera en la primera falla, un problema en una vista dejaría todo el
    laboratorio en la foto de ayer. Lo que quedó viejo lo dice su propio renglón
    en `analitica_refrescos`.
    """
    with pytest.raises(RuntimeError, match="refresh incompleto"):
        await refrescar_todo(sesion, ("dim_cliente", "fact_inventada", "fact_visitas"))

    # Las dos válidas se refrescaron de todos modos.
    filas = await _consultar(sesion, "SELECT vista FROM analitica_refrescos ORDER BY vista")
    vistas = {f["vista"] for f in filas}
    assert "dim_cliente" in vistas
    assert "fact_visitas" in vistas


# ---------------------------------------------------------------------------
# La advertencia de frescura
# ---------------------------------------------------------------------------
