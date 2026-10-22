"""Comandos de línea: el primer usuario de oficina, los recálculos, la base en blanco,
los camiones al sábado, las tiendas de las rutas, el mapa de Mazatlán y la base
para empezar a operar.

────────────────────────────────────────────────────────────────────────────
EL PROBLEMA DEL HUEVO Y LA GALLINA
────────────────────────────────────────────────────────────────────────────
Los usuarios se dan de alta en el panel, y al panel se entra con un usuario. Una
base recién migrada no tiene ninguno, así que no hay por dónde empezar.

Hay tres formas de resolverlo y dos son malas:

1. **Sembrar `admin / admin123` en una migración.** Es la que todo el mundo
   escribe. El problema no es el desarrollo: es que esa fila viaja a producción,
   nadie se acuerda de cambiarla, y queda un usuario con todos los permisos cuya
   contraseña está publicada en el repositorio. Con el panel detrás de un túnel
   de Cloudflare, eso es acceso a la operación completa desde internet.

2. **Una pantalla de "primer arranque" sin autenticar.** Funciona, y deja una
   ruta que crea administradores sin credenciales. Basta que alguien reinicie la
   base, o que la condición "¿ya hay usuarios?" se evalúe mal una vez, para que
   esa puerta quede abierta.

3. **Un comando que corre quien tiene acceso al servidor.** Es ésta. Quien puede
   ejecutar esto ya está dentro de la máquina, así que no concede nada nuevo, y
   no deja ninguna fila ni ninguna ruta de más cuando termina.

La contraseña se teclea, no se pasa como argumento: un `--password` queda en el
historial del shell y en la lista de procesos, donde lo ve cualquiera con `ps`.
"""

from __future__ import annotations

import asyncio
import getpass
import math
import sys
import uuid

from sqlalchemy import text

from app.core.db import motor
from app.core.seguridad import hashear_password

# Doce caracteres no es una cifra mágica, es el punto donde una frase corta
# ("camion rojo 14") ya resiste fuerza bruta offline con los parámetros de Argon2
# que usa este sistema. Se exige en vez de advertir: la contraseña del primer
# administrador es la que nadie vuelve a cambiar.
MINIMO_PASSWORD = 12

ROLES_DE_OFICINA = ("admin", "gerente", "supervisor")


async def crear_usuario_de_oficina() -> int:
    """Crea el primer usuario del panel, preguntando lo que hace falta."""
    print("Primer usuario de oficina del panel DSD.")
    print("La contraseña no se muestra ni queda en el historial del shell.\n")

    codigo = input("Código de empleado (ej. ADMIN01): ").strip().upper()
    if not codigo:
        print("ERROR: el código no puede ir vacío: es con lo que se entra.")
        return 1

    nombre = input("Nombre completo: ").strip()
    if not nombre:
        print("ERROR: falta el nombre.")
        return 1

    print(f"\nRoles de oficina: {', '.join(ROLES_DE_OFICINA)}")
    print("  admin       todos los permisos, incluido dar de alta usuarios")
    print("  gerente     SOLO LECTURA sobre la operación")
    print("  supervisor  opera: carga, corte del día, cuarentena")
    rol = (input("Rol [admin]: ").strip() or "admin").lower()
    if rol not in ROLES_DE_OFICINA:
        # El rol 'vendedor' se niega aquí a propósito: un vendedor no entra al
        # panel (se le responde que use la app), así que crearlo con este comando
        # produciría un usuario que no puede usar lo único que este comando
        # desbloquea. Los vendedores se dan de alta en el panel.
        print(f"ERROR: «{rol}» no es un rol de oficina.")
        if rol == "vendedor":
            print("Un vendedor no entra al panel: dalo de alta desde el panel mismo.")
        return 1

    password = getpass.getpass("Contraseña: ")
    if len(password) < MINIMO_PASSWORD:
        print(f"ERROR: mínimo {MINIMO_PASSWORD} caracteres. Una frase corta sirve.")
        return 1
    if password != getpass.getpass("Otra vez: "):
        print("ERROR: las dos contraseñas no coinciden.")
        return 1

    async with motor.begin() as con:
        existe = (
            await con.execute(
                text("SELECT nombre FROM usuarios WHERE codigo = :c"), {"c": codigo}
            )
        ).first()
        if existe is not None:
            print(f"ERROR: el código {codigo} ya lo tiene «{existe[0]}».")
            print("Para cambiarle la contraseña, hazlo desde el panel.")
            return 1

        # Una sucursal tiene que existir: `usuarios.sucursal_id` admite NULL, pero
        # un usuario sin sucursal queda fuera de cualquier reporte que agrupe por
        # ella, y eso se descubre meses después con los números ya mal.
        sucursal = (
            await con.execute(text("SELECT id FROM sucursales ORDER BY codigo LIMIT 1"))
        ).scalar_one_or_none()
        if sucursal is None:
            sucursal = uuid.uuid4()
            await con.execute(
                text(
                    "INSERT INTO sucursales (id, codigo, nombre) "
                    "VALUES (:id, 'MATRIZ', 'Matriz')"
                ),
                {"id": sucursal},
            )
            print("Se creó la sucursal MATRIZ: no había ninguna.")

        await con.execute(
            text(
                "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
                "                      rol_codigo) "
                "VALUES (:id, :s, :c, :n, :h, :r)"
            ),
            {
                "id": uuid.uuid4(),
                "s": sucursal,
                "c": codigo,
                "n": nombre,
                "h": hashear_password(password),
                "r": rol,
            },
        )

    print(f"\nListo: {codigo} ({rol}).")
    print("Entra en http://127.0.0.1:8000/panel")
    return 0


async def refrescar_analitica() -> int:
    """Recalcula el esquema estrella del laboratorio, ahora mismo.

    Para correrlo a mano y para el cron nocturno. No pasa por la cola: refresca
    de frente y espera a que termine, porque quien ejecuta esto quiere saber si
    salió bien — un job encolado devuelve el control de inmediato y el resultado
    aparece en el log del worker media hora después.

    El disparador normal no es éste: al cerrar una liquidación, el panel encola
    el job solo. Esto es para el arranque, para después de restaurar un respaldo,
    y para la noche.
    """
    from app.core.db import CrearSesion
    from app.workers.analitica import refrescar_todo

    print("Recalculando el esquema estrella…")
    async with CrearSesion() as sesion:
        try:
            resultados = await refrescar_todo(sesion)
        except RuntimeError as e:
            print(f"\nERROR: {e}")
            return 1

    for r in resultados:
        print(f"  {r['vista']:20} {r['renglones']:>8,} renglones  {r['duracion_ms']:>6} ms")
    print("\nListo. El laboratorio ya muestra estos datos con su hora al lado.")
    return 0


async def recalcular_tablero() -> int:
    """Recalcula los modelos de lectura del tablero de Gerencia, ahora mismo.

    El disparador normal es la propia sincronización: cada lote aceptado encola
    el job. Esto es para el arranque en frío —cuando el tablero nunca se ha
    calculado y la app diría "no hay cifras"—, para después de restaurar un
    respaldo, y para comprobar a mano que el recálculo funciona sin esperar al
    worker.
    """
    from app.core.db import CrearSesion
    from app.workers.tablero import recalcular_todo

    print("Recalculando el tablero…")
    async with CrearSesion() as sesion:
        resultado = await recalcular_todo(sesion)

    print(f"  {len(resultado['dias'])} día(s) en {resultado['duracion_ms']} ms")
    print(f"  días: {', '.join(resultado['dias'][:10])}")
    if resultado["equipos_sin_sincronizar"]:
        print(
            f"\n  AVISO: {resultado['equipos_sin_sincronizar']} equipo(s) no han "
            "sincronizado hoy. Las cifras del tablero son un piso, no un total."
        )
    if resultado["ops_en_cuarentena"]:
        print(
            f"  AVISO: {resultado['ops_en_cuarentena']} operación(es) en cuarentena, "
            "que no están contadas en ninguna cifra."
        )
    print("\nListo. El tablero móvil ya muestra estas cifras con su hora al lado.")
    return 0


async def base_en_blanco(*, solo_resumen: bool = False, confirmado: bool = False) -> int:
    """Vacía la base —se quedan los usuarios y sus teléfonos— y carga los artículos.

    Lo que se queda y lo que se va está en `app/infra/base_en_blanco.py`. Pide
    escribir una frase para confirmar: no se deshace sin el respaldo.

    `--resumen` solo dice lo que borraría, y `--confirmado` no pregunta: así
    `scripts/base_en_blanco.sh` pregunta ANTES de detener la API, y la página no
    se queda caída mientras alguien decide qué teclear.
    """
    from app.core.db import CrearSesion
    from app.infra.base_en_blanco import (
        PALABRA,
        ArticulosInvalidos,
        leer_articulos,
        limpiar_el_laboratorio,
        lo_que_se_borra,
        lo_que_se_queda,
        poner_en_blanco,
        quien_registra,
    )

    try:
        articulos = leer_articulos()
    except ArticulosInvalidos as e:
        print(f"ERROR en el archivo de artículos: {e}")
        return 1

    async with CrearSesion() as sesion:
        se_va = await lo_que_se_borra(sesion)
        se_queda = await lo_que_se_queda(sesion)
        print("BASE EN BLANCO\n")
        print("Se QUEDAN: " + ", ".join(f"{n} {t}" for t, n in se_queda.items())
              + " (los teléfonos vinculados), con roles, permisos, listas de precios y "
              "motivos.")
        print("Se BORRA todo lo demás, entre ello: "
              + ", ".join(f"{n} {t}" for t, n in se_va.items()) + ".")
        print(f"Se crea la Bodega principal y entran {len(articulos)} artículos con su "
              "precio y su existencia.\n")
        if solo_resumen:
            return 0
        if not confirmado:
            print("Haz el respaldo ANTES:  bash scripts/en_el_servidor.sh respaldar.sh")
            print("Esto no se deshace sin ese respaldo.\n")
            if input(f"Para seguir escribe {PALABRA}: ").strip().upper() != PALABRA:
                print("No se borró nada.")
                return 1

        try:
            resultado = await poner_en_blanco(
                sesion, articulos, quien=await quien_registra(sesion)
            )
        except ArticulosInvalidos as e:
            print(f"ERROR: {e}. No se borró nada.")
            return 1
        problema = await limpiar_el_laboratorio(sesion)

    print(f"\nListo. {resultado.articulos} artículos en {resultado.familias} familias.")
    if problema:
        print(f"OJO: el laboratorio de análisis no se recalculó ({problema}); se pone al "
              "día solo en la noche.")
    if resultado.entrada:
        print(f"Inventario inicial {resultado.entrada}: {resultado.piezas:,} piezas "
              f"{resultado.aviso_inventario}.")
    elif resultado.aviso_inventario:
        print(f"OJO: {resultado.aviso_inventario}")
    print("\nLos teléfonos vinculados olvidan lo de antes en su próxima sincronización")
    print("(con la app al día): entregan su cola, se vacían y bajan lo nuevo.")
    return 0


async def camiones_al_sabado(*, solo_resumen: bool = False, confirmado: bool = False) -> int:
    """Deja los camiones de las rutas 1101–1106 como quedaron el sábado 10 y pone
    los precios verdaderos del Excel de cargas (ADR 0002 §99).

    Qué hace y por qué la bodega no se toca está en `app/infra/camiones_al_sabado.py`.
    `--resumen` solo dice lo que haría; `--confirmado` no pregunta.
    """
    from app.core.db import CrearSesion
    from app.infra.base_en_blanco import ArticulosInvalidos, leer_articulos, quien_registra
    from app.infra.camiones_al_sabado import (
        FECHA,
        PALABRA,
        HojaInvalida,
        aplicar,
        leer_camiones,
        planear,
    )

    try:
        renglones = leer_camiones()
        articulos = leer_articulos()
    except (HojaInvalida, ArticulosInvalidos) as e:
        print(f"ERROR en los archivos: {e}")
        return 1

    async with CrearSesion() as sesion:
        try:
            plan = await planear(sesion, renglones, articulos)
        except HojaInvalida as e:
            print(f"ERROR: {e}")
            return 1
        print(f"CAMIONES AL {FECHA.upper()}\n")
        if plan.faltan_vendedores:
            print("FALTAN estos vendedores (dalos de alta con esa clave): "
                  + ", ".join(plan.faltan_vendedores))
        if plan.no_son_vendedores:
            print("No son vendedores activos: " + ", ".join(plan.no_son_vendedores))
        if plan.articulos_nuevos:
            print("Artículos nuevos: " + ", ".join(
                f"{a.sku} {a.nombre} ({a.familia})" for a in plan.articulos_nuevos))
        if plan.precios:
            print("Precios que cambian en la lista general:")
            for c in plan.precios:
                antes = "sin precio" if c.antes is None else f"${c.antes:,.2f}"
                print(f"  {c.sku} {c.nombre}: {antes} → ${c.despues:,.2f}")
        else:
            print("Los precios de la lista general ya son los del Excel.")
        for aviso in plan.precios_distintos:
            print(f"  OJO: {aviso}")
        if plan.camiones_nuevos:
            print("Se les crea camión a: " + ", ".join(plan.camiones_nuevos))
        print("\nCamión por camión (lo que queda):")
        for codigo, piezas in plan.piezas.items():
            print(f"  {codigo}: {piezas:,.0f} piezas, ${plan.valor[codigo]:,.2f}; "
                  f"{plan.ajustes.get(codigo, 0)} artículo(s) por ajustar")
            if plan.movidos_despues.get(codigo):
                print(f"    OJO: este camión ya movió mercancía después del sábado "
                      f"({plan.movidos_despues[codigo]} movimiento(s)); el ajuste lo deja "
                      "igual al Excel de todos modos.")
        print()
        if not plan.se_puede:
            print("No se cambió nada.")
            return 1
        if solo_resumen:
            return 0
        if not confirmado and input(
            f"Para seguir escribe {PALABRA}: "
        ).strip().upper() != PALABRA:
            print("No se cambió nada.")
            return 1
        try:
            resultado = await aplicar(
                sesion, renglones, quien=await quien_registra(sesion), articulos=articulos
            )
        except HojaInvalida as e:
            await sesion.rollback()
            print(f"ERROR: {e}. No se cambió nada.")
            return 1

    print(f"Listo. {resultado.ajustes} ajuste(s) de conteo"
          + (f" en {len(resultado.operaciones)} operación(es), una por camión "
             f"({', '.join(resultado.operaciones)})" if resultado.operaciones else "")
          + f", {resultado.precios} precio(s), {resultado.articulos_nuevos} artículo(s) "
          f"nuevo(s), {resultado.camiones_nuevos} camión(es) nuevo(s).")
    print("Cada teléfono recibe su camión en su próxima sincronización.")
    return 0


async def tiendas_de_rutas(*, solo_resumen: bool = False, confirmado: bool = False) -> int:
    """Da de alta las 1,834 tiendas de las rutas 1101–1107 con los códigos 100 a
    1933, su ruta y su día de visita (ADR 0002 §120).

    Qué hace está en `app/infra/tiendas_de_rutas.py`. `--resumen` solo dice lo que
    haría; `--confirmado` no pregunta.
    """
    from app.core.db import CrearSesion
    from app.infra.base_en_blanco import quien_registra
    from app.infra.tiendas_de_rutas import (
        PALABRA,
        HojaInvalida,
        aplicar,
        leer_tiendas,
        planear,
        por_ruta,
        resumen_de_dias,
    )

    try:
        tiendas = leer_tiendas()
    except HojaInvalida as e:
        print(f"ERROR en el archivo: {e}")
        return 1

    async with CrearSesion() as sesion:
        plan = await planear(sesion, tiendas)
        print(f"TIENDAS DE LAS RUTAS 1101–1107: {len(tiendas):,} tiendas, códigos "
              f"{tiendas[0].codigo} a {tiendas[-1].codigo}\n")
        for numero, suyas in por_ruta(tiendas).items():
            ruta = plan.rutas.get(numero)
            if ruta is None:
                destino = "ruta por decidir"
            else:
                destino = (f"{'ruta NUEVA ' if ruta.nueva else 'ruta '}{ruta.codigo} "
                           f"«{ruta.nombre}»"
                           + (f", de {ruta.titular}" if ruta.titular else ", SIN vendedor")
                           + ("" if ruta.activa else " (DESACTIVADA)"))
            print(f"  {numero} → {destino}: {len(suyas)} tiendas, códigos "
                  f"{suyas[0].codigo} a {suyas[-1].codigo} ({resumen_de_dias(suyas)})")
        sin_titular = [r.codigo for r in plan.rutas.values() if not r.titular]
        if sin_titular:
            print("\nOJO: sin vendedor, ningún teléfono recibe sus tiendas hasta que le "
                  "asignes titular en Usuarios y rutas: " + ", ".join(sin_titular))
        print(f"\nNuevas: {len(plan.nuevas):,}. Ya estaban: {plan.ya_estaban:,}.")
        for aviso in plan.repetidas_en_el_excel:
            print(f"OJO, repetida en el Excel (se cargan las dos): {aviso}")
        for aviso in plan.parecidas[:20]:
            print(f"OJO: {aviso}")
        if len(plan.parecidas) > 20:
            print(f"OJO: y {len(plan.parecidas) - 20} parecidas más.")
        if not plan.hay_lista_de_precios:
            print("OJO: no hay lista de precios activa: las tiendas entran sin lista.")
        for problema in plan.conflictos[:20] + plan.rutas_dudosas:
            print(f"NO SE PUEDE: {problema}")
        print()
        if not plan.se_puede:
            print("No se cambió nada.")
            return 1
        if solo_resumen:
            return 0
        if not plan.nuevas and not any(r.nueva for r in plan.rutas.values()):
            print("Ya estaban todas. No se cambió nada.")
            return 0
        if not confirmado and input(
            f"Para seguir escribe {PALABRA}: "
        ).strip().upper() != PALABRA:
            print("No se cambió nada.")
            return 1
        try:
            resultado = await aplicar(sesion, tiendas, quien=await quien_registra(sesion))
        except HojaInvalida as e:
            await sesion.rollback()
            print(f"ERROR: {e}. No se cambió nada.")
            return 1

    print(f"Listo. {resultado.tiendas:,} tienda(s) nueva(s)"
          + (f", ruta(s) nueva(s): {', '.join(resultado.rutas_nuevas)}"
             if resultado.rutas_nuevas else "")
          + ".")
    print("Cada teléfono recibe las tiendas de su ruta en su próxima sincronización.")
    return 0


async def ubicar_tiendas(*, solo_resumen: bool = False, confirmado: bool = False) -> int:
    """Pone las coordenadas del Excel LOCALES_MAZATLAN_2 en las tiendas de las rutas
    1101–1107 que no tengan ubicación, y da de alta las que no están (ADR 0002 §148).

    Qué hace está en `app/infra/ubicaciones_de_tiendas.py`. `--resumen` solo dice
    lo que haría; `--confirmado` no pregunta.
    """
    from app.core.db import CrearSesion
    from app.infra.base_en_blanco import quien_registra
    from app.infra.ubicaciones_de_tiendas import (
        PALABRA,
        HojaInvalida,
        aplicar,
        leer_renglones,
        planear,
    )

    try:
        renglones = leer_renglones()
    except HojaInvalida as e:
        print(f"ERROR en el archivo: {e}")
        return 1

    async with CrearSesion() as sesion:
        plan = await planear(sesion, renglones)
        print(f"UBICACIONES DE LAS TIENDAS (LOCALES_MAZATLAN_2): {len(renglones):,} "
              "tiendas, de lunes a sábado\n")
        for ruta, cuenta in plan.por_ruta().items():
            print(f"  {ruta}: {cuenta['con_tienda'] + cuenta['sin_tienda']} en el Excel → "
                  f"{cuenta['ubicar']} se ubican"
                  + (f", {cuenta['nuevas']} nuevas" if cuenta["nuevas"] else ""))
        print(f"\nSe ubican: {len(plan.por_ubicar):,}. Ya estaban: {plan.ya_estaban:,}.")
        for t, numero in plan.numeros:
            print(f"Se corrige el número que Excel volvió fecha: {t} ({t.calle}) "
                  f"{t.numero} → {numero}")
        for aviso in plan.repetidas_en_la_base:
            print(f"OJO, repetida en la base: {aviso}")
        for aviso in plan.en_otra_ruta:
            print(f"OJO: {aviso}")
        for aviso in plan.en_otro_dia:
            print(f"OJO, otro día: {aviso}")
        for aviso in plan.con_otra_ubicacion:
            print(f"Ya tiene otra ubicación, no se toca: {aviso}")
        for r in plan.sin_coordenadas:
            print(f"Sin coordenadas en el Excel: {r} — {r.correccion or 'vienen vacías'}")
        if plan.nuevas:
            print(f"\nNo están en la base y se dan de alta, con visita semanal "
                  f"({len(plan.nuevas)}, códigos {plan.nuevas[0].codigo} a "
                  f"{plan.nuevas[-1].codigo}):")
            for n in plan.nuevas:
                print(f"  {n}")
        if plan.sin_ruta:
            print(f"\nNO se pueden dar de alta: su ruta no existe en el sistema "
                  f"({len(plan.sin_ruta)}). Créala en Usuarios y rutas y vuelve a correrlo:")
            for r in plan.sin_ruta:
                print(f"  {r}")
        if plan.sin_renglon:
            print(f"\nTiendas de estas rutas que no vienen en el Excel: siguen sin "
                  f"ubicación hasta que su vendedor la capture ({len(plan.sin_renglon)}):")
            for t in plan.sin_renglon:
                print(f"  {t} ({t.domicilio}), ruta {t.ruta or 'sin ruta'}")
        print()
        if solo_resumen:
            return 0
        if not plan.por_ubicar and not plan.numeros and not plan.nuevas:
            print("Ya estaban todas. No se cambió nada.")
            return 0
        if not confirmado and input(
            f"Para seguir escribe {PALABRA}: "
        ).strip().upper() != PALABRA:
            print("No se cambió nada.")
            return 1
        resultado = await aplicar(sesion, renglones, quien=await quien_registra(sesion))

    print(f"Listo. {resultado.ubicadas:,} tienda(s) ubicada(s)"
          + (f", {len(resultado.nuevas)} nueva(s) ({resultado.nuevas[0]} a "
             f"{resultado.nuevas[-1]})" if resultado.nuevas else "")
          + (f" y {resultado.numeros} número(s) corregido(s)" if resultado.numeros else "")
          + ".")
    print("Cada teléfono recibe sus tiendas y su ubicación en su próxima sincronización.")
    return 0


async def compra_zuko(*, solo_resumen: bool = False, confirmado: bool = False) -> int:
    """Da de alta los siete ZUKO de la factura del 16/10 en la familia de Maruchan
    y los recibe en la bodega como compra (ADR 0002 §125).

    Qué hace está en `app/infra/compra_zuko.py`. `--resumen` solo dice lo que
    haría; `--confirmado` no pregunta.
    """
    from app.core.db import CrearSesion
    from app.infra.base_en_blanco import quien_registra
    from app.infra.compra_zuko import (
        PALABRA,
        PROVEEDOR,
        REFERENCIA,
        FacturaInvalida,
        aplicar,
        leer_factura,
        planear,
    )

    try:
        renglones = leer_factura()
    except FacturaInvalida as e:
        print(f"ERROR en el archivo: {e}")
        return 1

    piezas = sum(r.cantidad for r in renglones)
    importe = sum(r.importe for r in renglones)
    async with CrearSesion() as sesion:
        plan = await planear(sesion, renglones)
        print(f"COMPRA DE ZUKO: {REFERENCIA}, de {PROVEEDOR}\n")
        for r in renglones:
            marca = "NUEVO" if r in plan.nuevos else "ya estaba"
            print(f"  {r.sku} {r.nombre}: {r.cantidad:,.0f} a ${r.costo:,.2f} = "
                  f"${r.importe:,.2f} ({marca})")
        print(f"\nTotal: {piezas:,.0f} exhibidores, ${importe:,.2f}.")
        if plan.familia:
            print(f"Familia: {plan.familia}. Sin precio de venta: el vendedor no los ve "
                  "hasta que se les ponga en Productos.")
        if plan.bodega:
            print(f"Entran a: {plan.bodega}, como compra (folio EN-).")
        for problema in plan.problemas:
            print(f"NO SE PUEDE: {problema}")
        print()
        if not plan.se_puede:
            print("No se cambió nada.")
            return 1
        if plan.compra_hecha and not plan.nuevos:
            print(f"Ya estaba: la compra es {plan.compra_hecha}. No se cambió nada.")
            return 0
        if solo_resumen:
            return 0
        if not confirmado and input(
            f"Para seguir escribe {PALABRA}: "
        ).strip().upper() != PALABRA:
            print("No se cambió nada.")
            return 1
        try:
            resultado = await aplicar(sesion, renglones, quien=await quien_registra(sesion))
        except FacturaInvalida as e:
            await sesion.rollback()
            print(f"ERROR: {e}. No se cambió nada.")
            return 1

    print(f"Listo. {resultado.articulos_nuevos} artículo(s) nuevo(s) y la compra "
          f"{resultado.folio}: {resultado.piezas:,.0f} exhibidores, ${resultado.importe:,.2f}, "
          "en la bodega.")
    print("Para venderlos en la ruta, ponles precio en el panel, en Productos.")
    return 0


async def mapa_mazatlan(argumentos: list[str]) -> int:
    """Baja de OpenStreetMap el mapa de la zona de las tiendas para los teléfonos
    (ADR 0002 §130).

    Qué hace está en `app/infra/mapa.py`. `--resumen` solo dice qué zona y de
    dónde; `--zoom 14` lo hace más ligero; `--zona oeste,sur,este,norte` y
    `--fuente URL` cambian lo que se calcula solo.
    """
    import argparse
    from datetime import date

    from app.core.db import CrearSesion
    from app.infra import mapa

    lector = argparse.ArgumentParser(prog="python -m app.cli mapa-mazatlan")
    lector.add_argument("--resumen", action="store_true")
    lector.add_argument("--zoom", type=int, default=mapa.ZOOM_MAXIMO, choices=range(10, 16))
    lector.add_argument("--zona")
    lector.add_argument("--fuente")
    # «--zona -106.50,…» empieza con guion y argparse lo tomaría por otra opción:
    # se pega a su nombre, como si se hubiera escrito «--zona=-106.50,…».
    pegados: list[str] = []
    for arg in argumentos:
        if pegados and pegados[-1] == "--zona":
            pegados[-1] = f"--zona={arg}"
        else:
            pegados.append(arg)
    try:
        opciones = lector.parse_args(pegados)
    except SystemExit as e:
        return int(e.code or 0)

    async with CrearSesion() as sesion:
        try:
            if opciones.zona:
                partes = [float(p) for p in opciones.zona.split(",")]
                if len(partes) != 4:
                    raise ValueError
                zona = (partes[0], partes[1], partes[2], partes[3])
                mapa.validar_zona(zona)
                con_ubicacion = None
            else:
                percentiles, con_ubicacion = await mapa.percentiles_de_las_tiendas(sesion)
                zona = mapa.zona_de_las_tiendas(percentiles)
        except ValueError:
            print("ERROR: --zona va como oeste,sur,este,norte, por ejemplo "
                  "-106.50,23.13,-106.28,23.33")
            return 1
        except mapa.MapaInvalido as e:
            print(f"ERROR: {e}")
            return 1
        fuera = await mapa.tiendas_fuera(sesion, zona)

    oeste, sur, este, norte = zona
    ancho = (este - oeste) * 111.32 * math.cos(math.radians((sur + norte) / 2))
    alto = (norte - sur) * 110.57
    print("MAPA DE MAZATLÁN PARA LOS TELÉFONOS\n")
    print(f"  Zona: {oeste}, {sur} a {este}, {norte} (unos {ancho:.0f} × {alto:.0f} km)")
    if con_ubicacion is not None:
        print(f"  Sale de {con_ubicacion:,} tiendas con ubicación, más Mazatlán entero.")
    if fuera:
        print(f"  {fuera} tienda(s) con ubicación quedan FUERA del mapa: revisa sus "
              "coordenadas en Clientes, o da la zona con --zona.")
    print(f"  Hasta el zoom {opciones.zoom} (calle).")
    if opciones.resumen:
        print("\nCon --resumen no se baja nada.")
        return 0

    try:
        fuente = opciones.fuente or mapa.fuente_reciente(date.today())
        print(f"  Fuente: {fuente}\n\nBajando… (unos minutos)")
        directorio = mapa.directorio_de_mapas()
        extraccion = mapa.extraer(
            mapa.lector_http(fuente), zona, directorio / mapa.ARCHIVO,
            zoom_maximo=opciones.zoom, fuente=fuente,
        )
    except mapa.MapaInvalido as e:
        print(f"ERROR: {e}. No se cambió el mapa que había.")
        return 1
    except OSError as e:
        print(f"ERROR al bajar o guardar: {e}. No se cambió el mapa que había.")
        return 1
    version = fuente.rsplit("/", 1)[-1].removesuffix(".pmtiles")
    ficha = mapa.guardar_ficha(directorio, extraccion, version=version, tiendas_fuera=fuera)
    print(f"Listo: {ficha.teselas:,} teselas, {ficha.bytes / 1_000_000:.1f} MB, en "
          f"{extraccion.peticiones} peticiones. Versión {ficha.version}.")
    print("Cada teléfono lo baja con WiFi: lista de tiendas → Mapa → «Bajar el mapa de "
          "Mazatlán».")
    return 0


async def empezar_a_operar(argumentos: list[str]) -> int:
    """Se van los usuarios menos ADMIN01 y la operación queda en cero; el catálogo y
    las tiendas se quedan (ADR 0002 §131).

    Qué hace está en `app/infra/empezar_a_operar.py`. `--resumen` solo dice lo
    que haría; `--confirmado` no pregunta; `--se-queda CODIGO` cambia el
    administrador que se queda.
    """
    from app.core.db import CrearSesion
    from app.infra.empezar_a_operar import (
        PALABRA,
        QUEDA_POR_OMISION,
        NoSePuede,
        aplicar,
        planear,
    )

    opciones = set()
    se_queda = QUEDA_POR_OMISION
    resto = list(argumentos)
    while resto:
        arg = resto.pop(0)
        if arg == "--se-queda" and resto:
            se_queda = resto.pop(0)
        elif arg.startswith("--se-queda="):
            se_queda = arg.split("=", 1)[1]
        elif arg in ("--resumen", "--confirmado"):
            opciones.add(arg)
        else:
            print(f"Opción desconocida: {arg}. Van --resumen, --confirmado y "
                  "--se-queda CODIGO.")
            return 2

    async with CrearSesion() as sesion:
        try:
            plan = await planear(sesion, se_queda)
        except NoSePuede as e:
            print(f"NO SE PUEDE: {e}. No se cambió nada.")
            return 1
        q = plan.se_queda
        print("EMPEZAR A OPERAR: SE VAN LOS USUARIOS, LA OPERACIÓN QUEDA EN CERO\n")
        print(f"Se queda: {q.codigo} «{q.nombre}», con {q.telefonos} teléfono(s) "
              "vinculado(s).")
        print(f"Se van {len(plan.usuarios_que_se_van)} usuario(s) y "
              f"{plan.telefonos_que_se_van} teléfono(s):")
        for codigo, nombre in plan.usuarios_que_se_van:
            print(f"  {codigo} «{nombre}»")
        if plan.camiones:
            print(f"Se van sus {len(plan.camiones)} camión(es): {', '.join(plan.camiones)}.")
        print("Queda en cero:")
        for nombre, cuantos in plan.se_van.items():
            print(f"  {cuantos:>8,} {nombre}")
        print("Se queda:")
        for nombre, cuantos in plan.se_quedan.items():
            print(f"  {cuantos:>8,} {nombre}")
        print(f"  ({plan.tiendas_con_ubicacion:,} tiendas con su ubicación, y todas con su "
              "ruta y su plan de visita; las rutas sin titular y las bodegas vacías.)")
        print("Los folios vuelven a empezar en 1.\n")
        if "--resumen" in opciones:
            print("Con --resumen no se cambió nada.")
            return 0
        if "--confirmado" not in opciones and input(
            f"NO HAY VUELTA ATRÁS. Para seguir escribe {PALABRA}: "
        ).strip().upper() != PALABRA:
            print("No se cambió nada.")
            return 1
        try:
            await aplicar(sesion, plan)
        except NoSePuede as e:
            print(f"ERROR: {e}. No se cambió nada.")
            return 1

    print(f"Listo. Solo queda {q.codigo}, y la operación está en cero.")
    print("Sigue en el panel: da de alta a los usuarios (cada vendedor con su camión), "
          "asígnales su ruta, vincula sus teléfonos y captura el inventario en "
          "Almacén → Entradas → «Inventario inicial».")
    return 0

def main() -> int:
    comandos = {
        "crear-usuario": crear_usuario_de_oficina,
        "refrescar-analitica": refrescar_analitica,
        "recalcular-tablero": recalcular_tablero,
        "base-en-blanco": base_en_blanco,
        "camiones-al-sabado": camiones_al_sabado,
        "tiendas-de-rutas": tiendas_de_rutas,
        "ubicar-tiendas": ubicar_tiendas,
        "compra-zuko": compra_zuko,
        "mapa-mazatlan": mapa_mazatlan,
        "empezar-a-operar": empezar_a_operar,
    }
    if len(sys.argv) < 2 or sys.argv[1] not in comandos:
        print(f"Uso: python -m app.cli {{{'|'.join(comandos)}}}")
        return 2
    comando, opciones = sys.argv[1], set(sys.argv[2:])
    if comando == "mapa-mazatlan":
        return asyncio.run(mapa_mazatlan(sys.argv[2:]))
    if comando == "empezar-a-operar":
        return asyncio.run(empezar_a_operar(sys.argv[2:]))
    if comando in (
        "base-en-blanco", "camiones-al-sabado", "tiendas-de-rutas", "ubicar-tiendas",
        "compra-zuko",
    ) and opciones <= {
        "--resumen", "--confirmado"
    }:
        return asyncio.run(comandos[comando](
            solo_resumen="--resumen" in opciones, confirmado="--confirmado" in opciones,
        ))
    if opciones:
        print(f"«{comando}» no lleva opciones: {' '.join(sorted(opciones))}")
        return 2
    return asyncio.run(comandos[comando]())


if __name__ == "__main__":
    raise SystemExit(main())
