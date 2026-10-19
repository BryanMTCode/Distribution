"""La revisión de la app y del dashboard, punto por punto (ADR 0002 §112).

«Agrega al dashboard un checklist de todo lo que debo checar en la app para ver
que las funcionalidades están bien.» Y después: dashboard y app juntos.

Dos partes: lo que se prueba EN EL TELÉFONO y lo que se prueba EN EL DASHBOARD.
Las marcas de las dos van con la versión de la app que se revisa: es la que
cambia en cada entrega. Cuando solo se actualizó el servidor, cada parte se
puede volver a empezar.

La lista vive aquí, en código, y no en la base: cada punto describe una
pantalla que existe en esta versión, y cambia junto con ella. Lo que se guarda
en la base (`revision_app`, 0058) son las marcas: por versión de la app, qué
salió bien y qué falló, con su nota, quién y cuándo. Una versión nueva empieza
en blanco, y junto a cada punto se ve cómo quedó en la versión anterior.

La clave de cada punto es estable: cambiarla deja huérfanas sus marcas. Un
punto nuevo o que cambió lleva en `desde` el versionCode («+35») para que
aparezca como «nuevo» y se revise primero.

Cada punto dice CÓMO probarlo con los nombres de los botones que se ven en la
pantalla, y QUÉ debe pasar —incluido lo que debe aparecer en el panel—. Si un
botón cambia de nombre, este texto se cambia en el mismo commit.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text

ESTADOS = ("bien", "falla")


class MarcaInvalida(Exception):
    """La marca no se guardó, y el mensaje dice por qué."""


@dataclass(frozen=True)
class Punto:
    clave: str
    titulo: str
    pasos: str
    esperado: str
    # El versionCode en que llegó o cambió («+35»); nulo si es de antes.
    desde: str | None = None


@dataclass(frozen=True)
class Seccion:
    clave: str
    titulo: str
    # Con qué teléfono —o en dónde— y con qué usuario se prueba.
    quien: str
    puntos: tuple[Punto, ...] = field(default_factory=tuple)
    # 'telefono' o 'dashboard'.
    parte: str = "telefono"


PARTES = {"telefono": "En el teléfono", "dashboard": "En el dashboard"}


SECCIONES: tuple[Seccion, ...] = (
    Seccion(
        "entrar", "Instalar y entrar", "Cualquier teléfono",
        (
            Punto(
                "instalar_encima", "Se instala encima sin perder nada",
                "Instala el APK nuevo ENCIMA del que ya está, sin desinstalar, y abre la app.",
                "Abre sin pedir vincular otra vez. Las tiendas, el camión y lo que faltaba "
                "por subir siguen ahí.",
            ),
            Punto(
                "version_al_pie", "La versión correcta al pie",
                "En la pantalla de entrada, mira la línea de abajo.",
                "Dice la versión que acabas de instalar (la de esta revisión).",
            ),
            Punto(
                "vincular_clave", "Vincular un teléfono con la clave corta",
                "Solo si tienes un teléfono para probar: «Vincular este equipo» con el "
                "código del vendedor, su contraseña y la clave del equipo (por ejemplo "
                "RUTA4) dada de alta en el panel, en Teléfonos.",
                "Entra y baja sus tiendas, los artículos y lo que trae su camión.",
            ),
            Punto(
                "sesion_recordada", "La sesión se recuerda",
                "Entra con la contraseña, cierra la app por completo (quítala de las "
                "recientes) y vuelve a abrirla.",
                "Entra sola, sin pedir la contraseña, durante doce horas.",
            ),
            Punto(
                "entrar_gerencia", "Entrar como Gerencia",
                "En la pantalla de entrada, «Entrar como Gerencia» con tu código y "
                "contraseña, con señal.",
                "Abre el portal de la oficina con la barra de abajo: Tablero, Empresa, "
                "Vendedores, Clientes y Almacén (según tus permisos).",
            ),
        ),
    ),
    Seccion(
        "ruta", "Vendedor: la ruta y las tiendas", "Teléfono de un vendedor",
        (
            Punto(
                "hoy_y_las_demas", "«Hoy» y las demás tiendas de la ruta",
                "Abre la lista en «Hoy».",
                "Arriba, las tiendas que tocan hoy según el plan. Abajo, «Las demás de tu "
                "ruta · N», agrupadas por día, que se pliegan y despliegan con un toque.",
                desde="+34",
            ),
            Punto(
                "ruta_por_dia", "Mi ruta, agrupada por día",
                "Toca «Todos»; luego toca el encabezado de un día.",
                "Cada día con su encabezado («Lunes · 45»), el de hoy marcado «hoy», y sus "
                "tiendas en el orden del recorrido; la que toca dos días sale en los dos. "
                "Tocar el encabezado pliega o abre ese día.",
                desde="+40",
            ),
            Punto(
                "buscar_tienda", "Buscar una tienda",
                "Escribe parte del nombre o del código en el buscador de arriba.",
                "Encuentra la tienda aunque hoy no le toque.",
            ),
            Punto(
                "ubicacion_desde_lista", "La ubicación desde la lista",
                "Toca el botón de ubicación a la derecha de una tienda (en rojo, las que "
                "no tienen).",
                "Abre su perfil con el GPS y los campos de latitud y longitud, sin entrar "
                "a la visita. Lo que guardes se ve en el panel, en la ficha del cliente.",
                desde="+34",
            ),
            Punto(
                "alta_con_dias", "Dar de alta una tienda con sus días de visita",
                "«Nuevo»: nombre, datos y los días de la semana en que se visita. Guarda "
                "y sincroniza.",
                "Aparece en la lista, y en «Hoy» los días que escogiste. En el panel sale "
                "en Clientes como prospecto, con sus días en el Plan de visita.",
                desde="+33",
            ),
            Punto(
                "alta_coordenadas_a_mano", "Coordenadas escritas a mano en el alta",
                "En el alta, «Escribir las coordenadas»: pega «23.2494, -106.4111». Prueba "
                "también al revés (-106.4111, 23.2494).",
                "Las bien escritas quedan como «Puesta a mano»; las de al revés las dice "
                "y no las acepta.",
                desde="+34",
            ),
            Punto(
                "perfil_gps", "Perfil y ubicación con GPS",
                "Dentro de una tienda, menú ⋮ → «Perfil y ubicación»: lee el GPS o "
                "corrige a mano, y guarda.",
                "Queda guardado y, al sincronizar, el panel muestra la ubicación nueva.",
            ),
            Punto(
                "guia_flecha", "«Guíame»: la flecha hacia la tienda, sin señal",
                "Pon el teléfono en modo avión y deja prendida la ubicación. En la lista, "
                "toca la flechita junto a una tienda con ubicación («Cómo llegar»). "
                "Primero quieto; luego camina o ve en el camión hacia ella.",
                "Quieto: a cuánto está («350 m»), hacia dónde («al noreste») y la flecha "
                "con la «N» arriba. En marcha: «de frente», «a tu derecha»… y la flecha "
                "gira contigo. A unos metros de la puerta dice «Ya llegaste». Todo sin "
                "datos.",
                desde="+42",
            ),
            Punto(
                "abrir_en_mapas", "«Abrir en Mapas»: la ruta por calles",
                "En «Cómo llegar» (o en el perfil de la tienda), toca «Abrir en Mapas». "
                "Antes, con WiFi, descarga una vez el mapa de Mazatlán en Google Maps "
                "(«Mapas sin conexión») u Organic Maps.",
                "Abre la app de mapas del teléfono con la tienda marcada y su nombre, para "
                "pedir la ruta. Con el mapa descargado sirve en modo avión. Si el teléfono "
                "no tiene app de mapas, lo dice y manda a la flecha.",
                desde="+42",
            ),
            Punto(
                "cerca_de_mi", "«Cerca de mí»: las tiendas de la más cercana a la más lejana",
                "En la lista, toca «Cerca de mí» (afuera, sin techo). Vuelve a tocarlo.",
                "La lista se ordena por distancia y cada tienda dice «a 300 m», «a 1.2 km»; "
                "las que no tienen ubicación quedan al final. Otro toque la regresa al "
                "orden de la ruta. Con «Hoy» elegido, ordena solo las de hoy.",
                desde="+42",
            ),
            Punto(
                "mapa_bajar", "Bajar el mapa de Mazatlán al teléfono",
                "Antes, en el servidor: `python -m app.cli mapa-mazatlan` (PUESTA-EN-MARCHA, "
                "anexo del mapa). En el teléfono, con WiFi: lista → «Mapa» → «Bajar el "
                "mapa de Mazatlán».",
                "Una barra con los MB que van y el total; al terminar, «Listo» y las "
                "calles con su nombre, las colonias y el mar. Si se corta el WiFi a la "
                "mitad, lo dice y el botón vuelve a intentar. En ⋮ queda «Volver a bajar "
                "el mapa» con su fecha.",
                desde="+43",
            ),
            Punto(
                "mapa_sin_senal", "El mapa sin señal, con las tiendas y tú",
                "Ya con el mapa bajado, modo avión con la ubicación prendida: lista → "
                "«Mapa». Mueve y acerca el mapa; toca una tienda.",
                "Las calles se ven igual que con WiFi. Las tiendas de hoy en color, las "
                "visitadas en verde, las de otro día en gris, y tú como punto azul. "
                "Tocar una tienda dice a cuánto está, con «Guíame», «Ruta por calles» y "
                "«Perfil».",
                desde="+43",
            ),
            Punto(
                "mapa_desde_la_guia", "«Ver en el mapa» desde la guía",
                "En «Cómo llegar» de una tienda, toca «Ver en el mapa».",
                "El mapa con la tienda marcada en rojo, tú en azul y una línea punteada "
                "entre los dos, encuadrados en la pantalla.",
                desde="+43",
            ),
        ),
    ),
    Seccion(
        "visita", "Vendedor: la visita", "Teléfono de un vendedor con carga",
        (
            Punto(
                "catalogo_familias", "Los artículos por familia",
                "Entra a una tienda.",
                "Los artículos vienen agrupados por familia; cada familia se pliega y se "
                "despliega; el buscador filtra.",
                desde="+33",
            ),
            Punto(
                "precio_y_existencia", "Precio de lista y lo que trae el camión",
                "Mira un artículo y agrégalo varias veces.",
                "Muestra el precio de lista (no se puede cambiar) y cuánto queda en el "
                "camión, que baja mientras agregas; en cero dice agotado y no deja "
                "agregar más.",
            ),
            Punto(
                "venta_efectivo", "Vender en efectivo",
                "Agrega dos o tres artículos, «Ver pedido», revisa el total y «Cobrar en "
                "efectivo».",
                "«Venta registrada» con su folio. El folio sigue al anterior del "
                "teléfono, sin repetirse.",
            ),
            Punto(
                "ticket", "La remisión y el ticket",
                "En la venta registrada: «Imprimir remisión» si hay impresora, o «Ver el "
                "ticket» y compártelo por WhatsApp.",
                "El ticket trae el folio, la tienda, los artículos y el total.",
            ),
            Punto(
                "venta_en_el_panel", "La venta llega al panel",
                "Sincroniza y abre el Tablero del panel.",
                "La venta se suma a las de hoy, con el mismo total, en el vendedor "
                "correcto; en Ventas aparece con su folio.",
            ),
            Punto(
                "no_me_compro", "«No me compró» con su motivo",
                "En una tienda, menú ⋮ → «No me compró»: escoge el motivo y «Registrar "
                "la visita».",
                "Queda registrada; en el panel aparece en Desempeño → Visitas con ese "
                "motivo.",
            ),
            Punto(
                "reprogramar", "Reprogramar la visita («ven mañana»)",
                "En «No me compró», en «¿Te pidió que volvieras otro día?», escoge "
                "«Mañana» (o «Otro día…»).",
                "Ese día la tienda aparece en «Hoy» aunque no sea de sus días; sus días "
                "fijos no cambian.",
                desde="+33",
            ),
            Punto(
                "devolucion_cliente", "La tienda devuelve mercancía",
                "En una tienda, menú ⋮ → «Me devolvió mercancía»: producto, motivo y "
                "cuántas.",
                "Queda registrada y se ve en el panel como devolución de cliente.",
            ),
            Punto(
                "cambio_fisico", "Cambio físico (caducado o dañado, sin cobro)",
                "En una tienda, menú ⋮ → «Cambio físico»: el producto malo y cuántos.",
                "Sin cobro: del camión sale el bueno y el malo queda como merma.",
            ),
            Punto(
                "merma_camion", "Merma del camión",
                "En la lista, menú «Más» → «Registrar merma»: producto, motivo y "
                "cuántas.",
                "Baja la existencia del camión, y la merma aparece en el panel.",
            ),
        ),
    ),
    Seccion(
        "sincronizar", "Vendedor: sincronizar y trabajar sin señal",
        "Teléfono de un vendedor",
        (
            Punto(
                "sincronizar", "Sincronizar",
                "Toca el botón de sincronizar (las flechas de arriba).",
                "La barra de pendientes desaparece. En el panel, Teléfonos → "
                "Sincronizaciones muestra la subida con la versión de la app.",
            ),
            Punto(
                "sin_senal", "Vender sin señal",
                "Pon el modo avión, haz una venta y un «No me compró»; quita el modo avión "
                "y sincroniza.",
                "Sin señal todo funciona y queda como pendiente. Al volver la señal se "
                "sube una sola vez: en el panel no hay ventas repetidas.",
            ),
            Punto(
                "cambios_de_la_oficina", "Lo que cambia la oficina llega al teléfono",
                "En el panel cambia el nombre de una tienda o un precio; en el teléfono, "
                "sincroniza.",
                "El teléfono muestra el cambio.",
            ),
        ),
    ),
    Seccion(
        "cierre", "Vendedor: el camión y el cierre del día", "Teléfono de un vendedor",
        (
            Punto(
                "mi_camion", "Mi camión",
                "Botón del camión (arriba).",
                "Lo que trae, por familia, con lo vendido ya descontado.",
            ),
            Punto(
                "devolver_a_bodega", "Devolver a la bodega",
                "En «Mi camión», el botón de bodega → algunos productos o «Todo el "
                "camión» → «Registrar la devolución».",
                "Sale del camión y queda en tránsito hasta que la bodega lo recibe en el "
                "panel.",
            ),
            Punto(
                "mi_dia", "Mi día y los días anteriores",
                "Botón «Mi día» (arriba).",
                "Las ventas del día con su total; se puede ir a días anteriores y volver "
                "con «Ver hoy».",
            ),
            Punto(
                "impresora_real", "Elegir la impresora y probarla",
                "Prende la impresora y emparéjala en Ajustes del teléfono → Bluetooth "
                "(clave 0000 o 1234). En la app: «Mi ruta» → menú (⋮) → «Impresora» → "
                "«Buscar impresoras» → toca la tuya → «Imprimir una prueba».",
                "Sale un ticket de PRUEBA con la regla de números en una sola línea "
                "empezando por el 1, los acentos legibles y un total. Si se come la "
                "primera letra, «Mover a la derecha» → «+» y otra prueba, hasta ver el 1. "
                "Luego una venta: «Imprimir» saca el ticket completo en papel. Con la "
                "impresora apagada dice que no contestó (ADR 0002 §139 y §141).",
                desde="+46",
            ),
            Punto(
                "corte_vendedor", "Hacer el corte del día",
                "En «Mi día», «Hacer el corte del día»: revisa lo que entregas —ya no se "
                "escribe, la app lo calcula de lo vendido en efectivo— → «Confirmar el "
                "corte» → «Confirmar». En el ticket, «Imprimir» y luego «Enviar por "
                "WhatsApp (o correo…)».",
                "Muestra lo que queda en el camión sin contarlo. «Imprimir» dice si salió "
                "en la impresora; WhatsApp manda el mismo ticket. Al sincronizar, el corte "
                "se cierra solo: en «Mi día» dice «se cierra solo» y en el panel pasa a "
                "«Resueltos» sin que nadie lo cierre (ADR 0002 §133).",
                desde="+44",
            ),
            Punto(
                "solicitar_carga", "Solicitar la carga de mañana",
                "En «Mi día», «Solicitar carga para mañana»: artículos por familia, "
                "cantidades y una nota.",
                "La oficina la ve por aceptar. Es una al día: después, «Mi día» dice en "
                "qué va —pedida, ACEPTADA o rechazada con su motivo—.",
            ),
            Punto(
                "carga_aceptada_llega", "La carga aceptada llega al camión",
                "Después de que la oficina acepte la carga, sincroniza.",
                "«Mi camión» muestra la carga sumada a lo que traía.",
            ),
        ),
    ),
    Seccion(
        "oficina", "Oficina: Tablero, Empresa, Vendedores y Clientes",
        "Entrando como Gerencia o admin",
        (
            Punto(
                "tablero_dia", "Tablero del día",
                "Tablero en «Día»; cambia de día con el calendario.",
                "Las cifras del día (ventas, efectivo, visitas) y de qué día son; las "
                "mismas que el Tablero del panel.",
            ),
            Punto(
                "tablero_periodo", "Tablero por periodo",
                "Cambia a periodo: semana, mes, «Todo» y «Fechas…».",
                "Por vendedor y por día, con los mismos totales que el panel.",
            ),
            Punto(
                "mapa_del_dia", "El mapa del día",
                "En el Tablero, «Ver el mapa del día».",
                "Las tiendas del día sobre el lienzo, con su leyenda.",
            ),
            Punto(
                "barra_por_vista", "La barra de avance por Hoy, Semana o Mes",
                "En el Tablero, en «Avance contra el objetivo», cambia entre «Hoy», "
                "«Semana» y «Mes».",
                "Cada vendedor con lo vendido contra la suma de sus objetivos diarios de "
                "ese periodo, y la marca de lo esperado a la fecha; arriba, toda la "
                "empresa (la suma). Las mismas cifras que «Lo que suma» del dashboard.",
                desde="+37",
            ),
            Punto(
                "objetivos_app", "Objetivos (solo para ver)",
                "En el Tablero, botón de objetivos.",
                "Hoy, esta semana y este mes de toda la empresa y de cada vendedor, con lo "
                "vendido y lo esperado. No hay «Nuevo objetivo» ni «Quitar»: se ponen en "
                "el dashboard.",
                desde="+37",
            ),
            Punto(
                "empresa", "Empresa",
                "Pestaña Empresa; toca cada cifra.",
                "Cada cifra abre su lista (clientes, vendedores, artículos…).",
            ),
            Punto(
                "vendedores", "Vendedores y su camión",
                "Pestaña Vendedores: abre uno; revisa «Movimientos» y «Su camión».",
                "Los desactivados van aparte. Tocar una venta abre su detalle. «Su camión» "
                "trae los productos agrupados por familia, con las piezas de cada una, y "
                "cada familia se pliega con un toque.",
                desde="+36",
            ),
            Punto(
                "ruta_del_vendedor", "Su ruta de cada día",
                "Pestaña Vendedores: abre uno → «Su ruta». Toca otro día (Lun…Sáb).",
                "Abre en el día de hoy, con cuántas tiendas tiene cada día; las tiendas "
                "en el orden de su recorrido, con código y dirección, y las que no tienen "
                "ubicación marcadas. Hoy dice cuáles ya visitó (con venta o sin venta) y "
                "cuántas le faltan. Tocar una tienda abre su ficha.",
                desde="+38",
            ),
            Punto(
                "clientes_oficina", "Clientes",
                "Pestaña Clientes: busca uno y ábrelo.",
                "Su ficha y lo que compra.",
            ),
            Punto(
                "clientes_por_ruta", "Clientes por ruta y día",
                "Pestaña Clientes: toca una ruta; luego un día (Lun…Sáb) y «Todas las "
                "rutas».",
                "Abre con las rutas y cuántas tiendas tiene cada día, no con todas. Al "
                "elegir una ruta salen sus tiendas agrupadas por día, en el orden del "
                "recorrido; con un día, ese día de cada ruta.",
                desde="+39",
            ),
        ),
    ),
    Seccion(
        "almacen", "Oficina: Almacén", "Entrando como Gerencia o admin",
        (
            Punto(
                "existencias", "Existencias",
                "Almacén → Existencias.",
                "Por bodega, con las familias plegables y lo que vale el inventario por "
                "artículo.",
            ),
            Punto(
                "sin_entradas_ni_traspasos", "Sin Entradas ni Traspasos",
                "Abre Almacén y mira las pestañas.",
                "Existencias, Cargas, Corte del día y Mayoreo; Entradas y Traspasos ya no "
                "están en la app: se hacen en el panel (ADR 0002 §134).",
                desde="+44",
            ),
            Punto(
                "cargas_por_fecha", "Cargas por fecha",
                "Almacén → Cargas → «Por fecha».",
                "Las cargas de cada día, con lo que se subió.",
                desde="+33",
            ),
            Punto(
                "aceptar_carga", "Aceptar o rechazar la carga que pidió un vendedor",
                "Almacén → Cargas → «Cargas que pidieron los vendedores» → ábrela → "
                "ajusta lo que haga falta → «Aceptar y confirmar la carga» (o «Rechazar "
                "la solicitud» con su motivo).",
                "La aceptada baja de la bodega y sube al camión; el vendedor la ve al "
                "sincronizar. La rechazada le llega a su «Mi día» con el motivo. Si lo "
                "aceptado no alcanza en la bodega, no deja aceptar y dice cuánto hay: "
                "se baja la cantidad primero.",
                desde="+37",
            ),
            Punto(
                "cargas_por_dia", "Cargas por día, sin repetirse",
                "Almacén → Cargas, después de aceptar una que pidió un vendedor. Mira "
                "«Abiertas» y luego «Por fecha».",
                "Las cargas van agrupadas por día («Viernes 25 de septiembre · 2 carga(s) "
                "· 480 piezas»). La aceptada sale una sola vez, en su día, con «la pidió "
                "el vendedor» y el botón del ticket; ya no se repite en «Resueltas esta "
                "semana», donde solo quedan las rechazadas.",
                desde="+41",
            ),
            Punto(
                "nueva_carga", "Una carga a mano",
                "Almacén → Cargas → «Nueva carga» → «Abrir la carga» → «Agregar» → "
                "«Confirmar».",
                "Sube al camión. Si ese vendedor ya tiene la carga del día, explica por "
                "qué no deja.",
            ),
            Punto(
                "cerrar_corte", "Un corte que no se cerró solo",
                "Almacén → Corte del día → «Cortes de los vendedores». Normalmente está "
                "vacío: los cortes se cierran solos. Si hay uno, dice «No se cerró solo» "
                "y por qué; ábrelo → «Cerrar el corte».",
                "Dice la diferencia contra lo esperado y deja el ticket del corte.",
                desde="+44",
            ),
            Punto(
                "cortes_por_dia", "Cortes por día, sin repetirse",
                "Almacén → Corte del día, después de cerrar el corte de un vendedor. Baja "
                "hasta «Cortes por día».",
                "Cada día con su encabezado y cuántos cortes hubo. El que cerraste sale "
                "una sola vez, en su día, con «Lo mandó el vendedor · Entregó …»; ya no "
                "se repite en «Cerrados esta semana».",
                desde="+41",
            ),
            Punto(
                "mayoreo", "Venta de mayoreo",
                "Almacén → Mayoreo → nueva venta: un cliente de mayoreo, artículos al "
                "precio que decidas. Después, cancela una de prueba.",
                "Sale de la bodega con su folio MY- y su ticket; la cancelada devuelve la "
                "mercancía. En el panel sale en Ventas → Mayoreo.",
                desde="+33",
            ),
        ),
    ),
    Seccion(
        "pantalla", "El teléfono de los vendedores", "Hyundai HYLine Pro",
        (
            Punto(
                "pantalla_angosta", "Nada se corta en el teléfono de la empresa",
                "En el Hyundai HYLine Pro recorre el alta de tienda, «No me compró», "
                "«Solicitar carga» y la lista de clientes.",
                "Ningún texto ni botón se corta, se encima o se sale de la pantalla.",
            ),
        ),
    ),
    # ------------------------------------------------------------------
    # En el dashboard
    # ------------------------------------------------------------------
    Seccion(
        "panel_hoy", "Tablero y desempeño", "La computadora, entrando como admin",
        (
            Punto(
                "panel_entrar", "Entrar al dashboard",
                "Abre el dashboard y entra con tu código y contraseña.",
                "Abre el Tablero; el menú de la izquierda muestra solo lo que tu puesto "
                "puede abrir.",
            ),
            Punto(
                "panel_pendientes", "Los pendientes del Tablero",
                "En el Tablero, mira «Pendientes» y toca uno.",
                "Cada pendiente dice cuántos son y lleva a la pantalla donde se resuelve; "
                "si no hay nada, dice «Nada pendiente».",
            ),
            Punto(
                "panel_cifras_periodo", "Las cifras por periodo",
                "En «Cifras de hoy», cambia a la semana, el mes, «Todo» y unas fechas.",
                "Ventas, efectivo, clientes con compra y visitas sin venta; «Por "
                "vendedor» y «Por día» suman lo mismo que el total.",
            ),
            Punto(
                "panel_igual_que_la_app", "El dashboard y la app dicen lo mismo",
                "Compara el Tablero del dashboard con el Tablero de la app (Gerencia), "
                "hoy y en el mes.",
                "Las mismas cifras en los dos.",
            ),
            Punto(
                "panel_objetivo_de_hoy", "«Objetivo de hoy» en el Tablero",
                "Con un objetivo diario puesto, mira «Objetivo de hoy» en el Tablero.",
                "Cada vendedor con su objetivo, lo vendido hoy, el porcentaje y cuánto "
                "falta; el que hoy descansa dice «Hoy no aplica»; abajo, toda la empresa "
                "como la suma de todos.",
                desde="+37",
            ),
            Punto(
                "panel_desempeno", "Desempeño del día",
                "Desempeño → Del día.",
                "Cada vendedor contra sus mismos días de la semana, y dice de quién falta "
                "información (el teléfono que no ha sincronizado).",
            ),
            Punto(
                "panel_visitas", "Por qué no compraron",
                "Desempeño → Visitas.",
                "Los «No me compró» con su motivo y por vendedor, el cumplimiento del "
                "plan de visita y los cambios físicos.",
            ),
            Punto(
                "panel_objetivo_diario", "El objetivo diario de cada vendedor",
                "Desempeño → Objetivos: a un vendedor, «Poner» $3,500 marcando los días "
                "que trabaja (sin el domingo, digamos). Luego «Cambiar» a 4,000 desde "
                "hoy, programa un cambio a futuro y cancélalo, y al final «Quitar».",
                "Vale hasta que lo cambias; la semana se ve día por día con ✓ y los días "
                "sin marcar dicen «descanso»; abajo, «Toda la empresa» suma a todos; los "
                "días pasados se quedan con el objetivo que tenían.",
                desde="+37",
            ),
            Punto(
                "panel_objetivo_a_todos", "El mismo objetivo a todos los vendedores",
                "En «Poner un objetivo diario» escoge «Todos los vendedores»; luego "
                "cámbiale los días al que no trabaje alguno.",
                "Cada vendedor queda con el mismo objetivo diario, medido por su lado, y "
                "cada uno se puede ajustar después.",
                desde="+36",
            ),
            Punto(
                "panel_lo_que_suma", "Lo que suma: semana, mes y empresa",
                "Desempeño → Objetivos → «Lo que suma»: cambia el periodo a «Esta "
                "semana», «Este mes» y unas fechas.",
                "El objetivo de cada vendedor es la suma de sus días (la semana de quien "
                "trabaja seis días a $3,500 = $21,000; los días de descanso no suman); "
                "«Toda la empresa» es la suma de todos; el mayoreo sale aparte.",
                desde="+37",
            ),
        ),
        parte="dashboard",
    ),
    Seccion(
        "panel_operacion", "Cortes, cargas y ventas", "La computadora, entrando como admin",
        (
            Punto(
                "panel_cerrar_corte", "El corte del vendedor se cierra solo",
                "Que un vendedor confirme su corte en el teléfono y sincronice. Luego "
                "Cortes y cargas → Por aceptar.",
                "Sin que nadie lo cierre, el corte sale en «Resueltos en la última semana» "
                "a nombre del vendedor. Si el teléfono tenía algo sin subir, se queda en "
                "«Cortes por cerrar» con «No se cerró solo: …» y se cierra en cuanto "
                "suba; si urge, ahí mismo «Cerrar el corte» (ADR 0002 §133).",
                desde="+44",
            ),
            Punto(
                "panel_aceptar_carga", "Aceptar la carga que pidió un vendedor",
                "Cortes y cargas → Por aceptar → «Cargas por aceptar»: ajusta lo que haga "
                "falta → «Aceptar y confirmar la carga» (o «Rechazar la solicitud» con su "
                "motivo).",
                "No deja aceptar mientras el corte de ese vendedor esté por cerrar, ni "
                "si lo aceptado no alcanza en la bodega (dice cuánto hay de cada uno). La "
                "aceptada baja de la bodega y le llega al teléfono al sincronizar.",
                desde="+37",
            ),
            Punto(
                "panel_carga_a_mano", "Una carga a mano",
                "Cortes y cargas → Cargas → «Abrir borrador» → «Agregar» productos → "
                "«Confirmar la carga».",
                "Baja la bodega y sube el camión. «Cancelar el borrador» no mueve nada. Si "
                "el teléfono tiene operaciones sin subir, lo avisa antes de confirmar.",
            ),
            Punto(
                "panel_corte_a_mano", "Corte a mano contando el camión",
                "Cortes y cargas → Corte a mano: abre el de un camión, «Guardar conteo», "
                "«Guardar arqueo» y ciérralo.",
                "Compara lo contado contra lo esperado, en mercancía y en efectivo.",
            ),
            Punto(
                "panel_ventas", "Ventas: todas, por revisar y canceladas",
                "Operación de rutas → Ventas: «Todas», «Por revisar» y las canceladas.",
                "Cada venta con su folio, vendedor, cliente y total; las canceladas van "
                "aparte.",
            ),
            Punto(
                "panel_cancelar_venta", "Cancelar o corregir una venta",
                "Abre una venta de prueba: «Corregir la venta», o «Cancelar la venta» con "
                "su motivo.",
                "La cancelada pasa a canceladas, la mercancía vuelve al camión y el "
                "teléfono se entera al sincronizar.",
            ),
            Punto(
                "panel_mayoreo", "Venta de mayoreo",
                "Ventas → Mayoreo → «Nueva venta de mayoreo»: da de alta un cliente de "
                "mayoreo, escoge la bodega, los artículos y el precio → «Guardar la "
                "venta». Después cancélala.",
                "Folio MY- y su ticket; baja la bodega y la cancelación la devuelve. "
                "Cuenta en el objetivo de toda la empresa.",
                desde="+33",
            ),
            Punto(
                "panel_vendedores", "Vendedores y lo que hizo cada uno",
                "Operación de rutas → Vendedores: abre uno.",
                "Todo lo que hizo en el periodo: ventas, cargas, cortes y mermas; los "
                "desactivados van aparte.",
            ),
            Punto(
                "panel_cuenta_vendedor", "La cuenta del vendedor",
                "Vendedores → Cuentas.",
                "Sus faltantes, abonos y condonaciones, con el saldo.",
            ),
        ),
        parte="dashboard",
    ),
    Seccion(
        "panel_catalogos", "Clientes y productos", "La computadora, entrando como admin",
        (
            Punto(
                "panel_clientes", "Clientes",
                "Catálogos → Clientes: «Buscar» y abre uno; cambia un dato y «Guardar "
                "datos».",
                "Sus datos, días de visita, lista de precios y ubicación; el cambio llega "
                "al teléfono al sincronizar.",
            ),
            Punto(
                "panel_cliente_eliminar_de_verdad", "Eliminar una tienda de prueba",
                "Catálogos → Clientes: abre una tienda de prueba que nunca tuvo venta ni "
                "visita → hasta abajo, «Eliminar el cliente»: marca la casilla → "
                "«Eliminar». Búscala otra vez.",
                "Ya no aparece en ninguna lista: se borró de verdad. Antes, en el "
                "servidor decía «se eliminó» y se quedaba (ADR 0002 §138).",
                desde="+44",
            ),
            Punto(
                "panel_clientes_por_ruta", "Clientes por ruta y día",
                "Catálogos → Clientes → «Todos»: toca una cifra de la tabla de rutas por "
                "día; luego usa los filtros de ruta y día.",
                "La tabla dice cuántas tiendas tiene cada ruta cada día. Una ruta trae sus "
                "tiendas agrupadas por día, en el orden del recorrido, con «Plan de "
                "visita de esta ruta»; un día trae ese día de cada ruta.",
            ),
            Punto(
                "panel_prospecto", "Confirmar una tienda dada de alta en la calle",
                "Abre una tienda que dio de alta un vendedor (prospecto) → «Confirmar y "
                "asignar código».",
                "Recibe el código que sigue: 1934, 1935… (las de los Excel de rutas son "
                "100–1933), y deja de ser prospecto.",
            ),
            Punto(
                "panel_plan_visita", "El plan de visita",
                "Clientes → Plan de visita: escoge la ruta, marca los días → «Guardar el "
                "plan».",
                "El teléfono muestra esas tiendas en «Hoy» los días marcados.",
                desde="+33",
            ),
            Punto(
                "panel_productos", "Productos, precios y familias",
                "Catálogos → Productos: «Nuevo producto», cambia un precio («Guardar "
                "precios») y una familia («Agregar familia», «Renombrar»).",
                "Al sincronizar, el teléfono tiene el producto y el precio nuevos, en su "
                "familia.",
            ),
        ),
        parte="dashboard",
    ),
    Seccion(
        "panel_almacen", "Almacén", "La computadora, entrando como admin",
        (
            Punto(
                "panel_inventario", "Inventario y ajustes",
                "Almacén → Inventario: escoge la bodega y luego un camión; «Ajustar» un "
                "producto con su motivo.",
                "Existencias por familia; el ajuste del camión le llega al teléfono.",
            ),
            Punto(
                "panel_mover_mercancia", "Mover mercancía entre la bodega y un camión",
                "Almacén → Inventario → pestaña «Mover mercancía»: «De la bodega al "
                "camión», el camión y la bodega → «Ver lo que hay» → escribe cuánto de "
                "un artículo (en caja o pieza) → «Mover a …». Luego lo mismo «Del camión "
                "a la bodega».",
                "Baja el origen y sube el destino, y queda en «Lo último que se movió». "
                "Al teléfono del vendedor le llega al sincronizar, aunque no trajera ese "
                "artículo. Si escribes más de lo que hay, no mueve nada y dice cuánto "
                "hay. En Operaciones sale una sola vez, y anularla regresa las dos "
                "partes (ADR 0002 §135).",
                desde="+44",
            ),
            Punto(
                "panel_entrada", "Recibir mercancía",
                "Almacén → Entradas y salidas → Entradas → «Abrir borrador»: salen todos "
                "los artículos por familia. Escribe la cantidad de varios (y el costo, si "
                "es compra) → «Agregar a la entrada» → confirmar.",
                "Se agregan todos de un jalón, sin teclear SKU; si uno trae un error, los "
                "demás entran y se dice cuál no. Sube la bodega y queda en la lista con su "
                "folio (ADR 0002 §140).",
                desde="+45",
            ),
            Punto(
                "panel_salida", "Salida o conteo de bodega",
                "Entradas y salidas → Salidas → «Abrir borrador»: sale lo que hay en la "
                "bodega. En un conteo escribe lo que contaste de varios → «Capturar el "
                "conteo».",
                "Lo que falta se agrega con la resta hecha; lo que cuadra no se agrega. "
                "Baja la bodega, con su motivo (ADR 0002 §140).",
                desde="+45",
            ),
            Punto(
                "panel_devolucion", "Recibir la devolución de un camión",
                "Después de «Devolver a la bodega» en un teléfono, toca el pendiente del "
                "Tablero «devolución(es) de camión por recibir en bodega» y recíbela.",
                "La mercancía sale del tránsito y entra a la bodega.",
            ),
            Punto(
                "panel_compras", "Compras, proveedores y valor del inventario",
                "Almacén → Compras.",
                "Cuentas por pagar, por proveedor y el valor del inventario; las compras "
                "recibidas desde el teléfono aparecen aquí.",
            ),
        ),
        parte="dashboard",
    ),
    Seccion(
        "panel_admin", "Usuarios y teléfonos", "La computadora, entrando como admin",
        (
            Punto(
                "panel_usuarios", "Usuarios",
                "Administración → Usuarios y rutas: filtra por rol; «Crear usuario», "
                "«Cambiar contraseña» y «Desactivar» uno de prueba.",
                "Los desactivados van aparte, y un vendedor desactivado ya no entra a la "
                "app.",
            ),
            Punto(
                "panel_contrasena_sin_vincular", "Cambiar la contraseña de un vendedor",
                "Usuarios y rutas → «Más ▾» de un vendedor de prueba → escribe la "
                "contraseña nueva (se ve lo que escribes) → «Cambiar». En su teléfono, "
                "CON señal, sal y entra con la nueva; luego intenta con la vieja.",
                "La nueva entra sin sincronizar ni volver a vincular, y la vieja ya no. "
                "Sin señal, el teléfono sigue entrando con la vieja hasta que se conecte "
                "(ADR 0002 §136).",
                desde="+44",
            ),
            Punto(
                "panel_cambiar_codigo", "Cambiar el código de un usuario",
                "Usuarios y rutas → «Más ▾» de uno de prueba → «Cambiar código»: ponle "
                "otro (por ejemplo JUAN). Luego intenta darle su código viejo a otro "
                "usuario.",
                "Dice «VEND01 ahora es JUAN», entra con el nuevo y no con el viejo, y su "
                "camión cambia con él. El código viejo sale como apartado en su ficha: "
                "a otro no se le puede dar, y él sí puede volver a tenerlo.",
                desde="+43",
            ),
            Punto(
                "panel_rutas", "Rutas, camiones y almacenes",
                "En Usuarios y rutas, «Editar» el nombre de una ruta o un almacén.",
                "El nombre nuevo se ve en el dashboard y, al sincronizar, en el teléfono.",
            ),
            Punto(
                "panel_vincular", "Vincular un teléfono",
                "Teléfonos → «Vincular un teléfono»: el vendedor y la clave corta → "
                "«Vincular».",
                "Con esa clave se vincula el teléfono (ver el punto del teléfono «Vincular "
                "un teléfono con la clave corta»).",
            ),
            Punto(
                "panel_sincronizaciones", "Sincronizaciones",
                "Teléfonos → Sincronizaciones.",
                "Cada teléfono con su última subida y bajada, lo que tiene por bajar y su "
                "versión de la app; la bitácora con cada subida.",
            ),
            Punto(
                "panel_cuarentena", "Cuarentena",
                "Teléfonos → Cuarentena.",
                "Lo que el servidor no pudo aceptar, con su motivo; «Reprocesar» lo "
                "intenta otra vez. Lo normal es que esté vacía.",
            ),
            Punto(
                "panel_suspender", "Suspender y reactivar un teléfono",
                "Solo con un teléfono de prueba: Teléfonos → «Suspender» y luego "
                "«Reactivar».",
                "Suspendido deja de recibir datos nuevos pero puede subir lo que trae; "
                "reactivado sigue donde iba.",
            ),
        ),
        parte="dashboard",
    ),
    Seccion(
        "panel_operaciones", "Operaciones: todas juntas, y eliminar deshaciendo",
        "La computadora, entrando como admin, y el teléfono del vendedor a la mano",
        (
            Punto(
                "panel_operaciones_lista", "Todas las operaciones juntas",
                "Administración → Operaciones: cambia el periodo y filtra por tipo, por "
                "vendedor y por «Eliminadas».",
                "Ventas, mayoreo, visitas sin venta, mermas, cargas, devoluciones, "
                "traspasos, entradas, salidas, ajustes y cortes en una sola lista, la más "
                "reciente arriba; cada una abre su página. Lo eliminado no sale en la "
                "lista de siempre: está en «Eliminadas».",
            ),
            Punto(
                "panel_operaciones_anular_venta", "Eliminar una venta de prueba",
                "Haz una venta de prueba en el teléfono y sincroniza. En Operaciones ábrela, "
                "lee «Si la eliminas», escribe el motivo → «Eliminar y deshacer». "
                "Sincroniza el teléfono.",
                "En «Eliminadas» queda con tu nombre, la hora y el motivo; la mercancía "
                "vuelve al camión (Inventario), sale de las ventas del día, y en el "
                "teléfono la venta aparece cancelada con el motivo.",
            ),
            Punto(
                "panel_operaciones_anular_merma", "Eliminar una merma de prueba",
                "Registra una merma de prueba en el teléfono y sincroniza. Elimínala en "
                "Operaciones y vuelve a sincronizar el teléfono.",
                "Las piezas regresan al camión en el dashboard y en el teléfono (llega un "
                "ajuste «Anulación de merma…» con tu motivo), y deja de contar en las "
                "mermas del día.",
            ),
            Punto(
                "panel_operaciones_eliminar_corte", "Eliminar un corte de prueba",
                "Cierra el corte de un vendedor de prueba con un faltante de efectivo. En "
                "Operaciones abre ese corte → «Eliminar y deshacer».",
                "La carga vuelve a «Cargas por cerrar» y el corte del vendedor a «Cortes "
                "por cerrar»; en su cuenta, el faltante queda condonado. Se puede cerrar "
                "otra vez.",
                desde="+41",
            ),
            Punto(
                "panel_operaciones_eliminar_carga", "Eliminar una carga con su corte",
                "Con un vendedor de prueba: elimina primero su venta y luego su carga ya "
                "cortada. Sincroniza el teléfono.",
                "La página de la carga dice que se elimina también su corte; la mercancía "
                "regresa a la bodega, el corte queda en «Eliminadas» y el camión del "
                "teléfono se vacía de esa carga.",
                desde="+41",
            ),
            Punto(
                "panel_operaciones_varias", "Eliminar varias de prueba a la vez",
                "Operaciones → filtra por el vendedor de prueba → «Marcar todas» → «Eliminar "
                "las marcadas…». Lee la lista y escribe el motivo.",
                "Dice en qué orden se eliminan (ventas primero, entradas al final) y lo que "
                "pasa con cada una; al confirmar, «Eliminadas: N de N». Lo que no se pudo "
                "dice por qué.",
                desde="+41",
            ),
            Punto(
                "panel_operaciones_negativo", "Lo que quedaría en negativo",
                "Abre una carga cuyo camión ya vendió parte. Elimínala sin marcar la casilla; "
                "luego entra como supervisor.",
                "Dice «Quedaría en negativo» y no elimina sin la casilla «Eliminarla de "
                "todos modos». El supervisor ve la lista pero no el botón «Eliminar».",
                desde="+41",
            ),
        ),
        parte="dashboard",
    ),
    Seccion(
        "panel_facil", "El dashboard más fácil de leer",
        "La computadora, entrando como admin",
        (
            Punto(
                "panel_almacenes_tabla", "Los almacenes en una tabla",
                "Almacén → Inventario. Cambia de almacén con el selector y con la tabla.",
                "Arriba, tres cifras: la bodega, los camiones y el total. Después el "
                "selector y «Todos los almacenes», un renglón por almacén con lo que "
                "vale, y el que estás viendo marcado. Ya no hay un recuadro por almacén.",
                desde="+41",
            ),
            Punto(
                "panel_plan_por_dia", "El plan de visita por día",
                "Catálogos → Clientes → Plan de visita: toca «Lun · 45». Cambia un orden "
                "y «Guardar el plan».",
                "La lista queda solo con los clientes del lunes, en su orden. Al guardar "
                "regresa al lunes, y los clientes de los demás días no cambian.",
                desde="+41",
            ),
            Punto(
                "panel_eliminar_vendedor_nuevo", "Eliminar un vendedor de prueba",
                "Crea un vendedor de prueba con camión, vincula un teléfono, entra y da "
                "de alta una tienda. Luego Usuarios y rutas → «Más ▾» → «Eliminar».",
                "Se elimina con su teléfono y su camión vacío; la tienda se queda. Si "
                "tuviera ventas o algo en su camión, el aviso dice qué es, no «documentos "
                "firmados» ni «change_log» (ADR 0002 §137 y §138).",
                desde="+44",
            ),
            Punto(
                "panel_usuarios_mas", "Las acciones de cada usuario",
                "Administración → Usuarios y rutas: toca «Más ▾» en un vendedor.",
                "Cada renglón mide una línea con «Editar» y «Más ▾»; ahí se abren la "
                "contraseña nueva, «Desactivar» y «Eliminar».",
                desde="+41",
            ),
        ),
        parte="dashboard",
    ),
    Seccion(
        "panel_reporte", "Reporte en Excel para el administrador",
        "La computadora, entrando como admin, con Excel a la mano",
        (
            Punto(
                "panel_reporte_bajar", "Bajar el reporte en Excel",
                "Administración → Reporte en Excel → elige «Esta semana» → «Bajar el "
                "reporte en Excel», y ábrelo.",
                "Cinco hojas: Resumen, Ventas y efectivo, Inventario, Cargas y mermas, y "
                "Cuenta de vendedores, con encabezados en rojo y totales. Lo vendido y el "
                "efectivo del Resumen son los del Tablero de esa semana.",
                desde="+41",
            ),
            Punto(
                "panel_reporte_eliminado", "Lo eliminado no sale en el reporte",
                "Elimina una venta de prueba en Operaciones y vuelve a bajar el reporte "
                "del mismo periodo.",
                "La venta ya no cuenta en el Resumen ni en Ventas y efectivo, y sus piezas "
                "vuelven a estar en el camión en la hoja de Inventario.",
                desde="+41",
            ),
        ),
        parte="dashboard",
    ),
)

PUNTOS: dict[str, Punto] = {p.clave: p for s in SECCIONES for p in s.puntos}
assert len(PUNTOS) == sum(len(s.puntos) for s in SECCIONES), "claves repetidas"

_VERSION = re.compile(r"^\d+\.\d+\.\d+\+\d+$")


def orden_de_version(version: str) -> tuple:
    """«0.1.0+35» → (0, 1, 0, 35): para ordenar, la +100 después de la +99."""
    numeros = re.findall(r"\d+", version)
    return tuple(int(n) for n in numeros) if numeros else (-1,)


def version_valida(version: str) -> str:
    limpia = (version or "").strip()
    if not _VERSION.match(limpia):
        raise MarcaInvalida(
            "Escribe la versión como aparece al pie de la app, por ejemplo 0.1.0+35."
        )
    return limpia


@dataclass(frozen=True)
class Marca:
    estado: str
    nota: str | None
    quien: str | None
    cuando: datetime


async def marcas_de(sesion, version: str) -> dict[str, Marca]:
    filas = (
        await sesion.execute(
            text(
                """
                SELECT r.punto, r.estado, r.nota, u.nombre AS quien, r.revisado_en
                  FROM revision_app r
                  LEFT JOIN usuarios u ON u.id = r.revisado_por
                 WHERE r.version = :v
                """
            ),
            {"v": version},
        )
    ).mappings().all()
    return {f["punto"]: Marca(f["estado"], f["nota"], f["quien"], f["revisado_en"]) for f in filas}


async def marcar(
    sesion, *, version: str, punto: str, estado: str, nota: str | None, quien: uuid.UUID | None
) -> None:
    """Bien o falla, con su nota. Volver a marcar lo corrige. No hace commit."""
    version = version_valida(version)
    if punto not in PUNTOS:
        raise MarcaInvalida("Ese punto ya no está en la lista.")
    if estado not in ESTADOS:
        raise MarcaInvalida("Marca «bien» o «falla».")
    if estado == "falla" and not nota:
        raise MarcaInvalida("Escribe qué falló: es lo que hay que arreglar.")
    await sesion.execute(
        text(
            """
            INSERT INTO revision_app (version, punto, estado, nota, revisado_por)
            VALUES (:v, :p, :e, :n, :q)
            ON CONFLICT (version, punto) DO UPDATE SET
                estado       = excluded.estado,
                nota         = excluded.nota,
                revisado_por = excluded.revisado_por,
                revisado_en  = now()
            """
        ),
        {"v": version, "p": punto, "e": estado, "n": nota, "q": quien},
    )


async def desmarcar(sesion, *, version: str, punto: str) -> None:
    """Vuelve a pendiente. No hace commit."""
    await sesion.execute(
        text("DELETE FROM revision_app WHERE version = :v AND punto = :p"),
        {"v": version, "p": punto},
    )


async def volver_a_empezar(sesion, *, version: str, parte: str) -> int:
    """Borra las marcas de una parte en esa versión: para cuando solo se
    actualizó el servidor y hay que volver a revisar el dashboard. No hace
    commit."""
    claves = [p.clave for s in SECCIONES if s.parte == parte for p in s.puntos]
    resultado = await sesion.execute(
        text("DELETE FROM revision_app WHERE version = :v AND punto = ANY(:claves)"),
        {"v": version, "claves": claves},
    )
    return resultado.rowcount


async def versiones(sesion) -> dict[str, list[str]]:
    """Las versiones conocidas, de la más nueva a la más vieja, con los
    teléfonos de vendedor que la traen ahora («Juan Pérez · VEND01»).

    Salen de lo que reportan los teléfonos al sincronizar y de las que ya
    tienen marcas. Gerencia no sincroniza, así que su teléfono no aparece."""
    telefonos = (
        await sesion.execute(
            text(
                """
                SELECT COALESCE(
                         (SELECT l.app_version FROM sync_lotes l
                           WHERE l.dispositivo_id = d.id AND l.app_version IS NOT NULL
                           ORDER BY l.recibido_en DESC LIMIT 1),
                         d.app_version) AS version,
                       u.nombre || ' · ' || u.codigo AS quien
                  FROM dispositivos d
                  JOIN usuarios u ON u.id = d.usuario_id
                 WHERE d.estado = 'activo'
                 ORDER BY u.codigo
                """
            )
        )
    ).mappings().all()
    conocidas: dict[str, list[str]] = {}
    for t in telefonos:
        if t["version"]:
            conocidas.setdefault(t["version"], []).append(t["quien"])
    marcadas = (
        await sesion.execute(text("SELECT DISTINCT version FROM revision_app"))
    ).scalars().all()
    for v in marcadas:
        conocidas.setdefault(v, [])
    return dict(sorted(conocidas.items(), key=lambda kv: orden_de_version(kv[0]), reverse=True))


async def versiones_con_marcas(sesion) -> list[str]:
    return list(
        (await sesion.execute(text("SELECT DISTINCT version FROM revision_app"))).scalars()
    )


def anterior_a(version: str, conocidas) -> str | None:
    """La versión inmediata anterior entre `conocidas`."""
    previas = [v for v in conocidas if orden_de_version(v) < orden_de_version(version)]
    return max(previas, key=orden_de_version) if previas else None
