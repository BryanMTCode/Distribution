"""El andamio que comparten las pantallas del panel.

Existe para que `panel.py`, `productos.py` y `clientes.py` no se importen entre
sí: la navegación y el `render` los necesitan las tres, y ponerlos en cualquiera
de ellas obligaría a las otras dos a importarla.

────────────────────────────────────────────────────────────────────────────
LEER NÚMEROS QUE ESCRIBIÓ UNA PERSONA
────────────────────────────────────────────────────────────────────────────
Aquí viven los lectores de campos numéricos, y no son un detalle: un precio mal
leído es dinero mal cobrado en cinco camiones.

Las tres reglas que siguen:

1. **Nunca `float`.** `float("296.10")` ya no vale 296.10, y de ahí al centavo
   perdido hay un paso. Todo pasa por `Decimal` construido **desde el texto**.
2. **Se acepta lo que la gente escribe.** `$1,296.50` es lo que sale de copiar
   una celda de Excel. Se limpian el signo de pesos, las comas y los espacios
   antes de convertir; lo que no se limpia es un error que hay que decir.
3. **Un error se explica en español y con el valor que llegó.** "Cantidad
   inválida" no le dice a nadie qué corregir.
"""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.sesion_web import token_csrf
from app.core.db import obtener_sesion

plantillas = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# La navegación se declara una vez: una pantalla que no está aquí no existe para
# quien usa el panel, por más que su ruta responda.
#
# ─────────────────────────────────────────────────────────────────────────────
# CINCO MÓDULOS, NO DIECIOCHO ENLACES (octubre 2026)
# ─────────────────────────────────────────────────────────────────────────────
# El menú creció una pantalla a la vez hasta tener dieciocho enlaces en dos
# renglones, y la dirección lo describió con precisión: abrumador para la
# operación diaria. Cada pantalla tenía su razón; juntas, no se encontraba
# ninguna.
#
# Se agrupan por QUIÉN las usa y CUÁNDO, que es la pregunta con la que alguien
# llega al panel:
#
#   Hoy               lo primero que se abre en la mañana
#   Operación de rutas el día de los camiones, de la carga al corte
#   Catálogos         lo que el teléfono descarga: clientes y productos
#   Almacén           la bodega: lo que entra, lo que sale, lo que hay
#   Administración    lo que se revisa por semana, no por hora
#
# «Liquidación» se llama ahora «Corte del día»: es lo que la oficina dice en voz
# alta, y «liquidación» en México también suena a despido. La URL no cambia
# (/panel/liquidaciones) para no romper enlaces guardados.
NAVEGACION: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Hoy",
        [
            ("/panel", "Tablero"),
            ("/panel/desempeno", "Desempeño"),
            # La lista de «¿qué falta para operar?». Se queda después del arranque:
            # dar de alta un vendedor nuevo es arrancar otra vez, en chiquito.
            ("/panel/arranque", "Arranque"),
        ],
    ),
    (
        "Operación de rutas",
        [
            ("/panel/plan-visita", "Plan de visita"),
            ("/panel/cargas", "Cargas"),
            ("/panel/ventas", "Ventas"),
            ("/panel/cobranza", "Cobranza"),
            ("/panel/liquidaciones", "Corte del día"),
            # Junto al Corte: es donde terminan sus faltantes.
            ("/panel/vendedores/cuenta", "Cuenta de vendedores"),
        ],
    ),
    (
        "Catálogos",
        [
            ("/panel/clientes", "Clientes"),
            ("/panel/productos", "Productos"),
        ],
    ),
    (
        "Almacén",
        [
            # En el orden en que ocurren: sin una entrada, el inventario solo
            # puede mostrar ceros.
            ("/panel/inventario", "Inventario"),
            ("/panel/entradas", "Entradas"),
            ("/panel/salidas", "Salidas"),
            ("/panel/compras", "Compras"),
        ],
    ),
    (
        "Administración",
        [
            ("/panel/efectividad", "Efectividad"),
            ("/panel/objetivos", "Objetivos"),
            ("/panel/equipo", "Usuarios y rutas"),
            ("/panel/equipos", "Teléfonos"),
            ("/panel/cuarentena", "Cuarentena"),
            # Al final: el piloto es temporal por naturaleza —dos semanas— y la
            # pantalla misma explica qué hacer cuando no hay uno activo.
            ("/panel/piloto", "Piloto"),
        ],
    ),
]


# El permiso que pide cada pantalla para abrirse; `None` si basta con entrar al
# panel. El menú solo muestra lo que la persona puede abrir: un enlace que
# responde 403 es un callejón sin salida, y en la primera semana de operación
# cada callejón es una llamada a la oficina.
#
# Tiene que decir lo mismo que el `actor.exigir(...)` de cada pantalla. No se
# confía en que alguien se acuerde: `test_panel_arranque.py` entra con cada rol
# y revisa que nada de lo que ve responda 403 y que lo que no ve, sí. Una pantalla
# nueva en el menú sin renglón aquí truena al dibujar cualquier página.
PERMISO_DEL_MENU: dict[str, str | None] = {
    "/panel": None,
    "/panel/desempeno": "tablero.ver",
    "/panel/arranque": None,
    "/panel/plan-visita": "clientes.ver",
    "/panel/cargas": "inventario.ver",
    "/panel/ventas": None,
    "/panel/cobranza": "cobranza.ver",
    "/panel/liquidaciones": "inventario.ver",
    "/panel/vendedores/cuenta": "vendedores.cuenta_ver",
    "/panel/clientes": "clientes.ver",
    "/panel/productos": "catalogo.ver",
    "/panel/inventario": "inventario.ver",
    "/panel/entradas": "inventario.ver",
    "/panel/salidas": "inventario.ver",
    "/panel/compras": "inventario.ver",
    "/panel/efectividad": "ventas.ver_todas",
    "/panel/objetivos": "tablero.ver",
    "/panel/equipo": None,
    "/panel/equipos": "inventario.ver",
    "/panel/cuarentena": None,
    "/panel/piloto": "piloto.administrar",
}


def permiso_de_la_pantalla(enlace: str) -> str | None:
    """El permiso de la pantalla a la que lleva un enlace, aunque traiga
    subruta, `?filtro=` o `#ancla`: manda la entrada del menú más larga que
    le quede de prefijo."""
    ruta = enlace.split("?", 1)[0].split("#", 1)[0].rstrip("/") or "/panel"
    candidatas = [
        r for r in PERMISO_DEL_MENU if ruta == r or ruta.startswith(r + "/")
    ]
    return PERMISO_DEL_MENU[max(candidatas, key=len)] if candidatas else None


def menu_para(actor) -> list[tuple[str, list[tuple[str, str]]]]:
    """El menú de esta persona: sin las pantallas que no puede abrir, y sin los
    módulos que se quedan vacíos."""
    if actor is None:
        return []
    menu = []
    for titulo, enlaces in NAVEGACION:
        visibles = [
            (ruta, etiqueta)
            for ruta, etiqueta in enlaces
            if (permiso := PERMISO_DEL_MENU[ruta]) is None or actor.puede(permiso)
        ]
        if visibles:
            menu.append((titulo, visibles))
    return menu


def modulo_de(seccion: str) -> str | None:
    """El módulo al que pertenece una pantalla, para abrirlo en el lateral."""
    for titulo, enlaces in NAVEGACION:
        if any(etiqueta == seccion for _, etiqueta in enlaces):
            return titulo
    return None


SesionDep = Annotated[AsyncSession, Depends(obtener_sesion)]

# Cuatro decimales para precios, dos para dinero. La razón está en ADR 0002: una
# caja de 24 a $296.00 da $12.3333 la pieza, y a dos decimales las 24 piezas
# sumarían $295.92. El precio guarda cuatro; el importe redondea a dos una sola
# vez, al final.
DIEZMILESIMA = Decimal("0.0001")
CENTAVO = Decimal("0.01")


class CapturaInvalida(ValueError):
    """Lo que escribió la persona no se puede usar. El mensaje es para ella."""


def _limpiar(texto: str) -> str:
    return texto.strip().replace("$", "").replace(",", "").replace(" ", "")


def _a_decimal(texto: str | None, campo: str) -> Decimal | None:
    crudo = _limpiar(texto or "")
    if not crudo:
        return None
    try:
        valor = Decimal(crudo)
    except InvalidOperation as e:
        raise CapturaInvalida(f"{campo} no es un número: «{texto}».") from e
    if not valor.is_finite():
        raise CapturaInvalida(f"{campo} no es un número: «{texto}».")
    return valor


def leer_precio(texto: str | None, *, campo: str = "El precio") -> Decimal:
    """Un precio de lista, a cuatro decimales.

    Acepta `296`, `296.00`, `$1,296.50` y `12.3333`. Rechaza el vacío: un precio
    en blanco no es cero, es un precio que falta capturar, y guardarlo como cero
    haría que el vendedor regalara el producto.
    """
    valor = _a_decimal(texto, campo)
    if valor is None:
        raise CapturaInvalida(f"{campo} viene vacío.")
    if valor < 0:
        raise CapturaInvalida(f"{campo} no puede ser negativo.")
    if valor == 0:
        # Un precio en cero se vería idéntico a "regalado" en el ticket del
        # cliente. Si algún día hay producto gratis será una promoción, no un
        # precio de lista.
        raise CapturaInvalida(f"{campo} no puede ser cero.")
    if valor >= Decimal("1000000"):
        raise CapturaInvalida(f"{campo} parece un error de dedo: {valor}.")
    return valor.quantize(DIEZMILESIMA, rounding=ROUND_HALF_UP)


def leer_dinero(texto: str | None, *, campo: str = "El monto") -> Decimal:
    """Un monto de dinero, a dos decimales. El vacío vale cero.

    A diferencia de un precio, un límite de crédito en blanco **sí** significa
    cero: es el valor con el que nace todo cliente.
    """
    valor = _a_decimal(texto, campo)
    if valor is None:
        return Decimal("0.00")
    if valor < 0:
        raise CapturaInvalida(f"{campo} no puede ser negativo.")
    if valor >= Decimal("100000000"):
        raise CapturaInvalida(f"{campo} parece un error de dedo: {valor}.")
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def leer_factor(texto: str | None) -> Decimal:
    """Cuántas unidades base trae una presentación: el 24 de "caja de 24".

    Se guarda con cuatro decimales porque la columna los tiene, pero en este
    negocio siempre es entero: no hay producto a granel (ADR 0002), así que no
    existe la media caja.
    """
    valor = _a_decimal(texto, "El número de piezas")
    if valor is None:
        raise CapturaInvalida("Falta cuántas piezas trae la presentación.")
    if valor <= 0:
        raise CapturaInvalida("La presentación tiene que traer al menos una pieza.")
    if valor != valor.to_integral_value():
        raise CapturaInvalida(
            f"Una presentación trae piezas completas, no {valor}. "
            "Ningún producto se vende a granel."
        )
    if valor > 10000:
        raise CapturaInvalida(f"{valor} piezas por presentación parece un error de dedo.")
    return valor.quantize(DIEZMILESIMA)


def leer_entero(texto: str | None, *, campo: str, maximo: int) -> int:
    valor = _a_decimal(texto, campo)
    if valor is None:
        return 0
    if valor != valor.to_integral_value():
        raise CapturaInvalida(f"{campo} tiene que ser un número entero, no {valor}.")
    if valor < 0:
        raise CapturaInvalida(f"{campo} no puede ser negativo.")
    if valor > maximo:
        raise CapturaInvalida(f"{campo} no puede pasar de {maximo}.")
    return int(valor)


def texto_o_nulo(valor: str | None, *, maximo: int = 200) -> str | None:
    """Una cadena vacía en un formulario es "no capturado", no la cadena vacía.

    Guardar `''` en vez de `NULL` rompe los `IS NULL` de las consultas y hace que
    un campo sin capturar se vea como capturado con nada.
    """
    limpio = (valor or "").strip()
    if not limpio:
        return None
    return limpio[:maximo]


async def borrar_si_nadie_lo_usa(sesion: AsyncSession, sql: str, parametros: dict) -> bool:
    """Intenta el DELETE en un punto de guardado. `False` si algo lo referencia.

    ───────────────────────────────────────────────────────────────────────
    POR QUÉ ASÍ, Y NO PREGUNTANDO TABLA POR TABLA
    ───────────────────────────────────────────────────────────────────────
    «¿Tiene historia?» es la pregunta que decide entre borrar y dar de baja (ADR
    0002 §52). Contestarla con una lista de tablas a revisar se desactualiza en
    silencio: la siguiente migración agrega una tabla que apunta a `usuarios`, la
    lista no se entera, y el DELETE revienta con un error de llave foránea frente
    a quien usa el panel. Aquí contesta la base, que conoce TODAS sus llaves
    foráneas: si el DELETE pasa, nadie lo usaba; si no, se deshace solo el punto
    de guardado y quien llama da de baja.

    Las llaves con `ON DELETE CASCADE` (sesiones, alcance de rutas, objetivos) se
    van con el renglón: son del renglón, no historia.
    """
    try:
        async with sesion.begin_nested():
            await sesion.execute(text(sql), parametros)
        return True
    except IntegrityError:
        return False


async def auditar(
    sesion: AsyncSession,
    *,
    entidad: str,
    entidad_id,
    accion: str,
    quien,
    antes: dict | None = None,
    despues: dict | None = None,
    motivo: str | None = None,
) -> None:
    """Deja constancia de una edición o un borrado hecho desde el panel."""
    await sesion.execute(
        text(
            """
            INSERT INTO auditoria (entidad, entidad_id, accion, usuario_id,
                                   datos_antes, datos_despues, motivo, ocurrido_en)
            VALUES (:entidad, :id, :accion, :quien, CAST(:antes AS jsonb),
                    CAST(:despues AS jsonb), :motivo, now())
            """
        ),
        {
            "entidad": entidad,
            "id": entidad_id,
            "accion": accion,
            "quien": quien,
            "antes": json.dumps(antes, default=str) if antes is not None else None,
            "despues": json.dumps(despues, default=str) if despues is not None else None,
            "motivo": motivo,
        },
    )


def dinero(valor) -> str:
    """Para mostrar. El dinero se calcula en `Decimal` y se formatea al final."""
    return f"${Decimal(valor or 0):,.2f}"


def precio_corto(valor) -> str:
    """`296.0000` → `296.00`, y `12.3333` se queda como está.

    El precio guarda cuatro decimales porque los necesita para que la caja y la
    pieza cuadren, pero imprimir `$296.0000` en una tabla hace que quien la lee
    dude de si le falta algo. Se muestran los decimales que aportan, con un mínimo
    de dos: los centavos siempre se ven.
    """
    entero, _, fraccion = f"{Decimal(str(valor)):.4f}".partition(".")
    fraccion = fraccion.rstrip("0").ljust(2, "0")
    return f"{entero}.{fraccion}"


def sin_decimales(valor) -> str:
    """El factor o la cantidad, sin decimales: `24.0000` → `24`.

    **Y por esto no se usa `Decimal.normalize()`,** que es lo que uno escribe
    primero: `Decimal("240.000").normalize()` da `2.4E+2`. Quitar los ceros de la
    derecha de un entero le sube el exponente, así que la pantalla le habría dicho
    al almacenista «1E+1 CAJA = 2.4E+2 PZA». Lo encontró una prueba que afirmaba
    el texto que ve la persona, no el número guardado.
    """
    return f"{Decimal(str(valor)).to_integral_value():,}"


plantillas.env.filters["precio"] = precio_corto
plantillas.env.filters["entero"] = sin_decimales


def render(
    peticion: Request,
    plantilla: str,
    contexto: dict,
    *,
    actor=None,
    seccion: str = "",
) -> HTMLResponse:
    return plantillas.TemplateResponse(
        peticion,
        plantilla,
        {
            **contexto,
            "actor": actor,
            "seccion": seccion,
            "navegacion": menu_para(actor),
            "modulo_activo": modulo_de(seccion),
            "csrf": token_csrf(peticion),
        },
    )
