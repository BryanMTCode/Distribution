"""Los teléfonos: rezago, suspensión y borrado remoto (Fase 9).

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTA PANTALLA NO EXISTÍA Y HACE FALTA
────────────────────────────────────────────────────────────────────────────
Registrar y revocar un equipo solo se podía hacer por la API, con `curl`. Eso
basta mientras hay un teléfono; con ocho, deja a la oficina sin la única
pantalla que contesta las preguntas que se hacen de verdad:

    «¿Qué equipos no han subido nada hoy?»
    «¿A quién se le va a vencer el acceso mañana?»
    «Se fue el vendedor con el teléfono: ¿qué hago?»

Las tres son operativas, no técnicas, y las tres tienen una respuesta que hay
que poder dar sin abrir una terminal.

────────────────────────────────────────────────────────────────────────────
LA POLÍTICA DE DÍAS SIN SINCRONIZAR SE AVISA ANTES, NO DESPUÉS
────────────────────────────────────────────────────────────────────────────
La credencial local caduca a los `dias_max_offline` días y entonces el login
offline deja de funcionar: el vendedor tiene que conectarse para renovarla. Eso
ya estaba y es correcto.

Lo que faltaba es que alguien lo vea venir. Enterarse a las 6 de la mañana, en
la bodega, con el camión cargado y sin señal, es un día de ruta perdido —y
perfectamente evitable, porque el servidor sabe con días de antelación cuáles
están cerca. La pantalla los ordena por rezago, así que los que están por
caducar salen arriba solos.

────────────────────────────────────────────────────────────────────────────
TRES ACCIONES, Y LA TERCERA DESTRUYE DATOS
────────────────────────────────────────────────────────────────────────────
· **Suspender** — el equipo deja de recibir datos nuevos pero puede seguir
  entregando lo que trae. Es reversible.
· **Revocar** — mata sus tokens. Reversible solo registrando el equipo otra vez.
· **Ordenar el borrado** — el teléfono se borra solo cuando termine de
  entregar. NO es reversible, y por eso es la única acción de todo el panel que
  pide escribir una palabra para confirmar, además del motivo.

La diferencia entre revocar y borrar es la que más cuesta si se confunde:
revocar protege los datos del SERVIDOR, borrar quita la copia que el teléfono
lleva dentro. Lo primero no hace lo segundo, y la pantalla lo dice con esas
palabras.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from starlette import status

from app.api.admin.comun import SesionDep, render, texto_o_nulo
from app.api.admin.sesion_web import ActorWeb, exigir_csrf
from app.core.config import obtener_config
from app.domain.identificadores import nuevo_id

router = APIRouter(prefix="/panel/equipos", tags=["panel"], include_in_schema=False)

PERMISO = "dispositivos.administrar"

# La palabra que hay que teclear para ordenar un borrado.
#
# Es el mismo recurso que `make db-borrar`, y por la misma razón: la acción
# destruye datos y no se deshace. Un botón de «¿seguro?» se contesta con un clic
# reflejo; escribir una palabra obliga a leer.
CONFIRMACION_BORRADO = "BORRAR"

ETIQUETA_ESTADO = {
    "activo": "Activo",
    "suspendido": "Suspendido",
    "revocado": "Revocado",
}


@router.get("", response_class=HTMLResponse)
async def listar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    aviso: str = "",
    error: str = "",
) -> HTMLResponse:
    """Los equipos, ordenados por rezago: los que urgen salen arriba.

    El orden es la mitad del valor de esta pantalla. Por nombre o por fecha de
    registro, el equipo que lleva cinco días sin subir queda en medio de la
    lista y no lo ve nadie.
    """
    actor.exigir("inventario.ver")
    cfg = obtener_config()

    filas = (
        await sesion.execute(
            text(
                """
                SELECT d.id, d.etiqueta, d.modelo, d.app_version, d.estado,
                       d.ultima_sync_push_en, d.ultima_sync_pull_en,
                       d.cola_pendiente, d.cola_reportada_en,
                       d.revocado_en, d.revocado_motivo,
                       d.borrado_ordenado_en, d.borrado_motivo,
                       d.borrado_confirmado_en, d.borrado_cola_al_confirmar,
                       d.registrado_en,
                       u.id AS usuario_id, u.codigo AS usuario_codigo,
                       u.nombre AS usuario_nombre, u.rol_codigo,
                       COALESCE(u.dias_max_offline, :omision) AS dias_max_offline,
                       quien.nombre AS borrado_pedido_por,
                       -- Días completos desde el último push. NULL si nunca
                       -- ha hecho uno: «nunca» y «hoy» no son lo mismo, y un 0
                       -- ahí haría ver al equipo recién registrado como al día.
                       CASE WHEN d.ultima_sync_push_en IS NULL THEN NULL
                            ELSE EXTRACT(DAY FROM (now() - d.ultima_sync_push_en))::int
                       END AS dias_sin_subir
                  FROM dispositivos d
                  JOIN usuarios u ON u.id = d.usuario_id
                  LEFT JOIN usuarios quien ON quien.id = d.borrado_ordenado_por
                 ORDER BY
                   -- Los revocados al final: ya no hay nada que hacer con ellos.
                   (d.estado = 'revocado'),
                   -- Primero los que tienen orden de borrado sin confirmar: son
                   -- los que esperan una decisión (ir por el equipo).
                   (d.borrado_ordenado_en IS NULL OR d.borrado_confirmado_en IS NOT NULL),
                   -- Y después por rezago, con «nunca sincronizó» arriba.
                   d.ultima_sync_push_en ASC NULLS FIRST
                """
            ),
            {"omision": cfg.dias_max_offline},
        )
    ).mappings().all()

    hoy = datetime.now(UTC).date()
    equipos = []
    for f in filas:
        dias = f["dias_sin_subir"]
        limite = f["dias_max_offline"]
        # `restantes` es lo que la oficina necesita leer: cuántos días le quedan
        # a la credencial antes de exigir conexión. Negativo = ya caducó.
        restantes = None if dias is None else limite - dias
        equipos.append(
            dict(
                f,
                dias_restantes=restantes,
                al_dia=(
                    f["ultima_sync_push_en"] is not None
                    and f["ultima_sync_push_en"].date() >= hoy
                ),
                borrado_pendiente=(
                    f["borrado_ordenado_en"] is not None
                    and f["borrado_confirmado_en"] is None
                ),
            )
        )

    # Los vendedores que todavía no tienen un equipo activo: son los únicos a
    # quienes tiene sentido vincular uno, y presentarlos ya filtrados evita el
    # único error que esta pantalla puede dar — «ya tiene uno activo».
    sin_equipo = (
        await sesion.execute(
            text(
                """
                SELECT u.id, u.codigo, u.nombre
                  FROM usuarios u
                 WHERE u.rol_codigo = 'vendedor' AND u.activo
                   AND NOT EXISTS (
                         SELECT 1 FROM dispositivos d
                          WHERE d.usuario_id = u.id AND d.estado = 'activo')
                 ORDER BY u.codigo
                """
            )
        )
    ).mappings().all()

    return render(
        peticion,
        "equipos.html",
        {
            "equipos": equipos,
            "sin_equipo": sin_equipo,
            "dias_max_omision": cfg.dias_max_offline,
            "confirmacion": CONFIRMACION_BORRADO,
            "etiqueta_estado": ETIQUETA_ESTADO,
            "puede_editar": actor.puede(PERMISO),
            "rezagados": sum(1 for e in equipos if e["estado"] == "activo" and not e["al_dia"]),
            "por_caducar": sum(
                1
                for e in equipos
                if e["estado"] == "activo"
                and e["dias_restantes"] is not None
                and e["dias_restantes"] <= 2
            ),
            "borrados_pendientes": sum(1 for e in equipos if e["borrado_pendiente"]),
            "aviso": aviso,
            "error": error,
        },
        actor=actor,
        seccion="Teléfonos",
    )


def _volver(aviso: str = "", error: str = "") -> RedirectResponse:
    consulta = f"?aviso={aviso}" if aviso else f"?error={error}" if error else ""
    return RedirectResponse(
        f"/panel/equipos{consulta}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/registrar")
async def registrar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    vendedor_id: Annotated[str, Form()] = "",
    etiqueta: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Vincula un teléfono a un vendedor, desde la oficina.

    ─────────────────────────────────────────────────────────────────────────
    ESTE PASO NO EXISTÍA Y SIN ÉL NINGÚN VENDEDOR PUEDE ENTRAR A LA APP
    ─────────────────────────────────────────────────────────────────────────
    El login de un vendedor exige `dispositivo_id` de un equipo registrado y
    suyo (`api/v1/auth.py`), y `/dispositivos/registrar` crea el equipo a nombre
    de QUIEN LLAMA — que necesita un token, que necesita el login. La cadena se
    cierra sobre sí misma: un teléfono nuevo no tenía por dónde empezar.

    El único código que lo tapaba era el sembrador del modo demo, que escribe la
    credencial directo en el SQLite del teléfono. Los dos cerrojos de compilación
    lo eliminan del binario de release, así que en producción el camino
    simplemente no existía.

    Se rompe aquí, desde la oficina, y no relajando la regla del servidor: los
    equipos son de la empresa, y quién usa cuál es una decisión de la oficina.
    Dejar que un teléfono se vincule solo con las credenciales del vendedor haría
    que cualquiera que las consiga pueda enrolar su propio aparato.

    ─────────────────────────────────────────────────────────────────────────
    EL ID LO GENERA EL SERVIDOR AQUÍ, Y SE TECLEA EN EL TELÉFONO
    ─────────────────────────────────────────────────────────────────────────
    En la API lo genera el dispositivo —igual que los documentos de campo— y
    tiene sentido ahí. Aquí no hay dispositivo todavía: el teléfono no existe en
    el sistema hasta que alguien lo vincula. Así que lo genera el servidor y la
    pantalla lo muestra para teclearlo una vez en la app.
    """
    exigir_csrf(peticion, csrf)
    actor.exigir(PERMISO)

    nombre = texto_o_nulo(etiqueta, maximo=120)
    if nombre is None:
        return _volver(
            error="Ponle una etiqueta al equipo: «Moto G54 — Bryan». "
            "Es lo que se lee en la lista cuando hay ocho."
        )

    try:
        vendedor = uuid.UUID(vendedor_id)
    except ValueError:
        return _volver(error="Elige a qué vendedor se le vincula el equipo.")

    fila = (
        await sesion.execute(
            text(
                "SELECT nombre, rol_codigo, activo FROM usuarios WHERE id = :u"
            ),
            {"u": vendedor},
        )
    ).mappings().first()
    if fila is None:
        return _volver(error="Ese usuario no existe.")
    if fila["rol_codigo"] != "vendedor":
        return _volver(
            error="Solo un vendedor opera un teléfono en la calle. "
            "Gerencia entra al tablero con su usuario, sin vincular equipo."
        )
    if not fila["activo"]:
        return _volver(error=f"{fila['nombre']} está dado de baja.")

    # Un usuario opera UN equipo activo a la vez, y hay un índice único parcial
    # que lo impone (migración 0001). Se comprueba antes para dar un mensaje en
    # vez de un error de restricción, que en una pantalla es un 500.
    otro = (
        await sesion.execute(
            text(
                "SELECT etiqueta FROM dispositivos "
                " WHERE usuario_id = :u AND estado = 'activo'"
            ),
            {"u": vendedor},
        )
    ).scalar()
    if otro is not None:
        return _volver(
            error=f"{fila['nombre']} ya tiene «{otro}» activo. Suspende o revoca "
            "ese equipo antes de vincular otro: un vendedor opera uno a la vez."
        )

    # `registrado_en` lo pone la base con `now()`; no se pasa para no tener dos
    # relojes diciendo cuándo ocurrió esto.
    dispositivo = nuevo_id()
    await sesion.execute(
        text(
            """
            INSERT INTO dispositivos (id, usuario_id, etiqueta, estado)
            VALUES (:id, :u, :etiqueta, 'activo')
            """
        ),
        {"id": dispositivo, "u": vendedor, "etiqueta": nombre},
    )
    await sesion.commit()

    # Los rangos de folio NO se asignan aquí a propósito: los pide la app con
    # `/v1/dispositivos/{id}/folios` al vincularse, porque tienen que acabar en el
    # SQLite del teléfono y asignarlos en el servidor no los pone ahí. Ese
    # endpoint es idempotente —devuelve el rango activo si ya hay uno— así que la
    # app puede pedirlos otra vez cuando le falten.
    return _volver(
        aviso=f"Equipo «{nombre}» vinculado a {fila['nombre']}. "
        f"Tecléalo en el teléfono una sola vez: {dispositivo}"
    )


@router.post("/{dispositivo_id}/estado")
async def cambiar_estado(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    dispositivo_id: uuid.UUID,
    destino: Annotated[str, Form()],
    csrf: str = Form(""),
    motivo: str = Form(""),
) -> RedirectResponse:
    """Suspende, reactiva o revoca.

    Revocar exige motivo; suspender no. La diferencia es que suspender es una
    medida temporal que se deshace el mismo día, y revocar se consulta meses
    después —«¿por qué este equipo está muerto?»— cuando ya nadie se acuerda.
    """
    exigir_csrf(peticion, csrf)
    actor.exigir(PERMISO)

    if destino not in ("activo", "suspendido", "revocado"):
        return _volver(error="Estado inválido.")

    fila = (
        await sesion.execute(
            text("SELECT estado, usuario_id FROM dispositivos WHERE id = :d"),
            {"d": dispositivo_id},
        )
    ).mappings().first()
    if fila is None:
        return _volver(error="Ese equipo no existe.")

    if destino == "revocado" and not (motivo or "").strip():
        return _volver(error="Revocar un equipo exige un motivo.")

    if destino == "activo":
        # Un usuario opera UN equipo a la vez: hay un índice único parcial que
        # lo impone (migración 0001). Se comprueba aquí para dar un mensaje en
        # vez de un error de restricción, que en una pantalla es un 500.
        otro = (
            await sesion.execute(
                text(
                    "SELECT etiqueta FROM dispositivos "
                    " WHERE usuario_id = :u AND estado = 'activo' AND id <> :d"
                ),
                {"u": fila["usuario_id"], "d": dispositivo_id},
            )
        ).scalar_one_or_none()
        if otro is not None:
            return _volver(
                error=(
                    f"Ese usuario ya tiene activo «{otro}». Un vendedor opera un "
                    "solo equipo a la vez: suspende el otro primero."
                )
            )

    ahora = datetime.now(UTC)
    await sesion.execute(
        text(
            """
            UPDATE dispositivos
               SET estado = :destino,
                   revocado_en = CASE WHEN :destino = 'revocado'
                                      THEN COALESCE(revocado_en, :ahora) END,
                   revocado_motivo = CASE WHEN :destino = 'revocado'
                                          THEN :motivo END
             WHERE id = :d
            """
        ),
        {
            "destino": destino,
            "ahora": ahora,
            "motivo": texto_o_nulo(motivo),
            "d": dispositivo_id,
        },
    )
    if destino == "revocado":
        await sesion.execute(
            text(
                "UPDATE sesiones SET revocada_en = now() "
                "WHERE dispositivo_id = :d AND revocada_en IS NULL"
            ),
            {"d": dispositivo_id},
        )
    await sesion.commit()
    return _volver(aviso=f"Equipo {ETIQUETA_ESTADO[destino].lower()}.")


@router.post("/{dispositivo_id}/borrado")
async def ordenar_borrado(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    dispositivo_id: uuid.UUID,
    csrf: str = Form(""),
    motivo: str = Form(""),
    confirmacion: str = Form(""),
) -> RedirectResponse:
    """Ordena el borrado remoto. El teléfono entrega su cola ANTES de borrar.

    Deja el equipo en `suspendido` y no en `revocado`, y es la decisión que hace
    que todo esto funcione: un equipo suspendido todavía puede hacer push, así
    que puede subir lo que trae dentro. Revocarlo lo dejaría sin forma de
    entregar, y entonces el borrado costaría las ventas del día.
    """
    exigir_csrf(peticion, csrf)
    actor.exigir(PERMISO)

    if confirmacion.strip() != CONFIRMACION_BORRADO:
        return _volver(
            error=(
                f"Para ordenar un borrado hay que escribir {CONFIRMACION_BORRADO} "
                "en mayúsculas. No se deshace."
            )
        )
    limpio = (motivo or "").strip()
    if len(limpio) < 3:
        return _volver(error="El borrado exige un motivo: se consulta meses después.")

    actualizadas = (
        await sesion.execute(
            text(
                """
                UPDATE dispositivos
                   SET borrado_ordenado_en = COALESCE(borrado_ordenado_en, now()),
                       borrado_ordenado_por = :quien,
                       borrado_motivo = :motivo,
                       -- A `suspendido` solo si estaba activo: si ya estaba
                       -- revocado, reactivarlo a suspendido le devolvería la
                       -- capacidad de hacer push, que es lo contrario de lo
                       -- que alguien decidió al revocarlo.
                       estado = CASE WHEN estado = 'activo' THEN 'suspendido'
                                     ELSE estado END
                 WHERE id = :d AND borrado_confirmado_en IS NULL
                """
            ),
            {"quien": actor.usuario_id, "motivo": limpio[:300], "d": dispositivo_id},
        )
    ).rowcount
    await sesion.commit()

    if not actualizadas:
        return _volver(error="Ese equipo no existe o ya confirmó su borrado.")
    return _volver(
        aviso=(
            "Borrado ordenado. El teléfono lo ejecutará cuando termine de subir "
            "lo que trae; hasta entonces aparece como pendiente."
        )
    )


@router.post("/{dispositivo_id}/borrado/cancelar")
async def cancelar_borrado(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    dispositivo_id: uuid.UUID,
    csrf: str = Form(""),
) -> RedirectResponse:
    """Quita la orden antes de que el teléfono la ejecute.

    Sirve para el caso real: se ordenó el borrado del equipo equivocado, o el
    teléfono apareció. Solo funciona mientras no esté confirmado — después ya no
    hay nada que cancelar, los datos del teléfono ya no existen.

    No devuelve el equipo a `activo` por su cuenta: quien ordenó el borrado pudo
    haber querido suspenderlo también, y reactivarlo en silencio sería decidir
    por esa persona. La pantalla deja el botón de reactivar al lado.
    """
    exigir_csrf(peticion, csrf)
    actor.exigir(PERMISO)

    actualizadas = (
        await sesion.execute(
            text(
                """
                UPDATE dispositivos
                   SET borrado_ordenado_en = NULL,
                       borrado_ordenado_por = NULL,
                       borrado_motivo = NULL
                 WHERE id = :d AND borrado_confirmado_en IS NULL
                """
            ),
            {"d": dispositivo_id},
        )
    ).rowcount
    await sesion.commit()

    if not actualizadas:
        return _volver(error="No hay orden que cancelar: el teléfono ya borró.")
    return _volver(aviso="Orden de borrado cancelada. El equipo sigue suspendido.")
