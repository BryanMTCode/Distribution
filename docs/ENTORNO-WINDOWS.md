# Entorno de desarrollo — Windows 11

Guía para dejar la máquina lista y para seguir el avance del proyecto a lo largo de las fases.

---

## 0. La decisión que ahorra semanas: WSL2

Todo este stack —Makefile, rutas de Unix, PostgreSQL, Docker— asume Linux. En PowerShell nativo vas a
pelear con detalles que no aportan nada al negocio: rutas con `\`, permisos, saltos de línea, `make`
que no existe.

**Trabaja dentro de WSL2 con Ubuntu.** Media hora de configuración. Windows sigue siendo tu escritorio;
Ubuntu es donde vive el código.

Abre **PowerShell como Administrador** y corre:

```powershell
wsl --install -d Ubuntu-24.04
```

Reinicia. Al volver, Ubuntu te pedirá un usuario y contraseña (son de Linux, no de Windows; anótalos).

Comprueba:

```powershell
wsl -l -v          # debe decir Ubuntu-24.04, VERSION 2
```

> **Regla que no se rompe:** el código vive en el sistema de archivos de **Linux**
> (`/home/tu-usuario/...`), nunca en `/mnt/c/...`. Trabajar desde `/mnt/c` hace que todo vaya entre 5 y
> 10 veces más lento y rompe permisos de Git.

---

## 1. Qué instalar

### En Windows

| Programa | Para qué | Cómo |
|---|---|---|
| **Docker Desktop** | Corre PostgreSQL sin instalarlo nativo | `winget install Docker.DockerDesktop` |
| **VS Code** | Editor | `winget install Microsoft.VisualStudioCode` |
| **Git para Windows** | Opcional; dentro de WSL usarás el de Ubuntu | `winget install Git.Git` |

Después de instalar Docker Desktop: ábrelo → **Settings → Resources → WSL Integration** → activa
**Ubuntu-24.04**. Sin ese paso, `docker` no existe dentro de Ubuntu.

**Extensiones de VS Code** (búscalas por nombre e instálalas):

- **WSL** — indispensable, es la que abre el editor dentro de Ubuntu
- **Python** y **Ruff**
- **Docker**
- **Flutter** (para la Fase 3)

### Dentro de Ubuntu (WSL)

Abre la terminal de Ubuntu desde el menú Inicio y corre:

```bash
sudo apt update && sudo apt install -y git make curl postgresql-client
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
```

**No instales Python ni PostgreSQL.** `uv` baja Python 3.12 solo; PostgreSQL corre en Docker.
`postgresql-client` es solo `psql`, para inspeccionar la base a mano.

---

## 2. Arranque

```bash
git clone https://github.com/BryanMTCode/Distribution.git
cd Distribution
git checkout claude/exciting-hamilton-asnv8o

make instalar     # uv baja Python 3.12 y las dependencias
make db           # PostgreSQL + PostGIS en Docker
make migrar       # aplica las 8 migraciones
make pruebas      # deben pasar 148
```

Si ves `148 passed`, tu entorno está bien. Si no, el error casi siempre es uno de estos tres:

| Síntoma | Causa | Arreglo |
|---|---|---|
| `docker: command not found` | Falta la integración WSL | Docker Desktop → Settings → Resources → WSL Integration |
| `connection refused` en 5432 | La base no está arriba | `make db` |
| `port is already allocated` | Otro PostgreSQL ocupa el 5432 | `make db-parar` y vuelve a `make db` |

Para abrir el proyecto en el editor, **desde la terminal de Ubuntu**:

```bash
code .
```

Eso abre VS Code conectado a WSL. Abajo a la izquierda debe decir `WSL: Ubuntu-24.04`.

### Ver la API funcionando

```bash
make api
```

Abre <http://localhost:8000/docs> en tu navegador de Windows: es la documentación interactiva de la API,
generada sola. Puedes probar el login desde ahí.

### Crear tu usuario (la primera vez, y cada vez que recrees la base)

**No hay usuario ni contraseña por omisión, y es a propósito.** Ninguna migración siembra usuarios.

```bash
make usuario
```

Te pregunta código de empleado, nombre, rol y contraseña. La contraseña **se teclea, no se pasa como
argumento**: un `--password` queda en el historial del shell y en la lista de procesos, donde lo ve
cualquiera con `ps`.

Si acabas de hacer `make db-parar && make db && make migrar`, la base está limpia y esto es lo primero
que necesitas. Es el único camino de entrada cuando no hay usuarios.

> **Por qué no hay un `admin / admin123` sembrado.** El problema no es el desarrollo: es que esa fila
> viaja a producción, nadie se acuerda de cambiarla, y queda un usuario con todos los permisos cuya
> contraseña está publicada en el repositorio. Con el panel detrás del túnel de Cloudflare, eso es
> acceso a la operación completa desde internet. La otra opción común —una pantalla de «primer
> arranque» sin autenticar— deja una ruta que crea administradores sin credenciales; basta que la
> condición «¿ya hay usuarios?» se evalúe mal una vez para que quede abierta. Quien puede correr
> `make usuario` ya está dentro del servidor, así que no concede nada nuevo y no deja ninguna puerta.

Los demás usuarios —vendedores, supervisores, gerencia— se dan de alta **desde el panel**, en la
pantalla de Equipo. Este comando existe solo para el primero.

### Ver el panel de operación

Con el mismo `make api` corriendo, abre <http://localhost:8000/panel>.

Entra con un usuario de oficina (rol `admin` o `gerente`). **Un vendedor no puede entrar al panel**: si
teclea sus datos ahí, el panel le responde que entre por la app del teléfono. Es a propósito — el panel
ve toda la operación y el teléfono solo la ruta de quien lo trae.

Siete pantallas, y cada una responde una pregunta distinta:

| Pantalla | Qué contesta |
|---|---|
| **Tablero** | ¿cómo va el día? Ventas sincronizadas, importe, sobres en cuarentena, equipos que no reportan |
| **Productos** | el catálogo y los precios. Es por donde entra el negocio real al sistema |
| **Clientes** | ¿qué prospectos levantó la ruta y esperan código, lista de precios y crédito? |
| **Cargas** | qué se subió a cada camión hoy. Es el inventario con el que el vendedor puede vender |
| **Equipo** | usuarios, rutas, almacenes y listas de precios. La estructura sobre la que corre todo |
| **Ventas marcadas** | ¿qué ventas entraron con una advertencia? Son las que el servidor **sí** aceptó pero marcó |
| **Cuarentena** | ¿qué rechazó el servidor, y por qué? Con el sobre completo, tal como llegó |

Las dos últimas son las dos mitades del §0.1 ("el mundo físico ya ocurrió"): el servidor marca en vez de
rechazar, y alguien en la oficina tiene que poder **ver** esas marcas o la regla no sirve de nada.

### Capturar el primer producto de verdad

Esto es lo que reemplaza al Modo Demo cuando quieras probar con producto real:

1. **Productos → Nuevo producto.** SKU, nombre, unidad base `PZA`, y en «presentación adicional»
   `CAJA` con las piezas que trae (24, por ejemplo).
2. En el detalle, captura el **precio de la CAJA** en la lista general. Digamos $296.00.
3. En el renglón de `PZA` vas a ver la cuenta hecha: `$296.00 de CAJA ÷ 24 × 1 = $12.3333`.
   Toca **Usar** y queda guardada con sus cuatro decimales.

Ese $12.3333 es el punto entero del diseño: si capturaras $12.33, las 24 piezas sumarían $295.92 y se
te irían ocho centavos por caja, todos los días. Con cuatro decimales, 24 × $12.3333 vuelve a dar los
$296.00 exactos, porque el redondeo ocurre **una sola vez, sobre el importe**.

El producto viaja al teléfono en el siguiente `pull`: los disparadores de la base publican el cambio en
`change_log` solos, sin que ninguna pantalla se tenga que acordar.

### Dar de alta a un vendedor completo

Para que un vendedor salga a la calle hacen falta **tres cosas atadas entre sí**, y si falta una el
sistema no avisa: simplemente no deja hacer el trabajo.

En **Equipo → Nuevo usuario**, con rol `vendedor` y la casilla «crearle también su camión» marcada,
quedan dos de las tres en un solo paso. Después, en la misma pantalla, **crea su ruta y asígnalo como
titular**.

La pantalla marca arriba, en rojo, los vendedores **sin camión** y **sin ruta**. Esa alerta existe
porque un vendedor incompleto se ve idéntico a uno completo en cualquier lista de usuarios:

- **Sin camión** no se le puede cargar mercancía (la carga no tiene a dónde ir).
- **Sin ruta** su teléfono no recibe un solo cliente.

Lo segundo tiene una trampa que vale conocer: `rutas.vendedor_id` dice quién es el *titular*, pero lo
que filtra los datos que viajan al teléfono es la tabla `usuarios_rutas`. Son dos cosas distintas y las
dos hacen falta. El panel escribe las dos siempre; si algún día lo haces por SQL, acuérdate de la
segunda o vas a pasar una tarde depurando por qué el teléfono sincroniza bien y llega sin clientes.

### Cargar el camión de verdad

Esto es lo que por fin reemplaza al Modo Demo: hasta ahora el inventario del camión
solo se podía sembrar con datos inventados.

1. **Cargas → Abrir borrador.** Eliges vendedor, bodega y el día operativo. El camión
   no se elige: es el almacén del vendedor.
2. Captura lo que se sube, **en bultos**: `ATUN-140`, `10`, `CAJA`. El SKU o el código
   de barras sirven igual — quien está en la bodega tiene un lector en la mano.
   El sistema lo convierte: 10 cajas de 24 son 240 piezas, y así se guardan.
3. Cada renglón muestra **cuánto hay en bodega** al lado. Si no alcanza, lo dice en rojo.
4. **Confirmar la carga.**

Con eso pasan tres cosas en una sola transacción: el movimiento entra al libro mayor
(que es append-only: no se puede borrar), la bodega baja y el camión sube, y el teléfono
del vendedor recibe la carga con su detalle en el siguiente `pull`. Ahí es donde
`existencias_camion` se llena con datos reales.

Un par de cosas que conviene saber antes de usarlo en serio:

- **El borrador no llega al teléfono.** Mientras armas la lista puedes corregir y quitar
  renglones sin que el vendedor vea nada. Si llegara, vería mercancía que no tiene.
- **Si la bodega no alcanza, se confirma igual** y queda en negativo. No es un descuido:
  si el almacenista está subiendo 10 cajas y el sistema dice 8, el que está mal es el
  sistema. Rechazarlo haría que el camión saliera con mercancía sin registrar.
- **Una carga confirmada ya no se edita ni se cancela.** Lo que salió se corrige con un
  traspaso o un ajuste; lo que regresa al final del día es un retorno.

### Confirmar un prospecto levantado en ruta

Cuando el vendedor da de alta una tienda en la calle, el servidor la guarda **prospecto**: sin código,
sin lista de precios y con límite de crédito en cero. Eso es a propósito — el vendedor no se autoriza su
propia cartera. Pero hay que terminarlo desde aquí, o cada venta a ese cliente entra marcada como «no
trae lista de precios».

En **Clientes** (abre directo en «Por confirmar»), entra al negocio y toca **Confirmar y asignar código**.
El crédito se decide aparte, más abajo en la misma pantalla: confirmar que un negocio existe y decidir
cuánto se le presta son dos juicios distintos.

El panel no carga nada de internet: ni una hoja de estilos, ni una librería de JavaScript. El servidor de
la oficina puede quedarse sin salida a internet y el panel sigue viéndose igual. Si usáramos un CDN, una
herramienta de red local dependería de una conexión que puede no estar.

En desarrollo el `make api` enciende `DSD_DEBUG=1`, y eso apaga el atributo `Secure` de la cookie de
sesión — sin eso el navegador no la manda por `http` y el panel te devolvería al login en cada clic. En
producción el panel va detrás de Caddy con TLS y el atributo se enciende solo.

### Comandos del día a día

```bash
make ayuda        # lista todo lo disponible
make db           # levanta la base
make pruebas      # la suite completa
make lint         # revisa estilo
make usuario      # crea el primer usuario de oficina (pregunta la contraseña)
make api          # API con recarga automática (y el panel en /panel)
make contratos    # regenera vectores y OpenAPI — REVISA EL DIFF
make db-parar     # apaga y borra el contenedor de la base
```

---

## 3. Cómo monitorear el avance

### En GitHub (lo más importante)

| Qué | Dónde |
|---|---|
| **Qué se hizo y por qué** | [Commits de la rama](https://github.com/BryanMTCode/Distribution/commits/claude/exciting-hamilton-asnv8o) — cada mensaje explica la decisión, no solo el cambio |
| **Si todo sigue en verde** | [Pestaña Actions](https://github.com/BryanMTCode/Distribution/actions) — corre en cada `push` |
| **Ver un cambio a detalle** | Clic en cualquier commit: muestra el diff línea por línea |

**Lee la pestaña Actions como semáforo.** Verde = las 148 pruebas pasan, el estilo está limpio y los
contratos entre Dart y Python siguen coincidiendo. Rojo = algo se rompió, y el registro dice qué.

### En tu máquina

```bash
git pull                    # trae lo último
make pruebas                # confirma que corre en tu equipo, no solo en el mío
```

Que pase en CI y falle en tu máquina es información valiosa: casi siempre significa que algo depende
del entorno y no debería.

### En los documentos

| Archivo | Qué te dice |
|---|---|
| [`README.md`](../README.md) | Tabla de estado: qué pieza está lista y cuál no |
| [`docs/ARQUITECTURA.md`](ARQUITECTURA.md) | El plan de 11 fases con duraciones |
| [`docs/adr/`](adr/) | Cada decisión técnica grande, con las alternativas descartadas y por qué |

### Una señal de alarma que debes saber leer

Si en CI falla el paso **"Los vectores canónicos están al día"**, significa que alguien cambió la forma
en que se serializan los datos sin actualizar el contrato compartido. Eso, sin atrapar, se convierte
meses después en miles de tickets en cuarentena. Que falle el CI es exactamente lo que quieres.

---

## 4. Para la Fase 3 (todavía no)

Cuando llegue la app móvil:

```bash
sudo snap install flutter --classic    # dentro de WSL
```

Y en Windows: **Android Studio** (`winget install Google.AndroidStudio`), solo por el SDK de Android.

> **Aviso:** WSL2 no tiene acceso directo a USB ni a Bluetooth. Para probar en un teléfono real
> —que es lo único que vale para Bluetooth y GPS— vas a necesitar `usbipd-win`, o instalar Flutter
> directamente en Windows para la parte móvil. Se resuelve al llegar a la Fase 3; no lo montes ahora.

También necesitarás, físicamente:

- Un **teléfono Android de gama baja**, el mismo modelo que usarán tus vendedores.
- Una **impresora térmica Bluetooth de 58 mm**, la que vayas a comprar en volumen.

El emulador no sirve: no tiene Bluetooth ni GPS de verdad.

---

## 4.1 Evaluar la app en el teléfono sin levantar el servidor

Una instalación nueva no tiene credencial guardada, así que el login **exige conexión la primera
vez**. Es la regla de seguridad correcta —si no fuera así, cualquiera que robe un teléfono nuevo
entraría sin que la oficina haya autorizado nada—, pero estorba cuando lo único que quieres es ver
la interfaz en la mano.

Para eso existe el **modo demo**: se compila explícitamente, siembra una sesión y datos locales, y
entra directo a la ruta. No necesitas Python corriendo, ni `adb reverse`, ni red.

```bash
make app-demo     # equivale a: flutter run --dart-define=DSD_DEMO=true
```

O si prefieres instalar un APK a mano y luego desconectar el cable —lo natural para salir a la
calle—:

```bash
make apk-demo
adb install -r mobile/app/build/app/outputs/flutter-apk/app-debug.apk
```

En la pantalla de login aparece **"Sembrar datos y entrar"**. El PIN también se muestra ahí
(`481507`) por si quieres recorrer el login normal escribiéndolo.

### Qué siembra, y por qué eso

Cinco clientes elegidos para que se vean **de un golpe los cuatro estados de crédito** que la lista
distingue, no relleno:

| Cliente | Para ver |
|---|---|
| Abarrotes Doña Mary | crédito disponible normal |
| La Esquina de Ñoño 🏪 | disponible **ya descontado** por una venta encolada sin sincronizar (límite 3000 − saldo 900 − venta 1500 = **$600.00**) |
| Tienda del Mercado, local 12 | crédito agotado |
| Miscelánea El Buen Precio | bloqueado |
| Cremería Los Compadres | solo contado |

Los clientes se colocan **alrededor de tu posición real de GPS** (a 25, 45, 300, 600 y 900 metros).
Eso es a propósito: con dos vecinos dentro del radio de 60 m, el **aviso de posible duplicado** se
dispara de verdad en el lugar donde estés parado. Es lo único que un emulador no puede evaluar. Si
el GPS no da lectura, usa coordenadas fijas y no falla.

También siembra el **catálogo y la carga del camión**, que es lo que hace evaluable el carrito:

| Producto | Para ver |
|---|---|
| Sopa de fideo 70 g | caja de 24 a $296.00 y pieza a **$12.3333** — el precio de 4 decimales |
| Frijol bayo 1 kg, Aceite 900 ml | dos presentaciones con factores distintos (20 y 12) |
| Aceite 900 ml | poca existencia (30 pza): con 3 cajas se agota y sale el aviso de *"solo quedan N"* |
| Azúcar 1 kg | solo pieza, 4 en el camión |
| Atún 140 g | **Agotado** (subió al camión y se vendió todo) |
| Jabón 150 g | **No va en la carga** (la bodega no lo subió) — estado distinto de agotado |

Lo que vale la pena comprobar con el pulgar:

1. Toca un cliente → abre **su** catálogo, con **sus** precios.
2. Agrega una caja de sopa: el total debe decir **$296.00**, y la existencia bajar de 240 a 216.
3. Agrega 24 piezas en vez de la caja: el total debe ser **$296.00 otra vez**, no $295.92.
4. Intenta 3 cajas de aceite (solo hay 30 pza y la caja es de 12): debe decir **"Solo quedan 2.500"** —
   en cajas, no en piezas. Ese `.500` es información útil: puede llevarse 2 cajas y 6 piezas.
5. En el pedido, cambia a **Crédito** con *La Esquina de Ñoño*: debe decir cuánto le queda de línea, ya
   descontada la venta encolada de $1500.
6. **Cobra.** Debe aparecer el folio `VEND01-000001` en grande, el total, y el aviso de que se envía
   sola cuando haya señal. La remisión **no se imprime sola**: toca *"Imprimir remisión"*.
7. Cierra la app desde el selector de apps **a media visita**, con el carrito armado, y vuelve a
   entrar: el pedido tiene que estar como lo dejaste, con los mismos precios.
8. El rango de folios de la demo es de **30**, así que a la segunda venta ya debe salir el aviso de
   *"te quedan pocos folios"* sin tener que emitir cientos de tickets.
9. Toca **"Imprimir remisión"** y luego **"Ver el ticket"**: se abre el ticket como saldría del papel,
   en monoespaciado, con el ancho de 58 mm marcado. Una línea que se desborde lleva `>` en vez de `|`.
10. En el alta de cliente, el **lienzo espacial**: tu punto al centro y los clientes conocidos
    alrededor. Toca las flechas cardinales y fíjate cómo **se mueven los vecinos** — eso confirma que
    el ajuste va para el lado correcto.

### Revisar el ticket sin impresora

El ticket también se versiona como texto, así que se puede leer sin correr nada:

```bash
cat contracts/ticket_58mm_ejemplo.txt      # contado, crédito y reimpresión
make movil-ticket                          # regenerarlo tras un cambio de diseño
```

Y el teléfono deja una copia de cada ticket impreso:

```bash
adb exec-out run-as com.distribuidora.dsd_app cat files/tickets/ultimo.txt
```

Puedes pulsar el botón varias veces: la siembra es idempotente, no duplica nada **ni infla la carga del
camión**.

### Por qué esto no puede llegar al teléfono de un vendedor

El atajo tiene **dos cerrojos, y los dos son de compilación** (`mobile/app/lib/src/demo.dart`):

1. `bool.fromEnvironment('DSD_DEMO')` — hay que pedirlo al compilar.
2. `!kReleaseMode` — aunque alguien pase el define en un build de release, se ignora.

Las dos son constantes, así que el compilador de Dart **elimina el botón del árbol**: en un build de
release no está oculto, no existe en el binario. El CI lo vigila por los dos lados: la corrida normal
comprueba que el atajo esté apagado, y una prueba que corre en ambos modos comprueba que la
constante siga derivándose del define —si alguien la fijara a `true` para no teclear el flag, se
pone rojo—.

Además el login de demo **no salta la verificación**: guarda la credencial y llama al mismo login
offline con el PIN, recorriendo Argon2id igual que en producción. El hash es uno de los vectores
compartidos de `contracts/argon2_vectors.json`.

```bash
make movil-demo   # las pruebas de widget del camino demo
```

---

## 5. Para el servidor de la oficina (Fase 3, al salir el piloto)

- Mini PC (Intel N100 o similar, 16 GB RAM, SSD NVMe) con Ubuntu Server LTS
- **UPS / no-break** — no es opcional: un apagón con rutas sincronizando corrompe la base
- Cuenta gratuita de Cloudflare para el túnel
- Cuenta de Backblaze B2 o S3 para los respaldos

Ahí el despliegue es `cp .env.example .env`, rellenar, y `docker compose up -d`.
