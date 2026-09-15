"""Línea base: aplica el esquema completo desde db/migrations/*.sql

Revision ID: 0001_linea_base
Revises:
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0001_linea_base"
down_revision = None
branch_labels = None
depends_on = None

# El orden importa: 0004 cierra la FK diferida de 0001, y 0007 necesita la
# extensión btree_gist que declara 0001.
ARCHIVOS = [
    "0001_extensiones_identidad.sql",
    "0002_catalogo.sql",
    "0003_clientes_rutas.sql",
    "0004_inventario.sql",
    "0005_ventas_cobranza.sql",
    "0006_operaciones.sql",
    "0007_sync.sql",
    "0008_jobs.sql",
]


def upgrade() -> None:
    # El DDL se manda al cursor de psycopg sin pasar por ninguna capa de
    # parámetros. Dos razones, ambas reales en estos archivos:
    #   · op.execute() lee ':nombre' como parámetro ligado, y hay JSON de
    #     ejemplo en los comentarios ({"compra":3,"paga":2}).
    #   · exec_driver_sql() interpola '%', y el trigger de inmutabilidad usa
    #     RAISE EXCEPTION con '%' como marcador de formato de PL/pgSQL.
    # psycopg no interpola cuando execute() va sin parámetros.
    cruda = op.get_bind().connection.driver_connection
    for archivo in ARCHIVOS:
        with cruda.cursor() as cursor:
            cursor.execute(leer_sql(archivo))


def downgrade() -> None:
    # Bajar de la línea base significa quedarse sin esquema. Se hace explícito
    # en vez de fingir que hay un camino de vuelta seguro.
    raise NotImplementedError(
        "la línea base no se revierte: restaura un respaldo (ver ARQUITECTURA.md §1.4)"
    )
