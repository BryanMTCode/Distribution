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
> oficina, la **zona horaria**, el puerto 8000, los `.env`, los **respaldos**, el venv y el teléfono
> — y para cada falla imprime **el comando exacto que la arregla**. Si dice «Todo listo», salta a
> [§1.5](#15-abrir-el-panel). El resto de este documento explica cada paso por si el doctor señala
> algo, o por si quieres entender qué pasa.

---

## 0. Lo primero que hay que desaprender

**Para probar en local no se usa `docker compose`.** Es el error que genera casi toda la fricción.

| | `make db` | `docker compose up` |
|---|---|---|
| Para qué | **tu máquina, trabajo diario** | el VPS, producción (`docs/DESPLIEGUE.md`) |
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
locales «hoy» pasa a ser mañana**: el tablero de Gerencia muestra el día siguiente vacío, la carga
«para mañana» sale para pasado mañana, y el arqueo del corte no cuadra con el efectivo que la gente
tiene en la mano.

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
3. **Rol** — `admin` (todo), `gerente` (monitorea **y corrige**: ventas, inventario de camión
   y catálogo, desde octubre de 2026), `supervisor` (opera). Enter deja `admin`.
4. **Contraseña, dos veces** — **mínimo 12 caracteres**. No se muestra ni queda en el historial.

> El rol `vendedor` se rechaza aquí a propósito: un vendedor no entra al panel, entra por la app. Los
> vendedores se dan de alta **desde** el panel, en *Equipo*.
>
> En la ficha de cada usuario, **Eliminar** lo borra de verdad con sus teléfonos vinculados (y su
> camión si está vacío). Si ya tiene ventas, cargas u otros documentos no se borra ni se desactiva:
> dice qué tiene y no cambia nada. Para solo quitarle el acceso, **Desactivar** (ADR 0002 §88).
>
> Los desactivados no salen en la lista: están en «ver los N desactivado(s)» (§93). El nombre de
> una ruta, bodega, camión o lista se cambia ahí mismo, en su tabla (§94).

Hazlo cada vez que recrees la base. `make doctor` avisa cuando no hay ninguno.

### Flujos a validar en el panel

**Entradas de mercancía** — <http://127.0.0.1:8000/panel/entradas>

Es por donde el inventario **existe**. En un sistema recién instalado es el primer paso de todos:
sin una entrada, las bodegas están en ceros y una carga confirmada deja la bodega en negativo sin
que nada se queje (`existencias` no lleva CHECK de signo a propósito — §0.1).

1. **Abre un borrador** con motivo *Inventario inicial* y la nota de quién contó y cuándo. La nota es
   obligatoria solo en ese motivo: es el documento que explica de dónde salió todo el inventario del
   arranque, y se lee una vez en la vida del sistema — el día que algo no cuadra.
2. **Captura un renglón en cajas.** La pantalla responde con la conversión hecha
   («10 CAJA = 240 PZA»): es la misma función que usa el teléfono al armar una partida, escrita en un
   solo lugar del sistema.
3. **Mira la columna «existencia hoy»**, con su flecha a cómo queda al confirmar. Es donde se ve un
   cero de más *antes* de que sea un asiento que no se borra.
4. **Confirma.** Hasta ese momento no se movió nada. Después, el libro mayor y las existencias quedaron
   escritos en la misma transacción, y el renglón ya no se edita: se compensa con otro documento.
5. **Intenta confirmar dos veces.** Debe decir que ya está confirmada y **no** meter la mercancía otra
   vez: un doble clic en una pantalla lenta duplicaría una remisión completa, y el sobrante solo
   aparecería semanas después en un conteo físico.
6. **Intenta recibir en un camión.** No se puede, y el mensaje dice el camino correcto. No es una
   limitación de la pantalla: es §0.2 — el almacén del camión tiene un único dueño, y la oficina nunca
   le escribe existencias. Mercancía nueva entra a la bodega y de ahí sube con una carga, que es lo que
   el teléfono sabe recibir.

**Cargas: la regla del §2.3** — <http://127.0.0.1:8000/panel/cargas>

Es el guardia que evita que el arqueo de un día cerrado se mueva solo. Para probarlo:

1. **Simula un teléfono con cola**: en *Teléfonos* mira la columna de rezago, o directo en la base
   `UPDATE dispositivos SET cola_pendiente = 3, cola_reportada_en = now()`.
2. **Abre un borrador de carga.** Se crea, y avisa que no se va a poder confirmar. El borrador no mueve
   inventario ni publica delta, así que dejarlo existir no cuesta nada.
3. **Intenta confirmar.** Se rechaza, nombrando el equipo y cuántas operaciones reportó.
4. **Pon la cola en cero** y vuelve a confirmar: pasa. El bloqueo desaparece solo cuando el teléfono
   sincroniza, que es lo que de verdad ocurre en el patio mientras alguien captura.
5. **Fuerza una**: con cola pendiente, marca la casilla y escribe por qué. Confirma — y revisa
   `SELECT accion, motivo, datos_despues FROM auditoria WHERE entidad = 'carga'`. Ahí está la razón con
   la lista de bloqueos que había.
6. **Comprueba que la razón no viajó**: `SELECT payload FROM change_log WHERE entidad = 'carga' ORDER BY
   cursor DESC LIMIT 1`. El texto no está. `cargas` se publica completa al teléfono, así que la razón
   vive en `auditoria`, que no lleva disparador.

Lo que esto previene, dicho una vez: el teléfono se queda con ventas del lunes sin subir, el martes se
carga el camión, las ventas del lunes llegan con su fecha vieja, los modelos recalculan el lunes, y el
`efectivo_esperado` de una liquidación **ya cerrada** se calculó antes de ellas. El arqueo firmado deja
de cuadrar y nada grita.

**Compras** — <http://127.0.0.1:8000/panel/compras>

Contesta tres preguntas que el sistema no podía: a quién le compramos, cuánto dinero hay en el
almacén, y a quién le debemos y cuándo vence.

1. **Da de alta un proveedor** con sus días de crédito. De ahí sale el **vencimiento** de cada cuenta
   por pagar; cero días es contado y vence el mismo día.
2. **Recibe una compra** en *Entradas* eligiendo ese proveedor y capturando el costo **por bulto**,
   como viene en la factura. El sistema divide entre el factor, pondera el promedio del producto y
   crea la cuenta por pagar.
3. **Comprueba el centavo.** 10 cajas a $296.00 tienen que dar una cuenta por pagar de **$2,960.00**
   exactos — no $2,959.99. El costo por pieza es 296/24 = 12.3333… que no es exacto, así que el
   importe se calcula sobre las cajas capturadas y no sobre las piezas en que se convirtieron. Con el
   centavo de menos, el pago de la factura completa se rechazaría por exceder el saldo.
4. **Registra un pago parcial y luego el resto.** El pago se aplica a **esa** factura, no «a lo que se
   le debe»: a un proveedor se le paga una factura concreta y así se puede conciliar contra su estado
   de cuenta.
5. **Mira el valor del inventario.** Está al promedio ponderado e incluye lo que traen los camiones:
   es inventario de la empresa aunque esté en la calle. Los productos con existencia y **sin costo**
   se cuentan aparte en vez de valuarse en cero.

Dos cosas que conviene entender antes de usarlo:

- **El costo NO está en `productos`**, y no es un detalle de organización. La migración 0010 publica la
  *fila completa* de esa tabla en `change_log` con `ruta_id = NULL`, o sea a todos los teléfonos: una
  columna de costo ahí pondría el margen de la empresa en el SQLite de cada vendedor. Vive en
  `producto_costos`, sin disparador, y hay dos pruebas que lo defienden.
- **Pagar necesita otro permiso** (`compras.pagar`, solo gerencia). Recibir mercancía y pagarla son dos
  manos distintas.

**Salidas de bodega** — <http://127.0.0.1:8000/panel/salidas>

La otra mitad: lo que sale de la bodega sin ir a un camión. Dos clases, y la diferencia entre ellas es
la que decide si hay algo que arreglar en la bodega.

1. **Abre un conteo físico.** La nota es obligatoria: hace falta saber quién contó.
2. **Captura lo que CONTASTE**, no la diferencia — el sistema resta. El renglón guarda las tres cifras y
   la base de datos impone que la resta cuadre (`CONSTRAINT conteo_cuadra`), así que un bug en el panel
   no puede escribir un conteo que no cuadre con su propia aritmética.
3. **Cuenta de más, a propósito**: si capturas más de lo que el sistema tiene, te manda a *Entradas* con
   motivo «ajuste». Encontrar sobrante no es una salida.
4. **Intenta sacar más de lo que hay.** Se rechaza, y el mensaje dice cuánto hay y cuánto sale. Esto
   **no** contradice §0.1: ese principio es para hechos que ya pasaron en la calle y llegan tarde —el
   teléfono manda una merma sin existencia y el servidor la MARCA, no la rechaza— mientras que una
   salida de bodega la está tecleando alguien con el anaquel a la vista. El anaquel no puede tener menos
   que nada.
5. **Cuenta, luego mueve inventario, luego confirma.** Si entre el conteo y el cierre salió una carga,
   el confirmar lo rechaza: el anaquel también perdió esas piezas, así que aplicar la resta guardada
   descontaría dos veces. Vuelve a capturar el renglón y queda.
6. **Una merma lleva motivo** del catálogo cerrado de la Fase 6 (`DANADO_BODEGA`, `CADUCADO`, `ROBO`…);
   un **faltante de conteo no lo lleva**, y el intento se rechaza con su razón: la causa de un faltante
   de conteo no se conoce —si se supiera, se habría capturado como merma el día que pasó— y elegir uno
   sin saber convierte un dato duro en una acusación inventada.

El flujo completo de la bodega es **Entradas → Inventario → Cargas**, con **Salidas** para corregir, y
las cuatro pantallas están enlazadas entre sí.

**Transferencias** — <http://127.0.0.1:8000/panel/transferencias>

La operación es de contado (ADR 0002 §81): no hay cobranza ni cartera. Esta
pantalla es solo para cuadrar el dinero que no llegó en la mano.

1. Arriba, las ventas pagadas por transferencia que **esperan al banco**, la más vieja primero.
2. Palomea las que ya viste en el estado de cuenta y **confírmalas de una vez**. Confirmar dos veces
   no hace nada la segunda.
3. «**No llegó**» pide motivo, y puede cargársela a la cuenta del vendedor.

**Cortes y cargas por aceptar** — <http://127.0.0.1:8000/panel/cierres>

Dos secciones, cada una con sus botones (ADR 0002 §82). **Primero el corte, después la carga.**

1. **Cortes por cerrar.** Cada vendedor que hizo su corte en el teléfono, con el efectivo («Declara
   $X de $Y · faltan $Z») y, por producto, lo que **traía, cargó, vendió y le queda**. Nadie lo
   contó: es el cálculo. Escribe en «Efectivo que recibes» lo que **tú** contaste y **Cerrar el
   corte**: el camión queda en ese cálculo, sin ajustes, y lo que falte de efectivo —contra lo que
   recibiste, no contra lo que él dijo— va a su cuenta. Si el teléfono tiene algo sin subir, no deja.
2. **Cargas por aceptar.** La que pidió cada vendedor para mañana. Mientras su corte siga abierto,
   sale el aviso «Primero cierra su corte» y el botón no responde.
3. En «Se carga», deja vacío para cargar lo que pidió; escribe otro número para cambiarlo, o 0 para
   quitarlo. **Aceptar** confirma la carga de mañana desde la bodega principal.
4. **Rechazar** pide motivo: le llega al vendedor y puede mandar otra.

**Inventario** — <http://127.0.0.1:8000/panel/inventario>

Cada artículo dice su **precio** por pieza y su **valor** (existencia × precio de venta), con el total
al pie y lo que vale cada almacén en su tarjeta (ADR 0002 §86). Un artículo sin precio dice «sin
precio» y no suma; el total avisa cuántos son. En la app del gerente lo mismo está en Almacén →
Existencias.

Los artículos van **agrupados por familia**, con lo que suma y vale cada una (ADR 0002 §89). Cada
familia se pliega o despliega al tocarla, y «Plegar todas» las cierra (§92). Las familias se
agregan, renombran y borran en *Productos → Familias*; borrar una no borra sus artículos, que
quedan «Sin familia». En la app, el catálogo del vendedor y «Mi camión» van igual.

**Lo cancelado va aparte** (§95): en Ventas, la pestaña «Canceladas»; en Entradas, Salidas, Cargas y
los movimientos del vendedor, un bloque plegado al final.

1. Escoge almacén. El camión es un almacén como cualquier otro: ahí ves lo que trae cada vendedor.
2. Un número **en negativo no es un error del sistema**: es un conteo por revisar. Pasa cuando se cargó
   más de lo registrado, o cuando una venta offline entró con el camión ya en cero.
3. Y un saldo que **no amanece en cero es lo normal**: el camión es un almacén rodante y lo que no se
   vendió se queda arriba (ADR 0002 §17). Lo que lo pone en cero es que se venda todo, no que pase la
   noche.
4. Si la pantalla dice que **la caché no cuadra con el libro mayor**, eso **sí** es un bug: alguna
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

## 1.6-ter Lo que la Fase 9 añadió al arranque

Tres cosas que conviene conocer aunque en desarrollo no hagan falta.

### Los logs ahora son legibles o JSON, según el entorno

En desarrollo salen en texto, con el identificador de petición al frente:

```
10:41:03 INFO    [a3f91c0b] dsd.http: POST /v1/sync/push → 200
```

Ese `[a3f91c0b]` es el `peticion_id`, y también viaja en la cabecera
`X-Peticion-Id` de la respuesta. Cuando el vendedor diga «no me llegó», ese
identificador es lo que permite encontrar su petición entre las de ocho camiones
sincronizando a la vez.

En producción los logs salen en JSON, una línea por registro. Lo controla
`DSD_LOGS_JSON`, que por omisión sigue al entorno.

> **Lo que nunca sale en un log**: el payload de un sobre, las coordenadas, los
> tokens y las contraseñas. Está en `app/core/registro.py` con la lista completa
> y la razón de cada una.

### Las métricas están apagadas, y así se encienden

```bash
# Solo si quieres verlas en local:
export DSD_METRICAS_TOKEN="$(openssl rand -hex 16)"
make api
curl -s -H "Authorization: Bearer $DSD_METRICAS_TOKEN" \
     http://127.0.0.1:8000/metrics | head -30
```

Sin el token el endpoint **no existe** (404, no 401). Y nunca lleva dinero: solo
salud —colas, equipos rezagados, cuarentena, latencia— porque un endpoint de
monitoreo acaba en una serie temporal que nadie protege como el panel.

Lo que vale la pena mirar:

| Métrica | Si crece |
|---|---|
| `dsd_jobs_pendientes` | El worker está caído o atorado. El tablero se congela y la analítica deja de refrescarse **sin que nada más falle** |
| `dsd_jobs_fallidos` | Algo se reintentó hasta rendirse |
| `dsd_cuarentena_pendiente` | Dinero que ocurrió en la calle y no está en ninguna cifra |
| `dsd_equipos_rezagados` | Teléfonos que no han subido hoy |

### RLS: en desarrollo está APAGADO, y el arranque lo dice

Al levantar `make api` verás este aviso:

```
WARNING dsd.arranque: sin DSD_DATABASE_URL_API: las políticas por renglón
NO se están aplicando (ver db/ops/rol_api.sql)
```

Es correcto en desarrollo y **la API de producción no arranca sin esa
variable**. `curl -s http://127.0.0.1:8000/salud | jq .rls` lo confirma.

Si quieres probar con RLS activo en local —vale la pena una vez, para ver que
todo sigue funcionando con el rol restringido:

```bash
psql "postgresql://postgres:dsd@127.0.0.1:5432/dsd" \
     -v clave_api=rls_local -f server/db/ops/rol_api.sql
DSD_DATABASE_URL_API="postgresql+psycopg://dsd_api:rls_local@127.0.0.1:5432/dsd" make api
```

> **Si una pantalla del panel empieza a salir vacía**, lo primero que hay que
> descartar es RLS: un alcance que no se fijó devuelve cero renglones **sin
> ningún error en el log**. Es el modo de fallo propio de esta fase y está
> explicado en el ADR §35.

### Respaldos

```bash
make respaldo     # a ~/respaldos-dsd, con su checksum
make simulacro    # lo restaura en una base desechable y lo verifica
```

El segundo es el que importa: un respaldo que nunca restauraste no es un
respaldo. Todo lo demás —el cron, sacarlo del edificio, cómo restaurar de
verdad— está en [`docs/RESPALDOS.md`](RESPALDOS.md).

## 1.7 Antes de dar por bueno un cambio

```bash
make lint          # ruff sobre app y tests
make pruebas       # 1068 pruebas de Python — necesita la base arriba
make movil         # 556 de Dart + 252 de widget
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

### Corte del día y solicitud de carga — en «Mi día», al terminar la ruta

1. En **Mi día** (solo hoy) aparece «**Cierre del día**» → **Hacer el corte del día**.
2. **No hay nada que contar** (ADR 0002 §82): la pantalla muestra «Lo que te queda en el camión»
   —lo que traías, más tu carga, menos lo que vendiste— y lo vendido hoy, producto por producto.
   Solo pide el efectivo: intenta terminar sin escribirlo — **no debe pasar**.
3. Escribe el efectivo que entregas y termina. Sale el **ticket del corte**: lo vendido, «Faltan/Sobran
   $X contra lo vendido en efectivo» y «LE QUEDA EN EL CAMIÓN». No dice nada de transferencias.
4. **Compartir** abre la hoja del teléfono (WhatsApp, correo). El texto llega con negritas en WhatsApp.
5. **Solicitar carga para mañana**: busca productos y escribe cuántas cajas. Envía. Sale el ticket de
   la solicitud, «Pendiente: la revisa la oficina».
6. Todo esto funciona **sin señal**. Al sincronizar viaja a la oficina; cuando cierren tu corte y
   acepten la carga, Mi día dice «Carga de mañana ACEPTADA: CG-…» y el ticket cambia a «CARGA
   ACEPTADA», con lo que de verdad se cargó si la oficina cambió algo.

### Cerrar cortes y aceptar cargas — app de Gerencia → Almacén

1. **Corte del día** → arriba, «Cortes de los vendedores». Abre uno: el efectivo y lo que le queda en
   el camión (traía, cargó, vendió). **Cuenta el dinero que te entrega** y escríbelo en «Efectivo que
   recibes» (arranca en lo que él declaró); abajo dice al momento si falta o sobra. **Cerrar el
   corte**: lo que falte contra lo vendido va a su cuenta, con lo que tú contaste (ADR 0002 §87).
2. **Cargas** → arriba, «Cargas que pidieron los vendedores». Si su corte sigue abierto dice «Espera
   su corte» y no deja aceptar. Ya cerrado: corrige un renglón si hace falta y **Aceptar y confirmar
   la carga**. Sale el ticket para compartir. **Rechazar** pide motivo.

### Cobrar — solo efectivo

En el carrito **no hay forma de pago que elegir**: el botón dice «Cobrar en efectivo» (ADR 0002 §81).
Mi día y el tablero ya no muestran transferencias cuando no las hay.

### Perfil y ubicación del cliente — menú ⋮ de la visita

1. **Perfil y ubicación** muestra los datos del cliente y su ubicación (ADR 0002 §84).
2. **Tomar con el GPS** llena latitud y longitud y dice el `±N m`. Bajo techo dice que no leyó.
3. Escríbela a mano, o **pega** «23.2494, -106.4111» en el campo de latitud: se reparte en los dos.
4. Prueba ponerlas **al revés**, o la longitud sin el signo menos: **no debe guardar**, y lo dice.
5. Guarda: queda en el teléfono al momento (la geocerca la usa ya) y viaja al sincronizar.

### Recibir una compra — app de Gerencia → Almacén → Entradas

1. **Recibir compra** funciona **sin señal** (ADR 0002 §83): con el modo avión puesto, captura
   proveedor, productos y cuántas cajas. El costo es **opcional**.
2. Guarda: «Compra guardada en el teléfono». La tarjeta dice «1 compra(s) por mandar».
3. Quita el modo avión y toca **Mandar ahora** (o vuelve a abrir Entradas): aparece «Entró: EN-…» y
   la bodega principal sube. Mandarla dos veces no la suma dos veces.

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
   tenga—. Averigua la IP de tu PC en la red local y compila con ella:

   ```bash
   ip -4 addr show eth0 | grep inet
   cd mobile/app
   flutter run --dart-define=DSD_BASE_URL=http://192.168.1.50:8000
   ```

   **Si ese comando te da una `172.x.x.x`, no es la IP de tu red local**: es la red
   NAT interna de WSL2, y el teléfono no tiene ruta hacia ella. Ningún ajuste de
   firewall lo arregla. El camino es poner WSL en **modo espejo** —
   `networkingMode=mirrored` en `C:\Users\<usuario>\.wslconfig` y `wsl --shutdown` —
   y el procedimiento completo, con la alternativa por `portproxy` para Windows 10,
   está en [SIMULACRO.md](SIMULACRO.md), apartado «El teléfono en tu red local».

   Y la API tiene que escuchar en la red, no solo en localhost: `make api` ya
   levanta uvicorn en `0.0.0.0`. El **firewall de Windows** sobre el puerto 8000 es
   lo último que hay que descartar, no lo primero: antes va el modo de red de WSL.

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

### Teléfonos y borrado remoto — Panel → Teléfonos

Es la pantalla de la Fase 9, y la que conviene mirar una vez al día en
producción. Qué validar:

1. **El orden.** Los equipos salen ordenados por rezago: arriba lo que urge.
   Pon `ultima_sync_push_en` a cuatro días atrás en un equipo de prueba y
   comprueba que sube al principio y que la columna «acceso sin red» dice los
   días que le quedan.
2. **Suspender y reactivar.** Reversible. Con el equipo suspendido, la app
   **todavía puede subir** y no puede bajar; es la base del borrado remoto.
3. **Ordenar un borrado.** Exige escribir `BORRAR` en mayúsculas y un motivo.
   Fíjate en que el equipo queda **suspendido**, no revocado — si lo revocara,
   no podría entregar lo que trae y el borrado costaría las ventas del día.
4. **Cancelar la orden.** Funciona mientras el teléfono no la haya ejecutado.
5. **Lo que NO hace revocar.** Revocar mata los tokens y no toca la copia del
   teléfono. Es la confusión que más cuesta, y la pantalla lo dice.

6. **La clave del equipo** (ADR 0002 §85). Al vincular un teléfono escribe una clave corta —«RUTA4»—
   o déjala vacía y el panel inventa una de seis. Sale en la lista, debajo de la etiqueta, y se puede
   cambiar con «Cambiar clave». En el teléfono va en **«Clave del equipo»**, junto con el código y la
   contraseña del vendedor; ya no hace falta teclear el identificador largo (aunque sigue sirviendo).
   Una clave repetida o con acentos no se guarda, y se dice por qué.

El flujo completo con el teléfono en la mano está en
[`docs/SEGURIDAD-OPERATIVA.md`](SEGURIDAD-OPERATIVA.md) §2.

### El borrado remoto visto desde el teléfono

Con el equipo conectado y un borrado ordenado desde el panel:

1. **Con cola pendiente** (haz una venta sin señal primero): la app muestra la
   pantalla de equipo bloqueado con el **número** de operaciones por subir y el
   motivo que escribió la oficina. **Nada se borra.**
2. **Dale señal y toca «Buscar señal y entregar»**: sube lo pendiente y recién
   entonces se borra. Aparece la pantalla de «este equipo quedó limpio», sin
   botones — no hay ninguna salida que dar desde el teléfono.
3. **Comprueba en el panel** que la columna dice «borrado confirmado» con su
   hora y `cola al borrar: 0`. Eso es lo único que prueba que ocurrió.
4. **Cierra y abre la app**: ya no entra, porque la credencial del Keystore se
   borró con todo lo demás.

> Hazlo con un equipo de prueba. El borrado **no se deshace**.

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
| **3** | App vendedor MVP + piloto de campo | 3.5 sem | 🟡 **parcial** | ~70 % |
| **4** | Inventario de camión, liquidación | 2.5 sem | 🟡 casi | ~90 % |
| **5** | Crédito y cobranza | 2.5 sem | ✅ completa | 100 % |
| **6** | Alta en calle, mermas, no-drops | 2.0 sem | ✅ completa | 100 % |
| **7** | **Perfil Gerencia móvil** | 2.0 sem | ✅ completa | 100 % |
| **8** | Laboratorio analítico (Streamlit) | 3.5 sem | 🟡 **casi** | ~85 % |
| **9** | Endurecimiento, MDM, RLS | 2.5 sem | ✅ completa | 100 % |

## **Avance general: ≈ 93 %**

El cálculo, semana a semana de plan:

```
Fase 0   2.5 × 1.00 = 2.50      Fase 5   2.5 × 1.00 = 2.50
Fase 1   2.5 × 1.00 = 2.50      Fase 6   2.0 × 1.00 = 2.00
Fase 2   3.5 × 1.00 = 3.50      Fase 7   2.0 × 1.00 = 2.00
Fase 3   3.5 × 0.70 = 2.45      Fase 8   3.5 × 0.85 = 2.98
Fase 4   2.5 × 0.90 = 2.25      Fase 9   2.5 × 1.00 = 2.50
                                ─────────────────────────────
                                25.18 de 27 semanas = 93.3 %
```

**Lo que falta son 1.82 semanas, y 2 son calendario** — sí, menos semanas de
trabajo que de calendario, y no es un error de cuentas: el piloto de la Fase 3
son dos semanas que **nadie puede acelerar**, y su trabajo de oficina (veinte
minutos al día de captura y la junta del día 15) cabe de sobra en ellas.

El instrumento de ese piloto ya está construido —la pantalla *Piloto*, el cuadre
diario contra el papel, la bitácora y los doce criterios de salida sembrados
antes del primer día— y eso es lo único de esas dos semanas que se podía hacer
por adelantado. **No las acorta ni un día.** Lo que cambia es que al final de las
dos semanas haya un veredicto con cifras en vez de una impresión.

El resto son el socket Bluetooth (media semana, cuando llegue la EC-MP200), la
consulta online de bodega desde la app, y los modelos de pronóstico de la Fase 8
— que conviene hacer DESPUÉS del piloto, con meses de datos reales.

### Qué le falta a lo que está «parcial»

**Fase 3 (~70 %)** — el código está completo: catálogo offline, carrito, venta, cola de sincronización y
los bytes del ticket ESC/POS, todo probado. Y desde ahora también está el **instrumento del piloto**:
`docs/PILOTO.md` con el protocolo, la pantalla *Piloto* del panel, el cuadre diario contra el papel, la
bitácora y los doce criterios de salida. Faltan dos cosas, y ninguna es de código:

- **El transporte Bluetooth.** Los bytes del ticket están generados y verificados byte a byte (59
  pruebas), pero `Impresora` solo tiene implementación simulada: falta el socket, y eso espera a que
  recuperes la **EC-MP200**. Es media semana de trabajo cuando llegue el hardware. El piloto puede
  arrancar sin él —el papel va en paralelo, así que el comprobante del cliente sigue siendo la nota de
  siempre— y el criterio está declarado como **no evaluable** para que no se dé por probado.
- **Las dos semanas de piloto, que son calendario.** El vendedor en su ruta, con el papel al lado y la
  oficina capturando el cuadre cada mañana. Eso no se adelanta: empieza el lunes que decidas y termina
  catorce días después.

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

### Un hueco que el plan no tenía, y el porcentaje no refleja

Durante nueve fases **no hubo cómo meter mercancía al sistema**. El plan pone
«Compras/recepción» en la Fase 10 («Opcionales»), que queda fuera de las 27 semanas, así que ninguna
fase lo echaba de menos — y el libro mayor ya contemplaba los asientos de `'compra'` y `'ajuste'` desde
la migración 0004, con el permiso `inventario.ajustar` definido desde la 0009 sin que ningún rol lo
tuviera ni ningún código lo pidiera.

La forma del fallo es la que lo hizo durar: **no fallaba.** Confirmar una carga desde una bodega vacía
funciona —`existencias` no lleva `CHECK` de signo a propósito (§0.1)— así que el sistema dejaba la
bodega en negativo sin quejarse. La única forma de operar era inyectar inventario con SQL a mano.

Ya está construido: `/panel/entradas`, con documento, folio, ciclo borrador→confirmada y los tres
motivos (compra, inventario inicial, ajuste por conteo). **El porcentaje general no se mueve**, y es
correcto que no se mueva: no era alcance planeado de las fases 0–9. Lo que cambia es que el sistema se
puede operar sin tocar la base de datos a mano.

Y las **salidas** también: `/panel/salidas`, con el conteo físico que encuentra menos y la merma de
bodega. Las dos direcciones del ajuste están construidas, y con ellas un negativo de bodega ya tiene
cómo arreglarse — la pantalla de inventario lo dice en el renglón mismo.

Y el **módulo de compras** (Fase 10) también: `/panel/compras`, con el catálogo de proveedores, el
**costo promedio ponderado** y las **cuentas por pagar**. Con eso el sistema sabe por primera vez lo
que cuesta el inventario — el laboratorio de la Fase 8 no tenía ni una métrica de margen porque no
había con qué calcularla.

Lo que **sigue faltando** de esa familia son las **órdenes de compra** (su valor es anticipar lo que
viene en camino, y eso importa a una escala que este negocio todavía no tiene), los **anticipos** a
proveedor, y el **CFDI**. Ninguno bloquea operar.

### Una advertencia sobre la numeración

**Los números de fase que usamos al trabajar no coinciden con los de `ARQUITECTURA.md`.** En las
conversaciones llamamos «Fase 7» a la liquidación, pero en el plan la liquidación es parte de la **Fase
4**, y la **Fase 7** es el *dashboard de Gerencia móvil*. Esta tabla usa la numeración del plan, que es
la que vale para medir.

### Lo que el porcentaje no dice

El 93 % es de **alcance planeado**, y hay dos razones por las que el proyecto está mejor de lo que ese
número sugiere:

1. **Lo construido está probado de verdad**: 1068 pruebas de Python contra PostgreSQL real, 556 de Dart,
   252 de widget, y **siete** verificaciones de frescura de contratos en CI —vectores canónicos, deltas,
   importes, OpenAPI, ticket, sobres y esquema local—, cada una capaz de poner el CI en rojo si el
   código y su contrato se separan. No hay deuda oculta en lo hecho.
2. **Lo que falta es lo menos riesgoso.** La Fase 2 —el motor de sincronización, la que puede hundir un
   proyecto de DSD— está cerrada con pruebas de caos, y la 9 (endurecimiento, RLS, borrado remoto) ya
   está hecha. De lo que queda, dos de las 2.17 semanas son el piloto: calendario, no código.

Y una razón por la que está peor:

3. **Nada de esto ha visto un vendedor real.** Sigue siendo cierto y sigue siendo lo único que puede
   invalidar decisiones de diseño. Lo que cambió es que ya hay con qué medirlo: las 1003 pruebas
   demuestran que el sistema hace lo que decidimos, y **ninguna demuestra que lo que decidimos sea lo
   correcto**. Eso solo lo dice un vendedor en la calle, y ahora el piloto produce un veredicto con
   cifras en vez de una anécdota. Dos semanas de un vendedor con el teléfono en la mano valen más que
   las dos fases siguientes juntas.

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

# ───── respaldos (Fase 9) ─────
make respaldo                         # a ~/respaldos-dsd, con checksum
make simulacro                        # lo restaura y lo verifica — ESTE importa

# ───── piloto de campo (Fase 3) ─────
make piloto-listo VENDEDOR=VEND01     # ¿puede salir el lunes? lo que falta y cómo
#                                     # el protocolo:  docs/PILOTO.md
#                                     # la pantalla:   /panel/piloto

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
