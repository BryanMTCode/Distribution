# Sistema DSD — Distribuidora de abarrotes

Ecosistema de *Direct Store Delivery*: servidor local en oficina, app móvil multi-rol con operación
offline y panel web analítico.

## Estado

**Fase 1 — Catálogos y núcleo, en curso.** Fase 0 completa. Reglas de negocio definidas
([ADR 0002](docs/adr/0002-reglas-de-negocio.md)): autoventa, pieza y caja, crédito con límite en dinero
y bloqueo automático, remisión no fiscal, equipos de la empresa.

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
| Contrato canónico Dart↔Python (34 vectores) | ✅ lado Python verificado |
| Contrato Argon2id (7 vectores) | ✅ lado Python verificado |
| OpenAPI 3.1 + degradado a 3.0 para Dart | ✅ generado en CI |
| Implementación Dart del formato canónico | ⚠️ escrita, **sin ejecutar** (falta SDK) |
| Panel de operación (Jinja2 + HTMX) | ⛔ resto de la Fase 1 |
| Motor de sincronización | ⛔ Fase 2 |

**205 pruebas en verde** sobre PostgreSQL 16.13 + PostGIS.

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
  db/schema.sql      esquema local del dispositivo
  lib/dsd/           implementación Dart del formato canónico
  test/              la mitad Dart de los contratos
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
make pruebas                        # 205 pruebas
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
