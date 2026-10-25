"""Después de guardar, la petición sigue viendo lo que ve quien la hizo (ADR 0002 §178).

«Un vendedor aparece con lo que esperaba el sistema en 0»: el corte LQ-000015 se
cerró «vendió 0, esperaba $0» con sus 9 ventas del día ya en el servidor, la
carga del mismo día y el corte amarrado a esa carga. Lo cerró el gerente al
aceptar la carga del día siguiente, a las 15:14, con los teléfonos de los demás
sincronizando.

El alcance de quien pide (rol, usuario, rutas) se fijaba en la CONEXIÓN. Cerrar
un corte guarda a medias (`abrir_corte` hace commit), y después de un commit la
sesión toma del pool otra conexión: la que dejó la petición anterior, con el
alcance de otro. Con el de otro vendedor, las ventas de éste no se ven —RLS—, y
el corte las recalcula en cero.

Todo el resto de la suite corre como el dueño de las tablas, que salta RLS: por
eso aquí se usa el rol restringido de verdad, como en `test_rls.py`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_cierre_del_vendedor import _aceptar, _corte, _solicitud, dia  # noqa: F401
from tests.test_cortes_api import _cab
from tests.test_rls import cliente_rls, dos_rutas, motor_restringido, rol_api  # noqa: F401

pytestmark = pytest.mark.asyncio

VENTAS_DE = text("SELECT count(*) FROM ventas WHERE vendedor_id = :v")


async def test_despues_de_guardar_sigue_el_alcance_de_quien_pide(
    motor_restringido, dos_rutas  # noqa: F811
):
    from app.core.db import fijar_alcance

    fabrica = async_sessionmaker(motor_restringido, expire_on_commit=False)
    async with fabrica() as gerente:
        await fijar_alcance(gerente, rol="gerente", usuario_id=uuid.uuid4())
        assert (await gerente.execute(VENTAS_DE, {"v": dos_rutas["vendedor_a"]})).scalar_one() == 1

        # Mientras tanto, el teléfono de otro vendedor usa otra conexión y la deja.
        async with fabrica() as otro:
            await fijar_alcance(
                otro, rol="vendedor", usuario_id=dos_rutas["vendedor_b"],
                rutas=[dos_rutas["ruta_b"]],
            )
            await otro.execute(text("SELECT 1"))
            await otro.commit()

        # El gerente guarda, y lo siguiente sale de la conexión que dejó el otro.
        await gerente.commit()
        assert (await gerente.execute(VENTAS_DE, {"v": dos_rutas["vendedor_a"]})).scalar_one() == 1


async def _ensuciar_el_pool(motor, cuantas: int = 5) -> None:
    """Deja en el pool conexiones con el alcance de otro vendedor, como las dejan
    los teléfonos que sincronizan al final del día."""
    from app.core.db import fijar_alcance

    fabrica = async_sessionmaker(motor, expire_on_commit=False)
    sesiones = [fabrica() for _ in range(cuantas)]
    for s in sesiones:
        await fijar_alcance(s, rol="vendedor", usuario_id=uuid.uuid4())
    for s in sesiones:
        await s.commit()
        await s.close()


async def test_aceptar_la_carga_cierra_el_corte_con_sus_ventas(
    cliente_rls, motor_restringido, sesion, semilla, dia  # noqa: F811
):
    """El caso de LQ-000015: el gerente acepta la carga de mañana, eso cierra el
    corte de hoy, y el pool trae conexiones de otros vendedores."""
    corte_id = await _corte(sesion, dia, semilla, efectivo="2250.00")
    sid = await _solicitud(sesion, dia, semilla, corte_id=corte_id)
    cab = await _cab(cliente_rls, "GER01")
    await _ensuciar_el_pool(motor_restringido)

    r = await _aceptar(cliente_rls, sid, cab)

    assert r.status_code == 200, r.text
    await sesion.rollback()
    liquidacion = (
        await sesion.execute(
            text(
                "SELECT l.efectivo_esperado, l.efectivo_entregado, "
                "       (SELECT sum(cant_vendida) FROM liquidacion_detalle "
                "         WHERE liquidacion_id = l.id) AS vendida "
                "  FROM cortes_vendedor k JOIN liquidaciones l ON l.id = k.liquidacion_id "
                " WHERE k.id = :k"
            ),
            {"k": corte_id},
        )
    ).mappings().one()
    assert liquidacion["vendida"] == Decimal("180.000")
    assert liquidacion["efectivo_esperado"] == Decimal("2250.00")
    assert liquidacion["efectivo_entregado"] == Decimal("2250.00")
