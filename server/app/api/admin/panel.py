"""Panel de operación: HTML renderizado en el servidor.

────────────────────────────────────────────────────────────────────────────
QUÉ ES Y QUÉ NO ES ESTE PANEL
────────────────────────────────────────────────────────────────────────────
Es la herramienta de la oficina para **operar**: capturar catálogo y precios,
atender lo que el motor de sincronización rechazó, y revisar las ventas que
entraron marcadas.

No es un dashboard de análisis. Los números que muestra son operativos —cuántas
cosas necesitan atención ahora—, no indicadores de negocio. El análisis profundo
va en Streamlit sobre modelos de lectura (Fase 8), y mezclarlos terminaría en
consultas analíticas pesadas corriendo sobre las tablas transaccionales mientras
un vendedor sincroniza.

────────────────────────────────────────────────────────────────────────────
POR QUÉ SERVIDOR Y NO UNA SPA
────────────────────────────────────────────────────────────────────────────
Cinco personas en una oficina, sobre red local. Una SPA agregaría un proceso de
compilación, un segundo lenguaje y un estado duplicado en el navegador para
resolver un problema que no existe. Formularios y recargas completas son
instantáneos en LAN, y funcionan el día que alguien entre desde una computadora
vieja.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.api.admin.arranque import faltan_para_operar, pendientes_de_hoy, revisar_arranque
from app.api.admin.comun import SesionDep, dinero, render
from app.api.admin.periodo import PERIODOS, leer_periodo
from app.api.admin.sesion_web import (
    ActorWeb,
    abrir_sesion,
    cerrar_sesion,
    exigir_csrf,
)
from app.core.seguridad import verificar_password
from app.infra.models import Usuario

# `include_in_schema=False`: el panel NO entra al OpenAPI.
#
# De `contracts/openapi.json` sale el cliente Dart del dispositivo. Meter ahí
# rutas que devuelven HTML para una oficina ensuciaría el contrato del teléfono y
# haría que cada pantalla nueva del panel apareciera como un cambio de contrato
# en el diff. El panel es una interfaz, no una API.
#
# Las pantallas de catálogo y de clientes viven en `productos.py` y `clientes.py`,
# con su propio router. El andamio que comparten las tres (la navegación, el
# `render`, los lectores de campos numéricos) está en `comun.py`.
router = APIRouter(prefix="/panel", tags=["panel"], include_in_schema=False)


# ---------------------------------------------------------------------------
# Entrar y salir
# ---------------------------------------------------------------------------


@router.get("/entrar", response_class=HTMLResponse)
async def pantalla_entrar(peticion: Request) -> HTMLResponse:
    return render(peticion, "entrar.html", {"error": None})


@router.post("/entrar")
async def procesar_entrar(
    peticion: Request,
    sesion: SesionDep,
    codigo: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    usuario = (
        await sesion.execute(
            text("SELECT * FROM usuarios WHERE codigo = :c"), {"c": codigo.strip()}
        )
    ).mappings().first()

    # El mensaje es el MISMO para usuario inexistente, contraseña incorrecta y
    # usuario inactivo. Distinguirlos confirmaría qué códigos de empleado existen,
    # y el código de empleado es lo primero que alguien adivina.
    generico = "Código o contraseña incorrectos."

    if usuario is None or not usuario["activo"]:
        # Se verifica un hash de todas formas: responder de inmediato cuando el
        # usuario no existe delata su ausencia por el tiempo de respuesta.
        verificar_password(password, _HASH_SEÑUELO)
        return render(peticion, "entrar.html", {"error": generico})

    if not verificar_password(password, usuario["password_hash"]):
        return render(peticion, "entrar.html", {"error": generico})

    if usuario["rol_codigo"] == "vendedor":
        # El vendedor tiene su app; el panel es de oficina. Dejarlo entrar aquí le
        # daría pantallas de captura que no le corresponden.
        return render(
            peticion,
            "entrar.html",
            {"error": "Tu usuario es de ruta: entra por la app del teléfono."},
        )

    modelo = await sesion.get(Usuario, usuario["id"])
    respuesta = RedirectResponse("/panel", status_code=status.HTTP_303_SEE_OTHER)
    await abrir_sesion(
        sesion,
        modelo,
        respuesta,
        ip=peticion.client.host if peticion.client else None,
    )
    return respuesta


# Hash real de una contraseña que nadie usa, para gastar el mismo tiempo cuando el
# usuario no existe. Se genera al importar el módulo.
def _generar_señuelo() -> str:
    from app.core.seguridad import hashear_password

    return hashear_password("no-existe-este-usuario")


_HASH_SEÑUELO = _generar_señuelo()


@router.get("/salir")
async def salir(peticion: Request, sesion: SesionDep):
    respuesta = RedirectResponse("/panel/entrar", status_code=status.HTTP_303_SEE_OTHER)
    await cerrar_sesion(sesion, peticion, respuesta)
    return respuesta


# ---------------------------------------------------------------------------
# Tablero
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
async def tablero(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    periodo: str = "",
    desde: str = "",
    hasta: str = "",
) -> HTMLResponse:
    """Los indicadores que dicen qué necesita atención hoy.

    Cada uno es una consulta corta con índice. El tablero se abre cien veces al
    día: una consulta que recorra `ventas` completa lo volvería lento justo cuando
    el volumen crezca, que es cuando más se necesita.

    Las cifras de ventas y dinero son del PERIODO elegido (hoy por omisión). Los
    pendientes y los estados —la cuarentena, lo que espera al banco, la cartera
    vencida— son de ahora: no tiene sentido preguntar cuánta cuarentena había el
    mes pasado, sino cuánta hay que atender.
    """
    rango = leer_periodo(periodo, desde, hasta)
    fila = (
        await sesion.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM sync_cuarentena WHERE estado = 'pendiente')
                    AS cuarentena,
                  (SELECT count(*) FROM ventas
                    WHERE requiere_revision AND estado = 'confirmada') AS en_revision,
                  (SELECT count(*) FROM dispositivos WHERE estado = 'activo')
                    AS equipos_activos,
                  (SELECT count(*) FROM dispositivos
                    WHERE estado = 'activo'
                      AND (ultima_sync_push_en IS NULL
                           OR ultima_sync_push_en < CURRENT_DATE)) AS equipos_rezagados,
                  (SELECT count(*) FROM ventas
                    WHERE fecha_operativa BETWEEN :desde AND :hasta
                      AND estado = 'confirmada')
                    AS ventas_hoy,
                  (SELECT COALESCE(sum(total), 0) FROM ventas
                    WHERE fecha_operativa BETWEEN :desde AND :hasta
                      AND estado = 'confirmada')
                    AS importe_hoy,
                  (SELECT count(*) FROM cobros
                    WHERE requiere_revision AND estado IN ('confirmado', 'por_confirmar'))
                    AS cobros_en_revision,
                  -- Lo que espera al banco: no baja ninguna deuda hasta que alguien
                  -- lo confirme, así que un número que crece es crédito congelado.
                  (SELECT count(*) FROM cobros WHERE estado = 'por_confirmar')
                    AS cobros_por_confirmar,
                  (SELECT COALESCE(sum(importe), 0) FROM cobros
                    WHERE estado = 'por_confirmar') AS importe_por_confirmar,
                  -- EFECTIVO A ENTREGAR = VENTAS DE CONTADO + COBROS EN EFECTIVO.
                  --
                  -- Faltaba el primer término, y con él el caso más común de una
                  -- ruta: una venta de contado es dinero que el vendedor trae en la
                  -- bolsa y tiene que entregar. Con solo los cobros, un día entero
                  -- de ventas de contado mostraba «$0.00 efectivo a entregar hoy»
                  -- junto a las ventas ya sincronizadas en el mismo tablero.
                  --
                  -- Esta cuenta tiene que dar LO MISMO que `_efectivo_esperado` de
                  -- `liquidaciones.py`, que es la que decide el arqueo. Si las dos
                  -- no coinciden, el tablero promete un número y la liquidación
                  -- cobra otro — y el vendedor discute con razón.
                  --
                  -- Las ventas a CRÉDITO no suman: no se cobró nada. Y de los
                  -- cobros, solo los de forma `efectivo`: una transferencia entra al
                  -- sistema pero no a la bolsa, y sumarla haría que la caja nunca
                  -- cuadre y que el descuadre se atribuyera a quien no fue.
                  (
                    (SELECT COALESCE(sum(total), 0) FROM ventas
                      WHERE fecha_operativa BETWEEN :desde AND :hasta
                        AND estado = 'confirmada' AND tipo = 'contado')
                    +
                    (SELECT COALESCE(sum(importe), 0) FROM cobros
                      WHERE fecha_operativa BETWEEN :desde AND :hasta
                        AND estado = 'confirmado' AND forma_pago = 'efectivo')
                  ) AS efectivo_hoy,
                  (SELECT COALESCE(sum(saldo), 0) FROM cuentas_por_cobrar
                    WHERE estado IN ('abierta', 'parcial')
                      AND fecha_vencimiento < CURRENT_DATE) AS cartera_vencida,
                  (SELECT count(*) FROM productos WHERE activo) AS productos,
                  (SELECT count(*) FROM clientes WHERE estatus <> 'inactivo') AS clientes,
                  (SELECT count(*) FROM clientes WHERE estatus = 'prospecto')
                    AS prospectos,
                  (SELECT count(*) FROM productos p
                    WHERE p.activo
                      AND NOT EXISTS (
                        SELECT 1 FROM precios pr
                          JOIN listas_precios l ON l.id = pr.lista_id AND l.es_default
                         WHERE pr.producto_id = p.id)) AS sin_precio
                """
            ),
            {"desde": rango.inicio, "hasta": rango.fin},
        )
    ).mappings().one()

    indicadores = dict(fila)
    indicadores["importe_hoy"] = dinero(fila["importe_hoy"])
    indicadores["efectivo_hoy"] = dinero(fila["efectivo_hoy"])
    indicadores["importe_por_confirmar"] = dinero(fila["importe_por_confirmar"])
    # La bandera va aparte del texto: si la plantilla comparara el número ya
    # formateado contra "$0.00", el día que cambie el separador de miles la alerta
    # se encendería sola y nadie sabría por qué.
    indicadores["hay_cartera_vencida"] = Decimal(fila["cartera_vencida"] or 0) > 0
    indicadores["cartera_vencida"] = dinero(fila["cartera_vencida"])

    return render(
        peticion,
        "tablero.html",
        {
            "indicadores": indicadores,
            "pendientes": await pendientes_de_hoy(sesion, actor),
            "faltan_para_operar": faltan_para_operar(await revisar_arranque(sesion)),
            "periodo": rango,
            "periodos": PERIODOS,
            **await _cifras_del_periodo(sesion, rango),
        },
        actor=actor,
        seccion="Tablero",
    )


SQL_PERIODO = """
SELECT
  (SELECT COALESCE(sum(total) FILTER (WHERE tipo = 'contado'), 0) FROM ventas
    WHERE fecha_operativa BETWEEN :desde AND :hasta AND estado = 'confirmada') AS contado,
  (SELECT COALESCE(sum(total) FILTER (WHERE tipo = 'credito'), 0) FROM ventas
    WHERE fecha_operativa BETWEEN :desde AND :hasta AND estado = 'confirmada') AS credito,
  (SELECT count(*) FROM ventas
    WHERE fecha_operativa BETWEEN :desde AND :hasta AND estado = 'cancelada') AS canceladas,
  (SELECT COALESCE(sum(importe), 0) FROM cobros
    WHERE fecha_operativa BETWEEN :desde AND :hasta AND estado = 'confirmado') AS cobrado,
  (SELECT count(*) FROM mermas
    WHERE fecha_operativa BETWEEN :desde AND :hasta AND tipo <> 'devolucion_cliente')
    AS mermas,
  (SELECT count(*) FROM mermas
    WHERE fecha_operativa BETWEEN :desde AND :hasta AND tipo = 'devolucion_cliente')
    AS devoluciones,
  (SELECT count(*) FROM no_drops
    WHERE fecha_operativa BETWEEN :desde AND :hasta) AS no_ventas,
  (SELECT count(DISTINCT cliente_id) FROM ventas
    WHERE fecha_operativa BETWEEN :desde AND :hasta AND estado = 'confirmada')
    AS clientes_atendidos,
  (SELECT count(*) FROM clientes
    WHERE creado_en::date BETWEEN :desde AND :hasta) AS clientes_nuevos
"""

# Por vendedor: todos los vendedores activos, aunque no hayan vendido —un cero
# también es información—, y los inactivos solo si tuvieron algo en el periodo.
SQL_POR_VENDEDOR = """
WITH v AS (
    SELECT vendedor_id,
           count(*) AS ventas,
           sum(total) AS importe,
           sum(total) FILTER (WHERE tipo = 'contado') AS contado,
           sum(total) FILTER (WHERE tipo = 'credito') AS credito
      FROM ventas
     WHERE fecha_operativa BETWEEN :desde AND :hasta AND estado = 'confirmada'
     GROUP BY vendedor_id
), c AS (
    SELECT vendedor_id, sum(importe) AS cobrado
      FROM cobros
     WHERE fecha_operativa BETWEEN :desde AND :hasta AND estado = 'confirmado'
     GROUP BY vendedor_id
), m AS (
    SELECT vendedor_id, count(*) AS mermas
      FROM mermas
     WHERE fecha_operativa BETWEEN :desde AND :hasta AND tipo <> 'devolucion_cliente'
     GROUP BY vendedor_id
), n AS (
    SELECT vendedor_id, count(*) AS no_ventas
      FROM no_drops
     WHERE fecha_operativa BETWEEN :desde AND :hasta
     GROUP BY vendedor_id
)
SELECT u.id, u.codigo, u.nombre,
       COALESCE(v.ventas, 0) AS ventas, COALESCE(v.importe, 0) AS importe,
       COALESCE(v.contado, 0) AS contado, COALESCE(v.credito, 0) AS credito,
       COALESCE(c.cobrado, 0) AS cobrado, COALESCE(m.mermas, 0) AS mermas,
       COALESCE(n.no_ventas, 0) AS no_ventas
  FROM usuarios u
  LEFT JOIN v ON v.vendedor_id = u.id
  LEFT JOIN c ON c.vendedor_id = u.id
  LEFT JOIN m ON m.vendedor_id = u.id
  LEFT JOIN n ON n.vendedor_id = u.id
 WHERE u.rol_codigo = 'vendedor'
   AND (u.activo OR v.ventas IS NOT NULL OR c.cobrado IS NOT NULL)
 ORDER BY COALESCE(v.importe, 0) DESC, u.codigo
"""

SQL_POR_DIA = """
SELECT d::date AS fecha,
       COALESCE((SELECT count(*) FROM ventas
                  WHERE fecha_operativa = d::date AND estado = 'confirmada'), 0) AS ventas,
       COALESCE((SELECT sum(total) FROM ventas
                  WHERE fecha_operativa = d::date AND estado = 'confirmada'), 0) AS importe,
       COALESCE((SELECT sum(importe) FROM cobros
                  WHERE fecha_operativa = d::date AND estado = 'confirmado'), 0) AS cobrado
  FROM generate_series(CAST(:desde AS date), CAST(:hasta AS date), interval '1 day') d
 ORDER BY 1 DESC
"""

# El desglose por día se muestra hasta dos meses: más renglones ya no se leen,
# y para eso está el rango por vendedor o la analítica.
DIAS_MAXIMOS_POR_DIA = 62


async def _cifras_del_periodo(sesion, rango) -> dict:
    parametros = {"desde": rango.inicio, "hasta": rango.fin}
    cifras = dict((await sesion.execute(text(SQL_PERIODO), parametros)).mappings().one())
    por_vendedor = [
        dict(f) for f in (await sesion.execute(text(SQL_POR_VENDEDOR), parametros)).mappings()
    ]
    por_dia: list[dict] = []
    if 1 < rango.dias <= DIAS_MAXIMOS_POR_DIA:
        por_dia = [
            dict(f) for f in (await sesion.execute(text(SQL_POR_DIA), parametros)).mappings()
        ]
    # La barra de cada día es relativa al mejor día del periodo: es una forma de
    # ver de un vistazo qué día flojeó, no una gráfica con escala.
    tope = max((Decimal(d["importe"]) for d in por_dia), default=Decimal(0))
    for d in por_dia:
        d["barra"] = int(Decimal(d["importe"]) * 100 / tope) if tope else 0
    return {"cifras": cifras, "por_vendedor": por_vendedor, "por_dia": por_dia}


# ---------------------------------------------------------------------------
# Cuarentena
# ---------------------------------------------------------------------------


@router.get("/cuarentena", response_class=HTMLResponse)
async def cuarentena(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    estado: str = "pendiente",
) -> HTMLResponse:
    """Lo que el servidor rechazó.

    Es la pantalla más importante del panel, y la que no existía: hasta ahora una
    operación rechazada quedaba en una tabla que nadie miraba. El vendedor ya
    entregó la mercancía, así que cada fila aquí es un descuadre esperando.
    """
    if estado not in ("pendiente", "reprocesada", "descartada"):
        estado = "pendiente"

    filas = (
        await sesion.execute(
            text(
                """
                SELECT c.id, c.operacion_id, c.tipo, c.error_codigo, c.error_mensaje,
                       c.estado, c.recibido_en, c.nota_resolucion,
                       d.etiqueta AS equipo, u.nombre AS vendedor
                  FROM sync_cuarentena c
                  JOIN dispositivos d ON d.id = c.dispositivo_id
                  LEFT JOIN usuarios u ON u.id = c.usuario_id
                 WHERE c.estado = :estado
                 ORDER BY c.recibido_en DESC
                 LIMIT 200
                """
            ),
            {"estado": estado},
        )
    ).mappings().all()

    conteos = (
        await sesion.execute(
            text("SELECT estado, count(*) AS n FROM sync_cuarentena GROUP BY estado")
        )
    ).mappings().all()

    return render(
        peticion,
        "cuarentena.html",
        {
            "filas": filas,
            "estado": estado,
            "conteos": {c["estado"]: c["n"] for c in conteos},
        },
        actor=actor,
        seccion="Cuarentena",
    )


@router.get("/cuarentena/{id_fila}", response_class=HTMLResponse)
async def cuarentena_detalle(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    id_fila: int,
    error: str = "",
    guardado: str = "",
) -> HTMLResponse:
    """El payload completo, tal como llegó.

    Se muestra íntegro a propósito: para decidir qué hacer con una operación
    rechazada hay que ver exactamente qué mandó el equipo, no un resumen.
    """
    import json

    fila = (
        await sesion.execute(
            text(
                """
                SELECT c.*, d.etiqueta AS equipo, u.nombre AS vendedor
                  FROM sync_cuarentena c
                  JOIN dispositivos d ON d.id = c.dispositivo_id
                  LEFT JOIN usuarios u ON u.id = c.usuario_id
                 WHERE c.id = :id
                """
            ),
            {"id": id_fila},
        )
    ).mappings().first()

    if fila is None:
        return RedirectResponse("/panel/cuarentena", status_code=status.HTTP_303_SEE_OTHER)

    return render(
        peticion,
        "cuarentena_detalle.html",
        {
            "fila": fila,
            "payload": json.dumps(fila["payload"], indent=2, ensure_ascii=False),
            # `nota` es el aviso que ya usaba «descartar» cuando falta la nota.
            "error": "Escribe qué se hizo antes de marcarla como atendida."
            if error == "nota"
            else error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Cuarentena",
    )


@router.post("/cuarentena/{id_fila}/descartar")
async def descartar_cuarentena(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    id_fila: int,
    nota: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Marca la operación como atendida fuera del sistema.

    **No la borra.** El payload se conserva para siempre: es la única evidencia de
    una venta que existió en la calle y nunca entró. La nota es obligatoria porque
    "descartada" sin explicación es peor que pendiente — dentro de un mes nadie
    sabrá si se corrigió a mano o se perdió.
    """
    exigir_csrf(peticion, csrf)
    actor.exigir("sync.cuarentena")

    if not nota.strip():
        return RedirectResponse(
            f"/panel/cuarentena/{id_fila}?error=nota",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    await sesion.execute(
        text(
            "UPDATE sync_cuarentena "
            "   SET estado = 'descartada', resuelto_por = :quien, resuelto_en = now(), "
            "       nota_resolucion = :nota "
            " WHERE id = :id AND estado = 'pendiente'"
        ),
        {"id": id_fila, "quien": actor.usuario_id, "nota": nota.strip()},
    )
    await sesion.commit()
    return RedirectResponse("/panel/cuarentena", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/cuarentena/{id_fila}/reprocesar")
async def reprocesar_cuarentena_web(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    id_fila: int,
    csrf: Annotated[str, Form()] = "",
):
    """Vuelve a aplicar el payload guardado, con las mismas reglas que el push.

    Es la salida cuando el rechazo fue culpa nuestra —un servidor que todavía no
    conocía lo que mandó la app nueva—: sin esto, el teléfono reintenta y el
    servidor le contesta el mismo rechazo para siempre. Ver
    `ingesta.reprocesar_cuarentena`.
    """
    from urllib.parse import quote

    from app.infra.sync.ingesta import reprocesar_cuarentena

    exigir_csrf(peticion, csrf)
    actor.exigir("sync.cuarentena")
    paso, mensaje = await reprocesar_cuarentena(sesion, id_fila, actor.usuario_id)
    await sesion.commit()
    clave = "guardado" if paso else "error"
    return RedirectResponse(
        f"/panel/cuarentena/{id_fila}?{clave}={quote(mensaje)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ---------------------------------------------------------------------------
# Ventas en revisión
# ---------------------------------------------------------------------------

# Cómo se le explica a la oficina cada motivo. El código es para el sistema; esto
# es para la persona que tiene que decidir qué hacer.
EXPLICACION_MOTIVOS = {
    "excede_limite_credito": "Pasó su límite de crédito",
    "cliente_bloqueado": "El cliente estaba bloqueado",
    "precio_desactualizado": "Vendió con un precio que ya cambió",
    "sin_lista_de_precios": "No trae lista de precios",
    "fuera_de_geocerca": "Lejos del domicilio registrado",
    "sin_ubicacion": "Sin GPS al vender",
    "reloj_desfasado": "El reloj del equipo está desfasado",
}


@router.get("/ventas", response_class=HTMLResponse)
async def ventas(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    solo_revision: int = 1,
) -> HTMLResponse:
    """Las ventas recibidas, con las marcadas primero.

    El principio §0.1 dice que el servidor nunca rechaza una venta offline: la
    marca. **Esta pantalla es la otra mitad de ese principio.** Marcar sin que
    nadie mire convierte la bandera en ruido, y entonces el principio se vuelve
    una excusa para no validar.
    """
    filtro = "WHERE v.requiere_revision AND v.estado = 'confirmada'" if solo_revision else ""

    filas = (
        await sesion.execute(
            text(
                f"""
                SELECT v.id, v.folio_local, v.folio_servidor, v.total, v.tipo,
                       v.fecha_operativa, v.fecha_dispositivo, v.requiere_revision,
                       v.revision_motivos, v.distancia_cliente_m, v.desfase_reloj_seg,
                       v.estado, v.corregida_en,
                       c.nombre_comercial AS cliente, u.nombre AS vendedor
                  FROM ventas v
                  JOIN clientes c ON c.id = v.cliente_id
                  JOIN usuarios u ON u.id = v.vendedor_id
                  {filtro}
                 ORDER BY v.requiere_revision DESC, v.fecha_servidor DESC
                 LIMIT 200
                """  # noqa: S608 — `filtro` es una constante del código, no entrada
            )
        )
    ).mappings().all()

    return render(
        peticion,
        "ventas.html",
        {
            "filas": filas,
            "solo_revision": bool(solo_revision),
            "explicacion": EXPLICACION_MOTIVOS,
            "puede_editar": actor.puede("ventas.cancelar"),
        },
        actor=actor,
        seccion="Ventas",
    )
