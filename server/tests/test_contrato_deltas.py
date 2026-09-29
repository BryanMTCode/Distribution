"""Contrato de la forma de los deltas, entre el servidor y el dispositivo.

El `change_log` se llena con `to_jsonb(fila)`, así que los tipos salen como los
tiene PostgreSQL: los booleanos como `true`, los numéricos como número JSON, y
los importes NO como string. El aplicador de Dart tiene que digerir eso
exactamente, y escribirlo a mano en una prueba de Dart sería adivinar.

Esta prueba genera `contracts/deltas_de_ejemplo.json` a partir de un pull
REAL, y `mobile/packages/dsd_core/test/contrato_deltas_test.dart` lo aplica.
Los identificadores y los cursores se normalizan para que el archivo no cambie
en cada corrida: un fixture que churnea no sirve para detectar cambios de
verdad en el diff.
"""

from __future__ import annotations

import json
import pathlib
import re
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR

pytestmark = pytest.mark.asyncio

ARCHIVO = (
    pathlib.Path(__file__).resolve().parents[2] / "contracts" / "deltas_de_ejemplo.json"
)

# Fijos: el archivo se versiona y no debe cambiar si nada cambió.
PRODUCTO = uuid.UUID("019283e0-0001-7000-8000-000000000001")
CLIENTE = uuid.UUID("019283e0-0002-7000-8000-000000000002")
VENTA = uuid.UUID("019283e0-0003-7000-8000-000000000003")


async def _cab_vendedor(cliente, sesion, semilla) -> dict:
    dispositivo_id = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
             "VALUES (:d,:u,'Moto G54','activo',now())"),
        {"d": dispositivo_id, "u": semilla["vendedor"]},
    )
    await sesion.commit()
    r = await cliente.post("/v1/auth/login", json={
        "codigo": "VEND01", "password": PASSWORD_VENDEDOR, "dispositivo_id": str(dispositivo_id),
    })
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _sembrar(sesion, semilla) -> None:
    """Un catálogo mínimo y un cliente con cartera, con los ids fijos."""
    await sesion.execute(
        text("""
            INSERT INTO productos (id, sku, codigo_barras, nombre, unidad_base,
                                   tasa_iva, activo, creado_en, actualizado_en)
            VALUES (:p, 'FRIJOL-1KG', '7501234567890', 'Frijol negro 1 kg', 'PZA',
                    0.0000, true, now(), now())
        """),
        {"p": PRODUCTO},
    )
    await sesion.execute(
        text("""
            INSERT INTO producto_unidades (producto_id, unidad_codigo, factor,
                                           es_default, activo)
            VALUES (:p, 'PZA', 1, true, true), (:p, 'CAJA', 24, false, true)
        """),
        {"p": PRODUCTO},
    )
    await sesion.execute(
        text("""
            INSERT INTO precios (lista_id, producto_id, unidad_codigo, precio,
                                 precio_minimo, version, actualizado_en)
            VALUES (:l, :p, 'PZA', 25.5000, 24.0000, 1, now())
        """),
        {"l": semilla["lista_precios"], "p": PRODUCTO},
    )
    await sesion.execute(
        text("""
            INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, secuencia,
                                  telefono, calle, numero, colonia, lat, lng,
                                  ubicacion_origen, lista_precios_id,
                                  permite_credito, limite_credito, dias_credito,
                                  bloqueado, estatus, creado_en, actualizado_en)
            VALUES (:c, 'CLI-0001', 'La Esquina de Ñoño 🏪', :r, 3, '5512345678',
                    'Av. Hidalgo', '145', 'Centro', 19.4326000, -99.1332000,
                    'gps', :l, true, 5000.00, 15, false, 'activo', now(), now())
        """),
        {"c": CLIENTE, "r": semilla["ruta"], "l": semilla["lista_precios"]},
    )
    # Una factura abierta, para que el delta de cartera traiga saldo real.
    await sesion.execute(
        text("""
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, almacen_id, tipo,
                                subtotal, total, fecha_dispositivo, fecha_operativa)
            SELECT :v, d.id, 1, 'VEND01-000001', :c, :u, :a, 'credito',
                   1200.00, 1200.00, now(), CURRENT_DATE
              FROM dispositivos d WHERE d.usuario_id = :u LIMIT 1
        """),
        {"v": VENTA, "c": CLIENTE, "u": semilla["vendedor"], "a": semilla["camion"]},
    )
    await sesion.execute(
        text("""
            INSERT INTO cuentas_por_cobrar (venta_id, cliente_id, importe_original,
                                            importe_pagado, fecha_emision,
                                            fecha_vencimiento, estado, actualizado_en)
            VALUES (:v, :c, 1200.00, 0, CURRENT_DATE, CURRENT_DATE + 15,
                    'abierta', now())
        """),
        {"v": VENTA, "c": CLIENTE},
    )

    # -----------------------------------------------------------------------
    # El fixture tiene que salir IGUAL sin importar qué corrió antes.
    # -----------------------------------------------------------------------
    # `listas_precios` es dato de referencia: la migración 0009 la siembra y el
    # TRUNCATE entre pruebas NO la toca, porque el código depende de que exista
    # la lista GENERAL. Pero otras pruebas crean listas propias (la del contrato
    # de Dart, por ejemplo) y esas SOBREVIVEN hasta aquí.
    #
    # Pasó de verdad, y costó un CI rojo: el fixture se regeneró con dos listas
    # porque `test_contrato_dart.py` corre antes por orden alfabético. En mi
    # máquina no se vio, porque había corrido Dart antes que el servidor. Un
    # archivo versionado que depende del orden de las pruebas no sirve para
    # detectar cambios de verdad en el diff.
    await sesion.execute(text("DELETE FROM listas_precios WHERE NOT es_default"))
    # Y los renglones que ESE borrado acaba de generar: el trigger de change_log
    # registra la baja, así que limpiar sin esto dejaría un delta de 'delete' de
    # una lista que el dispositivo nunca tuvo. Se vacía la entidad completa y el
    # backfill de abajo la reconstruye desde cero.
    await sesion.execute(text("DELETE FROM change_log WHERE entidad = 'lista_precios'"))

    # La lista por omisión la siembra la migración 0009, ANTES de que existieran
    # los triggers de change_log: no tiene renglón propio. La 0013 lo siembra, y
    # aquí se aplica para que el fixture traiga el delta de verdad —el aplicador
    # de Dart tiene que digerir ese payload, y escribirlo a mano sería adivinar
    # qué emite to_jsonb.
    from db.sql import leer_sql

    await sesion.execute(text(leer_sql("0013_sembrar_change_log_referencia.sql")))
    await sesion.commit()


_RE_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)

# Los ids que esta prueba fija. Todo otro UUID lo genera la semilla al azar en
# cada corrida (ruta, lista de precios, sucursal), así que se estabiliza.
_FIJOS = {str(PRODUCTO), str(CLIENTE), str(VENTA)}

_VARIABLES = {
    "vencimiento_mas_antiguo", "fecha_emision", "fecha_vencimiento",
    "vigente_desde", "vigente_hasta",
}


def _normalizar(cambios: list[dict]) -> list[dict]:
    """Estabiliza el fixture para que se pueda versionar.

    Se le quitan cursores, fechas e identidades aleatorias. Lo que queda es la
    FORMA: qué entidades se emiten, con qué campos y con qué tipos de JSON. Eso
    es exactamente lo que el aplicador de Dart tiene que digerir, y lo que se
    quiere ver cambiar en un diff.
    """
    alias: dict[str, str] = {}

    def estabilizar(valor: object) -> object:
        if isinstance(valor, str) and _RE_UUID.match(valor) and valor not in _FIJOS:
            # Mismo UUID → mismo alias, en orden de aparición. Conserva las
            # relaciones entre deltas sin fijar el id concreto.
            return alias.setdefault(valor, f"uuid-de-la-semilla-{len(alias) + 1}")
        return valor

    salida = []
    for i, c in enumerate(cambios, start=1):
        payload = {
            campo: estabilizar(valor)
            for campo, valor in (c["payload"] or {}).items()
            if not campo.endswith(("_en", "_at")) and campo not in _VARIABLES
        }
        salida.append(
            {
                "cursor": i,
                "entidad": c["entidad"],
                "entidad_id": estabilizar(c["entidad_id"]),
                "operacion": c["operacion"],
                "payload": payload or None,
            }
        )
    return salida


async def test_generar_y_verificar_deltas(cliente, semilla, sesion):
    """Genera el fixture y comprueba que trae lo que el dispositivo necesita."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    await _sembrar(sesion, semilla)

    r = await cliente.get("/v1/sync/pull?cursor=0&limite=2000", headers=cab)
    assert r.status_code == 200, r.text
    cambios = _normalizar(r.json()["cambios"])

    entidades = {c["entidad"] for c in cambios}
    # Sin 'cartera' el dispositivo nunca sabría el saldo y calcularía el crédito
    # con cero. Ver ADR 0002 y la migración 0011.
    assert "cartera" in entidades
    assert {"producto", "producto_unidad", "precio", "cliente"} <= entidades

    cartera = next(c for c in cambios if c["entidad"] == "cartera")
    assert Decimal(str(cartera["payload"]["saldo"])) == Decimal("1200.00")
    assert Decimal(str(cartera["payload"]["disponible"])) == Decimal("3800.00")

    # Los booleanos salen como booleanos de JSON, no como 0/1: es justo lo que
    # el aplicador de Dart tiene que digerir.
    producto = next(c for c in cambios if c["entidad"] == "producto")
    assert producto["payload"]["activo"] is True

    # EXACTAMENTE una lista de precios, la de omisión.
    #
    # Esta aserción existe por un CI rojo: otra prueba había dejado una lista
    # propia en la base —`listas_precios` es dato de referencia y el TRUNCATE
    # entre pruebas no la toca— y el fixture se regeneró con dos. El síntoma
    # apareció del lado de Dart, como "Bad state: Too many elements", que no
    # apunta a ningún lado. Aquí falla con el nombre del problema.
    listas = [c for c in cambios if c["entidad"] == "lista_precios"]
    assert len(listas) == 1, (
        f"el fixture trae {len(listas)} listas de precios y debe traer una: "
        "alguna prueba dejó listas propias en la base. El archivo se versiona, "
        "así que no puede depender de qué corrió antes."
    )
    assert listas[0]["payload"]["es_default"] is True

    ARCHIVO.write_text(
        json.dumps(
            {
                "version": 1,
                "descripcion": (
                    "Deltas tal como los emite /v1/sync/pull. Los aplica "
                    "mobile/packages/dsd_core/test/contrato_deltas_test.dart. "
                    "Regenerar: pytest tests/test_contrato_deltas.py"
                ),
                "cambios": cambios,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


async def test_el_emoji_del_cliente_viaja_intacto(cliente, semilla, sesion):
    cab = await _cab_vendedor(cliente, sesion, semilla)
    await _sembrar(sesion, semilla)

    r = await cliente.get("/v1/sync/pull?cursor=0&limite=2000", headers=cab)
    nombres = {
        c["payload"]["nombre_comercial"]
        for c in r.json()["cambios"]
        if c["entidad"] == "cliente"
    }
    assert "La Esquina de Ñoño 🏪" in nombres


async def test_la_geografia_generada_no_viaja(cliente, semilla, sesion):
    """El hexadecimal de PostGIS solo engordaría cada delta: el dispositivo ya
    recibe lat y lng."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    await _sembrar(sesion, semilla)

    r = await cliente.get("/v1/sync/pull?cursor=0&limite=2000", headers=cab)
    for c in r.json()["cambios"]:
        if c["payload"]:
            assert "ubicacion" not in c["payload"]
