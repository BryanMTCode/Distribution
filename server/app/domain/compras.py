"""El costo: promedio ponderado, y por qué no vive en `productos`.

────────────────────────────────────────────────────────────────────────────
LA ELECCIÓN DE VALUACIÓN SE ESCRIBE, NO SE HEREDA DEL CÓDIGO
────────────────────────────────────────────────────────────────────────────
Hay tres formas de valuar inventario y elegir en silencio sería lo peor que
podría tener este módulo:

    último costo   simple, y miente cada vez que el proveedor sube: revalúa de
                   golpe todo lo viejo que sigue en el anaquel.
    PEPS (capas)   el más exacto y el más caro: exige rastrear capas y en qué
                   orden se consumen, con el inventario ya repartido en cinco
                   camiones que sincronizan tarde.
    PROMEDIO       una cifra por producto, sobrevive al consumo parcial sin
    PONDERADO      rastrear nada, y es de las opciones que NIF C-4 permite.

Se eligió el PROMEDIO PONDERADO. Las dos razones que lo deciden en este negocio
son la segunda y la tercera: con existencias en varios camiones y ventas que
llegan con horas de retraso, «en qué orden se consumieron las capas» es una
pregunta que el sistema no puede contestar con honestidad.

────────────────────────────────────────────────────────────────────────────
EL COSTO ES POR PRODUCTO, NO POR ALMACÉN
────────────────────────────────────────────────────────────────────────────
Un promedio por almacén divergiría entre la bodega y cada camión, y entonces
una carga —que no es una compra— cambiaría el costo del producto solo por
moverlo. El producto cuesta lo que cuesta, en el anaquel o en la calle.

────────────────────────────────────────────────────────────────────────────
Y EL COSTO NO VA EN `productos`
────────────────────────────────────────────────────────────────────────────
`fn_registrar_cambio` (migración 0010) publica la FILA COMPLETA de `productos`
en `change_log` con `ruta_id = NULL`, o sea a todos los dispositivos. Una
columna de costo ahí viajaría en el siguiente pull al SQLite de cada teléfono, y
cualquier vendedor vería el margen de la empresa en un aparato que se pierde.

De ahí `producto_costos`, tabla aparte y sin disparador. La separación es la
frontera entre lo que el teléfono necesita (precio de venta) y lo que no debe
salir de la oficina (lo que nos cuesta).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# Cuatro decimales, la escala de `Precio` del ADR 0002: una caja de 24 a $296.00
# cuesta $12.3333 la pieza, y a dos decimales las 24 piezas sumarían $295.92.
DIEZMILESIMA = Decimal("0.0001")
CENTAVO = Decimal("0.01")

# Tope de error de dedo para un costo unitario. No es una regla de negocio: un
# costo con tres ceros de más deja el promedio del producto inservible hasta que
# alguien lo rastree, y el promedio se mueve despacio justo por diseño.
COSTO_MAXIMO = Decimal("1000000")


@dataclass(frozen=True)
class Ponderacion:
    """El resultado de ponderar, con las piezas que lo explican.

    Se devuelven las tres cifras y no solo el promedio porque un costo que se ve
    raro se investiga con ellas: cuántas unidades había, a cuánto estaban, y qué
    entró. Guardarlas es lo que permite entender el número sin reconstruir el
    historial de compras.
    """

    costo_promedio: Decimal
    unidades_al_ponderar: Decimal
    hubo_ponderacion: bool

    @property
    def explicacion(self) -> str:
        if not self.hubo_ponderacion:
            return (
                "Sin unidades en mano: el promedio es el costo que entra, no hay "
                "nada contra qué ponderar."
            )
        return (
            f"Ponderado contra {self.unidades_al_ponderar.quantize(Decimal('1'))} "
            f"unidades que ya había."
        )


def ponderar(
    *,
    unidades_en_mano: Decimal,
    costo_actual: Decimal | None,
    unidades_que_entran: Decimal,
    costo_que_entra: Decimal,
) -> Ponderacion:
    """El promedio ponderado nuevo.

        nuevo = (en_mano × actual + entran × entra) / (en_mano + entran)

    Tres casos que no son el de la fórmula, y cada uno por una razón:

    1. **Sin costo actual** (primera compra del producto): el promedio es el
       costo que entra. No hay historia que ponderar.
    2. **Cero o menos unidades en mano**: igual. Ponderar contra un negativo
       —una bodega que quedó en rojo por una carga de más— daría un costo
       negativo con cara de dato bueno, y un costo negativo recorre el sistema
       hasta convertirse en un margen inventado.
    3. **Nada que entre**: el promedio no se mueve. Es el caso de un inventario
       inicial o un conteo sin costo capturado, que se valúan al promedio
       vigente — el tratamiento estándar de lo que aparece en un conteo.
    """
    if unidades_que_entran <= 0:
        return Ponderacion(
            costo_promedio=(costo_actual or costo_que_entra).quantize(
                DIEZMILESIMA, rounding=ROUND_HALF_UP
            ),
            unidades_al_ponderar=unidades_en_mano,
            hubo_ponderacion=False,
        )

    if costo_actual is None or unidades_en_mano <= 0:
        return Ponderacion(
            costo_promedio=costo_que_entra.quantize(
                DIEZMILESIMA, rounding=ROUND_HALF_UP
            ),
            unidades_al_ponderar=max(unidades_en_mano, Decimal(0)),
            hubo_ponderacion=False,
        )

    valor_viejo = unidades_en_mano * costo_actual
    valor_nuevo = unidades_que_entran * costo_que_entra
    unidades = unidades_en_mano + unidades_que_entran
    return Ponderacion(
        costo_promedio=(
            (valor_viejo + valor_nuevo) / unidades
        ).quantize(DIEZMILESIMA, rounding=ROUND_HALF_UP),
        unidades_al_ponderar=unidades_en_mano,
        hubo_ponderacion=True,
    )


def importe_de_renglon(cantidad: Decimal, costo_unitario: Decimal) -> Decimal:
    """Lo que cuesta un renglón, redondeado UNA sola vez y al final.

    Es la misma regla que el importe de una partida de venta (ADR 0002 §2): el
    costo guarda cuatro decimales para que la caja y la pieza cuadren, y el
    importe redondea a centavos una vez — nunca renglón por renglón dentro de
    una suma, que es de donde salen las diferencias de un peso en una factura de
    cuarenta renglones.
    """
    return (cantidad * costo_unitario).quantize(CENTAVO, rounding=ROUND_HALF_UP)


# ===========================================================================
# Las existencias contra las que se pondera
# ===========================================================================
# TODOS los almacenes menos los de merma: un camión cargado trae inventario de
# la empresa aunque esté en la calle, y lo que ya se mermó es pérdida, no
# inventario — ponderar contra ello diluiría el costo con unidades que ya no
# existen para vender.
SQL_UNIDADES_EN_MANO = """
SELECT COALESCE(sum(e.cantidad), 0) AS unidades
  FROM existencias e
  JOIN almacenes a ON a.id = e.almacen_id
 WHERE e.producto_id = :producto
   AND a.tipo <> 'merma'
"""

SQL_COSTO_ACTUAL = """
SELECT costo_promedio, ultimo_costo, ultima_compra_en, unidades_al_ponderar
  FROM producto_costos
 WHERE producto_id = :producto
"""

SQL_GUARDAR_COSTO = """
INSERT INTO producto_costos
    (producto_id, costo_promedio, ultimo_costo, ultima_compra_en,
     unidades_al_ponderar, actualizado_en)
VALUES (:producto, :promedio, :ultimo, :fecha, :unidades, now())
ON CONFLICT (producto_id) DO UPDATE SET
    costo_promedio       = excluded.costo_promedio,
    -- El último costo y su fecha solo se mueven cuando de verdad se COMPRÓ: un
    -- inventario inicial valuado al promedio no es una compra, y sobrescribir
    -- la fecha haría parecer que el proveedor cotizó ese día.
    ultimo_costo         = COALESCE(excluded.ultimo_costo, producto_costos.ultimo_costo),
    ultima_compra_en     = COALESCE(excluded.ultima_compra_en,
                                    producto_costos.ultima_compra_en),
    unidades_al_ponderar = excluded.unidades_al_ponderar,
    actualizado_en       = now()
"""

# El valor del inventario, que es la pregunta que no tenía respuesta: «¿cuánto
# dinero hay en el almacén?». Se valúa al promedio, y los productos sin costo se
# cuentan aparte en vez de valuarse en cero — un cero haría que el total se
# viera bajo sin decir por qué.
SQL_VALOR_DE_INVENTARIO = """
SELECT a.id AS almacen_id, a.codigo, a.nombre, a.tipo,
       COALESCE(sum(e.cantidad * c.costo_promedio)
                FILTER (WHERE c.costo_promedio IS NOT NULL), 0) AS valor,
       COALESCE(sum(e.cantidad) FILTER (WHERE c.costo_promedio IS NULL), 0)
           AS unidades_sin_costo,
       count(*) FILTER (WHERE c.costo_promedio IS NULL AND e.cantidad <> 0)
           AS productos_sin_costo
  FROM almacenes a
  LEFT JOIN existencias e ON e.almacen_id = a.id AND e.cantidad <> 0
  LEFT JOIN producto_costos c ON c.producto_id = e.producto_id
 WHERE a.activo
 GROUP BY a.id, a.codigo, a.nombre, a.tipo
 ORDER BY a.tipo, a.codigo
"""

# La cartera de proveedores por antigüedad, contada desde el VENCIMIENTO y no
# desde la emisión — el mismo criterio que la cartera de clientes: un proveedor
# a 30 días no está vencido el día 15.
SQL_CXP_POR_ANTIGUEDAD = """
SELECT p.id AS proveedor_id, p.codigo, p.nombre, p.dias_credito,
       count(*) AS cuentas,
       COALESCE(sum(c.saldo), 0) AS saldo,
       COALESCE(sum(c.saldo) FILTER (
           WHERE c.fecha_vencimiento >= CURRENT_DATE), 0) AS al_corriente,
       COALESCE(sum(c.saldo) FILTER (
           WHERE c.fecha_vencimiento < CURRENT_DATE
             AND c.fecha_vencimiento >= CURRENT_DATE - 30), 0) AS vencido_30,
       COALESCE(sum(c.saldo) FILTER (
           WHERE c.fecha_vencimiento < CURRENT_DATE - 30), 0) AS vencido_mas
  FROM cuentas_por_pagar c
  JOIN proveedores p ON p.id = c.proveedor_id
 WHERE c.estado IN ('abierta','parcial')
 GROUP BY p.id, p.codigo, p.nombre, p.dias_credito
HAVING COALESCE(sum(c.saldo), 0) <> 0
 ORDER BY sum(c.saldo) DESC
"""


def estado_de_cuenta(original: Decimal, pagado: Decimal) -> str:
    """En qué estado queda una cuenta por pagar después de un pago.

    Se calcula aquí y no con un `CASE` en el UPDATE para que la regla sea una
    sola y se pueda probar: `liquidada` exige igualdad exacta, no «casi», porque
    un centavo de diferencia deja la cuenta abierta para siempre y nadie
    entiende por qué aparece en la lista de pendientes.
    """
    if pagado <= 0:
        return "abierta"
    if pagado >= original:
        return "liquidada"
    return "parcial"
