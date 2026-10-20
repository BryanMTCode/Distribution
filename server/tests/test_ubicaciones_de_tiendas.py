"""Las coordenadas de las tiendas, del Excel LOCALES_MAZATLAN_2 (ADR 0002 §148).

1. **El archivo es el del Excel**: de lunes a sábado; la hoja «Ruta 1101» entera
   es de la 1101; las dos longitudes sin signo, corregidas, y la latitud de 26,
   sin coordenadas.
2. **Cada renglón encuentra su tienda** de las 1,834 de §120: por nombre y
   domicilio casi todas, en su ruta aunque la ruta se llame «CENTRO»; la
   repetida en la base recibe las dos; la que está en otra ruta se ubica sin
   moverla; las que no están se dicen.
3. **Se ubican con origen «manual»**, y le llega al teléfono; los números que
   Excel volvió fecha se corrigen.
4. **La que ya tiene ubicación no se toca**, y dos veces no cambia nada.
5. **El comando pide la frase**, y `--resumen` no cambia nada.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_tiendas_de_rutas import _como_el_servidor, _tr

pytestmark = pytest.mark.asyncio


def _ut():
    from app.infra import ubicaciones_de_tiendas

    return ubicaciones_de_tiendas


async def _con_las_tiendas(sesion, semilla) -> None:
    """La base como quedó después de §120: las 1,834 tiendas, sin coordenadas."""
    tr = _tr()
    await _como_el_servidor(sesion, semilla)
    await tr.aplicar(sesion, tr.leer_tiendas(), quien=semilla["admin"])


async def _cliente(sesion, codigo: str) -> dict:
    return dict(
        (
            await sesion.execute(
                text("SELECT lat, lng, ubicacion_origen, numero, ubicacion IS NOT NULL AS punto "
                     "  FROM clientes WHERE codigo = :c"),
                {"c": codigo},
            )
        ).mappings().one()
    )


async def test_el_archivo_es_el_del_excel():
    ut = _ut()
    renglones = ut.leer_renglones()
    assert len(renglones) == 1835
    assert {r.dia for r in renglones} == {1, 2, 3, 4, 5, 6}
    por_ruta = {ruta: sum(1 for r in renglones if r.ruta == ruta) for ruta in ut.RUTAS}
    assert por_ruta == {"1101": 261, "1102": 260, "1103": 241, "1104": 274, "1105": 257,
                        "1106": 269, "1107": 273}
    # La hoja «Ruta 1101»: lunes a miércoles de la 1101 y jueves a sábado de la 1108.
    dias_1101 = [sum(1 for r in renglones if r.ruta == "1101" and r.dia == d) for d in range(1, 7)]
    assert dias_1101 == [46, 44, 47, 46, 37, 41]
    corregidas = {r.nombre: (r.lat, r.lng, r.correccion) for r in renglones if r.correccion}
    assert corregidas["FRUTERÍA LA BONITA"][:2] == (Decimal("23.2776040"), Decimal("-106.4058410"))
    assert corregidas["ABARROTES NAI"][:2] == (None, None)
    assert "26.2676683" in corregidas["ABARROTES NAI"][2]
    # Todas en Mazatlán.
    assert all(Decimal("22.9") < r.lat < Decimal("23.5") and r.lng < 0
               for r in renglones if r.lat is not None)


async def test_lo_que_no_cuadra_se_dice_antes(tmp_path):
    ut = _ut()
    archivo = tmp_path / "malo.csv"
    archivo.write_text(
        "ruta,dia,nombre,domicilio,latitud,longitud,visita,id_origen,correccion\n"
        "1101,7,ABARROTES DOMINGO,CALLE 1 CENTRO,23.2,-106.4,SEMANAL,1,\n"
        "1102,1,ABARROTES CHINA,CALLE 2 CENTRO,23.2,106.4,SEMANAL,2,\n",
        encoding="utf-8",
    )
    with pytest.raises(ut.HojaInvalida) as error:
        ut.leer_renglones(archivo)
    assert "día «7»" in str(error.value)
    assert "La longitud en México es negativa" in str(error.value)


async def test_cada_renglon_encuentra_su_tienda(sesion, semilla):
    ut = _ut()
    await _con_las_tiendas(sesion, semilla)
    plan = await ut.planear(sesion, ut.leer_renglones())

    assert len(plan.parejas) == 1808 and len(plan.sin_tienda) == 27
    # La ruta de la 1102 se llama CENTRO: sus tiendas se encuentran en su ruta.
    assert plan.en_otra_ruta == [
        "ABARROTES SAN JOSE viene en la 1105 y en la base es 1665 de la 1106: se ubica "
        "sin cambiarla de ruta"
    ]
    assert plan.repetidas_en_la_base == [
        "908 y 909 ABARROTES SIXTO (SELENE 1116 VILLA GALAXIA): las 2 reciben la ubicación"
    ]
    assert plan.en_otro_dia == ["1290 ABARROTESEL GUERO viene el lun y en la base es el jue"]
    assert [str(r) for r in plan.sin_coordenadas] == [
        "ABARROTES NAI (FRANCISCO TOLEDO 10140 CANTERA 2), ruta 1107 jue"
    ]
    assert [(t.codigo, t.numero, n) for t, n in plan.numeros] == [
        ("113", "46025", "1-3"), ("708", "914660", "4404-4")
    ]
    # Las 1,808 parejas, menos la de NAI, más la repetida.
    assert len(plan.por_ubicar) == 1808
    assert {r.nombre for r in plan.sin_tienda} >= {"TINYSTORE", "ABARROTES JL"}
    assert len(plan.sin_renglon) == 25 and "322 SUPER PLAYA NORTE" in {
        str(t) for t in plan.sin_renglon
    }
    assert plan.por_ruta()["1101"] == {"con_tienda": 259, "sin_tienda": 2, "ubicar": 259}


async def test_se_ubican_y_le_llega_al_telefono(sesion, semilla):
    ut = _ut()
    await _con_las_tiendas(sesion, semilla)
    resultado = await ut.aplicar(sesion, ut.leer_renglones(), quien=semilla["admin"])
    assert (resultado.ubicadas, resultado.numeros) == (1808, 2)

    primera = await _cliente(sesion, "100")
    assert primera["punto"] and primera["ubicacion_origen"] == "manual"
    assert -106.5 < float(primera["lng"]) < -106.3
    assert (await _cliente(sesion, "908"))["lat"] == (await _cliente(sesion, "909"))["lat"]
    assert (await _cliente(sesion, "113"))["numero"] == "1-3"
    assert (await _cliente(sesion, "708"))["numero"] == "4404-4"
    # El delta del cliente, con su ubicación, para el teléfono de la 1101.
    deltas = (
        await sesion.execute(
            text("SELECT count(*) FROM change_log l JOIN clientes c ON c.id = l.entidad_id "
                 " WHERE l.entidad = 'cliente' AND c.codigo = '100' "
                 "   AND l.payload->>'lat' IS NOT NULL")
        )
    ).scalar_one()
    assert deltas >= 1
    # Cada una deja su constancia, como la del panel.
    auditadas = (
        await sesion.execute(
            text("SELECT count(*) FROM auditoria WHERE entidad = 'cliente' "
                 "   AND accion = 'ubicacion'")
        )
    ).scalar_one()
    assert auditadas == 1808


async def test_la_que_ya_tiene_ubicacion_no_se_toca_y_dos_veces_no_cambia(sesion, semilla):
    ut = _ut()
    await _con_las_tiendas(sesion, semilla)
    # El vendedor la tomó con el GPS parado en la tienda.
    await sesion.execute(
        text("UPDATE clientes SET lat = 23.2, lng = -106.4, ubicacion_origen = 'gps' "
             " WHERE codigo = '100'")
    )
    await sesion.commit()
    resultado = await ut.aplicar(sesion, ut.leer_renglones(), quien=semilla["admin"])
    assert resultado.ubicadas == 1807
    primera = await _cliente(sesion, "100")
    assert (primera["lat"], primera["ubicacion_origen"]) == (Decimal("23.2000000"), "gps")

    plan = await ut.planear(sesion, ut.leer_renglones())
    assert plan.por_ubicar == [] and plan.numeros == []
    assert plan.ya_estaban == 1807
    assert len(plan.con_otra_ubicacion) == 1
    assert plan.con_otra_ubicacion[0].startswith("100 PUESTO PLAYA SUR ya está en 23.2000000")


async def test_el_comando_pide_la_frase(motor, sesion, semilla, monkeypatch, capsys):
    monkeypatch.setattr(
        "app.core.db.CrearSesion",
        async_sessionmaker(motor, expire_on_commit=False, class_=AsyncSession),
    )
    from app.cli import ubicar_tiendas

    await _con_las_tiendas(sesion, semilla)
    assert await ubicar_tiendas(solo_resumen=True) == 0
    salida = capsys.readouterr().out
    assert "1,835 tiendas, de lunes a sábado" in salida
    assert "1101: 261 en el Excel → 259 se ubican, 2 sin tienda en la base" in salida
    assert "Se ubican: 1,808. Ya estaban: 0." in salida
    assert "113 LADRIDO Y MAULLIDO (MIGUEL ALEMAN) 46025 → 1-3" in salida
    assert "No están en la base, no se dan de alta (27)" in salida
    assert "TINYSTORE (GUILLERMO NELSON 1601 SENTRO), ruta 1101 lun" in salida

    monkeypatch.setattr("builtins.input", lambda _="": "no")
    assert await ubicar_tiendas() == 1
    con_ubicacion = text("SELECT count(*) FROM clientes WHERE lat IS NOT NULL")
    assert (await sesion.execute(con_ubicacion)).scalar_one() == 0

    monkeypatch.setattr("builtins.input", lambda _="": "ubicar")
    assert await ubicar_tiendas() == 0
    assert ("Listo. 1,808 tienda(s) ubicada(s) y 2 número(s) corregido(s)."
            in capsys.readouterr().out)
    await sesion.rollback()
    assert (await sesion.execute(con_ubicacion)).scalar_one() == 1808

    # Otra vez: ya estaban todas, no pregunta ni cambia nada.
    assert await ubicar_tiendas() == 0
    assert "Ya estaban todas" in capsys.readouterr().out
