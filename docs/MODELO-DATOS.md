# Modelo de datos — Fase 0

Complementa `ARQUITECTURA.md`. Aquí está el *cómo*: DDL, protocolo de sincronización y las pruebas que
demuestran que el modelo cumple lo que promete.

> **Independiente del stack.** Todo este documento es SQL y protocolo: no cambió con la decisión de
> backend del [ADR 0001](adr/0001-stack-tecnologico.md). La implementación en Python/SQLAlchemy del
> ingest (savepoints por sobre, `on_conflict_do_nothing`, advisory locks) está en
> `ARQUITECTURA.md` §3.1.

| Archivo | Contenido |
|---|---|
| `server/db/migrations/0001_extensiones_identidad.sql` | Extensiones, sucursales, RBAC, usuarios, dispositivos, sesiones, auditoría |
| `server/db/migrations/0002_catalogo.sql` | Productos, unidades y conversiones, listas de precios, promociones |
| `server/db/migrations/0003_clientes_rutas.sql` | Rutas, clientes, georreferencia, cola de duplicados, frecuencia de visita |
| `server/db/migrations/0004_inventario.sql` | Almacenes, libro mayor, existencias, cargas, liquidaciones, traspasos |
| `server/db/migrations/0005_ventas_cobranza.sql` | Ventas, partidas, cancelaciones, cartera, cobros y su aplicación |
| `server/db/migrations/0006_operaciones.sql` | Mermas, devoluciones, no-drops |
| `server/db/migrations/0007_sync.sql` | Lotes, idempotencia, cuarentena, `change_log`, rangos de folio, salud de sync |
| `server/db/migrations/0008_jobs.sql` | Cola de trabajos (`FOR UPDATE SKIP LOCKED`) |
| `server/db/ops/rol_analitico.sql` | Rol de PostgreSQL de solo lectura para Streamlit |
| `mobile/db/schema.sql` | Esquema SQLite/SQLCipher del dispositivo |
| `server/db/tests/smoke_invariantes.sql` | Prueba de las 6 invariantes del diseño |

**Estado de verificación:** las 8 migraciones aplican sin error sobre PostgreSQL 16 + PostGIS 3 (vía
Alembic), y las 6 invariantes pasan. El esquema del dispositivo aplica sobre SQLite 3.45 (18 tablas,
1 vista, 8 índices).

```bash
createdb dsd && psql -d dsd -c 'CREATE EXTENSION postgis;'
make migrar DB=postgresql+psycopg://…/dsd
psql -d dsd -v ON_ERROR_STOP=1 -f server/db/tests/smoke_invariantes.sql   # corre en una tx con ROLLBACK
```

> **Alembic aplica el SQL, no lo genera.** `target_metadata` es `None` a propósito: si alguien corriera
> `alembic revision --autogenerate`, obtendría una migración proponiendo borrar el trigger de
> inmutabilidad del libro mayor, que `autogenerate` no sabe ver. La revisión de línea base ejecuta cada
> archivo con el cursor crudo de psycopg — ni `op.execute()` (lee `:nombre` como parámetro, y hay JSON
> de ejemplo en los comentarios) ni `exec_driver_sql()` (interpola `%`, y el trigger usa `%` como
> marcador de formato de PL/pgSQL).

---

## 1. Convenciones

| Regla | Valor |
|---|---|
| Cantidades | `numeric(14,3)` — admite fracción de caja y granel |
| Precios | `numeric(14,4)` — precisión de lista antes de redondeo |
| Importes | `numeric(14,2)` — dinero |
| Inventario | **siempre en unidad base** del producto. La conversión caja→pieza se resuelve al capturar, nunca en el libro mayor. Ésta es la regla que evita el descuadre caja/pieza. |
| Relojes | `fecha_dispositivo` (forense) + `fecha_servidor` (autoridad) + `fecha_operativa` (el "día" del vendedor, que no coincide con el día natural) |
| Llaves | Documentos de campo: **UUID v7 generado en el teléfono**, sin `DEFAULT` en la tabla. Si el servidor generara la llave, el reenvío duplicaría el ticket. |

---

## 2. Quién es dueño de cada dato

La regla que elimina la necesidad de resolver conflictos: **nadie edita lo mismo desde dos lados.**

| Entidad | Dueño | Dirección | Conflictos posibles |
|---|---|---|---|
| Productos, unidades, precios, promociones | Servidor | → dispositivo | Ninguno (el servidor gana siempre) |
| Condiciones de crédito, lista del cliente, bloqueo | Servidor | → dispositivo | Ninguno |
| Ventas, partidas, cobros, mermas, no-drops | Dispositivo | → servidor | Ninguno (append-only) |
| Clientes nuevos de campo | Dispositivo | → servidor | Duplicados → cola humana, nunca fusión automática |
| Saldos de cartera | Servidor | → dispositivo (caché) | Ninguno; el teléfono nunca calcula el saldo definitivo |
| Existencias del camión | Dispositivo (dueño exclusivo) | → servidor | Ninguno: un solo actor por almacén |

Los clientes son el único caso mixto: el dispositivo crea el registro, el servidor es dueño de los campos
comerciales. Como las zonas de campos no se traslapan, tampoco hay conflicto.

---

## 3. Anatomía de una venta offline

```
 TELÉFONO (sin señal)                          SERVIDOR (cuando vuelve el internet)
 ─────────────────────                         ────────────────────────────────────
 1. venta_id = uuidv7()
 2. folio_consecutivo = 124        ← del rango asignado por el servidor (1..1000)
    folio_local = 'VEND01-000124'  ← esto se IMPRIME en el papel
 3. BEGIN (una sola transacción SQLite)
      INSERT ventas
      INSERT venta_partidas
      UPDATE existencias_camion    ← decremento local, dueño exclusivo
      INSERT outbox                ← MISMA transacción: sin estados intermedios
    COMMIT
 4. Imprime ticket por Bluetooth   ← el ESC/POS se guarda para reimprimir igual
 5. El worker drena el outbox en orden FIFO
         │
         └── POST /sync/push ──────────► 6. ¿operacion_id ya en sync_operaciones?
                                              SÍ  → devuelve el resultado guardado
                                                    (duplicada) y no reprocesa
                                              NO  → continúa
                                           7. INSERT ventas ... ON CONFLICT (id)
                                                  DO NOTHING
                                           8. Asigna folio_servidor (secuencia global)
                                           9. INSERT movimientos_inventario (append-only)
                                              UPDATE existencias (misma transacción)
                                          10. Revalida reglas:
                                              precio distinto / excede crédito /
                                              fuera de geocerca
                                                 ⇒ requiere_revision = true
                                                 ⇒ NUNCA rechaza (§0.1)
                                          11. INSERT sync_operaciones (idempotencia)
         ◄──── 200 { aceptada } ─────────┘
 12. outbox.estado = 'confirmada'
```

**El paso 6 es la garantía contra tickets duplicados.** No depende de que la red se porte bien.

### Por qué dos folios

`folio_local` se imprime y vive en el espacio de nombres del dispositivo:
`UNIQUE(dispositivo_id, folio_consecutivo)`. `folio_servidor` es el consecutivo global.

Si el teléfono generara el consecutivo global, dos vendedores offline emitirían el mismo número el mismo
día. Y si el consecutivo se asignara solo al sincronizar, el papel que ya tiene el cliente no tendría
folio. Por eso son dos, y por eso el servidor reparte **rangos** (`folios_rangos`, con un constraint
`EXCLUDE` que impide traslapes): al reinstalar la app, el equipo recibe un rango nuevo y jamás repite un
folio ya impreso.

---

## 4. Inventario: por qué no se descuadra

```
 BODEGA_PRINCIPAL ──carga──► CAMION_01 ──venta──► (sale del sistema)
        ▲                        │
        └──────retorno───────────┘

 movimientos_inventario  = libro mayor inmutable (trigger bloquea UPDATE/DELETE)
 existencias             = caché transaccional, misma transacción, job nocturno reconcilia
```

- El almacén `CAMION_01` tiene `responsable_id` obligatorio: **un solo dueño, cero concurrencia**.
- La oficina no hace `UPDATE` sobre el stock de un camión. Propone un `traspaso` que el vendedor acepta
  en la app.
- **Una venta offline nunca toca `BODEGA_PRINCIPAL`.** La bodega solo se mueve por carga y retorno,
  procesados por el servidor.
- `existencias` **admite negativos a propósito** (no hay `CHECK (cantidad >= 0)`). Una venta que llega
  tarde cuando el camión ya marcaba cero no se rechaza: la mercancía ya salió. El negativo queda visible
  en un índice parcial de alertas y se cobra en la liquidación.
- El descuadre se atrapa aritméticamente en `liquidacion_detalle.diferencia`, una columna generada:

  ```
  retornado − (cargado − vendido − merma + devuelto) = diferencia
  ```

---

## 5. El cursor del `pull`: por qué no es `updated_at`

Un cursor por *timestamp* **pierde registros en silencio**. Una transacción que empezó antes puede hacer
`COMMIT` después de que el dispositivo ya avanzó su marca de agua: ese registro nunca se entrega y nadie
se entera.

`change_log` usa `BIGSERIAL` y guarda además el `xid8` de la transacción. El endpoint de `pull` solo
entrega filas con `xid < pg_snapshot_xmin(pg_current_snapshot())`, es decir, solo lo que ya está
confirmado para todos. Así se evitan también los huecos por transacciones en vuelo.

---

## 6. Invariantes verificadas

`server/db/tests/smoke_invariantes.sql` corre dentro de una transacción con `ROLLBACK`, así que es seguro
ejecutarlo contra cualquier BD.

| # | Invariante | Cómo se garantiza |
|---|---|---|
| 1 | Reenviar el mismo lote 3 veces produce **1 sola venta** | UUID del dispositivo como PK + `ON CONFLICT DO NOTHING` |
| 2 | Un folio impreso **nunca** se reutiliza en el mismo equipo | `UNIQUE (dispositivo_id, folio_consecutivo)` |
| 3 | El libro mayor **no** se puede editar ni borrar | Trigger `trg_movimientos_inmutables` |
| 4 | La liquidación detecta el faltante | Columna generada `diferencia` |
| 5 | Los rangos de folio **no** se traslapan | `EXCLUDE USING gist` con `int4range` |
| 6 | Una existencia negativa se acepta y es detectable | Ausencia deliberada de `CHECK` + índice parcial |

---

## 7. Pruebas de caos pendientes (Fase 2)

Las invariantes anteriores son de esquema. El motor de sincronización necesita además pruebas de
comportamiento, y se construyen **antes** de la primera pantalla de venta:

- [ ] Entrega duplicada del mismo lote (red que reintenta sola).
- [ ] Lote parcialmente aplicado y reenviado completo.
- [ ] Operaciones que llegan fuera de orden.
- [ ] Corte de energía del servidor a media transacción de ingest.
- [ ] **Restauración de un respaldo viejo del teléfono** — reproduce el outbox completo; es la fuente de
      duplicados más realista y la que nadie prueba.
- [ ] App reinstalada: rango de folios nuevo, sin colisión con el anterior.
- [ ] Mismo `operacion_id` con `hash_payload` distinto ⇒ cuarentena, nunca aplicación.
- [ ] Reloj del teléfono adelantado 3 días.
- [ ] 5,000 operaciones acumuladas tras una semana sin señal.

---

## 8. Lo que falta definir antes de la Fase 1

Estas decisiones cambian el DDL, así que conviene cerrarlas pronto:

- **Unidades y conversiones reales.** ¿Hay producto a granel (kg)? ¿La caja siempre tiene el mismo
  contenido por SKU?
- **Reglas de precio.** ¿El precio depende del cliente, del volumen, o de ambos? ¿Quién puede dar
  descuento y hasta cuánto?
- **Lotes y caducidad.** El esquema ya lo soporta (`maneja_lote`), pero manejarlo en el camión cuesta
  disciplina operativa real. Decide si lo quieres desde v1.
- **Preventa vs autoventa.** El modelo actual asume autoventa (se vende de lo que trae el camión). La
  preventa agrega un documento de pedido y cambia el ciclo.
- **Catálogo de motivos de no-drop.** Escríbelo con los vendedores, no en el escritorio.

## 9. Contratos Dart ↔ Python

El `hash_payload` y el hash de Argon2id se calculan en dos lenguajes y **deben coincidir byte a byte**.
Los puntos donde divergen en silencio (orden de claves, `Decimal` vs `double`, formato de fecha y zona,
`null` vs clave ausente) se blindan con vectores de prueba compartidos en `contracts/`, ejecutados por
la suite de Python y la de Dart en CI. Detalle en `ARQUITECTURA.md` §1.6.

Decisión asociada: **el dinero viaja como *string* en el JSON**, nunca como número. `Decimal` en Python,
paquete `decimal` en Dart, `numeric` en PostgreSQL.
