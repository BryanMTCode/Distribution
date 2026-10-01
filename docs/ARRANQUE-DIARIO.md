# Arranque diario — manual operativo

Levantar el sistema desde cero después de reiniciar la PC. Pensado para seguirse sin pensar: cada paso
dice el comando exacto, qué debe responder, y qué hacer cuando no responde eso.

> **Antes que nada, el atajo.** Si solo quieres empezar a trabajar:
>
> ```bash
> cd ~/Distribution && make db && make doctor
> ```
>
> `make doctor` revisa Docker, el contenedor, el puerto 5432, las migraciones, si hay usuario de
> oficina, la **zona horaria**, el puerto 8000, los `.env`, el venv y el teléfono — y para cada falla
> imprime **el comando exacto que la arregla**. Si dice «Todo listo», salta a [§1.5](#15-abrir-el-panel). El resto de este
> documento explica cada paso por si el doctor señala algo, o por si quieres entender qué pasa.

---

## 0. Lo primero que hay que desaprender

**Para probar en local no se usa `docker compose`.** Es el error que genera casi toda la fricción.

| | `make db` | `docker compose up` |
|---|---|---|
| Para qué | **tu máquina, trabajo diario** | la mini PC de la oficina, producción |
| Qué levanta | solo PostgreSQL+PostGIS | postgres + api + worker + analítica + Caddy + túnel |
| Archivos secretos | **ninguno** | `.env` con 4 valores obligatorios |
| La API | la corres tú con `make api`, con recarga en caliente | dentro de un contenedor, sin recarga |

`docker-compose.yml` usa la sintaxis `${VARIABLE:?mensaje}`, que **aborta el arranque** si la variable
no existe. Eso es deliberado: en producción, levantar sin `DSD_JWT_SECRETO` dejaría un servidor firmando
tokens con un valor publicado en el repositorio. Pero significa que `docker compose up` sin `.env` falla
siempre, y de ahí vienen los «archivos secretos faltantes que detienen a Docker».

**En desarrollo no hace falta ninguna variable de entorno.** `server/app/core/config.py` tiene valor por
omisión para todas, y el Makefile pasa `DSD_DATABASE_URL` en cada objetivo. Ver [§1.3](#13-variables-de-entorno-la-respuesta-corta-es-ninguna).

---

# 1. Preparación y panel web

## 1.1 Arranque en frío, en orden

**Paso 1 — Windows: abrir Docker Desktop.** Espera a que el icono de la ballena deje de animarse y el
panel diga **«Engine running»**. Si no lo abres, todo lo demás falla con «no responde el motor».

> Para no repetir este paso cada día: Docker Desktop → *Settings* → *General* → marca **«Start Docker
> Desktop when you sign in to your computer»**.

**Paso 2 — abrir la terminal de Ubuntu**, no PowerShell. Menú inicio → `Ubuntu`. O desde Windows
Terminal, pestaña Ubuntu.

Para comprobar que estás en el lado correcto:

```bash
uname -r      # debe mencionar 'microsoft-standard-WSL2'
pwd           # debe empezar con /home/, NO con /mnt/c/
```

> **Nunca trabajes el repositorio desde `/mnt/c/...`.** El acceso de WSL al disco de Windows pasa por
> una capa de traducción; con un proyecto de este tamaño, `flutter` y `pytest` se vuelven de tres a diez
> veces más lentos. El repositorio vive en `~/Distribution`.

**Paso 3 — ir al proyecto y ponerlo al día:**

```bash
cd ~/Distribution
git status                    # confirma en qué rama estás
git pull origin claude/exciting-hamilton-asnv8o
```

**Paso 4 — levantar la base:**

```bash
make db
```

Responde una de dos cosas, y las dos están bien:

```
Contenedor existente arrancado; tus datos siguen ahí.   <- lo normal tras reiniciar
Contenedor creado.                                      <- la primera vez
Esperando a que acepte conexiones...
Listo en 127.0.0.1:5432 (usuario postgres, clave dsd)
```

**`make db` es idempotente: córrelo siempre, pase lo que pase.** Si el contenedor existe pero está
parado —el caso normal tras reiniciar la PC— lo arranca en lugar de fallar. Antes no era así: fallaba
con *«the container name is already in use»*, y la salida natural era borrarlo y recrearlo, lo cual
**se llevaba la base entera**. Por eso también hay un volumen con nombre (`dsd_pgdata`): los datos
sobreviven incluso a borrar el contenedor.

**Paso 5 — pasar el doctor:**

```bash
make doctor
```

**Paso 6 — abrir el editor** (desde la terminal de Ubuntu, con el punto):

```bash
code .
```

Abajo a la izquierda de VS Code debe decir `WSL: Ubuntu-24.04`. Si abres VS Code desde el menú de
Windows y navegas a la carpeta, trabajas sobre la capa lenta y los plugins de Python no encuentran el
venv.

## 1.2 Los tres comandos del día

```bash
make db        # la base (idempotente)
make migrar    # aplica migraciones pendientes — barato y seguro, córrelo siempre
make api       # la API y el panel, con recarga en caliente
```

`make api` **se queda ocupando la terminal**: es un servidor, no termina. Déjalo ahí y abre otra pestaña
para lo demás. Para pararlo, `Ctrl+C` en esa terminal.

Si vas a probar algo que use la cola de trabajos, en **otra** pestaña:

```bash
make worker
```

## 1.2-bis La zona horaria, que no es cosmética

`make doctor` la revisa, y vale la pena saber por qué está en la lista.

**«Hoy» lo deciden `date.today()` en Python y `CURRENT_DATE` en PostgreSQL**, y los dos usan la zona
del sistema. En el centro de México son UTC−6, así que **con el reloj en UTC, a partir de las 18:00
locales «hoy» pasa a ser mañana**: el tablero de Gerencia muestra el día siguiente vacío, la cobranza
del día sale sin cobros, y el arqueo de la liquidación no cuadra con el efectivo que la gente tiene en
la mano.

El síntoma desconcierta porque **a las once de la mañana todo funciona**.

Una WSL recién instalada suele venir en UTC. Se arregla una vez:

```bash
sudo ln -sf /usr/share/zoneinfo/America/Mexico_City /etc/localtime
date        # debe decir CST, no UTC
```

Y el contenedor de PostgreSQL toma su zona **al crearse**, no al arrancar. Si ya lo creaste antes de
arreglar el sistema, hay que recrearlo — los datos están en el volumen y **no se pierden**:

```bash
make db-parar
docker rm dsd-postgres     # quita el contenedor, NO el volumen
make db                    # lo crea otra vez, ahora con TZ=America/Mexico_City
```

`make doctor` compara las dos horas y falla si no coinciden: una mezcla hace que `CURRENT_DATE` y
`date.today()` discrepen, y entonces un renglón se escribe con una fecha y se lee con otra.

En producción, `docker-compose.yml` pone `TZ` en los cinco servicios (`DSD_ZONA` lo cambia si algún día
hace falta otra).

## 1.3 Variables de entorno: la respuesta corta es «ninguna»

Para trabajo local **no necesitas crear ningún archivo**. Comprobado contra `server/app/core/config.py`:
todos los ajustes tienen valor por omisión, y `DSD_JWT_SECRETO` trae uno de desarrollo que solo se
rechaza cuando `DSD_ENTORNO=produccion`.

El Makefile inyecta lo único variable:

```make
DB ?= postgresql+psycopg://postgres:dsd@127.0.0.1:5432/dsd
```

Y cada objetivo lo pasa: `make api` corre `DSD_DATABASE_URL="$(DB)" DSD_DEBUG=1 uvicorn ...`.

Para apuntar a otra base, sin editar nada:

```bash
make migrar DB=postgresql+psycopg://postgres:otra@127.0.0.1:5433/dsd
```

### Las dos trampas de los `.env`

**Trampa 1 — un `.env` a medias es peor que ninguno.** `config.py` declara
`SettingsConfigDict(env_file=".env", env_prefix="DSD_")`. Ese archivo se lee **antes** de los valores por
omisión, así que un `.env` con `DSD_ENTORNO=produccion` y sin `DSD_JWT_SECRETO` real hace que la API se
niegue a arrancar. En desarrollo, **no tengas `server/.env`**. `make doctor` lo avisa si aparece.

**Trampa 2 — el `.env` de la raíz no es el de la API.** `env_file=".env"` es relativo al directorio de
trabajo, y los objetivos del Makefile hacen `cd server` antes de arrancar. Así que:

- `Distribution/.env` → lo lee **`docker compose`** (producción). La API lanzada con `make api` **no**.
- `Distribution/server/.env` → lo leería la API. **No lo crees para desarrollo.**

### Cuando sí toca generar secretos (solo producción)

```bash
cd ~/Distribution
cp .env.example .env
printf 'DSD_DB_PASSWORD=%s\n'    "$(openssl rand -base64 24)" >> .env
printf 'DSD_JWT_SECRETO=%s\n'    "$(openssl rand -hex 32)"    >> .env
printf 'DSD_CLAVE_ANALITICA=%s\n' "$(openssl rand -base64 24)" >> .env
```

Luego **edita `.env` y borra las líneas vacías duplicadas** que venían de la plantilla, o Docker tomará
la primera (vacía) y no la que acabas de añadir. `DSD_TUNNEL_TOKEN` sale del panel de Cloudflare y se
pega a mano. `.env` está en `.gitignore`: nunca se versiona.

## 1.4 Procesos fantasma y puertos ocupados

### El 8000, ocupado por un uvicorn que creías muerto

Pasa cuando cierras la terminal sin `Ctrl+C`, o cuando VS Code mata el panel pero no al hijo.

```bash
# 1. ¿Quién es?
pgrep -af "uvicorn app.main:app"

# 2. Matarlo
pkill -f "uvicorn app.main:app"

# 3. Comprobar que se fue
pgrep -af uvicorn || echo "limpio"
```

Si no cede —raro, pero pasa cuando quedó en estado zombi:

```bash
pkill -9 -f "uvicorn app.main:app"
```

### El 8000, ocupado por algo que no es uvicorn

```bash
ss -ltnp | grep 8000
```

Te dice el PID. Si `ss` no está instalado: `sudo apt install -y iproute2`.

Si el puerto lo tiene un proceso **de Windows** (no aparece en `ss` de WSL pero el navegador sí
responde), desde PowerShell:

```powershell
netstat -ano | findstr :8000
taskkill /PID <el-pid> /F
```

### El 5432, ocupado por otro PostgreSQL

Es el peor de todos porque **no da «connection refused»**: el puerto contesta, pero quien contesta es
otro PostgreSQL —el servicio de Windows, casi siempre— donde no están nuestras tablas. El síntoma es
`relation "usuarios" does not exist` o un login que falla sin razón.

`make doctor` lo distingue y lo dice con esas palabras. Para arreglarlo, desde PowerShell **como
administrador**:

```powershell
Get-Service -Name "postgresql*"
Stop-Service -Name "postgresql-x64-16"          # el nombre que haya salido
Set-Service  -Name "postgresql-x64-16" -StartupType Manual   # que no vuelva al reiniciar
```

Y luego, en Ubuntu: `make db`.

### Contenedores viejos estorbando

```bash
docker ps -a --filter name=dsd        # qué hay
make db-parar                         # detiene SIN borrar: los datos se quedan
make db                               # vuelve a arrancarlo
```

> **`make db-parar` ya no borra nada.** Antes hacía `docker rm -f`, y usarlo para resolver un conflicto
> de puertos costaba la base completa. Para el reinicio de verdad existe `make db-borrar`, que pide
> escribir `BORRAR` en mayúsculas antes de tocar nada.

### Empezar con una base limpia, a propósito

```bash
make db-borrar     # pide confirmación escrita; quita contenedor Y volumen
make db
make migrar
make usuario       # la base limpia no tiene usuarios: hay que crear uno
```

## 1.5 Abrir el panel

| Qué | URL |
|---|---|
| **Panel de operación** | <http://127.0.0.1:8000/panel> |
| API interactiva (Swagger) | <http://127.0.0.1:8000/docs> |
| Salud del servidor | <http://127.0.0.1:8000/salud> |

Se abren en el navegador **de Windows**; WSL2 reenvía `localhost` solo.

### Crear el usuario administrador

**No hay usuario por omisión, y es deliberado**: sembrar `admin/admin123` en una migración dejaría en
producción un usuario con todos los permisos y la contraseña publicada en el repositorio, accesible
desde internet a través del túnel.

```bash
make usuario
```

Pregunta, en este orden:

1. **Código de empleado** — ej. `ADMIN01`. Es con lo que entras.
2. **Nombre** — el que aparece en el panel.
3. **Rol** — `admin` (todo), `gerente` (solo lectura), `supervisor` (opera). Enter deja `admin`.
4. **Contraseña, dos veces** — **mínimo 12 caracteres**. No se muestra ni queda en el historial.

> El rol `vendedor` se rechaza aquí a propósito: un vendedor no entra al panel, entra por la app. Los
> vendedores se dan de alta **desde** el panel, en *Equipo*.

Hazlo cada vez que recrees la base. `make doctor` avisa cuando no hay ninguno.

### Flujos a validar en el panel

**Cobranza** — <http://127.0.0.1:8000/panel/cobranza>

1. Abre en **el día de hoy**. Arriba, «Qué entregar hoy»: comprueba que la columna **Efectivo a
   entregar** no incluya las transferencias. Es la cifra que cuadra contra la mano del vendedor; si
   sumara el banco, la caja no cuadraría nunca.
2. Botón **«Por revisar»**: trae los cobros marcados con el motivo **en español**, no el código.
3. Entra a un cobro marcado. Valida tres cosas:
   - **a qué facturas se aplicó** el dinero, en orden de vencimiento más antiguo;
   - el bloque **«Qué veía el vendedor»** — el saldo que traía el teléfono contra el real. Ese número
     distingue a quien cobró a ciegas (equipo sin sincronizar) de quien cobró mal;
   - el botón **«Dar por revisado»**. Confirma que **no cambia el importe ni la aplicación**: solo
     registra quién lo miró, y el motivo original se queda al lado.
4. Enlace **«Ver la cartera por antigüedad»**: los tramos se cuentan desde el **vencimiento**, no desde
   la emisión. Un cliente a 30 días no debe aparecer vencido el día 15.

**Inventario** — <http://127.0.0.1:8000/panel/inventario>

1. Escoge almacén. El camión es un almacén como cualquier otro: ahí ves lo que trae cada vendedor.
2. Un número **en negativo no es un error del sistema**: es un conteo por revisar. Pasa cuando se cargó
   más de lo registrado, o cuando una venta offline entró con el camión ya en cero.
3. Si la pantalla dice que **la caché no cuadra con el libro mayor**, eso **sí** es un bug: alguna
   transacción escribió el movimiento y no la existencia. Entra al producto; el libro mayor es
   *append-only*, así que es el que tiene razón, y su saldo corriente te dice en qué movimiento se
   separaron.

**De paso, dos pantallas más que conviene recorrer:**

- **Efectividad** (<http://127.0.0.1:8000/panel/efectividad>) — abre con siete días, no con hoy. La
  columna «de quién depende» es el reporte: separa lo que podemos arreglar de lo que no.
- **Liquidación** — al cerrar, si el teléfono reportó cero pendientes hoy, **la casilla de «confirmo que
  terminó de sincronizar» no aparece**: el dato la sustituye. Si aparece, el cierre quedará marcado
  *sin respaldo de sincronización*, y eso es correcto.

## 1.6 El laboratorio analítico (Streamlit)

Es la Fase 8: las preguntas que no caben en una pantalla operativa — drop size,
rotación, clientes que se están yendo.

```bash
make refrescar-analitica     # recalcula el esquema estrella (segundos)
make analitica              # levanta el laboratorio en :8501
```

Abre <http://127.0.0.1:8501>. Ocupa su propia terminal, como `make api`.

**El orden importa.** El laboratorio lee vistas materializadas, no las tablas
transaccionales: si no has recalculado, muestra la foto del último refresco — y lo
dice arriba, con su hora. Si nunca se ha recalculado, te dice eso en vez de
mostrar ceros como si fueran datos.

En producción no hace falta ejecutarlo a mano: **al cerrar una liquidación el
panel encola el recálculo solo**, porque es el momento en que las cifras del día
quedan firmes. `make refrescar-analitica` es para el arranque, para después de
restaurar un respaldo, y para un cron nocturno.

### Qué validar

1. **La barra de arriba, siempre.** Dice de cuándo son los datos **y** cuántos
   equipos no habían sincronizado cuando se calcularon. Las dos cosas juntas son
   la advertencia: «actualizado hace 1 min» suena perfecto, pero si un teléfono no
   había subido su día, el total de ventas es un **piso**, no un total.
2. **Drop size**: comprueba que «Drop size» e «Importe por visita» sean números
   **distintos**. El primero divide entre las visitas que vendieron; el segundo
   entre todas. Son dos métricas, y confundirlas es el error más común al leer un
   reporte de DSD.
3. **Clientes en riesgo**: fíjate en la columna `cadencia_dias`. El riesgo se mide
   contra la cadencia **propia de cada cliente**, no contra un umbral fijo: el que
   compraba cada semana y lleva 20 días sale en riesgo, y el que siempre compró
   cada 45 no.
4. **Rotación**: léela junto a «días con existencia». Una rotación altísima sobre
   un producto que estuvo tres días en el camión no es éxito de ventas, es
   desabasto.

> Si una cifra te parece mal, la definición está escrita en
> `server/app/domain/analitica.py`, al lado de su SQL. El laboratorio no lleva
> consultas propias: las importa de ahí, para que la misma pregunta no se conteste
> distinto cada vez.

## 1.6-bis El tablero de Gerencia: fijar los objetivos y recalcularlo

El tablero móvil (Fase 7) **no lee las tablas de operación**: lee modelos de
lectura que recalcula un job. Eso tiene dos consecuencias para tus pruebas
locales.

### Hay que recalcularlo al menos una vez

```bash
make recalcular-tablero      # recalcula los días rancios + hoy (milisegundos)
```

Si nunca lo corres, la app dice «el tablero no se ha calculado todavía» en vez de
mostrar ceros — a propósito: «nadie ha vendido» y «el worker no está corriendo»
son dos cosas distintas, y confundirlas te mandaría a buscar el problema donde no
está.

En producción no hace falta: **cada lote de sincronización encola el recálculo**,
y el cierre de liquidación también. El job averigua solo qué días quedaron
rancios, así que ocho camiones subiendo a la vez encolan **uno**. `make
recalcular-tablero` es para el arranque en frío, para después de restaurar un
respaldo, y para comprobar a mano que funciona sin esperar al worker.

> Si lo corres mientras `make worker` está levantado, no hay conflicto: el job de
> la cola y el comando hacen lo mismo y el recálculo es idempotente.

### Sin objetivo de ruta, la tarjeta de avance no puede tener datos

Es el mismo hueco que tuvieron los catálogos de motivos en la Fase 6: la pantalla
lista y nada publicando el dato. Entra a **Panel → Objetivos**, pon una cifra
mensual a cada ruta activa y guarda. Un objetivo vacío **borra** el renglón en vez
de guardar cero: «sin objetivo» y «objetivo $0» son dos cosas distintas, y la
segunda daría 100 % de avance con la primera venta.

El botón «Copiar los objetivos de \<mes anterior\>» rellena los huecos sin pisar
lo que ya ajustaste a mano.

### Qué validar en el panel

1. **Objetivos**: fija uno, recarga, y comprueba que aparece con tu nombre al
   lado. Un objetivo sin autor es una decisión que nadie puede revisar después.
2. **La barra de avance** trae una marca vertical: es el avance **esperado** a
   prorrata de los días transcurridos. Sin ella, 67 % se lee igual el día 10 que
   el día 28, y es excelente o grave según cuál.
3. **Prueba un objetivo absurdo** (`180000000`): se detiene con un mensaje. Un
   cero de más deja la barra en 0.1 % todo el mes y nadie sabe por qué.

## 1.7 Antes de dar por bueno un cambio

```bash
make lint          # ruff sobre app y tests
make pruebas       # 622 pruebas de Python — necesita la base arriba
make movil         # 457 de Dart + 212 de widget
```

> Si `make pruebas` falla con errores de conexión a mitad de la corrida y los mismos archivos pasan al
> volver a correrlos, **no es tu código**: es el contenedor de PostgreSQL que se cayó. `make db` y
> repite. Pasó durante el desarrollo y cuesta media hora de depuración si no se sabe.
>
> Y **no corras dos `pytest` a la vez** contra la misma base: se truncan las tablas entre ellos y los
> fallos no significan nada.

---

# 2. Aplicación móvil en el teléfono

Las pruebas que valen son en hardware. El emulador no tiene **GPS real** ni **Bluetooth**, y el no-drop
exige ubicación: en emulador no se puede validar la pantalla que más importa de la Fase 6.

## 2.1 El puente USB: pasar el teléfono de Windows a Ubuntu

WSL2 no ve el USB por sí mismo. Hay que ceder el dispositivo con `usbipd-win`.

**Una sola vez, en Windows (PowerShell como administrador):**

```powershell
winget install --interactive --exact dorssel.usbipd-win
```

Cierra y vuelve a abrir PowerShell.

**Una sola vez, en el teléfono (POCO M5s):**

1. *Ajustes* → *Acerca del teléfono* → toca **«Versión de MIUI»** siete veces, hasta «Ya eres
   desarrollador».
2. *Ajustes* → *Opciones adicionales* → *Opciones de desarrollador* → activa **«Depuración por USB»**.
3. En el mismo menú, activa **«Instalar vía USB»**. Sin esto, `flutter run` instala y falla al lanzar.

**Una sola vez, en Ubuntu:**

```bash
sudo apt update && sudo apt install -y android-tools-adb
```

**Cada vez que conectas el cable** (PowerShell **como administrador**):

```powershell
usbipd list
```

Busca el renglón del teléfono — aparece como `POCO M5s`, `Xiaomi` o `SM-…`/`Android`. Copia su `BUSID`
(con la forma `2-4`).

```powershell
usbipd attach --wsl --busid 2-4
```

> Si es la primera vez con este teléfono, antes hace falta compartirlo:
> ```powershell
> usbipd bind --busid 2-4
> ```
> El `bind` se recuerda; el `attach` hay que repetirlo **cada vez** que conectas el cable o reinicias.

**Comprobar desde Ubuntu:**

```bash
adb devices
```

Debe salir:

```
List of devices attached
abc123def456    device
```

| Lo que sale | Qué significa | Arreglo |
|---|---|---|
| lista vacía | el `attach` no llegó a WSL | repite `usbipd attach`; revisa que sea PowerShell **admin** |
| `unauthorized` | el teléfono no confía en esta PC | desbloquea la pantalla y acepta **«Permitir depuración USB»** |
| `offline` | el puente quedó a medias | `adb kill-server && adb start-server`, y vuelve a hacer `attach` |
| `no permissions` | falta la regla udev | `sudo usermod -aG plugdev $USER` y **cierra y abre la sesión de WSL** (`wsl --shutdown` en PowerShell) |

`make doctor` también lo verifica e imprime el `usbipd` que falta.

## 2.2 Compilar e instalar en el teléfono conectado

Con `adb devices` mostrando `device`:

```bash
make app-demo
```

Equivale a `flutter run --dart-define=DSD_DEMO=true`. **Usa este, no `make app`**, para probar sin
servidor: el modo demo siembra datos y una sesión local, así que entras directo a la ruta sin necesitar
la API, ni red, ni `adb reverse`.

En la pantalla de login aparece **«Sembrar datos y entrar»**. El PIN también se muestra ahí (`481507`)
por si quieres recorrer el login normal.

**Cuándo usar cada uno:**

| Comando | Para qué |
|---|---|
| `make app-demo` | ver la interfaz y los flujos **sin servidor**. El 90 % de las pruebas. |
| `make app` | probar la **sincronización de verdad** contra tu API. Requiere el puente de red de abajo. |

Para que el teléfono alcance la API de tu PC con `make app`, en otra pestaña de Ubuntu:

```bash
adb reverse tcp:8000 tcp:8000
```

Eso hace que `127.0.0.1:8000` **dentro del teléfono** apunte a tu máquina. Hay que repetirlo cada vez
que se reconecta el cable.

## 2.3 Plan B: el APK a mano, cuando el puente USB falla

Es el camino más robusto, y además el natural para **salir a la calle sin cable**.

**Paso 1 — generar el APK:**

```bash
make apk-demo
```

Al terminar imprime la ruta. El archivo queda en:

```
mobile/app/build/app/outputs/flutter-apk/app-debug.apk
```

**Paso 2 — abrir esa carpeta en el explorador de Windows, desde Ubuntu:**

```bash
cd ~/Distribution/mobile/app/build/app/outputs/flutter-apk
explorer.exe .
```

El punto es obligatorio: `explorer.exe` sin argumento abre la carpeta de Documentos. Se abre una ventana
del explorador de Windows sobre la ruta de red `\\wsl$\Ubuntu-24.04\home\...`, con `app-debug.apk`
dentro.

Si prefieres copiarlo al escritorio de Windows en un solo paso:

```bash
cp ~/Distribution/mobile/app/build/app/outputs/flutter-apk/app-debug.apk \
   /mnt/c/Users/$USER/Desktop/
```

> Si tu usuario de Windows no es igual al de Ubuntu, la ruta no existirá. Averigua la correcta con
> `ls /mnt/c/Users/`. Para saber el nombre exacto que verá Windows:
> `wslpath -w ~/Distribution/mobile/app/build/app/outputs/flutter-apk/app-debug.apk`

**Paso 3 — pasarlo al teléfono.** Cualquiera de estas sirve:

- **Cable, como almacenamiento:** conecta el teléfono, en la notificación elige **«Transferencia de
  archivos»**, y arrastra el APK a `Descargas` desde el explorador de Windows.
- **Sin cable:** súbelo a Google Drive o WhatsApp Web y ábrelo desde el teléfono.
- **Si adb sí funciona** pero `flutter run` no: `adb install -r <ruta-del-apk>`. La `-r` reinstala
  conservando los datos; sin ella, una instalación encima falla.

**Paso 4 — instalar en el teléfono.** Abre `Descargas` → toca `app-debug.apk` → Android pedirá permitir
**«Instalar aplicaciones desconocidas»** para el gestor de archivos. Acéptalo.

> Si MIUI muestra **«App dañada»** o bloquea la instalación, desactiva *Ajustes* → *Seguridad* →
> **«Analizar apps antes de instalar»** mientras pruebas.

## 2.4 Flujos a validar en la app

### Merma — icono de bote de basura, arriba a la derecha de la lista de ruta

Vive ahí y no dentro de la visita a un cliente porque **la caja se revienta entre tienda y tienda**.

1. Escoge motivo. Es catálogo cerrado: viene del panel. **Si la lista sale vacía**, el equipo no recibió
   el catálogo — sincroniza y vuelve a entrar. La pantalla lo dice con palabras en vez de mostrar un
   desplegable vacío.
2. Busca un producto, cambia la unidad a **CAJA** y teclea `2`. **Debe mostrar abajo la conversión a
   piezas** (`Son 48 ...`). Ahí se atrapa el dedazo antes de guardar.
3. **La prueba que importa:** teclea una cantidad **mayor** de la que el camión dice traer. Debe
   **avisar y dejar guardar**. Si lo bloqueara, la pérdida no quedaría registrada y en la liquidación
   aparecería como **faltante del vendedor** — justo lo que este documento existe para evitar.
4. Escoge un motivo con `afecta_vendedor` (ej. **«Empaque roto»**): debe avisarte que **se te
   descuenta**. Enterarse en la liquidación es lo que rompe la confianza.
5. Después de guardar, revisa en el panel → *Inventario* que la existencia del camión bajó, y en
   *Efectividad* que la pérdida aparece en «Pérdidas por motivo».

### Devolución de cliente — menú de tres puntos dentro de la visita

Tiene que ser desde el cliente: sin él, la oficina no puede revisarla contra su venta. Valida que el
signo es el **contrario** — la mercancía **entra** al camión, la existencia **sube**.

### No-drop con GPS — mismo menú, «No me compró»

**Es el único documento del sistema que exige ubicación.** Sin coordenadas, «estuve ahí y no compró» es
indistinguible de «no fui».

1. **La validación central:** entra con el **GPS apagado**. El botón **«Registrar la visita» debe estar
   deshabilitado**, y la pantalla debe decir en qué se arregla: permiso, GPS apagado, o bajo techo.
   Prueba los tres estados: niega el permiso, apaga la ubicación, y métete donde no haya señal.
2. Prende el GPS, toca **«Leer GPS»** y espera el `±N m`. Ahí el botón se habilita.
3. Escoge **«No traigo lo que pidió»**: exige nota. Intenta guardar sin escribirla — **no debe pasar**.
   Y debe avisar que **«lo podemos arreglar nosotros»** (categoría *producto*).
4. Escoge **«Cerrado»**: no exige nota, y **no** debe decir que es nuestra culpa.
5. Después, en el panel → *Efectividad*: la visita perdida aparece con su categoría separada.

### Cobro de efectivo — icono de billetes, solo en clientes que deben

1. El botón **solo aparece si el cliente debe algo**. Comprueba que un cliente sin deuda no lo tiene: el
   día de cobranza lo que se busca es encontrar rápido a quién cobrarle.
2. El saldo se muestra **con su antigüedad** («Actualizado hace 3 h»). Un número sin fecha se trata como
   la verdad, y éste es una caché.
3. Teclea `500` — debe leerse como `500.00`. Prueba también `1,250.5` → `1250.50`.
4. **La prueba que importa:** cobra **más** de lo que dice que debe. Debe **dejarte**. El dinero ya está
   sobre el mostrador; si la pantalla lo impidiera, el vendedor se guardaría efectivo sin documento.
5. Cambia la forma de pago a **transferencia**: debe **exigir referencia**.
6. Guarda. Aparece el folio y el botón **«Imprimir recibo»** — la impresión es **un toque aparte**, no
   automática. Sin impresora emparejada, usa **«Ver el recibo en pantalla»** para revisar el papel.
7. Confirma en el panel → *Cobranza* que el cobro llegó, a qué factura se aplicó, y que si cobraste de
   más aparece **marcado** con «Cobró más de lo que el cliente debía».

### Tablero de Gerencia — se entra por «Entrar como Gerencia» en el login

**Gerencia entra en línea**, con código y contraseña, no con PIN: el tablero
existe para ver lo que están haciendo los otros y eso no se puede saber sin
preguntarle al servidor. Un vendedor **no** puede entrar por ahí — el servidor lo
rechaza con un mensaje que explica que necesita un dispositivo registrado.

Para probarlo necesitas dos cosas:

1. **Un usuario de rol `gerente`.** Créalo con `make usuario`.
2. **Que la app apunte a tu PC.** La dirección del servidor es de **tiempo de
   compilación** —no hay pantalla de ajustes a propósito: un campo editable es un
   camino para que un equipo robado mande la cartera a donde quiera quien lo
   tenga—. Averigua la IP de tu WSL/PC en la red local y compila con ella:

   ```bash
   ip addr show eth0 | grep 'inet '        # o la interfaz que uses
   cd mobile/app
   flutter run --dart-define=DSD_BASE_URL=http://192.168.1.50:8000
   ```

   Y la API tiene que escuchar en la red, no solo en localhost: `make api` ya
   levanta uvicorn en `0.0.0.0`. Si el teléfono no conecta, lo primero que hay
   que descartar es el **firewall de Windows** sobre el puerto 8000.

> `http://` sin TLS solo funciona en los builds de **debug**: el
> `usesCleartextTraffic` vive en `android/app/src/debug/AndroidManifest.xml` y
> únicamente ahí. En release Android lo prohíbe, y está bien que lo prohíba.

Qué validar, en este orden:

1. **La franja de arriba, antes de la primera cifra.** Dice cuándo lo calculó el
   servidor **y** cuándo lo bajó este teléfono. Son dos horas distintas: el
   servidor calculó a las 10:05, el teléfono lo bajó a las 10:40, y la cifra
   arrastra los 35 minutos de camino además de los que tuviera al calcularse.
2. **Apaga los datos del teléfono y vuelve a refrescar.** Las cifras **siguen
   ahí**, con la franja en rojo diciendo que son la última copia y de cuándo es.
   Una pantalla vacía con «sin conexión» no sirve para nada; las cifras de hace
   una hora con su etiqueta sirven para casi todo.
3. **Borra los datos de la app y refresca sin señal.** Ahora sí no hay copia, y lo
   dice con esas palabras en vez de mostrar ceros.
4. **Entra con un usuario sin el permiso `tablero.ver`.** La pantalla lo explica y
   **no** ofrece «volver a intentar»: no se arregla reintentando, se pide el
   permiso en la oficina.
5. **El mapa del día.** Es un lienzo, no un mapa con calles: no descarga nada y
   funciona en la bodega sin cobertura. Toca un punto para ver de qué cliente es.
   Las ventas sin GPS **no aparecen**, y eso es información.
6. **El vendedor sin movimiento** sale marcado en rojo en «Por vendedor». A media
   mañana es el renglón más urgente del tablero.

### El ciclo completo, si quieres probar la sincronización de verdad

```bash
# Pestaña 1
make db && make migrar && make api
# Pestaña 2
make worker
# Pestaña 3
adb reverse tcp:8000 tcp:8000
make app
```

En la app, entra con el vendedor que creaste en el panel (*Equipo*), sincroniza, y comprueba que lo que
registraste en el teléfono aparece en el panel.

---

# 3. Avance del proyecto

Medido contra el plan de fases de [`docs/ARQUITECTURA.md`](ARQUITECTURA.md) §3, ponderando cada fase por
su duración estimada (punto medio del rango). Las fases 0–9 suman **27 semanas** de plan; la 10
(«Opcionales») queda fuera porque no tiene alcance ni duración definidos.

| Fase | Entregable | Plan | Estado | Avance |
|---|---|---|---|---|
| **0** | Fundaciones, contratos Dart↔Python, auth, RBAC | 2.5 sem | ✅ completa | 100 % |
| **1** | Catálogos y núcleo, panel de operación | 2.5 sem | ✅ completa | 100 % |
| **2** | Motor de sincronización, pruebas de caos | 3.5 sem | ✅ completa | 100 % |
| **3** | App vendedor MVP | 3.5 sem | 🟡 **parcial** | ~60 % |
| **4** | Inventario de camión, liquidación | 2.5 sem | 🟡 casi | ~90 % |
| **5** | Crédito y cobranza | 2.5 sem | ✅ completa | 100 % |
| **6** | Alta en calle, mermas, no-drops | 2.0 sem | ✅ completa | 100 % |
| **7** | **Perfil Gerencia móvil** | 2.0 sem | ✅ completa | 100 % |
| **8** | Laboratorio analítico (Streamlit) | 3.5 sem | 🟡 **casi** | ~85 % |
| **9** | Endurecimiento, MDM, RLS | 2.5 sem | ⛔ no empezada | 0 % |

## **Avance general: ≈ 83 %**

El cálculo, semana a semana de plan:

```
Fase 0   2.5 × 1.00 = 2.50      Fase 5   2.5 × 1.00 = 2.50
Fase 1   2.5 × 1.00 = 2.50      Fase 6   2.0 × 1.00 = 2.00
Fase 2   3.5 × 1.00 = 3.50      Fase 7   2.0 × 1.00 = 2.00
Fase 3   3.5 × 0.60 = 2.10      Fase 8   3.5 × 0.85 = 2.98
Fase 4   2.5 × 0.90 = 2.25      Fase 9   2.5 × 0.00 = 0.00
                                ─────────────────────────────
                                22.33 de 27 semanas = 82.7 %
```

### Qué le falta a lo que está «parcial»

**Fase 3 (~60 %)** — el código está completo: catálogo offline, carrito, venta, cola de sincronización y
los bytes del ticket ESC/POS, todo probado. Faltan dos cosas, y ninguna es de código:

- **El transporte Bluetooth.** Los bytes del ticket están generados y verificados byte a byte (59
  pruebas), pero `Impresora` solo tiene implementación simulada: falta el socket, y eso espera a que
  recuperes la **EC-MP200**. Es media semana de trabajo cuando llegue el hardware.
- **El piloto con un vendedor real en una ruta, dos semanas, con el papel en paralelo.** Son 2 de las
  3.5 semanas de la fase, y no se puede acelerar: su valor es el calendario.

**Fase 4 (~90 %)** — carga, existencias offline y liquidación están completas. Falta la **consulta
online de la bodega principal desde la app** (con su estado explícito «requiere conexión»): no hay
endpoint de inventario en `/v1`, así que el vendedor no puede preguntar desde la calle si hay existencia
para una reposición.

**Fase 8 (~85 %)** — el esquema estrella, el job de refresco y el laboratorio están hechos y probados,
con las seis métricas que nombra el plan: drop size, frecuencia de visita, productividad por vendedor,
rotación, clientes en riesgo y efectividad. Lo que queda del alcance planeado es lo que el plan llama
«modelos»: pronóstico de demanda y cohortes de retención. Son trabajo de Pandas sobre un esquema que ya
existe, no de infraestructura — y conviene hacerlo **después** del piloto, cuando haya meses de datos
reales en vez de los de una semana de pruebas.

### Una advertencia sobre la numeración

**Los números de fase que usamos al trabajar no coinciden con los de `ARQUITECTURA.md`.** En las
conversaciones llamamos «Fase 7» a la liquidación, pero en el plan la liquidación es parte de la **Fase
4**, y la **Fase 7** es el *dashboard de Gerencia móvil*. Esta tabla usa la numeración del plan, que es
la que vale para medir.

### Lo que el porcentaje no dice

El 83 % es de **alcance planeado**, y hay dos razones por las que el proyecto está mejor de lo que ese
número sugiere:

1. **Lo construido está probado de verdad**: 681 pruebas de Python contra PostgreSQL real, 457 de Dart,
   212 de widget, y **siete** verificaciones de frescura de contratos en CI —vectores canónicos, deltas,
   importes, OpenAPI, ticket, sobres y esquema local—, cada una capaz de poner el CI en rojo si el
   código y su contrato se separan. No hay deuda oculta en lo hecho.
2. **Lo que falta es lo menos riesgoso.** La Fase 2 —el motor de sincronización, la que puede hundir un
   proyecto de DSD— está cerrada con pruebas de caos. La 9 es trabajo conocido sobre un modelo de datos
   que ya no se mueve.

Y una razón por la que está peor:

3. **Nada de esto ha visto un vendedor real.** El piloto de la Fase 3 es el único paso que puede
   invalidar decisiones de diseño, y sigue pendiente. Dos semanas de un vendedor con el teléfono en la
   mano valen más que las dos fases siguientes juntas.

---

## Apéndice: la tarjeta de referencia

```bash
# ───── arranque diario ─────
cd ~/Distribution
make db          # base (idempotente: córrelo siempre)
make doctor      # qué está mal y cómo se arregla
make migrar      # migraciones pendientes
make api         # API + panel  →  http://127.0.0.1:8000/panel

# ───── laboratorio analítico ─────
make refrescar-analitica              # recalcula el esquema estrella
make analitica                        # Streamlit  →  http://127.0.0.1:8501

# ───── tablero de Gerencia (Fase 7) ─────
make recalcular-tablero               # recalcula los modelos de lectura

# ───── cuando algo se atora ─────
pkill -f "uvicorn app.main:app"       # el 8000 ocupado
ss -ltnp | grep -E '8000|5432'        # quién tiene el puerto
make db-parar && make db              # rearranca la base sin perder datos
make db-borrar                        # base limpia DE VERDAD (pide confirmación)

# ───── teléfono ─────
# PowerShell admin:  usbipd list   →   usbipd attach --wsl --busid <BUSID>
adb devices                           # debe decir 'device'
make app-demo                         # instalar y correr, sin servidor
make apk-demo                         # plan B: generar el APK
explorer.exe .                        # abrir la carpeta del APK en Windows

# ───── antes de commitear ─────
make lint && make pruebas && make movil
```
