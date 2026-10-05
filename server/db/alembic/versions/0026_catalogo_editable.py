"""Quitar un precio sí llega al teléfono

Revision ID: 0026_catalogo_editable
Revises: 0025_ajuste_del_camion

El delta de `precio` se acota por `producto_id`, así que un DELETE con payload en
NULL no le decía al teléfono CUÁL de los precios del producto se quitó: el renglón
viejo se quedaba y el vendedor seguía ofreciendo una presentación retirada. Ver el
encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0026_catalogo_editable"
down_revision = "0025_ajuste_del_camion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0033_catalogo_editable.sql"))


def downgrade() -> None:
    # Vuelve al disparador genérico, que es el que tenía el defecto.
    op.execute("DROP TRIGGER IF EXISTS trg_cambio_precio ON precios")
    op.execute(
        "CREATE TRIGGER trg_cambio_precio "
        " AFTER INSERT OR UPDATE OR DELETE ON precios "
        " FOR EACH ROW EXECUTE FUNCTION fn_registrar_cambio('precio', 'producto_id')"
    )
    op.execute("DROP FUNCTION IF EXISTS fn_registrar_cambio_precio()")
