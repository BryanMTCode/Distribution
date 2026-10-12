"""La revisión de la app y del dashboard, en el panel (ADR 0002 §112).

«Agrega al dashboard un checklist de todo lo que debo checar en la app para ver
que las funcionalidades están bien.» Y después: dashboard y app juntos.

1. **La lista está completa y bien escrita**: claves únicas, cada punto con
   cómo y qué debe pasar.
2. **Abre en la versión que traen los teléfonos**, y dice quién falta.
3. **Bien o falla, por versión**: una falla pide nota; se desmarca; la versión
   siguiente empieza en blanco y recuerda lo que falló en la anterior.
4. **Dos partes**, teléfono y dashboard, con su avance cada una; una parte se
   vuelve a empezar sin tocar la otra.
5. **Se llega desde el Tablero y el menú**, y la oficina entera la usa.
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import text

from app.infra.revision_app import PARTES, PUNTOS, SECCIONES, orden_de_version
from tests.conftest import csrf_del_panel, solo_texto
from tests.test_panel_objetivos import _crear_usuario, _entrar

pytestmark = pytest.mark.asyncio


async def test_la_lista_esta_completa_y_bien_escrita():
    claves = [p.clave for s in SECCIONES for p in s.puntos]
    assert len(claves) == len(set(claves)) == len(PUNTOS) >= 70
    for s in SECCIONES:
        assert s.titulo and s.quien and s.puntos and s.parte in PARTES
        for p in s.puntos:
            assert re.fullmatch(r"[a-z_]+", p.clave), p.clave
            assert p.titulo and len(p.pasos) >= 10 and len(p.esperado) >= 20, p.clave
            assert p.desde is None or re.fullmatch(r"\+\d+", p.desde), p.clave
    # Las dos partes tienen lo suyo.
    assert {s.parte for s in SECCIONES} == set(PARTES)
    # La +100 va después de la +99, no antes.
    assert orden_de_version("0.1.0+100") > orden_de_version("0.1.0+99")


async def _telefono(sesion, semilla, version: str) -> None:
    await sesion.execute(
        text("INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en, "
             "app_version) VALUES (:d, :u, 'Hyundai', 'activo', now(), :v)"),
        {"d": uuid.uuid4(), "u": semilla["vendedor"], "v": version},
    )
    await sesion.commit()


DEL_TELEFONO = sum(len(s.puntos) for s in SECCIONES if s.parte == "telefono")
DEL_DASHBOARD = sum(len(s.puntos) for s in SECCIONES if s.parte == "dashboard")


async def _marcar(cliente, r, **datos):
    return await cliente.post(
        "/panel/revision-app/marcar",
        data={"csrf": csrf_del_panel(cliente, r), **datos},
        follow_redirects=True,
    )


async def test_bien_o_falla_por_version(cliente, sesion, semilla):
    await _telefono(sesion, semilla, "0.1.0+35")
    await _entrar(cliente)

    # Abre en la versión que trae el teléfono, con todo pendiente.
    r = await cliente.get("/panel/revision-app")
    plano = solo_texto(r)
    assert 'value="0.1.0+35"' in r.text
    assert "Con esta versión: Juan Pérez · VEND01." in plano
    assert f"0 de {DEL_TELEFONO} puntos revisados en el teléfono · 0.1.0+35" in plano
    for s in SECCIONES:
        assert (f'id="seccion_{s.clave}"' in r.text) == (s.parte == "telefono"), s.clave
    # Lo que llegó en esta versión se distingue.
    assert "nuevo en esta versión" in r.text.split('id="punto_objetivos_app"')[1][:600]

    r = await _marcar(cliente, r, version="0.1.0+35", punto="venta_efectivo", estado="bien")
    assert f"1 de {DEL_TELEFONO} puntos revisados" in solo_texto(r)
    assert 'id="marca_venta_efectivo"' in r.text

    # Una falla sin decir qué pasó no se guarda.
    r = await _marcar(cliente, r, version="0.1.0+35", punto="ticket", estado="falla")
    assert "Escribe qué falló" in solo_texto(r)
    r = await _marcar(cliente, r, version="0.1.0+35", punto="ticket", estado="falla",
                      nota="No comparte por WhatsApp")
    assert "Falla: No comparte por WhatsApp" in solo_texto(r)

    # «Con falla» muestra solo esa.
    r = await cliente.get("/panel/revision-app?version=0.1.0%2B35&filtro=falla")
    assert 'id="punto_ticket"' in r.text and 'id="punto_venta_efectivo"' not in r.text

    # Desmarcar la regresa a pendiente.
    r = await cliente.post(
        "/panel/revision-app/desmarcar",
        data={"csrf": csrf_del_panel(cliente, r), "version": "0.1.0+35",
              "punto": "venta_efectivo"},
        follow_redirects=True,
    )
    assert 'id="marca_venta_efectivo"' not in r.text

    # La versión siguiente empieza en blanco y recuerda lo que falló.
    r = await cliente.get("/panel/revision-app?version=0.1.0%2B36")
    plano = solo_texto(r)
    assert f"0 de {DEL_TELEFONO} puntos revisados en el teléfono · 0.1.0+36" in plano
    assert "En la 0.1.0+35: FALLA — No comparte por WhatsApp." in plano
    assert "Juan Pérez · VEND01 (0.1.0+35)" in plano


async def test_la_version_se_escribe_como_en_la_app(cliente, sesion, semilla):
    await _entrar(cliente)
    r = await cliente.get("/panel/revision-app")
    assert 'id="sin_version"' in r.text
    r = await _marcar(cliente, r, version="35", punto="venta_efectivo", estado="bien")
    assert "por ejemplo 0.1.0+35" in solo_texto(r)
    assert (await sesion.execute(text("SELECT count(*) FROM revision_app"))).scalar_one() == 0


async def test_se_llega_desde_el_tablero_y_la_usa_un_supervisor(cliente, sesion, semilla):
    await _entrar(cliente)
    tablero = await cliente.get("/panel")
    assert 'href="/panel/revision-app"' in tablero.text
    # Es una pestaña de Teléfonos.
    telefonos = await cliente.get("/panel/equipos")
    assert 'href="/panel/revision-app"' in telefonos.text

    # Quien prueba la app no siempre es el admin: un supervisor también marca.
    await cliente.get("/panel/salir")
    await _crear_usuario(sesion, semilla, "SUP01", "supervisor")
    await _entrar(cliente, "SUP01")
    r = await cliente.get("/panel/revision-app?version=0.1.0%2B35")
    assert r.status_code == 200
    r = await _marcar(cliente, r, version="0.1.0+35", punto="mi_dia", estado="bien")
    assert "Bien · Usuario SUP01" in solo_texto(r)


async def test_el_dashboard_es_otra_parte_y_se_vuelve_a_empezar(cliente, sesion, semilla):
    """Cuando solo se actualizó el servidor, el dashboard se vuelve a revisar
    sin tocar lo que ya se marcó del teléfono."""
    await _telefono(sesion, semilla, "0.1.0+35")
    await _entrar(cliente)
    r = await cliente.get("/panel/revision-app?parte=dashboard")
    plano = solo_texto(r)
    for s in SECCIONES:
        assert (f'id="seccion_{s.clave}"' in r.text) == (s.parte == "dashboard"), s.clave
    assert f"En el dashboard · 0 de {DEL_DASHBOARD}" in plano

    r = await _marcar(cliente, r, version="0.1.0+35", punto="venta_efectivo", estado="bien")
    r = await _marcar(cliente, r, version="0.1.0+35", punto="panel_cerrar_corte",
                      estado="bien", parte="dashboard")
    # Vuelve a la parte del dashboard, con su avance y el del teléfono.
    plano = solo_texto(r)
    assert f"1 de {DEL_DASHBOARD} puntos revisados en el dashboard" in plano
    assert f"En el teléfono · 1 de {DEL_TELEFONO}" in plano

    r = await cliente.post(
        "/panel/revision-app/volver-a-empezar",
        data={"csrf": csrf_del_panel(cliente, r), "version": "0.1.0+35", "parte": "dashboard"},
        follow_redirects=True,
    )
    plano = solo_texto(r)
    assert "En el dashboard: 1 marca(s) borrada(s)" in plano
    assert f"En el dashboard · 0 de {DEL_DASHBOARD}" in plano
    assert f"En el teléfono · 1 de {DEL_TELEFONO}" in plano

