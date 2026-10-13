"""Los vendedores, desde el teléfono de la oficina (`/v1/vendedores`).

────────────────────────────────────────────────────────────────────────────
POR QUÉ EXISTE
────────────────────────────────────────────────────────────────────────────
Pedido en operación (octubre 2026): «quiero ver en la app el almacén de cada
vendedor, sus detalles, sus ventas y todo eso». Es la pantalla Vendedores del
panel, para quien está en la calle o en la bodega con el teléfono.

────────────────────────────────────────────────────────────────────────────
LAS MISMAS CONSULTAS QUE EL PANEL
────────────────────────────────────────────────────────────────────────────
La lista, la ficha y la línea de tiempo se importan de
`app/api/admin/vendedores.py`; el periodo, de `periodo.py`. Así la app y el
dashboard no pueden decir dos cosas distintas del mismo vendedor.

────────────────────────────────────────────────────────────────────────────
QUIÉN
────────────────────────────────────────────────────────────────────────────
`ventas.ver_todas`: admin, gerente y supervisor. El vendedor no lo tiene: la
venta de los demás y la cuenta de cada uno no son suyas. Eso incluye el camión
de otro, aunque el vendedor tenga `inventario.ver` para el suyo.

El camión que se muestra es lo que el SERVIDOR sabe: lo que el vendedor vendió
sin señal todavía no está restado. La respuesta trae la hora del último
contacto del teléfono para que la pantalla lo diga.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text

from app.api.admin.periodo import PERIODOS, Periodo, periodo_pedido
from app.api.admin.vendedores import (
    LIMITE,
    PERMISO_VER,
    TIPOS,
    ficha_del_vendedor,
    lista_de_vendedores,
    movimientos_del_vendedor,
    telefonos_del_vendedor,
    vendedores_desactivados,
)
from app.api.deps import ActorDep, SesionDep
from app.api.esquemas import Cantidad, Dinero

router = APIRouter(prefix="/vendedores", tags=["vendedores"])


# ---------------------------------------------------------------------------
# Esquemas
# ---------------------------------------------------------------------------
class PeriodoVisto(BaseModel):
    clave: str
    etiqueta: str
    desde: date
    hasta: date
    descripcion: str

    @classmethod
    def de(cls, p: Periodo) -> PeriodoVisto:
        return cls(
            clave=p.clave, etiqueta=p.etiqueta, desde=p.inicio, hasta=p.fin,
            descripcion=p.descripcion,
        )


class VendedorEnLista(BaseModel):
    id: uuid.UUID
    codigo: str
    nombre: str
    activo: bool
    camion: str | None
    rutas: str | None
    ventas: int
    importe: Dinero
    # Lo vendido en efectivo: lo que entrega en el corte.
    efectivo: Dinero
    no_ventas: int
    mermas: int
    ultimo_contacto: datetime | None
    saldo_cuenta: Dinero


class ListaDeVendedores(BaseModel):
    periodo: PeriodoVisto
    periodos: list[tuple[str, str]]
    vendedores: list[VendedorEnLista]
    # Cuántos vendedores desactivados hay. No vienen en `vendedores`: se piden
    # aparte con `?desactivados=true` (ADR 0002 §93).
    desactivados: int = 0


class Ficha(BaseModel):
    id: uuid.UUID
    codigo: str
    nombre: str
    activo: bool
    camion: str | None
    rutas: str | None
    saldo_cuenta: Dinero


class ResumenDeTipo(BaseModel):
    tipo: str
    etiqueta: str
    cuantos: int
    importe: Dinero


class Movimiento(BaseModel):
    momento: datetime | None
    fecha: date | None
    tipo: str
    etiqueta: str
    folio: str | None
    cliente: str | None
    detalle: str | None
    importe: Dinero | None
    estado: str | None
    marca: bool | None
    # El id del documento. La app lo usa para abrir el detalle de una venta.
    ref: str | None


class Telefono(BaseModel):
    etiqueta: str
    estado: str
    ultima_sync_push_en: datetime | None
    ultima_sync_pull_en: datetime | None
    cola_pendiente: int | None


class DetalleDeVendedor(BaseModel):
    vendedor: Ficha
    periodo: PeriodoVisto
    periodos: list[tuple[str, str]]
    resumen: list[ResumenDeTipo]
    movimientos: list[Movimiento]
    recortado: bool
    limite: int
    telefonos: list[Telefono]


class Existencia(BaseModel):
    producto_id: uuid.UUID
    sku: str
    nombre: str
    unidad_base: str
    cantidad: Cantidad
    # La familia del artículo (§89), para agrupar «Su camión» (§114); nula si no
    # tiene. Vienen en el orden de la oficina, «Sin familia» al final.
    familia: str | None = None


class CamionDelVendedor(BaseModel):
    vendedor: str
    camion: str | None
    existencias: list[Existencia]
    piezas: Cantidad
    # Cuándo habló el teléfono por última vez: lo que vendió después de eso, sin
    # señal, todavía no está restado aquí.
    ultimo_contacto: datetime | None


class Partida(BaseModel):
    linea: int
    sku: str
    nombre: str
    unidad: str
    cantidad: Cantidad
    precio_unitario: Dinero
    importe: Dinero


class VentaVista(BaseModel):
    id: uuid.UUID
    folio: str | None
    fecha_operativa: date
    momento: datetime | None
    vendedor: str
    cliente: str
    tipo: str
    # 'efectivo' | 'transferencia'; nula solo en las ventas a crédito del piloto.
    forma_pago: str | None = None
    # 'confirmado' | 'por_confirmar' | 'rechazado' (la transferencia que no llegó).
    pago_estado: str | None = None
    estado: str
    total: Dinero
    partidas: list[Partida]


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
@router.get("", response_model=ListaDeVendedores)
async def lista(
    actor: ActorDep,
    sesion: SesionDep,
    periodo: str = "",
    desde: str = "",
    hasta: str = "",
    desactivados: bool = False,
) -> ListaDeVendedores:
    """Los vendedores activos; con `desactivados=true`, solo los desactivados."""
    actor.exigir(PERMISO_VER)
    rango = await periodo_pedido(sesion, periodo, desde, hasta)
    filas = await lista_de_vendedores(sesion, rango, activos=not desactivados)
    return ListaDeVendedores(
        periodo=PeriodoVisto.de(rango),
        periodos=list(PERIODOS),
        vendedores=[VendedorEnLista(**dict(f)) for f in filas],
        desactivados=await vendedores_desactivados(sesion),
    )


# Va antes de `/{vendedor_id}` para que «ventas» nunca se lea como un id.
@router.get("/ventas/{venta_id}", response_model=VentaVista)
async def venta(venta_id: uuid.UUID, actor: ActorDep, sesion: SesionDep) -> VentaVista:
    """Una venta con lo que se vendió, renglón por renglón."""
    actor.exigir(PERMISO_VER)
    cabecera = (
        await sesion.execute(
            text(
                "SELECT v.id, v.folio_local AS folio, v.fecha_operativa, "
                "       v.fecha_dispositivo AS momento, v.tipo, v.forma_pago, "
                "       v.pago_estado, v.estado, v.total, "
                "       u.nombre AS vendedor, c.nombre_comercial AS cliente "
                "  FROM ventas v "
                "  JOIN usuarios u ON u.id = v.vendedor_id "
                "  JOIN clientes c ON c.id = v.cliente_id "
                " WHERE v.id = :v"
            ),
            {"v": venta_id},
        )
    ).mappings().first()
    if cabecera is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "esa venta no existe")
    partidas = (
        await sesion.execute(
            text(
                """
                SELECT vp.linea, p.sku, p.nombre, vp.unidad_codigo AS unidad,
                       vp.cantidad, vp.precio_unitario, vp.importe
                  FROM venta_partidas vp
                  JOIN productos p ON p.id = vp.producto_id
                 WHERE vp.venta_id = :v
                 ORDER BY vp.linea
                """
            ),
            {"v": venta_id},
        )
    ).mappings().all()
    return VentaVista(**dict(cabecera), partidas=[Partida(**dict(p)) for p in partidas])


@router.get("/{vendedor_id}", response_model=DetalleDeVendedor)
async def detalle(
    vendedor_id: uuid.UUID,
    actor: ActorDep,
    sesion: SesionDep,
    periodo: str = "",
    desde: str = "",
    hasta: str = "",
    tipo: str = "",
) -> DetalleDeVendedor:
    """Su ficha y TODO lo que hizo en el periodo, en orden de hora."""
    actor.exigir(PERMISO_VER)
    rango = await periodo_pedido(sesion, periodo, desde, hasta)
    etiquetas = dict(TIPOS)
    if tipo not in etiquetas:
        tipo = ""

    ficha = await ficha_del_vendedor(sesion, vendedor_id)
    if ficha is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ese vendedor no existe")
    resumen, filas, recortado = await movimientos_del_vendedor(sesion, ficha, rango, tipo)
    telefonos = await telefonos_del_vendedor(sesion, vendedor_id)

    return DetalleDeVendedor(
        vendedor=Ficha(**{k: ficha[k] for k in Ficha.model_fields}),
        periodo=PeriodoVisto.de(rango),
        periodos=list(PERIODOS),
        # En el orden del día, como en el panel.
        resumen=[
            ResumenDeTipo(
                tipo=clave,
                etiqueta=etiqueta,
                cuantos=resumen[clave]["cuantos"],
                importe=resumen[clave]["importe"] or Decimal(0),
            )
            for clave, etiqueta in TIPOS
            if clave in resumen
        ],
        movimientos=[
            Movimiento(
                **{k: m[k] for k in Movimiento.model_fields if k != "etiqueta"},
                etiqueta=etiquetas.get(m["tipo"], m["tipo"]),
            )
            for m in filas
        ],
        recortado=recortado,
        limite=LIMITE,
        telefonos=[Telefono(**{k: t[k] for k in Telefono.model_fields}) for t in telefonos],
    )


@router.get("/{vendedor_id}/camion", response_model=CamionDelVendedor)
async def camion(vendedor_id: uuid.UUID, actor: ActorDep, sesion: SesionDep) -> CamionDelVendedor:
    """Lo que trae su camión, según el servidor.

    Con los renglones en negativo incluidos: un negativo es la señal de que algo
    se vendió sin estar cargado, y esconderlo sería esconder el problema.
    """
    actor.exigir(PERMISO_VER)
    ficha = await ficha_del_vendedor(sesion, vendedor_id)
    if ficha is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ese vendedor no existe")

    existencias = []
    if ficha["almacen_id"] is not None:
        existencias = (
            await sesion.execute(
                text(
                    """
                    SELECT e.producto_id, p.sku, p.nombre, p.unidad_base, e.cantidad,
                           cat.nombre AS familia
                      FROM existencias e
                      JOIN productos p ON p.id = e.producto_id
                      LEFT JOIN categorias cat ON cat.id = p.categoria_id
                     WHERE e.almacen_id = :a AND e.cantidad <> 0
                     ORDER BY (cat.id IS NULL), cat.orden, cat.nombre, p.nombre
                    """
                ),
                {"a": ficha["almacen_id"]},
            )
        ).mappings().all()
    ultimo = (
        await sesion.execute(
            text(
                "SELECT max(GREATEST(ultima_sync_push_en, ultima_sync_pull_en)) "
                "  FROM dispositivos WHERE usuario_id = :v"
            ),
            {"v": vendedor_id},
        )
    ).scalar_one_or_none()

    return CamionDelVendedor(
        vendedor=ficha["nombre"],
        camion=ficha["camion"],
        existencias=[Existencia(**dict(e)) for e in existencias],
        piezas=sum((Decimal(e["cantidad"]) for e in existencias), Decimal(0)),
        ultimo_contacto=ultimo,
    )


# ---------------------------------------------------------------------------
# Su ruta de cada día (ADR 0002 §121)
# ---------------------------------------------------------------------------
# «Quiero poder ver las rutas de cada vendedor de cada día en la app del
# administrador.» Las tiendas que le tocan cada día según el plan de visita
# (migración 0041), en el orden de su hoja, con las que todavía no tienen ubicación
# a la vista —las capturan los vendedores (§120)—. Y si el día es hoy, cuáles ya
# visitó: con venta, sin venta, o todavía no.

# El orden de la semana como se trabaja, con el número de la base:
# `extract(dow)`, donde 0 es domingo.
DIAS_DE_RUTA: tuple[tuple[int, str], ...] = (
    (1, "Lun"), (2, "Mar"), (3, "Mié"), (4, "Jue"), (5, "Vie"), (6, "Sáb"), (0, "Dom"),
)


class DiaDeRuta(BaseModel):
    dia: int
    etiqueta: str
    tiendas: int


class TiendaDeRuta(BaseModel):
    id: uuid.UUID
    codigo: str | None
    nombre: str
    direccion: str | None
    ruta: str
    orden: int | None
    con_ubicacion: bool
    # Las semanas del mes en que toca; vacía es todas.
    semanas: list[int] = []
    # Hoy le toca porque se le reprogramó la visita (migración 0054), no por su plan.
    reprogramada: bool = False
    # Solo si el día es hoy: 'venta', 'no_venta' o nula (todavía no la visita).
    visita: str | None = None
    importe: Dinero | None = None


class RutaDelVendedor(BaseModel):
    vendedor: str
    rutas: list[str]
    dia: int
    fecha: date
    es_hoy: bool
    dias: list[DiaDeRuta]
    tiendas: list[TiendaDeRuta]
    sin_ubicacion: int


SQL_SUS_TIENDAS = """
WITH sus_rutas AS (
    SELECT r.id, r.codigo FROM rutas r
     WHERE r.activo
       AND (r.vendedor_id = :v
            OR EXISTS (SELECT 1 FROM usuarios_rutas ur
                        WHERE ur.ruta_id = r.id AND ur.usuario_id = :v))
)
SELECT c.id, c.codigo, c.nombre_comercial AS nombre, c.calle, c.numero, c.colonia,
       sr.codigo AS ruta, c.secuencia AS orden, c.visita_reprogramada,
       (c.ubicacion IS NOT NULL OR (c.lat IS NOT NULL AND c.lng IS NOT NULL))
         AS con_ubicacion,
       COALESCE(array_agg(f.dia_semana) FILTER (WHERE f.id IS NOT NULL), '{}') AS dias,
       COALESCE(array_agg(f.semana_del_mes) FILTER (WHERE f.dia_semana = :dia), '{}')
         AS semanas,
       bool_or(f.dia_semana = :dia AND f.semana_del_mes IS NULL) AS todas_las_semanas
  FROM clientes c
  JOIN sus_rutas sr ON sr.id = c.ruta_id
  LEFT JOIN clientes_frecuencia f ON f.cliente_id = c.id
 WHERE c.estatus IN ('activo', 'prospecto') AND NOT c.mayoreo
 GROUP BY c.id, sr.codigo
"""


@router.get("/{vendedor_id}/ruta", response_model=RutaDelVendedor)
async def ruta(
    vendedor_id: uuid.UUID,
    actor: ActorDep,
    sesion: SesionDep,
    dia: int | None = None,
    fecha: date | None = None,
) -> RutaDelVendedor:
    """Las tiendas que le tocan un día de la semana, en el orden de su ruta.

    `dia` es 0–6 como `extract(dow)` (1 = lunes); sin él, el de `fecha`. `fecha`
    es el día operativo de quien pregunta (hoy): si el día pedido es el de hoy,
    cada tienda dice si ya se visitó.
    """
    actor.exigir(PERMISO_VER)
    ficha = await ficha_del_vendedor(sesion, vendedor_id)
    if ficha is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ese vendedor no existe")
    hoy = fecha or date.today()
    dia_de_hoy = hoy.isoweekday() % 7
    if dia is None:
        dia = dia_de_hoy
    if not 0 <= dia <= 6:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "el día va de 0 a 6")
    es_hoy = dia == dia_de_hoy

    filas = (
        await sesion.execute(text(SQL_SUS_TIENDAS), {"v": vendedor_id, "dia": dia})
    ).mappings().all()

    visitas: dict[uuid.UUID, tuple[str, Decimal | None]] = {}
    if es_hoy:
        for f in (
            await sesion.execute(
                text(
                    "SELECT cliente_id, 'no_venta' AS visita, NULL::numeric AS importe "
                    "  FROM no_drops WHERE vendedor_id = :v AND fecha_operativa = :hoy "
                    "UNION ALL "
                    "SELECT cliente_id, 'venta', sum(total) FROM ventas "
                    " WHERE vendedor_id = :v AND fecha_operativa = :hoy "
                    "   AND estado = 'confirmada' GROUP BY cliente_id"
                ),
                {"v": vendedor_id, "hoy": hoy},
            )
        ).mappings().all():
            # La venta manda sobre la visita sin venta del mismo cliente.
            if f["visita"] == "venta" or f["cliente_id"] not in visitas:
                visitas[f["cliente_id"]] = (f["visita"], f["importe"])

    cuenta = {d: 0 for d, _ in DIAS_DE_RUTA}
    tiendas: list[TiendaDeRuta] = []
    for f in filas:
        for d in set(f["dias"]):
            cuenta[d] += 1
        reprogramada = es_hoy and f["visita_reprogramada"] == hoy
        if dia not in f["dias"] and not reprogramada:
            continue
        visita, importe = visitas.get(f["id"], (None, None))
        direccion = " ".join(x for x in (f["calle"], f["numero"]) if x)
        if f["colonia"]:
            direccion = f"{direccion}, {f['colonia']}" if direccion else f["colonia"]
        tiendas.append(TiendaDeRuta(
            id=f["id"],
            codigo=f["codigo"],
            nombre=f["nombre"],
            direccion=direccion or None,
            ruta=f["ruta"],
            orden=f["orden"],
            con_ubicacion=f["con_ubicacion"],
            semanas=[] if f["todas_las_semanas"] or dia not in f["dias"]
            else sorted(s for s in f["semanas"] if s is not None),
            reprogramada=reprogramada and dia not in f["dias"],
            visita=visita,
            importe=importe,
        ))
    # Las reprogramadas para hoy no son de su recorrido: van al final.
    tiendas.sort(
        key=lambda t: (t.reprogramada, t.ruta, t.orden is None, t.orden or 0, t.nombre)
    )

    rutas = list(
        (
            await sesion.execute(
                text(
                    "SELECT r.codigo FROM rutas r WHERE r.activo "
                    "   AND (r.vendedor_id = :v OR EXISTS (SELECT 1 FROM usuarios_rutas ur "
                    "        WHERE ur.ruta_id = r.id AND ur.usuario_id = :v)) "
                    " ORDER BY r.codigo"
                ),
                {"v": vendedor_id},
            )
        ).scalars()
    )
    return RutaDelVendedor(
        vendedor=ficha["nombre"],
        rutas=rutas,
        dia=dia,
        fecha=hoy,
        es_hoy=es_hoy,
        # De lunes a sábado siempre; el domingo, solo si alguien lo trabaja.
        dias=[
            DiaDeRuta(dia=d, etiqueta=e, tiendas=cuenta[d])
            for d, e in DIAS_DE_RUTA
            if d != 0 or cuenta[0] or dia == 0
        ],
        tiendas=tiendas,
        sin_ubicacion=sum(1 for t in tiendas if not t.con_ubicacion),
    )
