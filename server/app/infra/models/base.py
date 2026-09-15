"""Base declarativa.

Los modelos NO son la fuente de verdad del esquema: lo es el SQL de
`db/migrations/`. Estas clases son el mapeo de lectura/escritura sobre tablas
que ya existen, con triggers, columnas generadas y constraints EXCLUDE que
SQLAlchemy no expresa. Nunca usar `Base.metadata.create_all()`.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
