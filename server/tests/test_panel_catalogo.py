"""La captura de catálogo y precios desde el panel.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
Esta pantalla es por donde el dinero entra al sistema: lo que se escriba aquí es
lo que cinco camiones van a cobrar mañana. Las pruebas se agrupan en tres cosas
que, si se rompen, se rompen en silencio:

1. **Que el precio llegue entero al teléfono.** Cuatro decimales, sin `float` en
   medio, y con su delta publicado en `change_log`. Un precio que la oficina ve y
   el camión no es peor que no tenerlo.
2. **Que un producto nuevo se pueda vender.** Un producto sin renglón en
   `producto_unidades` no admite precio, y un producto sin precio no le aparece al
   vendedor. Ninguno de los dos casos falla: simplemente no está.
3. **Que la aritmética de la caja y la pieza cierre.** $296.00 ÷ 24 = $12.3333, y
   24 × $12.3333 tiene que volver a dar $296.00.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.domain.importes import importe_de_linea
from tests.conftest import PASSWORD_VENDEDOR

pytestmark = pytest.mark.asyncio


async def _entrar(cliente, codigo: str = "ADMIN01") -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": codigo, "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


def _csrf_de(respuesta) -> str:
    marca = 'name="csrf" value="'
    inicio = respuesta.text.index(marca) + len(marca)
    return respuesta.text[inicio : respuesta.text.index('"', inicio)]


async def _crear_producto(cliente, **campos) -> str:
    """Crea un producto por el formulario y devuelve su id, sacado del redirect."""
    csrf = _csrf_de(await cliente.get("/panel/productos/nuevo"))
    datos = {
        "sku": "REF-1L",
        "nombre": "Refresco de cola 1 L",
        "unidad_base": "PZA",
        "tasa_iva": "0.1600",
        "unidad_caja": "CAJA",
        "factor_caja": "24",
        "csrf": csrf,
    }
    datos.update(campos)
    r = await cliente.post("/panel/productos/nuevo", data=datos, follow_redirects=False)
    assert r.status_code == 303, r.text
    return r.headers["location"].split("/panel/productos/")[1].split("?")[0]


# ---------------------------------------------------------------------------
# Alta
# ---------------------------------------------------------------------------


async def test_el_alta_deja_el_producto_listo_para_tener_precio(cliente, semilla, sesion):
    """La unidad base entra en `producto_unidades` en la MISMA transacción.

    La llave foránea de `precios` apunta al par (producto, unidad). Si el alta
    dejara ese renglón para después, cada producto nuevo pasaría por un estado en
    el que no se le puede poner precio, y el error sería una violación de llave
    foránea que no le dice nada a quien captura.
    """
    await _entrar(cliente)
    producto_id = await _crear_producto(cliente)

    presentaciones = (
        await sesion.execute(
            text(
                "SELECT unidad_codigo, factor, es_default FROM producto_unidades "
                " WHERE producto_id = :p ORDER BY factor"
            ),
            {"p": uuid.UUID(producto_id)},
        )
    ).mappings().all()

    assert [p["unidad_codigo"] for p in presentaciones] == ["PZA", "CAJA"]
    # La unidad base vale 1: una pieza es una pieza.
    assert presentaciones[0]["factor"] == Decimal("1.0000")
    assert presentaciones[0]["es_default"] is True
    assert presentaciones[1]["factor"] == Decimal("24.0000")


async def test_el_alta_publica_su_delta_sin_que_nadie_lo_escriba(cliente, semilla, sesion):
    """El disparador de la migración 0010 es lo que hace que el catálogo llegue.

    Si publicar el delta fuera responsabilidad de cada pantalla, la que se olvide
    produce un catálogo que la oficina ve y el camión no. Esta prueba es la que
    avisaría si alguien mueve esa responsabilidad al código.
    """
    await _entrar(cliente)
    producto_id = await _crear_producto(cliente)

    entidades = (
        await sesion.execute(
            text(
                "SELECT entidad, operacion FROM change_log "
                " WHERE entidad_id = :p ORDER BY cursor"
            ),
            {"p": uuid.UUID(producto_id)},
        )
    ).mappings().all()

    assert ("producto", "upsert") in [(e["entidad"], e["operacion"]) for e in entidades]
    assert ("producto_unidad", "upsert") in [
        (e["entidad"], e["operacion"]) for e in entidades
    ]


async def test_un_sku_repetido_se_explica_en_vez_de_reventar(cliente, semilla):
    """El UNIQUE de `sku` protege la base; el mensaje protege a quien captura."""
    await _entrar(cliente)
    await _crear_producto(cliente)

    csrf = _csrf_de(await cliente.get("/panel/productos/nuevo"))
    r = await cliente.post(
        "/panel/productos/nuevo",
        data={
            "sku": "REF-1L",
            "nombre": "Otro refresco",
            "unidad_base": "PZA",
            "tasa_iva": "0.0000",
            "csrf": csrf,
        },
    )
    assert r.status_code == 200
    assert "ya lo tiene" in r.text
    assert "Refresco de cola 1 L" in r.text


async def test_una_presentacion_no_trae_media_caja(cliente, semilla):
    """No hay producto a granel (ADR 0002), así que el factor es entero."""
    await _entrar(cliente)
    csrf = _csrf_de(await cliente.get("/panel/productos/nuevo"))
    r = await cliente.post(
        "/panel/productos/nuevo",
        data={
            "sku": "X-1",
            "nombre": "Producto raro",
            "unidad_base": "PZA",
            "unidad_caja": "CAJA",
            "factor_caja": "24.5",
            "tasa_iva": "0.0000",
            "csrf": csrf,
        },
    )
    assert r.status_code == 200
    assert "piezas completas" in r.text


# ---------------------------------------------------------------------------
# Precios
# ---------------------------------------------------------------------------


async def test_el_precio_se_guarda_con_sus_CUATRO_decimales(cliente, semilla, sesion):
    """12.3333 tiene que llegar como 12.3333, no como 12.33.

    Es el corazón de ADR 0002: el precio de la pieza sale de dividir el de la caja
    y casi nunca cabe en dos decimales.
    """
    await _entrar(cliente)
    producto_id = await _crear_producto(cliente)
    detalle = await cliente.get(f"/panel/productos/{producto_id}")

    r = await cliente.post(
        f"/panel/productos/{producto_id}/precios",
        data={
            "csrf": _csrf_de(detalle),
            "lista_id": str(semilla["lista_precios"]),
            "precio_CAJA": "296.00",
            "precio_PZA": "12.3333",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    precios = dict(
        (
            await sesion.execute(
                text(
                    "SELECT unidad_codigo, precio FROM precios WHERE producto_id = :p"
                ),
                {"p": uuid.UUID(producto_id)},
            )
        ).all()
    )
    assert precios["CAJA"] == Decimal("296.0000")
    assert precios["PZA"] == Decimal("12.3333")

    # Y la aritmética cierra: la caja vendida por piezas cobra lo mismo.
    assert importe_de_linea(Decimal("24"), precios["PZA"]) == Decimal("296.00")


async def test_aceptar_lo_que_sale_de_pegar_una_celda_de_excel(cliente, semilla, sesion):
    """`$1,296.50` es exactamente lo que se copia de una hoja de cálculo."""
    await _entrar(cliente)
    producto_id = await _crear_producto(cliente)
    detalle = await cliente.get(f"/panel/productos/{producto_id}")

    await cliente.post(
        f"/panel/productos/{producto_id}/precios",
        data={
            "csrf": _csrf_de(detalle),
            "lista_id": str(semilla["lista_precios"]),
            "precio_CAJA": "$1,296.50",
        },
        follow_redirects=False,
    )
    precio = (
        await sesion.execute(
            text(
                "SELECT precio FROM precios WHERE producto_id = :p AND unidad_codigo = 'CAJA'"
            ),
            {"p": uuid.UUID(producto_id)},
        )
    ).scalar_one()
    assert precio == Decimal("1296.5000")


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("0", "no puede ser cero"),
        ("-5", "no puede ser negativo"),
        ("como sea", "no es un número"),
        ("9999999", "error de dedo"),
    ],
)
async def test_un_precio_imposible_no_se_guarda(cliente, semilla, valor, esperado):
    """Un precio en cero se vería idéntico a "regalado" en el ticket del cliente."""
    await _entrar(cliente)
    producto_id = await _crear_producto(cliente)
    detalle = await cliente.get(f"/panel/productos/{producto_id}")

    r = await cliente.post(
        f"/panel/productos/{producto_id}/precios",
        data={
            "csrf": _csrf_de(detalle),
            "lista_id": str(semilla["lista_precios"]),
            "precio_CAJA": valor,
        },
        follow_redirects=True,
    )
    assert esperado in r.text


async def test_un_campo_en_blanco_deja_el_precio_como_estaba(cliente, semilla, sesion):
    """Vacío significa "no lo cambies", no "bórralo".

    Borrar un precio deja al producto invisible en esa presentación. Eso tiene que
    ser una decisión explícita, no el resultado de no escribir nada.
    """
    await _entrar(cliente)
    producto_id = await _crear_producto(cliente)
    detalle = await cliente.get(f"/panel/productos/{producto_id}")
    csrf, lista = _csrf_de(detalle), str(semilla["lista_precios"])

    await cliente.post(
        f"/panel/productos/{producto_id}/precios",
        data={"csrf": csrf, "lista_id": lista, "precio_CAJA": "296.00",
              "precio_PZA": "12.3333"},
        follow_redirects=False,
    )
    # Solo se toca la caja; la pieza va en blanco.
    await cliente.post(
        f"/panel/productos/{producto_id}/precios",
        data={"csrf": csrf, "lista_id": lista, "precio_CAJA": "310.00", "precio_PZA": ""},
        follow_redirects=False,
    )

    precios = dict(
        (
            await sesion.execute(
                text("SELECT unidad_codigo, precio FROM precios WHERE producto_id = :p"),
                {"p": uuid.UUID(producto_id)},
            )
        ).all()
    )
    assert precios["CAJA"] == Decimal("310.0000")
    assert precios["PZA"] == Decimal("12.3333")


async def test_cambiar_el_precio_sube_la_version_y_repetirlo_no(cliente, semilla, sesion):
    """`version` viaja al teléfono y vuelve en la venta.

    Con ella la oficina sabe con qué lista se cobró sin comparar importes uno por
    uno. Y guardar el mismo precio otra vez **no** la mueve: publicar un delta
    idéntico haría que todos los teléfonos volvieran a bajar el catálogo sin que
    nada hubiera cambiado.
    """
    await _entrar(cliente)
    producto_id = await _crear_producto(cliente)
    detalle = await cliente.get(f"/panel/productos/{producto_id}")
    csrf, lista = _csrf_de(detalle), str(semilla["lista_precios"])

    async def guardar(valor: str) -> None:
        await cliente.post(
            f"/panel/productos/{producto_id}/precios",
            data={"csrf": csrf, "lista_id": lista, "precio_CAJA": valor},
            follow_redirects=False,
        )

    async def version() -> int:
        return (
            await sesion.execute(
                text(
                    "SELECT version FROM precios "
                    " WHERE producto_id = :p AND unidad_codigo = 'CAJA'"
                ),
                {"p": uuid.UUID(producto_id)},
            )
        ).scalar_one()

    await guardar("296.00")
    assert await version() == 1
    await guardar("310.00")
    assert await version() == 2
    await guardar("310.00")
    assert await version() == 2


async def test_el_precio_publica_su_delta_con_los_cuatro_decimales(
    cliente, semilla, sesion
):
    """El delta es lo único que ve el teléfono. Si el precio se degrada ahí, se
    degrada en el camión.

    El payload lo arma `to_jsonb(NEW)` en el disparador, así que el precio viaja
    como **número** JSON y no como texto. La precisión aguanta porque un precio
    de este negocio necesita a lo más diez dígitos significativos y un `double`
    lleva quince: `Precio.deBase` en Dart lo recibe y hace
    `toStringAsFixed(4)` antes de convertirlo a diezmilésimas enteras, que es el
    único lugar donde el número cruza al dominio.
    """
    await _entrar(cliente)
    producto_id = await _crear_producto(cliente)
    detalle = await cliente.get(f"/panel/productos/{producto_id}")

    await cliente.post(
        f"/panel/productos/{producto_id}/precios",
        data={
            "csrf": _csrf_de(detalle),
            "lista_id": str(semilla["lista_precios"]),
            "precio_PZA": "12.3333",
        },
        follow_redirects=False,
    )

    payload = (
        await sesion.execute(
            text(
                "SELECT payload FROM change_log "
                " WHERE entidad = 'precio' AND entidad_id = :p "
                " ORDER BY cursor DESC LIMIT 1"
            ),
            {"p": uuid.UUID(producto_id)},
        )
    ).scalar_one()

    assert payload["unidad_codigo"] == "PZA"
    # Los cuatro decimales sobreviven el viaje.
    assert Decimal(f"{payload['precio']:.4f}") == Decimal("12.3333")
    assert payload["version"] == 1


async def test_derivar_la_pieza_de_la_caja_da_los_cuatro_decimales(
    cliente, semilla, sesion
):
    """El botón que existe por una división que no cierra.

    $296.00 ÷ 24 = $12.3333… Quien lo calcula en una hoja escribe 12.33, y a
    partir de ahí cada caja vendida por pieza cobra $295.92: ocho centavos menos,
    veinticuatro veces al día.
    """
    await _entrar(cliente)
    producto_id = await _crear_producto(cliente)
    detalle = await cliente.get(f"/panel/productos/{producto_id}")

    await cliente.post(
        f"/panel/productos/{producto_id}/precios",
        data={
            "csrf": _csrf_de(detalle),
            "lista_id": str(semilla["lista_precios"]),
            "precio_CAJA": "296.00",
        },
        follow_redirects=False,
    )

    # La pantalla ya muestra la sugerencia calculada, antes de tocar nada.
    detalle = await cliente.get(f"/panel/productos/{producto_id}")
    assert "12.3333" in detalle.text

    r = await cliente.post(
        f"/panel/productos/{producto_id}/precios/derivar",
        data={
            "csrf": _csrf_de(detalle),
            "lista_id": str(semilla["lista_precios"]),
            "hacia": "PZA",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    pieza = (
        await sesion.execute(
            text(
                "SELECT precio FROM precios "
                " WHERE producto_id = :p AND unidad_codigo = 'PZA'"
            ),
            {"p": uuid.UUID(producto_id)},
        )
    ).scalar_one()
    assert pieza == Decimal("12.3333")
    # Y las 24 piezas vuelven a valer la caja completa.
    assert importe_de_linea(Decimal("24"), pieza) == Decimal("296.00")
    # A dos decimales no habría cerrado, que es la razón de todo esto.
    assert importe_de_linea(Decimal("24"), Decimal("12.33")) == Decimal("295.92")


async def test_quitar_un_precio_es_una_accion_aparte(cliente, semilla, sesion):
    """Al quitarlo el vendedor deja de poder ofrecer esa presentación."""
    await _entrar(cliente)
    producto_id = await _crear_producto(cliente)
    detalle = await cliente.get(f"/panel/productos/{producto_id}")
    csrf, lista = _csrf_de(detalle), str(semilla["lista_precios"])

    await cliente.post(
        f"/panel/productos/{producto_id}/precios",
        data={"csrf": csrf, "lista_id": lista, "precio_CAJA": "296.00"},
        follow_redirects=False,
    )
    r = await cliente.post(
        f"/panel/productos/{producto_id}/precios/quitar",
        data={"csrf": csrf, "lista_id": lista, "unidad_codigo": "CAJA"},
        follow_redirects=True,
    )
    assert "ya no la verá" in r.text

    cuantos = (
        await sesion.execute(
            text("SELECT count(*) FROM precios WHERE producto_id = :p"),
            {"p": uuid.UUID(producto_id)},
        )
    ).scalar_one()
    assert cuantos == 0


# ---------------------------------------------------------------------------
# Lo que la pantalla tiene que delatar
# ---------------------------------------------------------------------------


async def test_el_filtro_delata_lo_que_el_vendedor_no_puede_vender(cliente, semilla):
    """Un producto sin precio no le aparece al vendedor: no falla, no avisa."""
    await _entrar(cliente)
    await _crear_producto(cliente)

    r = await cliente.get("/panel/productos?filtro=sin_precio")
    assert "Refresco de cola 1 L" in r.text
    assert "sin precio" in r.text


async def test_desactivar_no_borra(cliente, semilla, sesion):
    """Hay ventas que lo referencian, y una venta de hoy que todavía no sincroniza
    viene con su clave. Borrarlo la mandaría a cuarentena por una llave foránea."""
    await _entrar(cliente)
    producto_id = await _crear_producto(cliente)
    detalle = await cliente.get(f"/panel/productos/{producto_id}")

    await cliente.post(
        f"/panel/productos/{producto_id}",
        data={
            "csrf": _csrf_de(detalle),
            "sku": "REF-1L",
            "nombre": "Refresco de cola 1 L",
            "tasa_iva": "0.1600",
            # Sin `activo`: la casilla desmarcada no se manda.
        },
        follow_redirects=False,
    )

    fila = (
        await sesion.execute(
            text("SELECT activo FROM productos WHERE id = :p"),
            {"p": uuid.UUID(producto_id)},
        )
    ).mappings().one()
    assert fila["activo"] is False


async def test_el_factor_de_la_unidad_base_no_se_cambia(cliente, semilla, sesion):
    """Una pieza es una pieza. Cambiarlo convertiría en otra cosa todo el
    inventario, que está expresado en unidades base."""
    await _entrar(cliente)
    producto_id = await _crear_producto(cliente)
    detalle = await cliente.get(f"/panel/productos/{producto_id}")

    r = await cliente.post(
        f"/panel/productos/{producto_id}/presentaciones",
        data={"csrf": _csrf_de(detalle), "unidad_codigo": "PZA", "factor": "6"},
        follow_redirects=True,
    )
    assert "siempre vale 1" in r.text

    factor = (
        await sesion.execute(
            text(
                "SELECT factor FROM producto_unidades "
                " WHERE producto_id = :p AND unidad_codigo = 'PZA'"
            ),
            {"p": uuid.UUID(producto_id)},
        )
    ).scalar_one()
    assert factor == Decimal("1.0000")


# ---------------------------------------------------------------------------
# Acceso
# ---------------------------------------------------------------------------


@pytest.fixture
async def gerente(sesion, semilla) -> None:
    """Gerencia es de SOLO LECTURA sobre la operación (migración 0009)."""
    from app.core.seguridad import hashear_password

    await sesion.execute(
        text(
            "INSERT INTO usuarios(id, sucursal_id, codigo, nombre, password_hash, "
            "rol_codigo, creado_en, actualizado_en) "
            "VALUES (:id, :s, 'GER01', 'Gerente', :h, 'gerente', now(), now())"
        ),
        {"id": uuid.uuid4(), "s": semilla["sucursal"], "h": hashear_password(PASSWORD_VENDEDOR)},
    )
    await sesion.commit()


async def test_EL_GERENTE_SI_CAMBIA_EL_CATALOGO(cliente, semilla, gerente):
    """Decisión de la dirección, octubre 2026: gerencia deja de ser de solo lectura.

    La migración 0009 decía «Gerencia es de SOLO LECTURA sobre la operación:
    monitorea, no opera», y la 0031 le concede `catalogo.administrar`.

    Conviene saber qué abarca ese permiso, porque es uno y cubre dos cosas: los
    DATOS del producto —nombre, SKU, código de barras— y sus PRECIOS. No hay forma
    de conceder lo primero sin lo segundo sin partir el permiso en dos, y partirlo
    fragmentaría el modelo de permisos para una distinción que nadie pidió.
    """
    await _entrar(cliente, "ADMIN01")
    producto_id = await _crear_producto(cliente)
    await cliente.get("/panel/salir")

    await _entrar(cliente, "GER01")
    lectura = await cliente.get(f"/panel/productos/{producto_id}")
    assert lectura.status_code == 200
    assert "Refresco de cola 1 L" in lectura.text
    assert "Guardar precios" in lectura.text

    r = await cliente.post(
        f"/panel/productos/{producto_id}/precios",
        data={
            "csrf": _csrf_de(lectura),
            "lista_id": str(semilla["lista_precios"]),
            "precio_CAJA": "1.00",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303


async def test_capturar_un_precio_exige_el_token_csrf(cliente, semilla):
    await _entrar(cliente)
    producto_id = await _crear_producto(cliente)
    r = await cliente.post(
        f"/panel/productos/{producto_id}/precios",
        data={
            "csrf": "inventado",
            "lista_id": str(semilla["lista_precios"]),
            "precio_CAJA": "1.00",
        },
    )
    assert r.status_code == 403
