"""Los disparadores de change_log corren como su dueño (SECURITY DEFINER)

Revision ID: 0022_disparadores_definer
Revises: 0021_carga_sin_pendientes

Sin esto, cualquier escritura de la API con el rol restringido termina en 500:
el disparador corre como `dsd_api` e intenta insertar en `change_log`, que tiene
RLS y ninguna política de INSERT. Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0022_disparadores_definer"
down_revision = "0021_carga_sin_pendientes"
branch_labels = None
depends_on = None

_FUNCIONES = (
    "fn_registrar_cambio",
    "fn_registrar_cambio_carga",
    "fn_registrar_cambio_cartera",
    "fn_registrar_cambio_catalogo_texto",
)


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0029_disparadores_definer.sql"))


def downgrade() -> None:
    # Volver a SECURITY INVOKER restaura el fallo a propósito: deshacer esta
    # migración sin deshacer la 0022 deja el panel en 500. Se baja entero o no
    # se baja.
    for funcion in _FUNCIONES:
        op.execute(f"ALTER FUNCTION {funcion}() SECURITY INVOKER")
        op.execute(f"ALTER FUNCTION {funcion}() RESET search_path")
