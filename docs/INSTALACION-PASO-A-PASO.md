# Instalación paso a paso: del dominio al primer usuario

Receta lineal. **No te saltes pasos ni los reordenes**: varios dependen del
anterior de una forma que no se ve (el certificado TLS necesita que el DNS ya
resuelva; la API necesita un rol de PostgreSQL que crea otro contenedor).

Cada paso dice tres cosas:

- **DÓNDE** estás trabajando. Cambia de ventana cuando el paso lo diga.
- Los **comandos exactos**, listos para copiar y pegar.
- **✅ Qué debes ver** antes de pasar al siguiente, y **⚠️ qué hacer si no.**

Si un paso no da lo que dice el ✅, **para ahí**. Seguir adelante con un paso a
medias es lo que convierte un problema de diez minutos en una noche.

Este documento es el **cómo**. El **por qué** de cada decisión está en
[DESPLIEGUE.md](DESPLIEGUE.md), y vale leerlo después, no durante.

> **Convención:** donde diga `tudominio.com`, pon tu dominio. Donde diga
> `IP_DEL_SERVIDOR`, pon la IP del paso 4. Si pegas un comando con esos textos
> literales, no va a funcionar y el error no te lo va a decir claramente.

---

## Paso 0 · Lo que necesitas antes de empezar

**DÓNDE:** en tu silla, antes de abrir nada.

- [ ] Un **dominio** comprado (Namecheap, GoDaddy, Cloudflare Registrar…). Si no
      lo tienes, cómpralo ahora: la propagación del DNS tarda y es el único paso
      que no puedes apurar.
- [ ] Una **tarjeta** para DigitalOcean.
- [ ] Una o dos horas sin interrupciones. La construcción de las imágenes sola
      tarda de 10 a 25 minutos.

> **Por qué el dominio tiene que ser tuyo y no del proveedor:** la dirección del
> servidor queda **grabada en el APK al compilarlo**. Si algún día cambias de
> servidor y el nombre es tuyo, mueves un registro DNS y los teléfonos no se
> enteran. Si el nombre era del proveedor, hay que recompilar e reinstalar la app
> en cada teléfono.

---

## Paso 1 · Tu llave SSH

**DÓNDE:** en tu **WSL (Ubuntu)**, la terminal donde trabajas normalmente.

Trabajaremos desde WSL todo el tiempo, no desde PowerShell: ahí ya tienes `ssh`,
`dig` y `curl` y se comportan igual que en el servidor.

```bash
ls -la ~/.ssh/id_ed25519.pub
```

**✅ Si el archivo existe**, ya tienes llave. Pasa al final del paso.

**Si dice «No such file or directory»**, créala:

```bash
ssh-keygen -t ed25519 -C "bryan-dsd"
```

Dale Enter a las tres preguntas (ubicación por omisión, y contraseña vacía para
que no te la pida en cada conexión).

Ahora muestra la llave **pública** y cópiala completa:

```bash
cat ~/.ssh/id_ed25519.pub
```

**✅ Debes ver** una sola línea que empieza con `ssh-ed25519 AAAA…` y termina en
`bryan-dsd`. Cópiala al portapapeles: la vas a pegar en el paso 3.

> ⚠️ **Nunca** copies `id_ed25519` (sin `.pub`). Esa es la llave privada y no sale
> de tu máquina nunca.

---

## Paso 2 · Crea la cuenta de DigitalOcean

**DÓNDE:** en la web, `https://www.digitalocean.com`.

Regístrate y agrega el método de pago. Nada más por ahora.

---

## Paso 3 · Crea el droplet

**DÓNDE:** en la web de DigitalOcean → **Create → Droplets**.

Rellena exactamente así:

| Campo | Valor |
|---|---|
| **Region** | **New York** o **San Francisco** (no hay datacenter en México) |
| **Image** | **Ubuntu 24.04 (LTS) x64** |
| **Droplet type** | Basic |
| **CPU options** | Regular (SSD) |
| **Plan** | **4 GB RAM / 2 vCPU** (recomendado). 2 GB / 1 vCPU funciona, pero lee §1 de DESPLIEGUE.md antes |
| **Authentication** | **SSH Key** → *New SSH Key* → pega la llave del paso 1 |
| **Backups** | **Activado** |
| **Monitoring** | **Activado** |
| **Hostname** | `dsd-produccion` |

Y pulsa **Create Droplet**.

**✅ Debes ver**, al cabo de un minuto, el droplet en la lista con un punto verde
y una IP pública.

> ⚠️ **No elijas «Password» en Authentication.** Con contraseña, el servidor queda
> en internet aceptándola, y los escáneres automáticos lo encuentran en minutos.
> Si ya lo creaste con contraseña, bórralo y créalo de nuevo con la llave: es más
> rápido que arreglarlo.

---

## Paso 4 · Reserved IP

**DÓNDE:** en la web de DigitalOcean → **Networking → Reserved IPs**.

Asigna una Reserved IP a `dsd-produccion`.

**✅ Debes ver** una IP nueva asignada al droplet. **Esa** es tu
`IP_DEL_SERVIDOR` de aquí en adelante — no la IP original del droplet.

Apúntala:

```
IP_DEL_SERVIDOR = ____________________
```

> **Por qué:** te deja destruir y reconstruir la máquina sin tocar el DNS ni
> recompilar el APK. Es gratis mientras esté asignada.

---

## Paso 5 · El cortafuegos de nube

**DÓNDE:** en la web de DigitalOcean → **Networking → Firewalls → Create Firewall**.

- **Name:** `dsd-perimetro`
- **Inbound Rules** — borra lo que traiga por omisión y deja exactamente tres:

| Type | Protocol | Port Range | Sources |
|---|---|---|---|
| SSH | TCP | 22 | All IPv4, All IPv6 |
| HTTP | TCP | 80 | All IPv4, All IPv6 |
| HTTPS | TCP | 443 | All IPv4, All IPv6 |

- **Outbound Rules:** déjalas como vienen (todo permitido).
- **Apply to Droplets:** `dsd-produccion`.

**✅ Debes ver** el firewall con «1 Droplet» y las tres reglas.

> **Por qué este cortafuegos y no solo el del servidor:** Docker escribe sus
> propias reglas de iptables **por delante** de las de `ufw`, así que un puerto
> publicado por error queda abierto a internet aunque `ufw status` diga que está
> cerrado. Este cortafuegos vive **fuera** de la máquina: Docker no puede pasarlo
> por encima. Más adelante configuramos `ufw` también, como segunda capa.

---

## Paso 6 · La alerta de memoria

**DÓNDE:** en la web de DigitalOcean → **Monitoring → Alert Policies → Create**.

- Métrica: **Memory utilization**
- Condición: **is above 85 %** durante **5 minutes**
- Aplicar a: `dsd-produccion`
- Notificar: tu correo

**✅ Debes ver** la política creada y activa.

---

## Paso 7 · Apunta el dominio

**DÓNDE:** en la web de tu registrador de dominios (o de Cloudflare, si lo
administras ahí).

Crea **dos registros tipo A**, los dos apuntando a tu `IP_DEL_SERVIDOR`:

| Tipo | Nombre | Valor | TTL |
|---|---|---|---|
| A | `api` | `IP_DEL_SERVIDOR` | automático / 300 |
| A | `analitica` | `IP_DEL_SERVIDOR` | automático / 300 |

> ⚠️ **Si usas Cloudflare**, pon el ícono de la nube en **gris (DNS only)**, no
> naranja. Con la nube naranja, Cloudflare interpone su propio proxy y Let's
> Encrypt no puede validar el dominio contra tu servidor: el certificado no se
> emite y el error no dice eso.

---

## Paso 8 · 🚦 PUERTA: verifica el DNS antes de seguir

**DÓNDE:** en tu **WSL**.

Este es el paso que más gente se salta y es el que más caro sale.

```bash
dig +short api.tudominio.com
dig +short analitica.tudominio.com
```

**✅ Debes ver** tu `IP_DEL_SERVIDOR` impresa dos veces, una por comando.

**⚠️ Si no imprime nada, o imprime otra IP: NO SIGAS.** Espera y repite. La
propagación suele tardar de 2 a 30 minutos, a veces más. Pon un temporizador y
tómate un café.

> **Por qué es una puerta:** Caddy pide el certificado a Let's Encrypt, que valida
> conectándose al nombre para comprobar que llega a este servidor. Si levantas el
> sistema antes de que el DNS resuelva, la emisión falla — y Let's Encrypt
> **limita los reintentos por dominio y hora**. Un paso apurado aquí te puede
> dejar sin poder reintentar hasta mañana.

---

## Paso 9 · Primer acceso al servidor

**DÓNDE:** en tu **WSL**.

```bash
ssh root@IP_DEL_SERVIDOR
```

La primera vez pregunta si confías en la huella del servidor: escribe `yes`.

**✅ Debes ver** un prompt que diga `root@dsd-produccion:~#`.

**⚠️ Si dice `Connection timed out`:**
1. Revisa que el cortafuegos del paso 5 tenga el puerto 22 abierto.
2. Si sigue, entra por la **consola web**: en DigitalOcean, el droplet →
   **Access → Launch Droplet Console**. Eso entra sin pasar por SSH ni por el
   cortafuegos, y te sirve para cualquier problema de acceso de aquí en adelante.
   **Pruébala ahora aunque el SSH funcione**, para saber que la tienes.

**⚠️ Si dice `Permission denied (publickey)`:** la llave que pegaste en el paso 3
no corresponde a la de tu WSL. Agrega la correcta en el panel de DigitalOcean
(Settings → Security → SSH Keys) y destruye y recrea el droplet, o entra por la
consola web y pega la llave a mano en `/root/.ssh/authorized_keys`.

---

## Paso 10 · Actualiza el sistema y pon la hora

**DÓNDE:** **dentro del servidor**, como `root` (el prompt dice `root@`).

```bash
apt update && apt upgrade -y
apt install -y git
timedatectl set-timezone America/Mexico_City
```

Comprueba:

```bash
timedatectl | grep "Time zone"
```

**✅ Debes ver:** `Time zone: America/Mexico_City (CST, -0600)`

> **Por qué importa tanto la hora:** `CURRENT_DATE` decide qué es «hoy». Con el
> reloj en UTC —como vienen las imágenes de VPS— a partir de las 18:00 locales
> «hoy» ya es mañana: el tablero del día sale vacío por la tarde y el arqueo del
> camión compara el papel de un día contra las ventas de otro. Desconcierta porque
> a las 11 de la mañana todo funciona bien.

---

## Paso 11 · Crea tu usuario de trabajo

**DÓNDE:** **dentro del servidor**, como `root`.

```bash
adduser dsd
```

Te pide una contraseña (ponla y guárdala: la vas a necesitar para `sudo`) y luego
varios datos opcionales — Enter a todos.

```bash
usermod -aG sudo dsd
rsync --archive --chown=dsd:dsd ~/.ssh /home/dsd
```

**✅ Comprueba** que la llave quedó copiada:

```bash
ls -l /home/dsd/.ssh/authorized_keys
```

Debe existir y pertenecer a `dsd dsd`.

---

## Paso 12 · 🚦 PUERTA: entra como `dsd` sin cerrar esta sesión

**DÓNDE:** en una **SEGUNDA terminal de WSL**. Abre una ventana nueva y **deja la
sesión de `root` abierta** en la primera.

```bash
ssh dsd@IP_DEL_SERVIDOR
```

**✅ Debes ver** el prompt `dsd@dsd-produccion:~$`.

**⚠️ Si no entra:** vuelve a la terminal de `root` —que sigue abierta, por eso lo
hacemos así— y revisa el paso 11. Mientras tengas esa sesión viva, cualquier error
es reparable.

---

## Paso 13 · Cierra SSH a las contraseñas

**DÓNDE:** **dentro del servidor**, en la terminal de **`dsd`** (la segunda).

```bash
cat <<'EOF' | sudo tee /etc/ssh/sshd_config.d/99-dsd.conf
PasswordAuthentication no
PermitRootLogin no
EOF
sudo systemctl restart ssh
```

**✅ Debes ver** impresas las dos líneas que acabas de escribir.

---

## Paso 14 · 🚦 PUERTA: comprueba que todavía entras

**DÓNDE:** en una **TERCERA terminal de WSL**. No cierres las otras dos.

```bash
ssh dsd@IP_DEL_SERVIDOR
```

**✅ Debes ver** el prompt de `dsd`.

**⚠️ Si no entra**, usa la terminal de `root` del paso 9 —que sigue abierta— y
borra el archivo:

```bash
rm /etc/ssh/sshd_config.d/99-dsd.conf && systemctl restart ssh
```

Cuando esto funcione, ya puedes **cerrar la terminal de `root`** (`exit`). De aquí
en adelante todo es como `dsd`.

> **Por qué un archivo en `sshd_config.d/` y no editar el principal:** Ubuntu pone
> `Include /etc/ssh/sshd_config.d/*.conf` **arriba** del `sshd_config`, y OpenSSH
> se queda con **el primer valor que encuentra**. Así que el archivo nuevo endurece
> sobre el principal, y no al revés.

---

## Paso 15 · El swap

**DÓNDE:** **dentro del servidor**, como `dsd`.

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swap.conf
sudo sysctl --system
```

Comprueba:

```bash
free -h
```

**✅ Debes ver** una línea `Swap:` con `2.0Gi` en la columna total:

```
               total        used        free      shared  buff/cache   available
Mem:           3.8Gi       280Mi       3.3Gi       1.0Mi       400Mi       3.5Gi
Swap:          2.0Gi          0B       2.0Gi
```

**⚠️ Si `Swap:` dice `0B`**, el `swapon` falló. Repite desde `sudo mkswap`.

> **Por qué antes de desplegar:** el pico de memoria de construir las imágenes
> —la de analítica instala pyarrow, pandas y numpy— es **mayor** que el de operar.
> Sin swap, cuando la memoria se agota el kernel no va despacio: mata al proceso
> más grande, que suele ser PostgreSQL.

---

## Paso 16 · El cortafuegos del servidor

**DÓNDE:** **dentro del servidor**, como `dsd`.

```bash
sudo apt install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
```

Comprueba:

```bash
sudo ufw status verbose
```

**✅ Debes ver** `Status: active`, la política `deny (incoming)` y los tres
puertos 22, 80 y 443 permitidos.

> Usamos `--force enable` porque el `ufw enable` normal pregunta si quieres
> continuar y advierte que puede cortar la sesión SSH. Como ya permitimos el 22
> **antes** de activarlo, no la corta.

---

## Paso 17 · Instala Docker

**DÓNDE:** **dentro del servidor**, como `dsd`.

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker dsd
```

Ahora **sal y vuelve a entrar**, porque el grupo nuevo no aplica a una sesión ya
abierta:

```bash
exit
```

Y desde tu WSL:

```bash
ssh dsd@IP_DEL_SERVIDOR
```

Comprueba:

```bash
docker run --rm hello-world
```

**✅ Debes ver** un párrafo que empieza con
`Hello from Docker! This message shows that your installation appears to be working correctly.`

**⚠️ Si dice `permission denied while trying to connect to the Docker daemon`**, no
volviste a entrar. Sal (`exit`) y vuelve a entrar por SSH.

---

## Paso 18 · Descarga el sistema

**DÓNDE:** **dentro del servidor**, como `dsd`.

```bash
cd ~
git clone https://github.com/BryanMTCode/Distribution.git
cd Distribution
```

**✅ Debes ver** `Cloning into 'Distribution'...` y al final `done.`

Comprueba que estás en la rama buena:

```bash
git log --oneline -1
```

**✅ Debes ver** el último commit de `main`.

---

## Paso 19 · Las claves y los dominios

**DÓNDE:** **dentro del servidor**, como `dsd`, dentro de `~/Distribution`.

Primero copia la plantilla y protégela:

```bash
cp .env.example .env
chmod 600 .env
```

Genera las cuatro claves de golpe (no las escribas a mano):

```bash
for v in DSD_DB_PASSWORD DSD_JWT_SECRETO DSD_CLAVE_API DSD_CLAVE_ANALITICA; do
  sed -i "s|^$v=.*|$v=$(openssl rand -hex 32)|" .env
done
```

Pon tus dominios (**cambia `tudominio.com` por el tuyo en las dos líneas**):

```bash
sed -i "s|^DSD_DOMINIO_API=.*|DSD_DOMINIO_API=api.tudominio.com|" .env
sed -i "s|^DSD_DOMINIO_ANALITICA=.*|DSD_DOMINIO_ANALITICA=analitica.tudominio.com|" .env
```

Comprueba:

```bash
awk -F= '/^DSD_(DB_PASSWORD|JWT_SECRETO|CLAVE_API|CLAVE_ANALITICA)=/{printf "%-22s %d caracteres\n", $1, length($2)}' .env
grep -E '^DSD_(DOMINIO|ENTORNO|ZONA|TUNNEL)' .env
```

**✅ Debes ver exactamente esto** (con tus dominios):

```
DSD_DB_PASSWORD        64 caracteres
DSD_JWT_SECRETO        64 caracteres
DSD_CLAVE_API          64 caracteres
DSD_CLAVE_ANALITICA    64 caracteres
DSD_ENTORNO=produccion
DSD_ZONA=America/Mexico_City
DSD_DOMINIO_API=api.tudominio.com
DSD_DOMINIO_ANALITICA=analitica.tudominio.com
DSD_TUNNEL_TOKEN=
```

**⚠️ Si alguna clave dice `0 caracteres`**, el `for` no corrió. Repítelo.

**⚠️ `DSD_TUNNEL_TOKEN=` vacío es lo correcto.** El túnel de Cloudflare es solo
para un servidor en la oficina; en un VPS no hace falta y su servicio no se
levanta.

> **Por qué las claves van en hexadecimal y no en base64:** las tres de PostgreSQL
> se incrustan dentro de una URL de conexión. `openssl rand -base64` produce `/` y
> `+` la mitad de las veces, y libpq —el que usa el laboratorio analítico— parte
> mal una URL cuya contraseña trae `/`: intenta resolver el **nombre del rol** como
> si fuera el servidor y falla con «failed to resolve host 'dsd_analitica'». Peor:
> SQLAlchemy sí la tolera, así que la API arrancaría bien y solo el laboratorio
> quedaría roto, que es la falla más difícil de atribuir.

---

## Paso 20 · Levanta el sistema

**DÓNDE:** **dentro del servidor**, como `dsd`, dentro de `~/Distribution`.

```bash
docker compose up -d
```

**Esto tarda.** Construye dos imágenes; en 1 vCPU, de 10 a 25 minutos. **No está
colgado.** Vas a ver mucho texto de descargas y compilación.

**✅ Al final debes ver** una lista de líneas `Created` / `Started` y volver al
prompt.

Comprueba el orden en que arrancó:

```bash
docker compose ps
```

**✅ Debes ver:**

- `migraciones` y `roles` en estado **`Exited (0)`** ← cero es correcto: hicieron
  su trabajo y terminaron.
- `postgres`, `api`, `worker`, `analitica`, `caddy` en **`Up`**, y `api` con
  **`(healthy)`**.

**⚠️ Si `api` se reinicia en bucle**, mira por qué:

```bash
docker compose logs api | tail -30
```

- Si dice `role "dsd_api" does not exist` → el servicio `roles` falló.
  Mira `docker compose logs roles` y revisa `DSD_CLAVE_API` en el `.env`.
- Si habla de `DSD_DATABASE_URL_API` → falta `DSD_CLAVE_API`. Es a propósito: en
  producción la API **se niega a arrancar** sin el rol restringido, porque con el
  rol dueño las políticas de seguridad por renglón quedan escritas y sin efecto, y
  un teléfono podría ver la cartera completa.

---

## Paso 21 · Los certificados

**DÓNDE:** **dentro del servidor**, como `dsd`.

```bash
docker compose logs -f caddy
```

**✅ Debes ver** líneas con `certificate obtained successfully` para tus dos
dominios. Sal con `Ctrl+C`.

**⚠️ Si ves errores de `acme` o `challenge failed`:**
1. Vuelve al paso 8 y comprueba que el DNS resuelve a tu IP.
2. Si usas Cloudflare, la nube tiene que estar **gris**, no naranja.
3. Corrige y reinicia solo Caddy: `docker compose restart caddy`.

---

## Paso 22 · 🚦 PUERTA: la salud del sistema

**DÓNDE:** primero **dentro del servidor**, como `dsd`.

La API no publica puertos —todo entra por Caddy—, así que desde el servidor se
pregunta por dentro:

```bash
docker compose exec api python -c \
  "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/salud').read().decode())"
```

Y ahora **DÓNDE: en tu WSL**, que es la prueba que de verdad importa:

```bash
curl -s https://api.tudominio.com/salud
```

**✅ Debes ver**, en los dos casos, una respuesta con esta forma — seis campos, y
**las cuatro banderas en `true`**:

```json
{"ok":true,"base_de_datos":true,"postgis":true,"migraciones":NN,"version":"0.1.0","rls":true}
```

Donde `NN` es un número cualquiera mayor que cero: **pese a su nombre, ese campo
cuenta las TABLAS del esquema público**, no las migraciones aplicadas. Sirve para
distinguir «base vacía» de «base migrada», y para eso basta. Junto con `version`,
es informativo.

**Lo que tiene que estar en `true` son las cuatro banderas:** `ok`,
`base_de_datos`, `postgis` y `rls`. Si `NN` sale `0`, las migraciones no corrieron:
mira `docker compose logs migraciones`.

**⚠️ `"rls": false` es una PUERTA CERRADA, no un detalle.** Significa que la API se
está conectando como el dueño de las tablas y las políticas por renglón están
escritas pero sin efecto: un teléfono podría leer la cartera completa de la
empresa. Revisa `DSD_CLAVE_API` en el `.env` y `docker compose logs roles`. **No
captures datos reales hasta que diga `true`.**

**⚠️ Si `curl` desde WSL da `Could not resolve host`**, es el DNS: paso 8.
**Si da `Connection refused`**, es el cortafuegos de nube: paso 5.

---

## Paso 23 · La revisión de seguridad

**DÓNDE:** **dentro del servidor**, como `dsd`, dentro de `~/Distribution`.

```bash
bash scripts/revisar_servidor.sh
```

Revisa el cortafuegos, SSH, los puertos publicados, la zona horaria, los permisos
del `.env` y el cifrado del disco.

**✅ Debes ver** todas las líneas en `OK` y que el comando termine sin error.

**⚠️ Si alguna sale en `FALLA`**, el propio script te dice qué comando la arregla.
Córrelo y vuelve a pasar la revisión hasta que esté limpia.

> El apartado de cifrado de disco va a señalar algo: en un VPS el disco lo cifra
> el proveedor, no tú, y eso protege de un disco robado pero **no** del proveedor.
> La lectura completa de qué implica está en `SEGURIDAD-OPERATIVA.md` §5.1. No te
> impide operar; conviene saberlo.

---

## Paso 24 · Crea el primer usuario de oficina

**DÓNDE:** **dentro del servidor**, como `dsd`, dentro de `~/Distribution`.

No hay ningún usuario sembrado, y es deliberado: un `admin`/`admin123` en una
migración sería un usuario con todos los permisos y la contraseña publicada en el
repositorio.

```bash
docker compose exec api python -m app.cli crear-usuario
```

Te va a preguntar, en este orden:

| Pregunta | Qué poner |
|---|---|
| `Código de empleado` | `ADMIN01` — **es con lo que vas a entrar** |
| `Nombre completo` | tu nombre |
| `Rol [admin]` | Enter, para `admin` |
| `Contraseña` | **mínimo 12 caracteres**; no se muestra al escribir |
| `Otra vez` | la misma |

**✅ Debes ver** un mensaje de confirmación de que el usuario quedó creado.

**⚠️ Si escribes `vendedor` como rol, lo va a rechazar**, y está bien: un vendedor
no entra al panel, usa la app. Los vendedores se dan de alta **desde el panel**,
más tarde.

---

## Paso 25 · 🚦 PUERTA: entra al panel

**DÓNDE:** en el **navegador de tu PC**.

```
https://api.tudominio.com/panel
```

**✅ Debes ver** el candado de HTTPS y la pantalla de entrada. Entra con el código
(`ADMIN01`) y la contraseña del paso 24.

Ya dentro, debes ver el menú con las pantallas: Tablero, Productos, Clientes,
Cargas, Liquidación, Cobranza, Compras, Entradas, Salidas, Inventario, Ventas,
Efectividad, Objetivos, Cuarentena, Teléfonos, Usuarios y rutas, Piloto.

**⚠️ Si el navegador avisa del certificado**, el TLS no quedó: paso 21.
**⚠️ Si la entrada rebota sin decir nada**, revisa que la URL sea `https://` y no
`http://`: la cookie de sesión lleva el atributo `Secure` en producción y el
navegador no la guarda sobre `http`.

---

## Paso 26 · La prueba del reinicio

**DÓNDE:** **dentro del servidor**, como `dsd`.

```bash
sudo reboot
```

Espera dos o tres minutos y, **DÓNDE: en tu WSL**:

```bash
curl -s https://api.tudominio.com/salud
```

**✅ Debes ver** otra vez la misma respuesta, con las cuatro banderas en `true`,
**sin que nadie haya tocado nada**.

> **Por qué ahora:** tu proveedor va a reiniciar esta máquina por mantenimiento
> del host, sin avisarte y probablemente de noche. Si algo no vuelve solo, es
> mucho mejor descubrirlo hoy, contigo delante, que a las 3 de la mañana.

---

## Paso 27 · Snapshot

**DÓNDE:** en la web de DigitalOcean → el droplet → **Snapshots → Take Snapshot**.

Nómbralo `dsd-desplegado-limpio`.

**✅ Debes ver** el snapshot en la lista.

> **Por qué justo aquí:** el sistema está completo y **sin un solo dato real**. Si
> el simulacro te obliga a empezar de cero, este es el punto de retorno y te ahorra
> repetir los 26 pasos anteriores.

---

## Ya está en internet. Lo que falta antes del primer vendedor

El sistema está corriendo, pero **no es todavía apto para una venta real**. Tres
cosas en este orden, y las tres son bloqueantes:

1. **El keystore del APK, y su respaldo fuera del servidor.** Es lo único
   irrecuperable del proyecto: Android solo acepta actualizar una app si el APK
   nuevo viene firmado con la **misma** llave. Si se pierde, los teléfonos ya
   instalados no se pueden actualizar nunca más, y desinstalar borra la base local
   del vendedor con las ventas que no haya subido. Ver `ENTORNO-WINDOWS.md` §4.2.
2. **Un respaldo restaurado de verdad.** `RESPALDOS.md`. Un respaldo que nunca
   restauraste no es un respaldo; y un snapshot del proveedor tampoco lo es: un
   `DELETE` sin `WHERE` a las once de la noche se replica al snapshot de esa noche.
3. **El simulacro completo**, [SIMULACRO.md](SIMULACRO.md), ahora contra este
   servidor. Corre mejor aquí que en tu PC: desaparece el problema de red de WSL y
   pruebas el APK de release real, con `https://`, en lugar de un build de
   depuración.

Y una cosa que cambia en tu operación y conviene decidir hoy: **la oficina ya no
puede trabajar sin internet.** El momento en que duele es específico — a las 6 de
la mañana, capturando la carga para que el camión salga. Se mitiga con un failover
4G en el router de la oficina y capturando la carga **la noche anterior**, que
además es mejor práctica. Está en `DESPLIEGUE.md` §0.

---

## Índice de problemas

| Síntoma | Paso |
|---|---|
| `Connection timed out` en el 22 | 9 — y usa la **Droplet Console** del proveedor |
| `Permission denied (publickey)` | 9 |
| Me quedé fuera tras endurecer SSH | 14 — la consola web del proveedor entra siempre |
| `permission denied` del demonio de Docker | 17 — sal y vuelve a entrar por SSH |
| `api` se reinicia en bucle | 20 |
| `role "dsd_api" does not exist` | 20 |
| El certificado no se emite | 21 — y revisa la nube gris de Cloudflare |
| `"rls": false` | 22 — **no captures datos hasta resolverlo** |
| `Could not resolve host` | 8 |
| `Connection refused` desde fuera | 5 |
| El panel rebota al entrar | 25 — tiene que ser `https://` |
| El laboratorio se reinicia solo | `mem_limit` en `docker-compose.yml`; súbelo o amplía la máquina |
