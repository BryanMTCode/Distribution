"""El menú del panel: cinco módulos, ningún enlace muerto, ninguna pantalla huérfana.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTO TIENE PRUEBAS
────────────────────────────────────────────────────────────────────────────
El menú pasó de dieciocho enlaces planos a cinco módulos (octubre 2026), y en la
misma reestructura «Liquidación» se llamó «Corte del día». Las dos cosas se
rompen en silencio:

· Una pantalla que declara `seccion="Liquidación"` cuando el menú ya dice «Corte
  del día» no marca su enlace ni abre su módulo. Nada falla; el menú
  simplemente deja de decirle a la persona dónde está.
· Un enlace a una ruta que se renombró responde 404 y nadie lo nota hasta que
  alguien lo toca.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.conftest import PASSWORD_VENDEDOR, solo_texto


def _menu():
    """El menú, importado DENTRO de cada prueba y no al recolectar.

    `app.api.admin.comun` importa `app.core.db`, que crea el motor de la base AL
    IMPORTARSE, con la configuración que haya en ese momento. Importado arriba,
    este archivo lo cargaba durante la recolección —antes de que `conftest` fije la
    URL de pruebas— y todas las pruebas que usan ese motor directamente (la poda,
    el comando de arranque) intentaban conectarse a la base de producción con el
    usuario `dsd`. Pasaban solas y fallaban en la corrida completa.
    """
    from app.api.admin.comun import NAVEGACION, modulo_de

    return NAVEGACION, modulo_de

ADMIN = Path(__file__).resolve().parents[1] / "app" / "api" / "admin"


async def _entrar(cliente) -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": "ADMIN01", "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


def test_son_cinco_modulos_a_lo_mucho():
    """La petición de la dirección: «solo 4 o 5 módulos principales»."""
    NAVEGACION, _ = _menu()
    assert 4 <= len(NAVEGACION) <= 5


def test_ninguna_pantalla_aparece_dos_veces():
    NAVEGACION, _ = _menu()
    etiquetas = [e for _, enlaces in NAVEGACION for _, e in enlaces]
    rutas = [r for _, enlaces in NAVEGACION for r, _ in enlaces]
    assert len(etiquetas) == len(set(etiquetas))
    assert len(rutas) == len(set(rutas))


def test_liquidacion_se_llama_corte_del_dia():
    NAVEGACION, _ = _menu()
    etiquetas = {e for _, enlaces in NAVEGACION for _, e in enlaces}
    assert "Corte del día" in etiquetas
    assert "Liquidación" not in etiquetas


def test_toda_pantalla_declara_una_seccion_que_esta_en_el_menu():
    """Una pantalla con una sección que el menú no conoce queda huérfana: no se
    marca su enlace ni se abre su módulo, y no falla nada que lo avise."""
    _, modulo_de = _menu()
    huerfanas = []
    for archivo in sorted(ADMIN.glob("*.py")):
        for seccion in re.findall(r'seccion="([^"]+)"', archivo.read_text(encoding="utf-8")):
            if modulo_de(seccion) is None:
                huerfanas.append(f"{archivo.name}: {seccion}")
    assert huerfanas == []


@pytest.mark.asyncio
async def test_ningun_enlace_del_menu_esta_muerto(cliente, semilla):
    NAVEGACION, _ = _menu()
    await _entrar(cliente)
    for _, enlaces in NAVEGACION:
        for ruta, etiqueta in enlaces:
            r = await cliente.get(ruta)
            assert r.status_code == 200, f"{etiqueta} ({ruta}) respondió {r.status_code}"


@pytest.mark.asyncio
async def test_se_abre_el_modulo_de_la_pantalla_actual(cliente, semilla):
    """En el corte del día, «Operación de rutas» abierto y «Almacén» cerrado."""
    await _entrar(cliente)
    html = (await cliente.get("/panel/liquidaciones")).text

    def apertura(titulo: str) -> str:
        fin = html.index(f"<summary>{titulo}</summary>")
        return html[html.rindex("<details", 0, fin) : fin]

    assert "open" in apertura("Operación de rutas")
    assert "open" not in apertura("Almacén")
    assert 'class="activo" aria-current="page">Corte del día<' in html


@pytest.mark.asyncio
async def test_los_titulos_dicen_corte_del_dia(cliente, semilla):
    await _entrar(cliente)
    assert "Corte del día" in solo_texto(await cliente.get("/panel/liquidaciones"))
