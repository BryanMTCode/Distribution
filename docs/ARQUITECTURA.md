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

### 1.1 App móvil — Flutter + SQLite (Drift) + SQLCipher

| Componente | Elección | Justificación |
|---|---|---|
| Framework | Flutter 3.x (Dart) | Un solo código; render propio (se ve igual en gama baja); ecosistema ESC/POS y Bluetooth Classic (SPP) notablemente más maduro que React Native. |
| BD local | SQLite vía **Drift** | Se requieren transacciones ACID y modelo relacional (venta → partidas → movimientos). Drift aporta tipado fuerte, migraciones versionadas y queries reactivas. Descartados Hive/Isar por ser clave-valor/documentales. |
| Cifrado en reposo | **SQLCipher** | En el teléfono viven precios, márgenes, cartera y efectivo. El equipo se pierde y se roba. No es opcional. |
| Estado | Riverpod | Simple, testeable, sin el boilerplate de BLoC. |
| Impresión | `esc_pos_utils` + `print_bluetooth_thermal` / `flutter_blue_plus` | Payload ESC/POS generado localmente; 100% offline. |
| Mapas / GPS | `geolocator` + `flutter_map` (OSM) | OSM evita la factura de Google Maps, que escala mal. |

> **Alternativa considerada:** React Native + TypeScript (una sola sintaxis con el backend). Descartada:
> el soporte de impresoras Bluetooth Classic exige código nativo y semanas de integración.

### 1.2 Backend local — Node 22 + TypeScript + NestJS + PostgreSQL 17

| Componente | Elección | Justificación |
|---|---|---|
| Runtime | Node 22 + TypeScript | Mismo lenguaje que el dashboard web ⇒ la mitad de carga cognitiva para un desarrollador solo. |
| Framework | **NestJS** (adaptador Fastify) | Estructura modular opinionada: obliga a mantener orden en un proyecto de 2+ años mantenido por una persona. |
| Base de datos | **PostgreSQL 17** | Vistas materializadas, window functions, JSONB para payloads de sync, índices parciales, `LISTEN/NOTIFY`, replicación lógica si algún día hay réplica en nube. |
| ORM | **Drizzle** | SQL cercano al metal, clave para los reportes analíticos. (Prisma si se prioriza velocidad sobre control.) |
| Geo | **PostGIS** | Barato de habilitar; resuelve "clientes cerca de", densidad de no-drops y análisis de ruta. |
| Colas | Redis + **BullMQ** | Procesamiento de lotes de sync, refresh de vistas, alertas. |
| Tiempo real | **SSE** | Unidireccional servidor→app es todo lo que Gerencia necesita. Más simple y robusto que WebSockets tras un túnel. |
| Contrato | **OpenAPI** | Clientes Dart y TypeScript generados. Contract-first, no code-first. |

### 1.3 Dashboard web — React + TypeScript + Vite

SPA pura: TanStack Query + shadcn/ui + **ECharts** (mejor que Recharts para volumen y mapas).

**Sin Next.js.** Es un panel interno: no hay SEO ni necesidad de SSR, y agrega complejidad de despliegue
sin beneficio.

### 1.4 Infraestructura

Mini PC (Intel N100 o similar, 16 GB RAM, SSD NVMe) · Ubuntu Server LTS · **Docker Compose**:
`postgres`, `redis`, `api`, `worker`, `caddy` (TLS automático), `backup`.

**Conectividad — decisión crítica.** Un servidor en la oficina debe ser alcanzable desde la calle.
Recomendación: **Cloudflare Tunnel** (gratis) — sin IP fija, sin abrir puertos, TLS y protección DDoS
incluidos.

**Asume que tú eres el SRE:**

- **UPS / no-break obligatorio.** Un apagón con 8 rutas sincronizando corrompe la BD.
- **Respaldo desde el día 1:** `pg_dump` diario + WAL archiving, empujado con `restic` a Backblaze B2 o S3.
- **Simulacro de restauración.** Un respaldo que nunca restauraste no es un respaldo.
- **Failover 4G** en el router de la oficina.

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
| **0** | **Fundaciones.** Modelo de dominio y glosario escritos (unidades, cajas vs piezas, listas de precios, esquemas de crédito, reglas de descuento). Monorepo `/server /mobile /web /packages/contracts` con OpenAPI como contrato y clientes generados. Docker Compose, migraciones, CI, seeds. Auth + RBAC + registro de dispositivos. **Respaldos funcionando desde el día 1.** | 2–3 sem |
| **1** | **Catálogos y núcleo.** Productos, unidades y conversiones, listas de precios, clientes, rutas, almacenes, usuarios. CRUD + panel web básico. | 2–3 sem |
| **2** | **Motor de sincronización. La fase más importante.** Outbox/inbox, cursores, idempotencia, cuarentena, lotes. Se construye contra una entidad de juguete y se demuestra con **pruebas de caos**: entrega duplicada, lote parcial, orden invertido, corte a media transacción, restauración de respaldo del dispositivo. Incluye pantalla de *inspector de sync* en la app. | 3–4 sem |
| **3** | **App vendedor MVP.** Catálogo offline, carrito, venta de contado, impresión Bluetooth, cola de sync. **Piloto con UN vendedor en UNA ruta durante 2 semanas, con el proceso de papel en paralelo.** | 3–4 sem |
| **4** | **Inventario de camión.** Carga/embarque, existencias offline, consulta online de bodega principal (con estado explícito "requiere conexión"), liquidación/cierre con cálculo de diferencias. | 2–3 sem |
| **5** | **Crédito y cobranza.** Venta a crédito, saldos, abonos en efectivo, corte de caja y arqueo. | 2–3 sem |
| **6** | **Operaciones secundarias.** Alta de clientes en calle con GPS + ajuste manual, mermas/devoluciones, no-drops con **catálogo de motivos cerrado** (texto libre = datos inanalizables). | 2 sem |
| **7** | **Perfil Gerencia móvil.** Dashboard sobre modelos de lectura precalculados, nunca sobre tablas transaccionales. Venta del día, avance por ruta vs objetivo, cobranza, no-drops, mapa. Cada tarjeta con su marca de última sincronización. | 2 sem |
| **8** | **Analítica web.** Esquema estrella (`fact_ventas`, `dim_cliente/producto/ruta/tiempo`) en el mismo PostgreSQL, alimentado por jobs. Drop size, frecuencia de visita, productividad por vendedor, rotación, clientes en riesgo de abandono, efectividad de visita. | 3–4 sem |
| **9** | **Endurecimiento.** Logs estructurados, Sentry, métricas, simulacro de restauración, política de días máximos sin sincronizar, borrado remoto del dispositivo, MDM, RLS. | 2–3 sem |
| **10** | **Opcionales.** Compras/recepción, integración contable, CFDI. | — |

> **Sin data warehouse.** A volumen de miles de tickets diarios, PostgreSQL con vistas materializadas
> alcanza por años. ClickHouse/BigQuery serían complejidad sin retorno.

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

## 4. Decisiones abiertas

- [ ] ¿Remisión no fiscal es suficiente, o se requiere CFDI desde v1?
- [ ] Unidades de venta: ¿pieza, caja, y algún producto a granel (kg)? Define las conversiones antes de la Fase 1.
- [ ] Esquemas de crédito: ¿plazos fijos, límite por cliente, bloqueo automático por mora?
- [ ] ¿Preventa (toma de pedido) además de venta en camión, o solo autoventa?
- [ ] Modelo de dispositivos: ¿teléfono propio del vendedor (BYOD) o equipo de la empresa con MDM?
