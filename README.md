# Sistema DSD — Distribuidora de abarrotes

Ecosistema de *Direct Store Delivery*: servidor local en oficina, app móvil multi-rol con operación
offline y panel web analítico.

## Estado

**Fase 3 — App del vendedor: login offline y lista de clientes funcionando.** Fases 0, 1 y 2 hechas. Reglas de negocio
cerradas ([ADR 0002](docs/adr/0002-reglas-de-negocio.md)): autoventa, pieza y caja, crédito con límite
en dinero y bloqueo automático, remisión no fiscal, equipos de la empresa, sin lotes.

| Pieza | Estado |
|---|---|
| Migraciones PostgreSQL + PostGIS (0001–0008) | ✅ aplican vía Alembic |
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
| **Login sin señal** (Argon2id verificado en Dart) | ✅ con pruebas de widget |
| **Lista de clientes offline** con crédito compuesto | ✅ con pruebas de widget |
| Portal por rol (vendedor / gerencia) | ✅ con pruebas |
| Contrato de Argon2id (7 vectores) | ✅ **verificado en los dos lenguajes** |
| **Outbox del dispositivo** — documento y cola en una transacción | ✅ con pruebas |
| Reglas de crédito en Dart (espejo del servidor) | ✅ con pruebas |
| Dinero exacto en el dispositivo (centavos enteros) | ✅ con pruebas |
| Rangos de folio locales | ✅ con pruebas |
| Contrato canónico Dart↔Python (34 vectores) | ✅ **verificado en los dos lenguajes** |

| OpenAPI 3.1 + degradado a 3.0 para Dart | ✅ generado en CI |
| Sobres de Dart aceptados por el servidor real | ✅ prueba de contrato de punta a punta |
| Panel de operación (Jinja2 + HTMX) | ⛔ resto de la Fase 1 |
| Carrito, venta e impresión Bluetooth | ⛔ resto de la Fase 3 |

**258 pruebas de Python** sobre PostgreSQL 16.13 + PostGIS, **138 de Dart** y **20 de widget**, todas en verde.

## Stack

| Capa | Tecnología |
|---|---|
| App móvil | Flutter · Drift (SQLite) · SQLCipher |
| Backend | Python 3.12+ · FastAPI · SQLAlchemy 2.0 · Pydantic v2 · psycopg 3 |
| Base de datos | PostgreSQL 17 + PostGIS |
| Panel de operación | FastAPI + Jinja2 + HTMX |
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
    core/            config, seguridad (Argon2id + JWT), sesión de BD
    domain/          REGLAS PURAS — sin imports de framework
                     canonico.py      formato canónico y hash del payload
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
    lib/src/         canónico, dinero exacto, crédito, credencial, folios,
                     outbox, sobres, esquema embebido
    test/            138 pruebas que corren en segundos
    tool/            genera los sobres de ejemplo y el esquema embebido
  app/               APP FLUTTER:
    lib/src/datos/   base local, almacén seguro, repositorios
    lib/src/estado/  sesión y providers
    lib/src/pantallas/ login, ruta del vendedor, panel de gerencia
    test/            20 pruebas de widget, sin emulador
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
make pruebas                        # 258 pruebas de Python
make movil                          # 138 de Dart + 20 de widget
make app                            # corre la app en un teléfono conectado
make lint
make api                            # uvicorn con recarga
```

Producción: `cp .env.example .env`, rellenar, y `docker compose up -d`.

## Los contratos entre Dart y Python

Tres serializaciones se calculan en dos lenguajes y deben coincidir byte a byte. Los vectores de
`contracts/` los ejecutan **ambas suites** en CI: si una se pone roja y la otra no, hay divergencia.

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
