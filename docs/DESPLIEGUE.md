# Despliegue en un VPS

> **¿Vas a desplegar ahora mismo y quieres una receta en lugar de una explicación?**
> Usa [INSTALACION-PASO-A-PASO.md](INSTALACION-PASO-A-PASO.md): la misma
> instalación en secuencia lineal, diciendo en cada paso dónde estás trabajando,
> qué pegar y qué tienes que ver en la pantalla antes de continuar.
>
> Este documento es el **por qué** de cada decisión. Vale leerlo después del
> despliegue, o cuando algo no cuadre y haya que entender el fondo.

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

- **4 GB de RAM.** Es la recomendación, no el mínimo teórico. Abajo está el
  porqué, con los números.
- **2 vCPU**, **40 GB de disco** (la base de un año de operación de una ruta no
  llega a 1 GB; el espacio es para imágenes de Docker y respaldos locales).
- **Ubuntu Server LTS**, que es lo que el `Dockerfile` y los procedimientos
  asumen.
- **Respaldos/snapshots del proveedor activados** — baratos, y son otra red
  además de la de `RESPALDOS.md`. No la sustituyen: ver §6.

Un detalle de ubicación: la latencia a los teléfonos es irrelevante (los sobres de
sincronización son pequeños), pero el panel lo usa la oficina todo el día. Un
datacenter en México o en el sur de EE. UU. se siente mejor que uno en Europa.

### 2 GB o 4 GB: por qué 4

Con el laboratorio analítico encendido, el stack son cinco procesos y uno pesa
tanto como los otros cuatro juntos. Órdenes de magnitud en reposo, medidos sobre
las versiones que fija `uv.lock`:

| Proceso | RAM en reposo |
|---|---|
| `postgres` (PostGIS 17) | 150–350 MB |
| `api` (uvicorn + SQLAlchemy) | 150–250 MB |
| `worker` | 100–200 MB |
| `caddy` | 30–60 MB |
| `analitica` (Streamlit + pandas + pyarrow + numpy + altair) | **350–600 MB** |

Suma en reposo: **800 MB a 1.5 GB**, antes de atender una sola petición y sin
contar el caché de páginas que PostgreSQL necesita para no ir al disco en cada
consulta.

En 2 GB eso **cabe**, y ahí está la trampa: cabe en reposo y se rompe en el peor
momento. Tres cosas lo empujan al límite:

1. **Construir las imágenes.** Son dos, y la de analítica instala pyarrow, pandas
   y numpy. El pico de la construcción es mayor que el de la operación.
2. **Un informe grande en el laboratorio.** pandas materializa el resultado en
   memoria. Una consulta de un mes de una ruta es chica; una mal acotada, no.
3. **El plan de 2 GB de DigitalOcean trae 1 vCPU.** No es solo memoria: con un
   núcleo, una consulta del laboratorio compite por CPU con la API que está
   atendiendo la sincronización de un teléfono.

Y el modo de falla es malo. **Los droplets de DigitalOcean vienen sin swap.** Sin
swap, cuando la memoria se agota el kernel no degrada: mata. Y el OOM killer
escoge por tamaño, así que el candidato natural es PostgreSQL — la base se cae a
media venta por una consulta exploratoria. PostgreSQL se recupera de eso sin
corromper nada, pero la operación se detiene y la causa no se parece a la razón.

La diferencia de precio entre los dos planes es del orden de **12 USD al mes**
(confirma las tarifas vigentes). Es menos que una hora de la madrugada depurando
por qué la base se murió sola.

**Si de todas formas arrancas en 2 GB**, se puede, con dos mitigaciones:

- El techo de memoria del laboratorio ya está puesto en `docker-compose.yml`
  (`mem_limit: 768m`): con él, lo que muere bajo presión es el laboratorio y no la
  base. Se cae la pestaña de quien veía un informe; la operación sigue.
- **Crea swap**, abajo. Deja de ser opcional en 2 GB.

DigitalOcean permite **redimensionar la RAM de un droplet sin perder el disco**, y
es reversible: se apaga, se cambia de plan, se enciende. Así que empezar en 2 GB no
es una puerta cerrada. Pero hazlo sabiendo que el momento en que te vas a enterar
de que era poco es el que peor te queda.

### Swap: obligatorio en 2 GB, recomendable en 4

Los droplets no traen. Una sola vez, en el servidor:

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
# Que solo se use bajo presión real, no por costumbre:
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swap.conf
sudo sysctl --system
free -h      # debe aparecer la línea Swap con 2.0Gi
```

Esto va **antes** del primer `docker compose up -d`, porque el pico de construir
las imágenes es justo donde hace falta. El swap no hace rápido lo que no cabe:
convierte «un proceso murió» en «esto va lento», que es una falla con la que se
puede trabajar.

### Al crear el droplet, en el panel de DigitalOcean

Cosas que es barato decidir ahora y caro cambiar después:

- **Autenticación: llave SSH, no contraseña.** Pégala al crear el droplet. El §3 de
  abajo deshabilita el acceso por contraseña; si arrancas con contraseña, hay un
  rato en que el servidor está en internet aceptándola, y los escáneres tardan
  minutos en encontrarlo.
- **Región: NYC o SFO.** DigitalOcean no tiene datacenter en México. Da igual para
  los teléfonos, importa para el panel que la oficina usa todo el día.
- **Reserved IP** (Networking → Reserved IPs), y apunta el DNS **a ella**, no a la
  IP del droplet. Es gratis mientras esté asignada y te deja reconstruir o
  reemplazar la máquina sin tocar el DNS ni recompilar el APK — y la dirección del
  servidor en la app es de tiempo de compilación, así que eso último importa.
- **Backups activados** (≈20 % del costo). No sustituyen a `RESPALDOS.md`: un
  snapshot te devuelve la máquina, no un `pg_dump` verificado. Ver §7.
- **Monitoring activado.** Es gratis y es lo que te va a avisar de la memoria antes
  de que el OOM killer te avise a su manera. Pon una alerta de memoria al 85 %.
- **Hostname reconocible**, no `ubuntu-s-1vcpu-2gb-nyc3-01`. Aparece en el prompt y
  en los correos de alerta.
- **Toma un snapshot cuando el despliegue quede verde**, antes de capturar datos
  reales. Es tu punto de retorno si el simulacro te obliga a empezar de cero.

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

### El cortafuegos de nube: la defensa que Docker NO puede saltar

Esto es específico de DigitalOcean y vale más que el `ufw` de arriba.

La trampa que acabas de leer existe porque ufw y Docker escriben en la **misma**
tabla de iptables, dentro de la misma máquina, y Docker escribe primero. Un
**Cloud Firewall** de DigitalOcean no vive dentro del droplet: filtra en la red del
proveedor, antes de llegar. Docker no tiene manera de pasarlo por encima, porque no
puede escribir ahí.

O sea: el cortafuegos de nube es el que de verdad te protege del `ports:` que
alguien agregue «un ratito para depurar». Configúralo en el panel de
DigitalOcean —**Networking → Firewalls**— y aplícalo al droplet:

| Dirección | Regla |
|---|---|
| Entrante | TCP **22** — SSH. Si tienes IP fija en la oficina, limítalo a ella |
| Entrante | TCP **80** — el desafío de Let's Encrypt |
| Entrante | TCP **443** — la API y el panel |
| Saliente | Todo (el servidor necesita salir: apt, Docker Hub, Let's Encrypt) |

Nada más. Ni 5432, ni 8000, ni 8501.

**Conserva ufw de todas formas.** Son dos capas y fallan distinto: el de nube te
cubre de los errores dentro de la máquina, y ufw te cubre si algún día mueves el
servidor a un proveedor sin cortafuegos de nube, o si alguien borra la regla del
panel. `make servidor-revisar` solo puede ver ufw, así que seguirlo teniendo bien
configurado es lo que mantiene útil esa revisión.

> Si limitas el 22 a la IP de tu oficina y esa IP cambia, te quedas fuera del
> servidor. DigitalOcean tiene **consola web** (Access → Launch Droplet Console),
> que entra sin pasar por SSH ni por el cortafuegos. Compruébala **antes** de
> cerrar el 22, no después.

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

> **Antes de esto, el swap de §1 tiene que estar puesto** (`free -h` debe mostrar
> la línea Swap). El pico de memoria de construir las dos imágenes —la de analítica
> instala pyarrow, pandas y numpy— es mayor que el de operar, y es el momento más
> probable de un OOM en una máquina chica.

```bash
docker compose up -d
docker compose logs -f caddy      # para ver emitirse los certificados
```

En 1 vCPU, construir las dos imágenes puede tardar de 10 a 25 minutos. No es que
esté colgado.

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
