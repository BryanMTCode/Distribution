"""Las tiendas de las rutas 1101–1107 desde los Excel de la dirección (ADR 0002 §120).

1. **El archivo es el de los Excel**: 1,834 tiendas, con los códigos 100 a 1933
   en el orden pedido —la ruta 1101 de lunes a sábado, luego la 1102…— y cada
   día con las tiendas que dijo la dirección.
2. **Lo que no cuadra se dice antes** y no se carga nada.
3. **Cada tienda queda con su código, su ruta y su día**: la ruta se encuentra
   por su número o por su vendedor, y la que falta se crea con su titular y su
   alcance; el día entra al plan de visita; el teléfono de la ruta la recibe.
4. **Dos veces no duplica**, y un código que ya es de otra tienda no toca nada.
5. **El comando pide la frase**, y `--resumen` no cambia nada.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.asyncio


def _tr():
    # Importado aquí y no arriba: ver `_bb` en test_base_en_blanco.py.
    from app.infra import tiendas_de_rutas

    return tiendas_de_rutas


async def _como_el_servidor(sesion, semilla) -> dict:
    """Los vendedores VEND1101…VEND1106 (el de la 1107 todavía no existe). La
    ruta 1101 ya está dada de alta como «1101»; la del VEND1102 se llama
    «CENTRO»; las demás no existen."""
    ids = {}
    for n in range(1101, 1107):
        ids[n] = uuid.uuid4()
        await sesion.execute(
            text(
                "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
                "                      rol_codigo, creado_en, actualizado_en) "
                "VALUES (:u, :s, :c, :n, 'x', 'vendedor', now(), now())"
            ),
            {"u": ids[n], "s": semilla["sucursal"], "c": f"VEND{n}", "n": f"Vendedor {n}"},
        )
    rutas = {1101: uuid.uuid4(), 1102: uuid.uuid4()}
    for n, codigo in ((1101, "1101"), (1102, "CENTRO")):
        await sesion.execute(
            text("INSERT INTO rutas (id, codigo, nombre, vendedor_id) VALUES (:r, :c, :n, :v)"),
            {"r": rutas[n], "c": codigo, "n": f"Ruta {codigo}", "v": ids[n]},
        )
        await sesion.execute(
            text("INSERT INTO usuarios_rutas (usuario_id, ruta_id) VALUES (:u, :r)"),
            {"u": ids[n], "r": rutas[n]},
        )
    await sesion.commit()
    return {"vendedores": ids, "rutas": rutas}


async def test_el_archivo_es_el_de_los_excel():
    tr = _tr()
    tiendas = tr.leer_tiendas()
    assert len(tiendas) == 1834
    assert [t.codigo for t in tiendas] == [str(c) for c in range(100, 1934)]
    rutas = tr.por_ruta(tiendas)
    # Cada ruta sigue donde terminó la anterior.
    assert [(r, ts[0].codigo, ts[-1].codigo) for r, ts in rutas.items()] == [
        ("1101", "100", "359"), ("1102", "360", "615"), ("1103", "616", "855"),
        ("1104", "856", "1128"), ("1105", "1129", "1398"), ("1106", "1399", "1666"),
        ("1107", "1667", "1933"),
    ]
    assert tr.resumen_de_dias(rutas["1103"]) == (
        "lun 50 · mar 47 · mié 32 · jue 38 · vie 42 · sáb 31"
    )
    primera = tiendas[0]
    assert (primera.ruta, primera.dia, primera.orden, primera.nombre, primera.numero,
            primera.localidad) == ("1101", "lunes", 1, "PUESTO PLAYA SUR", "S/N", "MAZATLÁN")
    # La primera del martes de la 1101 es la 46 de la hoja: código 145.
    assert next(t for t in tiendas if t.ruta == "1101" and t.dia == "martes").codigo == "145"
    # «701.0» en la hoja es el 701.
    assert next(t for t in tiendas if t.nombre == "ABARROTES LA MORENA").numero == "701"


async def test_lo_que_no_cuadra_se_dice_antes(tmp_path):
    tr = _tr()
    renglones = tr.TIENDAS.read_text(encoding="utf-8").splitlines()
    corto = tmp_path / "corto.csv"
    corto.write_text("\n".join(renglones[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(tr.HojaInvalida, match="la ruta 1107 trae 27 tiendas el sabado y "
                                              "deben ser 28"):
        tr.leer_tiendas(corto)

    salteado = tmp_path / "salteado.csv"
    salteado.write_text("\n".join([renglones[0], *renglones[2:]]) + "\n", encoding="utf-8")
    with pytest.raises(tr.HojaInvalida, match="trae el código 101 y le toca 100"):
        tr.leer_tiendas(salteado)


async def test_cada_tienda_queda_con_su_codigo_su_ruta_y_su_dia(sesion, semilla):
    tr = _tr()
    base = await _como_el_servidor(sesion, semilla)
    tiendas = tr.leer_tiendas()
    plan = await tr.planear(sesion, tiendas)
    assert plan.se_puede and len(plan.nuevas) == 1834
    assert plan.rutas["1101"].id == base["rutas"][1101] and not plan.rutas["1101"].nueva
    # La del VEND1102 se llama CENTRO: se encuentra por su vendedor.
    assert plan.rutas["1102"].codigo == "CENTRO"
    assert plan.rutas["1103"].nueva and plan.rutas["1103"].codigo == "R1103"
    assert plan.rutas["1107"].titular is None
    assert plan.repetidas_en_el_excel == [
        "908 y 909 ABARROTES SIXTO (SELENE 1116, VILLA GALAXIA), ruta 1104 martes"
    ]

    resultado = await tr.aplicar(sesion, tiendas, quien=semilla["admin"])
    assert resultado.tiendas == 1834
    assert resultado.rutas_nuevas == ["R1103", "R1104", "R1105", "R1106", "R1107"]

    cliente = (
        await sesion.execute(
            text("SELECT c.*, r.codigo AS ruta FROM clientes c JOIN rutas r ON r.id = c.ruta_id "
                 " WHERE c.codigo = '100'")
        )
    ).mappings().one()
    assert cliente["nombre_comercial"] == "PUESTO PLAYA SUR"
    assert (cliente["calle"], cliente["numero"], cliente["colonia"], cliente["municipio"]) == (
        "EMBARCADERO DE LA PUNTILLA", "S/N", "PLAYA SUR", "MAZATLÁN"
    )
    assert cliente["ruta"] == "1101" and cliente["secuencia"] == 1
    assert cliente["estatus"] == "activo" and cliente["lista_precios_id"] == semilla[
        "lista_precios"]
    assert cliente["plan_visita"] == [{"dia": 1, "semana": None}]
    assert cliente["lat"] is None
    # La última es del sábado de la 1107.
    ultima = (
        await sesion.execute(
            text("SELECT r.codigo, c.plan_visita FROM clientes c JOIN rutas r ON r.id = c.ruta_id "
                 " WHERE c.codigo = '1933'")
        )
    ).mappings().one()
    assert ultima["codigo"] == "R1107" and ultima["plan_visita"] == [{"dia": 6, "semana": None}]

    # Cada ruta con las suyas; la nueva con su titular y su alcance.
    por_ruta = dict(
        (
            await sesion.execute(
                text("SELECT r.codigo, count(*) FROM clientes c JOIN rutas r ON r.id = c.ruta_id "
                     " GROUP BY r.codigo")
            )
        ).all()
    )
    assert por_ruta == {"1101": 260, "CENTRO": 256, "R1103": 240, "R1104": 273,
                        "R1105": 270, "R1106": 268, "R1107": 267}
    alcance = (
        await sesion.execute(
            text("SELECT count(*) FROM usuarios_rutas ur JOIN rutas r ON r.id = ur.ruta_id "
                 " WHERE r.codigo = 'R1103' AND ur.usuario_id = :u"),
            {"u": base["vendedores"][1103]},
        )
    ).scalar_one()
    assert alcance == 1
    # El teléfono de la ruta la recibe.
    deltas = (
        await sesion.execute(
            text("SELECT count(DISTINCT entidad_id) FROM change_log "
                 " WHERE entidad = 'cliente' AND ruta_id = :r"),
            {"r": base["rutas"][1101]},
        )
    ).scalar_one()
    assert deltas == 260


async def test_dos_veces_no_duplica_y_un_codigo_ajeno_no_toca_nada(sesion, semilla):
    tr = _tr()
    await _como_el_servidor(sesion, semilla)
    tiendas = tr.leer_tiendas()
    await tr.aplicar(sesion, tiendas, quien=semilla["admin"])
    plan = await tr.planear(sesion, tiendas)
    assert plan.nuevas == [] and plan.ya_estaban == 1834
    assert (await tr.aplicar(sesion, tiendas, quien=semilla["admin"])).tiendas == 0
    assert (await sesion.execute(text("SELECT count(*) FROM clientes"))).scalar_one() == 1834

    await sesion.execute(text("UPDATE clientes SET nombre_comercial = 'OTRA' WHERE codigo = '100'"))
    await sesion.execute(text("DELETE FROM clientes WHERE codigo = '101'"))
    await sesion.commit()
    plan = await tr.planear(sesion, tiendas)
    assert not plan.se_puede
    assert plan.conflictos == ["el código 100 ya es «OTRA», no «PUESTO PLAYA SUR»"]
    with pytest.raises(tr.HojaInvalida):
        await tr.aplicar(sesion, tiendas, quien=semilla["admin"])
    await sesion.rollback()
    assert (await sesion.execute(text("SELECT count(*) FROM clientes"))).scalar_one() == 1833


async def test_el_comando_pide_la_frase(motor, sesion, semilla, monkeypatch, capsys):
    monkeypatch.setattr(
        "app.core.db.CrearSesion",
        async_sessionmaker(motor, expire_on_commit=False, class_=AsyncSession),
    )
    from app.cli import tiendas_de_rutas

    await _como_el_servidor(sesion, semilla)
    assert await tiendas_de_rutas(solo_resumen=True) == 0
    salida = capsys.readouterr().out
    assert "1,834 tiendas, códigos 100 a 1933" in salida
    assert ("1101 → ruta 1101 «Ruta 1101», de VEND1101 · Vendedor 1101: 260 tiendas, códigos "
            "100 a 359 (lun 45 · mar 44 · mié 47 · jue 46 · vie 37 · sáb 41)") in salida
    assert "1107 → ruta NUEVA R1107 «Ruta 1107», SIN vendedor" in salida
    assert "repetida en el Excel (se cargan las dos): 908 y 909 ABARROTES SIXTO" in salida

    monkeypatch.setattr("builtins.input", lambda _="": "no")
    assert await tiendas_de_rutas() == 1
    assert (await sesion.execute(text("SELECT count(*) FROM clientes"))).scalar_one() == 0

    monkeypatch.setattr("builtins.input", lambda _="": "tiendas")
    assert await tiendas_de_rutas() == 0
    assert "Listo. 1,834 tienda(s) nueva(s), ruta(s) nueva(s): R1103" in capsys.readouterr().out
    await sesion.rollback()
    assert (await sesion.execute(text("SELECT count(*) FROM clientes"))).scalar_one() == 1834

    # Otra vez: ya estaban todas, no pregunta ni cambia nada.
    assert await tiendas_de_rutas() == 0
    assert "Ya estaban todas" in capsys.readouterr().out
