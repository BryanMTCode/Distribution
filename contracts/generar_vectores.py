#!/usr/bin/env python3
"""Genera contracts/canonical_vectors.json.

Los vectores son el contrato ejecutable entre Dart y Python: ambas suites los
ejecutan en CI. El día que alguien cambie un serializador, un lado se pone rojo
antes de producción — no seis meses después con miles de tickets en cuarentena.

Cada vector trae la forma canónica esperada ADEMÁS del hash. Cuando Dart falle,
la diferencia se ve en el texto, no en 64 caracteres hexadecimales.

Uso:  python3 contracts/generar_vectores.py
"""

from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "server"))

from app.domain.canonico import (  # noqa: E402
    a_texto_canonico,
    formatear_cantidad,
    formatear_dinero,
    formatear_instante,
    hash_payload,
)

CASOS: list[tuple[str, str, dict]] = [
    ("vacio", "Objeto vacío", {}),
    ("escalares", "Los tres escalares no numéricos", {"a": "x", "b": True, "c": False}),
    ("entero", "Los enteros van sin comillas", {"folio_consecutivo": 123}),
    ("entero_negativo", "Entero negativo", {"desfase_reloj_seg": -47}),
    ("entero_cero", "Cero es entero, no '0.00'", {"reimpresiones": 0}),
    (
        "orden_claves",
        "Las claves se ordenan por punto de código, no por inserción",
        {"zeta": 1, "alfa": 2, "Beta": 3, "_guion": 4},
    ),
    (
        "orden_claves_acentos",
        "El orden es por punto de código: 'z' (0x7A) antes que 'á' (0xE1)",
        {"árbol": 1, "zorro": 2},
    ),
    ("nulo_se_omite", "Una clave nula desaparece de la forma canónica", {"a": 1, "b": None}),
    (
        "nulo_equivale_a_ausente",
        "Mismo hash que {'a': 1}: es la regla que elimina la divergencia null/ausente",
        {"a": 1, "z": None},
    ),
    ("arreglo_vacio", "Arreglo vacío", {"partidas": []}),
    (
        "arreglo_orden_significativo",
        "El orden del arreglo SÍ importa: las partidas llevan número de línea",
        {"lineas": [3, 1, 2]},
    ),
    (
        "orden_claves_fuera_del_plano_basico",
        "TRAMPA: 'ﬀ' (U+FB00) va ANTES que '🔒' (U+1F512) por punto de código, "
        "pero DESPUÉS si se ordena por unidades UTF-16 — que es lo que hace "
        "String.compareTo de Dart. El comparador de Dart debe usar runas.",
        {"\ufb00": 1, "\U0001f512": 2, "a": 3},
    ),
    (
        "anidado",
        "Objetos anidados, canonizados recursivamente",
        {"venta": {"total": "250.00", "cliente": {"id": "c-1", "nombre": "Doña Mary"}}},
    ),
    (
        "anidado_profundo",
        "Anidamiento de 4 niveles",
        {"a": {"b": {"c": {"d": "fin"}}}},
    ),
    ("dinero_simple", "Dinero con escala fija de 2", {"total": formatear_dinero("250")}),
    (
        "dinero_centavos",
        "Dinero que en float se rompería: 0.1 + 0.2",
        {"total": formatear_dinero("0.30")},
    ),
    (
        "dinero_grande",
        "Importe grande sin separador de miles",
        {"total": formatear_dinero("1234567.89")},
    ),
    (
        "dinero_negativo",
        "Nota de crédito: dinero negativo",
        {"total": formatear_dinero("-125.50")},
    ),
    ("dinero_cero", "Cero en dinero conserva la escala", {"descuento": formatear_dinero("0")}),
    (
        "cantidad_escala_3",
        "Cantidad con escala fija de 3 (fracción de caja, granel)",
        {"cantidad": formatear_cantidad("12")},
    ),
    (
        "cantidad_fraccionaria",
        "Kilogramos con tres decimales",
        {"cantidad": formatear_cantidad("1.375")},
    ),
    (
        "instante_utc",
        "RFC 3339 en UTC con exactamente 3 decimales",
        {"fecha_dispositivo": formatear_instante(
            datetime(2026, 9, 15, 3, 14, 7, 123456, tzinfo=timezone.utc)
        )},
    ),
    (
        "instante_con_offset",
        "Un instante en UTC-6 se normaliza a UTC antes de canonizar",
        {"fecha_dispositivo": formatear_instante(
            datetime(2026, 9, 14, 21, 14, 7, 0, tzinfo=timezone(timedelta(hours=-6)))
        )},
    ),
    (
        "instante_medianoche",
        "Medianoche: los milisegundos se emiten aunque sean cero",
        {"fecha_operativa_inicio": formatear_instante(
            datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        )},
    ),
    ("acentos", "UTF-8 literal, sin escapes \\uXXXX", {"nombre": "Abarrotes Doña Mary Ñuñez"}),
    ("emoji", "Fuera del plano básico (pares suplentes en UTF-16)", {"nota": "cerrado 🔒 hoy"}),
    ("comillas", "Comillas dobles escapadas", {"nota": 'el dueño dijo "vuelvo al rato"'}),
    ("backslash", "Barra invertida escapada", {"ruta": "C:\\tickets\\hoy"}),
    ("salto_linea", "Salto de línea y tabulador con escape corto", {"obs": "linea1\nlinea2\tfin"}),
    ("control", "Carácter de control con escape \\u", {"raro": "a\x01b"}),
    ("slash_sin_escape", "La diagonal NO se escapa", {"url": "a/b/c"}),
    ("clave_vacia", "Clave vacía es válida", {"": "vacia"}),
    ("clave_con_acento", "Las claves también se escapan con las mismas reglas", {"año": 2026}),
    (
        "venta_completa",
        "Payload realista de una venta offline con dos partidas",
        {
            "id": "018f3a5c-7b2e-7c91-9f3d-2a1b4c5d6e7f",
            "folio_consecutivo": 124,
            "folio_local": "VEND01-000124",
            "cliente_id": "7a1c9e30-4b55-7d12-8e44-91f0a2b3c4d5",
            "visita_id": "018f3a5c-7b2e-7c91-9f3d-2a1b4c5d6e80",
            "tipo": "contado",
            "lista_precios_id": "c0ffee00-0000-4000-8000-000000000001",
            "lista_precios_version": 7,
            "subtotal": formatear_dinero("412.50"),
            "descuento": formatear_dinero("12.50"),
            "impuestos": formatear_dinero("0"),
            "total": formatear_dinero("400.00"),
            "lat": "19.4326000",
            "lng": "-99.1332000",
            "ubicacion_precision_m": "8.50",
            "rfc": None,
            "observaciones": None,
            "fecha_dispositivo": formatear_instante(
                datetime(2026, 9, 15, 17, 42, 3, 250000, tzinfo=timezone.utc)
            ),
            "partidas": [
                {
                    "id": "018f3a5c-7b2e-7c91-9f3d-2a1b4c5d6e81",
                    "linea": 1,
                    "producto_id": "aa11bb22-cc33-4d44-8e55-ff6677889900",
                    "unidad_codigo": "CAJA",
                    "factor_unidad": formatear_cantidad("24"),
                    "cantidad": formatear_cantidad("2"),
                    "cantidad_base": formatear_cantidad("48"),
                    "precio_unitario": "175.0000",
                    "descuento": formatear_dinero("12.50"),
                    "importe": formatear_dinero("337.50"),
                },
                {
                    "id": "018f3a5c-7b2e-7c91-9f3d-2a1b4c5d6e82",
                    "linea": 2,
                    "producto_id": "bb22cc33-dd44-4e55-8f66-001122334455",
                    "unidad_codigo": "PZA",
                    "factor_unidad": formatear_cantidad("1"),
                    "cantidad": formatear_cantidad("5"),
                    "cantidad_base": formatear_cantidad("5"),
                    "precio_unitario": "12.5000",
                    "descuento": formatear_dinero("0"),
                    "importe": formatear_dinero("62.50"),
                },
            ],
        },
    ),
]


def main() -> int:
    vectores = []
    for nombre, descripcion, payload in CASOS:
        vectores.append(
            {
                "nombre": nombre,
                "descripcion": descripcion,
                "payload": payload,
                "canonico": a_texto_canonico(payload),
                "sha256": hash_payload(payload),
            }
        )

    nombres = [v["nombre"] for v in vectores]
    if len(nombres) != len(set(nombres)):
        raise SystemExit("hay nombres de vector repetidos")

    destino = pathlib.Path(__file__).with_name("canonical_vectors.json")
    documento = {
        "version": 1,
        "descripcion": (
            "Vectores del formato canónico compartido entre Dart y Python. "
            "Reglas en contracts/README.md. Regenerar con "
            "python3 contracts/generar_vectores.py"
        ),
        "vectores": vectores,
    }
    destino.write_text(
        json.dumps(documento, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{len(vectores)} vectores escritos en {destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
