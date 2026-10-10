"""Comandos de línea: el primer usuario de oficina, los recálculos y la base en blanco.

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
    print("  supervisor  opera: carga, corte del día, cuarentena, transferencias")
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


def main() -> int:
    comandos = {
        "crear-usuario": crear_usuario_de_oficina,
        "refrescar-analitica": refrescar_analitica,
        "recalcular-tablero": recalcular_tablero,
        "base-en-blanco": base_en_blanco,
    }
    if len(sys.argv) < 2 or sys.argv[1] not in comandos:
        print(f"Uso: python -m app.cli {{{'|'.join(comandos)}}}")
        return 2
    comando, opciones = sys.argv[1], set(sys.argv[2:])
    if comando == "base-en-blanco" and opciones <= {"--resumen", "--confirmado"}:
        return asyncio.run(base_en_blanco(
            solo_resumen="--resumen" in opciones, confirmado="--confirmado" in opciones,
        ))
    if opciones:
        print(f"«{comando}» no lleva opciones: {' '.join(sorted(opciones))}")
        return 2
    return asyncio.run(comandos[comando]())


if __name__ == "__main__":
    raise SystemExit(main())
