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
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import lock_de_dispositivo
from app.domain.sync.resultados import Estado, ResultadoLote, ResultadoSobre
from app.domain.sync.sobres import CodigoError, Sobre, validar_lote, verificar_hash
from app.infra.sync.manejadores import Contexto, ErrorDeManejador, obtener_manejador
from app.workers.cola import encolar

log = logging.getLogger("dsd.sync")

# Cuánto espera el recálculo del tablero después de un lote aceptado.
#
# No es cero por una razón concreta: al llegar a la bodega los ocho equipos
# sincronizan casi al mismo tiempo, y con cero el primero arrancaría el job
# mientras los otros siete siguen subiendo — se recalcularía con el día a
# medias y habría que volver a hacerlo. Un minuto los agrupa en una corrida.
#
# El costo es que el tablero puede ir hasta un minuto atrás de la última venta
# recibida, y el tablero lo DICE: muestra la antigüedad de cada cifra.
RETRASO_TABLERO_SEG = 60

__all__ = ["procesar_lote", "reprocesar_cuarentena"]


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
    cola_pendiente: int | None = None,
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
        # La profundidad de cola se guarda SOLO si vino, con un COALESCE sobre el
        # parámetro y no sobre la columna: un equipo con app vieja no debe borrar lo
        # último que sí reportó, porque el cierre del día lee ese número.
        await sesion.execute(
            text(
                """
                UPDATE dispositivos
                   SET ultima_sync_push_en = now(),
                       cola_pendiente = COALESCE(CAST(:cola AS integer), cola_pendiente),
                       cola_reportada_en = CASE
                           WHEN :cola IS NULL THEN cola_reportada_en ELSE now()
                       END
                 WHERE id = :dev
                """
            ),
            {"dev": ctx.dispositivo_id, "cola": cola_pendiente},
        )

        # El tablero de Gerencia se recalcula cuando ENTRA operación, no cada
        # vez que alguien abre la pantalla (ver migración 0021). Va aquí dentro,
        # en la misma transacción que el lote: si el lote se deshace, no queda
        # encolado el recálculo de algo que no pasó.
        #
        # `clave_unica` fija y sin fecha, a propósito: el job averigua solo qué
        # días quedaron rancios, así que ocho camiones subiendo a la vez
        # encolan UNO. El retraso agrupa la ráfaga del final del día — ocho
        # equipos conectándose al llegar a la bodega — en una sola corrida.
        if resultado.aceptadas:
            await encolar(
                sesion,
                "recalcular_tablero",
                {"motivo": "sync", "dispositivo_id": str(ctx.dispositivo_id)},
                clave_unica="recalcular_tablero",
                retraso=timedelta(seconds=RETRASO_TABLERO_SEG),
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


# ---------------------------------------------------------------------------
# Reprocesar lo que quedó en cuarentena
# ---------------------------------------------------------------------------
# El servidor recuerda que rechazó un sobre, y a un reenvío le contesta lo mismo
# sin volver a aplicarlo (§1 de este archivo). Es lo correcto para un rechazo por
# los datos, y un callejón sin salida para uno por un fallo NUESTRO: el teléfono ya
# instalado con la versión nueva manda algo que el servidor todavía viejo no
# conocía, el servidor se actualiza... y el sobre se queda rechazado para siempre.
# El vendedor ve «1 con error», toca para reintentar, y nada cambia.
#
# La salida la decide una persona desde el panel: volver a aplicar el payload que
# se guardó, íntegro, con las MISMAS reglas que el push. Si pasa, la operación
# queda aceptada en `sync_operaciones`, y el siguiente reintento del teléfono
# recibe «duplicada» —ya está del otro lado—, la saca de su cola y se le quita lo
# rojo. Si no pasa, se queda pendiente con el motivo nuevo.


async def reprocesar_cuarentena(
    sesion: AsyncSession, id_fila: int, quien: uuid.UUID
) -> tuple[bool, str]:
    """Vuelve a aplicar una operación en cuarentena. Devuelve (pasó, mensaje).

    No hace `commit`: lo hace quien llama, igual que con el push.
    """
    fila = (
        await sesion.execute(
            text(
                "SELECT * FROM sync_cuarentena WHERE id = :id AND estado = 'pendiente' "
                "FOR UPDATE"
            ),
            {"id": id_fila},
        )
    ).mappings().first()
    if fila is None:
        return False, "Esa operación ya no está pendiente."
    if fila["error_codigo"] == CodigoError.HASH_NO_COINCIDE.value:
        # El contenido no es el que el equipo firmó: no hay forma de saber cuál es
        # el legítimo, y aplicarlo sería apostar.
        return False, (
            "Esta no se reprocesa: el contenido no coincide con su firma. Atiéndela "
            "por fuera y márcala como atendida."
        )

    usuario = (
        await sesion.execute(
            text("SELECT almacen_id FROM usuarios WHERE id = :u"), {"u": fila["usuario_id"]}
        )
    ).mappings().first()
    rutas = tuple(
        (
            await sesion.execute(
                text("SELECT ruta_id FROM usuarios_rutas WHERE usuario_id = :u"),
                {"u": fila["usuario_id"]},
            )
        ).scalars()
    )
    ctx = Contexto(
        dispositivo_id=fila["dispositivo_id"],
        usuario_id=fila["usuario_id"],
        rutas=rutas,
        almacen_id=usuario["almacen_id"] if usuario else None,
    )

    payload = fila["payload"] or {}
    operaciones = payload.get("operaciones") or []
    if not operaciones:
        return False, "La operación guardada no trae nada que aplicar."

    # El mismo candado que el push: si el teléfono está subiendo justo ahora, uno
    # espera al otro en vez de aplicar lo mismo dos veces.
    async with lock_de_dispositivo(sesion, ctx.dispositivo_id):
        try:
            async with sesion.begin_nested():
                for o in operaciones:
                    manejador = obtener_manejador(o["tipo"])
                    await manejador(sesion, ctx, uuid.UUID(o["entidad_id"]), o.get("datos") or {})
        except ErrorDeManejador as e:
            codigo, mensaje = e.codigo.value, e.mensaje
        except (IntegrityError, DBAPIError) as e:
            codigo, mensaje = CodigoError.CONFLICTO_DE_DATOS.value, str(getattr(e, "orig", e))
        else:
            codigo = mensaje = None

    if codigo is not None:
        await sesion.execute(
            text(
                "UPDATE sync_cuarentena SET error_codigo = :c, error_mensaje = :m "
                " WHERE id = :id"
            ),
            {"c": codigo, "m": (mensaje or "")[:2000], "id": id_fila},
        )
        return False, f"Sigue sin poder aplicarse: {mensaje}"

    entidad = payload.get("visita_id") or operaciones[0]["entidad_id"]
    actualizadas = (
        await sesion.execute(
            text(
                "UPDATE sync_operaciones "
                "   SET resultado = 'aceptada', error_codigo = NULL, error_mensaje = NULL, "
                "       entidad_id = :ent "
                " WHERE operacion_id = :op"
            ),
            {"op": fila["operacion_id"], "ent": entidad},
        )
    ).rowcount
    if not actualizadas:
        await sesion.execute(
            text(
                """
                INSERT INTO sync_operaciones (operacion_id, dispositivo_id, tipo, entidad_id,
                                              hash_payload, resultado)
                VALUES (:op, :dev, :tipo, :ent, :hash, 'aceptada')
                """
            ),
            {
                "op": fila["operacion_id"],
                "dev": fila["dispositivo_id"],
                "tipo": fila["tipo"],
                "ent": entidad,
                "hash": fila["hash_payload"],
            },
        )
    await sesion.execute(
        text(
            "UPDATE sync_cuarentena "
            "   SET estado = 'reprocesada', resuelto_por = :quien, resuelto_en = now(), "
            "       nota_resolucion = 'Reprocesada desde el panel' "
            " WHERE id = :id"
        ),
        {"quien": quien, "id": id_fila},
    )
    await encolar(
        sesion,
        "recalcular_tablero",
        {"motivo": "reproceso", "dispositivo_id": str(ctx.dispositivo_id)},
        clave_unica="recalcular_tablero",
        retraso=timedelta(seconds=RETRASO_TABLERO_SEG),
    )
    return True, (
        "Aplicada. En el teléfono, toca la barra roja para reintentar: el servidor le "
        "contesta que ya la tiene y se le quita el error."
    )

