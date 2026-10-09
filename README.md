# Sistema DSD — Distribuidora de abarrotes

Ecosistema de *Direct Store Delivery*: servidor local en oficina, app móvil multi-rol con operación
offline y panel web analítico.

## Estado

**Fases 0 a 7 hechas — el día completo del vendedor, su cierre y su lectura.** Sale el camión cargado,
se vende y se cobra offline, se registra lo que se perdió y a quién no se le vendió, la liquidación
cuadra contra la ecuación, y la oficina puede leer lo que pasó: transferencias por confirmar
y efectividad de visita por causa. Reglas de negocio
cerradas ([ADR 0002](docs/adr/0002-reglas-de-negocio.md)): autoventa, pieza y caja, **solo contado
—efectivo o transferencia— desde la retroalimentación del piloto (§81)**, remisión no fiscal, equipos de la empresa, sin lotes, y el **camión como
almacén rodante** — la mercancía que no se vende se queda arriba y se acumula con la carga del día
siguiente, así que no se le cobra como faltante al vendedor. Y lo que sí entrega baja
con un **traspaso que pasa por tránsito**: su palabra saca la mercancía del camión, pero
la bodega sube cuando alguien la cuenta.

| Pieza | Estado |
|---|---|
| Migraciones PostgreSQL + PostGIS (0001–0043) | ✅ aplican vía Alembic |
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
| **Solo contado** (§81) — la venta lleva su forma de pago: efectivo o transferencia | ✅ con pruebas |
| API de catálogo (productos, presentaciones, precios) | ✅ con pruebas |
| API de clientes (alta en campo, alcance por ruta) | ✅ con pruebas |
| **Motor de sincronización** — sobres, idempotencia, cuarentena | ✅ con pruebas de caos |
| Cursor de deltas con filtro de snapshot | ✅ verificado contra transacción en vuelo |
| change_log poblado por trigger | ✅ nada puede escribir sin dejar rastro |
| **Alta de cliente en la calle** con GPS y ajuste manual | ✅ 14 pruebas de widget |
| Aviso de posible duplicado antes de crearlo | ✅ por distancia, no por nombre |
| **Cliente de sincronización** del dispositivo | ✅ 22 pruebas de la tabla de decisiones |
| Aplicador de deltas al espejo local | ✅ contra el payload real del servidor |
| Delta de cartera (el saldo que faltaba) | ✅ migración 0011 |
| **Login sin señal** (Argon2id verificado en Dart) | ✅ con pruebas de widget |
| **Lista de clientes offline**, en orden de visita | ✅ con pruebas de widget |
| Modo demo para evaluar la UI en campo sin servidor | ✅ imposible en release (candado de compilación) |
| **Precio rígido en tres capas** (dominio · servidor · CHECK de PostgreSQL) | ✅ [ADR 0002 §7](docs/adr/0002-reglas-de-negocio.md) |
| Dinero de 2 decimales y **precio de 4** (caja↔pieza sin descuadre) | ✅ 5º contrato, 13 casos |
| **Catálogo de la visita** con precio de la lista del cliente | ✅ 18 pruebas de widget |
| **Carrito** con existencia del camión y forma de pago | ✅ 18 pruebas de widget + 25 de dominio |
| Cobro y remisión (impresión **a un toque**, no automática) | ✅ 15 pruebas de widget |
| **Ticket ESC/POS de 58 mm** — diseño, acentos, emoji, reimpresión | ✅ 59 pruebas de bytes |
| Vista previa del ticket, en el teléfono y versionada | ✅ [ver el papel](contracts/ticket_58mm_ejemplo.txt) |
| Impresora simulada con sus caminos de falla | ✅ 13 pruebas de widget |
| **Lienzo espacial offline** (radar de clientes, sin mosaicos) | ✅ 9 pruebas de widget |
| Portal por rol (vendedor / gerencia) | ✅ con pruebas |
| Contrato de Argon2id (7 vectores) | ✅ **verificado en los dos lenguajes** |
| **Outbox del dispositivo** — documento y cola en una transacción | ✅ con pruebas |
| Dinero exacto en el dispositivo (centavos enteros) | ✅ con pruebas |
| Rangos de folio locales | ✅ con pruebas |
| Contrato canónico Dart↔Python (34 vectores) | ✅ **verificado en los dos lenguajes** |

| OpenAPI 3.1 + degradado a 3.0 para Dart | ✅ generado en CI |
| Sobres de Dart aceptados por el servidor real | ✅ prueba de contrato de punta a punta |
| **Panel de operación** — sesión con cookie, CSRF, tablero | ✅ 17 pruebas |
| Pantalla de **cuarentena**: lo que el servidor rechazó | ✅ con payload íntegro |
| Pantalla de **ventas marcadas**, con el motivo en español | ✅ la otra mitad de §0.1 |
| **Captura de catálogo y precios** con cuatro decimales | ✅ 20 pruebas |
| **Confirmar prospectos de calle**: código y lista de precios | ✅ 16 pruebas |
| **Carga del camión** — bodega → camión, con su delta y su detalle | ✅ 20 pruebas |
| **Cargar varios a la vez** — la carga lista lo que hay en la bodega, se escriben cantidades y un botón | ✅ 8 pruebas |
| El teléfono **suma** la carga al sobrante, sin duplicarla en un `pull` repetido | ✅ 18 pruebas de Dart |
| **Usuarios, rutas, almacenes y listas** desde el panel | ✅ 23 pruebas |
| Arranque del primer usuario (`make usuario`) — **sin contraseña por omisión** | ✅ con pruebas |
| **Liquidación** — el camión es un almacén rodante: lo que durmió arriba no es faltante | ✅ 39 pruebas |
| **Gerencia corrige y el teléfono se entera** — cancelar y corregir ventas, ajustar el camión, editar y eliminar productos | ✅ 52 + 25 de Dart |
| **Sincronización blindada** — un delta que revienta se aparta, no congela el teléfono ([auditoría](docs/AUDITORIA-SINCRONIZACION.md)) | ✅ 31 pruebas |
| **Inventario y libro mayor** por almacén, con su saldo corriente | ✅ 13 pruebas |
| Transmisión Bluetooth (solo el socket: los bytes ya están) | ⛔ espera la impresora física |
| **Mermas y devoluciones** — el signo que evita que el faltante sea del vendedor | ✅ 44 pruebas + 32 de ingesta |
| **No-drops con geosello** — la única excepción a «marcar, no rechazar» | ✅ 19 pruebas de widget |
| Los catálogos de motivos llegan al teléfono (y se pueden desactivar) | ✅ en el contrato de deltas |
| **Transferencias por confirmar** — la oficina las busca en el banco para cuadrar el dinero; la que no llegó, con motivo | ✅ 9 pruebas |
| **Cierre del vendedor** (§82) — corte a ciegas y solicitud de carga sin señal, el gerente acepta (cierra el corte y confirma la carga de mañana), tickets por WhatsApp | ✅ 18 pruebas + 16 de Dart y 4 de widget |
| **Compra a proveedor sin señal** (§83) — el gerente la captura en la calle, se manda entera e idempotente a la bodega principal; costo opcional | ✅ 7 pruebas + 6 de Dart y 3 de widget |
| **Ubicación del cliente con GPS o a mano** (§84) — vendedor sin señal, oficina en la app y el panel, una sola regla de validación | ✅ 14 pruebas + 10 de Dart y 4 de widget |
| **Cuenta del vendedor** — faltante y mermas a su cargo **a costo**, efectivo; abonos y condonación | ✅ 23 pruebas |
| **Cambio físico** — fresco por caducado o dañado: sale del camión con documento, sin cobro ni faltante | ✅ 8 pruebas + 2 de Dart y 5 de widget |
| **Editar y eliminar la estructura** — usuarios, rutas, almacenes, listas, proveedores y motivos; la base decide si se borra o se da de baja | ✅ 24 pruebas |
| **Plan de visita** — «hoy te tocan» en el teléfono, y Efectividad cuenta lo que tocaba y nadie visitó | ✅ 18 pruebas + 11 de Dart y 5 de widget |
| **¿Listo para operar?** — los once pasos del arranque, y los **pendientes de hoy** arriba del tablero | ✅ 10 pruebas |
| **Panel → teléfono, acción por acción** — la ruta viaja con su titular, el cliente con su lista, el alcance se lee en vivo ([§8](docs/AUDITORIA-SINCRONIZACION.md)) | ✅ 10 pruebas + 3 de Dart |
| **Guardia entre lenguajes** — lo que publica el servidor, el teléfono lo sabe aplicar (y al revés) | ✅ 3 pruebas |
| **Cuadre del camión** — con todo entregado y todo traído, el teléfono queda igual al panel | ✅ 6 pruebas + 12 de Dart |
| **Tablero por periodo**, **movimientos de cada vendedor** y **bitácora de sincronizaciones** | ✅ 17 pruebas |
| **Reprocesar la cuarentena** desde el panel — lo rechazado por una causa ya corregida entra, lo atendido deja de salir rojo, y el teléfono se destraba | ✅ 6 pruebas |
| **El teléfono reporta su cola**, y `sync_completa` deja de ser una casilla | ✅ 8 + 6 pruebas |
| **Efectividad de visita** — cuántas visitas perdidas podemos arreglar nosotros | ✅ 20 pruebas |
| **Esquema estrella** (`fact_ventas`, `fact_visitas`, `dim_*`) con refresco por job | ✅ 27 pruebas |
| **Laboratorio analítico (Streamlit)** — drop size, rotación, clientes en riesgo | ✅ 8 pruebas con `AppTest` |
| **Tablero de Gerencia en el teléfono** — seis cifras, cada una con su antigüedad | ✅ 26 pruebas de widget |
| **Desempeño del día** — cada vendedor contra sus mismos días de la semana, y **de quién** falta información | ✅ 28 + 6 de Dart y 5 de widget |
| **Panel en cinco módulos** y Editar / Eliminar / Ajustar en las tablas, con delta al teléfono | ✅ 19 pruebas |
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
| **Devolver del camión a la bodega** — el vendedor declara, la bodega **cuenta** | ✅ 34 + 25 de Dart |
| **Compras** — proveedores, **costo promedio ponderado** y cuentas por pagar | ✅ 35 pruebas |
| **§2.3: no se carga con operaciones pendientes** — forzable, con constancia | ✅ 11 pruebas |
| **Respaldo y simulacro de restauración** que verifica las invariantes | ✅ `make simulacro` |
| **Despliegue de un comando** — `docker compose up -d` crea los roles de RLS en orden | ✅ 11 pruebas |
| **APK de producción firmado** — el build se detiene sin keystore, y revisa el APK | ✅ 7 + 5 de widget |
| **Dependencias con candado** — `uv.lock` versionado, los tres caminos lo usan | ✅ 9 pruebas |
| **Vincular el teléfono de un vendedor** — desde el panel, y entra sin señal después | ✅ 7 + 6 de widget |
| **Simulacro maestro end-to-end** — un día completo con cifras verificables | ✅ `docs/SIMULACRO.md` |
| **Despliegue en VPS** — cortafuegos, SSH, puertos y hora, con `make servidor-revisar` | ⏳ se hace al crear el servidor |
| **Instrumento del piloto** — cuadre diario contra el papel, bitácora y 12 criterios | ✅ 71 pruebas |
| **El piloto de campo en sí** — dos semanas de un vendedor real | ⏳ calendario, no código |

**1344 pruebas de Python** sobre PostgreSQL 16.13 + PostGIS, **640 de Dart** y **286 de widget**, todas en verde.

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

- [`docs/INSTALACION-PASO-A-PASO.md`](docs/INSTALACION-PASO-A-PASO.md) — **la receta para desplegar**:
  del dominio al primer usuario en 27 pasos lineales, diciendo en cada uno dónde estás trabajando, qué
  pegar y qué tienes que ver en pantalla antes de continuar. Empieza por aquí el día del despliegue.
- [`docs/DESPLIEGUE.md`](docs/DESPLIEGUE.md) — el **por qué** de ese despliegue: qué cambia respecto de
  un servidor en la oficina, el tamaño del VPS con los números, el swap, la trampa de Docker con `ufw` y
  el cortafuegos de nube que sí la resuelve.
- [`docs/PUESTA-EN-MARCHA.md`](docs/PUESTA-EN-MARCHA.md) — **del servidor desplegado al vendedor
  trabajando**, en dos mitades. La **Parte 0** es el ciclo de los errores que van saliendo:
  capturar el traceback de forma que sirva, respaldar, aplicar el arreglo según lo que tocó
  —migración, código, app— verificarlo donde falló y anotarlo, con su registro. Las **Partes 1 a 6**
  son el camino: respaldos, el keystore que no se puede perder, el APK de producción, vincular el
  teléfono, el simulacro y el día −1 del piloto. 18 pasos con su lista para tachar.
- [`docs/SIMULACRO.md`](docs/SIMULACRO.md) — el **simulacro maestro**: un día completo de operación con
  las cifras exactas que deben salir en siete puntos de control. *Si no cuadra aquí, no sale a la calle.*
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
                     comun.py         navegación (filtrada por permisos) y números escritos a mano
                     arranque.py      ¿listo para operar? y los pendientes de hoy
                     periodo.py       hoy, la semana, el mes o un rango: igual en todo el panel
                     vendedores.py    lo que hizo cada vendedor, en una línea de tiempo
                     sincronizaciones.py cada subida y cada bajada, y si cada teléfono está al día
                     desempeno.py     el día de cada vendedor contra sus mismos días de la semana
                     productos.py     catálogo y precios (los 4 decimales)
                     clientes.py      confirmar prospectos y su lista de precios
                     cargas.py        la carga del camión (bodega → camión)
                     equipo.py        usuarios, rutas, almacenes y listas de precios
                     inventario.py    existencias y libro mayor por almacén
                     liquidaciones.py el corte del día (Fase 7)
                     transferencias.py las transferencias por confirmar en el banco
                     cierres.py       cortes y cargas por aceptar (el cierre del vendedor)
                     cuenta_vendedores.py lo que debe cada vendedor, y cómo lo paga
                     equipo_fichas.py editar y eliminar usuarios, rutas, almacenes y listas
                     motivos.py       los catálogos de motivos (y si una merma se cobra)
                     plan_visita.py   qué días toca cada cliente, ruta por ruta
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
                                      y la referencia del mismo día de la semana
                     importes.py      la aritmética de una partida (un solo redondeo)
                     identificadores.py  UUIDv7
    infra/models/    SQLAlchemy 2.0 sobre el esquema del SQL
    infra/           cuenta_vendedor.py (los cargos del Corte) · cierre_del_vendedor.py
                     (aceptar el corte y la carga que pide, para el panel y la app)
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
                     borrador, VENTA, MERMA, NO-DROP, TICKET ESC/POS +
                     vista previa, forma de pago, CIERRE DEL DÍA y sus tickets para
                     compartir, credencial, folios, outbox, sobres,
                     ubicación, alta de clientes, sincronizador, aplicador de
                     deltas, esquema, TABLERO, TRASPASO, login en línea
    test/            640 pruebas que corren en segundos
    tool/            genera los sobres de ejemplo y el esquema embebido
  app/               APP FLUTTER:
    lib/src/datos/   base local, almacén seguro, repositorios
    lib/src/estado/  sesión y providers
    lib/src/pantallas/ login, ruta, catálogo, carrito, venta, ticket,
                     merma y devolución, no-drop, lienzo espacial, MI DÍA con su
                     CORTE y SOLICITUD DE CARGA,
                     MI CAMIÓN, DEVOLVER A LA BODEGA,
                     gerencia/ (tablero, mapa del día, cortes y cargas por aceptar)
    test/            286 pruebas de widget, sin emulador
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
make pruebas                        # 1375 pruebas de Python
make movil                          # 646 de Dart + 326 de widget
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
