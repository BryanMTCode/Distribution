"""Comandos de línea. Hoy uno: crear el primer usuario de oficina.

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
    print("  supervisor  opera: carga, liquidación, cuarentena, crédito")
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


def main() -> int:
    comandos = {"crear-usuario": crear_usuario_de_oficina}
    if len(sys.argv) != 2 or sys.argv[1] not in comandos:
        print(f"Uso: python -m app.cli {{{'|'.join(comandos)}}}")
        return 2
    return asyncio.run(comandos[sys.argv[1]]())


if __name__ == "__main__":
    raise SystemExit(main())
