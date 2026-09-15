#!/usr/bin/env python3
"""Exporta el contrato HTTP y lo degrada a OpenAPI 3.0.

FastAPI emite **OpenAPI 3.1**, y buena parte de los generadores de cliente de
Dart todavía solo digieren 3.0. Este paso corre en CI y produce las dos
versiones: `openapi.json` (3.1, la real) y `openapi-3.0.json` (la que consume el
generador del cliente Dart).

Descubrir esto en la Fase 3, con 40 endpoints, significa escribir los modelos de
Dart a mano.

Uso:  python3 contracts/exportar_openapi.py
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "server"))


def _degradar(nodo: Any) -> Any:
    """Convierte construcciones de JSON Schema 2020-12 a las de OpenAPI 3.0."""
    if isinstance(nodo, list):
        return [_degradar(x) for x in nodo]
    if not isinstance(nodo, dict):
        return nodo

    n = {k: _degradar(v) for k, v in nodo.items() if k != "$schema"}

    # 3.1: type: ["string", "null"]  →  3.0: type: string + nullable: true
    tipo = n.get("type")
    if isinstance(tipo, list):
        sin_null = [t for t in tipo if t != "null"]
        if len(tipo) != len(sin_null):
            n["nullable"] = True
        n["type"] = sin_null[0] if len(sin_null) == 1 else sin_null

    # 3.1: anyOf: [{...}, {"type": "null"}]  →  3.0: {...} + nullable: true
    for clave in ("anyOf", "oneOf"):
        if clave in n and isinstance(n[clave], list):
            ramas = [r for r in n[clave] if r != {"type": "null"}]
            if len(ramas) != len(n[clave]):
                n["nullable"] = True
            if len(ramas) == 1 and clave == "anyOf":
                unica = ramas[0]
                del n[clave]
                # Un $ref no admite hermanos en 3.0: se envuelve en allOf.
                if "$ref" in unica:
                    n["allOf"] = [unica]
                else:
                    n = {**unica, **{k: v for k, v in n.items() if k not in unica}}
            else:
                n[clave] = ramas

    # 3.1: exclusiveMinimum numérico  →  3.0: minimum + bandera booleana
    for limite, bandera in (("exclusiveMinimum", "minimum"), ("exclusiveMaximum", "maximum")):
        if isinstance(n.get(limite), (int, float)):
            n[bandera] = n[limite]
            n[limite] = True

    if "const" in n:
        n["enum"] = [n.pop("const")]

    if isinstance(n.get("examples"), list) and n["examples"]:
        n["example"] = n["examples"][0]
        del n["examples"]

    return n


def main() -> int:
    from app.main import crear_app  # noqa: PLC0415 — necesita el sys.path de arriba

    esquema = crear_app().openapi()
    destino = pathlib.Path(__file__).parent

    (destino / "openapi.json").write_text(
        json.dumps(esquema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    degradado = _degradar(copy.deepcopy(esquema))
    degradado["openapi"] = "3.0.3"
    (destino / "openapi-3.0.json").write_text(
        json.dumps(degradado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"{len(esquema['paths'])} rutas exportadas")
    print(f"  {destino / 'openapi.json'}      (3.1, contrato real)")
    print(f"  {destino / 'openapi-3.0.json'}  (para el generador de Dart)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
