"""Entradas de mercancía: de qué motivo sale cada tipo del libro mayor.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTA TABLITA ESTÁ EN EL DOMINIO Y NO EN LA PANTALLA
────────────────────────────────────────────────────────────────────────────
Traducir el motivo de un documento al tipo de un movimiento del libro mayor es
una regla de negocio, no un detalle de la interfaz: decide con qué nombre queda
escrito para siempre un asiento que no se puede editar
(`movimientos_inventario` es append-only por disparador).

Si viviera en el router, el día que haya un segundo camino de entrada —una
importación desde Excel, un endpoint— la traducción se escribiría dos veces, y
dos reglas iguales escritas dos veces es el defecto que la Fase 9 encontró en
`ROLES_DE_OFICINA`: se separan sin que nadie lo note y cada una es correcta por
separado.
"""

from __future__ import annotations

# Los tres motivos por los que entra mercancía nueva, con su etiqueta para la
# pantalla y el tipo con el que quedan en el libro mayor.
#
# 'inicial' y 'ajuste' comparten el tipo 'ajuste' del libro mayor: ninguno de
# los dos se le compró a nadie. Se distinguen por el documento, que es la razón
# de que el movimiento apunte al documento y no al contrario — el libro mayor
# se queda con sus ocho tipos y el detalle se recupera siempre.
MOTIVOS: dict[str, tuple[str, str, str]] = {
    "compra": (
        "compra",
        "Compra a proveedor",
        "Llegó mercancía del proveedor, con su remisión o factura.",
    ),
    "inicial": (
        "ajuste",
        "Inventario inicial",
        "Lo que ya estaba en la bodega el día que arrancó el sistema. "
        "No se compró: se declara, y se explica en la nota.",
    ),
    "ajuste": (
        "ajuste",
        "Ajuste por conteo físico",
        "El conteo encontró MÁS de lo que decía el sistema.",
    ),
}


def tipo_de_movimiento(motivo: str) -> str:
    """Con qué tipo queda el asiento. Un motivo desconocido no se adivina.

    Devolver 'ajuste' por omisión sería lo cómodo y dejaría pasar un motivo mal
    escrito como un ajuste de inventario silencioso. El CHECK de la tabla ya lo
    impide en la base; esto lo impide antes de llegar.
    """
    if motivo not in MOTIVOS:
        raise ValueError(f"motivo de entrada desconocido: {motivo!r}")
    return MOTIVOS[motivo][0]


def etiqueta(motivo: str) -> str:
    return MOTIVOS[motivo][1] if motivo in MOTIVOS else motivo


def explicacion(motivo: str) -> str:
    return MOTIVOS[motivo][2] if motivo in MOTIVOS else ""


# Los motivos que exigen nota, además del CHECK `entrada_inicial_con_nota`.
#
# El inventario inicial es el documento que explica de dónde salió TODO el
# inventario del arranque y se lee una sola vez en la vida del sistema: el día
# que algo no cuadra. Sin nota, ese día no hay nada que leer.
EXIGEN_NOTA = ("inicial",)

# Solo a una bodega. Es §0.2 y no una restricción de la pantalla: el almacén del
# camión tiene un único dueño exclusivo, y la oficina nunca le escribe
# existencias —propone un traspaso y el vendedor lo acepta en la app—.
#
# Una entrada directa a un camión le cambiaría el inventario bajo los pies a
# alguien que está vendiendo offline con otra cifra en el teléfono, y el
# descuadre le aparecería en su liquidación como un sobrante del que no sabe
# nada.
TIPOS_DE_DESTINO = ("bodega",)


# ===========================================================================
# El otro sentido: las salidas
# ===========================================================================
# Viven en este módulo y no en uno propio porque son la otra mitad de la misma
# regla: de qué tipo de documento sale qué asiento del libro mayor. Separarlas
# dejaría la traducción escrita en dos archivos que nadie lee juntos, que es
# exactamente cómo se separan dos reglas que deberían ser una.

# Los dos tipos de salida, con su asiento y su etiqueta.
#
# Aquí SÍ se usan dos tipos distintos del libro mayor, al contrario que en las
# entradas —donde 'inicial' y 'ajuste' comparten uno—: la diferencia entre una
# pérdida identificada y un descuadre sin explicar es la que decide si hay algo
# que arreglar en la bodega, y poder separarlas leyendo el libro mayor vale más
# que la simetría con las entradas.
TIPOS_DE_SALIDA: dict[str, tuple[str, str, str]] = {
    "conteo": (
        "ajuste",
        "Ajuste por conteo físico",
        "El conteo encontró MENOS de lo que decía el sistema. Se captura lo que "
        "se contó, no la diferencia.",
    ),
    "merma": (
        "merma",
        "Merma de bodega",
        "Se dañó, caducó o se perdió en la bodega. Lleva motivo del catálogo.",
    ),
}


def tipo_de_movimiento_de_salida(tipo: str) -> str:
    """Con qué tipo queda el asiento. Un tipo desconocido no se adivina."""
    if tipo not in TIPOS_DE_SALIDA:
        raise ValueError(f"tipo de salida desconocido: {tipo!r}")
    return TIPOS_DE_SALIDA[tipo][0]


def etiqueta_de_salida(tipo: str) -> str:
    return TIPOS_DE_SALIDA[tipo][1] if tipo in TIPOS_DE_SALIDA else tipo


def explicacion_de_salida(tipo: str) -> str:
    return TIPOS_DE_SALIDA[tipo][2] if tipo in TIPOS_DE_SALIDA else ""


# El tipo que captura LO CONTADO en vez de lo que sale.
#
# Quien hace un conteo anota lo que ve en el anaquel —«80»—, no «faltan 20»: eso
# exige restar a mano, a las siete de la mañana, por cada producto. Y la resta
# hecha a mano es de donde salen los errores que este documento viene a
# corregir. La base impone que la resta cuadre (`CONSTRAINT conteo_cuadra`).
CAPTURA_LO_CONTADO = ("conteo",)

# El tipo que exige motivo del catálogo cerrado de la Fase 6, y el que lo
# prohíbe.
#
# Un faltante de conteo es, por definición, un faltante CUYA CAUSA NO SE CONOCE:
# si se supiera, se habría capturado como merma el día que pasó. Obligar a
# elegir un motivo haría que alguien marcara 'ROBO' o 'DANADO_BODEGA' sin saber,
# y eso convierte un dato duro —«faltan 20 piezas»— en una acusación inventada
# que después alguien va a leer como un hecho.
EXIGEN_MOTIVO = ("merma",)
PROHIBEN_MOTIVO = ("conteo",)

# Y los dos exigen nota, por lo mismo que el inventario inicial: es el documento
# que alguien va a leer el día que la cifra no cuadre. En un conteo hace falta
# saber quién contó; en una merma, qué pasó más allá del código del motivo.
SALIDAS_EXIGEN_NOTA = ("conteo", "merma")
