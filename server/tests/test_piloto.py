"""El piloto en el panel: capturar, registrar y cerrar, contra PostgreSQL real.

La lógica de los criterios se prueba como función pura en
`test_piloto_criterios.py`. Aquí se ejercita lo que esas pruebas no pueden: el
SQL, las plantillas, los permisos y las reglas del formulario.

Y eso no es ceremonia: las pruebas puras no ejecutan una sola línea de SQL, y
las dos consultas de este módulo llegaron a PostgreSQL con `:desde::date` sin
sustituir —el mismo defecto que la Fase 7— hasta que se corrieron de verdad.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.domain.piloto import (
    SQL_CRITERIOS,
    VEREDICTOS,
    fin_por_omision,
    inicio_por_omision,
)
from tests.conftest import PASSWORD_VENDEDOR, solo_texto

pytestmark = pytest.mark.asyncio


# ===========================================================================
# Los criterios sembrados y el código no pueden separarse
# ===========================================================================
async def test_cada_criterio_evaluable_tiene_medicion_en_el_codigo(sesion):
    """La verificación de frescura de esta fase.

    Los umbrales viven en la tabla y las mediciones en el código. Si alguien
    siembra un criterio en una migración y olvida implementarlo, la pantalla lo
    muestra "sin medir" para siempre y nadie lo distingue de una falta de datos.
    Esta prueba es lo que pone el CI en rojo antes.
    """
    from app.domain.piloto import _MEDIDAS

    filas = (await sesion.execute(text(SQL_CRITERIOS))).mappings().all()
    assert filas, "la migración 0024 no sembró los criterios"

    evaluables = {f["codigo"] for f in filas if f["evaluable"]}
    sin_medicion = evaluables - set(_MEDIDAS)
    assert not sin_medicion, f"criterios sembrados sin medición: {sin_medicion}"

    sin_criterio = set(_MEDIDAS) - {f["codigo"] for f in filas}
    assert not sin_criterio, f"mediciones sin criterio sembrado: {sin_criterio}"


async def test_lo_que_no_se_evalua_esta_declarado_y_explicado(sesion):
    """Un criterio no evaluable sin nota sería un supuesto escondido."""
    filas = (await sesion.execute(text(SQL_CRITERIOS))).mappings().all()
    no_evaluables = [f for f in filas if not f["evaluable"]]
    assert no_evaluables, "si todo es evaluable, la impresora quedó sin declarar"
    for f in no_evaluables:
        assert f["nota"], f["codigo"]
    assert any("EC-MP200" in (f["nota"] or "") for f in no_evaluables)


async def test_ningun_criterio_bloqueante_es_no_evaluable(sesion):
    """Sería un piloto imposible de aprobar, y nadie entendería por qué."""
    filas = (await sesion.execute(text(SQL_CRITERIOS))).mappings().all()
    for f in filas:
        assert not (f["bloqueante"] and not f["evaluable"]), f["codigo"]


# ===========================================================================
# El panel, de punta a punta
# ===========================================================================
async def _entrar(cliente, codigo: str = "ADMIN01") -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": codigo, "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


async def _csrf(cliente, ruta: str = "/panel/piloto") -> str:
    r = await cliente.get(ruta)
    hallado = re.search(r'name="csrf" value="([^"]+)"', r.text)
    assert hallado, f"{ruta} no trajo token CSRF"
    return hallado.group(1)


async def _usuario(sesion, semilla, codigo: str, rol: str) -> uuid.UUID:
    from app.core.seguridad import hashear_password

    identificador = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
            "                      rol_codigo, creado_en, actualizado_en) "
            "VALUES (:u, :s, :cod, :nom, :h, :rol, now(), now())"
        ),
        {
            "u": identificador,
            "s": semilla["sucursal"],
            "cod": codigo,
            "nom": f"Usuario {codigo}",
            "h": hashear_password(PASSWORD_VENDEDOR),
            "rol": rol,
        },
    )
    await sesion.commit()
    return identificador


async def _definir(cliente, semilla, *, inicio: date, fin: date, codigo="f3-prueba"):
    csrf = await _csrf(cliente)
    return await cliente.post(
        "/panel/piloto/definir",
        data={
            "csrf": csrf,
            "codigo": codigo,
            "vendedor_id": str(semilla["vendedor"]),
            "ruta_id": str(semilla["ruta"]),
            "inicio": inicio.isoformat(),
            "fin": fin.isoformat(),
        },
        follow_redirects=False,
    )


async def _vender(sesion, semilla, *, dia: date, cuantas: int, total: str = "500"):
    """Ventas reales en la base: estas pruebas ejercitan el SQL, no el andamio."""
    dispositivo = (
        await sesion.execute(
            text("SELECT id FROM dispositivos WHERE usuario_id = :u LIMIT 1"),
            {"u": semilla["vendedor"]},
        )
    ).scalar()
    if dispositivo is None:
        dispositivo = uuid.uuid4()
        await sesion.execute(
            text(
                "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, "
                "registrado_en) VALUES (:d, :u, 'POCO M5s', 'activo', now())"
            ),
            {"d": dispositivo, "u": semilla["vendedor"]},
        )
    cliente_id = (
        await sesion.execute(text("SELECT id FROM clientes LIMIT 1"))
    ).scalar()
    if cliente_id is None:
        cliente_id = uuid.uuid4()
        await sesion.execute(
            text(
                "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, "
                "creado_en, actualizado_en) "
                "VALUES (:c, 'C00001', 'Doña Mary', :r, now(), now())"
            ),
            {"c": cliente_id, "r": semilla["ruta"]},
        )
    for i in range(cuantas):
        await sesion.execute(
            text(
                """
                INSERT INTO ventas (id, dispositivo_id, folio_consecutivo,
                                    folio_local, cliente_id, vendedor_id,
                                    almacen_id, tipo, subtotal, total,
                                    fecha_dispositivo, fecha_operativa)
                VALUES (:v, :d, :folio, :fl, :c, :u, :a, 'contado',
                        :total, :total, :ahora, :dia)
                """
            ),
            {
                "v": uuid.uuid4(),
                "d": dispositivo,
                "folio": int(dia.strftime("%j")) * 100 + i,
                "fl": f"VEND01-{dia:%j}{i:03d}",
                "c": cliente_id,
                "u": semilla["vendedor"],
                "a": semilla["camion"],
                "total": Decimal(total),
                "ahora": datetime.combine(dia, time(13, 0), tzinfo=UTC),
                "dia": dia,
            },
        )
    await sesion.commit()


# ---------------------------------------------------------------------------
# Definir
# ---------------------------------------------------------------------------
async def test_sin_piloto_la_pantalla_ofrece_definirlo(cliente, sesion, semilla):
    """Y no una pantalla vacía: es el hueco que dejó la Fase 6 con los motivos."""
    await _entrar(cliente)
    r = await cliente.get("/panel/piloto")
    assert r.status_code == 200
    texto = solo_texto(r)
    assert "Definir el piloto" in texto
    assert "VEND01" in texto
    assert "docs/PILOTO.md" in texto


async def test_el_formulario_propone_un_lunes_y_dos_semanas(cliente, sesion, semilla):
    await _entrar(cliente)
    r = await cliente.get("/panel/piloto")
    arranca = inicio_por_omision(date.today())
    assert arranca.isoformat() in r.text
    assert fin_por_omision(arranca).isoformat() in r.text


async def test_definir_el_piloto_lo_deja_activo(cliente, sesion, semilla):
    await _entrar(cliente)
    hoy = date.today()
    r = await _definir(cliente, semilla, inicio=hoy - timedelta(days=3),
                       fin=hoy + timedelta(days=10))
    assert r.status_code == 303
    fila = (
        await sesion.execute(
            text("SELECT codigo, activo, veredicto FROM pilotos")
        )
    ).mappings().one()
    assert fila["codigo"] == "f3-prueba"
    assert fila["activo"] is True
    assert fila["veredicto"] is None


async def test_un_piloto_de_menos_de_una_semana_se_rechaza(cliente, sesion, semilla):
    """Sin segunda semana no hay nada contra qué comparar la primera."""
    await _entrar(cliente)
    hoy = date.today()
    r = await _definir(cliente, semilla, inicio=hoy, fin=hoy + timedelta(days=3))
    assert r.status_code == 303
    assert "segunda%20semana" in r.headers["location"]
    assert (await sesion.execute(text("SELECT count(*) FROM pilotos"))).scalar() == 0


async def test_solo_puede_haber_un_piloto_activo(cliente, sesion, semilla):
    """Con dos, cada pantalla elegiría uno al azar. Lo impide el índice parcial."""
    await _entrar(cliente)
    hoy = date.today()
    assert (
        await _definir(cliente, semilla, inicio=hoy, fin=hoy + timedelta(days=13))
    ).status_code == 303
    with pytest.raises(Exception, match="uq_piloto_activo"):
        await _definir(
            cliente, semilla, inicio=hoy, fin=hoy + timedelta(days=13),
            codigo="f3-otro",
        )


# ---------------------------------------------------------------------------
# Capturar el cuadre
# ---------------------------------------------------------------------------
async def test_capturar_congela_lo_que_el_sistema_dice_en_ese_momento(
    cliente, sesion, semilla
):
    """La cifra del sistema NO viene del formulario: se vuelve a leer al guardar.

    Si viniera de la pantalla, el cuadre guardaría lo que el sistema decía al
    CARGAR la página —que pueden ser veinte minutos y una sincronización antes—
    y la diferencia congelada mediría el tiempo que tardó alguien en teclear.
    """
    await _entrar(cliente)
    hoy = date.today()
    ayer = hoy - timedelta(days=1)
    await _definir(cliente, semilla, inicio=hoy - timedelta(days=5),
                   fin=hoy + timedelta(days=8))
    await _vender(sesion, semilla, dia=ayer, cuantas=3, total="500")

    csrf = await _csrf(cliente)
    r = await cliente.post(
        "/panel/piloto/jornada",
        data={
            "csrf": csrf, "dia": ayer.isoformat(), "documentos": "5",
            "importe": "2,500.00", "cobranza": "0", "visitas": "7",
            "observaciones": "la 142 se canceló en el papel",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    fila = (
        await sesion.execute(
            text(
                "SELECT documentos_papel, documentos_sistema, importe_papel, "
                "       importe_sistema, diferencia_documentos, visitas_papel, "
                "       observaciones "
                "  FROM piloto_jornadas"
            )
        )
    ).mappings().one()
    assert fila["documentos_papel"] == 5
    assert fila["documentos_sistema"] == 3
    assert fila["importe_papel"] == Decimal("2500.00")
    assert fila["importe_sistema"] == Decimal("1500.00")
    # sistema − papel: negativo es la dirección peligrosa.
    assert fila["diferencia_documentos"] == -2
    assert fila["visitas_papel"] == 7
    assert "142" in fila["observaciones"]


async def test_el_aviso_dice_que_falta_y_que_significa(cliente, sesion, semilla):
    """Quien captura a las ocho de la mañana no va a abrir la tabla de criterios."""
    await _entrar(cliente)
    hoy = date.today()
    ayer = hoy - timedelta(days=1)
    await _definir(cliente, semilla, inicio=hoy - timedelta(days=5),
                   fin=hoy + timedelta(days=8))
    await _vender(sesion, semilla, dia=ayer, cuantas=3)
    csrf = await _csrf(cliente)
    r = await cliente.post(
        "/panel/piloto/jornada",
        data={"csrf": csrf, "dia": ayer.isoformat(), "documentos": "5",
              "importe": "2500", "cobranza": "0", "visitas": ""},
        follow_redirects=False,
    )
    destino = r.headers["location"]
    assert "faltan" in destino.lower()
    seguimiento = await cliente.get(destino)
    assert "venta perdida y no un retraso" in solo_texto(seguimiento)


async def test_no_se_captura_el_papel_de_hoy(cliente, sesion, semilla):
    """El vendedor todavía está en la calle: el cuadre quedaría contra un día
    incompleto, con un faltante que no es un faltante."""
    await _entrar(cliente)
    hoy = date.today()
    await _definir(cliente, semilla, inicio=hoy - timedelta(days=5),
                   fin=hoy + timedelta(days=8))
    csrf = await _csrf(cliente)
    r = await cliente.post(
        "/panel/piloto/jornada",
        data={"csrf": csrf, "dia": hoy.isoformat(), "documentos": "5",
              "importe": "2500", "cobranza": "0", "visitas": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "jornada%20cerrada" in r.headers["location"]
    assert (
        await sesion.execute(text("SELECT count(*) FROM piloto_jornadas"))
    ).scalar() == 0


async def test_no_se_captura_un_dia_fuera_del_piloto(cliente, sesion, semilla):
    await _entrar(cliente)
    hoy = date.today()
    await _definir(cliente, semilla, inicio=hoy - timedelta(days=5),
                   fin=hoy + timedelta(days=8))
    csrf = await _csrf(cliente)
    r = await cliente.post(
        "/panel/piloto/jornada",
        data={"csrf": csrf, "dia": (hoy - timedelta(days=30)).isoformat(),
              "documentos": "5", "importe": "2500", "cobranza": "0", "visitas": ""},
        follow_redirects=False,
    )
    assert "no%20est%C3%A1%20dentro%20del%20piloto" in r.headers["location"]


async def test_importe_sin_documentos_tambien_se_rechaza(cliente, sesion, semilla):
    """Ese importe salió de alguna nota.

    La comprobación va en los dos sentidos, y la cobranza queda fuera a
    propósito: un día de pasar a cobrar sin vender es normal.
    """
    await _entrar(cliente)
    hoy = date.today()
    ayer = hoy - timedelta(days=1)
    await _definir(cliente, semilla, inicio=hoy - timedelta(days=5),
                   fin=hoy + timedelta(days=8))
    csrf = await _csrf(cliente)
    r = await cliente.post(
        "/panel/piloto/jornada",
        data={"csrf": csrf, "dia": ayer.isoformat(), "documentos": "0",
              "importe": "1500", "cobranza": "0", "visitas": ""},
        follow_redirects=False,
    )
    assert "sali%C3%B3%20de%20alguna%20nota" in r.headers["location"]
    assert (
        await sesion.execute(text("SELECT count(*) FROM piloto_jornadas"))
    ).scalar() == 0

    # Y una jornada de solo cobranza sí entra.
    bueno = await cliente.post(
        "/panel/piloto/jornada",
        data={"csrf": csrf, "dia": ayer.isoformat(), "documentos": "0",
              "importe": "0", "cobranza": "800", "visitas": ""},
        follow_redirects=False,
    )
    assert bueno.status_code == 303
    assert (
        await sesion.execute(text("SELECT cobranza_papel FROM piloto_jornadas"))
    ).scalar() == Decimal("800.00")


async def test_documentos_sin_importe_se_rechaza(cliente, sesion, semilla):
    """Si el papel trae ventas, trae importe. Guardarlo en cero descuadraría
    el criterio del importe con una cifra que nadie escribió."""
    await _entrar(cliente)
    hoy = date.today()
    ayer = hoy - timedelta(days=1)
    await _definir(cliente, semilla, inicio=hoy - timedelta(days=5),
                   fin=hoy + timedelta(days=8))
    csrf = await _csrf(cliente)
    r = await cliente.post(
        "/panel/piloto/jornada",
        data={"csrf": csrf, "dia": ayer.isoformat(), "documentos": "5",
              "importe": "0", "cobranza": "0", "visitas": ""},
        follow_redirects=False,
    )
    assert "trae%20importe" in r.headers["location"]


async def test_capturar_dos_veces_el_mismo_dia_reemplaza(cliente, sesion, semilla):
    """Se teclea con prisa a las ocho de la mañana: corregir tiene que ser fácil."""
    await _entrar(cliente)
    hoy = date.today()
    ayer = hoy - timedelta(days=1)
    await _definir(cliente, semilla, inicio=hoy - timedelta(days=5),
                   fin=hoy + timedelta(days=8))
    for documentos in ("5", "6"):
        csrf = await _csrf(cliente)
        await cliente.post(
            "/panel/piloto/jornada",
            data={"csrf": csrf, "dia": ayer.isoformat(), "documentos": documentos,
                  "importe": "2500", "cobranza": "0", "visitas": ""},
            follow_redirects=False,
        )
    filas = (
        await sesion.execute(
            text("SELECT documentos_papel FROM piloto_jornadas")
        )
    ).scalars().all()
    assert filas == [6]


async def test_el_cuadre_sale_en_la_pantalla_con_su_diferencia(
    cliente, sesion, semilla
):
    await _entrar(cliente)
    hoy = date.today()
    ayer = hoy - timedelta(days=1)
    await _definir(cliente, semilla, inicio=hoy - timedelta(days=5),
                   fin=hoy + timedelta(days=8))
    await _vender(sesion, semilla, dia=ayer, cuantas=3)
    csrf = await _csrf(cliente)
    await cliente.post(
        "/panel/piloto/jornada",
        data={"csrf": csrf, "dia": ayer.isoformat(), "documentos": "5",
              "importe": "2500", "cobranza": "0", "visitas": ""},
        follow_redirects=False,
    )
    texto = solo_texto(await cliente.get("/panel/piloto"))
    assert "Cuadre diario" in texto
    assert "sistema − papel" in texto
    assert "Faltan 2 documento(s)" in texto


# ---------------------------------------------------------------------------
# Los permisos
# ---------------------------------------------------------------------------
async def test_un_vendedor_no_llega_ni_al_panel(cliente, sesion, semilla):
    """Y la garantía es más fuerte que un 403 en la pantalla: no entra al panel.

    `/panel/entrar` rechaza al rol de ruta antes de abrir sesión, así que el
    piloto —que es el instrumento de la decisión y muestra el dinero de la
    jornada— queda fuera de alcance sin depender de su propio permiso.
    """
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": "VEND01", "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 200          # se queda en la pantalla de entrar
    assert "entra por la app del teléfono" in solo_texto(r)

    # Y sin sesión, la pantalla del piloto manda al login en vez de responder.
    sin_sesion = await cliente.get("/panel/piloto", follow_redirects=False)
    assert sin_sesion.status_code == 303
    assert sin_sesion.headers["location"].startswith("/panel/entrar")


async def test_el_supervisor_si_puede_capturar(cliente, sesion, semilla):
    """Es quien ve al vendedor cada mañana y trae el papel en la mano.

    Si tuviera que pedirle a alguien más que lo teclee, no se teclea — y el
    primer criterio del piloto es justamente que el papel se capture.
    """
    await _usuario(sesion, semilla, "SUP01", "supervisor")
    await _entrar(cliente, "SUP01")
    r = await cliente.get("/panel/piloto")
    assert r.status_code == 200
    assert "Definir el piloto" in solo_texto(r)


async def test_el_permiso_esta_sembrado_para_gerencia_y_supervision(sesion):
    filas = (
        await sesion.execute(
            text(
                "SELECT rol_codigo FROM roles_permisos "
                " WHERE permiso_codigo = 'piloto.administrar' ORDER BY rol_codigo"
            )
        )
    ).scalars().all()
    assert filas == ["gerente", "supervisor"]


# ---------------------------------------------------------------------------
# La bitácora
# ---------------------------------------------------------------------------
async def test_registrar_una_incidencia_guarda_el_detalle(cliente, sesion, semilla):
    await _entrar(cliente)
    hoy = date.today()
    await _definir(cliente, semilla, inicio=hoy - timedelta(days=5),
                   fin=hoy + timedelta(days=8))
    csrf = await _csrf(cliente, "/panel/piloto/incidencias")
    r = await cliente.post(
        "/panel/piloto/incidencia",
        data={
            "csrf": csrf, "dia": hoy.isoformat(), "hora": "10:35",
            "categoria": "app", "severidad": "bloqueo",
            "que_hacia": "Agregando el tercer renglón del carrito",
            "que_paso": "La app se cerró y el carrito quedó vacío",
            "costo_una_venta": "1", "minutos": "12",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    fila = (
        await sesion.execute(
            text(
                "SELECT hora, severidad, categoria, que_hacia, que_paso, "
                "       costo_una_venta, hubo_que_usar_papel, minutos_perdidos, "
                "       resuelta_en "
                "  FROM piloto_incidencias"
            )
        )
    ).mappings().one()
    assert fila["hora"] == time(10, 35)
    assert fila["severidad"] == "bloqueo"
    assert fila["costo_una_venta"] is True
    assert fila["hubo_que_usar_papel"] is False
    assert fila["minutos_perdidos"] == 12
    assert fila["resuelta_en"] is None
    assert "tercer renglón" in fila["que_hacia"]


async def test_una_incidencia_sin_hora_se_acepta(cliente, sesion, semilla):
    """Exigirla haría que a las seis de la tarde alguien la inventara."""
    await _entrar(cliente)
    hoy = date.today()
    await _definir(cliente, semilla, inicio=hoy - timedelta(days=5),
                   fin=hoy + timedelta(days=8))
    csrf = await _csrf(cliente, "/panel/piloto/incidencias")
    r = await cliente.post(
        "/panel/piloto/incidencia",
        data={"csrf": csrf, "dia": hoy.isoformat(), "hora": "",
              "categoria": "proceso", "severidad": "molestia",
              "que_hacia": "Cobrando", "que_paso": "El teclado tapa el total",
              "minutos": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert (
        await sesion.execute(text("SELECT hora FROM piloto_incidencias"))
    ).scalar() is None


async def test_una_incidencia_sin_el_detalle_se_rechaza(cliente, sesion, semilla):
    """Sin qué hacía y qué pasó no se puede reproducir ni arreglar."""
    await _entrar(cliente)
    hoy = date.today()
    await _definir(cliente, semilla, inicio=hoy - timedelta(days=5),
                   fin=hoy + timedelta(days=8))
    csrf = await _csrf(cliente, "/panel/piloto/incidencias")
    r = await cliente.post(
        "/panel/piloto/incidencia",
        data={"csrf": csrf, "dia": hoy.isoformat(), "categoria": "app",
              "severidad": "bloqueo", "que_hacia": "  ", "que_paso": "",
              "minutos": "0"},
        follow_redirects=False,
    )
    assert "reproducir" in r.headers["location"]
    assert (
        await sesion.execute(text("SELECT count(*) FROM piloto_incidencias"))
    ).scalar() == 0


async def test_resolver_exige_decir_como(cliente, sesion, semilla):
    """La mitad del valor del piloto es la lista de "pasó esto y así se arregló"."""
    await _entrar(cliente)
    hoy = date.today()
    await _definir(cliente, semilla, inicio=hoy - timedelta(days=5),
                   fin=hoy + timedelta(days=8))
    csrf = await _csrf(cliente, "/panel/piloto/incidencias")
    await cliente.post(
        "/panel/piloto/incidencia",
        data={"csrf": csrf, "dia": hoy.isoformat(), "categoria": "app",
              "severidad": "bloqueo", "que_hacia": "Cobrando",
              "que_paso": "Se cerró", "minutos": "5"},
        follow_redirects=False,
    )
    incidencia_id = (
        await sesion.execute(text("SELECT id FROM piloto_incidencias"))
    ).scalar()

    csrf = await _csrf(cliente, "/panel/piloto/incidencias")
    vacio = await cliente.post(
        f"/panel/piloto/incidencia/{incidencia_id}/resolver",
        data={"csrf": csrf, "resolucion": "   "},
        follow_redirects=False,
    )
    assert "segunda%20ruta" in vacio.headers["location"]

    bueno = await cliente.post(
        f"/panel/piloto/incidencia/{incidencia_id}/resolver",
        data={"csrf": csrf, "resolucion": "Era memoria; se bajó el caché a 40 MiB"},
        follow_redirects=False,
    )
    assert "resuelta" in bueno.headers["location"]

    # Resolverla dos veces no la vuelve a abrir ni pisa la primera razón.
    repetida = await cliente.post(
        f"/panel/piloto/incidencia/{incidencia_id}/resolver",
        data={"csrf": csrf, "resolucion": "otra cosa"},
        follow_redirects=False,
    )
    assert "ya%20estaba%20resuelta" in repetida.headers["location"]
    assert "40 MiB" in (
        await sesion.execute(text("SELECT resolucion FROM piloto_incidencias"))
    ).scalar()


async def test_la_bitacora_vacia_dice_lo_que_de_verdad_significa(
    cliente, sesion, semilla
):
    """"Nadie está preguntando" es la explicación más probable, y hay que decirla."""
    await _entrar(cliente)
    hoy = date.today()
    await _definir(cliente, semilla, inicio=hoy - timedelta(days=5),
                   fin=hoy + timedelta(days=8))
    texto = solo_texto(await cliente.get("/panel/piloto/incidencias"))
    assert "nadie está preguntando" in texto


# ---------------------------------------------------------------------------
# Cerrar
# ---------------------------------------------------------------------------
async def test_cerrar_exige_veredicto_y_razon_escrita(cliente, sesion, semilla):
    await _entrar(cliente)
    hoy = date.today()
    await _definir(cliente, semilla, inicio=hoy - timedelta(days=14),
                   fin=hoy - timedelta(days=1))

    csrf = await _csrf(cliente)
    sin_veredicto = await cliente.post(
        "/panel/piloto/cerrar", data={"csrf": csrf, "nota": "todo bien"},
        follow_redirects=False,
    )
    assert "elegir%20el%20veredicto" in sin_veredicto.headers["location"]

    sin_nota = await cliente.post(
        "/panel/piloto/cerrar", data={"csrf": csrf, "veredicto": "adelante",
                                      "nota": "  "},
        follow_redirects=False,
    )
    assert "tres%20meses" in sin_nota.headers["location"]
    assert (
        await sesion.execute(text("SELECT activo FROM pilotos"))
    ).scalar() is True


async def test_cerrar_guarda_el_veredicto_y_libera_el_lugar(cliente, sesion, semilla):
    """Un piloto cerrado deja de ser el activo, y el historial se conserva."""
    await _entrar(cliente)
    hoy = date.today()
    await _definir(cliente, semilla, inicio=hoy - timedelta(days=14),
                   fin=hoy - timedelta(days=1))
    csrf = await _csrf(cliente)
    r = await cliente.post(
        "/panel/piloto/cerrar",
        data={"csrf": csrf, "veredicto": "repetir",
              "nota": "Tres bloqueos en la segunda semana, dos sin resolver."},
        follow_redirects=False,
    )
    assert r.status_code == 303
    fila = (
        await sesion.execute(
            text("SELECT activo, veredicto, veredicto_nota, cerrado_en FROM pilotos")
        )
    ).mappings().one()
    assert fila["activo"] is False
    assert fila["veredicto"] == "repetir"
    assert fila["cerrado_en"] is not None
    assert "segunda semana" in fila["veredicto_nota"]

    # Y la pantalla vuelve a ofrecer definir uno, con el anterior en el historial.
    texto = solo_texto(await cliente.get("/panel/piloto"))
    assert "Definir el piloto" in texto
    assert "Pilotos anteriores" in texto
    assert VEREDICTOS["repetir"] in texto


# ---------------------------------------------------------------------------
# El SQL contra datos reales
# ---------------------------------------------------------------------------
async def test_la_pantalla_lee_las_ventas_reales_del_vendedor(
    cliente, sesion, semilla
):
    """Ejercita SQL_SISTEMA_POR_DIA con datos en la base, no con andamios.

    Es la prueba que habría atrapado el `:periodo::date` de la Fase 7: las
    pruebas puras no ejecutan una sola línea de SQL.
    """
    await _entrar(cliente)
    hoy = date.today()
    await _definir(cliente, semilla, inicio=hoy - timedelta(days=5),
                   fin=hoy + timedelta(days=8))
    await _vender(sesion, semilla, dia=hoy - timedelta(days=2), cuantas=4,
                  total="250")
    await _vender(sesion, semilla, dia=hoy - timedelta(days=1), cuantas=2,
                  total="1000")

    r = await cliente.get("/panel/piloto")
    assert r.status_code == 200
    texto = solo_texto(r)
    assert "$1,000.00" in texto      # las 4 de $250
    assert "$2,000.00" in texto      # las 2 de $1000
    # Tres jornadas trabajadas con papel pendiente: hoy no cuenta.
    assert "jornadas sin capturar" in texto


async def test_una_venta_cancelada_no_cuenta_como_documento(cliente, sesion, semilla):
    """Y sí cuenta para el criterio de cancelaciones: son dos cifras distintas."""
    await _entrar(cliente)
    hoy = date.today()
    ayer = hoy - timedelta(days=1)
    await _definir(cliente, semilla, inicio=hoy - timedelta(days=5),
                   fin=hoy + timedelta(days=8))
    await _vender(sesion, semilla, dia=ayer, cuantas=4, total="250")
    await sesion.execute(
        text(
            "UPDATE ventas SET estado = 'cancelada' "
            " WHERE id = (SELECT id FROM ventas LIMIT 1)"
        )
    )
    await sesion.commit()

    csrf = await _csrf(cliente)
    await cliente.post(
        "/panel/piloto/jornada",
        data={"csrf": csrf, "dia": ayer.isoformat(), "documentos": "3",
              "importe": "750", "cobranza": "0", "visitas": ""},
        follow_redirects=False,
    )
    fila = (
        await sesion.execute(
            text(
                "SELECT documentos_sistema, importe_sistema, diferencia_documentos "
                "  FROM piloto_jornadas"
            )
        )
    ).mappings().one()
    assert fila["documentos_sistema"] == 3
    assert fila["importe_sistema"] == Decimal("750.00")
    assert fila["diferencia_documentos"] == 0
    assert "25.0 %" in solo_texto(await cliente.get("/panel/piloto"))


async def test_el_piloto_que_no_ha_empezado_no_dibuja_dias(cliente, sesion, semilla):
    """`generate_series` con el fin antes del inicio devuelve vacío sin caso
    especial, y la pantalla lo dice en vez de mostrar una tabla de ceros."""
    await _entrar(cliente)
    hoy = date.today()
    manana = hoy + timedelta(days=1)
    await _definir(cliente, semilla, inicio=manana, fin=manana + timedelta(days=13))
    texto = solo_texto(await cliente.get("/panel/piloto"))
    assert "empieza el" in texto
    assert "Todavía no hay jornadas que cuadrar" in texto
    # Y el formulario de captura no se dibuja: con `min` mayor que `max` el
    # navegador rechazaría cualquier fecha, y eso se lee como un error del
    # sistema en vez de "todavía no toca".
    assert "Guardar el cuadre" not in texto
    assert "El primer cuadre se captura el" in texto


async def test_los_criterios_se_muestran_con_su_umbral(cliente, sesion, semilla):
    """Los doce, con el umbral a la vista: es lo que hace que se puedan discutir
    antes y no después."""
    await _entrar(cliente)
    hoy = date.today()
    await _definir(cliente, semilla, inicio=hoy - timedelta(days=3),
                   fin=hoy + timedelta(days=10))
    texto = solo_texto(await cliente.get("/panel/piloto"))
    assert "Criterios de salida" in texto
    assert "No falta ninguna venta del papel" in texto
    assert "máximo 0.5 %" in texto
    assert "antes del primer día" in texto
    # Y lo que el piloto NO prueba, dicho en voz alta.
    assert "Lo que este piloto no prueba" in texto
    assert "El ticket impreso" in texto


async def test_en_curso_la_pantalla_no_aprueba_nada(cliente, sesion, semilla):
    await _entrar(cliente)
    hoy = date.today()
    await _definir(cliente, semilla, inicio=hoy - timedelta(days=3),
                   fin=hoy + timedelta(days=10))
    texto = solo_texto(await cliente.get("/panel/piloto"))
    assert "Piloto en curso" in texto
    assert "aprobado el día tres no midió dos semanas" in texto


async def test_un_rojo_a_media_medicion_es_un_pendiente_y_no_un_veredicto(
    cliente, sesion, semilla
):
    """Decir "no se despliega así" el día tres suena a sentencia sobre algo que
    todavía se puede arreglar, y la pantalla que solo regaña se deja de abrir."""
    await _entrar(cliente)
    hoy = date.today()
    await _definir(cliente, semilla, inicio=hoy - timedelta(days=3),
                   fin=hoy + timedelta(days=10))
    # Una jornada trabajada, sin liquidar y sin su cuadre: dos criterios en rojo.
    await _vender(sesion, semilla, dia=hoy - timedelta(days=2), cuantas=3)
    texto = solo_texto(await cliente.get("/panel/piloto"))
    assert "en rojo, y el piloto va en el día" in texto
    assert "Todavía se pueden arreglar" in texto
    assert "No se despliega así" not in texto
