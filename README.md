# Sistema DSD — Distribuidora de abarrotes

Ecosistema de *Direct Store Delivery*: servidor local en oficina, app móvil multi-rol con operación
offline y panel web analítico.

## Estado

**Fase 0 — Fundaciones.** Modelo de datos definido y verificado. Sin código de aplicación todavía.

## Documentación

- [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) — propuesta arquitectónica: stack, arquitectura de
  datos y plan de desarrollo por fases.
- [`docs/MODELO-DATOS.md`](docs/MODELO-DATOS.md) — DDL, protocolo de sincronización e invariantes
  verificadas.

## Estructura

```
server/db/migrations/   Migraciones PostgreSQL 16 + PostGIS (0001-0007)
server/db/tests/        Prueba de humo de las invariantes del diseño
mobile/db/schema.sql    Esquema local del dispositivo (SQLite + SQLCipher)
docs/                   Arquitectura y modelo de datos
```

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
