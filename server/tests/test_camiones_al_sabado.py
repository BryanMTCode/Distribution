"""Los camiones de las rutas 1101–1106 como quedaron el sábado 10 (ADR 0002 §99).

1. **La hoja cuadra**: 175 renglones de seis rutas, inventario + carga = total
   en cada uno, el dinero de cada hoja es el del Excel, y el precio de cada
   artículo es el mismo que trae el archivo de artículos.
2. **Si falta un vendedor no se toca nada**, y se dice cuál.
3. **Cada camión queda en el TOTAL de su hoja**, con un ajuste de conteo que
   lleva la cuenta en la nota; lo que el camión tenía y no viene en la hoja
   queda en cero; los precios suben a los verdaderos; los siete artículos que
   faltaban se dan de alta; la bodega no se toca.
4. **Dos veces no duplica nada.**
5. **El comando pide la frase**, y `--resumen` no cambia nada.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.asyncio

VENDEDORES = [f"VEND{n}" for n in range(1101, 1107)]

# Los precios que tenía el catálogo antes del Excel de cargas.
PRECIOS_VIEJOS = {"S-108": "579", "S-109": "900", "S-110": "649", "S-111": "423"}
NUEVOS = ("E-103", "E-104", "E-105", "E-106", "E-107", "E-108", "E-109")


def _cs():
    # Importado aquí y no arriba: ver `_bb` en test_base_en_blanco.py.
    from app.infra import camiones_al_sabado

    return camiones_al_sabado


async def _como_el_servidor(sesion, *, vendedores=VENDEDORES):
    """La base como está en el servidor: en blanco con los 25 artículos del primer
    Excel a sus precios de entonces, y los vendedores que dio de alta la dirección.
    El primero ya trae camión, con algo que no viene en su hoja."""
    from app.infra import base_en_blanco as bb

    articulos = [
        replace(a, precio=Decimal(PRECIOS_VIEJOS.get(a.sku, a.precio)))
        for a in bb.leer_articulos()
        if a.sku not in NUEVOS
    ]
    await bb.poner_en_blanco(sesion, articulos, quien=await bb.quien_registra(sesion))
    sucursal = (
        await sesion.execute(text("SELECT id FROM sucursales ORDER BY codigo LIMIT 1"))
    ).scalar_one()
    ids = {}
    for codigo in vendedores:
        ids[codigo] = uuid.uuid4()
        await sesion.execute(
            text(
                "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
                "                      rol_codigo, creado_en, actualizado_en) "
                "VALUES (:id, :s, :c, :n, 'x', 'vendedor', now(), now())"
            ),
            {"id": ids[codigo], "s": sucursal, "c": codigo, "n": f"Ruta {codigo[4:]}"},
        )
    if "VEND1101" in ids:
        camion = uuid.uuid4()
        await sesion.execute(
            text(
                "INSERT INTO almacenes (id, codigo, nombre, tipo, sucursal_id, responsable_id) "
                "VALUES (:id, 'CAMION_VEND1101', 'Camión de Ruta 1101', 'camion', :s, :u)"
            ),
            {"id": camion, "s": sucursal, "u": ids["VEND1101"]},
        )
        await sesion.execute(
            text("UPDATE usuarios SET almacen_id = :a WHERE id = :u"),
            {"a": camion, "u": ids["VEND1101"]},
        )
        await sesion.execute(
            text(
                "INSERT INTO existencias (almacen_id, producto_id, cantidad) "
                "SELECT :a, id, CASE sku WHEN 'F-101' THEN 5 ELSE 3 END "
                "  FROM productos WHERE sku IN ('F-101', 'T-101')"
            ),
            {"a": camion},
        )
    await sesion.commit()
    return ids


async def _camion(sesion, codigo: str) -> dict[str, Decimal]:
    filas = (
        await sesion.execute(
            text(
                "SELECT p.sku, e.cantidad FROM usuarios u "
                "  JOIN existencias e ON e.almacen_id = u.almacen_id "
                "  JOIN productos p ON p.id = e.producto_id "
                " WHERE u.codigo = :c"
            ),
            {"c": codigo},
        )
    ).all()
    return {sku: cantidad for sku, cantidad in filas}


async def _precio(sesion, sku: str):
    return (
        await sesion.execute(
            text(
                "SELECT pr.precio, pr.version FROM precios pr "
                "  JOIN productos p ON p.id = pr.producto_id "
                "  JOIN listas_precios l ON l.id = pr.lista_id AND l.es_default "
                " WHERE p.sku = :s AND pr.unidad_codigo = 'PZA'"
            ),
            {"s": sku},
        )
    ).one()


async def _quien(sesion):
    from app.infra.base_en_blanco import quien_registra

    return await quien_registra(sesion)


async def test_la_hoja_cuadra_con_el_excel():
    from app.infra.base_en_blanco import leer_articulos

    renglones = _cs().leer_camiones()
    assert len(renglones) == 175
    assert _cs().vendedores_de(renglones) == VENDEDORES
    # El dinero de cada hoja, como lo suma el Excel con sus precios. La 1105 dice
    # $14,778 porque cobra 9 Habaneros —AUDITORIA 1, A PAGAR 9—, pero en el camión
    # quedaron 10 (TOTAL), que es lo que se carga: un Habanero más, $160.
    valor = {}
    for r in renglones:
        valor[r.ruta] = valor.get(r.ruta, Decimal(0)) + r.total * r.precio
    assert valor == {
        "1101": Decimal("16945"), "1102": Decimal("17744"), "1103": Decimal("20119"),
        "1104": Decimal("14938"), "1105": Decimal("14778") + 160, "1106": Decimal("14819"),
    }
    # Un solo precio por artículo, y es el del archivo de artículos: los dos
    # archivos no pueden contradecirse.
    precios, avisos = _cs().precio_de_cada_articulo(renglones)
    del_archivo = {a.sku: a.precio for a in leer_articulos()}
    assert {sku: del_archivo[sku] for sku in precios} == precios
    assert precios["S-108"] == Decimal("676") and precios["E-108"] == Decimal("20")
    # La ruta 1101 trae el Super Mix a $19; las otras cinco, a $20.
    assert len(avisos) == 2 and all("ruta 1101" in a for a in avisos)


async def test_lo_que_no_cuadra_se_dice_antes(tmp_path):
    archivo = tmp_path / "camiones.csv"
    encabezado = "ruta,vendedor,producto_excel,sku,inventario,piezas,total,precio\n"
    archivo.write_text(encabezado + "1101,VEND1101,SALSA,V-100,15,24,40,17\n", encoding="utf-8")
    with pytest.raises(_cs().HojaInvalida, match="no da el total 40"):
        _cs().leer_camiones(archivo)
    archivo.write_text(
        encabezado + "1101,VEND1101,SALSA,V-100,1,0,1,17\n1101,vend1101,SALSA,v-100,1,0,1,17\n",
        encoding="utf-8",
    )
    with pytest.raises(_cs().HojaInvalida, match="dos veces"):
        _cs().leer_camiones(archivo)


async def test_si_falta_un_vendedor_no_se_toca_nada(sesion, semilla):
    await _como_el_servidor(sesion, vendedores=VENDEDORES[:5])
    renglones = _cs().leer_camiones()
    plan = await _cs().planear(sesion, renglones)
    assert plan.faltan_vendedores == ["VEND1106"] and not plan.se_puede

    with pytest.raises(_cs().HojaInvalida, match="VEND1106"):
        await _cs().aplicar(sesion, renglones, quien=await _quien(sesion))
    await sesion.rollback()
    assert (await sesion.execute(text("SELECT count(*) FROM ajustes_camion"))).scalar_one() == 0
    assert (await _precio(sesion, "S-108")).precio == Decimal("579")
    assert (
        await sesion.execute(text("SELECT count(*) FROM productos WHERE sku = 'E-104'"))
    ).scalar_one() == 0


async def test_cada_camion_queda_como_el_excel(sesion, semilla):
    ids = await _como_el_servidor(sesion)
    bodega_antes = (
        await sesion.execute(
            text("SELECT sum(e.cantidad) FROM existencias e "
                 "  JOIN almacenes a ON a.id = e.almacen_id AND a.tipo = 'bodega'")
        )
    ).scalar_one()
    renglones = _cs().leer_camiones()

    plan = await _cs().planear(sesion, renglones)
    assert plan.se_puede
    assert [a.sku for a in plan.articulos_nuevos] == [
        "E-104", "E-108", "E-109", "E-107", "E-105", "E-103", "E-106"
    ]
    assert {c.sku: (c.antes, c.despues) for c in plan.precios} == {
        "S-108": (Decimal("579"), Decimal("676")), "S-109": (Decimal("900"), Decimal("681")),
        "S-110": (Decimal("649"), Decimal("947")), "S-111": (Decimal("423"), Decimal("435")),
    }
    assert plan.camiones_nuevos == VENDEDORES[1:]
    assert plan.movidos_despues == {"VEND1101": 0}

    resultado = await _cs().aplicar(sesion, renglones, quien=await _quien(sesion))
    assert (resultado.articulos_nuevos, resultado.precios, resultado.camiones_nuevos) == (
        7, 4, 5
    )

    # Cada camión, igual a su hoja; lo que no venía, en cero.
    for codigo in VENDEDORES:
        hoja = {r.sku: r.total for r in renglones if r.vendedor == codigo}
        camion = await _camion(sesion, codigo)
        assert {s: c for s, c in camion.items() if s in hoja or c} == {
            s: c for s, c in hoja.items() if c or s in camion
        }, codigo
    assert (await _camion(sesion, "VEND1101"))["F-101"] == 0
    assert plan.piezas["VEND1103"] == sum(
        r.total for r in renglones if r.vendedor == "VEND1103"
    )

    # El vendedor nuevo trae su camión, como lo crea el alta del panel.
    codigo, responsable = (
        await sesion.execute(
            text("SELECT a.codigo, a.responsable_id FROM usuarios u "
                 "  JOIN almacenes a ON a.id = u.almacen_id WHERE u.codigo = 'VEND1104'")
        )
    ).one()
    assert (codigo, responsable) == ("CAMION_VEND1104", ids["VEND1104"])

    # La cuenta de la hoja, en la nota del ajuste; y el ajuste viaja al teléfono
    # de su vendedor.
    nota, folio = (
        await sesion.execute(
            text("SELECT aj.nota, aj.folio FROM ajustes_camion aj "
                 "  JOIN productos p ON p.id = aj.producto_id "
                 "  JOIN usuarios u ON u.almacen_id = aj.almacen_id "
                 " WHERE u.codigo = 'VEND1101' AND p.sku = 'T-101'")
        )
    ).one()
    assert "inventario 4 + carga de la tarde 6 = 10" in nota
    assert "sábado 10 de octubre de 2026" in nota and folio.startswith("AC-")
    al_telefono = (
        await sesion.execute(
            text("SELECT count(*) FROM change_log "
                 " WHERE entidad = 'ajuste_camion' AND vendedor_id = :v"),
            {"v": ids["VEND1102"]},
        )
    ).scalar_one()
    assert al_telefono == sum(
        1 for r in renglones if r.vendedor == "VEND1102" and r.total
    )

    # Una operación por camión (ADR 0002 §146): cada camión, una sola cabeza, y
    # sus demás ajustes van con ella.
    assert len(resultado.operaciones) == len(VENDEDORES)
    cabezas = (
        await sesion.execute(
            text("SELECT almacen_id, count(*) FILTER (WHERE pareja_id IS NULL) AS cabezas, "
                 "       count(DISTINCT COALESCE(pareja_id, id)) AS operaciones "
                 "  FROM ajustes_camion GROUP BY almacen_id")
        )
    ).all()
    assert all((c.cabezas, c.operaciones) == (1, 1) for c in cabezas), cabezas

    # Los precios verdaderos, con su versión nueva; los nuevos en su familia.
    assert tuple(await _precio(sesion, "S-110")) == (Decimal("947.00"), 2)
    assert tuple(await _precio(sesion, "E-108")) == (Decimal("20.00"), 1)
    familia = (
        await sesion.execute(
            text("SELECT c.nombre FROM productos p JOIN categorias c ON c.id = p.categoria_id "
                 " WHERE p.sku = 'E-104'")
        )
    ).scalar_one()
    assert familia == "Botanas Javi"

    # La bodega, intacta: su inventario ya era el de después de cargar.
    bodega_despues = (
        await sesion.execute(
            text("SELECT sum(e.cantidad) FROM existencias e "
                 "  JOIN almacenes a ON a.id = e.almacen_id AND a.tipo = 'bodega'")
        )
    ).scalar_one()
    assert bodega_despues == bodega_antes
    assert (
        await sesion.execute(text("SELECT count(*) FROM auditoria WHERE accion = 'al_sabado'"))
    ).scalar_one() == 1


async def test_dos_veces_no_duplica_y_avisa_lo_movido_despues(sesion, semilla):
    await _como_el_servidor(sesion)
    renglones = _cs().leer_camiones()
    await _cs().aplicar(sesion, renglones, quien=await _quien(sesion))
    ajustes = (await sesion.execute(text("SELECT count(*) FROM ajustes_camion"))).scalar_one()

    segunda = await _cs().aplicar(sesion, renglones, quien=await _quien(sesion))
    assert (segunda.ajustes, segunda.precios, segunda.articulos_nuevos,
            segunda.camiones_nuevos) == (0, 0, 0, 0)
    assert (
        await sesion.execute(text("SELECT count(*) FROM ajustes_camion"))
    ).scalar_one() == ajustes
    plan = await _cs().planear(sesion, renglones)
    assert set(plan.ajustes.values()) == {0}
    # Sus propios ajustes no cuentan como «movido después del sábado».
    assert set(plan.movidos_despues.values()) == {0}

    # Una venta del lunes sí se avisa.
    await sesion.execute(
        text(
            "INSERT INTO movimientos_inventario (tipo, almacen_origen_id, producto_id, "
            "       cantidad, documento_tipo, documento_id, fecha_servidor) "
            "SELECT 'venta', u.almacen_id, p.id, 1, 'venta', gen_random_uuid(), "
            "       TIMESTAMPTZ '2026-10-12 10:00-07' "
            "  FROM usuarios u, productos p WHERE u.codigo = 'VEND1102' AND p.sku = 'V-100'"
        )
    )
    await sesion.commit()
    plan = await _cs().planear(sesion, renglones)
    assert plan.movidos_despues["VEND1102"] == 1


async def test_el_comando_pide_la_frase(motor, sesion, semilla, monkeypatch, capsys):
    monkeypatch.setattr(
        "app.core.db.CrearSesion",
        async_sessionmaker(motor, expire_on_commit=False, class_=AsyncSession),
    )
    from app.cli import camiones_al_sabado

    await _como_el_servidor(sesion)
    assert await camiones_al_sabado(solo_resumen=True) == 0
    salida = capsys.readouterr().out
    assert "S-108 Costal Minino 15 kilos: $579.00 → $676.00" in salida
    assert "VEND1101: 350 piezas, $16,945.00" in salida

    monkeypatch.setattr("builtins.input", lambda _="": "no")
    assert await camiones_al_sabado() == 1
    assert (await sesion.execute(text("SELECT count(*) FROM ajustes_camion"))).scalar_one() == 0

    monkeypatch.setattr("builtins.input", lambda _="": "camiones")
    assert await camiones_al_sabado() == 0
    assert "Listo." in capsys.readouterr().out
    await sesion.rollback()
    assert (await sesion.execute(text("SELECT count(*) FROM ajustes_camion"))).scalar_one() > 0
