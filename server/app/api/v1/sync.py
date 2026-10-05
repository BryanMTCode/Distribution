"""Sincronización: push de la cola del dispositivo y pull de deltas."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.deps import ActorDep, ActorQueEntregaDep, SesionDep
from app.api.esquemas import EntradaBase
from app.domain.sync.sobres import (
    MAX_SOBRES_POR_LOTE,
    LoteInvalido,
    Operacion,
    Sobre,
)
from app.infra.sync.ingesta import procesar_lote
from app.infra.sync.manejadores import Contexto

router = APIRouter(prefix="/sync", tags=["sincronización"])


# ---------------------------------------------------------------------------
# PUSH
# ---------------------------------------------------------------------------

class OperacionEntrada(EntradaBase):
    tipo: str = Field(description="'cliente.crear', 'venta.crear', …")
    entidad_id: uuid.UUID = Field(description="UUID del documento, generado en el dispositivo")
    # Sin tipar a propósito: el hash se calculó sobre ESTE diccionario tal como
    # salió del teléfono. Normalizarlo aquí —convertir a Decimal y de vuelta,
    # reordenar claves— rompería la comparación y mandaría a cuarentena todo
    # sobre legítimo.
    datos: dict[str, Any] = Field(default_factory=dict)


class SobreEntrada(EntradaBase):
    operacion_id: uuid.UUID = Field(description="Llave de idempotencia del sobre")
    secuencia: int = Field(ge=0, description="Orden FIFO dentro de la cola del dispositivo")
    hash_payload: str = Field(min_length=64, max_length=64)
    operaciones: list[OperacionEntrada] = Field(min_length=1)
    visita_id: uuid.UUID | None = None


class LoteEntrada(EntradaBase):
    lote_id: uuid.UUID
    sobres: list[SobreEntrada] = Field(min_length=1, max_length=MAX_SOBRES_POR_LOTE)
    app_version: str | None = None

    # El reloj DEL TELÉFONO en el instante de enviar este lote.
    #
    # Es lo único con lo que se puede medir si ese reloj miente: se compara contra
    # el instante en que el servidor recibe, y entre las dos lecturas solo hay
    # red. Comparar contra `fecha_dispositivo` —como se hacía— mide cuánto tardó
    # la venta en sincronizarse, que en una operación offline son horas por
    # diseño: marcaba casi todas las ventas del día como «reloj desfasado».
    #
    # Opcional porque una app vieja no lo manda, y entonces no se marca nada: no
    # saber no es lo mismo que estar bien, pero inventar un desfase es peor.
    enviado_en: datetime | None = None

    # Cuántos sobres le quedan al dispositivo DESPUÉS de este lote.
    #
    # Es el único dato del sistema que solo el teléfono puede dar, y el cierre del
    # día depende de él: hasta ahora `liquidaciones.sync_completa` se escribía
    # porque alguien marcaba una casilla, y al auditar un cierre con sobrante ese
    # `true` parecía un hecho cuando era una afirmación.
    #
    # Opcional a propósito: un equipo con app vieja sigue sincronizando igual, y la
    # ausencia del campo se guarda como NULL —"nunca lo reportó"—, que no es lo
    # mismo que cero.
    cola_pendiente: int | None = Field(
        default=None,
        ge=0,
        # Entero de JSON, sin comillas: `contracts/README.md` §1.4. Sin `strict`,
        # Pydantic aceptaría "3" y también `true` —`bool` es subclase de `int` en
        # Python— y el contrato se volvería una sugerencia en el único campo del
        # que depende el cierre del día.
        strict=True,
        description=(
            "Sobres que quedan en la cola del dispositivo después de este lote. "
            "Omitirlo se registra como 'no reportado'."
        ),
    )


class ResultadoSobreSalida(BaseModel):
    operacion_id: uuid.UUID
    estado: str
    entidades: list[uuid.UUID] = Field(default_factory=list)
    error_codigo: str | None = None
    error_mensaje: str | None = None


class LoteSalida(BaseModel):
    lote_id: uuid.UUID
    aceptadas: int
    duplicadas: int
    rechazadas: int
    resultados: list[ResultadoSobreSalida]


@router.post("/push", response_model=LoteSalida)
async def push(
    entrada: LoteEntrada, actor: ActorQueEntregaDep, sesion: SesionDep
) -> LoteSalida:
    """Recibe la cola del dispositivo.

    Reenviar el mismo lote es inofensivo por diseño: cada sobre lleva su llave
    de idempotencia y el servidor responde lo mismo que la primera vez sin
    volver a aplicar nada.

    Responde 200 aunque haya sobres rechazados: el lote se procesó. El detalle
    por sobre va en `resultados`, y lo rechazado quedó en cuarentena del lado
    del servidor. Devolver un error de transporte por un sobre malo haría que
    el dispositivo reintentara el lote completo para siempre.

    ────────────────────────────────────────────────────────────────────────
    ES EL ÚNICO ENDPOINT QUE ACEPTA UN EQUIPO SUSPENDIDO
    ────────────────────────────────────────────────────────────────────────
    `ActorQueEntregaDep` en vez de `ActorDep`, y la excepción está declarada
    aquí —no en el guardia— para que un endpoint nuevo no la herede sin
    quererlo.

    La razón es la regla del borrado remoto: **nunca se borra lo que no se ha
    entregado** (migración 0023). Un equipo al que se le ordenó el borrado pasa
    a `suspendido` precisamente para que pueda subir las ventas que lleva
    dentro antes de borrarlas. Si este endpoint lo rechazara, cada borrado
    remoto costaría un día de operación.

    Lo que un equipo suspendido NO puede hacer es `pull`: entrega y no recibe
    nada nuevo. Ese endpoint sigue con el guardia estricto.
    """
    actor.exigir("ventas.crear")
    if actor.dispositivo_id is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "sincronizar exige un token emitido para un dispositivo registrado",
        )

    sobres = [
        Sobre(
            operacion_id=s.operacion_id,
            secuencia=s.secuencia,
            hash_payload=s.hash_payload,
            visita_id=s.visita_id,
            operaciones=[Operacion(o.tipo, o.entidad_id, o.datos) for o in s.operaciones],
        )
        for s in entrada.sobres
    ]

    contexto = Contexto(
        # Del token, nunca del cuerpo: si el dispositivo pudiera declarar a
        # nombre de quién escribe, un equipo comprometido escribiría en la ruta
        # de cualquier otro vendedor.
        dispositivo_id=actor.dispositivo_id,
        usuario_id=actor.usuario_id,
        rutas=tuple(actor.rutas),
        almacen_id=actor.almacen_id,

        enviado_en=entrada.enviado_en,
    )

    try:
        resultado = await procesar_lote(
            sesion,
            contexto,
            entrada.lote_id,
            sobres,
            app_version=entrada.app_version,
            cola_pendiente=entrada.cola_pendiente,
        )
    except LoteInvalido as e:
        # El contenedor está mal formado: no se procesa nada, porque no se
        # puede confiar en nada de lo que venga dentro.
        #
        # Y se REGISTRA, porque este camino no deja rastro en ningún otro lado:
        # `sync_cuarentena` solo recibe sobres que llegaron a procesarse, así que
        # un lote inválido desaparece del servidor y el único síntoma es un «1 con
        # error» en el teléfono del vendedor.
        logging.getLogger("dsd.sync").warning(
            "lote %s rechazado del dispositivo %s: %s",
            entrada.lote_id,
            contexto.dispositivo_id,
            e,
        )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e

    return LoteSalida(
        lote_id=resultado.lote_id,
        aceptadas=resultado.aceptadas,
        duplicadas=resultado.duplicadas,
        rechazadas=resultado.rechazadas,
        resultados=[
            ResultadoSobreSalida(
                operacion_id=r.operacion_id,
                estado=r.estado.value,
                entidades=r.entidades,
                error_codigo=r.error_codigo,
                error_mensaje=r.error_mensaje,
            )
            for r in resultado.resultados
        ],
    )


# ---------------------------------------------------------------------------
# PULL
# ---------------------------------------------------------------------------

class Cambio(BaseModel):
    cursor: int
    entidad: str
    entidad_id: uuid.UUID
    operacion: str
    payload: dict[str, Any] | None


class DeltaSalida(BaseModel):
    cursor: int = Field(description="Cursor a mandar en la siguiente llamada")
    hay_mas: bool = Field(description="True si faltan cambios por traer")
    cambios: list[Cambio]
    resincronizar: bool = Field(
        default=False,
        description=(
            "True cuando el cursor del dispositivo quedó por debajo de lo que el "
            "change_log todavía conserva. El dispositivo tiene que volver a "
            "empezar desde el cursor 0: lo que falta ya no se puede entregar."
        ),
    )


@router.get("/pull", response_model=DeltaSalida)
async def pull(
    actor: ActorDep,
    sesion: SesionDep,
    cursor: int = Query(default=0, ge=0),
    limite: int = Query(default=500, ge=1, le=2000),
) -> DeltaSalida:
    """Entrega los cambios posteriores al cursor.

    **El cursor es un BIGSERIAL, nunca un timestamp.** Con relojes
    desincronizados y transacciones concurrentes, un cursor por fecha pierde
    registros en silencio: una transacción que empezó antes puede hacer COMMIT
    después de que el dispositivo ya avanzó su marca de agua, y ese registro no
    se entrega jamás.

    El BIGSERIAL tiene el mismo problema si se lee sin cuidado —la secuencia
    avanza al INSERT, no al COMMIT—, así que solo se entregan filas de
    transacciones **ya confirmadas para todos**:

        xid < pg_snapshot_xmin(pg_current_snapshot())

    Sin ese filtro, un dispositivo podría llevarse el cursor 120 mientras el
    119 sigue en vuelo, y al confirmarse quedaría por debajo de su marca de
    agua para siempre.
    """
    if actor.dispositivo_id is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "sincronizar exige un token emitido para un dispositivo registrado",
        )

    # ------------------------------------------------------------------
    # ¿Le alcanza el cursor al dispositivo?
    # ------------------------------------------------------------------
    # Si el `change_log` ya se podó por debajo del cursor que trae, los deltas de
    # en medio no existen y NO hay forma de entregárselos. Seguir adelante sería
    # lo peor de los dos mundos: recibiría los siguientes y nunca sabría que le
    # faltan los de antes — un catálogo incompleto, en silencio, para siempre.
    #
    # Así que se le dice que vuelva a empezar. Es caro —una resincronización
    # completa— y es lo único correcto; y mientras nadie pode, el piso vale 0 y
    # esto no se dispara nunca. Ver la migración 0034.
    piso = (
        await sesion.execute(text("SELECT piso_cursor FROM sync_retencion"))
    ).scalar_one_or_none() or 0
    if cursor and cursor < piso:
        return DeltaSalida(cursor=0, hay_mas=True, cambios=[], resincronizar=True)

    rutas = list(actor.rutas)
    filas = (
        await sesion.execute(
            text(
                """
                SELECT cursor, entidad, entidad_id, operacion, payload
                  FROM change_log
                 WHERE cursor > :cursor
                   AND xid < pg_snapshot_xmin(pg_current_snapshot())
                   AND (ruta_id IS NULL OR ruta_id = ANY(:rutas))
                   AND (vendedor_id IS NULL OR vendedor_id = :usuario)
                 ORDER BY cursor
                 LIMIT :limite
                """
            ),
            {"cursor": cursor, "rutas": rutas, "usuario": actor.usuario_id, "limite": limite + 1},
        )
    ).mappings().all()

    hay_mas = len(filas) > limite
    filas = filas[:limite]
    nuevo_cursor = filas[-1]["cursor"] if filas else cursor

    if filas:
        # Solo avanza: un pull viejo que llegue tarde no debe retroceder la
        # marca de agua del dispositivo.
        await sesion.execute(
            text(
                "UPDATE dispositivos "
                "   SET ultimo_cursor_pull = GREATEST(ultimo_cursor_pull, :c), "
                "       ultima_sync_pull_en = now() "
                " WHERE id = :dev"
            ),
            {"c": nuevo_cursor, "dev": actor.dispositivo_id},
        )
        await sesion.commit()

    return DeltaSalida(
        cursor=nuevo_cursor,
        hay_mas=hay_mas,
        cambios=[Cambio(**dict(f)) for f in filas],
    )


# ---------------------------------------------------------------------------
# Estado
# ---------------------------------------------------------------------------

class EstadoSync(BaseModel):
    dispositivo_id: uuid.UUID
    ultimo_cursor_pull: int
    cursor_disponible: int
    cambios_pendientes: int
    ultima_sync_push_en: str | None
    ultima_sync_pull_en: str | None
    operaciones_en_cuarentena: int


@router.get("/estado", response_model=EstadoSync)
async def estado(actor: ActorDep, sesion: SesionDep) -> EstadoSync:
    """Lo que alimenta la pantalla de *inspector de sync* de la app.

    Se usa durante años: es la diferencia entre depurar con datos y depurar
    con adivinanzas.
    """
    if actor.dispositivo_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "el token no trae dispositivo")

    fila = (
        await sesion.execute(
            text(
                """
                SELECT d.id,
                       d.ultimo_cursor_pull,
                       d.ultima_sync_push_en,
                       d.ultima_sync_pull_en,
                       (SELECT COALESCE(MAX(cursor), 0) FROM change_log)      AS disponible,
                       (SELECT COUNT(*) FROM sync_cuarentena c
                         WHERE c.dispositivo_id = d.id AND c.estado = 'pendiente') AS cuarentena
                  FROM dispositivos d
                 WHERE d.id = :dev
                """
            ),
            {"dev": actor.dispositivo_id},
        )
    ).mappings().one()

    return EstadoSync(
        dispositivo_id=fila["id"],
        ultimo_cursor_pull=fila["ultimo_cursor_pull"],
        cursor_disponible=fila["disponible"],
        cambios_pendientes=max(0, fila["disponible"] - fila["ultimo_cursor_pull"]),
        ultima_sync_push_en=fila["ultima_sync_push_en"].isoformat()
        if fila["ultima_sync_push_en"] else None,
        ultima_sync_pull_en=fila["ultima_sync_pull_en"].isoformat()
        if fila["ultima_sync_pull_en"] else None,
        operaciones_en_cuarentena=fila["cuarentena"],
    )
