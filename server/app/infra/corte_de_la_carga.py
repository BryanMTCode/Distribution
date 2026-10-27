"""Qué días cuenta el corte de una carga (ADR 0002 §176 y §181).

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

────────────────────────────────────────────────────────────────────────────
HASTA QUÉ DÍA CONTÓ EL CORTE ANTERIOR (§181)
────────────────────────────────────────────────────────────────────────────
«¿Por qué con un vendedor me aparece en su corte que se cargaron 2,389 a su
cuenta?» Lo de hacia atrás arrancaba el día siguiente a la FECHA de la carga
anterior, y eso solo es cierto si la anterior contó nada más su día. La del
sábado quedó «para el domingo», se vendió el lunes, y su corte del lunes contó
el lunes; la siguiente carga fue del martes, y su corte arrancó el lunes otra
vez: esperaba lo del lunes, que ya se había entregado, más lo del martes, y el
lunes se le cargó al vendedor como faltante.

Así que cada corte guarda, al cerrarse, hasta qué día contó
(`liquidaciones.cuenta_hasta`), y el siguiente arranca el día después:

  · El corte del vendedor, hasta el día de su corte. Es lo que el teléfono le
    dice que entregue: lo vendido ESE día. Y la venta de mañana que ya subió
    cuando el gerente acepta en la mañana tampoco entra: es del corte de mañana.
  · El de la oficina, hasta antes de la siguiente carga, sin pasar de hoy.
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

# Desde cuándo cuenta, en SQL con la carga como `c`: su día, o el siguiente al
# último que contó el corte de la carga anterior si ésa ya está cortada y es
# antes. La anterior es la inmediata: la que tiene su corte eliminado y espera a
# que se vuelva a cerrar sigue siendo dueña de sus días, aunque haya otra
# cortada más atrás. Un corte de antes de §181 sin `cuenta_hasta` contó, a lo
# más, su día.
DESDE_LA_CARGA = """
    LEAST(c.fecha_operativa,
          COALESCE((SELECT CASE WHEN s.estado = 'liquidada'
                                THEN COALESCE((SELECT lq.cuenta_hasta FROM liquidaciones lq
                                                WHERE lq.carga_id = s.id
                                                  AND lq.estado = 'cerrada'),
                                              s.fecha_operativa) + 1
                                ELSE c.fecha_operativa END
                      FROM cargas s
                     WHERE s.vendedor_id = c.vendedor_id
                       AND s.fecha_operativa < c.fecha_operativa
                       AND s.estado IN ('confirmada', 'en_ruta', 'liquidada')
                     ORDER BY s.fecha_operativa DESC LIMIT 1),
                   c.fecha_operativa))
"""

# Hasta antes de qué día cuenta, en SQL con la carga como `c`: la siguiente
# carga, o el día después del último que cuenta su corte si ya se sabe; nula si
# no hay ninguno de los dos (LEAST no ve los nulos). Para traerla a Python, que
# no lee el infinito.
HASTA_DEL_CORTE = f"""
    LEAST({SIGUIENTE_CARGA},
          (SELECT lq.cuenta_hasta + 1 FROM liquidaciones lq
            WHERE lq.carga_id = c.id AND lq.estado <> 'anulada'))
"""

# El mismo tope para comparar dentro de SQL: sin tope, el infinito.
TOPE_DEL_CORTE = f"COALESCE({HASTA_DEL_CORTE}, 'infinity'::date)"
