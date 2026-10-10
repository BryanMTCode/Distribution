"""El plan de visita de una ruta: qué días toca cada cliente, y en qué orden.

────────────────────────────────────────────────────────────────────────────
POR QUÉ UNA RUTA ENTERA EN UNA PANTALLA
────────────────────────────────────────────────────────────────────────────
Planear se hace por ruta, no por cliente: lo que se busca es que el lunes no
tenga cuarenta tiendas y el martes ocho. Por eso la pantalla muestra todos los
clientes de la ruta con siete casillas cada uno, la cuenta por día arriba, y un
solo botón — igual que la carga masiva. Capturarlo cliente por cliente sería
cuarenta vueltas de abrir, palomear y guardar.

────────────────────────────────────────────────────────────────────────────
LO QUE GUARDAR NO TOCA
────────────────────────────────────────────────────────────────────────────
Un día que ya estaba en el plan y sigue palomeado **no se reescribe**: conserva
su `desde`. Si se borrara y se volviera a insertar, cada guardado movería la fecha
desde la que el plan cuenta y Efectividad perdería la historia de cumplimiento.
Solo se agrega lo nuevo y se quita lo desmarcado.

Los clientes con un plan **por semanas del mes** (solo la semana 1 y 3, por
ejemplo) no se editan aquí: siete casillas no pueden mostrar ese plan sin
aplanarlo a «todas las semanas». Se ven marcados como personalizados y se
editan en su ficha.

────────────────────────────────────────────────────────────────────────────
CÓMO LLEGA AL TELÉFONO
────────────────────────────────────────────────────────────────────────────
`clientes_frecuencia` tiene un disparador (migración 0041) que copia el plan a
`clientes.plan_visita`, y ese UPDATE publica el delta del cliente que el
teléfono ya sabe aplicar. No hay entidad nueva en la sincronización.
"""

from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.api.admin.comun import CapturaInvalida, SesionDep, auditar, leer_entero, render
from app.api.admin.sesion_web import ActorWeb, exigir_csrf

router = APIRouter(prefix="/panel/plan-visita", tags=["panel"], include_in_schema=False)

PERMISO_VER = "clientes.ver"
PERMISO = "clientes.administrar"

# El orden de la semana como se trabaja —de lunes a domingo— con el número que usa
# la base: `extract(dow)`, donde 0 es domingo.
DIAS = (
    (1, "Lun", "lunes"),
    (2, "Mar", "martes"),
    (3, "Mié", "miércoles"),
    (4, "Jue", "jueves"),
    (5, "Vie", "viernes"),
    (6, "Sáb", "sábado"),
    (0, "Dom", "domingo"),
)


def _volver(ruta_id, *, guardado: str = "", error: str = "") -> RedirectResponse:
    destino = f"/panel/plan-visita?ruta={ruta_id}"
    if error:
        destino += f"&error={quote(error)}"
    elif guardado:
        destino += f"&guardado={quote(guardado)}"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


async def plan_de(sesion, cliente_ids: list[uuid.UUID]) -> dict[uuid.UUID, set]:
    """El plan actual de cada cliente: {cliente: {(dia, semana), ...}}."""
    if not cliente_ids:
        return {}
    filas = (
        await sesion.execute(
            text(
                "SELECT cliente_id, dia_semana, semana_del_mes FROM clientes_frecuencia "
                " WHERE cliente_id = ANY(:ids)"
            ),
            {"ids": cliente_ids},
        )
    ).all()
    plan: dict[uuid.UUID, set] = {c: set() for c in cliente_ids}
    for cliente_id, dia, semana in filas:
        plan[cliente_id].add((dia, semana))
    return plan


async def reemplazar_plan(sesion, cliente_id: uuid.UUID, deseado: set) -> tuple[int, int]:
    """Deja el plan del cliente en `deseado`, tocando solo lo que cambió.

    Devuelve (agregados, quitados). Lo que ya estaba conserva su `desde`.
    """
    actual = (await plan_de(sesion, [cliente_id]))[cliente_id]
    quitar = actual - deseado
    agregar = deseado - actual
    for dia, semana in quitar:
        await sesion.execute(
            text(
                "DELETE FROM clientes_frecuencia WHERE cliente_id = :c AND dia_semana = :d "
                "   AND semana_del_mes IS NOT DISTINCT FROM :s"
            ),
            {"c": cliente_id, "d": dia, "s": semana},
        )
    if agregar:
        await sesion.execute(
            text(
                "INSERT INTO clientes_frecuencia (cliente_id, dia_semana, semana_del_mes) "
                "SELECT :c, d, s FROM unnest(CAST(:dias AS smallint[]), "
                "                            CAST(:semanas AS smallint[])) AS t(d, s)"
            ),
            {
                "c": cliente_id,
                "dias": [d for d, _ in agregar],
                "semanas": [s for _, s in agregar],
            },
        )
    return len(agregar), len(quitar)


@router.get("", response_class=HTMLResponse)
async def ver(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    ruta: str = "",
    guardado: str = "",
    error: str = "",
) -> HTMLResponse:
    actor.exigir(PERMISO_VER)

    rutas = (
        await sesion.execute(
            text(
                "SELECT r.id, r.codigo, r.nombre, u.nombre AS vendedor "
                "  FROM rutas r LEFT JOIN usuarios u ON u.id = r.vendedor_id "
                " WHERE r.activo ORDER BY r.codigo"
            )
        )
    ).mappings().all()

    elegida = None
    if ruta:
        try:
            ruta_id = uuid.UUID(ruta)
        except ValueError:
            ruta_id = None
        elegida = next((r for r in rutas if r["id"] == ruta_id), None)
    if elegida is None and rutas:
        elegida = rutas[0]

    clientes: list[dict] = []
    por_dia = {dow: 0 for dow, _, _ in DIAS}
    sin_plan = 0
    if elegida is not None:
        filas = (
            await sesion.execute(
                text(
                    """
                    SELECT c.id, c.codigo, c.nombre_comercial, c.secuencia, c.colonia
                      FROM clientes c
                     WHERE c.ruta_id = :r AND c.estatus <> 'baja'
                     ORDER BY c.secuencia NULLS LAST, c.nombre_comercial
                    """
                ),
                {"r": elegida["id"]},
            )
        ).mappings().all()
        plan = await plan_de(sesion, [f["id"] for f in filas])
        for f in filas:
            dias = plan[f["id"]]
            personalizado = any(semana is not None for _, semana in dias)
            semanales = {d for d, s in dias if s is None}
            for d, _ in dias:
                por_dia[d] += 1
            if not dias:
                sin_plan += 1
            clientes.append(
                {
                    **dict(f),
                    "dias": semanales if not personalizado else {d for d, _ in dias},
                    "personalizado": personalizado,
                    "detalle_semanas": sorted(dias, key=lambda x: (x[0], x[1] or 0)),
                }
            )

    return render(
        peticion,
        "plan_visita.html",
        {
            "rutas": rutas,
            "ruta": elegida,
            "clientes": clientes,
            "dias": DIAS,
            "por_dia": por_dia,
            "sin_plan": sin_plan,
            "puede_editar": actor.puede(PERMISO),
            "guardado": guardado,
            "error": error,
        },
        actor=actor,
        seccion="Clientes",
    )


@router.post("/{ruta_id}")
async def guardar(peticion: Request, actor: ActorWeb, sesion: SesionDep, ruta_id: uuid.UUID):
    """Guarda los días y el orden de todos los clientes de la ruta, de una vez."""
    actor.exigir(PERMISO)
    formulario = await peticion.form()
    exigir_csrf(peticion, str(formulario.get("csrf", "")))

    clientes = (
        await sesion.execute(
            text(
                "SELECT id, nombre_comercial, secuencia FROM clientes "
                " WHERE ruta_id = :r AND estatus <> 'baja'"
            ),
            {"r": ruta_id},
        )
    ).mappings().all()
    if not clientes:
        return _volver(ruta_id, error="Esta ruta no tiene clientes que planear.")
    plan = await plan_de(sesion, [c["id"] for c in clientes])

    # Primero se valida todo: un orden con letras en el renglón treinta no debe
    # dejar guardados los veintinueve de arriba y perdidos los de abajo.
    ordenes: dict[uuid.UUID, int | None] = {}
    for c in clientes:
        crudo = str(formulario.get(f"orden_{c['id']}", "")).strip()
        try:
            ordenes[c["id"]] = leer_entero(crudo, campo="El orden", maximo=9999) or None
        except CapturaInvalida:
            return _volver(
                ruta_id,
                error=f"El orden de «{c['nombre_comercial']}» no es un número: «{crudo}».",
            )

    cambiados = 0
    for c in clientes:
        cliente_id = c["id"]
        cambio = False
        # Los personalizados (por semanas del mes) se editan en su ficha: siete
        # casillas aplanarían su plan a «todas las semanas».
        if not any(s is not None for _, s in plan[cliente_id]):
            deseado = {
                (dow, None)
                for dow, _, _ in DIAS
                if formulario.get(f"dia_{cliente_id}_{dow}")
            }
            agregados, quitados = await reemplazar_plan(sesion, cliente_id, deseado)
            cambio = bool(agregados or quitados)
        if ordenes[cliente_id] != c["secuencia"]:
            await sesion.execute(
                text(
                    "UPDATE clientes SET secuencia = :s, actualizado_en = now() WHERE id = :c"
                ),
                {"s": ordenes[cliente_id], "c": cliente_id},
            )
            cambio = True
        cambiados += cambio

    await auditar(
        sesion,
        entidad="ruta",
        entidad_id=ruta_id,
        accion="plan_de_visita",
        quien=actor.usuario_id,
        despues={"clientes_con_cambio": cambiados},
    )
    await sesion.commit()
    if not cambiados:
        return _volver(ruta_id, guardado="No había nada que cambiar.")
    return _volver(
        ruta_id,
        guardado=f"Plan guardado: {cambiados} cliente(s) con cambios. El teléfono de la "
        "ruta lo recibe en su siguiente sincronización.",
    )
