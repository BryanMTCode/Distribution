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

Dos conclusiones incómodas y correctas:

**El robo no es el caso que manda.** Los tres primeros renglones son el día a
día, y en los tres el teléfono trae dentro operación sin sincronizar. Un sistema
diseñado solo para el robo borraría de inmediato y perdería ventas cobradas cada
vez que alguien renuncia.

**El riesgo más probable no es un atacante, es un bug nuestro.** Por eso la
Fase 9 dedica una migración entera a RLS: no protege de quien tiene la
contraseña de la base, protege de una consulta nueva que se olvida de filtrar
por ruta.

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

## 6. Qué mirar, y cada cuánto

| Cada | Dónde | Qué busca |
|---|---|---|
| Día | `Panel → Teléfonos` | Equipos sin subir hoy, accesos por caducar |
| Día | `Panel → Cuarentena` | Operaciones rechazadas: es dinero que ocurrió y no está en ninguna cifra |
| Semana | `simulacro.log` | Que el respaldo de verdad se pueda restaurar |
| Semana | `/metrics` o el log | `dsd_jobs_fallidos`, `dsd_jobs_pendientes` creciendo |
| Mes | La bitácora de RESPALDOS.md §4 | Que el simulacro se esté haciendo |
| Al desplegar | `curl /salud` | `ok: true` y `rls: true` |

Y una regla que no es una métrica: **si una pantalla del panel empieza a salir
vacía**, lo primero que hay que descartar es RLS — un alcance que no se fijó
devuelve cero renglones sin ningún error en el log. `/salud` dice si está
activo, y `docs/adr/0002-reglas-de-negocio.md` §33 explica el modo de fallo.
