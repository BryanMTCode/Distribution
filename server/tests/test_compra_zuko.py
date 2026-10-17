"""Los ZUKO de la factura del 16/10, a la familia de Maruchan y a la bodega (ADR 0002 §125).

«Se agregarán estos artículos a la familia de Maruchan. Se agregarán solo en el
almacén.»

1. **El archivo es la factura**: siete sabores, 600 exhibidores a $47.00, $28,200.00.
2. **Lo que no cuadra se dice antes**, y no se carga nada.
3. **Quedan en Maruchan y en la bodega**: siete artículos nuevos en esa familia,
   sin precio de venta —el vendedor no los ve—, recibidos en la bodega como una
   compra con el costo de la factura. Los camiones no se tocan.
4. **Dos veces no duplica nada**, y un SKU que ya es de otro artículo no toca nada.
5. **El comando pide la frase**, y `--resumen` no cambia nada.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.asyncio

SKUS = [f"T-{n}" for n in range(103, 110)]


def _cz():
    # Importado aquí y no arriba: ver `_bb` en test_base_en_blanco.py.
    from app.infra import compra_zuko

    return compra_zuko


async def _como_el_servidor(sesion) -> None:
    """La base como está en el servidor: el catálogo de la dirección, con la
    familia «Sopa Maruchan», y la bodega principal con su inventario."""
    from app.infra import base_en_blanco as bb

    await bb.poner_en_blanco(sesion, bb.leer_articulos(), quien=await bb.quien_registra(sesion))


async def _existencia(sesion, sku: str, almacen: str = "BODEGA_PRINCIPAL") -> Decimal:
    return (
        await sesion.execute(
            text("SELECT COALESCE(sum(e.cantidad), 0) FROM existencias e "
                 "  JOIN productos p ON p.id = e.producto_id "
                 "  JOIN almacenes a ON a.id = e.almacen_id "
                 " WHERE p.sku = :s AND a.codigo = :a"),
            {"s": sku, "a": almacen},
        )
    ).scalar_one()


async def test_el_archivo_es_la_factura():
    cz = _cz()
    renglones = cz.leer_factura()
    assert [r.sku for r in renglones] == SKUS
    assert [(r.nombre.split()[1], r.cantidad) for r in renglones] == [
        ("jamaica", 120), ("naranja", 120), ("piña", 120), ("uva", 60),
        ("horchata", 60), ("fresa", 60), ("limón", 60),
    ]
    assert {r.costo for r in renglones} == {Decimal("47.00")}
    assert sum(r.cantidad for r in renglones) == 600
    assert sum(r.importe for r in renglones) == Decimal("28200.00")
    assert {r.familia for r in renglones} == {"Maruchan"}


async def test_lo_que_no_cuadra_se_dice_antes(tmp_path):
    cz = _cz()
    renglones = cz.FACTURA.read_text(encoding="utf-8").splitlines()
    repetido = tmp_path / "repetido.csv"
    repetido.write_text("\n".join([*renglones, renglones[1]]) + "\n", encoding="utf-8")
    with pytest.raises(cz.FacturaInvalida, match="el SKU T-103 viene dos veces"):
        cz.leer_factura(repetido)

    sin_costo = tmp_path / "sin_costo.csv"
    sin_costo.write_text(
        "\n".join([renglones[0], renglones[1].replace(",47.00", ",0")]) + "\n", encoding="utf-8"
    )
    with pytest.raises(cz.FacturaInvalida, match="falta el costo"):
        cz.leer_factura(sin_costo)


async def test_quedan_en_maruchan_y_en_la_bodega(sesion, semilla):
    cz = _cz()
    await _como_el_servidor(sesion)
    renglones = cz.leer_factura()
    plan = await cz.planear(sesion, renglones)
    assert plan.se_puede and plan.familia == "Sopa Maruchan"
    assert plan.bodega == "Bodega principal"
    assert [r.sku for r in plan.nuevos] == SKUS and plan.compra_hecha is None

    resultado = await cz.aplicar(sesion, renglones, quien=semilla["admin"])
    assert resultado.articulos_nuevos == 7 and not resultado.ya_estaba
    assert resultado.folio.startswith("EN-")
    assert (resultado.piezas, resultado.importe) == (600, Decimal("28200.00"))

    # En la familia de Maruchan, junto a las sopas, y sin precio de venta.
    articulos = (
        await sesion.execute(
            text("SELECT p.sku, p.nombre, c.nombre AS familia, p.unidad_base, "
                 "       (SELECT count(*) FROM precios pr WHERE pr.producto_id = p.id) "
                 "         AS precios "
                 "  FROM productos p JOIN categorias c ON c.id = p.categoria_id "
                 " WHERE c.nombre = 'Sopa Maruchan' ORDER BY p.sku")
        )
    ).mappings().all()
    assert [a["sku"] for a in articulos] == ["T-100", "T-101", "T-102", *SKUS]
    zuko = [a for a in articulos if a["sku"] in SKUS]
    assert zuko[0]["nombre"] == "Zuko jamaica 8 gr exhibidor (12/8)"
    assert {(a["unidad_base"], a["precios"]) for a in zuko} == {("PZA", 0)}

    # En la bodega, como compra con el costo de la factura.
    assert await _existencia(sesion, "T-103") == 120
    assert await _existencia(sesion, "T-109") == 60
    compra = (
        await sesion.execute(
            text("SELECT motivo, estado, proveedor, referencia, fecha_operativa, "
                 "       importe_total FROM entradas WHERE folio = :f"),
            {"f": resultado.folio},
        )
    ).mappings().one()
    assert (compra["motivo"], compra["estado"], compra["proveedor"]) == (
        "compra", "confirmada", "El Competidor"
    )
    assert compra["referencia"] == "Factura del 16/10/2026: ZUKO 8 gr"
    assert compra["fecha_operativa"] == date(2026, 10, 16)
    assert compra["importe_total"] == Decimal("28200.00")
    costo = (
        await sesion.execute(
            text("SELECT c.costo_promedio FROM producto_costos c "
                 "  JOIN productos p ON p.id = c.producto_id WHERE p.sku = 'T-105'")
        )
    ).scalar_one()
    assert costo == Decimal("47.0000")

    # A los camiones no les llega nada: suben con una carga.
    en_camiones = (
        await sesion.execute(
            text("SELECT count(*) FROM existencias e "
                 "  JOIN productos p ON p.id = e.producto_id "
                 "  JOIN almacenes a ON a.id = e.almacen_id "
                 " WHERE p.sku LIKE 'T-1%' AND p.sku >= 'T-103' AND a.tipo = 'camion'")
        )
    ).scalar_one()
    assert en_camiones == 0


async def test_dos_veces_no_duplica_y_un_sku_ajeno_no_toca_nada(sesion, semilla):
    cz = _cz()
    await _como_el_servidor(sesion)
    renglones = cz.leer_factura()
    primera = await cz.aplicar(sesion, renglones, quien=semilla["admin"])

    plan = await cz.planear(sesion, renglones)
    assert plan.nuevos == [] and len(plan.ya_estaban) == 7
    assert plan.compra_hecha == primera.folio
    otra = await cz.aplicar(sesion, renglones, quien=semilla["admin"])
    assert otra.ya_estaba and otra.folio == primera.folio and otra.articulos_nuevos == 0
    assert await _existencia(sesion, "T-103") == 120
    compras = (
        await sesion.execute(text("SELECT count(*) FROM entradas WHERE motivo = 'compra'"))
    ).scalar_one()
    assert compras == 1

    # Si el SKU ya es de otro artículo, o la familia no está, no se toca nada.
    await sesion.execute(text("DELETE FROM entradas WHERE motivo = 'compra'"))
    await sesion.execute(
        text("UPDATE productos SET nombre = 'Otro artículo' WHERE sku = 'T-104'")
    )
    await sesion.execute(
        text("UPDATE categorias SET activo = false WHERE nombre = 'Sopa Maruchan'")
    )
    await sesion.commit()
    plan = await cz.planear(sesion, renglones)
    assert not plan.se_puede
    assert "el SKU T-104 ya es «Otro artículo», no «Zuko naranja 8 gr exhibidor (12/8)»." in (
        plan.problemas
    )
    assert any("no hay ninguna familia «Maruchan» activa" in p for p in plan.problemas)
    with pytest.raises(cz.FacturaInvalida):
        await cz.aplicar(sesion, renglones, quien=semilla["admin"])


async def test_la_familia_renombrada_se_encuentra(sesion, semilla):
    cz = _cz()
    await _como_el_servidor(sesion)
    await sesion.execute(
        text("UPDATE categorias SET nombre = 'Sopas Maruchan instantáneas' "
             " WHERE nombre = 'Sopa Maruchan'")
    )
    await sesion.commit()
    plan = await cz.planear(sesion, cz.leer_factura())
    assert plan.se_puede and plan.familia == "Sopas Maruchan instantáneas"


async def test_el_comando_pide_la_frase(motor, sesion, semilla, monkeypatch, capsys):
    monkeypatch.setattr(
        "app.core.db.CrearSesion",
        async_sessionmaker(motor, expire_on_commit=False, class_=AsyncSession),
    )
    from app.cli import compra_zuko

    await _como_el_servidor(sesion)
    assert await compra_zuko(solo_resumen=True) == 0
    salida = capsys.readouterr().out
    assert "T-103 Zuko jamaica 8 gr exhibidor (12/8): 120 a $47.00 = $5,640.00 (NUEVO)" in salida
    assert "Total: 600 exhibidores, $28,200.00." in salida
    assert "Familia: Sopa Maruchan. Sin precio de venta" in salida
    assert "Entran a: Bodega principal, como compra" in salida

    monkeypatch.setattr("builtins.input", lambda _="": "no")
    assert await compra_zuko() == 1
    assert (
        await sesion.execute(text("SELECT count(*) FROM productos WHERE sku = 'T-103'"))
    ).scalar_one() == 0

    monkeypatch.setattr("builtins.input", lambda _="": "zuko")
    assert await compra_zuko() == 0
    assert "Listo. 7 artículo(s) nuevo(s) y la compra EN-" in capsys.readouterr().out
    await sesion.rollback()
    assert await _existencia(sesion, "T-107") == 60

    # Otra vez: ya estaba, no pregunta ni cambia nada.
    assert await compra_zuko() == 0
    assert "Ya estaba: la compra es EN-" in capsys.readouterr().out
