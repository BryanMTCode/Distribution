"""La advertencia que acompaña a cada cifra del laboratorio.

Aritmética pura, sin base de datos — por eso vive aparte de `test_analitica.py`,
que es asíncrono de punta a punta: una prueba síncrona bajo su `pytestmark`
produce un aviso de pytest-asyncio, y el aviso tiene razón.

Lo que se defiende aquí es una idea, no un cálculo: **un número sin su
antigüedad se trata como la verdad.** Y en este sistema hay dos formas de que un
número esté viejo, no una:

1. El refresh es de hace horas.
2. El refresh es de hace un minuto, pero cuando corrió había teléfonos sin
   sincronizar.

La segunda es la que se olvida, y es la peor: «actualizado hace 1 min» suena
perfecto y la cifra del día puede estar incompleta.
"""

from __future__ import annotations

from app.domain.analitica import Frescura


def test_los_datos_frescos_y_completos_no_llevan_advertencia():
    f = Frescura(minutos=5, equipos_sin_sincronizar=0, ops_en_cuarentena=0)
    assert f.confiable is True
    assert f.advertencia is None


def test_un_equipo_sin_sincronizar_vuelve_la_cifra_un_piso():
    """Aunque el refresh sea de hace un minuto."""
    f = Frescura(minutos=1, equipos_sin_sincronizar=2, ops_en_cuarentena=0)
    assert f.confiable is False
    assert "piso y no un total" in f.advertencia


def test_datos_viejos_llevan_advertencia_aunque_el_mundo_este_completo():
    f = Frescura(minutos=400, equipos_sin_sincronizar=0, ops_en_cuarentena=0)
    assert f.confiable is False
    assert "6 h" in f.advertencia


def test_la_cuarentena_se_advierte_porque_no_esta_contada_en_ningun_numero():
    """Lo que está en cuarentena no está en NINGÚN número del laboratorio.

    No es que esté mal contado: es que no entró. Quien lea un total de ventas con
    operaciones en cuarentena está leyendo menos de lo que pasó.
    """
    f = Frescura(minutos=5, equipos_sin_sincronizar=0, ops_en_cuarentena=3)
    assert f.confiable is False
    assert "cuarentena" in f.advertencia


def test_las_advertencias_se_acumulan_en_una_sola_frase():
    """Tres problemas a la vez no son tres avisos: son uno que los nombra.

    Tres recuadros de advertencia apilados se dejan de leer, y entonces el
    mecanismo que existe para que alguien no decida con datos viejos deja de
    funcionar.
    """
    f = Frescura(minutos=400, equipos_sin_sincronizar=1, ops_en_cuarentena=2)
    assert f.confiable is False
    aviso = f.advertencia
    assert aviso.count(";") == 2
    assert aviso.endswith(".")
