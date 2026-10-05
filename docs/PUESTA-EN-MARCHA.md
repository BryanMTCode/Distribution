# Puesta en marcha: del servidor desplegado al vendedor trabajando

El servidor ya está en internet ([INSTALACION-PASO-A-PASO.md](INSTALACION-PASO-A-PASO.md)
terminado, `/salud` con las cuatro banderas en `true`). Esto es lo que falta para
que **la app esté lista para trabajar**.

Misma forma que el manual de instalación: secuencia lineal, **DÓNDE** estás en cada
paso, comandos completos, **✅ qué debes ver** y **⚠️ qué hacer si no.**

**El documento tiene dos mitades que se usan distinto:**

| | Qué es | Cuándo |
|---|---|---|
| **Parte 0** | El ciclo para los errores que encuentres: capturarlos, aplicar el arreglo y verificarlo | **Cada vez que algo falle**, en cualquier momento de lo demás |
| **Partes 1 a 6** | El camino: respaldos, la llave, el APK, el teléfono, el simulacro y el piloto | Una vez, en orden |

La Parte 0 va primera porque la vas a necesitar mientras haces las otras. No es un
paso que se tacha: es el procedimiento al que vuelves.

Hay dos sitios de trabajo y conviene tenerlos claros desde ahora:

| | Dónde | Para qué |
|---|---|---|
| **El servidor** | `ssh dsd@138.197.234.56`, en `~/Distribution` | Respaldos, revisiones, el panel |
| **Tu PC (WSL)** | `~/Distribution` | **Todo lo del APK.** El servidor no compila Android |

> **No queda ningún marcador: todos los comandos se copian y se pegan tal cual.**
> La IP del servidor es `138.197.234.56` y los nombres son
> `api.distribucionesse.com` y `analitica.distribucionesse.com`, los dos verificados
> contra el DNS.
>
> **La IP correcta empieza con `138`, no con `38`.** Lo anoto porque es un dígito
> fácil de perder al copiarla, y el síntoma engaña: `ssh dsd@38.197.234.56` da
> `Connection timed out`, que es exactamente lo que da un cortafuegos mal
> configurado. Te mandaría a revisar reglas durante media hora por un carácter.
>
> **Y para hablar con el servidor se usa el NOMBRE, no la IP**, en todo lo que vaya
> por `https://`: el certificado se valida contra el nombre, así que
> `https://138.197.234.56/salud` falla por certificado aunque el servidor conteste
> perfectamente. La IP es solo para `ssh` y `scp`.

> **Un paso es irreversible y está marcado 🔴: el keystore (paso 6).** Si se pierde
> esa llave, los teléfonos que ya tengan la app no se pueden actualizar nunca más,
> y desinstalar borra la base local del vendedor con las ventas que no hubiera
> subido. Es dinero de la calle que desaparece de las cifras. No lo pospongas.

Antes de empezar, en **los dos** sitios:

```bash
cd ~/Distribution && git pull origin main
```

---

# Parte 0 · El ciclo de los errores que vas encontrando

**Esta parte no se hace una vez: se usa cada vez que algo falla mientras haces el
resto.** Lo de abajo es la Parte 1 y siguientes, que es el camino. Esto es qué
hacer cuando el camino se rompe — y se va a romper, porque el sistema se está
estrenando contra la realidad por primera vez.

Ya pasó una vez y vale como ejemplo: **«Internal Server Error» al dar de alta un
producto.** No era el producto. Era que en producción la API se conecta con un rol
restringido y los disparadores que publican los cambios al teléfono no podían
escribir. Lo mismo habría impedido confirmar una carga y sincronizar una venta a
crédito. Un síntoma chico, una causa grande.

De ahí la regla de esta parte: **un error no se rodea, se reporta y se arregla.**

---

## Paso 0.1 · Captura el error de forma que sirva

**DÓNDE:** en **el servidor**, en `~/Distribution`, en cuanto veas la falla.

«Internal Server Error» en el navegador no dice nada: el detalle está en la
bitácora de la API. Tres comandos, en este orden:

```bash
docker compose logs --tail=40 api
docker compose ps
curl -s https://api.distribucionesse.com/salud
```

Lo que importa de cada uno:

| Comando | Qué buscas |
|---|---|
| `logs api --tail 40` | El *traceback*. La última línea nombra la causa; las de `psycopg` o `sqlalchemy` nombran la tabla |
| `ps` | Que `api` siga `Up (healthy)` y no reiniciándose en bucle |
| `/salud` | Las cuatro banderas. **`"rls": false` cambia el diagnóstico de todo** |

> **⚠️ Pégame la salida dentro de un bloque de código**, con tres acentos graves
> antes y después. No es estética: la salida de Gradle y de Docker trae líneas que
> empiezan con `>`, y si las pegas sueltas en tu terminal, bash las lee como
> redirección y te crea archivos con nombres raros. Ya te pasó: así aparecieron
> `mobile/app/Error:` y `mobile/app/Failed`.

Y dime estas tres cosas, que valen más que el traceback:

1. **Qué estabas haciendo**, con la pantalla exacta (*Panel → Productos → Nuevo*).
2. **Qué esperabas** y qué pasó.
3. **Si funciona en tu PC.** Si allí sí y en el servidor no, eso ya acota la causa
   a lo que difiere: el rol restringido, RLS, la zona horaria o el `.env`.

---

## Paso 0.2 · Respalda antes de aplicar cualquier arreglo

**DÓNDE:** en **el servidor**.

```bash
bash scripts/en_el_servidor.sh respaldar.sh
```

**✅ Debes ver** `OK  dsd-AAAAMMDD-HHMMSS.dump`.

No te lo brinques ni cuando el arreglo «es sólo un cambio de código». Cuesta
segundos y es la diferencia entre volver atrás y no poder.

---

## Paso 0.3 · Trae el arreglo y aplícalo según lo que cambió

**DÓNDE:** en **el servidor**.

```bash
git pull origin main
```

**Y ahora lo que importa: no todos los arreglos se aplican igual.** Mira qué
tocó el commit y usa la fila que corresponda:

```bash
git log --oneline -1
git show --stat HEAD
```

| Si el arreglo tocó | Comando | Por qué |
|---|---|---|
| **Una migración** (`server/db/migrations/`, `server/db/alembic/`) | `docker compose build migraciones api worker && docker compose up -d` | El archivo de migración vive **dentro de la imagen**: sin `build`, `alembic` no lo ve y no hace nada |
| **Código del servidor** (`server/app/`, plantillas HTML) | `docker compose build api worker && docker compose up -d` | Igual: el código va en la imagen |
| **El laboratorio** (`analytics/`) | `docker compose build analitica && docker compose up -d` | — |
| **Scripts o documentos** (`scripts/`, `docs/`) | nada más, el `git pull` basta | Se leen del disco, no de la imagen |
| **La app** (`mobile/`) | **en tu PC**: `make apk DSD_BASE_URL=https://api.distribucionesse.com` y reinstalar | El servidor no compila Android. Sube el `versionCode` antes (paso 9) |

> **Si tienes duda, `docker compose build && docker compose up -d` reconstruye
> todo.** Tarda más, nunca se queda corto, y la caché de Docker hace que lo que no
> cambió sea rápido.

---

## Paso 0.4 · Si el arreglo traía una migración, compruébala

**DÓNDE:** en **el servidor**. Sáltate este paso si no tocó migraciones.

```bash
docker compose logs --tail=10 migraciones
docker compose ps
```

**✅ Debes ver** que `alembic` llegó a la revisión nueva, y en `ps`:

- `migraciones` en **`Exited (0)`** ← cero es correcto: hizo su trabajo y terminó.
- `api` en **`Up (healthy)`**.

**⚠️ Si `migraciones` salió con un código distinto de 0**, la migración falló y la
base quedó como estaba. Pégame `docker compose logs migraciones`.

**⚠️ Si `api` se reinicia en bucle** después de una migración, mira
`docker compose logs --tail=30 api` antes de tocar nada.

---

## Paso 0.5 · 🚦 PUERTA: verifica el arreglo donde falló

**DÓNDE:** donde apareció el error, no en otro lado.

```bash
curl -s https://api.distribucionesse.com/salud
```

**✅ Las cuatro banderas en `true`.** Y después, **repite la acción exacta que
fallaba** — si era dar de alta un producto, da de alta un producto. Que el
servicio esté sano no prueba que el camino roto esté arreglado.

**⚠️ Si sigue fallando**, vuelve al paso 0.1 y captura otra vez: el traceback ya
va a nombrar algo distinto, y eso es progreso, no un retroceso.

---

## Paso 0.6 · Anótalo

Una línea por error, en el registro de abajo. No es burocracia: cuando el
vendedor esté en la calle y algo se repita, la pregunta va a ser «¿esto ya nos
pasó?», y esta tabla es la única que la contesta.

### Registro de errores encontrados

| # | Fecha | Qué veías | Causa real | Arreglo | Verificado |
|---|---|---|---|---|---|
| 1 | 2026-10-05 | «Internal Server Error» al dar de alta un producto en el panel | `change_log` tiene RLS sin política de INSERT, y los disparadores que la alimentan corrían como `dsd_api` en vez de como su dueño. También impedía confirmar cargas y sincronizar ventas a crédito | Migración **0029**: las cuatro `fn_registrar_cambio*` pasan a `SECURITY DEFINER` con `search_path` fijado | ☐ |
| 2 | 2026-10-05 | El celular se quedaba en pantalla negra al segundo arranque, y el login rebotaba al inicio | El esquema local no era idempotente (se aplica en cada arranque) y la sesión offline no fijaba el token | `IF NOT EXISTS` en las 32 tablas locales; `entrarOffline` fija el token; pantalla de arranque roto en vez de negra | ☐ |
| 3 | 2026-10-05 | «1 con error» al sincronizar ventas: no llegaban al tablero | El `lote_id` del empujón no era un UUID y el servidor contestaba 422 antes de tocar el dominio (sin rastro en cuarentena) | `lote_id` derivado del contenido con SHA-256; el servidor ahora registra los 422 con ruta y campo | ☐ |
| 4 | 2026-10-05 | «Se queda cargando y no avanza la venta» al vender lo último que quedaba de un producto | Dos fallas juntas: la guarda de existencia comparaba el REAL crudo contra la cantidad —con deriva, la última pieza se negaba para siempre— y cualquier falla que no fuera de negocio dejaba el botón girando sin estado terminal | La guarda compara en milésimas con la misma regla de redondeo que la pantalla; el cierre ahora solo lanza `VentaRechazada` o `CierreRoto`, y la pantalla avisa en diálogo si la venta quedó o no | ☐ |
| 5 | | | | | |

---

## Si un arreglo empeora las cosas

Dos salidas, de la más barata a la más cara:

**1. Volver al commit anterior** (sirve si fue código, no una migración):

```bash
cd ~/Distribution
git log --oneline -5          # elige el de antes
git checkout <hash-anterior>
docker compose build api worker && docker compose up -d
```

Y dímelo, para arreglarlo bien y que vuelvas a `main`.

**2. El snapshot del proveedor**, si la base quedó mal. En DigitalOcean, el
droplet → **Snapshots** → restaurar `dsd-desplegado-limpio`. Pierdes lo capturado
desde entonces, así que antes de restaurar baja el último respaldo:

```bash
scp dsd@138.197.234.56:~/respaldos-dsd/dsd-*.dump ~/
```

> **Una migración no se deshace con `git checkout`.** El código vuelve atrás, la
> base no. Si hay que revertir una migración, dímelo: `alembic downgrade` existe,
> pero cada migración decide qué puede deshacer sin perder datos y eso se mira
> caso por caso.

---

## Lo que NO es un error del sistema

Para que no gastes un reporte en esto:

| Lo que ves | Qué es |
|---|---|
| `migraciones` y `roles` en `Exited (0)` | **Correcto.** Son tareas que terminan, no servicios |
| El primer `docker compose up` tarda 10–25 min | Normal: construye dos imágenes |
| `make respaldo` da `connection refused` en el servidor | Usa `bash scripts/en_el_servidor.sh respaldar.sh`: ahí la base no publica puerto |
| No existe `mobile/app/android/gradlew` tras clonar | Normal: Flutter lo regenera |
| El panel pide entrar otra vez tras un `up -d` | La sesión vive en una cookie firmada; reiniciar la API no la invalida, pero el navegador puede haber caducado |

---

# Parte 1 · Los respaldos, antes que nada

Hoy el sistema está operando **sin respaldos**. Esto va primero porque es lo único
de esta lista que protege de un error que ya puedas haber cometido.

## Paso 1 · El primer respaldo

**DÓNDE:** en **el servidor**, en `~/Distribution`.

```bash
bash scripts/en_el_servidor.sh respaldar.sh
```

**✅ Debes ver:**

```
Respaldando a /respaldos/dsd-AAAAMMDD-HHMMSS.dump
OK  dsd-AAAAMMDD-HHMMSS.dump (xxK)

Quedan 1 respaldo(s) en /respaldos.
```

**⚠️ Si dice `connection refused`:** estás corriendo `make respaldo` en lugar de
este comando. En el servidor PostgreSQL no publica puerto —el compose solo saca 80
y 443, a propósito— así que no hay nada en `127.0.0.1:5432`. Este envoltorio entra
por la red de Docker.

**⚠️ Si dice `No hay .env`:** no estás en `~/Distribution`, o estás en tu PC y no
en el servidor.

## Paso 2 · Comprueba que el archivo sirve

**DÓNDE:** en **el servidor**.

```bash
ls -lh ~/respaldos-dsd/
cd ~/respaldos-dsd && sha256sum -c *.sha256
cd ~/Distribution
```

**✅ Debes ver** el `.dump` con un tamaño razonable, su `.sha256` al lado, y la
verificación diciendo `OK`.

**⚠️ Si el `.dump` pesa unos pocos bytes**, el dump falló aunque dijera OK. Mira
qué pasó: `bash scripts/en_el_servidor.sh respaldar.sh` otra vez, leyendo el error.

## Paso 3 · 🚦 PUERTA: restaura el respaldo de verdad

**DÓNDE:** en **el servidor**.

```bash
bash scripts/en_el_servidor.sh simulacro.sh
```

Restaura el último respaldo en una base desechable, le cuenta los renglones y
corre unas comprobaciones sobre los datos.

**✅ Debes ver** las líneas de comprobación en verde y un veredicto final de que el
respaldo es restaurable.

**⚠️ Si falla, lee QUÉ falló: los tres casos piden cosas opuestas.** El script
ahora los distingue y te imprime el error de PostgreSQL:

| Dice | Significa | Qué hacer |
|---|---|---|
| `la semilla de la prueba chocó con un dato que ya existe` | El respaldo **está bien**; un código de la prueba coincide con uno real | Actualiza el repo (`git pull`) y repite. Si persiste, dímelo |
| `una invariante del diseño NO se cumple` | El esquema está completo pero **una regla no se aplica**. El caso más grave | No sigas. Pégame la línea `FALLA` |
| `el esquema restaurado está incompleto` | El dump no trajo algo: un disparador, un índice | No sigas. Pégame el `ERROR:` |

Para investigar sobre la base del simulacro —que normalmente se borra al
terminar— consérvala:

```bash
DSD_SIMULACRO_CONSERVAR=1 bash scripts/en_el_servidor.sh simulacro.sh
```

`RESPALDOS.md` §2 explica qué busca cada comprobación.

> **Por qué es una puerta:** un respaldo que nunca restauraste no es un respaldo,
> es un archivo. Y un snapshot del proveedor tampoco lo es: un `DELETE` sin `WHERE`
> a las once de la noche se replica al snapshot de esa noche, y no hay nada ahí que
> verifique que la base se puede levantar.

## Paso 4 · El cron

**DÓNDE:** en **el servidor**.

```bash
crontab -e
```

Pega estas dos líneas al final (si tu usuario no es `dsd`, cambia las rutas):

```cron
0 3 * * * cd /home/dsd/Distribution && bash scripts/en_el_servidor.sh respaldar.sh >> /home/dsd/respaldos-dsd/cron.log 2>&1
0 4 * * 0 cd /home/dsd/Distribution && bash scripts/en_el_servidor.sh simulacro.sh >> /home/dsd/respaldos-dsd/simulacro.log 2>&1
```

Guarda y comprueba que quedó:

```bash
crontab -l
```

**✅ Debes ver** las dos líneas.

> **Rutas absolutas, nunca `~`.** El cron corre con un entorno mínimo: `~` puede no
> expandir a lo que esperas y el `PATH` no es el de tu sesión.

**📅 Y mañana, sin falta:**

```bash
cat ~/respaldos-dsd/cron.log
ls -lt ~/respaldos-dsd/ | head
```

Debes ver un respaldo nuevo con la fecha de hoy. **Un cron que falla en silencio es
peor que no tener cron**, porque crees que estás respaldado y no lo estás.

## Paso 5 · Saca una copia fuera del proveedor

**DÓNDE:** en **tu PC (WSL)**.

```bash
scp dsd@138.197.234.56:~/respaldos-dsd/dsd-*.dump ~/respaldos-dsd-copia/
```

(Crea la carpeta antes con `mkdir -p ~/respaldos-dsd-copia`.)

**✅ Debes ver** el archivo copiado en tu máquina.

> **Por qué fuera del proveedor y no solo fuera del servidor:** el riesgo nuevo que
> trae un VPS no es que se queme el edificio, es que **pierdas la cuenta** —un cargo
> rechazado, una cuenta comprometida— y con ella el servidor y los respaldos al
> mismo tiempo. Un respaldo en el object storage del mismo proveedor no cuenta.
> El procedimiento con cifrado GPG está en `RESPALDOS.md` §3.

---

# Parte 2 · 🔴 La llave que no se puede perder

**DÓNDE:** todo esto en **tu PC (WSL)**. El keystore no vive en el servidor.

## Paso 6 · Crea el keystore

```bash
mkdir -p ~/llaves
keytool -genkeypair -v \
  -keystore ~/llaves/dsd-release.jks \
  -storetype PKCS12 \
  -keyalg RSA -keysize 4096 -validity 10000 \
  -alias dsd
```

Te pide:

| Pregunta | Qué poner |
|---|---|
| `Enter keystore password` | Una contraseña larga. **Apúntala en papel ahora.** |
| `Re-enter new password` | La misma |
| `What is your first and last name?` | El nombre de la distribuidora |
| `organizational unit` / `organization` | `DSD` / el nombre de la empresa |
| Ciudad, estado, país | Los tuyos (`MX` para el país) |
| `Is CN=… correct?` | `yes` |

**✅ Debes ver** `[Storing /home/TU_USUARIO/llaves/dsd-release.jks]` y el archivo
existiendo:

```bash
ls -l ~/llaves/dsd-release.jks
```

> **`-validity 10000` son unos 27 años, y no es exageración.** Un certificado
> vencido deja de poder firmar actualizaciones, y entonces el problema es
> exactamente el mismo que haberlo perdido.

## Paso 7 · 🔴 PUERTA: respáldalo ANTES de firmar nada

**No sigas al paso 8 sin esto hecho.** Es el paso que todo el mundo posterga y es
el único de este documento que se cobra en dinero.

Tres cosas, y las tres hacen falta —el archivo sin la contraseña no sirve:

1. **El archivo** `~/llaves/dsd-release.jks` copiado a **dos lugares que no sean
   esta máquina**. Por ejemplo una memoria USB guardada fuera de la oficina y un
   almacenamiento cifrado en la nube.
2. **La contraseña, en papel**, guardada aparte del archivo.
3. **El alias**, que es `dsd`. Sin él la llave no se puede usar aunque tengas el
   archivo y la contraseña.

```bash
# Por ejemplo, a una USB montada en Windows como D:
mkdir -p /mnt/d/llaves-dsd
cp ~/llaves/dsd-release.jks /mnt/d/llaves-dsd/
ls -l /mnt/d/llaves-dsd/
```

**✅ Debes ver** el archivo en los dos destinos, y la contraseña escrita en papel.

> **Qué pasa exactamente si se pierde.** Android solo acepta actualizar una app
> instalada si el APK nuevo viene firmado con **la misma** llave. Si no coincide, la
> instalación se rechaza con «App not installed» y el único camino es desinstalar. Y
> desinstalar, en esta app, **borra la base local del vendedor** con las ventas, los
> cobros y las mermas que todavía no hubiera subido: dinero que ocurrió en la calle
> y que ya no está en ninguna cifra del sistema.
>
> El keystore está en `.gitignore` junto con `*.jks` y `*.keystore`. **No se
> versiona**, y un repositorio es exactamente el lugar donde no debe estar, porque
> se clona.

## Paso 8 · El `key.properties`

Gradle no te va a preguntar la contraseña en cada build: la lee de un archivo que
tampoco se versiona.

```bash
cd ~/Distribution
cp mobile/app/android/key.properties.example mobile/app/android/key.properties
nano mobile/app/android/key.properties
```

Rellena los cuatro campos (pon **tu** usuario en la ruta, no `TU_USUARIO`):

```properties
storeFile=/home/bh_me/llaves/dsd-release.jks
storePassword=la-contraseña-del-paso-6
keyAlias=dsd
keyPassword=la-misma-contraseña-del-paso-6
```

> `storePassword` y `keyPassword` son la misma si en el paso 6 diste Enter cuando
> `keytool` ofreció reutilizar la contraseña del almacén para la clave.

**✅ Comprueba** que el archivo no quedó con marcadores sin rellenar:

```bash
grep -c '^\(storeFile\|storePassword\|keyAlias\|keyPassword\)=.\+' mobile/app/android/key.properties
```

**✅ Debes ver** `4`.

**⚠️ Si ves menos de 4**, falta rellenar alguno. El build se detendría diciendo
cuáles, pero mejor verlo aquí.

---

# Parte 3 · El APK de producción

**DÓNDE:** en **tu PC (WSL)**.

## Paso 9 · Compílalo

```bash
cd ~/Distribution
make apk DSD_BASE_URL=https://api.distribucionesse.com
```

Cambia `api.distribucionesse.com` por tu dominio real. Tarda varios minutos.

**✅ Debes ver, al terminar:**

```
  OK    versión 0.1.0+1  (versionCode 1)
  OK    servidor https://api.distribucionesse.com
  OK    firmado con una llave de producción
        CN=Distribuidora, OU=DSD, …
        SHA-256 del certificado: 4f1c9a77e3b8…
```

**⚠️ Se niega a construir en tres casos, y los tres son a propósito:**

| Si se queja de | Por qué |
|---|---|
| `DSD_BASE_URL` falta | El APK apuntaría a `api.localhost`: se instalaría bien y el login fallaría con un error de red. El vendedor reportaría «no hay internet» y la mañana se iría revisando el router |
| no empieza con `https://` | Android prohíbe el tráfico sin TLS en release. Un APK de producción con `http://` compila bien y no puede conectarse a nada |
| `key.properties` falta | Firmar con la llave de depuración funciona hoy y rompe la actualización mañana. Paso 8 |

## Paso 10 · Apunta la huella SHA-256

**En papel, junto a la contraseña del keystore.**

```
SHA-256 del certificado: ______________________________________
```

**Tiene que ser la misma en todos los APK que reparta la empresa, para siempre.**
Si un día sale distinta, se firmó con otra llave y ese APK no va a poder actualizar
los teléfonos que ya están en la calle.

**⚠️ Si en lugar de la huella viste `no encontré apksigner`**, el APK está bien
—solo no se pudo revisar la firma—. `apksigner` viene en las build-tools del SDK de
Android y no siempre está en el `PATH`. Dos salidas:

```bash
# A. Decirle dónde está el SDK y volver a revisar, sin recompilar:
export ANDROID_HOME=$HOME/Android/Sdk
bash scripts/revisar_apk.sh https://api.distribucionesse.com

# B. O sacar la huella del keystore directamente, que es el mismo certificado:
keytool -list -v -keystore ~/llaves/dsd-release.jks -alias dsd | grep -i SHA256
```

## Paso 11 · Instálalo en el teléfono

El APK quedó en:

```
mobile/app/build/app/outputs/flutter-apk/app-release.apk
```

**Camino A — por Drive (el que usamos).** El teléfono del vendedor no va a estar
enchufado a tu PC, así que éste es el camino real. En WSL:

```bash
wslpath -w ~/Distribution/mobile/app/build/app/outputs/flutter-apk/app-release.apk
```

`make apk` ya imprime esa ruta al terminar, así que normalmente no hace falta
correrlo aparte. Pega la ruta en el explorador de Windows, sube el `.apk` a Drive,
y ábrelo desde el teléfono.

En el teléfono, la primera vez, Android pide permiso para **instalar de orígenes
desconocidos**: se concede a la app desde la que abres el archivo (Drive o el
gestor de archivos), no a la app que instalas.

> 🔴 **Instálalo ENCIMA del que ya está. NO DESINSTALES.** Desinstalar borra la
> base local del vendedor, con las ventas, los cobros y las mermas que todavía no
> hubiera subido. Si Android dice «App not installed», **no desinstales para
> salir del paso**: es un problema de firma o de `versionCode`, y desinstalar
> cambia un problema de diez minutos por dinero perdido. Dímelo y lo vemos.

**Camino B — por cable, con `adb`**, si el teléfono está enchufado. Desde
**PowerShell como administrador**:

```powershell
usbipd list
usbipd attach --wsl --busid <BUSID>
```

Y en **WSL**:

```bash
adb devices
adb install -r mobile/app/build/app/outputs/flutter-apk/app-release.apk
```

**✅ Debes ver** `Success`.

**✅ En los dos casos debes ver** el ícono de la app en el teléfono, y al abrirla
la pantalla de entrada — no una pantalla negra.

> **La prueba que de verdad importa, y que solo se puede hacer una vez que hay algo
> instalado:** instalar un APK nuevo **encima** de uno ya instalado. La firma y el
> `versionCode` solo se ejercitan ahí. Un APK que se instala limpio en un teléfono
> vacío no prueba nada sobre la actualización, que es donde está el riesgo. Hazlo
> antes del piloto, con el `versionCode` subido (el número después del `+` en
> `mobile/app/pubspec.yaml`).

---

# Parte 4 · Que el teléfono entre

Esta es la prueba mínima de que la app sirve. Es más corta que el simulacro
completo y si falla, falla aquí.

## Paso 12 · Da de alta al vendedor

**DÓNDE:** en el **navegador**, en `https://api.distribucionesse.com/panel`.

**Panel → Usuarios y rutas.** Crea el vendedor con su ruta y su camión. Anota su
**código de empleado** (por ejemplo `VEND01`) y la contraseña que le pongas.

**✅ Debes ver** el vendedor en la lista, activo, con ruta y almacén de camión
asignados.

**⚠️ Un vendedor NO se crea con `crear-usuario`** en el servidor: ese comando
rechaza el rol `vendedor` a propósito, porque un vendedor no entra al panel.

## Paso 13 · Vincula el teléfono

**DÓNDE:** en el **navegador**, **Panel → Teléfonos**.

En «Vincular un teléfono»: elige al vendedor en la lista, ponle una etiqueta que te
sirva para reconocerlo (`Moto G - Juan`), y guarda.

**✅ Debes ver** un aviso con el identificador del equipo, parecido a:

```
Equipo «Moto G - Juan» vinculado a Juan Pérez.
Tecléalo en el teléfono una sola vez: 019283ab-...
```

**Cópialo o anótalo.** Lo vas a teclear en el teléfono en el paso siguiente.

**⚠️ Si el vendedor no aparece en la lista**, es porque ya tiene otro equipo activo
(solo se permite uno) o porque su rol no es `vendedor`. Revisa el paso 12.

> **Por qué el alta la hace la oficina y no el teléfono:** un vendedor que pudiera
> registrar su propio equipo podría mover su sesión a un teléfono que la empresa no
> controla, y el borrado remoto dejaría de alcanzarlo.

## Paso 14 · 🚦 PUERTA: entra desde el teléfono

**DÓNDE:** en el **teléfono**, con la app abierta y **con WiFi o datos**.

1. En la pantalla de entrada, abre **«Vincular este equipo»**.
2. Teclea el **identificador del paso 13**, el **código del vendedor** y su
   **contraseña**.
3. Pulsa **«Vincular y entrar»**.

**✅ Debes ver** que la app entra y queda lista para trabajar, con los datos de la
ruta descargados.

**⚠️ Si dice que el dispositivo no está registrado:** el identificador está mal
teclado. Son 36 caracteres con guiones; cópialo, no lo transcribas.

**⚠️ Si dice que no hay conexión:** abre el navegador **del teléfono** en
`https://api.distribucionesse.com/salud`. Si eso no responde, el problema es la red o el
DNS, no la app.

**⚠️ Si la app muestra una pantalla que dice que se compiló sin servidor**, el APK
se construyó con `flutter build apk --release` a secas en lugar de con `make apk`.
Vuelve al paso 9.

> **Esto es lo que no funcionaba hasta hace unos días**, y el modo demo lo tapaba: el
> servidor solo entrega la credencial local si el login trae un dispositivo
> registrado, activo y de ese vendedor. Sin el paso 13 no hay login posible en un
> APK de producción, y el síntoma parece un problema de red.

---

# Parte 5 · El simulacro completo

## Paso 15 · 🚦 PUERTA: un día entero de operación

**DÓNDE:** [SIMULACRO.md](SIMULACRO.md), de principio a fin, contra **este
servidor**.

Siete puntos de control con las cifras exactas que deben salir. El que decide es el
**6**: retorno esperado, efectivo esperado **1 945.40**, y las **dos diferencias en
cero**.

Dos cosas que cambian ahora que el servidor está en internet y simplifican el
guion:

- **Sáltate todo el apartado de red de WSL.** Ya no aplica: el teléfono habla con
  `https://api.distribucionesse.com` desde cualquier red, incluso con datos móviles.
- **Usa el APK de producción del paso 9**, no un build de depuración. Estás probando
  el artefacto de verdad.

**✅ Debes terminar** con las dos diferencias de la liquidación en cero y el camión
en cero.

**⚠️ Si un punto de control no cuadra, para ahí.** Apunta el número del punto y la
cifra que te salió: eso ubica la pieza sin adivinar.

> **Por qué es la última puerta:** si el inventario y el dinero no cuadran al
> centavo con un día inventado y controlado, no van a cuadrar con un día real.

---

# Parte 6 · El día −1 del piloto

## Paso 16 · La revisión automática

**DÓNDE:** en **el servidor**, en `~/Distribution`.

```bash
bash scripts/en_el_servidor.sh piloto_listo.sh VEND01
```

Revisa los nueve primeros puntos de la lista del día −1 y **dice qué falta y cómo
se arregla**.

**✅ Debes ver** los nueve en verde.

> La lista completa de `PILOTO.md` §1 está escrita para la máquina de desarrollo.
> En el servidor la traducción es ésta:
>
> | # | En PILOTO.md dice | En el servidor |
> |---|---|---|
> | 1 | `make simulacro` | Paso 3 de este documento |
> | 2 | `make doctor` (zona) | `timedatectl` — ya quedó al instalar |
> | 3 | `make migrar` | El servicio `migraciones` ya corrió |
> | 4 | `/salud` con `"rls": true` | `curl -s https://api.distribucionesse.com/salud` |
> | 5–10 | el panel | el panel, igual |

## Paso 17 · Los dos puntos que ningún comando puede revisar

**11 · El entrenamiento del vendedor.** Media jornada de práctica **haciendo**, no
mirando: diez ventas de prueba a clientes reales de su ruta, un cobro, un no-drop,
una merma, una cancelación y la liquidación al final. **Con la base de pruebas, no
con producción.**

Lo que de verdad hay que entrenar no es la app, son las dos reglas que chocan con la
costumbre:

- **El precio no se negocia en la calle.** No hay campo para cambiarlo, y no es un
  descuido: es la regla sellada del sistema.
- **Lo que no se captura no existe.** La app no es la libreta de la noche.

**13 · El APK firmado con la llave de producción.** Ya lo tienes del paso 9, y la
huella SHA-256 apuntada del paso 10.

## Paso 18 · Define el piloto en el panel

**DÓNDE:** en el **navegador**, **Panel → Piloto**.

Y de ahí en adelante, la rutina diaria de `PILOTO.md` §2: quince minutos en la
mañana capturando el cuadre de ayer con la hoja del vendedor en la mano, y cinco en
la tarde cerrando la liquidación.

La pregunta de cada mañana es **«¿qué te estorbó ayer?»**, no «¿todo bien?» — que se
contesta «bien» siempre y no sirve para nada.

---

# Resumen: la lista para tachar

**La Parte 0 no se tacha**: se usa cada vez que algo falla, y su registro de
errores se va llenando. Lo que se tacha es el camino:

| | Paso | Dónde |
|---|---|---|
| ☐ | 1–2 · Primer respaldo y verificado | Servidor |
| ☐ | 3 · 🚦 Simulacro de restauración | Servidor |
| ☐ | 4 · Cron, y revisarlo mañana | Servidor |
| ☐ | 5 · Copia fuera del proveedor | Tu PC |
| ☐ | 6 · 🔴 Crear el keystore | Tu PC |
| ☐ | 7 · 🔴 Respaldarlo en dos sitios + contraseña en papel | Tu PC |
| ☐ | 8 · `key.properties` | Tu PC |
| ☐ | 9 · `make apk` | Tu PC |
| ☐ | 10 · Apuntar la huella SHA-256 | Papel |
| ☐ | 11 · Instalar en el teléfono | Tu PC / teléfono |
| ☐ | 12 · Alta del vendedor | Panel |
| ☐ | 13 · Vincular el teléfono | Panel |
| ☐ | 14 · 🚦 El teléfono entra | Teléfono |
| ☐ | 15 · 🚦 Simulacro completo | SIMULACRO.md |
| ☐ | 16–18 · Día −1 del piloto | Servidor y panel |

---

# Una decisión que conviene tomar esta semana

Con el servidor en la nube, **la oficina ya no puede operar sin internet.** Hoy no
se nota; el momento en que duele es específico: **a las 6 de la mañana, cuando hay
que capturar y confirmar la carga para que el camión salga.**

Se mitiga de dos formas y conviene tener las dos:

1. **Failover 4G en el router de la oficina.**
2. **Capturar la carga la noche anterior**, que además es mejor práctica: a las 6 am
   nadie quiere estar capturando quince renglones.

Está en `DESPLIEGUE.md` §0, y es lo único de la mudanza al VPS que empeoró respecto
de tener el servidor en la oficina.
