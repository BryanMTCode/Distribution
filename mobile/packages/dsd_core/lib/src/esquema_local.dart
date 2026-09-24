// GENERADO — no editar a mano.
// Fuente: mobile/db/schema.sql
// Regenerar: dart run tool/generar_esquema.dart

/// Esquema de la base local del dispositivo (SQLite + SQLCipher).
///
/// Dos zonas con reglas opuestas: el ESPEJO (catálogo, precios, clientes,
/// saldos) se sobrescribe con lo que manda el servidor, y la zona PROPIA
/// (ventas, cobros, mermas, no-drops, clientes nuevos) nace aquí y solo viaja
/// hacia el servidor. Nadie edita lo mismo desde dos lados.
library;

const esquemaLocal = r'''
-- =============================================================================
-- Esquema local del dispositivo (SQLite + SQLCipher)
-- =============================================================================
-- NO es una copia del esquema del servidor. Es el mínimo necesario para operar
-- un día de ruta sin señal, más la maquinaria de sincronización.
--
-- Dos zonas con reglas opuestas:
--   · ESPEJO   (solo lectura): catálogo, precios, clientes, saldos. Se
--              reemplaza con lo que manda el servidor. El servidor gana.
--   · PROPIA   (solo escritura): ventas, cobros, mermas, no-drops, clientes
--              nuevos. Nace aquí, viaja hacia el servidor, nunca regresa
--              modificada.
--
-- Nadie edita lo mismo desde dos lados ⇒ no hay conflictos que resolver.
-- =============================================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- =============================================================================
-- ZONA ESPEJO — se sobrescribe desde /sync/pull
-- =============================================================================

CREATE TABLE sync_estado (
    clave           TEXT PRIMARY KEY,
    valor           TEXT
);
-- Filas esperadas: cursor_pull, ultima_sync_ok, carga_id_activa,
--                  fecha_operativa, dispositivo_id, rango_folio_hasta

CREATE TABLE productos (
    id                  TEXT PRIMARY KEY,
    sku                 TEXT NOT NULL,
    codigo_barras       TEXT,
    nombre              TEXT NOT NULL,
    categoria_id        TEXT,
    unidad_base         TEXT NOT NULL,
    tasa_iva            REAL NOT NULL DEFAULT 0,
    activo              INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX ix_productos_barras ON productos(codigo_barras);
CREATE INDEX ix_productos_nombre ON productos(nombre);

CREATE TABLE producto_unidades (
    producto_id     TEXT NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    unidad_codigo   TEXT NOT NULL,
    factor          REAL NOT NULL,
    es_default      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (producto_id, unidad_codigo)
);

CREATE TABLE precios (
    lista_id        TEXT NOT NULL,
    producto_id     TEXT NOT NULL,
    unidad_codigo   TEXT NOT NULL,
    precio          REAL NOT NULL,
    precio_minimo   REAL,
    -- Se copia en cada venta. Permite al servidor saber con qué versión de la
    -- lista se vendió sin comparar importes.
    version         INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (lista_id, producto_id, unidad_codigo)
);

CREATE TABLE clientes (
    id                      TEXT PRIMARY KEY,
    codigo                  TEXT,
    nombre_comercial        TEXT NOT NULL,
    telefono                TEXT,
    direccion               TEXT,
    referencias             TEXT,
    lat                     REAL,
    lng                     REAL,
    ubicacion_origen        TEXT,
    ubicacion_precision_m   REAL,
    secuencia               INTEGER,
    lista_precios_id        TEXT,
    permite_credito         INTEGER NOT NULL DEFAULT 0,
    limite_credito          REAL NOT NULL DEFAULT 0,
    bloqueado               INTEGER NOT NULL DEFAULT 0,

    -- Saldo en CACHÉ. No es autoridad: el servidor manda. Se muestra siempre
    -- con su antigüedad ("actualizado hace 2 h") para que el vendedor sepa
    -- qué tan confiable es el número que está viendo.
    saldo_cache             REAL NOT NULL DEFAULT 0,
    saldo_cache_en          TEXT,

    -- 1 cuando el cliente nació en este teléfono y aún no lo confirma el
    -- servidor. Es zona PROPIA hasta que llega su confirmación.
    es_local                INTEGER NOT NULL DEFAULT 0,
    sincronizado            INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX ix_clientes_secuencia ON clientes(secuencia);
CREATE INDEX ix_clientes_nombre    ON clientes(nombre_comercial);

-- Inventario del camión: se siembra con la carga confirmada y se decrementa
-- localmente. Único dueño ⇒ sin concurrencia.
CREATE TABLE existencias_camion (
    producto_id     TEXT PRIMARY KEY REFERENCES productos(id),
    cant_cargada    REAL NOT NULL DEFAULT 0,   -- snapshot inmutable de la carga
    cant_actual     REAL NOT NULL DEFAULT 0,   -- lo que queda ahora mismo
    carga_id        TEXT
);

CREATE TABLE motivos_no_drop (
    codigo          TEXT PRIMARY KEY,
    nombre          TEXT NOT NULL,
    categoria       TEXT NOT NULL,
    requiere_nota   INTEGER NOT NULL DEFAULT 0,
    orden           INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE motivos_merma (
    codigo          TEXT PRIMARY KEY,
    nombre          TEXT NOT NULL
);

-- Credencial para login offline: hash Argon2id replicado desde el servidor.
CREATE TABLE credencial_local (
    usuario_id          TEXT PRIMARY KEY,
    codigo              TEXT NOT NULL,
    nombre              TEXT NOT NULL,
    rol                 TEXT NOT NULL,
    password_hash       TEXT NOT NULL,
    permisos_json       TEXT NOT NULL DEFAULT '[]',
    almacen_id          TEXT,
    -- Después de esta fecha, el login exige conexión. Evita que un equipo
    -- extraviado opere indefinidamente.
    valida_hasta        TEXT NOT NULL,
    actualizada_en      TEXT NOT NULL
);

-- =============================================================================
-- ZONA PROPIA — nace aquí, viaja al servidor
-- =============================================================================
-- Todos los ids son UUID v7 generados EN ESTE DISPOSITIVO. Ese UUID es la PK
-- también en PostgreSQL: por eso reenviar un lote nunca duplica un ticket.
-- =============================================================================

CREATE TABLE ventas (
    id                      TEXT PRIMARY KEY,          -- uuidv7 local
    folio_consecutivo       INTEGER NOT NULL UNIQUE,   -- dentro del rango asignado
    folio_local             TEXT NOT NULL UNIQUE,      -- 'VEND01-000123' (impreso)
    visita_id               TEXT,
    cliente_id              TEXT NOT NULL REFERENCES clientes(id),
    carga_id                TEXT,
    tipo                    TEXT NOT NULL DEFAULT 'contado',
    estado                  TEXT NOT NULL DEFAULT 'confirmada',
    lista_precios_id        TEXT,
    lista_precios_version   INTEGER,
    subtotal                REAL NOT NULL DEFAULT 0,
    descuento               REAL NOT NULL DEFAULT 0,
    impuestos               REAL NOT NULL DEFAULT 0,
    total                   REAL NOT NULL,
    lat                     REAL,
    lng                     REAL,
    ubicacion_precision_m   REAL,
    fecha_dispositivo       TEXT NOT NULL,
    fecha_operativa         TEXT NOT NULL,
    impreso                 INTEGER NOT NULL DEFAULT 0,
    reimpresiones           INTEGER NOT NULL DEFAULT 0,
    -- Payload ESC/POS conservado para reimprimir sin recalcular. Una
    -- reimpresión debe salir IDÉNTICA al original, marcada como COPIA.
    ticket_escpos           BLOB,
    sincronizada            INTEGER NOT NULL DEFAULT 0,
    creado_en               TEXT NOT NULL
);
CREATE INDEX ix_ventas_pendientes ON ventas(sincronizada) WHERE sincronizada = 0;
CREATE INDEX ix_ventas_cliente    ON ventas(cliente_id);

CREATE TABLE venta_partidas (
    id                  TEXT PRIMARY KEY,
    venta_id            TEXT NOT NULL REFERENCES ventas(id) ON DELETE CASCADE,
    linea               INTEGER NOT NULL,
    producto_id         TEXT NOT NULL REFERENCES productos(id),
    unidad_codigo       TEXT NOT NULL,
    factor_unidad       REAL NOT NULL,
    cantidad            REAL NOT NULL,
    cantidad_base       REAL NOT NULL,
    precio_unitario     REAL NOT NULL,
    descuento           REAL NOT NULL DEFAULT 0,
    promocion_id        TEXT,
    tasa_iva            REAL NOT NULL DEFAULT 0,
    importe             REAL NOT NULL,
    UNIQUE (venta_id, linea)
);

CREATE TABLE cobros (
    id                  TEXT PRIMARY KEY,
    folio_consecutivo   INTEGER NOT NULL UNIQUE,
    folio_local         TEXT NOT NULL UNIQUE,
    visita_id           TEXT,
    cliente_id          TEXT NOT NULL REFERENCES clientes(id),
    importe             REAL NOT NULL,
    forma_pago          TEXT NOT NULL DEFAULT 'efectivo',
    referencia          TEXT,
    -- Lo que el teléfono CREÍA que debía el cliente. Forense, no autoridad.
    saldo_cache_disp    REAL,
    lat                 REAL,
    lng                 REAL,
    -- Un cobro también se cancela (se contó mal el efectivo, el cliente se
    -- arrepintió). Sin este campo, el dispositivo no podía representarlo y el
    -- cálculo de crédito contaría como abono algo que ya no existe.
    estado              TEXT NOT NULL DEFAULT 'confirmado'
                        CHECK (estado IN ('confirmado','cancelado')),
    fecha_dispositivo   TEXT NOT NULL,
    fecha_operativa     TEXT NOT NULL,
    impreso             INTEGER NOT NULL DEFAULT 0,
    ticket_escpos       BLOB,
    sincronizado        INTEGER NOT NULL DEFAULT 0,
    creado_en           TEXT NOT NULL
);

CREATE TABLE mermas (
    id                  TEXT PRIMARY KEY,
    folio_consecutivo   INTEGER NOT NULL UNIQUE,
    tipo                TEXT NOT NULL,              -- merma | devolucion_cliente
    cliente_id          TEXT REFERENCES clientes(id),
    venta_origen_id     TEXT,
    motivo_codigo       TEXT NOT NULL,
    observaciones       TEXT,
    foto_path           TEXT,                       -- sube cuando haya señal
    foto_subida         INTEGER NOT NULL DEFAULT 0,
    lat                 REAL,
    lng                 REAL,
    fecha_dispositivo   TEXT NOT NULL,
    fecha_operativa     TEXT NOT NULL,
    sincronizada        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE merma_detalle (
    id              TEXT PRIMARY KEY,
    merma_id        TEXT NOT NULL REFERENCES mermas(id) ON DELETE CASCADE,
    producto_id     TEXT NOT NULL REFERENCES productos(id),
    cantidad_base   REAL NOT NULL
);

CREATE TABLE no_drops (
    id                      TEXT PRIMARY KEY,
    folio_consecutivo       INTEGER NOT NULL UNIQUE,
    visita_id               TEXT,
    cliente_id              TEXT NOT NULL REFERENCES clientes(id),
    motivo_codigo           TEXT NOT NULL REFERENCES motivos_no_drop(codigo),
    nota                    TEXT,
    -- Obligatorio: un no-drop sin GPS es indistinguible de una visita que
    -- nunca se hizo.
    lat                     REAL NOT NULL,
    lng                     REAL NOT NULL,
    ubicacion_precision_m   REAL,
    ubicacion_origen        TEXT NOT NULL DEFAULT 'gps',
    fecha_dispositivo       TEXT NOT NULL,
    fecha_operativa         TEXT NOT NULL,
    sincronizado            INTEGER NOT NULL DEFAULT 0
);

-- =============================================================================
-- OUTBOX — el corazón de la sincronización
-- =============================================================================
-- Se escribe en la MISMA transacción SQLite que el documento de negocio. Si la
-- app muere entre ambos, no existe estado inconsistente posible: o hay venta y
-- outbox, o no hay ninguno de los dos.
--
-- La cola NUNCA se atora: una operación rechazada por el servidor pasa a
-- 'cuarentena' y la cola sigue avanzando.
-- =============================================================================

CREATE TABLE outbox (
    operacion_id    TEXT PRIMARY KEY,          -- uuidv7; llave de idempotencia
    tipo            TEXT NOT NULL,             -- 'venta.crear','cobro.crear',...
    entidad_id      TEXT NOT NULL,
    payload         TEXT NOT NULL,             -- JSON canónico
    hash_payload    TEXT NOT NULL,             -- SHA-256 del payload canónico
    -- Orden FIFO estricto. Un cobro que referencia una venta creada offline
    -- debe viajar después de ella.
    secuencia       INTEGER NOT NULL,
    -- Agrupa venta + cobro + alta de cliente de la misma visita para que el
    -- servidor los aplique en una sola transacción.
    visita_id       TEXT,
    estado          TEXT NOT NULL DEFAULT 'pendiente'
                    CHECK (estado IN ('pendiente','enviando','confirmada','cuarentena')),
    intentos        INTEGER NOT NULL DEFAULT 0,
    ultimo_error    TEXT,
    proximo_intento TEXT,                      -- backoff exponencial
    creado_en       TEXT NOT NULL,
    confirmado_en   TEXT
);
CREATE INDEX ix_outbox_cola ON outbox(estado, secuencia);
CREATE UNIQUE INDEX ix_outbox_entidad ON outbox(tipo, entidad_id);

CREATE TABLE sync_bitacora (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    lote_id         TEXT,
    direccion       TEXT NOT NULL CHECK (direccion IN ('push','pull')),
    operaciones     INTEGER NOT NULL DEFAULT 0,
    aceptadas       INTEGER NOT NULL DEFAULT 0,
    duplicadas      INTEGER NOT NULL DEFAULT 0,
    rechazadas      INTEGER NOT NULL DEFAULT 0,
    exito           INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    duracion_ms     INTEGER,
    ocurrido_en     TEXT NOT NULL
);

-- Alimenta la pantalla "Inspector de sync" de la app. Se usa durante años:
-- es la diferencia entre depurar con datos y depurar con adivinanzas.
CREATE VIEW v_pendientes_sync AS
SELECT
    (SELECT COUNT(*) FROM outbox WHERE estado = 'pendiente')   AS pendientes,
    (SELECT COUNT(*) FROM outbox WHERE estado = 'cuarentena')  AS en_cuarentena,
    (SELECT MIN(creado_en) FROM outbox WHERE estado = 'pendiente') AS mas_antiguo,
    (SELECT valor FROM sync_estado WHERE clave = 'ultima_sync_ok') AS ultima_sync_ok;
''';
