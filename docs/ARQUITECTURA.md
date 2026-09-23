# Propuesta Arquitectónica — Sistema DSD (Direct Store Delivery)

> Distribuidora de abarrotes. Servidor local en oficina + app móvil multi-rol + panel web analítico.
> Documento vivo. Última revisión: 2026-09-15.

---

## 0. Tres principios que condicionan todo el diseño

### 0.1 El mundo físico ya ocurrió

Cuando el vendedor imprime el ticket y entrega la mercancía, eso **ya pasó** — aunque el precio haya
cambiado en la oficina hace 10 minutos o el cliente esté sobre su límite de crédito.

**Regla:** el servidor *nunca* rechaza una venta offline por reglas de negocio. La acepta, la aplica y la
marca `requiere_revision = true` con un motivo. Un servidor que rechaza ventas ya cobradas descuadra la
caja y destruye la confianza del vendedor en el sistema.

Solo se rechaza lo estructuralmente imposible (payload corrupto, producto inexistente, firma inválida) y
eso va a **cuarentena**, nunca a la basura.

### 0.2 El inventario del camión tiene un solo dueño

Ésta es la razón por la que el offline es viable. El almacén `CAMION_XX` es modificado **exclusivamente**
por su vendedor. No hay concurrencia ⇒ no hay conflictos de merge ⇒ no hace falta CRDT ni resolución
automática de conflictos.

Si la oficina necesita mover stock de un camión, se modela como **traspaso pendiente** que el vendedor
debe aceptar en la app. Nunca como un `UPDATE` directo sobre existencias del camión.

### 0.3 "Tiempo real" es "tiempo real de lo sincronizado"

Si una ruta lleva 2 horas sin señal, el dashboard miente. Cada tarjeta del panel de Gerencia muestra
`Ruta 4 — última sync hace 47 min`. Un dashboard que aparenta una certeza que no tiene provoca
decisiones malas.

---

## 1. Stack tecnológico

**Decisión registrada en [`adr/0001-stack-tecnologico.md`](adr/0001-stack-tecnologico.md).** Python de
extremo a extremo en el servidor, Flutter en el móvil, y la superficie web partida en dos según lo que
cada mitad realmente hace.

### 1.1 App móvil — Flutter + SQLite (Drift) + SQLCipher

| Componente | Elección | Justificación |
|---|---|---|
| Framework | Flutter 3.x (Dart) | Un solo código; render propio (se ve igual en gama baja); ecosistema ESC/POS y Bluetooth Classic (SPP) notablemente más maduro que React Native. |
| BD local | SQLite vía **Drift** | Se requieren transacciones ACID y modelo relacional (venta → partidas → movimientos). Drift aporta tipado fuerte, migraciones versionadas y queries reactivas. Descartados Hive/Isar por ser clave-valor/documentales. |
| Cifrado en reposo | **SQLCipher** | En el teléfono viven precios, márgenes, cartera y efectivo. El equipo se pierde y se roba. No es opcional. |
| Estado | Riverpod | Simple, testeable, sin el boilerplate de BLoC. |
| Impresión | `esc_pos_utils` + `print_bluetooth_thermal` / `flutter_blue_plus` | Payload ESC/POS generado localmente; 100% offline. |
| Mapas / GPS | `geolocator` + `flutter_map` (OSM) | OSM evita la factura de Google Maps, que escala mal. |
| Dinero | paquete `decimal` | Dart **no tiene decimal nativo**; su `double` es IEEE-754. Ver §1.6. |

### 1.2 Backend — Python 3.12+ · FastAPI · SQLAlchemy 2.0 · PostgreSQL 17

| Componente | Elección | Justificación |
|---|---|---|
| Lenguaje | **Python 3.12+** | Un solo lenguaje para API, panel de operación, jobs y analítica. Con Flutter en el móvil, cualquier backend implica dos lenguajes: Python es el que además cubre la Fase 8 sin un tercer stack. |
| Framework | **FastAPI** | Async nativo, validación por tipos, OpenAPI generado de fábrica (de ahí sale el cliente Dart). |
| ORM | **SQLAlchemy 2.0** (ORM tipado) + **Pydantic v2** por separado | **No SQLModel.** El contrato del dispositivo y la tabla deben estar desacoplados: el teléfono manda una venta sin `folio_servidor`, `fecha_servidor` ni `requiere_revision` — los agrega el servidor. Fusionar ambas capas en una clase obliga a marcar todo opcional y la validación deja de validar. |
| Driver | **psycopg 3** (async) | Maneja `Decimal` y `uuid` nativamente y trae `COPY` de primera, que se necesita para lotes grandes y para el refresh del esquema estrella. |
| Migraciones | **Alembic**, ejecutando SQL escrito a mano | `autogenerate` **no** detecta triggers, `EXCLUDE`, columnas generadas ni extensiones. Los `.sql` de `server/db/migrations/` son la fuente de verdad; cada revisión los aplica con `op.execute()`. Si el ORM "sincroniza" el esquema, un día propondrá borrar el trigger de inmutabilidad. |
| Base de datos | **PostgreSQL 17 + PostGIS** | Vistas materializadas, window functions, JSONB, `LISTEN/NOTIFY`, `EXCLUDE`, replicación lógica. PostGIS resuelve "clientes cerca de", densidad de no-drops y análisis de ruta. |
| Colas | **Cola en PostgreSQL** con `FOR UPDATE SKIP LOCKED` | Ver §1.5. Sin Redis en la v1. |
| Auth | `pyjwt` + `argon2-cffi` (Argon2id) | El mismo hash se replica al dispositivo para el login offline. Ver §1.6. |
| Tiempo real | `sse-starlette` + `LISTEN/NOTIFY` | Unidireccional servidor→app es todo lo que Gerencia necesita; más simple y robusto que WebSockets tras un túnel. |
| Pruebas | `pytest` + `pytest-asyncio` + `testcontainers` + **Hypothesis** | Hypothesis es la razón técnica más fuerte de este stack: ver §1.7. |
| Paquetes | **uv** con lockfile versionado | Python en producción es más frágil que Node; el lockfile y Docker lo compensan. |

**Lo que FastAPI no te da y tienes que imponer tú:** estructura. NestJS obliga a modularizar; FastAPI te
deja hacer lo que quieras, y un proyecto de dos años mantenido por una sola persona se degrada sin
disciplina. La estructura de `server/app/` se define en la Fase 0 y no se renegocia.

**Disciplina async — el error #1 en FastAPI:** una llamada síncrona bloqueante dentro de un `async def`
congela el event loop entero, sin avisar, degradándose solo bajo carga. Regla: o todo el camino es async
(`psycopg` async, `httpx`), o el endpoint se declara `def` normal y FastAPI lo manda al threadpool.
Nunca se mezcla.

### 1.3 Superficie web — dos mitades con reglas distintas

El panel administrativo **no es un dashboard**. Solo una de sus pantallas es análisis; el resto son
escrituras transaccionales críticas: resolver la cuarentena de sync, fusionar clientes duplicados,
confirmar cargas, cerrar liquidaciones, autorizar ajustes de inventario. Por eso se parte:

```
┌──────────────────────────────────────────────────────────────────┐
│  PANEL DE OPERACIÓN          FastAPI + Jinja2 + HTMX             │
│  CRUD, cuarentena, precios, cargas, liquidaciones, ajustes       │
│  · Mismo proceso, misma sesión, mismo RBAC que la API            │
│  · Sin cadena de build, sin npm, sin JavaScript propio           │
│  · Escrituras con el mismo patrón de idempotencia que /sync/push │
└──────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│  LABORATORIO ANALÍTICO       Streamlit + Pandas / Polars         │
│  Cohortes, drop size, churn, pronósticos, modelos estadísticos   │
│  · SOLO LECTURA, con rol de PostgreSQL de solo lectura           │
│  · Contra el esquema estrella y las vistas materializadas        │
│  · Servicio Docker y subdominio aparte tras el túnel             │
└──────────────────────────────────────────────────────────────────┘
```

**Por qué HTMX y no Streamlit para el CRUD.** Streamlit re-ejecuta el script completo de arriba a abajo
en cada interacción, y su estado (`st.session_state`) es frágil para flujos de varios pasos. Poner las
escrituras administrativas ahí contradice el principio que sostiene todo el sistema: es un framework
cuyo modelo de ejecución facilita disparar dos veces la misma acción. Además carece de modelo de
usuarios (no hay RBAC por ruta) y no tiene URL por registro — no se puede mandar un enlace a "la
liquidación LQ-0847". Con Jinja2 + HTMX se devuelven fragmentos de HTML desde FastAPI: tablas con
acciones por fila, formularios con validación del servidor y modales, sin build step.

**Por qué Streamlit sigue estando.** Para exploración es exactamente la herramienta correcta, y es el
camino natural hacia los modelos estadísticos de la Fase 8. La condición es que sea **solo lectura**: un
rol de PostgreSQL sin permisos de escritura, de modo que una consulta mal escrita en el laboratorio no
pueda tocar la cartera. Pandas es la última milla (el modelo, el pronóstico), **no la capa de
agregación**: agregar un año de ventas en memoria es órdenes de magnitud más lento que un `GROUP BY`
sobre vistas materializadas.

> Si más adelante los cuadernos crecen, **marimo** merece evaluarse: es reactivo por grafo de
> dependencias en vez de re-ejecutar el script, y sus cuadernos son archivos `.py` versionables.

### 1.4 Infraestructura

Mini PC (Intel N100 o similar, 16 GB RAM, SSD NVMe) · Ubuntu Server LTS · **Docker Compose**:
`postgres`, `api` (uvicorn, 2–4 workers), `worker`, `analytics` (Streamlit), `caddy` (TLS automático),
`backup`.

**Conectividad — decisión crítica.** Un servidor en la oficina debe ser alcanzable desde la calle.
**Cloudflare Tunnel** (gratis): sin IP fija, sin abrir puertos, TLS y protección DDoS incluidos.

**Asume que tú eres el SRE:**

- **UPS / no-break obligatorio.** Un apagón con 8 rutas sincronizando corrompe la BD.
- **Respaldo desde el día 1:** `pg_dump` diario + WAL archiving, empujado con `restic` a Backblaze B2 o S3.
- **Simulacro de restauración.** Un respaldo que nunca restauraste no es un respaldo.
- **Failover 4G** en el router de la oficina.

### 1.5 Cola de trabajos: PostgreSQL, no Celery ni Redis

```sql
SELECT * FROM jobs
 WHERE estado = 'pendiente' AND ejecutar_en <= now()
 ORDER BY id
 FOR UPDATE SKIP LOCKED
 LIMIT 10;
```

`FOR UPDATE SKIP LOCKED` da una cola con múltiples consumidores sin duplicar trabajo, y **encolar el job
queda dentro de la misma transacción que la escritura de negocio** — garantía que un broker externo no
puede ofrecer. Una pieza móvil menos que respaldar, monitorear y reiniciar tras un apagón. Celery es
pesado y sus semánticas de entrega sobre Redis son una fuente conocida de sorpresas.

Si algún día hace falta más, `arq` (asyncio nativo, mucho más ligero que Celery) es el siguiente escalón.
Con 10 rutas no se llega ahí.

### 1.6 Tres contratos entre Dart y Python que se cierran en la Fase 0

Son baratos ahora y carísimos después. Los tres se blindan igual: **un archivo de vectores de prueba
compartido en `contracts/`, que la suite de Python y la de Dart ejecutan en CI**. El día que alguien
cambie un serializador, un lado se pone rojo antes de producción y no seis meses después con miles de
tickets en cuarentena.

| Contrato | Riesgo si falla | Blindaje |
|---|---|---|
| **Dinero como *string* en el JSON** | Pydantic serializa `Decimal` a número JSON; Dart lo recibe como `double` IEEE-754 y aparece un centavo fantasma en una cartera que se arrastra meses. | `Decimal` en Python, paquete `decimal` en Dart, `numeric` en PostgreSQL, **string en el wire**. Configurado desde el primer endpoint. |
| **Hash canónico del payload** | `hash_payload` debe dar el mismo SHA-256 en ambos lados. Si no, la alarma contra manipulación (mismo `operacion_id`, payload distinto) se vuelve un generador de falsos positivos y se acaba desactivando. Difieren en silencio: orden de claves, `Decimal` vs `double`, formato de fecha y zona, `null` vs clave ausente. | `contracts/canonical_vectors.json`: ~30 payloads con su hash esperado. |
| **Parámetros de Argon2id** | Los bindings de Argon2 en Dart son FFI y no comparten defaults con `argon2-cffi`. Un hash que el teléfono no puede verificar significa que el vendedor no entra sin señal — el peor momento para descubrirlo. | Memoria, iteraciones, paralelismo y longitud de sal fijados explícitamente, con vectores compartidos. |

### 1.7 Por qué este stack es mejor para *este* sistema

Tres ventajas que no son de gusto personal:

1. **`Decimal` nativo.** JavaScript no tiene tipo decimal: todo el dinero pasa por `float64` o por
   librerías de terceros. En un sistema de cobranza con saldos que se arrastran meses, eso es una fuente
   real de centavos perdidos.
2. **Hypothesis.** Para las pruebas de caos de la Fase 2, el *property-based testing* no tiene
   equivalente serio en JavaScript. Se declara la invariante y la máquina busca el contraejemplo —
   exactamente lo que necesita un motor de sincronización.
3. **Una sola mente para el analítico.** Vistas materializadas, jobs de refresh, esquema estrella y
   modelos viven en el mismo lenguaje que el ingest.

---

## 2. Arquitectura de datos

### 2.1 Roles y alcance

RBAC clásico. Regla de oro: **la UI oculta, el servidor prohíbe.** Cambiar la interfaz por rol es
cosmética; la seguridad vive en el backend.

```
usuarios → roles (vendedor | supervisor | gerente | admin)
         → permisos[]  (granular: ventas.crear, inventario.ajustar, ...)
         → alcance: rutas[], almacen_id (camión), sucursal_id
```

El JWT transporta `{ user_id, rol, permisos[], rutas[], almacen_id, device_id }`. Todo servicio del
backend pasa por un *scope guard* obligatorio que filtra por ruta/vendedor. Row Level Security de
PostgreSQL como segunda capa de defensa (Fase 9).

En Flutter, un `RoleGateway` en la raíz resuelve el shell de navegación tras el login
(`VendedorShell` vs `GerenciaShell`), con módulos **físicamente separados**: el perfil Gerencia no
compila contra la maquinaria offline. Son dos apps que comparten binario.

**Login offline.** El vendedor no siempre tendrá señal al iniciar el día. En el primer login online se
guarda un verificador local (hash **Argon2id** del PIN) en la BD cifrada, con vigencia (ej. 7 días sin
sincronizar ⇒ login forzosamente online). El dispositivo queda ligado: `device_id` registrado, una sesión
activa por equipo, lista de revocación para teléfono robado.

### 2.2 Inventario sin descuadres: el camión es un almacén

El corazón del modelo es un **libro mayor de movimientos inmutable**:

```
almacenes            BODEGA_PRINCIPAL, CAMION_01, CAMION_02, ...

movimientos_inventario   (append-only; nunca UPDATE ni DELETE, garantizado por trigger)
  id, tipo, almacen_origen, almacen_destino, producto_id,
  cantidad, unidad, documento_tipo, documento_id, fecha_servidor, usuario_id

existencias          (snapshot, actualizado en la MISMA transacción que el movimiento)
  almacen_id, producto_id, cantidad
```

El libro mayor es la verdad auditable; `existencias` es la caché transaccional. Un job nocturno
reconcilia ambos y alerta ante cualquier divergencia.

**Ciclo diario del DSD** — es lo que hace seguro el offline:

1. **Carga / embarque.** Al amanecer, traspaso `BODEGA_PRINCIPAL → CAMION_01`. Ese documento genera el
   *snapshot base* que el teléfono descarga con `carga_id` y versión.
2. **Venta en ruta (offline).** Todos los decrementos ocurren contra `CAMION_01`, que tiene un único
   dueño. Cero concurrencia, cero conflictos.
3. **Liquidación / cierre.** Retorno de producto no vendido y arqueo de efectivo. El servidor recalcula:

   ```
   carga − ventas − mermas − devoluciones − retorno = diferencia
   ```

   Si `diferencia ≠ 0` se levanta un **faltante** ligado al vendedor. Los descuadres se atrapan
   contablemente, no a mano.

**La bodega principal solo se mueve por eventos de carga y retorno, procesados por el servidor. Una venta
offline jamás toca el stock de bodega.** De ahí viene la garantía.

### 2.3 Sin tickets duplicados: idempotencia, no esperanza

Una red mala entrega el mismo lote dos veces. Es normal, no es un bug. La solución no es evitar el
duplicado en el envío, sino que **recibirlo dos veces no cambie nada**:

> **Entrega "al menos una vez" + receptor idempotente = efecto de exactamente una vez.**

Mecanismos:

| Mecanismo | Detalle |
|---|---|
| **UUID del dispositivo** | La venta nace con `venta_id = uuidv7()` en el teléfono; ese UUID *es* la PK en PostgreSQL. El ingest hace `INSERT ... ON CONFLICT (id) DO NOTHING`. Reenviar es inofensivo por construcción. Igual para clientes nuevos, cobros, mermas y no-drops. |
| **Dos folios, nunca uno** | `folio_local` = consecutivo del dispositivo (`VEND01-000123`), es el que se imprime, con `UNIQUE(dispositivo_id, consecutivo)`. `folio_servidor` = consecutivo global asignado al ingresar. **Nunca dejar que el teléfono adivine un consecutivo global**: dos vendedores offline emitirían el mismo folio. |
| **Patrón Outbox en el teléfono** | En la misma transacción SQLite se escribe la venta *y* su registro en `outbox`. Si la app muere entre ambos, no hay estado inconsistente posible. |
| **Registro de operaciones procesadas** | `sync_operaciones(operacion_id PK, hash_payload, resultado, procesado_at)`. Un `operacion_id` repetido devuelve el resultado guardado sin reprocesar. Repetido **con hash distinto** ⇒ alarma roja (bug o manipulación) ⇒ cuarentena. |
| **Sobre de visita atómico** | Alta de cliente + venta + cobro de una misma visita viajan en un sobre y se aplican en una transacción. Como todas las FK son UUIDs generados en el dispositivo, la integridad referencial funciona aunque el servidor nunca haya visto a ese cliente. |
| **Todo es inmutable** | Una venta jamás se edita: se cancela con un documento compensatorio (motivo obligatorio, PIN de supervisor opcional). Esto vuelve trivial la sincronización y deja auditoría completa. |
| **Relojes** | El reloj del teléfono miente. Se guardan `fecha_dispositivo` **y** `fecha_servidor`. Se ordena y factura por la del servidor; las desviaciones grandes se marcan para investigar. |

**Protocolo de sincronización:**

```
POST /sync/push   → { device_id, lote_id, operaciones[] }
                  ← por operación: aceptada | duplicada | rechazada + motivo

GET  /sync/pull?cursor=<bigint>
                  → catálogo, precios, clientes de ruta, cargas, saldos
```

Para el `pull` se usa una tabla `change_log` con `BIGSERIAL` como cursor monotónico.
**No usar `updated_at`**: con relojes desincronizados y transacciones concurrentes se pierden registros
en silencio.

Una operación rechazada **nunca bloquea la cola**: va a cuarentena para revisión en el panel web. Una
cola atorada es una app inservible.

**Dirección de propiedad del dato — la regla que elimina los merges:**

| Entidad | Dueño | Dirección |
|---|---|---|
| Catálogo, precios, promociones, límites de crédito | Servidor | Solo lectura en el teléfono |
| Ventas, cobros, mermas, no-drops, clientes nuevos | Dispositivo | Solo escritura hacia el servidor |
| Saldos de cartera | Servidor (autoridad) | El teléfono muestra caché con "actualizado hace X" |

Nadie edita lo mismo desde dos lados. Sin edición concurrente no hay conflictos que resolver.

**Cobranza.** El teléfono nunca calcula el saldo definitivo: registra el abono contra la factura. El
servidor aplica la asignación (FIFO, sobrante a saldo a favor) y es la única autoridad sobre la cartera.

**GPS.** Se guarda `lat, lng, precision_m, origen (gps|manual), capturado_en` en altas de cliente,
no-drops **y en cada venta**. El geo-sello de la venta es la mejor herramienta antifraude: dice si el
vendedor realmente estuvo ahí. Cuando ajusta manualmente, `origen = 'manual'` queda registrado — es dato
auditable, no es trampa.

**Riesgo poco obvio.** Restaurar un respaldo viejo del teléfono o reinstalar la app reproduce el outbox
completo. Las llaves de idempotencia evitan el duplicado, pero además se impone la regla:
**no se puede iniciar una carga nueva con operaciones pendientes del día anterior.**

---

## 3. Plan de desarrollo modular

Cada fase es una rebanada vertical desplegable. **El orden importa: la Fase 2 va antes que cualquier
funcionalidad de venta, y no es negociable.**

| Fase | Entregable | Duración |
|---|---|---|
| **0** | **Fundaciones.** Modelo de dominio y glosario escritos (unidades, cajas vs piezas, listas de precios, esquemas de crédito, reglas de descuento). Monorepo `/server /mobile /analytics /contracts`, Alembic sobre el SQL ya verificado, Docker Compose, CI, seeds. Auth + RBAC + registro de dispositivos. Los **tres contratos Dart↔Python** de §1.6. **Respaldos funcionando desde el día 1.** | 2–3 sem |
| **1** | **Catálogos y núcleo.** Productos, unidades y conversiones, listas de precios, clientes, rutas, almacenes, usuarios. API + primeras pantallas del panel de operación (Jinja2 + HTMX). | 2–3 sem |
| **2** | **Motor de sincronización. La fase más importante.** Outbox/inbox, cursores, idempotencia, cuarentena, lotes. Se construye contra una entidad de juguete y se demuestra con **pruebas de caos**: entrega duplicada, lote parcial, orden invertido, corte a media transacción, restauración de respaldo del dispositivo. Incluye pantalla de *inspector de sync* en la app. | 3–4 sem |
| **3** | **App vendedor MVP.** Catálogo offline, carrito, venta de contado, impresión Bluetooth, cola de sync. **Piloto con UN vendedor en UNA ruta durante 2 semanas, con el proceso de papel en paralelo.** | 3–4 sem |
| **4** | **Inventario de camión.** Carga/embarque, existencias offline, consulta online de bodega principal (con estado explícito "requiere conexión"), liquidación/cierre con cálculo de diferencias. | 2–3 sem |
| **5** | **Crédito y cobranza.** Venta a crédito, saldos, abonos en efectivo, corte de caja y arqueo. | 2–3 sem |
| **6** | **Operaciones secundarias.** Alta de clientes en calle con GPS + ajuste manual, mermas/devoluciones, no-drops con **catálogo de motivos cerrado** (texto libre = datos inanalizables). | 2 sem |
| **7** | **Perfil Gerencia móvil.** Dashboard sobre modelos de lectura precalculados, nunca sobre tablas transaccionales. Venta del día, avance por ruta vs objetivo, cobranza, no-drops, mapa. Cada tarjeta con su marca de última sincronización. | 2 sem |
| **8** | **Laboratorio analítico (Streamlit).** Esquema estrella (`fact_ventas`, `dim_cliente/producto/ruta/tiempo`) en el mismo PostgreSQL, alimentado por jobs. Drop size, frecuencia de visita, productividad por vendedor, rotación, clientes en riesgo de abandono, efectividad de visita. Rol de BD de solo lectura. | 3–4 sem |
| **9** | **Endurecimiento.** Logs estructurados, Sentry, métricas, simulacro de restauración, política de días máximos sin sincronizar, borrado remoto del dispositivo, MDM, RLS. | 2–3 sem |
| **10** | **Opcionales.** Compras/recepción, integración contable, CFDI. | — |

> **Sin data warehouse.** A volumen de miles de tickets diarios, PostgreSQL con vistas materializadas
> alcanza por años. ClickHouse/BigQuery serían complejidad sin retorno.

### 3.1 Detalle técnico de las Fases 0–2

**El modelo de datos no cambia.** Las 7 migraciones, el esquema SQLite y las 6 invariantes verificadas
son agnósticas del lenguaje. Haber empezado la Fase 0 por los datos y no por el framework es lo que
permitió cambiar de stack sin costo.

#### Estructura del repositorio (Fase 0)

```
server/
  pyproject.toml            uv, lockfile versionado
  app/
    main.py                 ensamblado de la app, nada de lógica
    core/                   config, seguridad, sesión de BD, dependencias
    domain/                 REGLAS DE NEGOCIO PURAS — sin imports de FastAPI
      ventas/               ni de SQLAlchemy. Testeable sin base de datos.
      inventario/
      sync/
    infra/
      models/               SQLAlchemy 2.0 (tablas)
      repos/                acceso a datos
    api/
      v1/                   routers + esquemas Pydantic (contrato del dispositivo)
      admin/                Jinja2 + HTMX (panel de operación)
    workers/                jobs en segundo plano
  db/migrations/            SQL verificado, envuelto por Alembic
  tests/
mobile/                     Flutter
analytics/                  Streamlit — servicio aparte, rol read-only
contracts/                  OpenAPI + vectores canónicos compartidos (§1.6)
```

La regla que salva el proyecto a los 18 meses: **`domain/` no importa nada de FastAPI ni de
SQLAlchemy.** Ahí viven el cálculo de liquidación, la aplicación FIFO de cobros y la validación de la
venta. Se prueban en milisegundos sin levantar una base de datos.

#### Generación del cliente Dart

FastAPI emite **OpenAPI 3.1**, y buena parte de los generadores de Dart todavía solo digieren 3.0. Se
resuelve en la Fase 0 con un paso de CI que produzca una versión degradada a 3.0.x del spec, y que de
ahí salga el cliente. Descubrirlo en la Fase 3, con 40 endpoints, significa escribir los modelos a mano.

#### UUIDv7

Lo genera **el teléfono**, no el servidor; Python solo recibe y valida. Para documentos nacidos en el
servidor: `uuid.uuid7()` está en la stdlib desde Python 3.14; en 3.12/3.13, la librería `uuid6`. En la
Fase 0, un test que compare la ordenabilidad temporal de 1,000 UUIDs generados en cada lado — toda la
ventaja de v7 sobre v4 es que ordenan por tiempo.

#### Motor de sincronización (Fase 2) — las cuatro piezas del ingest

**a) Un SAVEPOINT por sobre de visita. No es opcional.** Cuando PostgreSQL lanza un `IntegrityError`,
**la transacción completa queda abortada**: si se procesan 200 operaciones en una sola transacción y la
número 7 viola un constraint, las 193 restantes se pierden aunque fueran válidas.

```python
for sobre in sorted(lote.sobres, key=lambda s: s.secuencia):
    try:
        async with session.begin_nested():       # SAVEPOINT
            await aplicar_sobre(session, sobre)  # venta + cobro + cliente, atómico
            resultados.append(Aceptada(sobre.operacion_id))
    except IntegrityError as e:
        # El savepoint revierte solo este sobre; la sesión sigue usable.
        resultados.append(await a_cuarentena(sobre, e))
await session.commit()
```

Sin `begin_nested()`, un payload malformado envenena el lote entero y la cola del vendedor se atora —
justo el escenario que el diseño prohíbe.

**b) Idempotencia.**

```python
stmt = (insert(Venta).values(**datos)
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(Venta.id))
fue_nueva = (await session.execute(stmt)).scalar_one_or_none() is not None
# fue_nueva=False ⇒ reenvío. Se responde 'duplicada'. No es error.
```

El `.returning()` distingue *inserté* de *ya existía* sin una consulta extra, y alimenta el contador de
`duplicadas` en `sync_lotes`.

**c) Serializar los lotes del mismo dispositivo.** Un teléfono con red intermitente puede reintentar
mientras el envío original sigue procesándose. Un *advisory lock* lo resuelve sin tabla de bloqueos, y se
libera solo al terminar la transacción; los lotes de dispositivos distintos siguen en paralelo.

```python
await session.execute(
    text("SELECT pg_advisory_xact_lock(hashtext('sync_push'), hashtext(:dev))"),
    {"dev": str(device_id)},
)
```

**d) Pruebas de caos con Hypothesis.** Se declara la invariante y la máquina busca el contraejemplo:

```python
@given(operaciones=lotes_de_operaciones(), caos=estrategia_de_caos())
async def test_ningun_intercalado_duplica_un_ticket(operaciones, caos):
    # caos duplica lotes, los reordena, corta a media transacción,
    # y reproduce el outbox completo como si fuera un respaldo restaurado
    await ejecutar(operaciones, caos)
    assert await contar_ventas() == len({o.id for o in operaciones})
    assert await libro_mayor_cuadra_con_existencias()
```

Hypothesis encuentra el intercalado patológico que no se te habría ocurrido escribir a mano, y cuando
falla entrega el caso mínimo que lo reproduce.

### 3.1 Bandera fiscal (México)

**Imprimir un ticket no es facturar.** Lo que sale de la impresora Bluetooth es una *remisión* no fiscal.
El timbrado CFDI 4.0 ocurre en el servidor contra un PAC, probablemente con factura global diaria para
público en general.

Aislar el módulo fiscal detrás de una interfaz desde el principio (para poder cambiar de PAC), pero
**no construirlo en la v1**. Decisión pendiente: ¿la remisión impresa es suficiente para tus clientes?

### 3.2 Estimación honesta

**6 a 9 meses** para una v1 sólida trabajando solo, tiempo completo.

Quien prometa 3 meses está omitiendo la Fase 2, los respaldos o el piloto en paralelo — justo las tres
cosas que deciden si el sistema sobrevive al primer mes en la calle.

---

## 4. Decisiones de negocio

**Cerradas** en [`adr/0002-reglas-de-negocio.md`](adr/0002-reglas-de-negocio.md):

| Decisión | Definición |
|---|---|
| Modelo de venta | **Autoventa.** Se entrega y cobra en el momento; el inventario sale del camión al vender. Sin preventa. |
| Unidades | **Pieza y caja.** Sin granel, ninguna unidad es fraccionable. |
| Crédito | Límite en dinero por cliente. Al excederlo se **bloquea el crédito** pero **se sigue vendiendo de contado**. El bloqueo offline cuenta la cola local del dispositivo. |
| Comprobante | **Remisión no fiscal** por Bluetooth. CFDI en fase posterior. |
| Dispositivos | **Equipos de la empresa** (Android de gama baja). Sin BYOD, con MDM posible en la Fase 9. |

Ninguna obligó a modificar el esquema de datos: ya estaban modeladas.

**Abierta, no bloqueante:**

- [ ] **Lotes y caducidad.** El esquema lo soporta y hoy está apagado. Decisión operativa —exige que el
      vendedor distinga lotes al cargar y al liquidar— que conviene cerrar antes de la Fase 4, porque
      cambia esas dos pantallas.

Las decisiones de **stack** están en [`adr/0001-stack-tecnologico.md`](adr/0001-stack-tecnologico.md).
