"""Las pantallas del dashboard dentro de la app de la oficina (ADR 0002 §194).

«Solo quiero poder mover el almacén, agregar productos, ese tipo de cosas» desde
la app, «sin tener que actualizar la app a los vendedores». La pestaña Almacén
de la app muestra unos botones; cada uno abre esa pantalla del dashboard dentro
de la app, con la sesión que la app ya tiene.

Los dos lados los decide el servidor, para que cambiar el dashboard no pida otra
APK:

  · **Qué botones** salen y a qué pantalla lleva cada uno: `ATAJOS`, aquí. Uno
    nuevo, uno menos o uno que lleve a otra parte es un cambio en el servidor.
  · **Quién** los ve: `QUIEN_LOS_VE`. Hoy, el administrador.

Con su token, la app pide un boleto de un solo uso; con él, `/panel/entrar/app`
abre la sesión del dashboard sin pedir la contraseña otra vez (ver
`app/infra/boletos_del_panel.py`). Son endpoints nuevos: nada de lo que usa el
teléfono del vendedor cambia.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.admin.comun import permiso_de_la_pantalla, puede_todos
from app.api.deps import Actor, ActorDep, SesionDep
from app.infra import boletos_del_panel

router = APIRouter(prefix="/panel", tags=["panel"])

# Quién abre el dashboard desde la app. Hoy, solo el administrador; darle a otro
# rol es cambiar esta línea, sin APK.
QUIEN_LOS_VE = frozenset({"admin"})


class Atajo(BaseModel):
    titulo: str
    descripcion: str
    # La pantalla del dashboard: una ruta de /panel.
    ruta: str
    # Un nombre que la app convierte en su ícono; uno que no conoce, sale con el
    # de siempre.
    icono: str


# En el orden en que se ven. Cada uno sale solo si quien lo pide puede abrir esa
# pantalla en el dashboard.
ATAJOS: tuple[Atajo, ...] = (
    Atajo(titulo="Mover mercancía", icono="mover", ruta="/panel/inventario/mover",
          descripcion="De la bodega a un camión, de regreso, o de un camión a otro"),
    Atajo(titulo="Entradas", icono="entrada", ruta="/panel/entradas",
          descripcion="Lo que llega a la bodega: compras y lo que se encontró de más"),
    Atajo(titulo="Salidas y mermas", icono="salida", ruta="/panel/salidas",
          descripcion="Lo que sale de la bodega sin venderse"),
    # §196, §197. La APK +60 no conoce este ícono y lo pinta con el de abrir.
    Atajo(titulo="Producto malo", icono="malo", ruta="/panel/inventario/producto-malo",
          descripcion="Lo malo que traen los vendedores: se les cambia por bueno"),
    Atajo(titulo="Ajustar inventario", icono="ajustar", ruta="/panel/inventario/ajustar",
          descripcion="Contar y dejar cada artículo en lo que hay"),
    Atajo(titulo="Artículos", icono="articulos", ruta="/panel/productos",
          descripcion="Dar de alta productos, sus presentaciones y sus precios"),
    Atajo(titulo="Lo que se movió", icono="movimientos", ruta="/panel/inventario/movimientos",
          descripcion="De cuánto estaba a cuánto pasó cada artículo"),
)


def _lo_ve(actor: Actor) -> bool:
    return actor.rol in QUIEN_LOS_VE


@router.get("/atajos", response_model=list[Atajo])
async def atajos(actor: ActorDep) -> list[Atajo]:
    """Los botones que la app pone en Almacén. Vacío para quien no los ve: la
    app entonces no muestra nada."""
    if not _lo_ve(actor):
        return []
    return [a for a in ATAJOS if puede_todos(actor, permiso_de_la_pantalla(a.ruta))]


class Boleto(BaseModel):
    boleto: str
    expira_en: datetime
    # Dónde se canjea: por POST dentro de la app, por GET en el navegador.
    entrar: str = "/panel/entrar/app"


@router.post("/boleto", response_model=Boleto)
async def pedir_boleto(actor: ActorDep, sesion: SesionDep) -> Boleto:
    if not _lo_ve(actor):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "El dashboard desde la app es del administrador; en la computadora "
            "entra con tu código y contraseña.",
        )
    boleto, expira = await boletos_del_panel.emitir(sesion, actor.usuario_id)
    await sesion.commit()
    return Boleto(boleto=boleto, expira_en=expira)
