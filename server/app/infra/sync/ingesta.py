"""Ingesta de lotes: el corazón del motor de sincronización.

    Entrega "al menos una vez" + receptor idempotente = efecto de
    exactamente una vez.

Una red mala entrega el mismo lote dos veces. Eso es normal, no es un bug. No
se intenta evitar el duplicado en el envío: se hace que recibirlo dos veces no
cambie nada.

Cuatro mecanismos, cada uno resolviendo un fallo real:

1. **Advisory lock por dispositivo.** Un teléfono con red intermitente reintenta
   mientras el envío original sigue procesándose. Sin el lock, los dos pasarían
   a la vez por el "¿ya procesado?" y ambos aplicarían.

2. **Registro de operaciones procesadas.** Un `operacion_id` repetido devuelve
   el resultado guardado sin reprocesar. Repetido **con hash distinto** es
   alarma roja: bug del cliente o manipulación, y va a cuarentena sin aplicarse.

3. **Un SAVEPOINT por sobre.** Cuando PostgreSQL lanza un `IntegrityError`, la
   transacción completa queda abortada: sin savepoint, el sobre número 7 de un
   lote de 200 tira los 193 siguientes aunque fueran válidos.

4. **Cuarentena.** Un sobre rechazado no bloquea la cola del dispositivo, y su
   payload íntegro queda del lado del servidor para revisión humana. Una cola
   atorada deja al vendedor sin poder vender; un payload perdido es dinero
   perdido.
"""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import lock_de_dispositivo
from app.domain.sync.resultados import Estado, ResultadoLote, ResultadoSobre
from app.domain.sync.sobres import CodigoError, Sobre, validar_lote, verificar_hash
from app.infra.sync.manejadores import Contexto, ErrorDeManejador, obtener_manejador

log = logging.getLogger("dsd.sync")

__all__ = ["procesar_lote"]


async def _registrar_lote(
    sesion: AsyncSession, lote_id: uuid.UUID, ctx: Contexto, total: int, app_version: str | None
) -> None:
    """Registra el lote. Idempotente: reenviarlo no crea un segundo renglón."""
    await sesion.execute(
        text(
            """
            INSERT INTO sync_lotes (id, dispositivo_id, usuario_id, total_operaciones,
                                    app_version)
            VALUES (:id, :dev, :usr, :total, :ver)
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id": lote_id,
            "dev": ctx.dispositivo_id,
            "usr": ctx.usuario_id,
            "total": total,
            "ver": app_version,
        },
    )


async def _previo(sesion: AsyncSession, operacion_id: uuid.UUID) -> dict | None:
    fila = (
        await sesion.execute(
            text(
                "SELECT hash_payload, resultado, error_codigo, error_mensaje, entidad_id "
                "FROM sync_operaciones WHERE operacion_id = :id"
            ),
            {"id": operacion_id},
        )
    ).mappings().first()
    return dict(fila) if fila else None


async def _anotar(
    sesion: AsyncSession,
    sobre: Sobre,
    ctx: Contexto,
    lote_id: uuid.UUID,
    resultado: Estado,
    *,
    error_codigo: str | None = None,
    error_mensaje: str | None = None,
) -> None:
    await sesion.execute(
        text(
            """
            INSERT INTO sync_operaciones (operacion_id, dispositivo_id, lote_id, tipo,
                                          entidad_id, hash_payload, resultado,
                                          error_codigo, error_mensaje)
            VALUES (:op, :dev, :lote, :tipo, :ent, :hash, :res, :cod, :msg)
            ON CONFLICT (operacion_id) DO NOTHING
            """
        ),
        {
            "op": sobre.operacion_id,
            "dev": ctx.dispositivo_id,
            "lote": lote_id,
            "tipo": "+".join(sobre.tipos)[:200],
            "ent": sobre.visita_id or sobre.operaciones[0].entidad_id,
            "hash": sobre.hash_payload,
            "res": resultado.value,
            "cod": error_codigo,
            "msg": (error_mensaje or "")[:2000] or None,
        },
    )


async def _cuarentena(
    sesion: AsyncSession,
    sobre: Sobre,
    ctx: Contexto,
    codigo: CodigoError,
    mensaje: str,
) -> None:
    """Guarda el payload íntegro para revisión humana en el panel."""
    await sesion.execute(
        text(
            """
            INSERT INTO sync_cuarentena (operacion_id, dispositivo_id, usuario_id, tipo,
                                         payload, hash_payload, error_codigo, error_mensaje)
            VALUES (:op, :dev, :usr, :tipo, CAST(:payload AS jsonb), :hash, :cod, :msg)
            """
        ),
        {
            "op": sobre.operacion_id,
            "dev": ctx.dispositivo_id,
            "usr": ctx.usuario_id,
            "tipo": "+".join(sobre.tipos)[:200],
            "payload": json.dumps(
                {
                    "visita_id": str(sobre.visita_id) if sobre.visita_id else None,
                    "secuencia": sobre.secuencia,
                    "operaciones": [
                        {"tipo": o.tipo, "entidad_id": str(o.entidad_id), "datos": o.datos}
                        for o in sobre.operaciones
                    ],
                }
            ),
            "hash": sobre.hash_payload,
            "cod": codigo.value,
            "msg": mensaje[:2000],
        },
    )


async def procesar_lote(
    sesion: AsyncSession,
    ctx: Contexto,
    lote_id: uuid.UUID,
    sobres: list[Sobre],
    *,
    app_version: str | None = None,
) -> ResultadoLote:
    """Aplica un lote completo. Hace commit al final."""
    ordenados = validar_lote(sobres)   # LoteInvalido sale hacia el endpoint
    resultado = ResultadoLote(lote_id=lote_id)

    # Serializa los lotes de este mismo dispositivo. Se libera al terminar la
    # transacción; los de otros equipos siguen en paralelo.
    async with lock_de_dispositivo(sesion, ctx.dispositivo_id):
        await _registrar_lote(sesion, lote_id, ctx, len(ordenados), app_version)

        for sobre in ordenados:
            resultado.resultados.append(await _procesar_sobre(sesion, ctx, lote_id, sobre))

        await sesion.execute(
            text(
                """
                UPDATE sync_lotes
                   SET aceptadas = :a, duplicadas = :d, rechazadas = :r, procesado_en = now()
                 WHERE id = :id
                """
            ),
            {
                "id": lote_id,
                "a": resultado.aceptadas,
                "d": resultado.duplicadas,
                "r": resultado.rechazadas,
            },
        )
        await sesion.execute(
            text("UPDATE dispositivos SET ultima_sync_push_en = now() WHERE id = :dev"),
            {"dev": ctx.dispositivo_id},
        )

    await sesion.commit()
    return resultado


async def _procesar_sobre(
    sesion: AsyncSession, ctx: Contexto, lote_id: uuid.UUID, sobre: Sobre
) -> ResultadoSobre:
    # ---- 1. ¿Ya lo habíamos procesado? ------------------------------------
    previo = await _previo(sesion, sobre.operacion_id)
    if previo is not None:
        # Dos formas de que un reenvío no sea el mismo sobre: que declare otro
        # hash, o que declare el mismo pero traiga otro contenido. La segunda
        # es inofensiva —en un reenvío no se aplica nada— pero es exactamente
        # la señal que se quiere ver si un equipo está manipulado o si el
        # cliente tiene un bug de serialización.
        if previo["hash_payload"] != sobre.hash_payload or not verificar_hash(sobre):
            # ALARMA: mismo id, contenido distinto. No se aplica ninguno de los
            # dos: no hay forma de saber cuál es el legítimo.
            log.warning(
                "sobre %s reenviado con hash distinto desde %s",
                sobre.operacion_id,
                ctx.dispositivo_id,
            )
            await _cuarentena(
                sesion,
                sobre,
                ctx,
                CodigoError.HASH_NO_COINCIDE,
                "el mismo operacion_id llegó antes con un contenido distinto",
            )
            return ResultadoSobre(
                sobre.operacion_id,
                Estado.RECHAZADA,
                error_codigo=CodigoError.HASH_NO_COINCIDE.value,
                error_mensaje="contenido distinto para un operacion_id ya usado",
            )
        # Reenvío legítimo: se responde lo mismo que la primera vez.
        estado = (
            Estado.RECHAZADA if previo["resultado"] == Estado.RECHAZADA.value
            else Estado.DUPLICADA
        )
        return ResultadoSobre(
            sobre.operacion_id,
            estado,
            entidades=[previo["entidad_id"]] if previo["entidad_id"] else [],
            error_codigo=previo["error_codigo"],
            error_mensaje=previo["error_mensaje"],
        )

    # ---- 2. ¿El contenido es el que el dispositivo dice? -------------------
    if not verificar_hash(sobre):
        await _cuarentena(
            sesion, sobre, ctx, CodigoError.HASH_NO_COINCIDE,
            "el hash no corresponde al contenido del sobre",
        )
        await _anotar(
            sesion, sobre, ctx, lote_id, Estado.RECHAZADA,
            error_codigo=CodigoError.HASH_NO_COINCIDE.value,
            error_mensaje="el hash no corresponde al contenido",
        )
        return ResultadoSobre(
            sobre.operacion_id,
            Estado.RECHAZADA,
            error_codigo=CodigoError.HASH_NO_COINCIDE.value,
            error_mensaje="el hash no corresponde al contenido del sobre",
        )

    # ---- 3. Aplicar, cada sobre en su propio SAVEPOINT ---------------------
    try:
        async with sesion.begin_nested():
            for operacion in sobre.operaciones:
                manejador = obtener_manejador(operacion.tipo)
                await manejador(sesion, ctx, operacion.entidad_id, operacion.datos)
            await _anotar(sesion, sobre, ctx, lote_id, Estado.ACEPTADA)
    except ErrorDeManejador as e:
        # El savepoint ya revirtió lo del sobre; la sesión sigue usable.
        await _cuarentena(sesion, sobre, ctx, e.codigo, e.mensaje)
        await _anotar(
            sesion, sobre, ctx, lote_id, Estado.RECHAZADA,
            error_codigo=e.codigo.value, error_mensaje=e.mensaje,
        )
        return ResultadoSobre(
            sobre.operacion_id, Estado.RECHAZADA,
            error_codigo=e.codigo.value, error_mensaje=e.mensaje,
        )
    except (IntegrityError, DBAPIError) as e:
        mensaje = str(getattr(e, "orig", e))
        log.warning("sobre %s violó una restricción: %s", sobre.operacion_id, mensaje)
        await _cuarentena(sesion, sobre, ctx, CodigoError.CONFLICTO_DE_DATOS, mensaje)
        await _anotar(
            sesion, sobre, ctx, lote_id, Estado.RECHAZADA,
            error_codigo=CodigoError.CONFLICTO_DE_DATOS.value, error_mensaje=mensaje,
        )
        return ResultadoSobre(
            sobre.operacion_id, Estado.RECHAZADA,
            error_codigo=CodigoError.CONFLICTO_DE_DATOS.value, error_mensaje=mensaje,
        )

    return ResultadoSobre(
        sobre.operacion_id,
        Estado.ACEPTADA,
        entidades=[o.entidad_id for o in sobre.operaciones],
    )
