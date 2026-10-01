"""El reporte de efectividad: lo que la Fase 6 capturó, leído.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
El no-drop existe porque sin él un día de 20 visitas con 12 ventas se ve igual que
uno de 12 visitas con 12 ventas. Capturar el dato y no leerlo deja el problema
intacto, y peor: el vendedor dedica tiempo a registrar visitas perdidas, nadie las
mira, y en cuanto eso se nota deja de registrarlas.

Cuatro cosas que se rompen, y todas producen un número que *parece* bien:

1. **Contar documentos en vez de clientes visitados.** Dos remisiones al mismo
   cliente el mismo día son una visita; contarlas como dos premia al vendedor que
   parte un pedido en dos.
2. **Perder al vendedor que no tuvo no-drops.** Un `JOIN` normal entre ventas y
   no-drops lo desaparecería del reporte **por haber tenido un día perfecto**.
3. **Mezclar las categorías.** "Cerrado" y "no traigo lo que pidió" son dos
   problemas distintos: el primero es el mundo y el segundo es la bodega. Sin la
   categoría, ocho visitas perdidas por falta de carga se ven como "no compró".
4. **Contar las devoluciones como pérdida.** Una devolución de cliente es mercancía
   vendible que regresa; sumarla a las mermas inventaría una pérdida que no existe.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR, solo_texto, texto_plano

pytestmark = pytest.mark.asyncio


async def _entrar(cliente, codigo: str = "ADMIN01") -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": codigo, "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


@pytest.fixture
async def semana(sesion, semilla) -> dict:
    """Un vendedor con 4 visitas: 2 ventas (una partida en dos remisiones) y 2 no-drops.

    Las cifras están elegidas para que cada error del reporte dé un número
    distinto:

      · contando clientes visitados → 4 visitas, 2 ventas, 50%
      · contando documentos         → 5 visitas, 3 ventas, 60%

    Los dos no-drops son de categorías distintas —uno del cliente y uno nuestro—
    para que la línea de «lo podemos arreglar nosotros» no pueda coincidir con el
    total por accidente.
    """
    dispositivo = uuid.uuid4()
    hoy = date.today()
    ahora = datetime.now(UTC)
    clientes = {}

    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'POCO M5s de Juan', 'activo', now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    for i, nombre in enumerate(
        ["Doña Mary", "El Puente", "La Esquina", "Los Pinos"], start=1
    ):
        identificador = uuid.uuid4()
        await sesion.execute(
            text(
                "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, "
                "                      creado_en, actualizado_en) "
                "VALUES (:c, :cod, :n, :r, now(), now())"
            ),
            {
                "c": identificador,
                "cod": f"C{i:05d}",
                "n": nombre,
                "r": semilla["ruta"],
            },
        )
        clientes[nombre] = identificador

    folio = [100]

    async def venta(a_quien, dia=None):
        folio[0] += 1
        await sesion.execute(
            text(
                """
                INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                    cliente_id, vendedor_id, almacen_id, tipo,
                                    subtotal, total, fecha_dispositivo, fecha_operativa)
                VALUES (:v, :d, :folio, :folio_local, :c, :u, :a, 'contado',
                        500, 500, :ahora, :dia)
                """
            ),
            {
                "v": uuid.uuid4(),
                "d": dispositivo,
                "folio": folio[0],
                "folio_local": f"VEND01-{folio[0]:06d}",
                "c": a_quien,
                "u": semilla["vendedor"],
                "a": semilla["camion"],
                "ahora": ahora,
                "dia": dia or hoy,
            },
        )

    async def no_drop(a_quien, motivo, dia=None):
        folio[0] += 1
        await sesion.execute(
            text(
                """
                INSERT INTO no_drops (id, dispositivo_id, folio_consecutivo, cliente_id,
                                      vendedor_id, ruta_id, motivo_codigo, lat, lng,
                                      fecha_dispositivo, fecha_operativa)
                VALUES (:n, :d, :folio, :c, :u, :r, :m, 20.6736, -103.3440,
                        :ahora, :dia)
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

    # Doña Mary compró, y el pedido se partió en DOS remisiones: una visita.
    await venta(clientes["Doña Mary"])
    await venta(clientes["Doña Mary"])
    await venta(clientes["El Puente"])
    # Cerrado: del cliente, nada que podamos hacer.
    await no_drop(clientes["La Esquina"], "CERRADO")
    # No traíamos lo que pidió: es nuestro, y se arregla en la bodega mañana.
    await no_drop(clientes["Los Pinos"], "AGOTADO_EN_CAMION")
    await sesion.commit()

    return {
        "dispositivo": dispositivo,
        "clientes": clientes,
        "dia": hoy,
        "vendedor": semilla["vendedor"],
    }


async def _merma(sesion, semilla, dispositivo, motivo, unidades, *, tipo="merma",
                 cliente_id=None, dia=None, producto=None):
    """Una merma con un renglón, como la escribe el manejador del push."""
    merma_id = uuid.uuid4()
    if producto is None:
        producto = uuid.uuid4()
        await sesion.execute(
            text(
                "INSERT INTO productos (id, sku, nombre, unidad_base) "
                "VALUES (:p, :sku, 'Refresco de cola 600 ml', 'PZA')"
            ),
            {"p": producto, "sku": f"REF-{str(producto)[:6]}"},
        )
    await sesion.execute(
        text(
            """
            INSERT INTO mermas (id, dispositivo_id, folio_consecutivo, folio_local,
                                tipo, almacen_id, vendedor_id, cliente_id,
                                motivo_codigo, fecha_dispositivo, fecha_operativa)
            VALUES (:id, :d, :folio, :folio_local, :tipo, :a, :u, :c, :m,
                    now(), :dia)
            """
        ),
        {
            "id": merma_id,
            "d": dispositivo,
            "folio": abs(hash(str(merma_id))) % 90000 + 1,
            "folio_local": f"VEND01-M{str(merma_id)[:5]}",
            "tipo": tipo,
            "a": semilla["camion"],
            "u": semilla["vendedor"],
            "c": cliente_id,
            "m": motivo,
            "dia": dia or date.today(),
        },
    )
    await sesion.execute(
        text(
            "INSERT INTO merma_detalle (id, merma_id, producto_id, cantidad_base) "
            "VALUES (:id, :m, :p, :cant)"
        ),
        {"id": uuid.uuid4(), "m": merma_id, "p": producto, "cant": unidades},
    )
    await sesion.commit()
    return producto


# ---------------------------------------------------------------------------
# La aritmética de la visita
# ---------------------------------------------------------------------------


async def test_una_visita_es_un_cliente_visitado_no_un_documento(cliente, semana):
    """Dos remisiones al mismo cliente el mismo día son UNA visita.

    Contarlas como dos premiaría al vendedor que parte un pedido en dos, y la
    efectividad de la empresa subiría sin que nadie visitara a nadie más.
    """
    await _entrar(cliente)
    plano = solo_texto(await cliente.get("/panel/efectividad"))

    # 4 clientes visitados, 2 compraron: 50%. Por documentos daría 5 y 60%.
    assert "4 visitas" in plano
    assert "50.0%" in plano
    assert "60.0%" not in plano


async def test_el_vendedor_sin_no_drops_no_desaparece(cliente, sesion, semana, semilla):
    """Un día perfecto no puede borrar al vendedor del reporte.

    Es lo que haría un `JOIN` normal entre ventas y no-drops, y el síntoma sería
    absurdo: el mejor vendedor del día ausente de la tabla.
    """
    await sesion.execute(text("DELETE FROM no_drops"))
    await sesion.commit()

    await _entrar(cliente)
    plano = solo_texto(await cliente.get("/panel/efectividad"))
    assert "Juan Pérez" in plano
    assert "100.0%" in plano


async def test_el_vendedor_que_no_vendio_nada_tambien_aparece(
    cliente, sesion, semana
):
    """Y el caso contrario, que es el que de verdad hay que ver."""
    await sesion.execute(text("DELETE FROM ventas"))
    await sesion.commit()

    await _entrar(cliente)
    plano = solo_texto(await cliente.get("/panel/efectividad"))
    assert "Juan Pérez" in plano
    assert "0.0%" in plano


async def test_un_periodo_sin_visitas_lo_dice(cliente, semana):
    await _entrar(cliente)
    hace_un_mes = (semana["dia"] - timedelta(days=40)).isoformat()
    hace_mes_y_medio = (semana["dia"] - timedelta(days=50)).isoformat()
    plano = solo_texto(
        await cliente.get(
            f"/panel/efectividad?desde={hace_mes_y_medio}&hasta={hace_un_mes}"
        )
    )
    assert "No hay visitas registradas" in plano


async def test_el_rango_invertido_se_endereza(cliente, semana):
    """Quien teclea las fechas al revés quiere ver ese periodo, no un error."""
    await _entrar(cliente)
    hoy = semana["dia"].isoformat()
    hace_tres = (semana["dia"] - timedelta(days=3)).isoformat()
    plano = solo_texto(
        await cliente.get(f"/panel/efectividad?desde={hoy}&hasta={hace_tres}")
    )
    assert "4 visitas" in plano


async def test_una_fecha_basura_no_tumba_la_pantalla(cliente, semana):
    """Un parámetro manipulado o un navegador raro no pueden dar un 500."""
    await _entrar(cliente)
    r = await cliente.get("/panel/efectividad?desde=ayer&hasta=pasado")
    assert r.status_code == 200
    assert "4 visitas" in solo_texto(r)


async def test_el_periodo_por_omision_son_siete_dias(cliente, sesion, semana, semilla):
    """Una efectividad del 60% sobre 20 visitas es ruido; sobre 140 es un dato.

    Por eso no abre en "hoy". Esta prueba lo fija comprobando que una visita de hace
    tres días entra en la vista inicial, y una de hace quince no.
    """
    dentro = semana["dia"] - timedelta(days=3)
    fuera = semana["dia"] - timedelta(days=15)
    for dia, codigo in ((dentro, "C00010"), (fuera, "C00011")):
        otro = uuid.uuid4()
        await sesion.execute(
            text(
                "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, "
                "                      creado_en, actualizado_en) "
                "VALUES (:c, :cod, :n, :r, now(), now())"
            ),
            {"c": otro, "cod": codigo, "n": f"Tienda {codigo}", "r": semilla["ruta"]},
        )
        await sesion.execute(
            text(
                """
                INSERT INTO no_drops (id, dispositivo_id, folio_consecutivo, cliente_id,
                                      vendedor_id, motivo_codigo, lat, lng,
                                      fecha_dispositivo, fecha_operativa)
                VALUES (:n, :d, :folio, :c, :u, 'CERRADO', 20.0, -103.0, now(), :dia)
                """
            ),
            {
                "n": uuid.uuid4(),
                "d": semana["dispositivo"],
                "folio": 7000 + dia.day,
                "c": otro,
                "u": semana["vendedor"],
                "dia": dia,
            },
        )
    await sesion.commit()

    await _entrar(cliente)
    plano = solo_texto(await cliente.get("/panel/efectividad"))
    # 4 de hoy + 1 de hace tres días. La de hace quince queda fuera.
    assert "5 visitas" in plano


# ---------------------------------------------------------------------------
# La categoría, que es la razón de ser del reporte
# ---------------------------------------------------------------------------


async def test_separa_lo_que_podemos_arreglar_de_lo_que_no(cliente, semana):
    """«Cerrado» es el mundo; «no traigo lo que pidió» es la bodega.

    Sin esta línea, ocho visitas perdidas por falta de carga se ven como "no
    compró" y nadie cambia nada en la bodega.
    """
    await _entrar(cliente)
    plano = solo_texto(await cliente.get("/panel/efectividad"))

    assert "visitas perdidas que son nuestras" in plano
    # Una de las dos perdidas es nuestra: 50% de las perdidas.
    assert "50.0% de las perdidas" in plano
    assert "Lo podemos arreglar nosotros" in plano


async def test_los_motivos_se_muestran_con_su_nombre_y_su_cuenta(cliente, semana):
    await _entrar(cliente)
    plano = solo_texto(await cliente.get("/panel/efectividad"))
    assert "Cerrado" in plano
    assert "No traigo lo que pidió" in plano
    assert "Del producto o la carga" in plano
    assert "Del cliente" in plano


async def test_el_motivo_mas_frecuente_va_primero(cliente, sesion, semana, semilla):
    """En la calle lo que importa es el patrón, no el inventario de motivos."""
    for i in range(3):
        otro = uuid.uuid4()
        await sesion.execute(
            text(
                "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, "
                "                      creado_en, actualizado_en) "
                "VALUES (:c, :cod, :n, :r, now(), now())"
            ),
            {
                "c": otro,
                "cod": f"C0002{i}",
                "n": f"Tienda extra {i}",
                "r": semilla["ruta"],
            },
        )
        await sesion.execute(
            text(
                """
                INSERT INTO no_drops (id, dispositivo_id, folio_consecutivo, cliente_id,
                                      vendedor_id, motivo_codigo, lat, lng,
                                      fecha_dispositivo, fecha_operativa)
                VALUES (:n, :d, :folio, :c, :u, 'AGOTADO_EN_CAMION', 20.0, -103.0,
                        now(), :dia)
                """
            ),
            {
                "n": uuid.uuid4(),
                "d": semana["dispositivo"],
                "folio": 8000 + i,
                "c": otro,
                "u": semana["vendedor"],
                "dia": semana["dia"],
            },
        )
    await sesion.commit()

    await _entrar(cliente)
    plano = solo_texto(await cliente.get("/panel/efectividad"))
    assert plano.index("No traigo lo que pidió") < plano.index("Cerrado")


async def test_el_filtro_de_ruta_acota_el_reporte(cliente, semana):
    await _entrar(cliente)
    con_ruta = solo_texto(await cliente.get("/panel/efectividad?ruta=R04"))
    assert "4 visitas" in con_ruta

    otra = solo_texto(await cliente.get("/panel/efectividad?ruta=R99"))
    assert "No hay visitas registradas" in otra


async def test_cuenta_visitas_y_clientes_distintos_por_motivo(
    cliente, sesion, semana
):
    """Tres «cerrado» de un mismo cliente no es lo mismo que de tres clientes.

    El primero es un cliente con un horario raro —se arregla cambiando la hora de
    la visita— y el segundo es un patrón de la zona. Con una sola columna los dos
    casos se ven idénticos.
    """
    # La Esquina vuelve a estar cerrado otro día: 2 visitas, 1 cliente.
    await sesion.execute(
        text(
            """
            INSERT INTO no_drops (id, dispositivo_id, folio_consecutivo, cliente_id,
                                  vendedor_id, motivo_codigo, lat, lng,
                                  fecha_dispositivo, fecha_operativa)
            VALUES (:n, :d, 9100, :c, :u, 'CERRADO', 20.0, -103.0, now(), :dia)
            """
        ),
        {
            "n": uuid.uuid4(),
            "d": semana["dispositivo"],
            "c": semana["clientes"]["La Esquina"],
            "u": semana["vendedor"],
            "dia": semana["dia"] - timedelta(days=2),
        },
    )
    await sesion.commit()

    await _entrar(cliente)
    plano = solo_texto(await cliente.get("/panel/efectividad"))
    # El renglón: motivo, categoría, visitas, clientes.
    assert "Cerrado Del cliente 2 1" in plano


# ---------------------------------------------------------------------------
# Las mermas, que tenían el mismo problema de no leerse
# ---------------------------------------------------------------------------


async def test_las_mermas_se_suman_por_motivo(cliente, sesion, semana, semilla):
    """Dos motivos, dos renglones, y el total de arriba es la suma de los dos."""
    await _merma(sesion, semilla, semana["dispositivo"], "ROTO", Decimal("24.000"))
    await _merma(sesion, semilla, semana["dispositivo"], "CADUCADO", Decimal("6.000"))

    await _entrar(cliente)
    plano = solo_texto(await cliente.get("/panel/efectividad"))
    assert "De 30 unidades mermadas" in plano
    # Las unidades van en entero, no en "24.000": lo que se lee es cajas y piezas,
    # no milésimos. `Decimal.normalize()` daría aquí "2.4E+1".
    assert "Empaque roto Sí, al vendedor 24" in plano
    assert "Producto caducado No, la absorbe la empresa 6" in plano


async def test_dice_cuanto_sale_de_la_bolsa_del_vendedor(
    cliente, sesion, semana, semilla
):
    """El dato que importa no es cuánto se perdió, sino cuánto se le está cobrando.

    'ROTO' trae `afecta_vendedor = true` y 'CADUCADO' no. Sumarlos sin distinguir
    escondería lo único de esta tabla que alguien va a discutir.
    """
    await _merma(sesion, semilla, semana["dispositivo"], "ROTO", Decimal("24.000"))
    await _merma(sesion, semilla, semana["dispositivo"], "CADUCADO", Decimal("100.000"))

    await _entrar(cliente)
    plano = solo_texto(await cliente.get("/panel/efectividad"))
    # 124 mermadas en total, y solo 24 salen de la bolsa de alguien. Un total sin
    # esa separación haría que se discutiera el número equivocado.
    assert "De 124 unidades mermadas, 24 salen de la bolsa" in plano
    assert "Sí, al vendedor" in plano
    assert "No, la absorbe la empresa" in plano


async def test_una_devolucion_de_cliente_no_cuenta_como_perdida(
    cliente, sesion, semana, semilla
):
    """Es mercancía vendible que regresa, no pérdida.

    Sumarla inventaría una merma que no existe, y además la cargaría contra el
    motivo 'DEVOLUCION_CLIENTE' como si algo se hubiera roto.
    """
    await _merma(
        sesion,
        semilla,
        semana["dispositivo"],
        "DEVOLUCION_CLIENTE",
        Decimal("48.000"),
        tipo="devolucion_cliente",
        cliente_id=semana["clientes"]["Doña Mary"],
    )

    await _entrar(cliente)
    plano = solo_texto(await cliente.get("/panel/efectividad"))
    assert "No se registró ninguna merma" in plano
    assert "Devolución del cliente" not in plano


async def test_la_pantalla_explica_donde_se_ven_las_devoluciones(
    cliente, sesion, semana, semilla
):
    """Decir que no están aquí no basta: hay que decir dónde sí.

    Si no, la ausencia parece un hueco del reporte y alguien va a pedir que se
    sumen, que es exactamente lo que no debe pasar.
    """
    await _merma(sesion, semilla, semana["dispositivo"], "ROTO", Decimal("12.000"))

    await _entrar(cliente)
    plano = solo_texto(await cliente.get("/panel/efectividad"))
    assert "no son pérdida" in plano
    assert "Se ven en la liquidación" in plano


async def test_una_merma_cancelada_no_cuenta(cliente, sesion, semana, semilla):
    await _merma(sesion, semilla, semana["dispositivo"], "ROTO", Decimal("24.000"))
    await sesion.execute(text("UPDATE mermas SET estado = 'cancelada'"))
    await sesion.commit()

    await _entrar(cliente)
    plano = solo_texto(await cliente.get("/panel/efectividad"))
    assert "No se registró ninguna merma" in plano


# ---------------------------------------------------------------------------
# Permisos y navegación
# ---------------------------------------------------------------------------


async def test_efectividad_aparece_en_la_navegacion(cliente, semana):
    """Una pantalla que no está en la navegación no existe para quien usa el panel."""
    await _entrar(cliente)
    tablero = await cliente.get("/panel")
    # La etiqueta que se ve, y el enlace que la lleva. Afirmar solo una de las dos
    # dejaría pasar un menú con el texto correcto apuntando a otra parte.
    assert "Efectividad" in solo_texto(tablero)
    assert "/panel/efectividad" in texto_plano(tablero)


async def test_sin_sesion_manda_a_entrar(cliente, semana):
    r = await cliente.get("/panel/efectividad", follow_redirects=False)
    assert r.status_code == 303
    assert "/panel/entrar" in r.headers["location"]


async def test_sin_el_permiso_de_ver_todas_las_ventas_se_niega(
    cliente, sesion, semilla, semana
):
    """Ver la efectividad de todos los vendedores es información de oficina."""
    from app.core.seguridad import hashear_password

    await sesion.execute(
        text(
            "INSERT INTO usuarios(id, sucursal_id, codigo, nombre, password_hash, "
            "rol_codigo, creado_en, actualizado_en) "
            "VALUES (:id, :s, 'GER02', 'Gerente', :h, 'gerente', now(), now())"
        ),
        {
            "id": uuid.uuid4(),
            "s": semilla["sucursal"],
            "h": hashear_password(PASSWORD_VENDEDOR),
        },
    )
    # `roles_permisos` es dato de referencia y el TRUNCATE entre pruebas no lo toca,
    # así que se restituye sin falta en el `finally`.
    await sesion.execute(
        text(
            "DELETE FROM roles_permisos "
            " WHERE rol_codigo = 'gerente' AND permiso_codigo = 'ventas.ver_todas'"
        )
    )
    await sesion.commit()
    try:
        await _entrar(cliente, "GER02")
        r = await cliente.get("/panel/efectividad", follow_redirects=False)
        assert r.status_code == 403
        assert "ventas.ver_todas" in r.text
    finally:
        await sesion.execute(
            text(
                "INSERT INTO roles_permisos (rol_codigo, permiso_codigo) "
                "VALUES ('gerente', 'ventas.ver_todas') ON CONFLICT DO NOTHING"
            )
        )
        await sesion.commit()
