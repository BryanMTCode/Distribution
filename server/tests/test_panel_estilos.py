"""Las plantillas del panel no usan una combinación de clases sin estilo.

────────────────────────────────────────────────────────────────────────────
EL DEFECTO QUE ESTA PRUEBA EXISTE PARA NO REPETIR
────────────────────────────────────────────────────────────────────────────
Siete pantallas usaban `class="aviso-caja aviso"` —el aviso ámbar— y la regla
`.aviso-caja.aviso` no existía. Las dos clases sueltas sí tenían estilo
(`.aviso-caja` y `.tarjeta.aviso`), así que nada se veía roto en el HTML: el aviso
salía como un párrafo más, sin fondo, y se perdía justo donde tenía que llamar la
atención. «Un borrador no mueve inventario» y «la mercancía está en tránsito» se
leían como texto de relleno.

Ninguna prueba lo vio porque todas buscan PALABRAS, y las palabras estaban ahí. Lo
vio la primera captura de pantalla de «Desempeño».

Por eso la revisión es sobre COMBINACIONES: si un bloque tiene variantes en la hoja
de estilos (`.aviso-caja.error`, `.aviso-caja.bien`…), cualquier variante que una
plantilla le ponga tiene que estar definida.
"""

from __future__ import annotations

import re
from pathlib import Path

PLANTILLAS = Path(__file__).resolve().parents[1] / "app" / "api" / "admin" / "templates"


def _hoja_de_estilos() -> str:
    base = (PLANTILLAS / "base.html").read_text(encoding="utf-8")
    return base[base.index("<style>") : base.index("</style>")]


def _combinaciones_definidas(css: str) -> set[tuple[str, str]]:
    """Los pares `.bloque.variante` que la hoja de estilos sí define."""
    return set(re.findall(r"\.([a-zA-Z][\w-]*)\.([a-zA-Z][\w-]*)", css))


def combinaciones_sin_estilo(css: str, plantillas: dict[str, str]) -> list[str]:
    """Cada `bloque variante` usado en una plantilla y que no tiene regla.

    Solo se revisan los bloques que TIENEN variantes en la hoja: `class="mono
    numero"` combina dos clases independientes y no es una variante de nada.
    """
    definidas = _combinaciones_definidas(css)
    con_variantes = {bloque for bloque, _ in definidas}
    faltan: list[str] = []
    for nombre, texto in plantillas.items():
        for valor in re.findall(r'class="([^"]*)"', texto):
            # Lo que decide Jinja no se puede revisar aquí; se queda lo estático.
            clases = re.sub(r"{%.*?%}|{{.*?}}", " ", valor).split()
            if not clases or clases[0] not in con_variantes:
                continue
            bloque = clases[0]
            for variante in clases[1:]:
                if (bloque, variante) not in definidas:
                    faltan.append(f"{nombre}: .{bloque}.{variante}")
    return sorted(set(faltan))


def test_ninguna_plantilla_usa_una_variante_sin_estilo():
    plantillas = {
        p.name: p.read_text(encoding="utf-8") for p in PLANTILLAS.glob("*.html")
    }
    assert combinaciones_sin_estilo(_hoja_de_estilos(), plantillas) == []


def test_la_revision_si_atrapa_el_defecto_que_la_motivo():
    """La prueba de la prueba: sin `.aviso-caja.aviso`, se pone roja.

    Una revisión que nunca falla puede estar revisando nada. Ésta se corre contra
    la hoja tal como estaba antes del arreglo.
    """
    css_viejo = _hoja_de_estilos().replace(".aviso-caja.aviso", ".otra-cosa")
    plantilla = '<div class="aviso-caja aviso">Un borrador no mueve inventario</div>'
    assert combinaciones_sin_estilo(css_viejo, {"entradas.html": plantilla}) == [
        "entradas.html: .aviso-caja.aviso"
    ]
