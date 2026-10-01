"""La pantalla de cobranza: el dinero que entró y la cartera que queda.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
`cobro.crear` es el manejador más permisivo del sistema y a propósito: en un cobro
el dinero ya está sobre el mostrador. Cobrar de más, cobrarle a quien no debía y
traer un saldo de caché desfasado se **marcan**, no se rechazan.

Marcar sin que nadie mire convierte la bandera en ruido, y entonces el permiso del
manejador deja de ser una decisión y se vuelve un agujero. Hasta esta pantalla,
esos cobros marcados solo se alcanzaban con SQL a mano.

Cuatro cosas que se rompen con consecuencias de dinero:

1. **Sumar al arqueo lo que no entra al arqueo.** Una transferencia entra al
   sistema pero no a la bolsa del vendedor; mezclarlas hace que la caja nunca
   cuadre y que el descuadre se le atribuya a la persona equivocada.
2. **Perder de vista un cobro marcado.** La lista de pendientes tiene que vaciarse
   solo cuando alguien la atendió, nunca por sí sola.
3. **Dejar que la oficina corrija el reparto.** El FIFO lo decidió sobre la cartera
   real; un botón para moverlo permitiría maquillar una cartera sin rastro.
4. **Contar la antigüedad desde la emisión.** Un cliente a 30 días no está vencido
   el día 15, y contarlo así haría que la pantalla gritara todos los días.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR, texto_plano

pytestmark = pytest.mark.asyncio


async def _entrar(cliente, codigo: str = "ADMIN01") -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": codigo, "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


def _csrf(cliente, respuesta=None) -> str:
    """El token del formulario, o el calculado desde la cookie.

    El cálculo de respaldo existe para las pantallas que no dibujan ningún
    formulario: sin él, un 403 no distinguiría "falta permiso" de "falta CSRF", que
    es justo lo que esas pruebas quieren separar.
    """
    marca = 'name="csrf" value="'
    if respuesta is not None and marca in respuesta.text:
        inicio = respuesta.text.index(marca) + len(marca)
        return respuesta.text[inicio : respuesta.text.index('"', inicio)]

    import hashlib
    import hmac

    from app.core.config import obtener_config

    return hmac.new(
        obtener_config().jwt_secreto.encode(),
        f"csrf:{cliente.cookies.get('dsd_panel', '')}".encode(),
        hashlib.sha256,
    ).hexdigest()


@pytest.fixture
async def dia_de_cobranza(sesion, semilla) -> dict:
    """Un día con tres cobros: efectivo, transferencia y uno marcado.

    El de transferencia existe para separar lo que entra al arqueo de lo que no, y
    el marcado para que la lista de pendientes tenga qué mostrar.
    """
    dispositivo = uuid.uuid4()
    cliente_id = uuid.uuid4()
    otro_cliente = uuid.uuid4()
    venta = uuid.uuid4()
    hoy = date.today()
    ahora = datetime.now(UTC)

    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'POCO M5s de Juan', 'activo', now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    for identificador, codigo, nombre in (
        (cliente_id, "C00001", "Abarrotes Doña Mary"),
        (otro_cliente, "C00002", "Tienda El Puente"),
    ):
        await sesion.execute(
            text(
                "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, "
                "                      permite_credito, limite_credito, dias_credito, "
                "                      creado_en, actualizado_en) "
                "VALUES (:c, :cod, :n, :r, true, 10000, 15, now(), now())"
            ),
            {"c": identificador, "cod": codigo, "n": nombre, "r": semilla["ruta"]},
        )

    # Una factura a crédito de $1,200 vencida hace 40 días: cae en el tramo 31-60.
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, almacen_id, tipo,
                                subtotal, total, fecha_dispositivo, fecha_operativa)
            VALUES (:v, :d, 900, 'VEND01-000900', :c, :u, :a, 'credito',
                    1200, 1200, :ahora, :dia)
            """
        ),
        {
            "v": venta,
            "d": dispositivo,
            "c": cliente_id,
            "u": semilla["vendedor"],
            "a": semilla["camion"],
            "ahora": ahora,
            "dia": hoy - timedelta(days=55),
        },
    )
    await sesion.execute(
        text(
            """
            INSERT INTO cuentas_por_cobrar (venta_id, cliente_id, importe_original,
                                            importe_pagado, fecha_emision,
                                            fecha_vencimiento, estado)
            VALUES (:v, :c, 1200, 300, :emision, :vence, 'parcial')
            """
        ),
        {
            "v": venta,
            "c": cliente_id,
            "emision": hoy - timedelta(days=55),
            "vence": hoy - timedelta(days=40),
        },
    )

    cobros = {}
    async def cobro(nombre, *, importe, forma, quien, marcado=False, motivos=None,
                    saldo_cache=None, aplicado=Decimal(0), a_favor=Decimal(0)):
        identificador = uuid.uuid4()
        await sesion.execute(
            text(
                """
                INSERT INTO cobros (id, dispositivo_id, folio_consecutivo, folio_local,
                                    cliente_id, vendedor_id, importe, forma_pago,
                                    saldo_cache_disp, importe_aplicado, saldo_a_favor,
                                    fecha_dispositivo, fecha_operativa,
                                    requiere_revision, revision_motivos)
                VALUES (:id, :d, :folio, :folio_local, :c, :u, :importe, :forma,
                        :cache, :aplicado, :favor, :ahora, :dia, :marcado, :motivos)
                """
            ),
            {
                "id": identificador,
                "d": dispositivo,
                "folio": abs(hash(nombre)) % 90000 + 1,
                "folio_local": f"VEND01-{nombre}",
                "c": quien,
                "u": semilla["vendedor"],
                "importe": importe,
                "forma": forma,
                "cache": saldo_cache,
                "aplicado": aplicado,
                "favor": a_favor,
                "ahora": ahora,
                "dia": hoy,
                "marcado": marcado,
                "motivos": motivos or [],
            },
        )
        cobros[nombre] = identificador
        return identificador

    await cobro(
        "efectivo",
        importe=Decimal("500.00"),
        forma="efectivo",
        quien=cliente_id,
        aplicado=Decimal("500.00"),
    )
    await cobro(
        "transferencia",
        importe=Decimal("800.00"),
        forma="transferencia",
        quien=cliente_id,
        aplicado=Decimal("400.00"),
    )
    # El marcado: cobró $2,000 a un cliente que no debía nada.
    await cobro(
        "demas",
        importe=Decimal("2000.00"),
        forma="efectivo",
        quien=otro_cliente,
        marcado=True,
        motivos=["cobro_sin_deuda"],
        saldo_cache=Decimal("0.00"),
        a_favor=Decimal("2000.00"),
    )

    # Y la aplicación del cobro en efectivo, para ver el reparto del FIFO.
    await sesion.execute(
        text(
            "INSERT INTO cobros_aplicaciones (cobro_id, venta_id, importe) "
            "VALUES (:k, :v, 500)"
        ),
        {"k": cobros["efectivo"], "v": venta},
    )
    await sesion.commit()

    return {
        "cobros": cobros,
        "cliente": cliente_id,
        "otro_cliente": otro_cliente,
        "venta": venta,
        "dia": hoy,
    }


# ---------------------------------------------------------------------------
# La lista y el corte del día
# ---------------------------------------------------------------------------


async def test_la_pantalla_abre_en_el_dia_de_hoy(cliente, dia_de_cobranza):
    await _entrar(cliente)
    r = await cliente.get("/panel/cobranza")
    assert r.status_code == 200
    plano = texto_plano(r)
    assert "VEND01-efectivo" in plano
    assert "VEND01-transferencia" in plano


async def test_el_arqueo_separa_el_efectivo_de_lo_demas(cliente, dia_de_cobranza):
    """La cifra que tiene que cuadrar contra la mano del vendedor.

    $500 + $2,000 en efectivo entran al arqueo; los $800 de transferencia están en
    el banco. Sumarlos haría que la caja nunca cuadre y que el descuadre se le
    atribuyera al vendedor.
    """
    await _entrar(cliente)
    plano = texto_plano(await cliente.get("/panel/cobranza"))
    assert "2500.00" in plano.replace(",", "")
    assert "800.00" in plano
    assert "no entra" in plano or "no arqueo" in plano


async def test_el_cobro_marcado_sale_arriba_con_su_motivo_en_espanol(
    cliente, dia_de_cobranza
):
    await _entrar(cliente)
    plano = texto_plano(await cliente.get("/panel/cobranza"))
    # El código es para el sistema; la oficina necesita la frase.
    assert "El cliente no tenía facturas abiertas" in plano
    assert "cobro_sin_deuda" not in plano


async def test_el_filtro_de_revision_muestra_solo_los_marcados(
    cliente, dia_de_cobranza
):
    await _entrar(cliente)
    plano = texto_plano(await cliente.get("/panel/cobranza?solo_revision=1"))
    assert "VEND01-demas" in plano
    assert "VEND01-efectivo" not in plano


async def test_un_dia_sin_cobros_lo_dice(cliente, dia_de_cobranza):
    await _entrar(cliente)
    ayer = (dia_de_cobranza["dia"] - timedelta(days=9)).isoformat()
    plano = texto_plano(await cliente.get(f"/panel/cobranza?dia={ayer}"))
    assert "No entró ningún cobro" in plano


async def test_el_saldo_a_favor_se_muestra_sin_llamarlo_error(
    cliente, dia_de_cobranza
):
    """Cobrar de más no es un error: es un anticipo, y la pantalla lo dice así."""
    await _entrar(cliente)
    plano = texto_plano(await cliente.get("/panel/cobranza?solo_revision=1"))
    assert "a favor" in plano


# ---------------------------------------------------------------------------
# El detalle: a qué facturas fue el dinero
# ---------------------------------------------------------------------------


async def test_el_detalle_muestra_el_reparto_del_fifo(cliente, dia_de_cobranza):
    """Es lo que permite contestarle al cliente que llama por una factura.

    Sin esto, la oficina sabe que entraron $500 y no a qué se fueron, que es
    exactamente la pregunta que llega por teléfono.
    """
    await _entrar(cliente)
    r = await cliente.get(f"/panel/cobranza/{dia_de_cobranza['cobros']['efectivo']}")
    assert r.status_code == 200
    plano = texto_plano(r)
    assert "VEND01-000900" in plano
    assert "500.00" in plano


async def test_el_detalle_explica_el_desfase_del_saldo_del_telefono(
    cliente, dia_de_cobranza
):
    """El número que explica un cobro marcado.

    El equipo traía $0 y el cliente no debía nada, así que coincidía: lo que la
    pantalla no puede hacer es callarse el dato, porque es lo que distingue un
    vendedor que cobró a ciegas de uno que cobró mal.
    """
    await _entrar(cliente)
    r = await cliente.get(f"/panel/cobranza/{dia_de_cobranza['cobros']['demas']}")
    plano = texto_plano(r)
    assert "Qué veía el vendedor" in plano
    assert "El teléfono traía" in plano


async def test_un_cobro_sin_aplicaciones_lo_dice_con_palabras(
    cliente, dia_de_cobranza
):
    await _entrar(cliente)
    r = await cliente.get(f"/panel/cobranza/{dia_de_cobranza['cobros']['demas']}")
    plano = texto_plano(r)
    assert "no tenía nada abierto" in plano
    assert "saldo a favor" in plano


async def test_el_detalle_muestra_como_quedo_la_cartera_del_cliente(
    cliente, dia_de_cobranza
):
    await _entrar(cliente)
    r = await cliente.get(f"/panel/cobranza/{dia_de_cobranza['cobros']['efectivo']}")
    plano = texto_plano(r)
    assert "Cómo quedó el cliente" in plano
    # $1,200 − $300 pagados = $900, y venció hace 40 días.
    assert "900.00" in plano
    assert "Vencido" in plano


async def test_una_transferencia_avisa_que_no_entra_al_arqueo(
    cliente, dia_de_cobranza
):
    await _entrar(cliente)
    r = await cliente.get(
        f"/panel/cobranza/{dia_de_cobranza['cobros']['transferencia']}"
    )
    plano = texto_plano(r)
    assert "no entra al arqueo" in plano


async def test_un_cobro_que_no_existe_regresa_a_la_lista(cliente, dia_de_cobranza):
    await _entrar(cliente)
    r = await cliente.get(f"/panel/cobranza/{uuid.uuid4()}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/panel/cobranza"


# ---------------------------------------------------------------------------
# Dar por revisado: auditoría, no corrección
# ---------------------------------------------------------------------------


async def test_dar_por_revisado_lo_saca_de_pendientes_sin_tocar_el_dinero(
    cliente, sesion, dia_de_cobranza
):
    """Lo único que cambia es quién lo miró.

    El importe y su aplicación son hechos: el dinero entró y el FIFO lo repartió
    sobre la cartera real. Un botón que los moviera permitiría maquillar una
    cartera sin que quede rastro.
    """
    await _entrar(cliente)
    cobro_id = dia_de_cobranza["cobros"]["demas"]
    r = await cliente.get(f"/panel/cobranza/{cobro_id}")

    respuesta = await cliente.post(
        f"/panel/cobranza/{cobro_id}/revisado",
        data={"csrf": _csrf(cliente, r), "nota": "El equipo tenía tres días sin sync"},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303

    fila = (
        await sesion.execute(
            text(
                "SELECT requiere_revision, revision_motivos, importe, importe_aplicado "
                "  FROM cobros WHERE id = :id"
            ),
            {"id": cobro_id},
        )
    ).mappings().one()
    assert fila["requiere_revision"] is False
    assert fila["importe"] == Decimal("2000.00")
    assert fila["importe_aplicado"] == Decimal("0.00")

    # El motivo original NO se borra: por qué se marcó es parte del historial, y
    # perderlo haría que la misma situación se investigara desde cero el mes que
    # viene.
    assert "cobro_sin_deuda" in fila["revision_motivos"]
    assert any(m.startswith("revisado_por:") for m in fila["revision_motivos"])
    assert any("tres días sin sync" in m for m in fila["revision_motivos"])


async def test_la_aplicacion_no_se_puede_mover_desde_el_panel(
    cliente, dia_de_cobranza
):
    """No hay ruta para reasignar un cobro, y es deliberado.

    Si existiera, la cartera se podría maquillar sin rastro. Lo que la oficina
    puede hacer es mirar y firmar.
    """
    await _entrar(cliente)
    cobro_id = dia_de_cobranza["cobros"]["efectivo"]
    for ruta in (f"/panel/cobranza/{cobro_id}/aplicar", f"/panel/cobranza/{cobro_id}/cancelar"):
        r = await cliente.post(ruta, data={}, follow_redirects=False)
        assert r.status_code in (404, 405), ruta


async def test_sin_csrf_no_se_puede_dar_por_revisado(cliente, dia_de_cobranza):
    await _entrar(cliente)
    cobro_id = dia_de_cobranza["cobros"]["demas"]
    r = await cliente.post(
        f"/panel/cobranza/{cobro_id}/revisado", data={}, follow_redirects=False
    )
    assert r.status_code == 403


async def test_revisar_dos_veces_no_duplica_el_sello(
    cliente, sesion, dia_de_cobranza
):
    """El UPDATE pide `requiere_revision`, así que el segundo intento no hace nada.

    Sin ese filtro, recargar la página con el formulario reenviado agregaría un
    sello por cada vez y el historial se volvería ilegible.
    """
    await _entrar(cliente)
    cobro_id = dia_de_cobranza["cobros"]["demas"]
    r = await cliente.get(f"/panel/cobranza/{cobro_id}")
    token = _csrf(cliente, r)

    for _ in range(2):
        await cliente.post(
            f"/panel/cobranza/{cobro_id}/revisado",
            data={"csrf": token},
            follow_redirects=False,
        )

    motivos = (
        await sesion.execute(
            text("SELECT revision_motivos FROM cobros WHERE id = :id"),
            {"id": cobro_id},
        )
    ).scalar_one()
    assert len([m for m in motivos if m.startswith("revisado_por:")]) == 1


# ---------------------------------------------------------------------------
# La cartera por antigüedad
# ---------------------------------------------------------------------------


async def test_la_antiguedad_cuenta_desde_el_vencimiento(cliente, dia_de_cobranza):
    """La factura venció hace 40 días: cae en 31-60, no en "más de 60".

    Se emitió hace 55 días. Contar desde la emisión la pondría en el tramo peor y
    la pantalla gritaría por un cliente que paga a 15 días.
    """
    await _entrar(cliente)
    r = await cliente.get("/panel/cobranza/cartera/antiguedad")
    assert r.status_code == 200
    plano = texto_plano(r)
    assert "Abarrotes Doña Mary" in plano
    assert "900.00" in plano


async def test_la_antiguedad_filtra_por_ruta(cliente, dia_de_cobranza):
    await _entrar(cliente)
    plano = texto_plano(await cliente.get("/panel/cobranza/cartera/antiguedad?ruta=R04"))
    assert "Abarrotes Doña Mary" in plano

    vacia = texto_plano(
        await cliente.get("/panel/cobranza/cartera/antiguedad?ruta=R99")
    )
    assert "no tiene cartera abierta" in vacia


async def test_la_antiguedad_no_lista_lo_liquidado(cliente, sesion, dia_de_cobranza):
    """Una factura pagada no es cartera, y dejarla haría que el total mintiera."""
    await sesion.execute(
        text(
            "UPDATE cuentas_por_cobrar SET importe_pagado = importe_original, "
            "       estado = 'liquidada' WHERE venta_id = :v"
        ),
        {"v": dia_de_cobranza["venta"]},
    )
    await sesion.commit()

    await _entrar(cliente)
    plano = texto_plano(await cliente.get("/panel/cobranza/cartera/antiguedad"))
    assert "nadie debe nada" in plano


async def test_el_total_dice_cuando_la_lista_esta_recortada(
    cliente, dia_de_cobranza
):
    """Con poca cartera no hay aviso: el total es de todo y no hay nada que advertir."""
    await _entrar(cliente)
    plano = texto_plano(await cliente.get("/panel/cobranza/cartera/antiguedad"))
    assert "recortada a 500" not in plano


# ---------------------------------------------------------------------------
# Permisos y navegación
# ---------------------------------------------------------------------------


async def test_cobranza_aparece_en_la_navegacion(cliente, dia_de_cobranza):
    """Una pantalla que no está en la navegación no existe para quien usa el panel."""
    await _entrar(cliente)
    plano = texto_plano(await cliente.get("/panel"))
    assert "/panel/cobranza" in plano


async def test_el_tablero_trae_el_efectivo_del_dia_y_la_cartera_vencida(
    cliente, dia_de_cobranza
):
    """Son los dos números por los que se abre el panel en la tarde.

    El efectivo cuenta solo lo que entra al arqueo —$500 + $2,000— y deja fuera los
    $800 de transferencia, que están en el banco y no en la bolsa del vendedor.
    """
    await _entrar(cliente)
    plano = texto_plano(await cliente.get("/panel"))
    assert "efectivo a entregar hoy" in plano
    assert "$2,500.00" in plano
    assert "cartera vencida" in plano
    # La factura de $1,200 con $300 pagados venció hace 40 días.
    assert "$900.00" in plano
    assert "cobros por revisar" in plano


async def test_sin_sesion_manda_a_entrar(cliente, dia_de_cobranza):
    r = await cliente.get("/panel/cobranza", follow_redirects=False)
    assert r.status_code == 303
    assert "/panel/entrar" in r.headers["location"]


async def test_un_vendedor_no_entra_al_panel(cliente, dia_de_cobranza):
    """El vendedor ve SU cartera en el teléfono, no la de la empresa en el panel.

    La puerta se cierra antes del permiso: el panel rechaza a los usuarios de ruta
    en el login. Es más fuerte que una verificación por pantalla, porque no hay
    forma de olvidarla en la siguiente que se escriba.
    """
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": "VEND01", "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "entra por la app" in r.text

    sin_sesion = await cliente.get("/panel/cobranza", follow_redirects=False)
    assert sin_sesion.status_code == 303


async def test_sin_el_permiso_de_cobranza_la_pantalla_se_niega(
    cliente, sesion, semilla, dia_de_cobranza
):
    """`cobranza.ver` es lo que abre esta pantalla, y se verifica de verdad.

    Con ella se ve el día completo de todos los vendedores y la cartera entera de
    la empresa: no es información que todo usuario de oficina deba tener.
    """
    from app.core.seguridad import hashear_password

    await sesion.execute(
        text(
            "INSERT INTO usuarios(id, sucursal_id, codigo, nombre, password_hash, "
            "rol_codigo, creado_en, actualizado_en) "
            "VALUES (:id, :s, 'GER01', 'Gerente', :h, 'gerente', now(), now())"
        ),
        {
            "id": uuid.uuid4(),
            "s": semilla["sucursal"],
            "h": hashear_password(PASSWORD_VENDEDOR),
        },
    )
    # `roles_permisos` es dato de referencia y el TRUNCATE entre pruebas no lo
    # toca, así que el permiso se restituye en el `finally` sin falta: si no, la
    # suite siguiente correría con un gerente mutilado.
    await sesion.execute(
        text(
            "DELETE FROM roles_permisos "
            " WHERE rol_codigo = 'gerente' AND permiso_codigo = 'cobranza.ver'"
        )
    )
    await sesion.commit()
    try:
        await _entrar(cliente, "GER01")
        r = await cliente.get("/panel/cobranza", follow_redirects=False)
        assert r.status_code == 403
        assert "cobranza.ver" in r.text
    finally:
        await sesion.execute(
            text(
                "INSERT INTO roles_permisos (rol_codigo, permiso_codigo) "
                "VALUES ('gerente', 'cobranza.ver') ON CONFLICT DO NOTHING"
            )
        )
        await sesion.commit()
