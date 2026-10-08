"""La guardia entre los dos lenguajes: lo que uno manda, el otro lo sabe recibir.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTO TIENE PRUEBAS
────────────────────────────────────────────────────────────────────────────
El servidor (Python + PostgreSQL) y el teléfono (Dart) se hablan por nombres:

· El servidor publica en `change_log` con un nombre de entidad —'cliente',
  'carga', 'ajuste_camion'— y el teléfono decide qué hacer con cada uno en
  `AplicadorDeltas._aplicarUno`. Un nombre que el teléfono no conoce no truena:
  cae en `deltas_desconocidos`, el cursor avanza y el dato **no se aplica
  nunca**. Para la oficina el cambio se guardó; para el vendedor nunca pasó.
· El teléfono manda operaciones con un `tipo` —'venta.crear', 'merma.crear'— y
  el servidor busca su manejador. Un tipo sin manejador va a cuarentena: la venta
  ocurrió en la calle y el servidor no la registra.

Ninguna de las dos fallas se ve en las pruebas de cada lado por separado: cada
lado es correcto con lo que conoce. Esta prueba lee los dos y los compara. Se
escribió en la auditoría panel → teléfono de octubre de 2026.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import text

RAIZ = Path(__file__).resolve().parents[2]
DSD_CORE = RAIZ / "mobile" / "packages" / "dsd_core" / "lib"
APLICADOR = DSD_CORE / "src" / "aplicador_deltas.dart"

# Lo que el teléfono acepta a propósito sin aplicarlo. Cada renglón explica por
# qué; agregar uno es una decisión, no una forma de silenciar la prueba.
ACEPTADAS_SIN_APLICAR = {
    # El panel no tiene pantalla de promociones y la regla sellada es que el
    # vendedor no da descuentos; el teléfono las recibe para no llenar
    # `deltas_desconocidos` con algo que sí sabe que viene.
    "promocion",
}


async def _entidades_publicadas(sesion) -> set[str]:
    """Todos los nombres de entidad que algún disparador escribe en `change_log`.

    Se leen del catálogo de PostgreSQL, no de los .sql: lo que cuenta es lo que
    quedó instalado después de todas las migraciones. Dos formas:

    · Literal en la función: `INSERT INTO change_log (...) VALUES ('cliente', ...`
      o `... SELECT 'cliente', ...`.
    · Genérica (`fn_registrar_cambio`, `fn_registrar_cambio_catalogo_texto`): el
      nombre es el primer argumento del disparador, `TG_ARGV[0]`.
    """
    funciones = (
        await sesion.execute(
            text(
                """
                SELECT p.oid, p.prosrc
                  FROM pg_proc p
                  JOIN pg_namespace n ON n.oid = p.pronamespace
                 WHERE n.nspname = 'public'
                   AND p.prosrc ILIKE '%INSERT INTO change_log%'
                """
            )
        )
    ).all()

    literal = re.compile(
        r"INSERT INTO change_log\s*\([^)]*\)\s*(?:VALUES\s*\(\s*|SELECT\s+)'([a-z_]+)'",
        re.IGNORECASE,
    )
    entidades: set[str] = set()
    genericas = []
    for oid, fuente in funciones:
        entidades |= set(literal.findall(fuente))
        if re.search(r"VALUES\s*\(\s*TG_ARGV\[0\]", fuente, re.IGNORECASE):
            genericas.append(oid)

    for oid in genericas:
        argumentos = (
            await sesion.execute(
                text("SELECT tgargs FROM pg_trigger WHERE tgfoid = :f AND NOT tgisinternal"),
                {"f": oid},
            )
        ).scalars().all()
        for crudo in argumentos:
            # `tgargs` es bytea: los argumentos separados por NUL.
            entidades.add(bytes(crudo).split(b"\x00")[0].decode())
    return entidades


def _casos_del_aplicador() -> set[str]:
    """Los nombres que `_aplicarUno` sabe despachar."""
    fuente = APLICADOR.read_text(encoding="utf-8")
    inicio = fuente.index("bool _aplicarUno(")
    fin = fuente.index("};", inicio)
    return set(re.findall(r"'([a-z_]+)'\s*=>", fuente[inicio:fin]))


def _tipos_que_manda_el_telefono() -> set[str]:
    tipos: set[str] = set()
    for archivo in DSD_CORE.rglob("*.dart"):
        fuente = archivo.read_text(encoding="utf-8")
        for bloque in re.finditer(r"OperacionLocal\((.*?)\)", fuente, re.DOTALL):
            tipos |= set(re.findall(r"tipo:\s*'([a-z_.]+)'", bloque.group(1)))
    return tipos


@pytest.mark.asyncio
async def test_todo_lo_que_publica_el_servidor_el_telefono_lo_sabe_aplicar(sesion, esquema):  # noqa: ARG001
    publicadas = await _entidades_publicadas(sesion)
    # Si la lectura del catálogo se rompe, la prueba pasaría sin comparar nada.
    assert {"cliente", "producto", "carga", "identidad", "motivo_merma"} <= publicadas, (
        f"la lectura de los disparadores no encontró lo básico: {sorted(publicadas)}"
    )

    casos = _casos_del_aplicador()
    assert "cliente" in casos, "no se pudo leer el switch de `_aplicarUno`"

    sin_caso = publicadas - casos
    assert sin_caso == set(), (
        "el servidor publica entidades que el teléfono no sabe aplicar; caerían en "
        f"`deltas_desconocidos` y el vendedor nunca vería el cambio: {sorted(sin_caso)}"
    )


def test_lo_que_el_telefono_acepta_sin_aplicar_esta_declarado():
    """`'promocion' => true` es aceptar sin aplicar. Que sea visible aquí."""
    fuente = APLICADOR.read_text(encoding="utf-8")
    inicio = fuente.index("bool _aplicarUno(")
    fin = fuente.index("};", inicio)
    sin_aplicar = set(re.findall(r"'([a-z_]+)'\s*=>\s*true", fuente[inicio:fin]))
    assert sin_aplicar == ACEPTADAS_SIN_APLICAR


def test_todo_lo_que_manda_el_telefono_el_servidor_lo_sabe_procesar():
    from app.infra.sync import manejadores

    tipos = _tipos_que_manda_el_telefono()
    assert {"venta.crear", "merma.crear"} <= tipos, (
        f"la lectura del código Dart no encontró lo básico: {sorted(tipos)}"
    )

    sin_manejador = tipos - set(manejadores._REGISTRO)
    assert sin_manejador == set(), (
        "el teléfono manda operaciones que el servidor no conoce; irían a cuarentena "
        f"con la mercancía ya entregada: {sorted(sin_manejador)}"
    )
