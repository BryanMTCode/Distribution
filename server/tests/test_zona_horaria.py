"""La zona de la operación manda también en PostgreSQL.

La operación es en Mazatlán (`America/Mazatlan`, una hora detrás de la Ciudad de
México todo el año). `CURRENT_DATE` decide qué es «hoy» para el tablero y el
corte, y usa la zona de la sesión de PostgreSQL, que `initdb` fija una sola vez:
la conexión tiene que pedirla con la misma `TZ` que usa Python, o de 23:00 a
medianoche en Mazatlán el panel ya diría que es mañana.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import URL_PRUEBAS

# `app.core.db` se importa DENTRO de cada prueba, nunca aquí arriba. Al importarse
# crea los motores con la URL de ese momento, y pytest importa todos los archivos
# ANTES de que el conftest apunte la configuración a la base de pruebas: un
# import aquí dejaba el motor dueño apuntando a la base por omisión, y cuatro
# pruebas de otros archivos —las que usan ese motor— fallaban con «password
# authentication failed for user dsd» solo en la suite completa.


@pytest.mark.asyncio
async def test_la_sesion_usa_la_zona_de_la_operacion(monkeypatch):
    from app.core import db

    monkeypatch.setenv("TZ", "America/Mazatlan")
    motor = create_async_engine(URL_PRUEBAS, **db._opciones_de_zona())
    try:
        async with motor.connect() as con:
            zona = (await con.execute(text("SHOW TimeZone"))).scalar_one()
        assert zona == "America/Mazatlan"
    finally:
        await motor.dispose()


def test_sin_tz_no_se_fuerza_nada(monkeypatch):
    from app.core import db

    monkeypatch.delenv("TZ", raising=False)
    assert db._opciones_de_zona() == {}
