"""Cola de trabajos sobre PostgreSQL.

Lo que se prueba aquí es lo que justifica no traer Redis: `FOR UPDATE SKIP
LOCKED` da exclusión mutua real entre workers, y encolar puede ir en la misma
transacción que la escritura de negocio.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.workers.cola import (
    encolar,
    marcar_fallido,
    marcar_hecho,
    recuperar_huerfanos,
    tomar_lote,
)

pytestmark = pytest.mark.asyncio


async def test_encolar_y_tomar(sesion):
    await encolar(sesion, "refrescar_vistas", {"dia": "2026-09-15"})
    await sesion.commit()

    lote = await tomar_lote(sesion, limite=10)
    await sesion.commit()
    assert len(lote) == 1
    assert lote[0]["tipo"] == "refrescar_vistas"
    assert lote[0]["payload"] == {"dia": "2026-09-15"}
    assert lote[0]["intentos"] == 1


async def test_clave_unica_evita_duplicados(sesion):
    """Encolar dos veces 'refrescar_vistas:2026-09-15' es un no-op, no un error:
    el evento que lo dispara puede llegar tres veces."""
    primero = await encolar(sesion, "refrescar", clave_unica="refrescar:2026-09-15")
    segundo = await encolar(sesion, "refrescar", clave_unica="refrescar:2026-09-15")
    await sesion.commit()
    assert primero is not None
    assert segundo is None

    pendientes = (await sesion.execute(text("SELECT count(*) FROM jobs"))).scalar_one()
    assert pendientes == 1


async def test_la_misma_clave_se_reencola_cuando_el_job_termino(sesion):
    """El índice único solo cubre jobs activos: mañana se vuelve a encolar."""
    job_id = await encolar(sesion, "refrescar", clave_unica="refrescar:diario")
    await sesion.commit()
    await marcar_hecho(sesion, job_id)
    await sesion.commit()

    otra_vez = await encolar(sesion, "refrescar", clave_unica="refrescar:diario")
    await sesion.commit()
    assert otra_vez is not None


async def test_encolar_va_en_la_misma_transaccion_que_el_negocio(sesion):
    """La garantía que ningún broker externo puede dar: si la escritura de
    negocio se revierte, el job encolado se revierte con ella."""
    await encolar(sesion, "procesar_venta", {"venta_id": "x"})
    await sesion.rollback()

    total = (await sesion.execute(text("SELECT count(*) FROM jobs"))).scalar_one()
    assert total == 0


async def test_dos_workers_nunca_toman_el_mismo_job(motor):
    """El corazón de SKIP LOCKED: el segundo worker salta las filas bloqueadas
    en vez de esperarlas, y nunca procesa lo mismo dos veces."""
    fabrica = async_sessionmaker(motor, expire_on_commit=False)

    async with fabrica() as preparacion:
        for i in range(20):
            await encolar(preparacion, "trabajo", {"n": i})
        await preparacion.commit()

    async def worker() -> list[int]:
        async with fabrica() as s:
            lote = await tomar_lote(s, limite=10)
            await asyncio.sleep(0.05)   # sostiene el bloqueo mientras el otro toma
            await s.commit()
            return [j["id"] for j in lote]

    a, b = await asyncio.gather(worker(), worker())
    assert len(a) == 10
    assert len(b) == 10
    assert not (set(a) & set(b)), "dos workers tomaron el mismo job"
    assert len(set(a) | set(b)) == 20


async def test_un_job_fallido_reintenta_con_backoff(sesion):
    job_id = await encolar(sesion, "frágil")
    await sesion.commit()
    await tomar_lote(sesion, limite=1)
    await sesion.commit()

    await marcar_fallido(sesion, job_id, "la impresora no respondió")
    await sesion.commit()

    fila = (
        await sesion.execute(
            text(
                "SELECT estado, intentos, ultimo_error, ejecutar_en > now() AS en_el_futuro "
                "FROM jobs WHERE id = :id"
            ),
            {"id": job_id},
        )
    ).mappings().one()
    assert fila["estado"] == "pendiente"
    assert fila["intentos"] == 1
    assert fila["en_el_futuro"] is True


async def test_al_agotar_intentos_queda_fallido_y_visible(sesion):
    """Un job fallido NO se borra: queda en el panel de operación. Un trabajo
    que desaparece en silencio es un descuadre esperando a suceder."""
    job_id = await encolar(sesion, "condenado")
    await sesion.execute(
        text("UPDATE jobs SET max_intentos = 1 WHERE id = :id"), {"id": job_id}
    )
    await sesion.commit()

    await tomar_lote(sesion, limite=1)
    await sesion.commit()
    await marcar_fallido(sesion, job_id, "error definitivo")
    await sesion.commit()

    fila = (
        await sesion.execute(
            text("SELECT estado, terminado_en FROM jobs WHERE id = :id"), {"id": job_id}
        )
    ).mappings().one()
    assert fila["estado"] == "fallido"
    assert fila["terminado_en"] is not None


async def test_un_job_programado_al_futuro_no_se_toma(sesion):
    await encolar(sesion, "mañana", retraso=timedelta(hours=1))
    await sesion.commit()
    assert await tomar_lote(sesion) == []


async def test_se_recupera_el_job_de_un_worker_muerto(sesion):
    """Apagón a media ejecución: se recupera por tiempo, sin heartbeat. Una
    pieza móvil menos."""
    job_id = await encolar(sesion, "interrumpido")
    await sesion.commit()
    await tomar_lote(sesion, limite=1)
    await sesion.commit()

    await sesion.execute(
        text("UPDATE jobs SET tomado_en = now() - interval '1 hour' WHERE id = :id"),
        {"id": job_id},
    )
    await sesion.commit()

    assert await recuperar_huerfanos(sesion) == 1
    await sesion.commit()
    assert len(await tomar_lote(sesion, limite=1)) == 1


async def test_respeta_la_prioridad(sesion):
    await encolar(sesion, "normal", prioridad=100)
    await encolar(sesion, "urgente", prioridad=1)
    await sesion.commit()

    lote = await tomar_lote(sesion, limite=1)
    assert lote[0]["tipo"] == "urgente"
