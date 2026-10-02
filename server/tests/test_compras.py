"""Compras: proveedores, costo promedio ponderado y cuentas por pagar.

────────────────────────────────────────────────────────────────────────────
LA PRUEBA QUE MÁS IMPORTA DE ESTE ARCHIVO
────────────────────────────────────────────────────────────────────────────
`test_el_costo_no_viaja_al_telefono`. Lo obvio al construir esto era
`productos.costo_promedio`, y habría sido una fuga silenciosa: la migración 0010
publica `to_jsonb(NEW)` —LA FILA COMPLETA— de `productos` en `change_log` con
`ruta_id = NULL`, o sea a todos los dispositivos. El margen de la empresa habría
viajado en el siguiente pull al SQLite de cada teléfono.

Nadie lo nota revisando el diff: la columna se agrega en un lugar y el dato sale
por otro, diecisiete migraciones más atrás. De ahí `producto_costos`, tabla
aparte y sin disparador, y de ahí esta prueba.

Y las otras cuatro:

1. El promedio ponderado es ponderado: 100@10 + 100@20 = 15, no 20 ni 15.5.
2. Se pondera contra lo que había ANTES de la entrada, no después.
3. Un renglón sin costo se valúa al promedio vigente y NO lo mueve.
4. Un pago nunca excede el saldo, y dos pagos simultáneos no lo rebasan.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text

from app.domain.compras import estado_de_cuenta, importe_de_renglon, ponderar
from tests.conftest import PASSWORD_VENDEDOR, solo_texto


# ===========================================================================
# La aritmética, como función pura
# ===========================================================================
def test_el_promedio_es_ponderado_no_el_ultimo_ni_la_media():
    """100 piezas a $10 más 100 a $20 son $15, y la distinción no es académica:
    el último costo diría $20 y revaluaría de golpe todo lo viejo del anaquel."""
    r = ponderar(
        unidades_en_mano=Decimal(100),
        costo_actual=Decimal("10"),
        unidades_que_entran=Decimal(100),
        costo_que_entra=Decimal("20"),
    )
    assert r.costo_promedio == Decimal("15.0000")
    assert r.hubo_ponderacion is True
    assert r.unidades_al_ponderar == Decimal(100)


def test_el_promedio_pondera_de_verdad_con_cantidades_distintas():
    """900@10 + 100@20 = 11, no 15: la media simple sería el error fácil."""
    r = ponderar(
        unidades_en_mano=Decimal(900),
        costo_actual=Decimal("10"),
        unidades_que_entran=Decimal(100),
        costo_que_entra=Decimal("20"),
    )
    assert r.costo_promedio == Decimal("11.0000")


def test_la_primera_compra_fija_el_costo():
    r = ponderar(
        unidades_en_mano=Decimal(0),
        costo_actual=None,
        unidades_que_entran=Decimal(240),
        costo_que_entra=Decimal("12.3333"),
    )
    assert r.costo_promedio == Decimal("12.3333")
    assert r.hubo_ponderacion is False
    assert "no hay nada contra qué ponderar" in r.explicacion


def test_con_existencia_negativa_el_promedio_es_el_costo_que_entra():
    """Ponderar contra un negativo daría un costo negativo con cara de dato
    bueno, y un costo negativo recorre el sistema hasta ser un margen inventado."""
    r = ponderar(
        unidades_en_mano=Decimal(-5),
        costo_actual=Decimal("10"),
        unidades_que_entran=Decimal(100),
        costo_que_entra=Decimal("20"),
    )
    assert r.costo_promedio == Decimal("20.0000")
    assert r.hubo_ponderacion is False


def test_si_nada_entra_el_promedio_no_se_mueve():
    """Es el caso del inventario inicial sin costo: se valúa al promedio
    vigente, que es el tratamiento estándar de lo que aparece en un conteo."""
    r = ponderar(
        unidades_en_mano=Decimal(100),
        costo_actual=Decimal("10"),
        unidades_que_entran=Decimal(0),
        costo_que_entra=Decimal("99"),
    )
    assert r.costo_promedio == Decimal("10.0000")


def test_el_importe_redondea_una_sola_vez_y_al_final():
    """24 piezas a $12.3333 son $296.00 exactos — el ejemplo del ADR 0002 §2.

    Redondear el costo a dos decimales primero daría 24 × 12.33 = $295.92, y esa
    diferencia de ocho centavos por renglón es la que no cuadra en una factura
    de cuarenta renglones.
    """
    assert importe_de_renglon(Decimal(24), Decimal("12.3333")) == Decimal("296.00")


def test_el_estado_de_la_cuenta_exige_igualdad_exacta():
    """Un centavo de diferencia dejaría la cuenta abierta para siempre y nadie
    entendería por qué sigue en la lista de pendientes."""
    assert estado_de_cuenta(Decimal(100), Decimal(0)) == "abierta"
    assert estado_de_cuenta(Decimal(100), Decimal("99.99")) == "parcial"
    assert estado_de_cuenta(Decimal(100), Decimal(100)) == "liquidada"
    assert estado_de_cuenta(Decimal(100), Decimal(101)) == "liquidada"


# ===========================================================================
# Andamios
# ===========================================================================
async def _entrar(cliente, codigo: str = "ADMIN01") -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": codigo, "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


def _csrf(cliente) -> str:
    import hashlib
    import hmac

    from app.core.config import obtener_config

    cookie = cliente.cookies.get("dsd_panel", "")
    return hmac.new(
        obtener_config().jwt_secreto.encode(),
        f"csrf:{cookie}".encode(),
        hashlib.sha256,
    ).hexdigest()


async def _producto(sesion, *, sku: str = "COCA600", factor: int = 24) -> uuid.UUID:
    categoria = (
        await sesion.execute(text("SELECT id FROM categorias LIMIT 1"))
    ).scalar()
    if categoria is None:
        categoria = uuid.uuid4()
        await sesion.execute(
            text("INSERT INTO categorias (id, codigo, nombre) VALUES (:i,'ABA','Ab')"),
            {"i": categoria},
        )
    identificador = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, categoria_id, unidad_base, "
            "                       creado_en, actualizado_en) "
            "VALUES (:i, :sku, :nom, :c, 'PZA', now(), now())"
        ),
        {"i": identificador, "sku": sku, "nom": f"Producto {sku}", "c": categoria},
    )
    for codigo, f in (("PZA", 1), ("CAJA", factor)):
        await sesion.execute(
            text(
                "INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, "
                "                               es_default, activo) "
                "VALUES (:p, :u, :f, :pred, true)"
            ),
            {"p": identificador, "u": codigo, "f": f, "pred": codigo == "CAJA"},
        )
    await sesion.commit()
    return identificador


async def _proveedor(cliente, *, codigo="ABARR01", nombre="Abarrotes del Centro",
                     dias="30"):
    return await cliente.post(
        "/panel/compras/proveedor",
        data={"csrf": _csrf(cliente), "codigo": codigo, "nombre": nombre,
              "dias_credito": dias, "rfc": "abc123456xy1", "contacto": "Don Chuy",
              "telefono": "", "notas": ""},
        follow_redirects=False,
    )


async def _id_proveedor(sesion, codigo="ABARR01") -> uuid.UUID:
    return (
        await sesion.execute(
            text("SELECT id FROM proveedores WHERE codigo = :c"), {"c": codigo}
        )
    ).scalar_one()


async def _comprar(
    cliente, semilla, sesion, *, sku="COCA600", bultos="10", costo="296.00",
    motivo="compra", proveedor=None, referencia="F-45821", fecha="",
    confirmar=True, nota="",
):
    """Una entrada completa, del borrador a la confirmación."""
    abrir = await cliente.post(
        "/panel/entradas/nueva",
        data={
            "csrf": _csrf(cliente), "motivo": motivo,
            "almacen_destino_id": str(semilla["bodega"]),
            "proveedor_id": str(proveedor) if proveedor else "",
            "proveedor": "" if proveedor else "Central de abastos",
            "referencia": referencia, "fecha": fecha,
            "nota": nota or ("conteo inicial" if motivo == "inicial" else ""),
        },
        follow_redirects=False,
    )
    assert abrir.status_code == 303, abrir.headers.get("location")
    entrada = uuid.UUID(
        abrir.headers["location"].split("/panel/entradas/")[1].split("?")[0]
    )
    renglon = await cliente.post(
        f"/panel/entradas/{entrada}/renglon",
        data={"csrf": _csrf(cliente), "producto": sku, "unidad_codigo": "CAJA",
              "cantidad": bultos, "costo": costo, "lote": "", "caducidad": ""},
        follow_redirects=False,
    )
    if confirmar:
        await cliente.post(
            f"/panel/entradas/{entrada}/confirmar",
            data={"csrf": _csrf(cliente)}, follow_redirects=False,
        )
    return entrada, renglon


async def _costo(sesion, producto) -> dict | None:
    return (
        await sesion.execute(
            text(
                "SELECT costo_promedio, ultimo_costo, ultima_compra_en, "
                "       unidades_al_ponderar FROM producto_costos "
                " WHERE producto_id = :p"
            ),
            {"p": producto},
        )
    ).mappings().first()


# ===========================================================================
# LA PRUEBA QUE MÁS IMPORTA: el costo no sale de la oficina
# ===========================================================================
async def test_el_costo_no_viaja_al_telefono(sesion):
    """`producto_costos` no lleva disparador de change_log, y no puede llevarlo.

    `fn_registrar_cambio` publica la FILA COMPLETA, así que un disparador en
    esta tabla mandaría el costo promedio a todos los dispositivos en el
    siguiente pull. Si alguien lo agrega, esta prueba se pone roja.
    """
    disparadores = (
        await sesion.execute(
            text(
                "SELECT t.tgname FROM pg_trigger t "
                "  JOIN pg_class c ON c.oid = t.tgrelid "
                " WHERE NOT t.tgisinternal "
                "   AND c.relname IN ('producto_costos','cuentas_por_pagar',"
                "                     'pagos_proveedor','proveedores')"
            )
        )
    ).scalars().all()
    assert disparadores == [], (
        f"estas tablas no deben publicarse al teléfono: {disparadores}"
    )


async def test_productos_no_tiene_columna_de_costo(sesion):
    """Y es la otra mitad de la misma defensa: `productos` SÍ se publica entera.

    La prueba afirma la ausencia porque el error que previene es agregar la
    columna «donde se ve natural» — y el dato saldría por un disparador escrito
    diecisiete migraciones antes.
    """
    columnas = (
        await sesion.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                " WHERE table_name = 'productos'"
            )
        )
    ).scalars().all()
    assert not [c for c in columnas if "costo" in c], (
        "`productos` se publica completa a todos los dispositivos: el costo "
        "va en `producto_costos`"
    )
    # Y se confirma que esa tabla sí se publica, que es la razón de todo esto.
    publicada = (
        await sesion.execute(
            text(
                "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                " WHERE NOT t.tgisinternal AND c.relname = 'productos'"
            )
        )
    ).scalar()
    assert publicada >= 1


# ===========================================================================
# El costo, de punta a punta
# ===========================================================================
async def test_la_primera_compra_fija_el_promedio_y_el_ultimo_costo(
    cliente, sesion, semilla
):
    """10 cajas de 24 a $296 la caja = $12.3333 la pieza."""
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _proveedor(cliente)
    await _comprar(cliente, semilla, sesion,
                   proveedor=await _id_proveedor(sesion))

    costo = await _costo(sesion, producto)
    assert costo is not None
    assert costo["costo_promedio"] == Decimal("12.3333")
    assert costo["ultimo_costo"] == Decimal("12.3333")
    assert costo["ultima_compra_en"] == date.today()


async def test_la_segunda_compra_pondera_contra_lo_que_habia(
    cliente, sesion, semilla
):
    """Y pondera contra lo de ANTES de la entrada, no contra lo de después.

    Si se ponderara contra la existencia ya actualizada, las unidades que entran
    se contarían dos veces y el promedio se quedaría a medio camino del costo
    nuevo. Con 240@10 + 240@20 el resultado correcto es 15; el error daría
    16.67.
    """
    await _entrar(cliente)
    producto = await _producto(sesion, factor=24)
    await _proveedor(cliente)
    proveedor = await _id_proveedor(sesion)

    # 10 cajas a $240 = $10 la pieza.
    await _comprar(cliente, semilla, sesion, bultos="10", costo="240",
                   proveedor=proveedor)
    assert (await _costo(sesion, producto))["costo_promedio"] == Decimal("10.0000")

    # Otras 10 cajas a $480 = $20 la pieza. 240@10 + 240@20 = 15.
    await _comprar(cliente, semilla, sesion, bultos="10", costo="480",
                   proveedor=proveedor, referencia="F-2")
    costo = await _costo(sesion, producto)
    assert costo["costo_promedio"] == Decimal("15.0000")
    assert costo["ultimo_costo"] == Decimal("20.0000")
    assert costo["unidades_al_ponderar"] == Decimal("240.000")


async def test_un_renglon_sin_costo_se_valua_al_promedio_y_no_lo_mueve(
    cliente, sesion, semilla
):
    """El inventario inicial o el ajuste por conteo: nadie compró esas unidades.

    Guardarlas en cero arrastraría el promedio a la baja con un costo que nadie
    pagó, y es el error que un `COALESCE(costo, 0)` cometería sin avisar.
    """
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _proveedor(cliente)
    await _comprar(cliente, semilla, sesion, bultos="10", costo="240",
                   proveedor=await _id_proveedor(sesion))
    assert (await _costo(sesion, producto))["costo_promedio"] == Decimal("10.0000")

    # Aparecen 10 cajas en un conteo, sin costo.
    await _comprar(cliente, semilla, sesion, bultos="10", costo="",
                   motivo="ajuste", proveedor=None, referencia="",
                   nota="conteo del anaquel 2")
    costo = await _costo(sesion, producto)
    assert costo["costo_promedio"] == Decimal("10.0000")   # intacto
    # Y el documento sí se valuó, al promedio: 240 piezas × $10.
    entrada = (
        await sesion.execute(
            text("SELECT importe_total FROM entradas WHERE motivo = 'ajuste'")
        )
    ).scalar()
    assert Decimal(entrada) == Decimal("2400.00")


async def test_un_producto_sin_costo_ni_promedio_no_se_inventa_un_cero(
    cliente, sesion, semilla
):
    """Queda sin costo, y el valor de inventario lo cuenta aparte."""
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _comprar(cliente, semilla, sesion, bultos="10", costo="",
                   motivo="inicial", proveedor=None, referencia="",
                   nota="arranque del sistema")

    assert await _costo(sesion, producto) is None
    texto = solo_texto(await cliente.get("/panel/compras"))
    assert "productos con existencia y sin costo" in texto
    assert "No se valúan en cero" in texto


async def test_una_compra_sin_costo_no_se_confirma(cliente, sesion, semilla):
    """Sin costo no hay cuenta por pagar ni promedio: el módulo no serviría."""
    await _entrar(cliente)
    await _producto(sesion)
    await _proveedor(cliente)
    entrada, renglon = await _comprar(
        cliente, semilla, sesion, costo="", proveedor=await _id_proveedor(sesion),
        confirmar=False,
    )
    # El renglón ni siquiera entra: el costo es obligatorio al capturar.
    assert "obligatorio" in renglon.headers["location"]
    assert (
        await sesion.execute(text("SELECT count(*) FROM entrada_detalle"))
    ).scalar() == 0


async def test_un_costo_de_cero_se_rechaza(cliente, sesion, semilla):
    """Un cero no es «gratis», es un costo que falta — y arrastra el promedio."""
    await _entrar(cliente)
    await _producto(sesion)
    await _proveedor(cliente)
    _, renglon = await _comprar(
        cliente, semilla, sesion, costo="0", proveedor=await _id_proveedor(sesion),
        confirmar=False,
    )
    assert "arrastrar" in renglon.headers["location"]


async def test_el_costo_se_captura_por_bulto_y_se_guarda_por_unidad(
    cliente, sesion, semilla
):
    """Como viene en la factura. Guardarlo por bulto haría que el promedio de un
    producto que se compra en caja y se vende en pieza fuera 24 veces el real."""
    await _entrar(cliente)
    await _producto(sesion, factor=24)
    await _proveedor(cliente)
    _, renglon = await _comprar(
        cliente, semilla, sesion, bultos="10", costo="296.00",
        proveedor=await _id_proveedor(sesion),
    )
    fila = (
        await sesion.execute(
            text("SELECT cantidad, costo_unitario FROM entrada_detalle")
        )
    ).mappings().one()
    assert fila["cantidad"] == Decimal("240.000")
    assert fila["costo_unitario"] == Decimal("12.3333")
    # Y el aviso dice las dos cifras, porque quien captura revisa contra la
    # factura que habla de cajas.
    destino = renglon.headers["location"]
    assert "296.00" in destino
    assert "12.3333" in destino


async def test_dos_capturas_del_mismo_renglon_ponderan_el_costo(
    cliente, sesion, semilla
):
    """Dos tarimas del mismo producto a precios distintos en la misma factura.

    Quedarse con el último costo aplicaría a las 240 piezas de la primera tarima
    el precio de la segunda, que es justo el error que el promedio ponderado
    existe para no cometer.
    """
    await _entrar(cliente)
    await _producto(sesion, factor=24)
    await _proveedor(cliente)
    proveedor = await _id_proveedor(sesion)

    abrir = await cliente.post(
        "/panel/entradas/nueva",
        data={"csrf": _csrf(cliente), "motivo": "compra",
              "almacen_destino_id": str(semilla["bodega"]),
              "proveedor_id": str(proveedor), "proveedor": "",
              "referencia": "F-1", "fecha": "", "nota": ""},
        follow_redirects=False,
    )
    entrada = uuid.UUID(
        abrir.headers["location"].split("/panel/entradas/")[1].split("?")[0]
    )
    for bultos, costo in (("10", "240"), ("10", "480")):
        await cliente.post(
            f"/panel/entradas/{entrada}/renglon",
            data={"csrf": _csrf(cliente), "producto": "COCA600",
                  "unidad_codigo": "CAJA", "cantidad": bultos, "costo": costo,
                  "lote": "", "caducidad": ""},
            follow_redirects=False,
        )
    fila = (
        await sesion.execute(
            text("SELECT cantidad, costo_unitario FROM entrada_detalle")
        )
    ).mappings().one()
    assert fila["cantidad"] == Decimal("480.000")
    assert fila["costo_unitario"] == Decimal("15.0000")   # ponderado, no 20


# ===========================================================================
# El valor del inventario
# ===========================================================================
async def test_el_valor_del_inventario_incluye_los_camiones(
    cliente, sesion, semilla
):
    """Un camión cargado trae inventario de la empresa aunque esté en la calle."""
    await _entrar(cliente)
    producto = await _producto(sesion)
    await _proveedor(cliente)
    await _comprar(cliente, semilla, sesion, bultos="10", costo="240",
                   proveedor=await _id_proveedor(sesion))

    # 48 piezas se van al camión, directo en la tabla: lo que se prueba es la
    # valuación, y el camino de la carga ya tiene sus pruebas.
    await sesion.execute(
        text(
            "UPDATE existencias SET cantidad = cantidad - 48 "
            " WHERE almacen_id = :b AND producto_id = :p"
        ),
        {"b": semilla["bodega"], "p": producto},
    )
    await sesion.execute(
        text(
            "INSERT INTO existencias (almacen_id, producto_id, cantidad) "
            "VALUES (:c, :p, 48)"
        ),
        {"c": semilla["camion"], "p": producto},
    )
    await sesion.commit()

    texto = solo_texto(await cliente.get("/panel/compras"))
    # 240 piezas × $10, repartidas en bodega y camión: el total no cambia.
    assert "$2,400.00" in texto
    assert "$1,920.00" in texto   # 192 en la bodega
    assert "$480.00" in texto     # 48 en el camión


# ===========================================================================
# Las cuentas por pagar
# ===========================================================================
async def test_la_compra_con_proveedor_genera_la_cuenta_con_su_vencimiento(
    cliente, sesion, semilla
):
    await _entrar(cliente)
    await _producto(sesion)
    await _proveedor(cliente, dias="30")
    await _comprar(cliente, semilla, sesion, bultos="10", costo="296.00",
                   proveedor=await _id_proveedor(sesion))

    cuenta = (
        await sesion.execute(
            text(
                "SELECT importe_original, importe_pagado, saldo, estado, "
                "       fecha_emision, fecha_vencimiento, referencia "
                "  FROM cuentas_por_pagar"
            )
        )
    ).mappings().one()
    assert cuenta["importe_original"] == Decimal("2960.00")
    assert cuenta["saldo"] == Decimal("2960.00")
    assert cuenta["estado"] == "abierta"
    assert cuenta["fecha_vencimiento"] == date.today() + timedelta(days=30)
    assert cuenta["referencia"] == "F-45821"


async def test_la_cuenta_dice_lo_que_dice_la_factura_al_centavo(
    cliente, sesion, semilla
):
    """El defecto que encontró la construcción, y costaba una operación entera.

    10 cajas a $296.00 son $2,960.00 en la factura. Pero el costo por pieza es
    296/24 = 12.3333..., que no es exacto, y 240 x 12.3333 da $2,959.99.

    Con ese centavo de menos, la cuenta por pagar nace en $2,959.99, alguien
    captura el pago de $2,960.00 que de verdad hizo, y el sistema lo RECHAZA por
    exceder el saldo. Por eso el renglón congela el importe calculado sobre los
    bultos capturados, y no sobre las piezas en que se convirtieron.
    """
    await _entrar(cliente)
    await _producto(sesion, factor=24)
    await _proveedor(cliente)
    entrada, _ = await _comprar(cliente, semilla, sesion, bultos="10",
                                costo="296.00",
                                proveedor=await _id_proveedor(sesion))

    renglon = (
        await sesion.execute(
            text("SELECT cantidad, costo_unitario, importe FROM entrada_detalle")
        )
    ).mappings().one()
    # Las dos verdades, distintas a propósito y las dos guardadas.
    assert renglon["importe"] == Decimal("2960.00")          # la factura
    assert renglon["costo_unitario"] == Decimal("12.3333")   # la valuación
    assert renglon["cantidad"] * renglon["costo_unitario"] != renglon["importe"]

    assert (
        await sesion.execute(text("SELECT importe_total FROM entradas"))
    ).scalar() == Decimal("2960.00")
    assert (
        await sesion.execute(text("SELECT importe_original FROM cuentas_por_pagar"))
    ).scalar() == Decimal("2960.00")

    # Y lo que de verdad importa: el pago de la factura completa se acepta y
    # liquida la cuenta.
    r = await cliente.post(
        f"/panel/compras/cuenta/{entrada}/pago",
        data={"csrf": _csrf(cliente), "importe": "2,960.00",
              "forma_pago": "transferencia", "referencia": "SPEI 1",
              "fecha": "", "nota": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "liquidada" in r.headers["location"].lower()
    assert (
        await sesion.execute(text("SELECT estado FROM cuentas_por_pagar"))
    ).scalar() == "liquidada"


async def test_sin_proveedor_del_catalogo_no_hay_cuenta_por_pagar(
    cliente, sesion, semilla
):
    """Es la compra de contado en la central de abastos, y pasa."""
    await _entrar(cliente)
    await _producto(sesion)
    await _comprar(cliente, semilla, sesion, bultos="10", costo="296.00",
                   proveedor=None)
    assert (
        await sesion.execute(text("SELECT count(*) FROM cuentas_por_pagar"))
    ).scalar() == 0
    # Y el documento lo dice, en vez de dejarlo suponer.
    entrada = (await sesion.execute(text("SELECT id FROM entradas"))).scalar()
    texto = solo_texto(await cliente.get(f"/panel/entradas/{entrada}"))
    assert "Sin cuenta por pagar" in texto
    assert "Central de abastos" in texto


async def test_un_proveedor_de_contado_vence_el_mismo_dia(cliente, sesion, semilla):
    await _entrar(cliente)
    await _producto(sesion)
    await _proveedor(cliente, dias="0")
    await _comprar(cliente, semilla, sesion, bultos="10", costo="296.00",
                   proveedor=await _id_proveedor(sesion))
    cuenta = (
        await sesion.execute(
            text("SELECT fecha_emision, fecha_vencimiento FROM cuentas_por_pagar")
        )
    ).mappings().one()
    assert cuenta["fecha_vencimiento"] == cuenta["fecha_emision"]


async def test_un_pago_parcial_y_luego_la_liquidacion(cliente, sesion, semilla):
    await _entrar(cliente)
    await _producto(sesion)
    await _proveedor(cliente)
    entrada, _ = await _comprar(cliente, semilla, sesion, bultos="10",
                                costo="296.00",
                                proveedor=await _id_proveedor(sesion))

    for importe, estado in (("1,000.00", "parcial"), ("1960.00", "liquidada")):
        r = await cliente.post(
            f"/panel/compras/cuenta/{entrada}/pago",
            data={"csrf": _csrf(cliente), "importe": importe,
                  "forma_pago": "transferencia", "referencia": "SPEI 1",
                  "fecha": "", "nota": ""},
            follow_redirects=False,
        )
        assert r.status_code == 303, r.headers.get("location")
        assert (
            await sesion.execute(text("SELECT estado FROM cuentas_por_pagar"))
        ).scalar() == estado

    cuenta = (
        await sesion.execute(
            text("SELECT importe_pagado, saldo FROM cuentas_por_pagar")
        )
    ).mappings().one()
    assert cuenta["importe_pagado"] == Decimal("2960.00")
    assert cuenta["saldo"] == Decimal("0.00")
    assert (
        await sesion.execute(text("SELECT count(*) FROM pagos_proveedor"))
    ).scalar() == 2


async def test_un_pago_mayor_que_el_saldo_se_rechaza(cliente, sesion, semilla):
    """El CHECK de la tabla lo impediría, y su error no le dice nada a quien
    está capturando una transferencia."""
    await _entrar(cliente)
    await _producto(sesion)
    await _proveedor(cliente)
    entrada, _ = await _comprar(cliente, semilla, sesion, bultos="10",
                                costo="296.00",
                                proveedor=await _id_proveedor(sesion))
    r = await cliente.post(
        f"/panel/compras/cuenta/{entrada}/pago",
        data={"csrf": _csrf(cliente), "importe": "5000", "forma_pago": "cheque",
              "referencia": "", "fecha": "", "nota": ""},
        follow_redirects=False,
    )
    assert "anticipo" in r.headers["location"]
    assert (
        await sesion.execute(text("SELECT importe_pagado FROM cuentas_por_pagar"))
    ).scalar() == Decimal("0.00")


async def test_no_se_paga_una_cuenta_liquidada(cliente, sesion, semilla):
    await _entrar(cliente)
    await _producto(sesion)
    await _proveedor(cliente)
    entrada, _ = await _comprar(cliente, semilla, sesion, bultos="10",
                                costo="296.00",
                                proveedor=await _id_proveedor(sesion))
    await cliente.post(
        f"/panel/compras/cuenta/{entrada}/pago",
        data={"csrf": _csrf(cliente), "importe": "2960", "forma_pago": "efectivo",
              "referencia": "", "fecha": "", "nota": ""},
        follow_redirects=False,
    )
    r = await cliente.post(
        f"/panel/compras/cuenta/{entrada}/pago",
        data={"csrf": _csrf(cliente), "importe": "1", "forma_pago": "efectivo",
              "referencia": "", "fecha": "", "nota": ""},
        follow_redirects=False,
    )
    assert "ya%20est%C3%A1%20liquidada" in r.headers["location"]


async def test_un_pago_con_fecha_futura_se_rechaza(cliente, sesion, semilla):
    await _entrar(cliente)
    await _producto(sesion)
    await _proveedor(cliente)
    entrada, _ = await _comprar(cliente, semilla, sesion, bultos="10",
                                costo="296.00",
                                proveedor=await _id_proveedor(sesion))
    r = await cliente.post(
        f"/panel/compras/cuenta/{entrada}/pago",
        data={"csrf": _csrf(cliente), "importe": "100", "forma_pago": "efectivo",
              "referencia": "", "nota": "",
              "fecha": (date.today() + timedelta(days=1)).isoformat()},
        follow_redirects=False,
    )
    assert "futura" in r.headers["location"]


async def test_la_antiguedad_se_cuenta_desde_el_vencimiento(cliente, sesion, semilla):
    """Un proveedor a 30 días no está vencido el día 15 — el mismo criterio que
    la cartera de clientes."""
    await _entrar(cliente)
    await _producto(sesion)
    await _proveedor(cliente, dias="30")
    await _comprar(cliente, semilla, sesion, bultos="10", costo="296.00",
                   proveedor=await _id_proveedor(sesion),
                   fecha=(date.today() - timedelta(days=15)).isoformat())

    texto = solo_texto(await cliente.get("/panel/compras"))
    assert "en 15 día(s)" in texto
    assert "vencida hace" not in texto

    # Y a los 45 días sí: se mueve la emisión hacia atrás.
    await sesion.execute(
        text(
            "UPDATE cuentas_por_pagar "
            "   SET fecha_emision = CURRENT_DATE - 45, "
            "       fecha_vencimiento = CURRENT_DATE - 15"
        )
    )
    await sesion.commit()
    texto = solo_texto(await cliente.get("/panel/compras"))
    assert "vencida hace 15 día(s)" in texto


# ===========================================================================
# El catálogo
# ===========================================================================
async def test_el_codigo_del_proveedor_se_normaliza(cliente, sesion, semilla):
    """`abarrotes` y `ABARROTES` serían dos proveedores con la misma deuda
    repartida entre los dos."""
    await _entrar(cliente)
    await _proveedor(cliente, codigo="abarr01")
    assert (
        await sesion.execute(text("SELECT codigo FROM proveedores"))
    ).scalar() == "ABARR01"
    # Y el RFC también.
    assert (
        await sesion.execute(text("SELECT rfc FROM proveedores"))
    ).scalar() == "ABC123456XY1"


async def test_un_codigo_repetido_dice_de_quien_es(cliente, sesion, semilla):
    await _entrar(cliente)
    await _proveedor(cliente, codigo="ABARR01", nombre="Abarrotes del Centro")
    r = await _proveedor(cliente, codigo="ABARR01", nombre="Otro")
    destino = r.headers["location"]
    assert "ya%20es%20de" in destino
    texto = solo_texto(await cliente.get(destino))
    assert "Abarrotes del Centro" in texto
    assert (
        await sesion.execute(text("SELECT count(*) FROM proveedores"))
    ).scalar() == 1


async def test_un_proveedor_se_desactiva_y_no_se_borra(cliente, sesion, semilla):
    """Las entradas de hace dos años apuntan a él."""
    await _entrar(cliente)
    await _proveedor(cliente)
    proveedor = await _id_proveedor(sesion)
    r = await cliente.post(
        f"/panel/compras/proveedor/{proveedor}/estado",
        data={"csrf": _csrf(cliente)}, follow_redirects=False,
    )
    assert r.status_code == 303
    assert (
        await sesion.execute(text("SELECT activo FROM proveedores"))
    ).scalar() is False
    # Sigue existiendo, y deja de ofrecerse al capturar una entrada.
    assert (
        await sesion.execute(text("SELECT count(*) FROM proveedores"))
    ).scalar() == 1
    assert str(proveedor) not in (await cliente.get("/panel/entradas")).text


async def test_el_proveedor_necesita_codigo_y_nombre(cliente, sesion, semilla):
    await _entrar(cliente)
    r = await cliente.post(
        "/panel/compras/proveedor",
        data={"csrf": _csrf(cliente), "codigo": "", "nombre": "Sin código",
              "dias_credito": "0"},
        follow_redirects=False,
    )
    assert "c%C3%B3digo%20y%20nombre" in r.headers["location"]


async def test_el_nombre_del_proveedor_se_congela_en_la_entrada(
    cliente, sesion, semilla
):
    """Si se renombra, la compra de hace dos años sigue diciendo a quién se le
    compró — el mismo razonamiento que `lista_precios_version` en la venta."""
    await _entrar(cliente)
    await _producto(sesion)
    await _proveedor(cliente, nombre="Abarrotes del Centro")
    proveedor = await _id_proveedor(sesion)
    await _comprar(cliente, semilla, sesion, bultos="10", costo="296.00",
                   proveedor=proveedor)

    await cliente.post(
        "/panel/compras/proveedor",
        data={"csrf": _csrf(cliente), "proveedor_id": str(proveedor),
              "codigo": "ABARR01", "nombre": "Distribuidora del Bajío S.A.",
              "dias_credito": "15", "rfc": "", "contacto": "", "telefono": "",
              "notas": ""},
        follow_redirects=False,
    )
    assert (
        await sesion.execute(text("SELECT proveedor FROM entradas"))
    ).scalar() == "Abarrotes del Centro"
    assert (
        await sesion.execute(text("SELECT nombre FROM proveedores"))
    ).scalar() == "Distribuidora del Bajío S.A."


# ===========================================================================
# Los permisos: dos manos
# ===========================================================================
async def _usuario(sesion, semilla, codigo: str, rol: str) -> None:
    from app.core.seguridad import hashear_password

    await sesion.execute(
        text(
            "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
            "                      rol_codigo, creado_en, actualizado_en) "
            "VALUES (:u, :s, :cod, :cod, :h, :rol, now(), now())"
        ),
        {"u": uuid.uuid4(), "s": semilla["sucursal"], "cod": codigo,
         "h": hashear_password(PASSWORD_VENDEDOR), "rol": rol},
    )
    await sesion.commit()


async def test_el_supervisor_administra_y_no_paga(cliente, sesion, semilla):
    """Ya recibe mercancía, así que necesita capturar su costo. Pagar es otra
    mano: que la misma persona reciba y pague sin que nadie lo vea es la receta
    de una factura inventada."""
    await _usuario(sesion, semilla, "SUP01", "supervisor")
    await _entrar(cliente, "SUP01")

    r = await cliente.get("/panel/compras")
    assert r.status_code == 200
    assert "Dar de alta" in solo_texto(r)       # compras.administrar

    # Y el pago le está negado, aunque la pantalla no dibuje el botón.
    await _producto(sesion)
    await _proveedor(cliente)
    entrada, _ = await _comprar(cliente, semilla, sesion, bultos="10",
                                costo="296.00",
                                proveedor=await _id_proveedor(sesion))
    bloqueado = await cliente.post(
        f"/panel/compras/cuenta/{entrada}/pago",
        data={"csrf": _csrf(cliente), "importe": "100", "forma_pago": "efectivo",
              "referencia": "", "fecha": "", "nota": ""},
        follow_redirects=False,
    )
    assert bloqueado.status_code == 403


async def test_el_permiso_de_pagar_tiene_un_solo_dueno(sesion):
    """Gerencia, y nadie más. Es la separación de manos del módulo."""
    duenos = (
        await sesion.execute(
            text(
                "SELECT rol_codigo FROM roles_permisos "
                " WHERE permiso_codigo = 'compras.pagar' ORDER BY rol_codigo"
            )
        )
    ).scalars().all()
    assert duenos == ["gerente"]


async def test_un_vendedor_no_llega_al_panel_de_compras(cliente, sesion, semilla):
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": "VEND01", "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 200
    sin_sesion = await cliente.get("/panel/compras", follow_redirects=False)
    assert sin_sesion.status_code == 303
    assert sin_sesion.headers["location"].startswith("/panel/entrar")
