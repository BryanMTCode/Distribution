"""La revisión de cada vendedor antes de salir: `scripts/piloto_listo.sh`.

«Dale una revisada a todas las conexiones de la app y cosas necesarias para
operar, porque hoy ya operarán normalmente» (octubre 2026, ADR 0002 §171).

La revisión era la del piloto: el usuario, su camión, su ruta, su teléfono, sus
clientes y el respaldo. Le faltaba lo que el día de operación pide y se descubre
a las seis de la mañana: los folios que le quedan al teléfono, la versión de la
app, el corte de ayer sin cerrar, la carga y lo que quedó en cuarentena. Y se
revisaba un vendedor a la vez; ahora `todos` los revisa de una vez.

Se corre el script de verdad, con `psql`, contra la base de pruebas.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from tests.conftest import URL_PRUEBAS

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(shutil.which("psql") is None, reason="hace falta psql"),
]

_RAIZ = Path(__file__).resolve().parents[2]


def _correr(*argumentos: str, respaldos: Path) -> tuple[int, str]:
    r = subprocess.run(
        ["bash", str(_RAIZ / "scripts" / "piloto_listo.sh"), *argumentos],
        capture_output=True,
        text=True,
        timeout=120,
        env={
            **os.environ,
            "DSD_DATABASE_URL": URL_PRUEBAS.replace("+psycopg", ""),
            "DSD_RESPALDOS": str(respaldos),
        },
    )
    return r.returncode, r.stdout


async def _telefono(sesion, semilla, *, version: str | None = "0.1.0+50") -> uuid.UUID:
    dispositivo = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en, "
            "                          app_version, ultima_sync_push_en) "
            "VALUES (:d, :u, 'HY T4', 'activo', now(), :v, now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"], "v": version},
    )
    return dispositivo


async def _folios(sesion, dispositivo, tipo: str, *, consumido: int) -> None:
    await sesion.execute(
        text(
            "INSERT INTO folios_rangos (dispositivo_id, documento_tipo, desde, hasta, "
            "                           consumido_hasta) "
            "VALUES (:d, :t, 1, 1000, :c)"
        ),
        {"d": dispositivo, "t": tipo, "c": consumido},
    )


async def test_dice_lo_que_le_falta_para_vender_hoy(sesion, semilla, tmp_path):
    dispositivo = await _telefono(sesion, semilla)
    await _folios(sesion, dispositivo, "venta", consumido=800)
    await _folios(sesion, dispositivo, "merma", consumido=0)
    # Sin folios de visita: el vínculo no alcanzó a traerlos.
    await sesion.execute(
        text(
            "INSERT INTO cortes_vendedor (id, vendedor_id, dispositivo_id, fecha_operativa, "
            "                             efectivo_declarado, fecha_dispositivo, estado) "
            "VALUES (gen_random_uuid(), :v, :d, :ayer, 1500, now(), 'pendiente')"
        ),
        {"v": semilla["vendedor"], "d": dispositivo, "ayer": date.today() - timedelta(days=1)},
    )
    await sesion.execute(
        text(
            "INSERT INTO sync_cuarentena (operacion_id, dispositivo_id, usuario_id, tipo, "
            "                             payload, hash_payload, error_codigo, "
            "                             error_mensaje, estado) "
            "VALUES (:o, :d, :u, 'venta.crear', '{}', 'x', 'conflicto_de_datos', "
            "        'la venta referencia un cliente que no existe', 'pendiente')"
        ),
        {"o": uuid.uuid4(), "d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.commit()

    codigo, salida = _correr("VEND01", respaldos=tmp_path)

    assert codigo == 1, salida
    assert "Para vender hoy" in salida
    assert "le quedan 200 folios de venta: al sincronizar se le alargan solos" in salida
    assert "su teléfono no tiene folios de visita" in salida
    assert "folios de merma: le quedan 1000" in salida
    assert "tiene la app 0.1.0+50 y la última es la" in salida
    assert "1 corte(s) de días anteriores sin cerrar" in salida
    assert "su camión está vacío y no tiene carga" in salida
    assert "1 operación(es) suya(s) en cuarentena" in salida
    # El lunes sin señal: lo que no ha bajado se dice antes de que se vaya.
    assert "a su teléfono le faltan" in salida
    assert "cambio(s) por bajar: plan de visita, carga o tiendas" in salida


async def test_el_vendedor_listo_sale_en_verde(sesion, semilla, tmp_path):
    version = next(
        linea.split(":", 1)[1].strip()
        for linea in (_RAIZ / "mobile/app/pubspec.yaml").read_text().splitlines()
        if linea.startswith("version:")
    )
    dispositivo = await _telefono(sesion, semilla, version=version)
    for tipo in ("venta", "no_drop", "merma"):
        await _folios(sesion, dispositivo, tipo, consumido=10)
    await sesion.execute(
        text(
            "INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id, "
            "                    vendedor_id, ruta_id, fecha_operativa, estado) "
            "VALUES (gen_random_uuid(), 'CG-0001', :b, :c, :v, :r, CURRENT_DATE, "
            "        'confirmada')"
        ),
        {"b": semilla["bodega"], "c": semilla["camion"], "v": semilla["vendedor"],
         "r": semilla["ruta"]},
    )
    # Ya bajó todo lo que hay para él.
    await sesion.execute(
        text("UPDATE dispositivos SET ultimo_cursor_pull = (SELECT max(cursor) FROM change_log), "
             "       ultima_sync_pull_en = now() WHERE id = :d"),
        {"d": dispositivo},
    )
    await sesion.commit()

    _, salida = _correr("VEND01", respaldos=tmp_path)

    seccion = salida.split("Para vender hoy", 1)[1].split("Sus clientes", 1)[0]
    assert "FALLA" not in seccion and "NOTA" not in seccion, seccion
    assert f"app {version}" in seccion
    assert "carga CG-0001 (en el camión)" in seccion
    assert "nada suyo en cuarentena" in seccion
    assert "su teléfono ya bajó todo" in seccion and "puede salir sin señal" in seccion


async def test_todos_revisa_a_cada_vendedor_y_dice_quien_falta(sesion, semilla, tmp_path):
    codigo, salida = _correr("todos", respaldos=tmp_path)

    assert codigo == 1
    assert "Todos los vendedores" in salida
    # Sin teléfono, sin carga y sin respaldo: le falta.
    assert "POR ARREGLAR VEND01" in salida


async def test_dice_cuantas_tiendas_le_salen_y_cuantas_le_tocan_hoy(sesion, semilla, tmp_path):
    """«Verifica cuántas tiendas aparecen a cada vendedor» (ADR 0002 §174).

    Antes contaba `clientes.activo`, que no existe: la consulta fallaba callada y
    la revisión decía siempre «sus rutas no tienen clientes».
    """
    hoy = (
        await sesion.execute(
            text("SELECT extract(dow FROM now() AT TIME ZONE 'America/Mazatlan')::int")
        )
    ).scalar_one()
    for n in range(7):
        tienda = uuid.uuid4()
        await sesion.execute(
            text(
                "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, "
                "                      creado_en, actualizado_en) "
                "VALUES (:c, :codigo, 'Abarrotes', :r, now(), now())"
            ),
            {"c": tienda, "codigo": f"PL-{n}", "r": semilla["ruta"]},
        )
        # Tres le tocan hoy; las otras cuatro, mañana.
        await sesion.execute(
            text("INSERT INTO clientes_frecuencia (cliente_id, dia_semana) VALUES (:c, :d)"),
            {"c": tienda, "d": hoy if n < 3 else (hoy + 1) % 7},
        )
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, creado_en, actualizado_en) "
            "VALUES (gen_random_uuid(), 'PL-SR', 'Sin ruta', now(), now())"
        )
    )
    await sesion.commit()

    _, salida = _correr("VEND01", respaldos=tmp_path)
    seccion = salida.split("Sus clientes", 1)[1].split("El respaldo", 1)[0]
    assert "7 tiendas en sus rutas; hoy (" in seccion, seccion
    assert "le tocan 3" in seccion
    assert "R04: 7, hoy 3" in seccion
    assert "1 tienda(s) sin ruta: les salen a todos los vendedores" in seccion
    assert "sus rutas no tienen clientes" not in seccion
    assert "su teléfono todavía no revisa cuáles tiendas son suyas" in seccion

    # El teléfono pregunta (la +59) y queda anotado lo que soltó.
    dispositivo = await _telefono(sesion, semilla)
    await sesion.execute(
        text("UPDATE dispositivos SET tiendas_en_telefono = 14, tiendas_soltadas = 7, "
             "       tiendas_revisadas_en = now() WHERE id = :d"),
        {"d": dispositivo},
    )
    await sesion.commit()
    _, salida = _correr("VEND01", respaldos=tmp_path)
    assert "su teléfono enseñaba 14 tiendas y soltó 7 que no eran suyas" in salida

    _, resumen = _correr("todos", respaldos=tmp_path)
    renglon = next(r for r in resumen.splitlines() if "VEND01" in r and "tiendas" in r)
    assert "R04: 7 tiendas, hoy 3" in renglon
