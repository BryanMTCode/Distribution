# ADR 0001 — Stack tecnológico

- **Fecha:** 2026-09-15
- **Estado:** Aceptada
- **Contexto:** Sistema DSD desarrollado por una sola persona, servidor local en oficina,
  app móvil offline-first, objetivo declarado de correr modelos estadísticos sobre la operación.

---

## Decisión

| Capa | Elección |
|---|---|
| App móvil | Flutter + Drift (SQLite) + SQLCipher |
| Backend | **Python 3.12+ · FastAPI · SQLAlchemy 2.0 + Pydantic v2 · psycopg 3** |
| Base de datos | PostgreSQL 17 + PostGIS |
| Migraciones | Alembic ejecutando SQL escrito a mano |
| Panel de operación (CRUD) | **FastAPI + Jinja2 + HTMX** |
| Laboratorio analítico | **Streamlit + Pandas/Polars, solo lectura** |
| Cola de trabajos | **PostgreSQL con `FOR UPDATE SKIP LOCKED`** |
| Infraestructura | Docker Compose, Caddy, Cloudflare Tunnel |

---

## Alternativas consideradas

### Node 22 + NestJS + React/Vite (propuesta inicial del arquitecto) — descartada

El argumento a favor era "un solo lenguaje para backend y web". **No sobrevive al contexto**: con Flutter
en el móvil, cualquier backend implica dos lenguajes. Dart + TypeScript y Dart + Python cuestan lo mismo
en carga cognitiva, y Python cubre además la analítica de la Fase 8 sin un tercer stack.

Tres ventajas concretas de Python que decidieron el cambio:

1. **`Decimal` nativo.** JavaScript no tiene tipo decimal; todo el dinero pasa por `float64` o
   librerías de terceros. En cobranza con saldos que se arrastran meses, eso son centavos perdidos.
2. **Hypothesis.** El *property-based testing* que necesitan las pruebas de caos del motor de
   sincronización no tiene equivalente serio en JavaScript.
3. **Un solo entorno para el análisis**, que era un objetivo declarado del negocio.

**Lo que se pierde y hay que compensar:** NestJS impone estructura modular; FastAPI no. La disciplina la
pone el desarrollador (ver estructura de `server/app/` en `ARQUITECTURA.md` §3.1). Y el empaquetado de
Python en producción es más frágil: se compensa con Docker y `uv` con lockfile.

### SQLModel — descartada

Fusiona el modelo de base de datos con el esquema de la API en una sola clase. Es exactamente lo que **no**
se quiere aquí: el contrato del dispositivo y la tabla deben estar desacoplados, porque el teléfono manda
una venta sin `folio_servidor`, `fecha_servidor` ni `requiere_revision` — los agrega el servidor. Con una
sola clase, todo queda opcional y la validación deja de validar.

Además, el esquema usa columnas generadas, triggers, `EXCLUDE USING gist`, índices parciales y PostGIS,
donde SQLModel estorba. **SQLAlchemy 2.0 + Pydantic v2 por separado**: dos capas porque son dos
responsabilidades.

### Streamlit como panel administrativo completo — descartada parcialmente

Streamlit **sí** entra al stack, pero solo como laboratorio analítico de solo lectura.

El panel administrativo no es un dashboard. De sus pantallas, solo una es análisis; el resto son
escrituras transaccionales críticas: resolver la cuarentena de sync, fusionar clientes duplicados,
confirmar cargas, cerrar liquidaciones, autorizar ajustes de inventario.

Cuatro razones para no poner esas escrituras en Streamlit:

1. **Modelo de re-ejecución.** Streamlit re-ejecuta el script completo en cada interacción y su estado
   (`st.session_state`) es frágil en flujos de varios pasos. Todo el sistema está construido alrededor de
   no duplicar documentos; meter las escrituras administrativas en un framework cuyo modelo de ejecución
   facilita disparar dos veces la misma acción es una contradicción arquitectónica.
2. **Sin modelo de usuarios.** No hay RBAC ni alcance por ruta, y el panel va expuesto a internet por el
   túnel.
3. **Pandas como motor de agregación** es órdenes de magnitud más lento que `GROUP BY` sobre vistas
   materializadas. Pandas es la última milla, no la capa de agregación.
4. **Sin URL por registro.** No se puede compartir un enlace a "la liquidación LQ-0847".

**Alternativa elegida: Jinja2 + HTMX**, que mantiene el 100% de Python, sin npm ni cadena de build, y
devuelve fragmentos de HTML desde los mismos endpoints de FastAPI con el mismo RBAC.

**Si en el futuro se revierte esta decisión** y se quiere Streamlit también para el CRUD, la condición no
negociable es que toda escritura pase por la API de FastAPI (nunca por SQLAlchemy directo desde
Streamlit) y que esos endpoints exijan `Idempotency-Key` igual que `/sync/push`.

### Redis + Celery/BullMQ para la cola — descartada en v1

`FOR UPDATE SKIP LOCKED` sobre PostgreSQL da una cola multi-consumidor sin duplicar trabajo, y **encolar
el job queda dentro de la misma transacción que la escritura de negocio** — garantía que un broker
externo no puede ofrecer. Una pieza móvil menos que respaldar, monitorear y reiniciar tras un apagón.

Siguiente escalón si hiciera falta: `arq` (asyncio nativo, mucho más ligero que Celery). Con 10 rutas no
se llega ahí.

---

## Consecuencias

**Positivas**

- Un solo lenguaje del ingest al modelo estadístico.
- `Decimal` de extremo a extremo en el servidor.
- Hypothesis disponible para las pruebas de caos de la Fase 2.
- Menos piezas de infraestructura (sin Redis, sin npm, sin cadena de build en el panel).
- **El modelo de datos ya verificado no cambió ni una línea**: las 7 migraciones, el esquema SQLite y las
  6 invariantes son agnósticas del lenguaje.

**Negativas y su mitigación**

| Consecuencia | Mitigación |
|---|---|
| FastAPI no impone estructura | Estructura de `server/app/` definida en Fase 0 y no renegociable; `domain/` sin imports de framework |
| Una llamada bloqueante congela el event loop | O todo el camino es async, o el endpoint se declara `def` y va al threadpool. Nunca mezclar |
| Empaquetado de Python más frágil | Docker + `uv` con lockfile versionado |
| Dos lenguajes ⇒ serializaciones que divergen en silencio | Vectores de prueba compartidos en `contracts/`, ejecutados por ambas suites en CI (ver `ARQUITECTURA.md` §1.6) |
| Alembic `autogenerate` no ve triggers ni `EXCLUDE` | Los `.sql` son la fuente de verdad; Alembic los aplica con `op.execute()`. Nunca dejar que el ORM sincronice el esquema |
| HTMX es menos conocido que React | Curva corta; a cambio, cero build step y cero código JavaScript propio que mantener |

---

## Pendiente de cerrar en la Fase 0

Los tres contratos entre Dart y Python (`ARQUITECTURA.md` §1.6), baratos ahora y carísimos después:

- [ ] Dinero como *string* en el JSON, en todos los esquemas Pydantic y modelos Dart.
- [ ] `contracts/canonical_vectors.json` — hash canónico del payload verificado por ambas suites.
- [ ] Parámetros de Argon2id fijados, con vectores compartidos.
