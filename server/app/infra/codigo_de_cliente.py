"""El código de una tienda nueva: el número que sigue (ADR 0002 §122).

Las tiendas de las rutas 1101–1107 llevan los códigos 100 a 1933 (§120), y la
dirección pidió que las nuevas sigan ahí: 1934, 1935… Antes eran «C00001».

Sale de `seq_codigo_cliente` (migración 0014) y no de `max()+1`: dos personas
confirmando a la vez obtendrían el mismo número. La secuencia arranca en 1934
(migración 0060) aunque las tiendas del Excel todavía no se carguen, para no
pisar su rango. Y si un número ya lo tiene alguien —un código escrito a mano—,
se salta al siguiente en vez de chocar contra el UNIQUE.
"""

from __future__ import annotations

from sqlalchemy import text

# El primero después de las tiendas de los Excel de rutas.
PRIMER_CODIGO_NUEVO = 1934


async def siguiente_codigo(sesion) -> str:
    """El siguiente código libre. Gasta el consecutivo aunque la transacción se
    deshaga: un hueco en la serie es preferible a dos tiendas con el mismo."""
    while True:
        numero = (
            await sesion.execute(text("SELECT nextval('seq_codigo_cliente')"))
        ).scalar_one()
        codigo = str(numero)
        ocupado = (
            await sesion.execute(
                text("SELECT 1 FROM clientes WHERE codigo = :c"), {"c": codigo}
            )
        ).first()
        if ocupado is None:
            return codigo
