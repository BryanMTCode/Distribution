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
| **Escaneo y fuerza bruta contra el servidor** | **Alta** | Es ruido de fondo de internet desde que el VPS existe: SSH solo con llave y cortafuegos (§5.1) |
| **Se pierde la cuenta del proveedor** | Baja | Respaldos fuera del PROVEEDOR, no solo fuera del servidor (§5.1) |

Tres conclusiones incómodas y correctas:

**El robo no es el caso que manda.** Los tres primeros renglones son el día a
día, y en los tres el teléfono trae dentro operación sin sincronizar. Un sistema
diseñado solo para el robo borraría de inmediato y perdería ventas cobradas cada
vez que alguien renuncia.

**El riesgo más probable no es un atacante, es un bug nuestro.** Por eso la
Fase 9 dedica una migración entera a RLS: no protege de quien tiene la
contraseña de la base, protege de una consulta nueva que se olvida de filtrar
por ruta.

**El servidor es el único renglón en el que se pierde todo de golpe.** Un teléfono
robado trae la ruta de un vendedor; el servidor trae la operación completa, los
secretos y la copia local de los respaldos. Y desde que vive en un VPS, su primer
renglón dejó de ser improbable: el escaneo contra una IP pública no es un ataque
dirigido, es tráfico constante, y basta una contraseña débil para que entre. La
§5.1 es lo que lo contiene.

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

> La frase de los respaldos **en papel**, fuera del servidor. Si pierdes el
> servidor —o la cuenta del proveedor— la frase se va con él y los respaldos
> remotos no se abren.

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
  `*.keystore`) y fuera del servidor.
- **Dos respaldos en sitios distintos**, uno de ellos fuera del local.
- La contraseña y el `alias` **en papel**, con la frase del cifrado de respaldos.
  El archivo sin la contraseña no sirve de nada.
- La **huella SHA-256** del certificado, apuntada desde el primer APK. Es con lo
  que se comprueba que un APK nuevo va a poder actualizar a los instalados;
  `make apk` la imprime en cada build.

El procedimiento completo está en
[ENTORNO-WINDOWS §4.2](ENTORNO-WINDOWS.md#42-el-apk-de-producción-y-la-llave-que-no-se-puede-perder).

---

## 5.1 El servidor, que ahora está en internet

Este documento exigía cifrado en el **teléfono** y en los **respaldos que salen del
edificio**, y no decía nada del servidor. Ahí viven las tres cosas juntas: la base
completa, el `.env` con cuatro secretos en texto plano, y la copia local de los
respaldos.

> **Esta sección se reescribió al mover el servidor a un VPS.** La versión anterior
> era un procedimiento de LUKS + TPM contra el robo de una mini PC de la oficina.
> La amenaza cambió, no desapareció — y lo que la reemplaza no es menos trabajo,
> es otro trabajo. El razonamiento está en [ADR 0002 §47](adr/0002-reglas-de-negocio.md).

### El modelo de amenaza del VPS, dicho en voz alta

| Lo que de verdad pasa | Qué lo contiene |
|---|---|
| **Escaneo y fuerza bruta contra SSH**, desde el minuto uno | SSH solo con llave, root sin acceso (`DESPLIEGUE.md` §3) |
| **Un puerto abierto sin querer** (la base, sobre todo) | ufw + solo 80/443 publicados, y la trampa de Docker (`DESPLIEGUE.md` §4) |
| El proveedor o el hipervisor pueden leer el disco | Nada, realmente. Ver abajo |
| Un snapshot olvidado con la base dentro | Borrar los snapshots viejos; no son respaldos (§7 de `DESPLIEGUE.md`) |
| **Perder la cuenta del proveedor** | Respaldos fuera del proveedor, no solo fuera del servidor |
| Alguien con acceso al `.env` dentro del servidor | `chmod 600` y que nadie más tenga cuenta |
| Un bug nuestro que filtra otra ruta | RLS (migración 0022) — sigue siendo el riesgo más probable |

El primer renglón es el que cambió de categoría. En la oficina, detrás del túnel de
Cloudflare, el servidor **no tenía ni un puerto abierto**: nadie podía intentar
nada contra él. Un VPS tiene IP pública y lo escanean todo el día, desde que
existe. No es un ataque dirigido a ti; es ruido de fondo de internet, y basta una
contraseña débil para que el ruido entre.

### Por qué el disco NO va cifrado, dicho a propósito

Es una decisión, no un olvido, y conviene que esté escrita porque es la pregunta
que cualquiera haría.

El cifrado de disco protege la máquina **apagada**. En la oficina eso tenía un
sentido claro: alguien se lleva la mini PC o le saca el SSD. En un VPS nadie se
lleva tu disco.

¿Y el proveedor? Puede leerlo, y **el cifrado tampoco lo evita**: la frase tiene
que entrar al arrancar, así que vive en la memoria de una máquina virtual que el
hipervisor controla. Cifrar el disco de un VPS protege de que alguien compre el
servidor usado dentro de diez años, no del operador.

Y el costo es real: la mayoría de los VPS no exponen TPM, así que LUKS significa
teclear la frase por la consola web del proveedor **en cada reinicio** — incluidos
los que el proveedor hace por mantenimiento del host, de noche y sin avisar. Eso
convierte un reinicio rutinario en una ruta que no sale a las 6 de la mañana.

Un riesgo que el cifrado no cubre, a cambio de una interrupción que sí ocurre, no
es un buen cambio. **Lo que de verdad protege los datos fuera del servidor son los
respaldos cifrados**, que ya existen (`RESPALDOS.md` §3) y cubren el caso que
importa: una copia que sale de la máquina.

> Si algún día la operación mueve dinero suficiente para que la lectura por parte
> del proveedor sea un riesgo que no quieras aceptar, la respuesta no es cifrar el
> disco del VPS: es **volver a un servidor propio**, y entonces el procedimiento de
> LUKS + TPM que estaba aquí vuelve a tener sentido. Vive en el historial de git,
> en el commit que lo escribió.

### Lo que sí hay que hacer, y que `make servidor-revisar` comprueba

```bash
make servidor-revisar
```

| Qué | Por qué |
|---|---|
| Cortafuegos activo, solo 22/80/443 | El servidor está en internet abierto |
| Que Docker no publique nada más que 80/443 | Docker salta ufw y `ufw status` no lo dice |
| SSH sin contraseñas, root sin acceso | Fuerza bruta desde el minuto uno |
| La zona horaria de la operación | Las imágenes de VPS vienen en UTC, y en UTC−6 eso descuadra el arqueo (`ARRANQUE-DIARIO`, Fase 7) |
| `.env` en 600 | Son cuatro secretos en texto plano mientras el servidor corre |
| Si el disco **sí** está cifrado: que quede una frase de recuperación | Con el TPM como única llave, un reinicio del firmware lo vuelve ilegible para siempre |

Sale con error si algo está mal y dice qué comando lo arregla. **No comprueba lo
único que no se puede comprobar leyendo configuración:** que el servidor vuelva
solo después de un reinicio. Eso se prueba reiniciándolo.

### Lo que esto NO reemplaza

- **Los respaldos siguen cifrándose aparte** (`RESPALDOS.md` §3), y ahora tienen
  que salir **del proveedor**, no solo del servidor: perder la cuenta es perder las
  dos cosas a la vez.
- **El `.env` sigue siendo texto plano** con el servidor encendido, que es siempre.
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
| Mes | `make servidor-revisar` | Cortafuegos, SSH, puertos publicados, hora y disco (§5.1) |
| Al desplegar | `curl /salud` | `ok: true` y `rls: true` |

Y una regla que no es una métrica: **si una pantalla del panel empieza a salir
vacía**, lo primero que hay que descartar es RLS — un alcance que no se fijó
devuelve cero renglones sin ningún error en el log. `/salud` dice si está
activo, y `docs/adr/0002-reglas-de-negocio.md` §33 explica el modo de fallo.
