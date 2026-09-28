#!/usr/bin/env python3
"""Genera contracts/importes_de_ejemplo.json: el quinto contrato.

El importe de una partida se calcula en **tres lugares**: el teléfono al armar
el carrito, el servidor al recibir el sobre, y el CHECK de PostgreSQL en
`venta_partidas`. Si los tres no dan el mismo centavo, una venta legítima cae en
revisión por un redondeo — y una bandera de revisión que se enciende sola se
acaba ignorando, que es peor que no tenerla.

Este archivo es el árbitro. Lo genera Python con `Decimal`, lo consume la suite
de Dart (`importes_contrato_test.dart`) y lo verifica PostgreSQL en
`server/tests/test_contrato_importes.py`. Los casos no son aleatorios: cada uno
existe porque es un lugar donde dos implementaciones se separan.

Uso:  python3 contracts/generar_importes.py
"""

from __future__ import annotations

import json
import pathlib
import sys
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "server"))

from app.domain.canonico import (  # noqa: E402
    formatear_cantidad,
    formatear_dinero,
    formatear_precio,
)
from app.domain.importes import cantidad_base, importe_de_linea  # noqa: E402

# (nombre, por qué existe el caso, cantidad, precio_unitario, factor_unidad)
CASOS: list[tuple[str, str, str, str, str]] = [
    (
        "pieza_simple",
        "Lo más común: piezas a un precio exacto en centavos",
        "5",
        "13.50",
        "1",
    ),
    (
        "caja_precio_exacto",
        "Dos cajas de 24 a precio de caja",
        "2",
        "296.00",
        "24",
    ),
    (
        "precio_derivado_de_caja",
        "EL CASO QUE JUSTIFICA LOS 4 DECIMALES: 296.00/24 = 12.3333 por pieza. "
        "La caja completa vale 296.00 exactos. Con el precio en centavos "
        "(12.33) daría 295.92, y el cliente lo reclama con la lista en la mano.",
        "24",
        "12.3333",
        "1",
    ),
    (
        "medio_hacia_arriba",
        "TRAMPA: 0.125 → 0.13. El modo por omisión de Decimal en Python es "
        "medio-al-par y daría 0.12, mientras PostgreSQL y Dart dan 0.13.",
        "1",
        "0.1250",
        "1",
    ),
    (
        "medio_hacia_arriba_acumulado",
        "3 × 0.1250 = 0.375 → 0.38",
        "3",
        "0.1250",
        "1",
    ),
    (
        "medio_hacia_arriba_impar",
        "1 × 0.1350 = 0.135 → 0.14 (medio-al-par daría 0.14 también; el par "
        "cambia según el dígito anterior, y por eso hay dos casos)",
        "1",
        "0.1350",
        "1",
    ),
    (
        "granel_media_unidad",
        "Cantidad fraccionaria: media bolsa de granel",
        "0.500",
        "45.80",
        "1",
    ),
    (
        "granel_tres_decimales",
        "1.375 kg — la escala completa de la cantidad",
        "1.375",
        "32.5000",
        "1",
    ),
    (
        "factor_fraccionario",
        "Un display de 6.5 unidades base: raro, pero el esquema lo admite y el "
        "descuadre de inventario no perdona",
        "2",
        "80.00",
        "6.5",
    ),
    (
        "precio_cero",
        "Producto de regalo o promocional con precio cero: el importe es cero, "
        "no un error",
        "3",
        "0",
        "1",
    ),
    (
        "importe_grande",
        "99 999 piezas a 9 999.9999 — muy por encima de cualquier venta real, "
        "para confirmar que el entero de 64 bits de Dart no desborda",
        "99999",
        "9999.9999",
        "1",
    ),
    (
        "centavo_solitario",
        "El importe más chico que existe",
        "1",
        "0.0100",
        "1",
    ),
    (
        "redondeo_hacia_abajo",
        "0.124 → 0.12: el otro lado del medio",
        "1",
        "0.1240",
        "1",
    ),
]


def main() -> int:
    casos = []
    for nombre, porque, cantidad, precio, factor in CASOS:
        c = Decimal(cantidad)
        p = Decimal(precio)
        f = Decimal(factor)
        casos.append(
            {
                "nombre": nombre,
                "por_que": porque,
                "cantidad": formatear_cantidad(c),
                "precio_unitario": formatear_precio(p),
                "factor_unidad": formatear_precio(f),
                "importe": formatear_dinero(importe_de_linea(c, p)),
                "cantidad_base": formatear_cantidad(cantidad_base(c, f)),
            }
        )

    destino = pathlib.Path(__file__).resolve().parent / "importes_de_ejemplo.json"
    documento = {
        "descripcion": (
            "La aritmética de una partida de venta, calculada por Python con "
            "Decimal. Dart y PostgreSQL deben dar exactamente estos importes. "
            "Regla: importe = redondear_medio_arriba(cantidad * precio, 2), "
            "UNA sola vez, al final. Sin descuento: el vendedor no otorga "
            "descuentos en la calle (ADR 0002 §7)."
        ),
        "regla": "importe = ROUND_HALF_UP(cantidad * precio_unitario, 2)",
        "casos": casos,
    }
    destino.write_text(
        json.dumps(documento, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"{len(casos)} casos → {destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
