# Seguridad operativa: teléfonos, accesos y qué hacer cuando algo se pierde

Este documento es para decidir, no para programar. Lo que describe está
construido; lo que aquí se añade es **cuándo usar cada cosa** y en qué orden.

---

## 1. El modelo de amenaza, dicho en voz alta

Vale la pena escribirlo porque determina todo lo demás, y porque la respuesta
intuitiva («el robo») no es el caso frecuente.

| Amenaza | Probabilidad | Qué la contiene |
|---|---|---|
| **El vendedor deja la empresa con el teléfono** | Alta | Borrado remoto, y entrega primero |
| **El teléfono se cambia por otro** | Alta | Revocar el viejo; registrar el nuevo |
| **El teléfono se extravía y aparece** | Media | Suspender; reactivar cuando aparece |
| **El teléfono se roba** | Baja | SQLCipher + PIN + Argon2id; después, borrado |
| **Alguien copia la base del teléfono** | Baja | La base está cifrada y la llave vive en el Keystore |
| **Un error en una consulta nueva filtra otra ruta** | **Media** | RLS (migración 0022) |
| **Alguien se lleva un respaldo** | Baja | Cifrar antes de sacarlo (RESPALDOS.md §3) |
| **Se llevan la mini PC de la oficina** | Baja | Disco cifrado con LUKS (§5.1). Dentro va TODO: la base, el `.env` y la copia local de los respaldos |

Tres conclusiones incómodas y correctas:

**El robo no es el caso que manda.** Los tres primeros renglones son el día a
día, y en los tres el teléfono trae dentro operación sin sincronizar. Un sistema
diseñado solo para el robo borraría de inmediato y perdería ventas cobradas cada
vez que alguien renuncia.

**El riesgo más probable no es un atacante, es un bug nuestro.** Por eso la
Fase 9 dedica una migración entera a RLS: no protege de quien tiene la
contraseña de la base, protege de una consulta nueva que se olvida de filtrar
por ruta.

**El renglón de la mini PC es el único en el que se pierde todo de golpe.** Un
teléfono robado trae la ruta de un vendedor; el servidor trae la operación
completa, los secretos y la copia local de los respaldos. Que su probabilidad sea
baja no cambia que su costo no tenga techo — y es el renglón que este documento
tuvo sin respuesta hasta la §5.1.

---

## 2. Qué hacer en cada caso

### «El vendedor renunció y se llevó el teléfono»

1. `Panel → Teléfonos` → **Suspender**. Deja de recibir datos nuevos y **puede
   seguir subiendo** lo que trae.
2. Llamarlo y pedirle que abra la app con señal una vez. La app le dirá cuántas
   operaciones le faltan por subir.
3. Cuando la columna «por subir» quede en cero, **Ordenar el borrado**. El
   teléfono se limpia solo en su siguiente apertura con señal y lo confirma.
4. Si no se puede contactar: **Ordenar el borrado igual**. El equipo queda
   bloqueado con sus datos dentro. El día que alguien lo encienda con señal,
   entrega y después se borra. Un equipo bloqueado con datos dentro es
   recuperable; uno borrado, no.

> **Revocar no borra.** Revocar mata los tokens —protege los datos del
> servidor— y deja intacta la copia del teléfono. Es la confusión que más cuesta
> en esta pantalla y por eso está escrita ahí también.

### «Se cambió de teléfono»

1. Registrar el nuevo desde la app (primer login con señal).
2. El servidor **impide** dos equipos activos del mismo usuario (índice único de
   la migración 0001), así que hay que **suspender el viejo primero**. La
   pantalla lo dice con el nombre del equipo que estorba.
3. Con el viejo ya sin cola: **Ordenar el borrado**.

### «El teléfono se extravió»

1. **Suspender** de inmediato. No ordenar el borrado todavía.
2. Si aparece: **Reactivar**.
3. Si no aparece en un par de días: **Ordenar el borrado**.

El orden importa: suspender es reversible y no destruye nada. Ordenar el
borrado, una vez que el teléfono lo ejecuta, no se deshace.

### «Se robaron el teléfono»

1. **Revocar** (no solo suspender): los tokens mueren en la siguiente petición.
2. **Ordenar el borrado**, para cuando el aparato vuelva a tener señal.
3. Y respirar: sin el PIN del vendedor, la base no se abre. Está cifrada con
   SQLCipher y la llave vive en el Keystore de Android, no en el archivo. El PIN
   se verifica con Argon2id de 64 MiB — probar PINes en ese aparato cuesta
   cientos de milisegundos cada uno.

### «A un vendedor se le venció el acceso en la bodega»

No debería pasar, y si pasa es una falla de vigilancia, no del sistema:
`Panel → Teléfonos` ordena por rezago y marca con días de antelación los accesos
por caducar. Mirar esa pantalla una vez al día es la rutina que lo evita.

Cuando ya pasó: el vendedor necesita **una** conexión. Con señal, entra normal y
la credencial se renueva. Sin señal en la bodega, lo más rápido es compartir
datos del teléfono de alguien más por un minuto.

---

## 3. Cómo funciona el borrado remoto, en una frase

> **Nunca se borra lo que no se ha entregado.**

El flujo completo —y la razón de cada paso— está en la migración
`0023_borrado_remoto.sql` y en el ADR §34. En resumen:

```
   ORDENAR  →  DRENAR  →  BORRAR  →  CONFIRMAR
  (oficina)   (teléfono)  (teléfono)  (teléfono)
```

- **Ordenar** deja el equipo en `suspendido`, no en `revocado`. Es la decisión
  que hace que todo funcione: un equipo suspendido todavía puede hacer push.
- **Drenar** es automático: la app sube lo que trae en su siguiente
  sincronización.
- **Borrar** es el teléfono quien lo hace, y solo cuando la cola queda en cero.
  Borra la credencial, la llave de la base y los tres archivos de SQLite.
- **Confirmar** es lo único que prueba que el borrado ocurrió. La orden sola
  solo prueba que alguien la pidió — y entre las dos puede pasar una semana.

Mientras no confirme, la pantalla lo muestra como **«borrado sin confirmar»**.
Si eso lleva días, es una decisión que tomar —ir por el equipo— no un dato que
esperar.

---

## 4. MDM: qué resuelve y qué no

MDM (*Mobile Device Management*) es administrar los teléfonos desde una consola:
forzar el PIN del aparato, cifrar el almacenamiento, instalar la app sin la Play
Store, bloquear el equipo a una sola app, y borrarlo de fábrica.

### Lo que el sistema ya hace sin MDM

| Función | Cómo está resuelta hoy |
|---|---|
| Cifrado de los datos de la app | SQLCipher, con la llave en el Keystore |
| Acceso con credencial | PIN + Argon2id, y caducidad por días sin sincronizar |
| Revocar el acceso | `Panel → Teléfonos` |
| **Borrar los datos de la app** | Borrado remoto (Fase 9) |
| Saber qué equipo está rezagado | `Panel → Teléfonos`, ordenado por rezago |

### Lo que solo MDM puede hacer

| Función | Por qué la app no puede |
|---|---|
| Forzar el PIN **del teléfono** | Es del sistema operativo, no de la app |
| Borrado **de fábrica** | La app solo puede borrar lo suyo |
| Impedir instalar otras apps | Política del dispositivo |
| Modo kiosco (solo esta app) | Política del dispositivo |
| Localizar el aparato | Permiso del sistema, no de la app |
| Desplegar actualizaciones sin Play Store | Canal de distribución |

### La recomendación, con su razón

**Con ocho teléfonos o menos, MDM no vale la pena todavía.** Android Enterprise
es gratuito en su nivel básico, pero exige inscribir cada aparato, mantener la
consola y resolver sus propios problemas. Lo que de verdad protege los datos del
negocio —el cifrado, el acceso y el borrado— ya está construido y probado.

**Lo que sí conviene hacer ahora, y es gratis:**

1. **PIN o patrón obligatorio en el teléfono**, configurado a mano en cada
   aparato. Es la capa que la app no puede dar.
2. **Cifrado del dispositivo activado** (en Android moderno viene por omisión;
   vale comprobarlo).
3. **«Buscar mi dispositivo» activado** con la cuenta de la empresa, no la
   personal del vendedor. Es lo que localiza y bloquea un aparato perdido.
4. **Cuenta de Google de la empresa** en los teléfonos de trabajo. Si el
   vendedor usa la suya, al irse se lleva el control del aparato.
5. **Una hoja firmada** de qué equipo tiene quién, con IMEI. Suena burocrático y
   es lo que resuelve la discusión el día que un teléfono no vuelve.

**Cuándo sí poner MDM:** cuando sean más de diez equipos, cuando haya rotación
de vendedores frecuente, o el día que haga falta desplegar una versión nueva de
la app a todos sin que cada uno instale un APK a mano. Ese último punto es el
que suele decidirlo.

---

## 5. Los accesos del servidor

### Los tres roles de PostgreSQL, y por qué son tres

| Rol | Lo usa | Puede |
|---|---|---|
| **dueño** (`dsd`/`postgres`) | Alembic, workers, CLI | Todo. Salta RLS por ser dueño de las tablas |
| **`dsd_api`** | La API | Leer y escribir **dentro del alcance de cada petición**. Sin DDL, sin BYPASSRLS |
| **`dsd_analitica`** | El laboratorio Streamlit | Solo `SELECT` |

El del medio es el que hace que RLS sirva. Sin `DSD_DATABASE_URL_API`, la API se
conecta con el rol dueño y **las políticas quedan escritas y sin efecto**. En
producción la API no arranca sin esa variable; `/salud` lo reporta en `rls`.

En el despliegue con compose **los dos roles los crea el servicio `roles`**, que
corre después de las migraciones y aplica `db/ops/rol_api.sql` y
`db/ops/rol_analitico.sql` con las claves del `.env`. Es idempotente y se repite
en cada `up`, así que **rotar una de esas dos claves es cambiarla en `.env` y
volver a levantar**:

```bash
docker compose up -d        # reaplica el script antes de que la API arranque
```

A mano, fuera de compose —o al restaurar un respaldo, donde los roles no vienen
en el dump (`docs/RESPALDOS.md`)—:

```bash
psql -d dsd -v clave_api="$(openssl rand -hex 24)" -f server/db/ops/rol_api.sql
```

### Rotación de secretos

| Secreto | Cuándo rotarlo | Qué se rompe al rotar |
|---|---|---|
| `DSD_JWT_SECRETO` | Si se sospecha fuga | **Todas** las sesiones y tokens. Los vendedores entran igual (el PIN es local) pero no pueden sincronizar hasta volver a entrar con señal |
| Clave de `dsd_api` | Cada tanto, y al cambiar de personal de TI | Nada, si se actualiza el `.env` y se reinicia la API |
| Clave de `dsd_analitica` | Idem | El laboratorio |
| `DSD_METRICAS_TOKEN` | Idem | El scrape del monitor |
| Frase del cifrado de respaldos | **Nunca a la ligera** | Los respaldos viejos quedan ilegibles. Si se rota, hay que conservar la frase anterior mientras existan respaldos cifrados con ella |
| Keystore del APK (`dsd-release.jks`) | **NUNCA** | Los teléfonos ya instalados dejan de poder actualizarse. Ver abajo |

> La frase de los respaldos **en papel**, fuera de la mini PC. Si el disco
> muere, la frase muere con él y los respaldos remotos no se abren.

### El keystore del APK no se rota: se conserva

Es el único secreto de esta lista que **no se puede cambiar nunca**, y la razón no
es política sino de cómo funciona Android: solo acepta actualizar una app
instalada si el APK nuevo viene firmado con la **misma** llave. Rotarla —o
perderla— significa que los teléfonos que ya están en la calle no se pueden
actualizar más. El único camino sería desinstalar, y desinstalar **borra la base
local del vendedor**, con las ventas, los cobros y las mermas que todavía no
hubiera subido: dinero que ocurrió y que ya no está en ninguna cifra.

Así que se trata como lo que es, un activo de la empresa y no un archivo de
trabajo:

- Vive **fuera del repositorio** (`.gitignore` cubre `key.properties`, `*.jks` y
  `*.keystore`) y fuera de la mini PC de la oficina.
- **Dos respaldos en sitios distintos**, uno de ellos fuera del local.
- La contraseña y el `alias` **en papel**, con la frase del cifrado de respaldos.
  El archivo sin la contraseña no sirve de nada.
- La **huella SHA-256** del certificado, apuntada desde el primer APK. Es con lo
  que se comprueba que un APK nuevo va a poder actualizar a los instalados;
  `make apk` la imprime en cada build.

El procedimiento completo está en
[ENTORNO-WINDOWS §4.2](ENTORNO-WINDOWS.md#42-el-apk-de-producción-y-la-llave-que-no-se-puede-perder).

---

## 5.1 El disco de la mini PC

Este documento exigía cifrado en el **teléfono** y en los **respaldos que salen
del edificio**, y no decía nada del disco del servidor. Ahí viven las tres cosas
juntas: la base completa, el `.env` con cuatro secretos en texto plano, y la copia
local de los respaldos (`~/respaldos-dsd`). Quien se lleve el equipo se lleva las
tres.

### Primero, qué protege y qué no

Esto es lo que más se malentiende, así que va antes de cualquier comando.

**El cifrado de disco protege la máquina APAGADA.** Encendida —que es siempre— el
disco está abierto: el sistema lo necesita para funcionar. Quien entre a la
oficina con el servidor prendido y consiga una cuenta con permisos lee todo, y el
cifrado no interviene. Lo que impide es que alguien se lleve el equipo, o le saque
el SSD, y lo lea en otra parte.

Que es, precisamente, el caso realista:

| Lo que de verdad pasa | ¿Lo tapa el cifrado? |
|---|---|
| Se roban la mini PC de la oficina | **Sí** |
| Le sacan el SSD y lo leen en una laptop | **Sí** |
| Se llevan el disco externo con un respaldo | Ya lo tapaba GPG (`RESPALDOS.md` §3) |
| Alguien con acceso físico mientras está encendida | **No** — eso es la puerta y los permisos |
| Un bug nuestro que filtra otra ruta | **No** — eso es RLS (migración 0022) |

### La tensión que decide todo: el arranque

Un disco LUKS pide su frase al arrancar. En un servidor sin pantalla ni teclado y
sin nadie en la oficina, eso significa:

> Hay un apagón largo, el UPS se agota, el equipo se apaga. A las 6 de la mañana
> el vendedor sale a ruta y **el servidor sigue abajo**, esperando que alguien
> vaya a teclear una frase.

No es hipotético: es el escenario que el UPS existe para cubrir, y el UPS solo
cubre los cortes cortos. Así que la elección real es entre cuatro cosas:

| Opción | ¿Protege el equipo apagado? | ¿Arranca solo? |
|---|---|---|
| Sin cifrar | No | Sí |
| LUKS + frase al arrancar | Sí, del todo | **No** |
| **LUKS + llave sellada en el TPM** | Sí si sacan el disco; no si arrancan el equipo | **Sí** |
| LUKS + TPM **con PIN** | Sí, del todo | No |

**La recomendación es la tercera**, y la razón es que el ataque que de verdad
ocurre en una oficina de distribución es «se llevaron la computadora», no «un
atacante con tiempo quiso la cartera». El TPM tapa eso sin dejar la ruta esperando
a que alguien conduzca a la oficina.

Y el hueco que deja —arrancar el equipo robado— se cierra en el BIOS, no en el
disco: **contraseña de BIOS y arranque desde USB deshabilitado**. Sin eso, quien
tenga el equipo puede arrancar un Ubuntu en vivo firmado y el TPM entregaría la
llave igual, porque la medición de arranque seguro (PCR 7) no distingue un USB
firmado del sistema instalado. Con eso, para saltarse la contraseña del BIOS hay
que resetear el CMOS — **y eso resetea el TPM, que borra la llave**. El disco queda
cerrado. Las dos medidas juntas son lo que hace que esto sirva; por separado,
ninguna.

> Si prefieres la protección completa y asumir el costo, usa **frase al arrancar**
> y acepta que después de cada apagón largo alguien tiene que ir. Es una decisión
> de operación, no técnica. `dropbear-initramfs` permite teclear la frase por SSH,
> pero solo desde la red local: el túnel de Cloudflare no existe todavía en ese
> punto del arranque, así que no sirve para desbloquear desde fuera.

### Cuándo se hace: al instalar, no después

**Antes de desplegar nada.** El instalador de Ubuntu Server cifra el disco de
entrada; cifrar una instalación que ya está operando se puede hacer
(`cryptsetup reencrypt`) pero es un procedimiento largo sobre datos reales, y aquí
no hace falta correr ese riesgo porque el servidor todavía no existe.

Si algún día hay que cifrar una mini PC que ya opera: **reinstalar y restaurar de
un respaldo sale más barato y más seguro** que recifrar en sitio. El respaldo y el
simulacro ya existen (`RESPALDOS.md`), que es lo que hace viable esa respuesta.

### El procedimiento

**1. Antes de instalar, en el BIOS:**

- Activa el **TPM** (en los mini PC con Intel suele llamarse *Intel PTT* o
  *Security Device Support*).
- Activa **Secure Boot**.
- Pon **contraseña de administrador** del BIOS.
- **Deshabilita el arranque desde USB y desde red**, y deja el SSD como único
  dispositivo de arranque.

Los cuatro, no tres. El tercero y el cuarto son los que cierran el hueco de arriba.

**2. Instala Ubuntu Server LTS** y en el paso de almacenamiento elige:

```
[X] Use an entire disk
[X] Set up this disk as an LVM group
[X] Encrypt the LVM group with LUKS
```

Te pide una frase. **Esa frase es la llave de recuperación de todo el sistema.**
Genérala como las demás y apúntala en papel:

```bash
openssl rand -hex 24
```

**3. Apúntala donde ya están las otras dos.** En papel, fuera de la mini PC, con:

- la frase del cifrado de respaldos (`RESPALDOS.md`),
- la contraseña del keystore del APK (`ENTORNO-WINDOWS.md` §4.2).

Son tres secretos que, si se pierden, no se regeneran. Van juntos.

**4. Comprueba que quedó cifrado:**

```bash
make cifrado-revisar
```

**5. Sella la llave en el TPM**, para que arranque solo.

> **Esto es lo único de este procedimiento que no pude probar**, y conviene que lo
> sepas antes de empezar: aquí no hay TPM ni Ubuntu Server. Lo que sigue son los
> dos caminos conocidos, en orden, con la prueba que dice si funcionó. **El paso 6
> no es opcional**: es lo que distingue «lo configuré» de «funciona».

Primero, que haya TPM:

```bash
systemd-analyze has-tpm2      # systemd 254+; si no existe el subcomando:
ls -l /dev/tpmrm0             # tiene que estar
```

**Camino A — `systemd-cryptenroll`.** El de systemd, el más directo:

```bash
# La partición LUKS es la que `make cifrado-revisar` nombra.
sudo systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=7 /dev/nvme0n1p3
```

Y hay que agregar la opción a `/etc/crypttab`. **A mano, no con un `sed`**: si ese
archivo queda mal, el equipo no arranca.

```bash
sudo cp /etc/crypttab /etc/crypttab.antes      # por si acaso
sudoedit /etc/crypttab
```

La línea pasa de esto:

```
dm_crypt-0 UUID=1a2b3c…  none  luks,discard
```

a esto —solo se agrega la opción al final, separada por coma:

```
dm_crypt-0 UUID=1a2b3c…  none  luks,discard,tpm2-device=auto
```

Y después:

```bash
sudo update-initramfs -u -k all
```

**Camino B — `clevis`, si el A no funciona.** El initramfs de Ubuntu LTS usa los
scripts clásicos de `cryptsetup` y no siempre honra `tpm2-device=auto`, que es una
opción de `systemd-cryptsetup`. Si después del paso 6 el equipo sigue pidiendo la
frase, este es el camino que sí está empaquetado para Ubuntu:

```bash
sudo apt install -y clevis clevis-luks clevis-tpm2 clevis-initramfs
sudo clevis luks bind -d /dev/nvme0n1p3 tpm2 '{"pcr_ids":"7"}'
sudo update-initramfs -u -k all
```

`clevis luks bind` también agrega una ranura y deja la frase donde estaba.

> **Ni `systemd-cryptenroll` ni `clevis` borran la frase**: agregan una segunda
> ranura de llave, la que abre el TPM. **Nunca uses `--wipe-slot` sobre la ranura
> de la frase.** Si queda el TPM como única llave, una actualización de BIOS —o
> cambiar la pila de la tarjeta madre— resetea el TPM y **el disco no se vuelve a
> abrir nunca**. Es la forma más silenciosa de perder la operación completa, sale
> de seguir un tutorial hasta el paso que dice «wipe-slot» creyendo que limpia
> algo, y `make cifrado-revisar` la marca como FALLA justamente por eso.

**6. La prueba que importa, y no la salta nadie:**

```bash
sudo reboot        # primero un reinicio normal
```

Si arrancó sin pedir nada, el sellado funcionó. Y luego **la de verdad**:

> **Desenchufa el equipo de la corriente** —con el UPS apagado o fuera— espera un
> minuto, y vuelve a enchufarlo.

Tiene que levantar solo y `/salud` tiene que responder sin que nadie toque nada.
Eso es lo único que prueba que la ruta de mañana a las 6 no se va a quedar
esperando. Si pide la frase, el sellado no sirvió: revisa `/etc/crypttab` y el
initramfs, o asume la opción de la frase manual **a sabiendas**.

### Lo que lo rompe después, y qué hacer

| Qué pasó | Síntoma | Qué hacer |
|---|---|---|
| Actualización de BIOS/firmware | Pide la frase al arrancar | Teclea la frase y **vuelve a sellar** contra las mediciones nuevas: camino A, `systemd-cryptenroll --wipe-slot=tpm2 --tpm2-device=auto --tpm2-pcrs=7 <part>`; camino B, `clevis luks unbind -d <part> -s <ranura>` y volver a hacer `bind` |
| Se reseteó el CMOS o cambió la tarjeta madre | Ídem | Igual que arriba |
| Alguien borró la ranura de la frase | Nada, hasta que el TPM se resetee — y entonces se perdió todo | Agregar una frase **ahora**: `cryptsetup luksAddKey <part>` |

El `--wipe-slot=tpm2` de la primera fila **sí** es correcto: borra la ranura del
TPM, no la de la frase, y es la forma de volver a sellar contra las mediciones
nuevas.

> **Las actualizaciones de firmware se hacen estando en la oficina, nunca en
> remoto.** Es la consecuencia práctica de la primera fila y es fácil de pisar:
> `fwupd` aplica firmware **al reiniciar**, así que un `apt upgrade` que lo
> arrastre, seguido de un reinicio desde casa, deja el servidor pidiendo la frase
> con nadie enfrente — y la ruta de la mañana esperando. Si el equipo no las
> necesita, lo más simple es no instalar `fwupd`; si las necesita, se programan
> para un día que alguien esté ahí y se vuelve a sellar en la misma visita.

### Lo que esto NO reemplaza

- **Los respaldos siguen cifrándose aparte** (`RESPALDOS.md` §3). Un respaldo que
  sale del edificio ya no está en el disco cifrado.
- **El `.env` sigue siendo texto plano con la máquina encendida.** Lo único que lo
  separa de otra cuenta del sistema son sus permisos: `chmod 600 .env`.
  `make cifrado-revisar` lo revisa.
- **RLS sigue siendo lo que protege de un bug nuestro**, que es el riesgo más
  probable de este sistema (§1).

---

## 6. Qué mirar, y cada cuánto

| Cada | Dónde | Qué busca |
|---|---|---|
| Día | `Panel → Teléfonos` | Equipos sin subir hoy, accesos por caducar |
| Día | `Panel → Cuarentena` | Operaciones rechazadas: es dinero que ocurrió y no está en ninguna cifra |
| Semana | `simulacro.log` | Que el respaldo de verdad se pueda restaurar |
| Semana | `/metrics` o el log | `dsd_jobs_fallidos`, `dsd_jobs_pendientes` creciendo |
| Mes | La bitácora de RESPALDOS.md §4 | Que el simulacro se esté haciendo |
| Mes | `make cifrado-revisar` | Que el disco siga cifrado y que la frase de recuperación siga en su ranura (§5.1) |
| Al desplegar | `curl /salud` | `ok: true` y `rls: true` |

Y una regla que no es una métrica: **si una pantalla del panel empieza a salir
vacía**, lo primero que hay que descartar es RLS — un alcance que no se fijó
devuelve cero renglones sin ningún error en el log. `/salud` dice si está
activo, y `docs/adr/0002-reglas-de-negocio.md` §33 explica el modo de fallo.
