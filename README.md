# Sistema DSD — Distribuidora de abarrotes

Ecosistema de *Direct Store Delivery*: servidor local en oficina, app móvil multi-rol con operación
offline y panel web analítico.

## Estado

**Fases 0 a 7 hechas — el día completo del vendedor, su cierre y su lectura.** Sale el camión cargado,
se vende y se cobra offline, se registra lo que se perdió y a quién no se le vendió, la liquidación
cuadra contra la ecuación, y la oficina puede leer lo que pasó: cobros marcados, cartera por antigüedad
y efectividad de visita por causa. Reglas de negocio
cerradas ([ADR 0002](docs/adr/0002-reglas-de-negocio.md)): autoventa, pieza y caja, crédito con límite
en dinero y bloqueo automático, remisión no fiscal, equipos de la empresa, sin lotes.

| Pieza | Estado |
|---|---|
| Migraciones PostgreSQL + PostGIS (0001–0020) | ✅ aplican vía Alembic |
| **La venta offline** — folio, inventario, cola y ticket en una transacción | ✅ 20 pruebas de atomicidad |
| Ingesta de la venta: **marca, nunca rechaza** (§0.1) | ✅ 19 pruebas |
| Borrador del carrito (sobrevive a que Android mate la app) | ✅ 9 pruebas |
| Rangos de folio locales: ni hueco ni duplicado | ✅ con prueba de rollback |
| Esquema SQLite del dispositivo | ✅ aplica en SQLite 3.45 |
| Invariantes del diseño (6) | ✅ verificadas |
| Auth + RBAC + credencial offline | ✅ con pruebas |
| Registro de dispositivos y rangos de folio | ✅ con pruebas |
| Cola de trabajos (`FOR UPDATE SKIP LOCKED`) | ✅ con pruebas de concurrencia |
| Datos de referencia (roles, permisos, unidades, motivos) | ✅ sembrados por migración |
| Reglas de crédito con bloqueo offline | ✅ con pruebas de propiedades |
| API de catálogo (productos, presentaciones, precios) | ✅ con pruebas |
| API de clientes (alta en campo, alcance por ruta, cartera) | ✅ con pruebas |
| **Motor de sincronización** — sobres, idempotencia, cuarentena | ✅ con pruebas de caos |
| Cursor de deltas con filtro de snapshot | ✅ verificado contra transacción en vuelo |
| change_log poblado por trigger | ✅ nada puede escribir sin dejar rastro |
| **Alta de cliente en la calle** con GPS y ajuste manual | ✅ 14 pruebas de widget |
| Aviso de posible duplicado antes de crearlo | ✅ por distancia, no por nombre |
| **Cliente de sincronización** del dispositivo | ✅ 22 pruebas de la tabla de decisiones |
| Aplicador de deltas al espejo local | ✅ contra el payload real del servidor |
| Delta de cartera (el saldo que faltaba) | ✅ migración 0011 |
| **Login sin señal** (Argon2id verificado en Dart) | ✅ con pruebas de widget |
| **Lista de clientes offline** con crédito compuesto | ✅ con pruebas de widget |
| Modo demo para evaluar la UI en campo sin servidor | ✅ imposible en release (candado de compilación) |
| **Precio rígido en tres capas** (dominio · servidor · CHECK de PostgreSQL) | ✅ [ADR 0002 §7](docs/adr/0002-reglas-de-negocio.md) |
| Dinero de 2 decimales y **precio de 4** (caja↔pieza sin descuadre) | ✅ 5º contrato, 13 casos |
| **Catálogo de la visita** con precio de la lista del cliente | ✅ 18 pruebas de widget |
| **Carrito** con existencia del camión y crédito compuesto | ✅ 18 pruebas de widget + 25 de dominio |
| Cobro y remisión (impresión **a un toque**, no automática) | ✅ 15 pruebas de widget |
| **Ticket ESC/POS de 58 mm** — diseño, acentos, emoji, reimpresión | ✅ 59 pruebas de bytes |
| Vista previa del ticket, en el teléfono y versionada | ✅ [ver el papel](contracts/ticket_58mm_ejemplo.txt) |
| Impresora simulada con sus caminos de falla | ✅ 13 pruebas de widget |
| **Lienzo espacial offline** (radar de clientes, sin mosaicos) | ✅ 9 pruebas de widget |
| Portal por rol (vendedor / gerencia) | ✅ con pruebas |
| Contrato de Argon2id (7 vectores) | ✅ **verificado en los dos lenguajes** |
| **Outbox del dispositivo** — documento y cola en una transacción | ✅ con pruebas |
| Reglas de crédito en Dart (espejo del servidor) | ✅ con pruebas |
| Dinero exacto en el dispositivo (centavos enteros) | ✅ con pruebas |
| Rangos de folio locales | ✅ con pruebas |
| Contrato canónico Dart↔Python (34 vectores) | ✅ **verificado en los dos lenguajes** |

| OpenAPI 3.1 + degradado a 3.0 para Dart | ✅ generado en CI |
| Sobres de Dart aceptados por el servidor real | ✅ prueba de contrato de punta a punta |
| **Panel de operación** — sesión con cookie, CSRF, tablero | ✅ 17 pruebas |
| Pantalla de **cuarentena**: lo que el servidor rechazó | ✅ con payload íntegro |
| Pantalla de **ventas marcadas**, con el motivo en español | ✅ la otra mitad de §0.1 |
| **Captura de catálogo y precios** con cuatro decimales | ✅ 20 pruebas |
| **Confirmar prospectos de calle**: código, lista y crédito | ✅ 16 pruebas |
| **Carga del camión** — bodega → camión, con su delta y su detalle | ✅ 20 pruebas |
| El teléfono aplica la carga sin revivir lo ya vendido | ✅ 12 pruebas de Dart |
| **Usuarios, rutas, almacenes y listas** desde el panel | ✅ 23 pruebas |
| Arranque del primer usuario (`make usuario`) — **sin contraseña por omisión** | ✅ con pruebas |
| **Liquidación y retorno** — la ecuación que atrapa descuadres | ✅ 25 pruebas |
| **Inventario y libro mayor** por almacén, con su saldo corriente | ✅ 13 pruebas |
| Transmisión Bluetooth (solo el socket: los bytes ya están) | ⛔ espera la impresora física |
| **Cobranza en la app** — abono, recibo impreso y FIFO en el servidor | ✅ 63 pruebas |
| **Mermas y devoluciones** — el signo que evita que el faltante sea del vendedor | ✅ 44 pruebas + 32 de ingesta |
| **No-drops con geosello** — la única excepción a «marcar, no rechazar» | ✅ 19 pruebas de widget |
| Los catálogos de motivos llegan al teléfono (y se pueden desactivar) | ✅ en el contrato de deltas |
| **Cobranza en el panel** — arqueo del día, cobros marcados y antigüedad | ✅ 25 pruebas |
| **El teléfono reporta su cola**, y `sync_completa` deja de ser una casilla | ✅ 8 + 6 pruebas |
| **Efectividad de visita** — cuántas visitas perdidas podemos arreglar nosotros | ✅ 20 pruebas |
| **Esquema estrella** (`fact_ventas`, `fact_visitas`, `dim_*`) con refresco por job | ✅ 27 pruebas |
| **Laboratorio analítico (Streamlit)** — drop size, rotación, clientes en riesgo | ✅ 8 pruebas con `AppTest` |
| **Tablero de Gerencia en el teléfono** — seis cifras, cada una con su antigüedad | ✅ 26 pruebas de widget |
| Modelos de lectura del tablero, recalculados al sincronizar (no al abrir la pantalla) | ✅ 25 + 19 pruebas |
| **Objetivos mensuales por ruta** desde el panel, y el avance contra lo esperado | ✅ 15 pruebas |
| **Login en línea de Gerencia** — su teléfono NO guarda credencial offline | ✅ 18 pruebas de Dart |
| **Logs estructurados** con `peticion_id`, y lo que NUNCA entra en uno | ✅ 28 pruebas, con las tres |
| **Métricas Prometheus** de salud — apagadas por omisión, y sin dinero dentro | ✅ incluidas en esas 28 |
| **Sentry opcional** con filtro de salida propio, probado como función pura | ✅ sin payload, sin locales |
| **Seguridad por renglón (RLS)** con rol restringido — la segunda cerradura | ✅ 25 pruebas contra `dsd_api` |
| **Borrado remoto** — entrega primero, borra después, y lo confirma | ✅ 25 del servidor + 16 de Dart |
| **Panel de teléfonos** — rezago, accesos por caducar, suspender y borrar | ✅ en esas 25 |
| **Entradas de mercancía** — compra, inventario inicial y ajuste, con documento | ✅ 32 pruebas |
| **Salidas de bodega** — conteo físico y merma, y nunca dejan negativo | ✅ 37 pruebas |
| **Compras** — proveedores, **costo promedio ponderado** y cuentas por pagar | ✅ 35 pruebas |
| **§2.3: no se carga con operaciones pendientes** — forzable, con constancia | ✅ 11 pruebas |
| **Respaldo y simulacro de restauración** que verifica las invariantes | ✅ `make simulacro` |
| **Despliegue de un comando** — `docker compose up -d` crea los roles de RLS en orden | ✅ 11 pruebas |
| **APK de producción firmado** — el build se detiene sin keystore, y revisa el APK | ✅ 7 + 5 de widget |
| **Dependencias con candado** — `uv.lock` versionado, los tres caminos lo usan | ✅ 9 pruebas |
| **Cifrado del disco del servidor** — procedimiento y `make cifrado-revisar` | ⏳ se hace al instalar la mini PC |
| **Instrumento del piloto** — cuadre diario contra el papel, bitácora y 12 criterios | ✅ 71 pruebas |
| **El piloto de campo en sí** — dos semanas de un vendedor real | ⏳ calendario, no código |

**973 pruebas de Python** sobre PostgreSQL 16.13 + PostGIS, **475 de Dart** y **225 de widget**, todas en verde.

## Stack

| Capa | Tecnología |
|---|---|
| App móvil | Flutter · Drift (SQLite) · SQLCipher |
| Backend | Python 3.12+ · FastAPI · SQLAlchemy 2.0 · Pydantic v2 · psycopg 3 |
| Base de datos | PostgreSQL 17 + PostGIS |
| Panel de operación | FastAPI + Jinja2, HTML del servidor, **sin CDN** |
| Laboratorio analítico | Streamlit + Pandas/Polars (solo lectura) |
| Cola de trabajos | PostgreSQL (`FOR UPDATE SKIP LOCKED`) |
| Infraestructura | Docker Compose · Caddy · Cloudflare Tunnel |

Razonamiento y alternativas descartadas en el [ADR 0001](docs/adr/0001-stack-tecnologico.md).

## Documentación

- [`docs/ARRANQUE-DIARIO.md`](docs/ARRANQUE-DIARIO.md) — **manual operativo**: levantar todo desde cero
  tras reiniciar la PC, puertos ocupados, procesos fantasma, el puente USB al teléfono, los flujos a
  validar y el avance del proyecto. El atajo es `make db && make doctor`.
- [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) — propuesta arquitectónica: stack, arquitectura de
  datos y plan de desarrollo por fases.
- [`docs/MODELO-DATOS.md`](docs/MODELO-DATOS.md) — DDL, protocolo de sincronización e invariantes
  verificadas.
- [`docs/RESPALDOS.md`](docs/RESPALDOS.md) — respaldar, el **simulacro de restauración**, sacar la copia
  del edificio y cómo restaurar de verdad el día que haga falta. *Un respaldo que nunca restauraste no
  es un respaldo.*
- [`docs/PILOTO.md`](docs/PILOTO.md) — el **protocolo del piloto de campo**: la lista del día −1, la
  rutina diaria de la oficina, los doce criterios de salida, cuándo abortar antes de las dos semanas y
  la junta del día 15. *El papel en paralelo no es un respaldo: es el patrón de medida.*
- [`docs/SEGURIDAD-OPERATIVA.md`](docs/SEGURIDAD-OPERATIVA.md) — qué hacer cuando un teléfono se pierde
  o un vendedor se va, el borrado remoto paso a paso, los tres roles de PostgreSQL, rotación de
  secretos y qué resuelve MDM (y qué no).
- [`docs/adr/`](docs/adr/) — decisiones de arquitectura registradas.

## Estructura

```
server/
  app/
    api/admin/       PANEL DE OPERACIÓN: sesión con cookie, CSRF, plantillas
                     comun.py         navegación y lectura de números escritos a mano
                     productos.py     catálogo y precios (los 4 decimales)
                     clientes.py      confirmar prospectos y decidir el crédito
                     cargas.py        la carga del camión (bodega → camión)
                     equipo.py        usuarios, rutas, almacenes y listas de precios
                     inventario.py    existencias y libro mayor por almacén
                     liquidaciones.py el cierre del día (Fase 7)
                     cobranza.py      arqueo del día, cobros marcados y antigüedad
                     efectividad.py   visitas, no-drops por categoría y mermas por motivo
                     objetivos.py     la meta mensual de cada ruta (sin ella el tablero no compara)
                     equipos.py       los teléfonos: rezago, suspensión y borrado remoto
    core/            config, seguridad (Argon2id + JWT), sesión de BD
                     registro.py      logs estructurados y el peticion_id
                     metricas.py      /metrics en formato Prometheus, sin dinero
                     observabilidad.py Sentry opcional, con filtro de salida
    domain/          REGLAS PURAS — sin imports de framework
                     canonico.py      formato canónico y hash del payload
                     liquidacion.py   la ecuación del cierre del día
                     analitica.py     las métricas del laboratorio, escritas UNA vez
                     tablero.py       las cifras del tablero de Gerencia, con su frescura
                     importes.py      la aritmética de una partida (un solo redondeo)
                     identificadores.py  UUIDv7
    infra/models/    SQLAlchemy 2.0 sobre el esquema del SQL
    api/v1/          auth, dispositivos, salud, tablero
    workers/         cola sobre PostgreSQL + proceso worker
  db/
    migrations/      SQL: la FUENTE DE VERDAD del esquema
    alembic/         aplica ese SQL, no lo genera
    ops/             scripts operativos (rol de la API sin BYPASSRLS, rol analítico)
    tests/           prueba de humo de las invariantes
  tests/             suite de pytest
mobile/
  db/schema.sql      esquema local del dispositivo — FUENTE DE VERDAD
  packages/dsd_core/ NÚCLEO OFFLINE en Dart puro (sin Flutter):
    lib/src/         canónico, dinero exacto, precio de 4 decimales, carrito,
                     borrador, VENTA, COBRO, MERMA, NO-DROP, TICKET ESC/POS +
                     vista previa, crédito, credencial, folios, outbox, sobres,
                     ubicación, alta de clientes, sincronizador, aplicador de
                     deltas, esquema, TABLERO, login en línea
    test/            475 pruebas que corren en segundos
    tool/            genera los sobres de ejemplo y el esquema embebido
  app/               APP FLUTTER:
    lib/src/datos/   base local, almacén seguro, repositorios
    lib/src/estado/  sesión y providers
    lib/src/pantallas/ login, ruta, catálogo, carrito, venta, ticket, abono,
                     merma y devolución, no-drop, lienzo espacial,
                     gerencia/ (tablero, mapa del día)
    test/            225 pruebas de widget, sin emulador
analytics/           LABORATORIO ANALÍTICO (Streamlit, solo lectura)
                     app.py  dibuja; las DEFINICIONES viven en
                             server/app/domain/analitica.py
contracts/           vectores compartidos + OpenAPI
deploy/              Caddyfile
docs/                arquitectura, modelo de datos, ADRs, respaldos y seguridad operativa
```

## Desarrollo

**¿Windows 11?** Empieza por [`docs/ENTORNO-WINDOWS.md`](docs/ENTORNO-WINDOWS.md): instalación paso a
paso con WSL2 y cómo seguir el avance del proyecto.

```bash
make instalar                       # venv + EXACTAMENTE lo de server/uv.lock
make candado                        # regenera los candados — REVISA EL DIFF
make migrar DB=postgresql+psycopg://…/dsd
make usuario                        # el primer usuario de oficina — NO hay uno por omisión
make pruebas                        # 973 pruebas de Python
make movil                          # 475 de Dart + 225 de widget
make movil-ticket                   # regenera la vista previa del ticket — MÍRALA
make app                            # corre la app en un teléfono conectado
make app-demo                       # ídem, con datos sembrados y sin necesidad de servidor
make doctor                         # revisa el entorno y dice qué arreglar
make lint
make api                            # uvicorn con recarga — el panel en /panel
```

Producción: `cp .env.example .env`, rellenar, y `docker compose up -d`.

## Los contratos entre Dart y Python

Cinco cálculos se hacen en dos lenguajes —y uno de ellos también en PostgreSQL— y deben coincidir
byte a byte, o al centavo. Los vectores de `contracts/` los ejecutan **ambas suites** en CI: si una se
pone roja y la otra no, hay divergencia.

```bash
make contratos    # regenera y REVISA EL DIFF antes de commitear
```

Detalle en [`contracts/README.md`](contracts/README.md).

## Verificar el modelo

```bash
createdb dsd && psql -d dsd -c 'CREATE EXTENSION postgis;'
for f in server/db/migrations/0*.sql; do psql -d dsd -v ON_ERROR_STOP=1 -f "$f"; done
psql -d dsd -v ON_ERROR_STOP=1 -f server/db/tests/smoke_invariantes.sql
```

La prueba corre dentro de una transacción con `ROLLBACK`: no deja residuos.

## Los tres principios

1. **El mundo físico ya ocurrió.** El servidor nunca rechaza una venta offline por reglas de negocio;
   la acepta y la marca para revisión.
2. **El inventario del camión tiene un solo dueño.** Sin concurrencia no hay conflictos, y por eso el
   offline es seguro.
3. **"Tiempo real" es "tiempo real de lo sincronizado".** Cada métrica se muestra junto a la antigüedad
   de sus datos.
