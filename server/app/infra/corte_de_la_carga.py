"""Qué días cuenta el corte de una carga (ADR 0002 §176).

«En el corte me aparece que vendió 0 de todo» y «el sistema espera 0». El corte
de una carga contaba solo lo del día EXACTO de la carga, y la carga se hace con
la fecha de mañana (§82): la que se acepta el sábado queda del domingo, el lunes
se vende con fecha del lunes, y el corte no encontraba nada —todo lo entregado
salía de sobrante, también en el Excel—.

Ahora cuenta desde el día de la carga hasta antes de la siguiente carga del
vendedor. Como un vendedor no tiene dos cargas el mismo día
(`uq_carga_vendedor_dia`), cada día cae en un solo corte.

Y hacia atrás, los días que quedaron sueltos desde su carga anterior YA CORTADA:
el vendedor que hizo el corte de ayer hoy en la mañana pidió la carga «para
mañana», y lo que vendió hoy quedaba antes de la fecha de su carga. Solo si la
anterior está cortada: si sigue abierta, esos días son de ella, y contarlos en
las dos sería contarlos dos veces.
"""

from __future__ import annotations

# Para usarse en SQL con la carga como `c`: la fecha de la siguiente carga viva
# del mismo vendedor; nula si todavía no hay otra.
SIGUIENTE_CARGA = """
    (SELECT min(s.fecha_operativa) FROM cargas s
      WHERE s.vendedor_id = c.vendedor_id
        AND s.fecha_operativa > c.fecha_operativa
        AND s.estado IN ('confirmada', 'en_ruta', 'liquidada'))
"""

# Desde cuándo cuenta, en SQL con la carga como `c`: su día, o el siguiente de la
# carga anterior si ésa ya está cortada y es antes. La anterior es la inmediata:
# la que tiene su corte eliminado y espera a que se vuelva a cerrar sigue
# siendo dueña de sus días, aunque haya otra cortada más atrás.
DESDE_LA_CARGA = """
    LEAST(c.fecha_operativa,
          COALESCE((SELECT CASE WHEN s.estado = 'liquidada' THEN s.fecha_operativa + 1
                                ELSE c.fecha_operativa END
                      FROM cargas s
                     WHERE s.vendedor_id = c.vendedor_id
                       AND s.fecha_operativa < c.fecha_operativa
                       AND s.estado IN ('confirmada', 'en_ruta', 'liquidada')
                     ORDER BY s.fecha_operativa DESC LIMIT 1),
                   c.fecha_operativa))
"""

# El tope para comparar dentro de SQL: sin siguiente carga, sin tope. Infinito no
# se puede leer en Python: para traerla, `SIGUIENTE_CARGA`.
HASTA_LA_SIGUIENTE_CARGA = f"COALESCE({SIGUIENTE_CARGA}, 'infinity'::date)"
