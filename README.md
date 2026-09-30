# Sistema DSD — Distribuidora de abarrotes

Ecosistema de *Direct Store Delivery*: servidor local en oficina, app móvil multi-rol con operación
offline y panel web analítico.

## Estado

**Fase 3 cerrada — el ciclo completo del vendedor funciona offline.** Fases 0, 1 y 2 hechas. Reglas de negocio
cerradas ([ADR 0002](docs/adr/0002-reglas-de-negocio.md)): autoventa, pieza y caja, crédito con límite
en dinero y bloqueo automático, remisión no fiscal, equipos de la empresa, sin lotes.

| Pieza | Estado |
|---|---|
| Migraciones PostgreSQL + PostGIS (0001–0013) | ✅ aplican vía Alembic |
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
| Panel de usuarios, rutas, almacenes y listas de precios | ⛔ lo que falta de la Fase 1 |
| Liquidación y retorno al cierre del día | ⛔ Fase 7 |
| Transmisión Bluetooth (solo el socket: los bytes ya están) | ⛔ espera la impresora física |
| Cobranza, mermas y no-drops | ⛔ Fases 5 y 6 |

**412 pruebas de Python** sobre PostgreSQL 16.13 + PostGIS, **382 de Dart** y **125 de widget**, todas en verde.

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

- [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) — propuesta arquitectónica: stack, arquitectura de
  datos y plan de desarrollo por fases.
- [`docs/MODELO-DATOS.md`](docs/MODELO-DATOS.md) — DDL, protocolo de sincronización e invariantes
  verificadas.
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
    core/            config, seguridad (Argon2id + JWT), sesión de BD
    domain/          REGLAS PURAS — sin imports de framework
                     canonico.py      formato canónico y hash del payload
                     importes.py      la aritmética de una partida (un solo redondeo)
                     identificadores.py  UUIDv7
    infra/models/    SQLAlchemy 2.0 sobre el esquema del SQL
    api/v1/          auth, dispositivos, salud
    workers/         cola sobre PostgreSQL + proceso worker
  db/
    migrations/      SQL: la FUENTE DE VERDAD del esquema
    alembic/         aplica ese SQL, no lo genera
    ops/             scripts operativos (rol de solo lectura)
    tests/           prueba de humo de las invariantes
  tests/             suite de pytest
mobile/
  db/schema.sql      esquema local del dispositivo — FUENTE DE VERDAD
  packages/dsd_core/ NÚCLEO OFFLINE en Dart puro (sin Flutter):
    lib/src/         canónico, dinero exacto, precio de 4 decimales, carrito,
                     borrador, VENTA, TICKET ESC/POS + vista previa, crédito,
                     credencial, folios, outbox, sobres, ubicación, alta de
                     clientes, sincronizador, aplicador de deltas, esquema
    test/            367 pruebas que corren en segundos
    tool/            genera los sobres de ejemplo y el esquema embebido
  app/               APP FLUTTER:
    lib/src/datos/   base local, almacén seguro, repositorios
    lib/src/estado/  sesión y providers
    lib/src/pantallas/ login, ruta, catálogo, carrito, venta, ticket,
                     lienzo espacial, gerencia
    test/            125 pruebas de widget, sin emulador
analytics/           Streamlit (solo lectura)
contracts/           vectores compartidos + OpenAPI
deploy/              Caddyfile
docs/                arquitectura, modelo de datos y ADRs
```

## Desarrollo

**¿Windows 11?** Empieza por [`docs/ENTORNO-WINDOWS.md`](docs/ENTORNO-WINDOWS.md): instalación paso a
paso con WSL2 y cómo seguir el avance del proyecto.

```bash
make instalar                       # venv + dependencias (uv, Python 3.12)
make migrar DB=postgresql+psycopg://…/dsd
make pruebas                        # 412 pruebas de Python
make movil                          # 382 de Dart + 125 de widget
make movil-ticket                   # regenera la vista previa del ticket — MÍRALA
make app                            # corre la app en un teléfono conectado
make app-demo                       # ídem, con datos sembrados y sin necesidad de servidor
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
