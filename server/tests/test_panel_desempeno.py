"""Desempeño del día: cómo va la operación ahora, persona por persona.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTA PANTALLA NECESITA PRUEBAS Y NO SOLO UNA MIRADA
────────────────────────────────────────────────────────────────────────────
Nadie verifica una cifra que sale de una pantalla de monitoreo: se decide con
ella. Y aquí las decisiones son sobre personas — se llama a alguien y se le pide
explicaciones por un número—, así que un número mal construido no cuesta una
consulta mal hecha, cuesta una conversación injusta.

Las cinco formas de que esta pantalla dé un número que *parece* bien:

1. **Comparar contra ayer.** La ruta visita a los mismos clientes cada martes.
   Comparar el martes contra el lunes mide qué clientes tocaban, no cómo se
   trabajó, y el vendedor con razón diría que la cifra no significa nada.

2. **Meter al promedio los días que no trabajó.** Un martes de vacaciones en cero
   baja su referencia y la pantalla dice que hoy va de maravilla. Al revés
   también miente: un martes en que SÍ trabajó y vendió poco tiene que contar.

3. **Dibujar una flecha sobre un solo día de historia.** Un promedio de uno es
   una anécdota, y poner una flecha roja enfrente de alguien por eso es inventar
   un argumento.

4. **Leer «no sincronizó» como «no vendió».** Son el caso más grave: el cero de
   un vendedor cuyo día entero está en su teléfono no es un cero (§0.3). Piden
   dos llamadas distintas y la pantalla tiene que distinguirlas.

5. **Un día sin calcular que se ve igual que un día sin ventas.** El primero es
   un worker caído; el segundo es información. Confundirlos hace que una falla
   del sistema se lea como un problema de la gente.

Y una sexta, que es de consistencia: el **efectivo a entregar** tiene que dar lo
mismo que el arqueo de la liquidación. Si no, la pantalla promete un número y la
liquidación cobra otro — y el vendedor discute con razón.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.domain.tablero import dias_de_referencia
from app.workers.tablero import recalcular_dia
from tests.conftest import PASSWORD_VENDEDOR, solo_texto


# ===========================================================================
# Andamios
# ===========================================================================
async def _entrar(cliente, codigo: str = "ADMIN01") -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": codigo, "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


@pytest.fixture
async def jornada(sesion, semilla) -> dict:
    """El día de hoy, con cifras elegidas para que cada error dé otro número.

      · 3 remisiones a 2 clientes → visitas = 2, documentos = 3
      · venta = $500 + $500 + $1,200 = $2,200
      · drop size sobre visitas CON VENTA (2) → $1,100
      · drop size sobre documentos (3), que sería el error → $733.33
      · efectivo = contado ($1,000) + cobro en efectivo ($300) = $1,300
    """
    dispositivo = uuid.uuid4()
    hoy = date.today()
    ahora = datetime.now(UTC)

    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en, "
            "                          ultima_sync_push_en) "
            "VALUES (:d, :u, 'POCO M5s de Juan', 'activo', now(), now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )

    clientes: dict[str, uuid.UUID] = {}
    for i, nombre in enumerate(["Mary", "Puente"], start=1):
        cid = uuid.uuid4()
        await sesion.execute(
            text(
                "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, "
                "                      creado_en, actualizado_en) "
                "VALUES (:c, :cod, :n, :r, now(), now())"
            ),
            {"c": cid, "cod": f"C{i:05d}", "n": nombre, "r": semilla["ruta"]},
        )
        clientes[nombre] = cid

    folio = [0]

    async def venta(a_quien, total, *, tipo="contado"):
        folio[0] += 1
        await sesion.execute(
            text(
                """
                INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                    cliente_id, vendedor_id, ruta_id, almacen_id, tipo,
                                    subtotal, total, fecha_dispositivo, fecha_operativa)
                VALUES (:v, :d, :f, :fl, :c, :u, :r, :a, :tipo,
                        :total, :total, :ahora, :dia)
                """
            ),
            {
                "v": uuid.uuid4(),
                "d": dispositivo,
                "f": folio[0],
                "fl": f"VEND01-{folio[0]:06d}",
                "c": a_quien,
                "u": semilla["vendedor"],
                "r": semilla["ruta"],
                "a": semilla["camion"],
                "tipo": tipo,
                "total": total,
                "ahora": ahora,
                "dia": hoy,
            },
        )

    # Mary partió su pedido en dos remisiones: sigue siendo UNA visita.
    await venta(clientes["Mary"], Decimal("500.00"))
    await venta(clientes["Mary"], Decimal("500.00"))
    await venta(clientes["Puente"], Decimal("1200.00"), tipo="credito")
    await sesion.execute(
        text(
            """
            INSERT INTO cobros (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, importe, forma_pago,
                                fecha_dispositivo, fecha_operativa)
            VALUES (:id, :d, 90, 'VEND01-A000090', :c, :u, 300, 'efectivo', :ahora, :dia)
            """
        ),
        {
            "id": uuid.uuid4(),
            "d": dispositivo,
            "c": clientes["Mary"],
            "u": semilla["vendedor"],
            "ahora": ahora,
            "dia": hoy,
        },
    )
    await sesion.commit()
    await recalcular_dia(sesion, hoy)
    await sesion.commit()

    return {"dispositivo": dispositivo, "clientes": clientes, "dia": hoy}


async def _historia(
    sesion,
    vendedor,
    *,
    dias: list[date],
    venta: Decimal | int = 2000,
    visitas: int = 4,
    cobrado: Decimal | int = 0,
):
    """Renglones de `tablero_dia` para días pasados.

    Se escribe el modelo de lectura directamente y no se simula un mes de
    operación: lo que estas pruebas miden es qué hace la PANTALLA con la historia,
    y el worker que la produce ya tiene las suyas en `test_tablero.py`.
    """
    for dia in dias:
        await sesion.execute(
            text(
                """
                INSERT INTO tablero_dia (fecha, vendedor_id, venta_total,
                                         venta_contado, documentos_venta, visitas,
                                         visitas_con_venta, cobrado_total,
                                         cobrado_efectivo, calculado_en)
                VALUES (:f, :v, :venta, :venta, 3, :visitas, :visitas, :cobrado,
                        :cobrado, now())
                ON CONFLICT (fecha, vendedor_id) DO UPDATE
                   SET venta_total = excluded.venta_total,
                       visitas = excluded.visitas,
                       cobrado_total = excluded.cobrado_total
                """
            ),
            {
                "f": dia,
                "v": vendedor,
                "venta": venta,
                "visitas": visitas,
                "cobrado": cobrado,
            },
        )
    await sesion.commit()


async def _ver(cliente, fecha: date | None = None) -> str:
    destino = "/panel/desempeno"
    if fecha is not None:
        destino += f"?fecha={fecha.isoformat()}"
    r = await cliente.get(destino)
    assert r.status_code == 200, r.text
    return solo_texto(r)


# ===========================================================================
# Lo básico: el día, persona por persona
# ===========================================================================
async def test_muestra_la_venta_del_dia_de_cada_vendedor(cliente, semilla, jornada):
    await _entrar(cliente)
    plano = await _ver(cliente)

    assert "Juan Pérez" in plano
    assert "VEND01" in plano
    assert "$2,200.00" in plano
    # El desglose, porque contado y crédito no son lo mismo para la caja.
    assert "$1,000.00 contado" in plano
    assert "$1,200.00 crédito" in plano


async def test_el_drop_size_se_calcula_sobre_las_visitas_con_venta(
    cliente, semilla, jornada
):
    """$2,200 entre 2 visitas con venta son $1,100.

    Entre los 3 documentos darían $733.33, y esa es la cifra que premiaría al
    vendedor que parte un pedido en dos remisiones. Es la misma definición que
    `analitica.SQL_DROP_SIZE`: si el panel y el laboratorio dijeran dos números
    con el mismo nombre, los dos quedarían inservibles.
    """
    await _entrar(cliente)
    plano = await _ver(cliente)

    assert "$1,100.00" in plano
    assert "733" not in plano


async def test_el_efectivo_a_entregar_cuadra_con_el_arqueo_de_la_liquidacion(
    cliente, sesion, semilla, jornada
):
    """Contado más cobros en efectivo. La MISMA cuenta que decide el arqueo.

    Si las dos no coincidieran, esta pantalla prometería un número y la
    liquidación cobraría otro, y el vendedor discutiría con razón.
    """
    from app.api.admin.liquidaciones import _efectivo_esperado

    esperado = await _efectivo_esperado(sesion, semilla["vendedor"], jornada["dia"])
    assert esperado == Decimal("1300.00")

    await _entrar(cliente)
    plano = await _ver(cliente)
    assert "$1,300.00" in plano
    assert "efectivo a entregar" in plano


# ===========================================================================
# La comparación: contra el mismo día de la semana
# ===========================================================================
async def test_compara_contra_los_mismos_dias_de_la_semana_no_contra_ayer(
    cliente, sesion, semilla, jornada
):
    """Ayer puede ser un día completamente distinto, y no es la referencia.

    Los martes anteriores promediaron $2,000 y hoy lleva $2,200: +10%. Si la
    pantalla comparara contra ayer ($9,000), diría que va 75% abajo y alguien
    recibiría una llamada por haber tenido un martes normal.
    """
    hoy = jornada["dia"]
    await _historia(sesion, semilla["vendedor"], dias=dias_de_referencia(hoy), venta=2000)
    await _historia(
        sesion, semilla["vendedor"], dias=[hoy - timedelta(days=1)], venta=9000
    )

    await _entrar(cliente)
    plano = await _ver(cliente)

    assert "$2,000.00" in plano, "el promedio de sus mismos días de la semana"
    assert "10.0%" in plano
    assert "$9,000.00" not in plano, "ayer no es la referencia de hoy"


async def test_con_un_solo_dia_de_historia_no_se_dibuja_flecha(
    cliente, sesion, semilla, jornada
):
    """Un promedio de uno es una anécdota, no una referencia."""
    hoy = jornada["dia"]
    await _historia(
        sesion, semilla["vendedor"], dias=dias_de_referencia(hoy)[:1], venta=2000
    )

    await _entrar(cliente)
    plano = await _ver(cliente)

    assert "Sin referencia todavía" in plano


async def test_un_dia_que_no_trabajo_no_le_baja_el_promedio(
    cliente, sesion, semilla, jornada
):
    """Un martes de vacaciones en cero haría que hoy se leyera como un éxito.

    Con tres martes de $2,000 y uno en blanco, el promedio sigue siendo $2,000 —no
    $1,500—. Un día sin venta, sin visita y sin cobro es «no trabajó», no «trabajó
    mal».
    """
    hoy = jornada["dia"]
    referencias = dias_de_referencia(hoy)
    await _historia(sesion, semilla["vendedor"], dias=referencias[:3], venta=2000)
    await _historia(
        sesion, semilla["vendedor"], dias=referencias[3:], venta=0, visitas=0
    )

    await _entrar(cliente)
    plano = await _ver(cliente)

    assert "$2,000.00" in plano
    assert "$1,500.00" not in plano


async def test_un_dia_trabajado_con_mala_venta_si_cuenta(
    cliente, sesion, semilla, jornada
):
    """La otra mitad de la regla, y la que evita que el filtro mienta al revés.

    Un martes en que visitó a cuatro clientes y no le compró ninguno es un mal día
    REAL y tiene que bajar su promedio. Si se excluyera por tener venta en cero, la
    referencia se defendería de los días malos y nunca habría una flecha roja.
    """
    hoy = jornada["dia"]
    referencias = dias_de_referencia(hoy)
    await _historia(sesion, semilla["vendedor"], dias=referencias[:2], venta=4000)
    # Visitó y no vendió: cuenta, y arrastra el promedio a $2,000.
    await _historia(sesion, semilla["vendedor"], dias=referencias[2:], venta=0, visitas=4)

    await _entrar(cliente)
    plano = await _ver(cliente)

    assert "$2,000.00" in plano


async def test_muy_por_debajo_de_su_dia_se_señala_arriba(
    cliente, sesion, semilla, jornada
):
    hoy = jornada["dia"]
    await _historia(
        sesion, semilla["vendedor"], dias=dias_de_referencia(hoy), venta=10000
    )

    await _entrar(cliente)
    plano = await _ver(cliente)

    assert "por debajo de" in plano
    assert "-78.0%" in plano


async def test_una_diferencia_chica_no_pinta_rojo(cliente, sesion, semilla, jornada):
    """El margen existe para que el rojo signifique algo.

    $2,200 contra $2,100 son +4.8%: la venta de un día depende de quién tenía
    dinero ese día, y una pantalla que pinta eso de rojo enseña a ignorar el rojo.
    """
    hoy = jornada["dia"]
    await _historia(
        sesion, semilla["vendedor"], dias=dias_de_referencia(hoy), venta=2100
    )

    await _entrar(cliente)
    plano = await _ver(cliente)

    assert "4.8%" in plano
    assert "por debajo de" not in plano


# ===========================================================================
# §0.3 · «no sincronizó» no es «no vendió»
# ===========================================================================
async def _vendedor(sesion, semilla, *, codigo: str, nombre: str) -> uuid.UUID:
    from app.core.seguridad import hashear_password

    identificador = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO usuarios(id, sucursal_id, codigo, nombre, password_hash, "
            "rol_codigo, creado_en, actualizado_en) "
            "VALUES (:id, :s, :cod, :n, :h, 'vendedor', now(), now())"
        ),
        {
            "id": identificador,
            "s": semilla["sucursal"],
            "cod": codigo,
            "n": nombre,
            "h": hashear_password(PASSWORD_VENDEDOR),
        },
    )
    return identificador


async def _telefono(sesion, usuario, *, ultimo_push, cola: int = 0) -> None:
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en, "
            "                          ultima_sync_push_en, cola_pendiente, "
            "                          cola_reportada_en) "
            "VALUES (:d, :u, 'Equipo de prueba', 'activo', now(), :push, :cola, now())"
        ),
        {"d": uuid.uuid4(), "u": usuario, "push": ultimo_push, "cola": cola},
    )


def _como_si_fuera_mediodia(monkeypatch) -> None:
    """Baja el umbral de la hora para que el aviso del silencio se dibuje.

    Sin esto la prueba mediría la hora del reloj de quien la corre: antes de las
    once el aviso no sale, la aserción «no aparece entre los que no hicieron nada»
    se cumple sola, y la prueba pasa aunque el código esté mal. Pasó: una mutación
    que leía «no sincronizó» como «no vendió» quedó en verde.
    """
    monkeypatch.setattr(
        "app.api.admin.desempeno.HORA_EN_QUE_EL_SILENCIO_YA_NO_SE_EXPLICA", 0
    )


async def test_el_que_no_sincronizo_no_se_lee_como_el_que_no_vendio(
    cliente, sesion, semilla, jornada, monkeypatch
):
    """El caso más grave de toda la pantalla.

    El cero de un vendedor cuyo día entero está en su teléfono no es un cero: es
    una ausencia de información. Las dos situaciones piden llamadas distintas, así
    que van en dos avisos separados y con nombres — y cada persona aparece en el
    suyo y **no** en el otro.
    """
    _como_si_fuera_mediodia(monkeypatch)

    callado = await _vendedor(sesion, semilla, codigo="VEND02", nombre="Pedro Sin Señal")
    await _telefono(
        sesion, callado, ultimo_push=datetime.now(UTC) - timedelta(days=1), cola=7
    )
    # Y uno que SÍ sincronizó y no trae nada: ése sí es «no ha hecho nada».
    quieto = await _vendedor(sesion, semilla, codigo="VEND03", nombre="Lupe Sin Venta")
    await _telefono(sesion, quieto, ultimo_push=datetime.now(UTC))
    await sesion.commit()

    # El sellado del día les pone su renglón en cero, como a cualquier vendedor activo.
    await recalcular_dia(sesion, jornada["dia"])
    await sesion.commit()

    await _entrar(cliente)
    plano = await _ver(cliente)

    assert "sin sincronizar hoy" in plano
    assert "7 operación(es) en cola" in plano
    assert "sin una sola operación" in plano

    # Cada uno DENTRO de su aviso y fuera del otro. Es toda la prueba, y se mide
    # recortando el bloque en vez de comparando posiciones: con posiciones, un
    # código que metiera a Pedro en los DOS avisos seguiría pasando, porque su
    # primera aparición seguiría estando donde se espera. Pasó.
    silencio = plano[
        plano.index("sin una sola operación") : plano.index("El día")
    ]
    sincronizacion = plano[
        plano.index("sin sincronizar hoy") : plano.index("sin una sola operación")
    ]
    assert "Pedro Sin Señal" in sincronizacion
    assert "Pedro Sin Señal" not in silencio, (
        "no sabemos qué ha hecho Pedro: su cero es una ausencia de información, "
        "no un día sin vender"
    )
    assert "Lupe Sin Venta" in silencio
    assert "Lupe Sin Venta" not in sincronizacion


async def test_antes_de_media_manana_el_silencio_no_se_señala(
    cliente, sesion, semilla, jornada, monkeypatch
):
    """A las siete de la mañana todos los renglones están en cero.

    Un aviso que sale todos los días a esa hora enseña a ignorar los avisos, y
    entonces no sirve el día que importa.
    """
    monkeypatch.setattr(
        "app.api.admin.desempeno.HORA_EN_QUE_EL_SILENCIO_YA_NO_SE_EXPLICA", 25
    )
    quieto = await _vendedor(sesion, semilla, codigo="VEND03", nombre="Lupe Sin Venta")
    await _telefono(sesion, quieto, ultimo_push=datetime.now(UTC))
    await sesion.commit()
    await recalcular_dia(sesion, jornada["dia"])
    await sesion.commit()

    await _entrar(cliente)
    plano = await _ver(cliente)

    assert "sin una sola operación" not in plano
    # Y su renglón sigue en la tabla con sus ceros: no se esconde, no se grita.
    assert "Lupe Sin Venta" in plano


async def test_la_frescura_dice_que_las_cifras_son_un_piso(
    cliente, sesion, semilla, jornada
):
    """Un equipo sin sincronizar convierte el total en un mínimo (§0.3)."""
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en, "
            "                          ultima_sync_push_en) "
            "VALUES (:d, :u, 'Tablet olvidada', 'activo', now(), NULL)"
        ),
        {"d": uuid.uuid4(), "u": semilla["admin"]},
    )
    await sesion.commit()

    await _entrar(cliente)
    plano = await _ver(cliente)

    assert "piso" in plano
    assert "sin sincronizar hoy" in plano


async def test_un_dia_sin_calcular_no_se_parece_a_un_dia_sin_ventas(
    cliente, sesion, semilla
):
    """Un worker caído y un día flojo no deben verse igual.

    `SQL_SELLAR_DIA` deja un renglón en cero por cada vendedor activo, así que
    «ningún renglón» solo puede significar que nadie calculó el día.
    """
    await _entrar(cliente)
    plano = await _ver(cliente)

    assert "no tiene ningún renglón calculado" in plano
    assert "el worker del tablero no está corriendo" in plano


# ===========================================================================
# El día que se mira
# ===========================================================================
async def test_un_dia_pasado_no_habla_de_equipos_sin_sincronizar(
    cliente, sesion, semilla, jornada
):
    """La frescura de hoy no dice nada de un día cerrado.

    «2 equipos sin sincronizar» es verdad de AHORA. Pegarlo a las cifras del martes
    pasado sugeriría que ese día está incompleto por esa razón, cuando lo que lo
    completa o no es si alguien subió documentos viejos.
    """
    pasado = dias_de_referencia(jornada["dia"])[0]
    await _historia(sesion, semilla["vendedor"], dias=[pasado], venta=1500)

    await _entrar(cliente)
    plano = await _ver(cliente, pasado)

    assert "Día cerrado" in plano
    assert "$1,500.00" in plano
    assert "piso" not in plano


async def test_el_futuro_se_recorta_a_hoy(cliente, semilla, jornada):
    """`tablero_dia` no tiene renglones de mañana, y una pantalla vacía por eso se
    vería igual que una con el worker caído. Dos cosas que no deben parecerse."""
    manana = jornada["dia"] + timedelta(days=1)
    await _entrar(cliente)
    plano = await _ver(cliente, manana)

    assert "$2,200.00" in plano, "mira hoy, no mañana"


async def test_una_fecha_ilegible_cae_a_hoy(cliente, semilla, jornada):
    await _entrar(cliente)
    r = await cliente.get("/panel/desempeno?fecha=el-martes")
    assert r.status_code == 200
    assert "$2,200.00" in solo_texto(r)


# ===========================================================================
# Quién puede verla
# ===========================================================================
async def test_sin_el_permiso_no_se_entra(cliente, sesion, semilla, jornada):
    """`tablero.ver` lo tienen gerente y supervisor, así que hoy los tres roles del
    panel la pasan. La guarda se prueba con la revocación por persona que el
    sistema ya soporta — no borrando el renglón del rol, que son datos de
    referencia que ninguna prueba vacía."""
    from app.core.seguridad import hashear_password

    identificador = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO usuarios(id, sucursal_id, codigo, nombre, password_hash, "
            "rol_codigo, creado_en, actualizado_en) "
            "VALUES (:id, :s, 'GER01', 'Gerente', :h, 'gerente', now(), now())"
        ),
        {
            "id": identificador,
            "s": semilla["sucursal"],
            "h": hashear_password(PASSWORD_VENDEDOR),
        },
    )
    await sesion.execute(
        text(
            "INSERT INTO usuarios_permisos (usuario_id, permiso_codigo, otorgado) "
            "VALUES (:u, 'tablero.ver', false)"
        ),
        {"u": identificador},
    )
    await sesion.commit()

    await _entrar(cliente, "GER01")
    r = await cliente.get("/panel/desempeno")
    assert r.status_code == 403, r.text


async def test_gerencia_la_ve(cliente, sesion, semilla, jornada):
    from app.core.seguridad import hashear_password

    await sesion.execute(
        text(
            "INSERT INTO usuarios(id, sucursal_id, codigo, nombre, password_hash, "
            "rol_codigo, creado_en, actualizado_en) "
            "VALUES (:id, :s, 'GER01', 'Gerente', :h, 'gerente', now(), now())"
        ),
        {
            "id": uuid.uuid4(),
            "s": semilla["sucursal"],
            "h": hashear_password(PASSWORD_VENDEDOR),
        },
    )
    await sesion.commit()

    await _entrar(cliente, "GER01")
    plano = await _ver(cliente)
    assert "$2,200.00" in plano


async def test_la_pantalla_esta_en_la_navegacion(cliente, semilla, jornada):
    """Una pantalla que no está en la navegación no existe para quien usa el panel."""
    await _entrar(cliente)
    plano = await _ver(cliente)
    assert "Desempeño" in plano


# ===========================================================================
# Lo que encontró la primera captura de pantalla, y ninguna prueba
# ===========================================================================
# Las cuatro pruebas de aquí abajo existen porque la pantalla pasó dieciocho
# pruebas en verde y la primera vez que alguien la MIRÓ con datos decía cosas
# falsas. Todas buscaban las palabras correctas; ninguna buscaba las incorrectas.


async def test_los_dias_se_nombran_en_español(cliente, sesion, semilla, jornada):
    """`strftime('%A')` usa el locale del proceso, y en el contenedor es el C.

    La primera versión decía «3 por debajo de sus tuesdays».
    """
    from app.api.admin.desempeno import DIAS_PLURAL

    await _historia(
        sesion, semilla["vendedor"], dias=dias_de_referencia(jornada["dia"]), venta=2000
    )
    await _entrar(cliente)
    plano = await _ver(cliente)

    assert f"vs. sus {DIAS_PLURAL[jornada['dia'].weekday()]}" in plano
    for en_ingles in (
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
    ):
        assert en_ingles not in plano.lower(), f"«{en_ingles}» en la pantalla"


async def test_el_que_no_sincronizo_no_sale_por_debajo_de_su_promedio(
    cliente, sesion, semilla, jornada, monkeypatch
):
    """La primera versión decía «Pedro: -100% por debajo de sus martes».

    Era exactamente la mentira que la pantalla existe para no decir: el día de
    Pedro sigue en su teléfono, así que su cero no es una caída. Su renglón dice
    «sin datos de hoy» y no entra a la lista de los que van abajo.
    """
    _como_si_fuera_mediodia(monkeypatch)
    pedro = await _vendedor(sesion, semilla, codigo="VEND02", nombre="Pedro Sin Señal")
    await _telefono(sesion, pedro, ultimo_push=datetime.now(UTC) - timedelta(days=1))
    await sesion.commit()
    await recalcular_dia(sesion, jornada["dia"])
    await sesion.commit()
    await _historia(sesion, pedro, dias=dias_de_referencia(jornada["dia"]), venta=1800)

    await _entrar(cliente)
    plano = await _ver(cliente)

    assert "sin datos de hoy" in plano
    assert "-100.0%" not in plano
    assert "por debajo de" not in plano


async def test_el_que_no_hizo_nada_sale_en_un_solo_aviso(
    cliente, sesion, semilla, jornada, monkeypatch
):
    """Lupe sincronizó y no trae nada: va en el aviso del silencio, y solo ahí.

    Está -100% abajo de su promedio, y es verdad, y no agrega nada: repetirla en
    la lista de los que van abajo obliga a quien lee a reconciliar dos listas.
    """
    _como_si_fuera_mediodia(monkeypatch)
    lupe = await _vendedor(sesion, semilla, codigo="VEND03", nombre="Lupe Sin Venta")
    await _telefono(sesion, lupe, ultimo_push=datetime.now(UTC))
    await sesion.commit()
    await recalcular_dia(sesion, jornada["dia"])
    await sesion.commit()
    await _historia(sesion, lupe, dias=dias_de_referencia(jornada["dia"]), venta=3000)

    await _entrar(cliente)
    plano = await _ver(cliente)

    assert "sin una sola operación" in plano
    assert "por debajo de" not in plano


async def test_el_total_con_alguien_sin_sincronizar_es_un_piso_no_una_caida(
    cliente, sesion, semilla, jornada
):
    """Hoy van $2,200 contra un promedio de $6,000 —y falta el día de Pedro—.

    «-63%» pintado de ámbar diría que la operación se cayó, cuando lo único que se
    sabe es que falta información. La cifra se muestra, sin color y con la razón.
    """
    pedro = await _vendedor(sesion, semilla, codigo="VEND02", nombre="Pedro Sin Señal")
    await _telefono(sesion, pedro, ultimo_push=datetime.now(UTC) - timedelta(days=1))
    await sesion.commit()
    await recalcular_dia(sesion, jornada["dia"])
    await sesion.commit()
    await _historia(
        sesion, semilla["vendedor"], dias=dias_de_referencia(jornada["dia"]), venta=6000
    )

    await _entrar(cliente)
    r = await cliente.get("/panel/desempeno")
    plano = solo_texto(r)

    assert "falta el día de 1 vendedor(es)" in plano
    assert "es un piso, no una caída" in plano
    # La tarjeta del total no se pinta de ámbar: se busca la ÚLTIMA tarjeta que
    # abre antes de la etiqueta «vendido», que es la suya.
    antes = r.text[: r.text.index('<div class="etiqueta">vendido</div>')]
    apertura = antes[antes.rindex('<div class="tarjeta') :].split(">")[0]
    assert "aviso" not in apertura, apertura
