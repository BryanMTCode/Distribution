"""Transferencias y cheques: por confirmar hasta que la oficina vea el dinero.

Regla de la dirección (octubre 2026, migración 0038): **una transferencia sin
confirmar NO libera crédito.** El saldo del cliente se restaura hasta que la
oficina confirma que el dinero está en firme en la cuenta.

Lo que estas pruebas defienden, en orden de cuánto dinero cuesta romperlo:

1. Un pago que no es efectivo **no baja la deuda** al sincronizar, ni en la
   cartera ni en el crédito disponible.
2. **La base no deja** abonar uno por confirmar, por ningún camino.
3. Confirmar lo aplica **una sola vez**, en el mismo FIFO que el efectivo.
4. Rechazar uno ya confirmado (el cheque rebotó) **reabre las facturas**.
5. El teléfono se entera de todo eso por el delta de cartera.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.conftest import PASSWORD_VENDEDOR, solo_texto
from tests.test_cobro_ingesta import (
    _aplicar,
    _leer_cobro,
    _payload,
    _saldos,
    sembrar_escenario,
)
from tests.test_panel_cobranza import _csrf as _csrf_de_la_cookie

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def escenario(sesion, semilla) -> dict:
    """El de la ingesta de cobros: $800 vencida y $1,200 por vencer."""
    return await sembrar_escenario(sesion, semilla)


async def _entrar(cliente, codigo: str = "ADMIN01") -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": codigo, "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


def _csrf(respuesta) -> str:
    marca = 'name="csrf" value="'
    inicio = respuesta.text.index(marca) + len(marca)
    return respuesta.text[inicio : respuesta.text.index('"', inicio)]


async def _cartera(sesion, cliente_id) -> dict:
    return dict(
        (
            await sesion.execute(
                text("SELECT * FROM v_cartera_cliente WHERE cliente_id = :c"),
                {"c": cliente_id},
            )
        ).mappings().one()
    )


async def _ultimo_delta_de_cartera(sesion, cliente_id) -> dict:
    return (
        await sesion.execute(
            text(
                "SELECT payload FROM change_log "
                " WHERE entidad = 'cartera' AND entidad_id = :c "
                " ORDER BY cursor DESC LIMIT 1"
            ),
            {"c": cliente_id},
        )
    ).scalar_one()


async def _transferencia(sesion, escenario, semilla, importe="500.00", folio=1, **campos):
    cobro_id = await _aplicar(
        sesion,
        escenario,
        semilla,
        _payload(
            escenario,
            importe=importe,
            forma_pago=campos.pop("forma_pago", "transferencia"),
            referencia=campos.pop("referencia", f"SPEI-{folio:04d}"),
            folio_consecutivo=folio,
            folio_local=f"VEND01-{folio:06d}",
            **campos,
        ),
    )
    await sesion.commit()
    return cobro_id


# ===========================================================================
# 1. Al sincronizar: se registra, y NO baja la deuda
# ===========================================================================
async def test_una_transferencia_nace_por_confirmar_y_no_toca_la_deuda(
    sesion, semilla, escenario
):
    antes = await _saldos(sesion, escenario["cliente"])
    cobro_id = await _transferencia(sesion, escenario, semilla, "800.00")

    cobro = await _leer_cobro(sesion, cobro_id)
    assert cobro["estado"] == "por_confirmar"
    assert cobro["importe_aplicado"] == Decimal("0")
    # Ni una factura se movió: la regla de la dirección, en la cartera.
    assert await _saldos(sesion, escenario["cliente"]) == antes
    aplicaciones = (
        await sesion.execute(
            text("SELECT count(*) FROM cobros_aplicaciones WHERE cobro_id = :c"),
            {"c": cobro_id},
        )
    ).scalar_one()
    assert aplicaciones == 0


async def test_un_cheque_tambien_espera_al_banco(sesion, semilla, escenario):
    cobro_id = await _transferencia(
        sesion, escenario, semilla, "300.00", forma_pago="cheque", referencia="CH 0042"
    )
    assert (await _leer_cobro(sesion, cobro_id))["estado"] == "por_confirmar"


async def test_el_efectivo_se_sigue_aplicando_al_llegar(sesion, semilla, escenario):
    """La regla es para lo que no está en la mano. El efectivo, como siempre."""
    cobro_id = await _transferencia(
        sesion, escenario, semilla, "500.00", forma_pago="efectivo", referencia=None
    )
    cobro = await _leer_cobro(sesion, cobro_id)
    assert cobro["estado"] == "confirmado"
    assert cobro["importe_aplicado"] == Decimal("500.00")


async def test_por_confirmar_no_libera_credito_pero_se_informa(sesion, semilla, escenario):
    """El saldo sigue completo; `por_confirmar` dice cuánto reportó pagado.

    Sin ese número el vendedor vería la deuda completa y le volvería a cobrar.
    """
    antes = await _cartera(sesion, escenario["cliente"])
    await _transferencia(sesion, escenario, semilla, "800.00")
    despues = await _cartera(sesion, escenario["cliente"])

    assert despues["saldo"] == antes["saldo"] == Decimal("2000.00")
    assert despues["disponible"] == antes["disponible"]
    assert despues["por_confirmar"] == Decimal("800.00")


async def test_el_telefono_se_entera_de_lo_que_esta_por_confirmar(
    sesion, semilla, escenario
):
    """Nacer por confirmar no toca ninguna factura: sin su propio disparador, el
    delta de cartera nunca saldría."""
    await _transferencia(sesion, escenario, semilla, "800.00")
    delta = await _ultimo_delta_de_cartera(sesion, escenario["cliente"])
    assert Decimal(str(delta["por_confirmar"])) == Decimal("800.00")
    assert Decimal(str(delta["saldo"])) == Decimal("2000.00")


# ===========================================================================
# 2. La base lo sostiene, venga de donde venga
# ===========================================================================
async def test_la_base_no_deja_abonar_uno_por_confirmar(sesion, semilla, escenario):
    cobro_id = await _transferencia(sesion, escenario, semilla, "800.00")
    with pytest.raises(IntegrityError, match="cobro_sin_aplicar_hasta_confirmar"):
        await sesion.execute(
            text("UPDATE cobros SET importe_aplicado = 800 WHERE id = :id"),
            {"id": cobro_id},
        )
    await sesion.rollback()


async def test_la_base_no_deja_efectivo_por_confirmar(sesion, semilla, escenario):
    """Sería dinero que el arqueo cobra y la cartera no abona: el vendedor lo
    pagaría dos veces."""
    cobro_id = await _transferencia(
        sesion, escenario, semilla, "100.00", forma_pago="efectivo", referencia=None
    )
    with pytest.raises(IntegrityError, match="cobro_efectivo_no_espera_banco"):
        await sesion.execute(
            text(
                "UPDATE cobros SET estado = 'por_confirmar', importe_aplicado = 0, "
                "       saldo_a_favor = 0 WHERE id = :id"
            ),
            {"id": cobro_id},
        )
    await sesion.rollback()


async def test_la_base_no_deja_rechazar_sin_motivo(sesion, semilla, escenario):
    cobro_id = await _transferencia(sesion, escenario, semilla, "100.00")
    with pytest.raises(IntegrityError, match="cobro_rechazo_con_motivo"):
        await sesion.execute(
            text("UPDATE cobros SET estado = 'rechazado' WHERE id = :id"), {"id": cobro_id}
        )
    await sesion.rollback()


# ===========================================================================
# 3. Confirmar desde el panel
# ===========================================================================
async def test_la_lista_muestra_lo_que_espera_al_banco(cliente, sesion, semilla, escenario):
    await _transferencia(sesion, escenario, semilla, "800.00", referencia="SPEI-778899")
    await _entrar(cliente)
    r = await cliente.get("/panel/cobranza/por-confirmar")
    assert r.status_code == 200
    plano = solo_texto(r)
    assert "SPEI-778899" in plano
    assert "Abarrotes Doña Mary" in plano
    assert "800.00" in plano


async def test_confirmar_aplica_en_fifo_y_libera_el_credito(
    cliente, sesion, semilla, escenario
):
    cobro_id = await _transferencia(sesion, escenario, semilla, "1000.00")
    await _entrar(cliente)
    pagina = await cliente.get("/panel/cobranza/por-confirmar")
    r = await cliente.post(
        "/panel/cobranza/confirmar",
        data={"csrf": _csrf(pagina), "cobro": [str(cobro_id)]},
        follow_redirects=True,
    )
    assert "Se confirmaron 1 cobro(s)" in solo_texto(r)

    cobro = await _leer_cobro(sesion, cobro_id)
    assert cobro["estado"] == "confirmado"
    assert cobro["importe_aplicado"] == Decimal("1000.00")
    assert cobro["resuelto_por"] == semilla["admin"]

    # El mismo FIFO que el efectivo: primero la vencida ($800), luego $200 a la otra.
    saldos = await _saldos(sesion, escenario["cliente"])
    assert saldos[escenario["factura_vencida"]]["estado"] == "liquidada"
    assert saldos[escenario["factura_por_vencer"]]["saldo"] == Decimal("1000.00")

    cartera = await _cartera(sesion, escenario["cliente"])
    assert cartera["saldo"] == Decimal("1000.00")
    assert cartera["por_confirmar"] == Decimal("0")
    delta = await _ultimo_delta_de_cartera(sesion, escenario["cliente"])
    assert Decimal(str(delta["saldo"])) == Decimal("1000.00")
    assert Decimal(str(delta["por_confirmar"])) == Decimal("0")


async def test_confirmar_dos_veces_no_abona_dos_veces(cliente, sesion, semilla, escenario):
    """El doble clic, o dos personas conciliando el mismo estado de cuenta."""
    cobro_id = await _transferencia(sesion, escenario, semilla, "300.00")
    await _entrar(cliente)
    pagina = await cliente.get("/panel/cobranza/por-confirmar")
    for _ in range(2):
        await cliente.post(
            "/panel/cobranza/confirmar",
            data={"csrf": _csrf(pagina), "cobro": [str(cobro_id)]},
        )
    cartera = await _cartera(sesion, escenario["cliente"])
    assert cartera["saldo"] == Decimal("1700.00")


async def test_se_confirman_varios_de_una_vez(cliente, sesion, semilla, escenario):
    uno = await _transferencia(sesion, escenario, semilla, "200.00", folio=1)
    dos = await _transferencia(sesion, escenario, semilla, "300.00", folio=2)
    tres = await _transferencia(sesion, escenario, semilla, "50.00", folio=3)
    await _entrar(cliente)
    pagina = await cliente.get("/panel/cobranza/por-confirmar")
    await cliente.post(
        "/panel/cobranza/confirmar",
        data={"csrf": _csrf(pagina), "cobro": [str(uno), str(dos)]},
    )
    assert (await _leer_cobro(sesion, uno))["estado"] == "confirmado"
    assert (await _leer_cobro(sesion, dos))["estado"] == "confirmado"
    # El que no se palomeó sigue esperando.
    assert (await _leer_cobro(sesion, tres))["estado"] == "por_confirmar"
    assert (await _cartera(sesion, escenario["cliente"]))["por_confirmar"] == Decimal("50.00")


async def test_confirmar_sin_deuda_lo_marca_como_el_efectivo(cliente, sesion, semilla, escenario):
    """Las marcas de deuda se deciden al aplicar, contra la cartera de ESE momento."""
    cobro_id = await _transferencia(sesion, escenario, semilla, "2500.00")
    await _entrar(cliente)
    pagina = await cliente.get("/panel/cobranza/por-confirmar")
    await cliente.post(
        "/panel/cobranza/confirmar", data={"csrf": _csrf(pagina), "cobro": [str(cobro_id)]}
    )
    cobro = await _leer_cobro(sesion, cobro_id)
    assert cobro["saldo_a_favor"] == Decimal("500.00")
    assert cobro["requiere_revision"]
    assert "cobro_excede_deuda" in cobro["revision_motivos"]


# ===========================================================================
# 4. Rechazar
# ===========================================================================
async def test_rechazar_exige_motivo(cliente, sesion, semilla, escenario):
    cobro_id = await _transferencia(sesion, escenario, semilla, "800.00")
    await _entrar(cliente)
    pagina = await cliente.get(f"/panel/cobranza/{cobro_id}")
    r = await cliente.post(
        f"/panel/cobranza/{cobro_id}/rechazar",
        data={"csrf": _csrf(pagina), "motivo": "   "},
        follow_redirects=True,
    )
    assert "Escribe por qué se rechaza" in solo_texto(r)
    assert (await _leer_cobro(sesion, cobro_id))["estado"] == "por_confirmar"


async def test_rechazar_uno_por_confirmar_deja_la_deuda_completa(
    cliente, sesion, semilla, escenario
):
    cobro_id = await _transferencia(sesion, escenario, semilla, "800.00")
    await _entrar(cliente)
    pagina = await cliente.get(f"/panel/cobranza/{cobro_id}")
    r = await cliente.post(
        f"/panel/cobranza/{cobro_id}/rechazar",
        data={"csrf": _csrf(pagina), "motivo": "No aparece en el estado de cuenta"},
        follow_redirects=True,
    )
    assert "sigue completa" in solo_texto(r)

    cobro = await _leer_cobro(sesion, cobro_id)
    assert cobro["estado"] == "rechazado"
    assert cobro["resolucion_nota"] == "No aparece en el estado de cuenta"
    cartera = await _cartera(sesion, escenario["cliente"])
    assert cartera["saldo"] == Decimal("2000.00")
    assert cartera["por_confirmar"] == Decimal("0")
    # El teléfono deja de mostrarlo como «por confirmar»: sin esto el vendedor no
    # le volvería a cobrar un pago que nunca llegó.
    delta = await _ultimo_delta_de_cartera(sesion, escenario["cliente"])
    assert Decimal(str(delta["por_confirmar"])) == Decimal("0")


async def test_un_cheque_que_rebota_despues_reabre_las_facturas(
    cliente, sesion, semilla, escenario
):
    cobro_id = await _transferencia(
        sesion, escenario, semilla, "1000.00", forma_pago="cheque", referencia="CH 77"
    )
    await _entrar(cliente)
    pagina = await cliente.get("/panel/cobranza/por-confirmar")
    await cliente.post(
        "/panel/cobranza/confirmar", data={"csrf": _csrf(pagina), "cobro": [str(cobro_id)]}
    )
    assert (await _cartera(sesion, escenario["cliente"]))["saldo"] == Decimal("1000.00")

    # Tres días después, el banco lo devuelve.
    pagina = await cliente.get(f"/panel/cobranza/{cobro_id}")
    r = await cliente.post(
        f"/panel/cobranza/{cobro_id}/rechazar",
        data={"csrf": _csrf(pagina), "motivo": "Cheque devuelto por fondos insuficientes"},
        follow_redirects=True,
    )
    assert "vuelven a deberse" in solo_texto(r)

    saldos = await _saldos(sesion, escenario["cliente"])
    vencida = saldos[escenario["factura_vencida"]]
    assert vencida["saldo"] == Decimal("800.00")
    assert vencida["estado"] == "abierta"
    assert saldos[escenario["factura_por_vencer"]]["saldo"] == Decimal("1200.00")
    cobro = await _leer_cobro(sesion, cobro_id)
    assert cobro["estado"] == "rechazado"
    assert cobro["importe_aplicado"] == Decimal("0")
    aplicaciones = (
        await sesion.execute(
            text("SELECT count(*) FROM cobros_aplicaciones WHERE cobro_id = :c"),
            {"c": cobro_id},
        )
    ).scalar_one()
    assert aplicaciones == 0
    assert (await _cartera(sesion, escenario["cliente"]))["saldo"] == Decimal("2000.00")


async def test_el_efectivo_no_se_rechaza(cliente, sesion, semilla, escenario):
    cobro_id = await _transferencia(
        sesion, escenario, semilla, "500.00", forma_pago="efectivo", referencia=None
    )
    await _entrar(cliente)
    # La ficha de un cobro en efectivo no dibuja el formulario de rechazo: el POST
    # se manda a mano, que es justo el camino que la pantalla no ofrece.
    pagina = await cliente.get(f"/panel/cobranza/{cobro_id}")
    assert "forma_rechazar" not in pagina.text
    r = await cliente.post(
        f"/panel/cobranza/{cobro_id}/rechazar",
        data={"csrf": _csrf_de_la_cookie(cliente), "motivo": "lo que sea"},
        follow_redirects=True,
    )
    assert "El efectivo no se rechaza" in solo_texto(r)
    assert (await _leer_cobro(sesion, cobro_id))["estado"] == "confirmado"


async def test_rechazar_deja_constancia(cliente, sesion, semilla, escenario):
    cobro_id = await _transferencia(sesion, escenario, semilla, "800.00")
    await _entrar(cliente)
    pagina = await cliente.get(f"/panel/cobranza/{cobro_id}")
    await cliente.post(
        f"/panel/cobranza/{cobro_id}/rechazar",
        data={"csrf": _csrf(pagina), "motivo": "No llegó"},
    )
    fila = (
        await sesion.execute(
            text(
                "SELECT accion, usuario_id, motivo FROM auditoria "
                " WHERE entidad = 'cobro' AND entidad_id = :c"
            ),
            {"c": cobro_id},
        )
    ).mappings().one()
    assert fila["accion"] == "rechazar"
    assert fila["usuario_id"] == semilla["admin"]
    assert fila["motivo"] == "No llegó"


# ===========================================================================
# 5. Quién puede
# ===========================================================================
async def _usuario(sesion, semilla, codigo: str, rol: str) -> None:
    from app.core.seguridad import hashear_password

    sucursal = (
        await sesion.execute(text("SELECT id FROM sucursales LIMIT 1"))
    ).scalar_one()
    await sesion.execute(
        text(
            "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
            "                      rol_codigo, creado_en, actualizado_en) "
            "VALUES (:id, :s, :c, :c, :h, :r, now(), now())"
        ),
        {
            "id": uuid.uuid4(),
            "s": sucursal,
            "c": codigo,
            "h": hashear_password(PASSWORD_VENDEDOR),
            "r": rol,
        },
    )
    await sesion.commit()


async def test_el_supervisor_ve_pero_no_confirma(cliente, sesion, semilla, escenario):
    """La mano que vigila la ruta no da por buena la transferencia de su vendedor."""
    cobro_id = await _transferencia(sesion, escenario, semilla, "800.00")
    await _usuario(sesion, semilla, "SUP01", "supervisor")
    await _entrar(cliente, "SUP01")

    pagina = await cliente.get("/panel/cobranza/por-confirmar")
    assert pagina.status_code == 200
    assert 'name="cobro"' not in pagina.text  # sin casillas
    r = await cliente.post(
        "/panel/cobranza/confirmar", data={"csrf": _csrf(pagina), "cobro": [str(cobro_id)]}
    )
    assert r.status_code == 403
    assert (await _leer_cobro(sesion, cobro_id))["estado"] == "por_confirmar"


async def test_gerencia_confirma(cliente, sesion, semilla, escenario):
    cobro_id = await _transferencia(sesion, escenario, semilla, "800.00")
    await _usuario(sesion, semilla, "GER01", "gerente")
    await _entrar(cliente, "GER01")
    pagina = await cliente.get("/panel/cobranza/por-confirmar")
    await cliente.post(
        "/panel/cobranza/confirmar", data={"csrf": _csrf(pagina), "cobro": [str(cobro_id)]}
    )
    assert (await _leer_cobro(sesion, cobro_id))["estado"] == "confirmado"


# ===========================================================================
# 6. Dónde se ve
# ===========================================================================
async def test_el_tablero_avisa_cuanto_espera_al_banco(cliente, sesion, semilla, escenario):
    await _transferencia(sesion, escenario, semilla, "800.00")
    await _entrar(cliente)
    r = await cliente.get("/panel")
    assert 'id="tarjeta_por_confirmar"' in r.text
    plano = solo_texto(r)
    assert "transferencias y cheques por confirmar" in plano
    assert "800.00" in plano


async def test_el_corte_del_dia_no_cuenta_la_transferencia_como_efectivo(
    cliente, sesion, semilla, escenario
):
    await _transferencia(sesion, escenario, semilla, "800.00", folio=1)
    await _transferencia(
        sesion, escenario, semilla, "150.00", folio=2, forma_pago="efectivo", referencia=None
    )
    await _entrar(cliente)
    r = await cliente.get(f"/panel/cobranza?dia={escenario['dia'].isoformat()}")
    plano = solo_texto(r)
    assert "por confirmar" in plano
    # Efectivo a entregar: solo los 150.
    assert "$150.00" in plano
