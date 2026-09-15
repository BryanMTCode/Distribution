"""UUIDv7: toda su ventaja sobre v4 es que ordenan por tiempo."""

from __future__ import annotations

import time
import uuid

from app.domain.identificadores import es_uuid7, instante_de_uuid7, nuevo_id


def test_version_correcta():
    assert es_uuid7(nuevo_id())


def test_ordenan_por_tiempo():
    """Propiedad que hace utilizable un índice sobre la PK sin fragmentar."""
    primeros = [str(nuevo_id()) for _ in range(200)]
    time.sleep(0.005)
    ultimos = [str(nuevo_id()) for _ in range(200)]
    assert max(primeros) < min(ultimos)


def test_no_colisionan():
    assert len({nuevo_id() for _ in range(10_000)}) == 10_000


def test_el_instante_embebido_nunca_queda_en_el_pasado():
    """Forense: el instante embebido es una COTA SUPERIOR del momento de
    creación, no un reloj.

    La implementación adelanta su reloj interno ~1 ms por identificador para
    garantizar monotonicidad dentro del mismo milisegundo, así que en ráfaga
    el timestamp queda en el futuro. Lo que sí se puede afirmar —y es lo que
    importa— es que nunca queda en el pasado.
    """
    antes_ms = int(time.time() * 1000)
    ms = instante_de_uuid7(nuevo_id())
    assert ms >= antes_ms


def test_la_generacion_en_rafaga_conserva_el_orden():
    """La monotonicidad es la razón de usar v7: un índice sobre la PK no se
    fragmenta. Se sostiene incluso generando miles en el mismo milisegundo."""
    ids = [str(nuevo_id()) for _ in range(5_000)]
    assert ids == sorted(ids)


def test_el_desfase_en_rafaga_es_acotado():
    """Deja constancia del costo de esa monotonicidad: ~1 ms por identificador.

    Se mide el avance ENTRE el primero y el último de la ráfaga, no contra el
    reloj de pared: el contador interno es global al proceso, así que medir
    contra `time.time()` haría que esta prueba dependiera de cuántos
    identificadores generaron las pruebas anteriores.
    """
    n = 2_000
    primero = instante_de_uuid7(nuevo_id())
    ultimo = None
    for _ in range(n):
        ultimo = nuevo_id()
    avance = instante_de_uuid7(ultimo) - primero
    assert 0 <= avance <= 2 * n, f"avance inesperado de {avance} ms en {n} identificadores"


def test_un_uuid4_no_pasa_por_uuid7():
    try:
        instante_de_uuid7(uuid.uuid4())
    except ValueError:
        return
    raise AssertionError("debió rechazar un UUIDv4")
