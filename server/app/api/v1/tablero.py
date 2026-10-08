"""Tablero de Gerencia: lo que el teléfono del gerente pide (Fase 7).

────────────────────────────────────────────────────────────────────────────
UN SOLO VIAJE, SEIS BLOQUES
────────────────────────────────────────────────────────────────────────────
`GET /v1/tablero` devuelve el tablero completo en una respuesta. Seis peticiones
—una por tarjeta— serían seis viajes sobre la conexión de un teléfono, y además
seis marcas de antigüedad distintas por accidente del orden en que llegaron las
respuestas. El tablero es una foto: tiene que ser una sola foto.

────────────────────────────────────────────────────────────────────────────
TODO SALE DE LOS MODELOS DE LECTURA
────────────────────────────────────────────────────────────────────────────
Ninguna consulta de aquí agrega sobre `ventas`. La única que toca tablas
transaccionales es la del mapa, y es un listado acotado de un día (ver
`SQL_MAPA_DEL_DIA`). La razón está en el encabezado de la migración 0021: si el
tablero barriera las tablas de operación, tres gerentes con la pantalla abierta
harían que la SINCRONIZACIÓN de los camiones fuera lenta. El vendedor esperando
en la calle por una pantalla de oficina es exactamente al revés de lo que
importa.

────────────────────────────────────────────────────────────────────────────
LA ANTIGÜEDAD NO ES ADORNO
────────────────────────────────────────────────────────────────────────────
Cada bloque trae su `calculado_en` y el tablero trae su `frescura`. En un DSD
las cifras del día son un PISO: un camión sin señal desde las 10 tiene ventas
reales que no están aquí (§0.3). Decir "venta de hoy: $42,180" sin decir "hace 2
min, con 2 equipos sin sincronizar" es dar por total lo que es un mínimo.
"""

from __future__ import annotations

import calendar
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import text

from app.api.admin.transferencias import cuantas_por_confirmar
from app.api.deps import ActorDep, SesionDep
from app.api.esquemas import Cantidad, Dinero
from app.domain.tablero import (
    MAXIMO_PUNTOS_MAPA,
    SQL_AVANCE_POR_RUTA,
    SQL_DIA_POR_VENDEDOR,
    SQL_ESTADO_DEL_MUNDO,
    SQL_MAPA_DEL_DIA,
    SQL_REFERENCIA_DEL_DIA,
    SQL_REFERENCIA_POR_VENDEDOR,
    SQL_RESUMEN_DIA,
    SQL_SINCRONIA_POR_VENDEDOR,
    SQL_VENTA_MES_SIN_RUTA,
    Avance,
    Frescura,
    Referencia,
    dias_de_referencia,
    inicio_de_mes,
    porcentaje,
)
from app.workers.tablero import asegurar_fresco

router = APIRouter(prefix="/tablero", tags=["tablero"])

PERMISO = "tablero.ver"


# ---------------------------------------------------------------------------
# Esquemas
# ---------------------------------------------------------------------------
class FrescuraSalida(BaseModel):
    """De cuándo son las cifras y qué falta para que estén completas."""

    calculado_en: datetime | None
    minutos: int | None
    confiable: bool
    equipos_sin_sincronizar: int
    cola_reportada: int
    ops_en_cuarentena: int
    advertencia: str | None


class ReferenciaSalida(BaseModel):
    """Contra qué se compara el día, y qué tan en serio tomarlo.

    El promedio es de los MISMOS DÍAS DE LA SEMANA anteriores, no de los días
    anteriores: la ruta visita a los mismos clientes cada martes, así que comparar
    el martes contra el lunes mide qué clientes tocaban y no cómo se trabajó.

    `suficiente` en `false` significa que no hay historia bastante y que la app
    **no debe dibujar una flecha**: un promedio de un solo día es una anécdota, y
    poner una flecha roja sobre eso es inventarle un argumento a alguien.
    """

    promedio: Dinero
    dias: int
    suficiente: bool
    variacion: Decimal | None
    # 'arriba' | 'parejo' | 'abajo' | 'sin_referencia' | 'incompleta'.
    #
    # 'incompleta' es que a la cifra de hoy se SABE que le falta información —el
    # teléfono de esa persona, o de alguna para el total, no ha enviado nada—. La
    # variación sigue viniendo, pero es la de un piso: la app no la pinta de rojo.
    # Una app anterior que no conozca la palabra la lee como 'sin_referencia' y no
    # dibuja flecha, que es la degradación correcta.
    lectura: str


class VentaDelDia(BaseModel):
    fecha: date
    total: Dinero
    # Todo es de contado (ADR 0002 §81): el dinero del día por forma de pago. El
    # efectivo se entrega en el corte; la transferencia se confirma en el banco.
    efectivo: Dinero
    transferencia: Dinero
    documentos: int
    # El ticket promedio sale de los documentos, no de las visitas: es la cifra
    # que el gerente compara con la de ayer. El drop size "de verdad" —por
    # visita que vendió— vive en el bloque de visitas, con ese nombre.
    ticket_promedio: Dinero
    referencia: ReferenciaSalida
    calculado_en: datetime | None


class VisitasDelDia(BaseModel):
    visitas: int
    con_venta: int
    no_drops: int
    # Los no-drops que la empresa puede arreglar: 'operacion', 'producto',
    # 'vendedor'. Es la cifra accionable del bloque.
    no_drops_nuestros: int
    efectividad: Decimal
    drop_size: Dinero
    calculado_en: datetime | None


class TransferenciasPorConfirmar(BaseModel):
    """Lo que espera al banco AHORA, de cualquier día: un pendiente, no un flujo."""

    cuantas: int
    importe: Dinero


class MermasDelDia(BaseModel):
    documentos: int
    unidades: Cantidad
    calculado_en: datetime | None


class RenglonVendedor(BaseModel):
    vendedor_id: uuid.UUID
    codigo: str | None
    nombre: str
    venta: Dinero
    documentos: int
    visitas: int
    con_venta: int
    no_drops: int
    # Lo vendido en efectivo: lo que entrega en el corte.
    efectivo: Dinero
    efectividad: Decimal
    # Su propio mismo día de la semana. Mismo trato que el total: sin historia
    # bastante, no hay flecha.
    referencia: ReferenciaSalida
    # Lo que convierte el renglón en una llamada telefónica.
    #
    # El cero de un vendedor cuyo día entero está en su teléfono NO es un cero (§0.3),
    # y antes de esto la app decía «sin movimiento todavía hoy» en los dos casos — que
    # en el segundo es falso: no es que no haya vendido, es que no sabemos.
    ultimo_push: datetime | None
    cola_reportada: int
    sin_sincronizar: bool


class RenglonRuta(BaseModel):
    ruta_id: uuid.UUID
    codigo: str
    nombre: str
    venta_mes: Dinero
    objetivo: Dinero | None
    logrado: Decimal | None
    esperado: Decimal
    diferencia: Decimal | None
    semaforo: str
    visitas_mes: int
    dias_con_venta: int
    clientes_distintos: int


class AvanceDelMes(BaseModel):
    periodo: date
    dia_del_mes: int
    dias_del_mes: int
    rutas: list[RenglonRuta]
    # Lo que no se pudo atribuir a ninguna ruta. Se muestra porque es la
    # diferencia entre el total del mes y la suma de las barras; callarlo haría
    # que las cifras no cuadraran sin explicación.
    venta_sin_ruta: Dinero
    documentos_sin_ruta: int


class Tablero(BaseModel):
    frescura: FrescuraSalida
    venta: VentaDelDia
    visitas: VisitasDelDia
    por_confirmar: TransferenciasPorConfirmar
    mermas: MermasDelDia
    vendedores: list[RenglonVendedor]
    avance: AvanceDelMes


class PuntoDelMapa(BaseModel):
    clase: str          # 'venta' | 'no_drop'
    lat: Decimal
    lng: Decimal
    cliente: str
    vendedor: str | None
    importe: Dinero | None
    motivo: str | None
    momento: datetime


class MapaDelDia(BaseModel):
    fecha: date
    puntos: list[PuntoDelMapa]
    # Cuántos se dejaron fuera por el tope. Un mapa recortado en silencio haría
    # que el gerente contara visitas sobre el dibujo y le faltaran.
    recortados: bool
    calculado_en: datetime | None


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------
def _minutos(desde: datetime | None) -> int | None:
    if desde is None:
        return None
    if desde.tzinfo is None:
        desde = desde.replace(tzinfo=UTC)
    return max(0, int((datetime.now(UTC) - desde).total_seconds() // 60))


def _frescura(calculado_en: datetime | None, mundo) -> FrescuraSalida:
    """Arma la frescura. Un tablero nunca calculado NO es un tablero fresco.

    `calculado_en is None` significa que el worker no ha corrido ni una vez. Se
    devuelve `confiable=False` con su advertencia en vez de `minutos=0`, que es
    el error de leer "recién calculado" cuando lo correcto es "no hay cifras".
    """
    minutos = _minutos(calculado_en)
    frescura = Frescura(
        minutos=minutos if minutos is not None else 10**6,
        equipos_sin_sincronizar=mundo["equipos_sin_sincronizar"] or 0,
        cola_reportada=int(mundo["cola_reportada"] or 0),
        ops_en_cuarentena=mundo["ops_en_cuarentena"] or 0,
    )
    if calculado_en is None:
        return FrescuraSalida(
            calculado_en=None,
            minutos=None,
            confiable=False,
            equipos_sin_sincronizar=frescura.equipos_sin_sincronizar,
            cola_reportada=frescura.cola_reportada,
            ops_en_cuarentena=frescura.ops_en_cuarentena,
            advertencia=(
                "El tablero no se ha calculado todavía: no hay cifras que mostrar. "
                "Si esto no cambia en unos minutos, el worker no está corriendo."
            ),
        )
    return FrescuraSalida(
        calculado_en=calculado_en,
        minutos=minutos,
        confiable=frescura.confiable,
        equipos_sin_sincronizar=frescura.equipos_sin_sincronizar,
        cola_reportada=frescura.cola_reportada,
        ops_en_cuarentena=frescura.ops_en_cuarentena,
        advertencia=frescura.advertencia,
    )


def _fecha_pedida(fecha: date | None) -> date:
    hoy = date.today()
    if fecha is None:
        return hoy
    if fecha > hoy:
        # Un día que no ha pasado no tiene cifras. Devolver ceros haría que el
        # tablero dijera "no se vendió nada" de un día que todavía no ocurrió.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "esa fecha no ha ocurrido")
    return fecha


# ---------------------------------------------------------------------------
# El tablero
# ---------------------------------------------------------------------------
@router.get("", response_model=Tablero)
async def ver_tablero(
    actor: ActorDep,
    sesion: SesionDep,
    fecha: Annotated[
        date | None, Query(description="Día operativo; por omisión hoy")
    ] = None,
) -> Tablero:
    actor.exigir(PERMISO)
    # Si el worker no ha calculado el tablero, se calcula ahora (ver la función).
    await asegurar_fresco()
    dia = _fecha_pedida(fecha)
    periodo = inicio_de_mes(dia)

    resumen = (
        await sesion.execute(text(SQL_RESUMEN_DIA), {"fecha": dia})
    ).mappings().one()
    por_confirmar = await cuantas_por_confirmar(sesion)
    refresco = (
        await sesion.execute(text("SELECT * FROM tablero_refrescos WHERE id"))
    ).mappings().first()
    mundo = (await sesion.execute(text(SQL_ESTADO_DEL_MUNDO))).mappings().one()

    # La antigüedad del tablero es la del REFRESCO, no la del renglón del día:
    # si el worker lleva una hora caído, el renglón de hoy sigue diciendo
    # "calculado hace una hora" y es cierto, pero lo que el gerente necesita
    # saber es que el tablero completo está viejo.
    calculado_en = refresco["calculado_en"] if refresco else None

    por_vendedor = (
        await sesion.execute(text(SQL_DIA_POR_VENDEDOR), {"fecha": dia})
    ).mappings().all()
    # La referencia y el estado de los teléfonos: tres consultas cortas sobre
    # `tablero_dia` y `dispositivos`, en el mismo viaje. El tablero es una foto.
    referencias = dias_de_referencia(dia)
    ref_dia = (
        await sesion.execute(text(SQL_REFERENCIA_DEL_DIA), {"dias": referencias})
    ).mappings().one()
    ref_vendedor = {
        f["vendedor_id"]: f
        for f in (
            await sesion.execute(
                text(SQL_REFERENCIA_POR_VENDEDOR), {"dias": referencias}
            )
        ).mappings().all()
    }
    sincronia = {
        f["usuario_id"]: f
        for f in (
            await sesion.execute(text(SQL_SINCRONIA_POR_VENDEDOR))
        ).mappings().all()
    }
    rutas = (
        await sesion.execute(text(SQL_AVANCE_POR_RUTA), {"periodo": periodo})
    ).mappings().all()
    sin_ruta = (
        await sesion.execute(text(SQL_VENTA_MES_SIN_RUTA), {"periodo": periodo})
    ).mappings().one()

    documentos = int(resumen["documentos_venta"])
    visitas = int(resumen["visitas"])
    con_venta = int(resumen["visitas_con_venta"])
    venta_total = Decimal(resumen["venta_total"])

    # El esperado se prorratea contra HOY, no contra el día pedido.
    #
    # `tablero_mes_ruta` guarda el mes **a la fecha**: si hoy es 20 y alguien
    # abre el tablero del día 5, `venta_mes` sigue trayendo los 20 días. Usar el
    # día 5 como numerador del prorrateo compararía veinte días de venta contra
    # cinco días de objetivo y toda ruta se vería adelantadísima.
    #
    # Un mes ya terminado se prorratea al 100%: su avance es el definitivo.
    hoy = date.today()
    dias_del_mes = calendar.monthrange(periodo.year, periodo.month)[1]
    dia_del_mes = hoy.day if periodo == inicio_de_mes(hoy) else dias_del_mes

    vendedores = [
        _renglon_vendedor(
            f,
            ref_vendedor.get(f["vendedor_id"]),
            sincronia.get(f["vendedor_id"]),
            dia=dia,
            es_hoy=dia == hoy,
        )
        for f in por_vendedor
    ]

    return Tablero(
        frescura=_frescura(calculado_en, mundo),
        venta=VentaDelDia(
            fecha=dia,
            total=venta_total,
            efectivo=Decimal(resumen["venta_efectivo"]),
            transferencia=Decimal(resumen["venta_transferencia"]),
            documentos=documentos,
            ticket_promedio=(
                (venta_total / documentos).quantize(Decimal("0.01"))
                if documentos
                else Decimal("0.00")
            ),
            # Un piso si a alguien le falta el día: leerlo como «abajo» pintaría
            # una caída que puede no existir.
            referencia=_referencia(
                Decimal(ref_dia["venta_promedio"]),
                int(ref_dia["dias"]),
                venta_total,
                incompleta=any(v.sin_sincronizar for v in vendedores),
            ),
            calculado_en=resumen["calculado_en"],
        ),
        visitas=VisitasDelDia(
            visitas=visitas,
            con_venta=con_venta,
            no_drops=int(resumen["no_drops"]),
            no_drops_nuestros=int(resumen["no_drops_nuestros"]),
            efectividad=porcentaje(con_venta, visitas),
            # Drop size: lo que deja una visita en la que SÍ se vendió. El
            # denominador son las visitas con venta, igual que en el
            # laboratorio; si fuera el total de visitas sería otra métrica
            # ("importe por visita") y las dos cifras no coincidirían.
            drop_size=(
                (venta_total / con_venta).quantize(Decimal("0.01"))
                if con_venta
                else Decimal("0.00")
            ),
            calculado_en=resumen["calculado_en"],
        ),
        por_confirmar=TransferenciasPorConfirmar(
            cuantas=int(por_confirmar["cuantas"]),
            importe=Decimal(por_confirmar["importe"]),
        ),
        mermas=MermasDelDia(
            documentos=int(resumen["mermas_documentos"]),
            unidades=Decimal(resumen["mermas_unidades"]),
            calculado_en=resumen["calculado_en"],
        ),
        vendedores=vendedores,
        avance=AvanceDelMes(
            periodo=periodo,
            dia_del_mes=dia_del_mes,
            dias_del_mes=dias_del_mes,
            rutas=[_renglon_ruta(f, dia_del_mes, dias_del_mes) for f in rutas],
            venta_sin_ruta=Decimal(sin_ruta["venta"]),
            documentos_sin_ruta=int(sin_ruta["documentos"]),
        ),
    )


def _referencia(
    promedio: Decimal, dias: int, hoy: Decimal, *, incompleta: bool = False
) -> ReferenciaSalida:
    """Arma la salida desde el dataclass del dominio, que es quien decide.

    La aritmética vive en `Referencia` y no aquí a propósito: el panel web usa el
    mismo objeto, y el día que el margen de «parejo» cambie tiene que cambiar en
    los dos lados o el gerente vería dos verdades según la pantalla que abriera.
    """
    referencia = Referencia(promedio=promedio, dias=dias)
    return ReferenciaSalida(
        promedio=referencia.promedio,
        dias=referencia.dias,
        suficiente=referencia.suficiente,
        variacion=referencia.variacion(hoy),
        lectura=referencia.lectura(hoy, incompleta=incompleta),
    )


def _renglon_vendedor(fila, ref, equipo, *, dia: date, es_hoy: bool) -> RenglonVendedor:
    """El renglón de una persona, con su referencia y el estado de su teléfono.

    `sin_sincronizar` solo puede ser verdad de HOY: para un día cerrado, «no ha
    hecho push hoy» no dice nada de lo que pasó ese martes, y pintarlo de rojo
    sugeriría que las cifras de ese día están incompletas por una razón que no es.
    """
    venta = Decimal(fila["venta_total"])
    ultimo_push = equipo["ultimo_push"] if equipo else None
    sin_sincronizar = es_hoy and (ultimo_push is None or ultimo_push.date() < dia)
    return RenglonVendedor(
        vendedor_id=fila["vendedor_id"],
        codigo=fila["codigo"],
        nombre=fila["nombre"],
        venta=venta,
        documentos=int(fila["documentos_venta"]),
        visitas=int(fila["visitas"]),
        con_venta=int(fila["visitas_con_venta"]),
        no_drops=int(fila["no_drops"]),
        efectivo=Decimal(fila["venta_efectivo"]),
        efectividad=porcentaje(fila["visitas_con_venta"], fila["visitas"]),
        referencia=_referencia(
            Decimal(ref["venta_promedio"]) if ref else Decimal("0"),
            int(ref["dias"]) if ref else 0,
            venta,
            incompleta=sin_sincronizar,
        ),
        ultimo_push=ultimo_push,
        cola_reportada=int(equipo["cola_reportada"]) if equipo else 0,
        sin_sincronizar=sin_sincronizar,
    )


def _renglon_ruta(fila, dia_del_mes: int, dias_del_mes: int) -> RenglonRuta:
    avance = Avance(
        venta=Decimal(fila["venta_mes"]),
        objetivo=Decimal(fila["objetivo_venta"]) if fila["objetivo_venta"] is not None else None,
        dia_del_mes=dia_del_mes,
        dias_del_mes=dias_del_mes,
    )
    return RenglonRuta(
        ruta_id=fila["ruta_id"],
        codigo=fila["codigo"],
        nombre=fila["nombre"],
        venta_mes=avance.venta,
        objetivo=avance.objetivo,
        logrado=avance.logrado,
        esperado=avance.esperado,
        diferencia=avance.diferencia,
        semaforo=avance.semaforo,
        visitas_mes=int(fila["visitas_mes"]),
        dias_con_venta=int(fila["dias_con_venta"]),
        clientes_distintos=int(fila["clientes_distintos"]),
    )


# ---------------------------------------------------------------------------
# El mapa
# ---------------------------------------------------------------------------
@router.get("/mapa", response_model=MapaDelDia)
async def ver_mapa(
    actor: ActorDep,
    sesion: SesionDep,
    fecha: Annotated[date | None, Query()] = None,
) -> MapaDelDia:
    """Las visitas del día con coordenadas: ventas y no-drops.

    Va aparte del tablero porque son cientos de renglones y la mayoría de las
    aperturas del tablero no abren el mapa. Pedirlo siempre costaría la
    transferencia del mapa en cada *pull to refresh*.

    Una venta sin coordenadas no aparece —el filtro es `lat IS NOT NULL`— y eso
    es información: significa que el GPS no respondió. Los no-drops, en cambio,
    tienen `lat/lng` NOT NULL por diseño (un no-drop sin coordenadas es
    indistinguible de una visita que no ocurrió), así que ahí no hay huecos.
    """
    actor.exigir(PERMISO)
    dia = _fecha_pedida(fecha)

    # Se pide uno más que el tope para saber si hubo recorte sin un COUNT extra.
    filas = (
        await sesion.execute(
            text(SQL_MAPA_DEL_DIA), {"fecha": dia, "limite": MAXIMO_PUNTOS_MAPA + 1}
        )
    ).mappings().all()
    recortados = len(filas) > MAXIMO_PUNTOS_MAPA
    refresco = (
        await sesion.execute(text("SELECT calculado_en FROM tablero_refrescos WHERE id"))
    ).mappings().first()

    return MapaDelDia(
        fecha=dia,
        puntos=[
            PuntoDelMapa(
                clase=f["clase"],
                lat=f["lat"],
                lng=f["lng"],
                cliente=f["cliente"],
                vendedor=f["vendedor"],
                importe=Decimal(f["importe"]) if f["importe"] is not None else None,
                motivo=f["motivo"],
                momento=f["momento"],
            )
            for f in filas[:MAXIMO_PUNTOS_MAPA]
        ],
        recortados=recortados,
        calculado_en=refresco["calculado_en"] if refresco else None,
    )


# ---------------------------------------------------------------------------
# Por periodo: lo mismo que el tablero del panel
# ---------------------------------------------------------------------------
# Pedido en operación (octubre 2026): «que el gerente pueda ver en la app los
# datos por días y periodos, y lo de un vendedor en particular: lo mismo que en el
# dashboard». Las cifras salen de `cifras_del_periodo`, la MISMA función del
# tablero del panel, y el periodo de `leer_periodo`: «esta semana» empieza el
# mismo lunes en los dos.


class PeriodoDelTablero(BaseModel):
    clave: str
    etiqueta: str
    desde: date
    hasta: date
    descripcion: str


class CifrasDelPeriodo(BaseModel):
    efectivo: Dinero
    transferencias: Dinero
    total: Dinero
    canceladas: int
    mermas: int
    devoluciones: int
    no_ventas: int
    clientes_atendidos: int
    clientes_nuevos: int


class VendedorDelPeriodo(BaseModel):
    id: uuid.UUID
    codigo: str
    nombre: str
    ventas: int
    importe: Dinero
    efectivo: Dinero
    transferencias: Dinero
    mermas: int
    no_ventas: int


class DiaDelPeriodo(BaseModel):
    fecha: date
    ventas: int
    importe: Dinero
    efectivo: Dinero


class TableroDelPeriodo(BaseModel):
    periodo: PeriodoDelTablero
    periodos: list[tuple[str, str]]
    cifras: CifrasDelPeriodo
    por_vendedor: list[VendedorDelPeriodo]
    # Vacío en un periodo de un solo día, o de más de dos meses (como el panel).
    por_dia: list[DiaDelPeriodo]


@router.get("/periodo", response_model=TableroDelPeriodo)
async def ver_periodo(
    actor: ActorDep,
    sesion: SesionDep,
    periodo: str = "",
    desde: str = "",
    hasta: str = "",
) -> TableroDelPeriodo:
    from app.api.admin.panel import cifras_del_periodo
    from app.api.admin.periodo import PERIODOS, leer_periodo

    actor.exigir(PERMISO)
    rango = leer_periodo(periodo, desde, hasta)
    datos = await cifras_del_periodo(sesion, rango)
    cifras = datos["cifras"]
    return TableroDelPeriodo(
        periodo=PeriodoDelTablero(
            clave=rango.clave, etiqueta=rango.etiqueta, desde=rango.inicio,
            hasta=rango.fin, descripcion=rango.descripcion,
        ),
        periodos=list(PERIODOS),
        cifras=CifrasDelPeriodo(
            **cifras,
            total=Decimal(cifras["efectivo"] or 0) + Decimal(cifras["transferencias"] or 0),
        ),
        por_vendedor=[VendedorDelPeriodo(**v) for v in datos["por_vendedor"]],
        por_dia=[
            DiaDelPeriodo(**{k: d[k] for k in DiaDelPeriodo.model_fields})
            for d in datos["por_dia"]
        ],
    )


# ---------------------------------------------------------------------------
# La empresa: el tamaño del negocio
# ---------------------------------------------------------------------------
class ResumenDeLaEmpresa(BaseModel):
    clientes_activos: int
    prospectos: int
    clientes_inactivos: int
    clientes_nuevos_mes: int
    vendedores: int
    vendedores_con_camion: int
    usuarios_oficina: int
    rutas: int
    telefonos: int
    productos: int
    productos_sin_precio: int
    bodegas: int
    camiones: int
    piezas_en_bodegas: Cantidad
    piezas_en_camiones: Cantidad
    existencias_negativas: int
    vendido_mes: Dinero
    vendido_anio: Dinero


@router.get("/empresa", response_model=ResumenDeLaEmpresa)
async def ver_empresa(actor: ActorDep, sesion: SesionDep) -> ResumenDeLaEmpresa:
    """Cuántos clientes, vendedores, artículos… La misma consulta que la pantalla
    Empresa del panel (`app/api/admin/empresa.py`)."""
    from app.api.admin.empresa import resumen_de_la_empresa

    actor.exigir(PERMISO)
    return ResumenDeLaEmpresa(**await resumen_de_la_empresa(sesion))
