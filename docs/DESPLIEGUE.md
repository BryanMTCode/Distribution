# Despliegue en un VPS

El servidor de producción vive en un **VPS en la nube**, no en una mini PC en la
oficina. Esto es el procedimiento completo, de cero a operando.

> **Antes vivía en `ENTORNO-WINDOWS.md` §5**, pensado para una mini PC con UPS y
> túnel de Cloudflare. El cambio a VPS no toca una línea de código —la app apunta
> a un hostname fijado al compilar y Caddy resuelve TLS solo— pero sí cambia tres
> decisiones de infraestructura y una de seguridad. Las razones están en
> [ADR 0002 §47](adr/0002-reglas-de-negocio.md).

## 0. Qué cambia respecto de un servidor en la oficina

Vale leerlo antes de empezar, porque hay una cosa que se pone peor y conviene que
sea a sabiendas.

| | En la oficina | En un VPS |
|---|---|---|
| **UPS / no-break** | Obligatorio | Deja de ser tu problema |
| **El disco se muere** | Riesgo #1 de `RESPALDOS.md` | Del proveedor (pero un snapshot **no** es un respaldo) |
| **Túnel de Cloudflare** | Necesario: sin IP fija ni puertos | Opcional: hay IP pública y Caddy hace el TLS |
| **Cifrado del disco** | LUKS + TPM, con su procedimiento | Se replantea: ver `SEGURIDAD-OPERATIVA.md` §5.1 |
| **Expuesto a internet** | No: cero puertos abiertos | **Sí: IP pública, escaneada todo el día** |
| **La oficina sin internet** | Sigue operando en red local | **No puede hacer nada** |
| **Costo** | ~$250 USD una vez | ~$25–40 USD al mes |

Las dos últimas filas son las que importan.

**La oficina deja de poder operar sin internet.** Hoy no se nota porque no hay
servidor; cuando lo haya, el momento en que duele es específico: a las 6 de la
mañana, cuando hay que capturar y confirmar la carga para que el camión salga. Con
el servidor en la oficina eso funciona aunque el enlace esté caído. Con un VPS, no.

Se mitiga de dos formas, y conviene tener las dos:

1. **Failover 4G en el router de la oficina** — `ARQUITECTURA.md` §1.4 ya lo pedía.
2. **Capturar la carga la noche anterior**, que además es mejor práctica: a las 6
   am nadie quiere estar capturando quince renglones.

**Y el servidor queda en internet abierto.** Eso es nuevo y es la mayor parte de
lo que hay que configurar abajo.

---

## 1. El VPS

Lo que hace falta:

- **4 GB de RAM** bastan para PostgreSQL + API + worker + Streamlit con 8–10
  teléfonos. Con 8 GB vas sobrado y no lo vuelves a pensar.
- **2 vCPU**, **40 GB de disco** (la base de un año de operación de una ruta no
  llega a 1 GB; el espacio es para imágenes de Docker y respaldos locales).
- **Ubuntu Server LTS**, que es lo que el `Dockerfile` y los procedimientos
  asumen.
- **Respaldos/snapshots del proveedor activados** — baratos, y son otra red
  además de la de `RESPALDOS.md`. No la sustituyen: ver §6.

Un detalle de ubicación: la latencia a los teléfonos es irrelevante (los sobres de
sincronización son pequeños), pero el panel lo usa la oficina todo el día. Un
datacenter en México o en el sur de EE. UU. se siente mejor que uno en Europa.

## 2. Antes de tocar el servidor: el DNS

Caddy pide el certificado a Let's Encrypt validando que el nombre **ya resuelve** a
este servidor. Así que el DNS va primero:

```
api.tudominio.com         A    <IP del VPS>
analitica.tudominio.com   A    <IP del VPS>
```

Espera a que resuelvan (`dig +short api.tudominio.com`) antes de levantar el
stack. Si levantas antes, Caddy falla al emitir el certificado y hay que
reintentar — y Let's Encrypt tiene límites de intentos.

## 3. El usuario y el acceso

**Nunca se trabaja como root, y SSH nunca acepta contraseñas.** Con IP pública,
los intentos de fuerza bruta empiezan a los minutos de que la máquina existe.

```bash
# Como root, la primera vez:
adduser dsd
usermod -aG sudo dsd
rsync --archive --chown=dsd:dsd ~/.ssh /home/dsd     # copia tu llave
```

Y endurecer SSH con un archivo propio, sin editar el `sshd_config` del sistema:

```bash
cat <<'EOF' | sudo tee /etc/ssh/sshd_config.d/99-dsd.conf
PasswordAuthentication no
PermitRootLogin no
EOF
sudo systemctl restart ssh
```

> **Abre otra terminal y comprueba que puedes entrar antes de cerrar esta.** Si
> algo quedó mal, con la sesión abierta lo arreglas; sin ella, te quedas fuera y
> hay que entrar por la consola del proveedor.

El orden importa y es contraintuitivo: Ubuntu pone `Include
/etc/ssh/sshd_config.d/*.conf` **arriba** de `sshd_config`, y en OpenSSH **gana el
primer valor obtenido**. Por eso un archivo en `.d/` endurece sobre el archivo
principal y no al revés.

## 4. El cortafuegos, y la trampa de Docker

```bash
sudo apt update && sudo apt install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp     # SSH
sudo ufw allow 80/tcp     # Caddy, para el desafío de Let's Encrypt
sudo ufw allow 443/tcp    # Caddy
sudo ufw enable
```

**Y ahora la trampa, que es la que deja bases de datos públicas sin que nadie lo
note:** Docker escribe sus **propias** reglas de iptables, por delante de las de
ufw. Un `ports:` en `docker-compose.yml` queda abierto a internet **aunque ufw
diga que ese puerto está cerrado**, y `ufw status` no lo menciona.

El `docker-compose.yml` de este proyecto publica **solo 80 y 443** a propósito: la
base, la API y el laboratorio viven en la red interna de compose y todo entra por
Caddy. Si alguna vez le agregas un `ports:` a `postgres` «un ratito para
depurar», esa base queda expuesta a internet hasta que lo quites.

`make servidor-revisar` lo comprueba leyendo el compose, y hay una prueba en la
suite que se pone roja si alguien publica un puerto que no sea 80 o 443.

## 5. Instalar Docker y desplegar

```bash
# Docker, del repositorio oficial (el de Ubuntu suele ir atrás)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker dsd        # cerrar sesión y volver a entrar

# La zona horaria de la OPERACIÓN, no UTC. Las imágenes de VPS vienen en UTC.
sudo timedatectl set-timezone America/Mexico_City

git clone https://github.com/BryanMTCode/Distribution.git
cd Distribution
cp .env.example .env
chmod 600 .env
```

Rellena el `.env`. Las cuatro claves, **en hexadecimal** porque las tres de
PostgreSQL van dentro de una URL de conexión:

```bash
openssl rand -hex 32    # DSD_DB_PASSWORD
openssl rand -hex 32    # DSD_JWT_SECRETO
openssl rand -hex 32    # DSD_CLAVE_API         ← el rol que hace que RLS sirva
openssl rand -hex 32    # DSD_CLAVE_ANALITICA   ← el rol de solo lectura
```

Más `DSD_DOMINIO_API` y `DSD_DOMINIO_ANALITICA` con los nombres del paso 2.
**`DSD_TUNNEL_TOKEN` se deja vacío**: en un VPS no hace falta, y el servicio del
túnel va detrás de un perfil que `docker compose up -d` no levanta.

Y arriba:

```bash
docker compose up -d
docker compose logs -f caddy      # para ver emitirse los certificados
```

Levanta en este orden, y cada paso espera al anterior:

| | Servicio | Qué hace |
|---|---|---|
| 1 | `postgres` | La base, sin puertos publicados |
| 2 | `migraciones` | `alembic upgrade head` — las 28 migraciones |
| 3 | `roles` | Crea `dsd_api` y `dsd_analitica` desde `db/ops/` |
| 4 | `api`, `worker`, `analitica` | La operación |
| 5 | `caddy` | TLS y la puerta de entrada |

El paso 3 es el que no existía hasta hace poco: `api` se conecta como `dsd_api` y
sin ese servicio se quedaría reiniciándose con «role does not exist», con
PostgreSQL arriba y migrado.

## 6. Comprobar que quedó bien

```bash
make servidor-revisar
```

Revisa el cortafuegos, SSH, los puertos publicados, la zona horaria, los permisos
del `.env` y el estado del cifrado del disco. **Sale con error si algo de eso está
mal**, y dice qué comando lo arregla.

Y la salud de la aplicación. `api` no publica puertos, así que no se consulta con
`curl` desde el servidor: se pregunta desde dentro, igual que hace su propio
`healthcheck`.

```bash
docker compose ps        # migraciones y roles en exited (0); api en healthy

docker compose exec api python -c \
  "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/salud').read().decode())"

# Y desde fuera, que es lo que de verdad importa:
curl -s https://api.tudominio.com/salud
```

Lo que importa de la respuesta:

```json
{ "ok": true, "base_de_datos": true, "postgis": true, "rls": true }
```

**`"rls": false` significa que la API se está conectando con el rol dueño y las
políticas por renglón están escritas y sin efecto** — un teléfono podría ver la
cartera completa. Revisa `DSD_CLAVE_API` y `docker compose logs roles`.

### La prueba del reinicio

```bash
sudo reboot
```

El proveedor va a reiniciar esta máquina por mantenimiento del host, sin avisarte
y probablemente de noche. Así que pruébalo tú primero: tiene que volver sola y
`/salud` responder sin que nadie toque nada. Si pide algo —una frase de disco, un
servicio que no arranca solo— es mejor descubrirlo ahora.

## 7. Los respaldos, y el fallo nuevo que trae el VPS

`RESPALDOS.md` sigue aplicando tal cual, con un cambio en el razonamiento.

Su riesgo #1 era «el disco de la mini PC muere». Eso ahora lo cubre el proveedor.
Pero aparece uno que la oficina no tenía:

> **Pierdes la cuenta del proveedor y pierdes el servidor y los respaldos al mismo
> tiempo.** Una suspensión por un cargo rechazado, una cuenta comprometida, un
> proveedor que cierra.

Por eso **el respaldo fuera del edificio sigue siendo fuera del PROVEEDOR**: un
respaldo en el object storage del mismo proveedor no cuenta. Si el VPS está en un
sitio, los respaldos van a otro.

Y los snapshots del proveedor **no son respaldos**: un `DELETE` sin `WHERE` a las
once de la noche se replica al snapshot de esa noche, y no hay nada ahí que
verifique que la base se puede restaurar. Para eso está `make simulacro`.

## 8. Después del despliegue, y antes del primer vendedor

1. **Crear el primer usuario** — no hay ninguno sembrado, y es a propósito:

   ```bash
   docker compose exec api python -m app.cli crear-usuario
   ```

2. **Programar el respaldo y correr el simulacro** — `RESPALDOS.md`. Un respaldo
   que nunca restauraste no es un respaldo.
3. **La prueba del reinicio** del paso 6, si no la hiciste.
4. **La revisión del día −1 del piloto** — `PILOTO.md`. El script necesita `psql` y
   la base, que no publica puerto, así que se corre desde dentro de la red:

   ```bash
   docker compose run --rm --no-deps --entrypoint bash \
     -v .:/repo \
     -e DSD_DATABASE_URL="postgresql://dsd:$DSD_DB_PASSWORD@postgres:5432/dsd" \
     postgres /repo/scripts/piloto_listo.sh VEND01
   ```

   Se monta el repositorio completo y no solo `scripts/`: el script compara la
   migración aplicada contra la última de `server/db/alembic/versions/`.

## 9. Si algún día lo mueves a la oficina

No es una migración, es una restauración: `RESPALDOS.md` §6 completo, más levantar
el túnel con `docker compose --profile tunel up -d` y volver a apuntar
`DSD_BASE_URL` al compilar el APK. Vale tenerlo presente al elegir el dominio:
**si los nombres son tuyos y no del proveedor, la mudanza es un cambio de DNS** y
los teléfonos no se enteran.
