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

CREATE TABLE IF NOT EXISTS sync_estado (
    clave           TEXT PRIMARY KEY,
    valor           TEXT
);
-- Filas esperadas: cursor_pull, ultima_sync_ok, carga_id_activa,
--                  fecha_operativa, dispositivo_id, rango_folio_hasta

CREATE TABLE IF NOT EXISTS productos (
    id                  TEXT PRIMARY KEY,
    sku                 TEXT NOT NULL,
    codigo_barras       TEXT,
    nombre              TEXT NOT NULL,
    categoria_id        TEXT,
    unidad_base         TEXT NOT NULL,
    tasa_iva            REAL NOT NULL DEFAULT 0,
    activo              INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_productos_barras ON productos(codigo_barras);
CREATE INDEX IF NOT EXISTS ix_productos_nombre ON productos(nombre);

CREATE TABLE IF NOT EXISTS producto_unidades (
    producto_id     TEXT NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    unidad_codigo   TEXT NOT NULL,
    factor          REAL NOT NULL,
    es_default      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (producto_id, unidad_codigo)
);

-- Listas de precios. El dispositivo las necesita por UN caso concreto: el
-- vendedor da de alta una tienda en la calle y quiere venderle EN ESE MOMENTO.
-- Ese cliente nace sin lista asignada —la asigna el servidor al confirmarlo—, y
-- sin una lista por omisión no habría con qué cotizarle. Negarle la venta al
-- cliente que se acaba de registrar es justo lo contrario de para qué existe el
-- alta en la calle.
CREATE TABLE IF NOT EXISTS listas_precios (
    id              TEXT PRIMARY KEY,
    codigo          TEXT NOT NULL,
    nombre          TEXT NOT NULL,
    es_default      INTEGER NOT NULL DEFAULT 0,
    activo          INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_listas_default ON listas_precios(es_default) WHERE es_default = 1;

CREATE TABLE IF NOT EXISTS precios (
    -- SIN llave foránea a listas_precios a propósito. Los deltas se aplican en
    -- una sola transacción todo-o-nada; si un precio llegara antes que su lista
    -- —el servidor los emite por orden de cursor, no por dependencia—, la
    -- transacción abortaría y el dispositivo NO VOLVERÍA A SINCRONIZAR NUNCA.
    -- La integridad referencial la sostiene el servidor, que es el dueño del
    -- catálogo; aquí solo hay un espejo.
    lista_id        TEXT NOT NULL,
    producto_id     TEXT NOT NULL,
    unidad_codigo   TEXT NOT NULL,
    precio          REAL NOT NULL,
    -- Sin uso: la regla de cero descuentos (ADR 0002 §7) colapsa el piso y el
    -- precio de venta en un solo número, y ese número es `precio`. Se conserva
    -- la columna porque el servidor la emite en el delta.
    precio_minimo   REAL,
    -- Se copia en cada venta. Permite al servidor saber con qué versión de la
    -- lista se vendió sin comparar importes.
    version         INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (lista_id, producto_id, unidad_codigo)
);

CREATE TABLE IF NOT EXISTS clientes (
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
    -- Del crédito del piloto. Todo es de contado (ADR 0002 §81): siguen aquí
    -- porque un teléfono que se actualiza ya las tiene, pero nada las lee.
    permite_credito         INTEGER NOT NULL DEFAULT 0,
    limite_credito          REAL NOT NULL DEFAULT 0,
    bloqueado               INTEGER NOT NULL DEFAULT 0,

    -- Saldo en CACHÉ. No es autoridad: el servidor manda. Se muestra siempre
    -- con su antigüedad ("actualizado hace 2 h") para que el vendedor sepa
    -- qué tan confiable es el número que está viendo.
    saldo_cache             REAL NOT NULL DEFAULT 0,
    saldo_cache_en          TEXT,

    -- Transferencias y cheques que el cliente reportó pagados y la oficina
    -- todavía no confirma en el banco. NO se restan del saldo —no liberan
    -- crédito (migración 0038 del servidor)—: se muestran para que el vendedor
    -- no le vuelva a cobrar lo que ya le pagaron.
    por_confirmar           REAL NOT NULL DEFAULT 0,

    -- Qué días le toca visita, como JSON: [{"dia":1,"semana":null}, ...]. La
    -- oficina lo captura en el plan de visita (migración 0041 del servidor) y
    -- llega dentro del delta del cliente. Ver `plan_visita.dart`.
    plan_visita             TEXT,

    -- 1 cuando el cliente nació en este teléfono y aún no lo confirma el
    -- servidor. Es zona PROPIA hasta que llega su confirmación.
    es_local                INTEGER NOT NULL DEFAULT 0,
    sincronizado            INTEGER NOT NULL DEFAULT 1,
    -- 0 cuando la oficina lo dio de baja o lo pasó a otra ruta.
    --
    -- El teléfono NO borra clientes, y eso no es una preferencia: `ventas`,
    -- `cobros`, `no_drops` y el borrador apuntan a esta tabla con llave foránea,
    -- así que un DELETE con una venta todavía sin sincronizar aborta la tanda de
    -- deltas completa — y como la tanda es todo-o-nada y el cursor solo avanza al
    -- aplicarla, el teléfono repetiría esa misma tanda para siempre: deja de
    -- sincronizar y nadie se entera.
    activo                  INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_clientes_secuencia ON clientes(secuencia);
CREATE INDEX IF NOT EXISTS ix_clientes_nombre    ON clientes(nombre_comercial);

-- Inventario del camión: un SALDO que las cargas suben y las ventas bajan.
-- Único dueño ⇒ sin concurrencia.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- EL CAMIÓN ES UN ALMACÉN RODANTE: NO AMANECE EN CEROS
-- ─────────────────────────────────────────────────────────────────────────────
-- Decisión de la dirección, octubre 2026: la mercancía que no se vende se queda
-- a dormir en el camión y se acumula con la carga del día siguiente. Así que una
-- carga confirmada **se suma** a lo que haya; no lo reemplaza. Antes lo
-- reemplazaba —"la carga es el inventario completo del día"— y el cierre le
-- cobraba al vendedor como faltante todo lo que había dormido arriba.
--
-- Que se sume obliga a algo que reemplazar no necesitaba: saber si una carga YA
-- se aplicó. Un `pull` repetido tras un corte de red trae el mismo delta otra
-- vez, y sumarlo dos veces le regalaría al camión una carga entera. De eso se
-- encarga `cargas_aplicadas`.
--
-- SIN llave foránea a productos, por la misma razón que `precios.lista_id`: los
-- deltas se aplican en una transacción todo-o-nada, y si el renglón de una carga
-- llegara antes que el producto al que apunta —o si el delta de ese producto se
-- hubiera podado del change_log— la transacción abortaría y el dispositivo NO
-- VOLVERÍA A SINCRONIZAR NUNCA. La integridad la sostiene el servidor, que es el
-- dueño del catálogo y de la carga.
--
-- El costo de no tenerla es un renglón huérfano que no aparece en el catálogo
-- hasta que llegue su producto. El costo de tenerla es un teléfono muerto.
CREATE TABLE IF NOT EXISTS existencias_camion (
    producto_id     TEXT PRIMARY KEY,
    -- Lo que subió en la ÚLTIMA carga que tocó este renglón. Es informativo:
    -- cuánto le entregó la bodega la última vez, no el total histórico.
    cant_cargada    REAL NOT NULL DEFAULT 0,
    cant_actual     REAL NOT NULL DEFAULT 0,   -- el saldo: lo que trae ahora mismo
    carga_id        TEXT                       -- la última carga que lo tocó
);

-- Qué cargas ya se sumaron al camión, y si su cierre ya se aplicó.
--
-- Es la memoria que hace idempotente la acumulación. Sin ella, el mismo delta
-- llegando dos veces —un `pull` repetido tras un corte de red— sumaría la carga
-- dos veces, y el vendedor vería el doble de mercancía de la que trae: la
-- ofrecería, no la tendría, y el descuadre aparecería en la liquidación sin que
-- nadie pudiera explicarlo.
--
-- `ajuste_aplicado_en` es lo mismo para el cierre: el delta de la carga liquidada
-- trae el ajuste del conteo físico, y sumarlo dos veces cobraría el faltante dos
-- veces.
-- Los ajustes de la oficina al camión que este teléfono ya sumó.
--
-- Misma razón que `cargas_aplicadas`: el ajuste viaja como una DIFERENCIA firmada
-- —no como el saldo resultante, que al llegar tarde borraría las ventas hechas
-- mientras tanto—, y una diferencia sumada dos veces está mal. Un `pull` repetido
-- tras un corte de red trae el mismo delta otra vez.
--
-- Se guarda el folio y la nota para poder decirle al vendedor QUÉ le cambiaron y
-- por qué: un número que baja sin explicación es la forma más rápida de que deje
-- de confiar en el sistema.
CREATE TABLE IF NOT EXISTS ajustes_camion_aplicados (
    ajuste_id   TEXT PRIMARY KEY,
    folio       TEXT,
    nota        TEXT,
    aplicado_en TEXT NOT NULL
);

-- Las devoluciones a la bodega que el vendedor capturó.
--
-- Nacen en el teléfono y sin señal: el vendedor es el único que sabe que acabó de
-- bajar 18 cajas, y exigirle conexión para registrarlo haría que lo apuntara en
-- papel. Es el mismo trato que una merma, por la misma razón.
--
-- El FOLIO lo pone el servidor y llega de vuelta por delta. Un traspaso no se le
-- entrega a un cliente, así que no necesita un folio impreso offline y no vale la
-- pena darle un rango propio; mientras no llegue, el renglón se identifica por su
-- fecha.
--
-- `estado` sigue al del servidor: `propuesto` mientras la mercancía está en
-- tránsito, `aceptado` cuando la bodega la recibió y contó. Es el comprobante del
-- vendedor de que eso dejó de ser su responsabilidad.
CREATE TABLE IF NOT EXISTS traspasos (
    id                  TEXT PRIMARY KEY,
    folio               TEXT,
    estado              TEXT NOT NULL DEFAULT 'propuesto',
    observaciones       TEXT,
    resuelto_en         TEXT,
    fecha_dispositivo   TEXT NOT NULL,
    fecha_operativa     TEXT NOT NULL,
    sincronizado        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS traspaso_detalle (
    id                  TEXT PRIMARY KEY,
    traspaso_id         TEXT NOT NULL REFERENCES traspasos(id) ON DELETE CASCADE,
    producto_id         TEXT NOT NULL REFERENCES productos(id),
    cantidad_base       REAL NOT NULL,
    -- Lo que la bodega contó. NULL mientras está en tránsito. Se guarda aparte de
    -- lo declarado porque la diferencia entre las dos es el dato que importa.
    cantidad_recibida   REAL,
    UNIQUE (traspaso_id, producto_id)
);

CREATE TABLE IF NOT EXISTS cargas_aplicadas (
    carga_id            TEXT PRIMARY KEY,
    aplicada_en         TEXT NOT NULL,
    ajuste_aplicado_en  TEXT
);

-- Rangos de folio asignados por el servidor.
--
-- El folio que se imprime lo genera el teléfono, pero **dentro de un rango que
-- el servidor le asignó**. Es la defensa contra el escenario que casi nadie
-- prueba: se reinstala la app, el contador local vuelve a 1, y el equipo empieza
-- a reimprimir folios que ya están en papel en manos de clientes.
--
-- `consumido_hasta` se actualiza DENTRO de la misma transacción que escribe el
-- documento. Si la transacción se deshace, la marca no avanza y el siguiente
-- intento reutiliza el mismo número: sin hueco y sin duplicado.
CREATE TABLE IF NOT EXISTS folios_rangos (
    tipo            TEXT PRIMARY KEY,          -- 'venta', 'cobro'
    desde           INTEGER NOT NULL,
    hasta           INTEGER NOT NULL,
    consumido_hasta INTEGER NOT NULL,
    asignado_en     TEXT,
    CHECK (hasta > desde),
    CHECK (consumido_hasta >= desde - 1 AND consumido_hasta <= hasta)
);

CREATE TABLE IF NOT EXISTS motivos_no_drop (
    codigo          TEXT PRIMARY KEY,
    nombre          TEXT NOT NULL,
    categoria       TEXT NOT NULL,
    requiere_nota   INTEGER NOT NULL DEFAULT 0,
    orden           INTEGER NOT NULL DEFAULT 0,
    activo          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS motivos_merma (
    codigo          TEXT PRIMARY KEY,
    nombre          TEXT NOT NULL,
    -- Si la pérdida se le carga al vendedor en la liquidación. Lo decide la
    -- OFICINA en el catálogo, y el teléfono lo MUESTRA al capturar: el vendedor
    -- está eligiendo un motivo que decide si el dinero sale de su bolsa, y
    -- esconderlo haría que esa elección fuera a ciegas.
    afecta_vendedor INTEGER NOT NULL DEFAULT 0,
    -- La oficina puede retirar un motivo del catálogo. Viaja al teléfono porque
    -- si no, el vendedor seguiría viéndolo y escogiéndolo: para él la
    -- desactivación no habría pasado, y el servidor marcaría su merma por un
    -- motivo que no eligió mal.
    activo          INTEGER NOT NULL DEFAULT 1
);

-- Credencial para login offline: hash Argon2id replicado desde el servidor.
CREATE TABLE IF NOT EXISTS credencial_local (
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

-- Borrador del carrito de la visita en curso.
--
-- El carrito no es un documento: no tiene folio, no descuenta inventario y no
-- viaja al servidor. Pero perderlo SÍ duele. En un Android de gama baja, veinte
-- minutos en un mercado con la app en segundo plano alcanzan para que el sistema
-- la mate, y el vendedor tendría que rearmar quince renglones frente al cliente
-- —o, más probable, apuntarlos en papel y dejar de usar la app—.
--
-- Una sola visita a la vez: el carrito pertenece al cliente que se está
-- atendiendo, y cambiar de cliente lo reemplaza. Arrastrar renglones de una
-- tienda a la siguiente sería la forma más rápida de facturarle a quien no pidió
-- nada.
CREATE TABLE IF NOT EXISTS carrito_borrador (
    id              INTEGER PRIMARY KEY CHECK (id = 1),   -- fila única
    cliente_id      TEXT NOT NULL,
    -- Cómo va a pagar: todo es de contado (ADR 0002 §81).
    forma_pago      TEXT NOT NULL DEFAULT 'efectivo',
    referencia_pago TEXT,
    -- Las líneas, con su presentación y su precio ya resuelto. Se guarda el
    -- precio con el que se armó: si el catálogo se refresca a media visita, el
    -- vendedor sigue viendo lo que le cotizó al cliente.
    lineas_json     TEXT NOT NULL,
    actualizado_en  TEXT NOT NULL
);

-- =============================================================================
-- ZONA PROPIA — nace aquí, viaja al servidor
-- =============================================================================
-- Todos los ids son UUID v7 generados EN ESTE DISPOSITIVO. Ese UUID es la PK
-- también en PostgreSQL: por eso reenviar un lote nunca duplica un ticket.
-- =============================================================================

CREATE TABLE IF NOT EXISTS ventas (
    id                      TEXT PRIMARY KEY,          -- uuidv7 local
    folio_consecutivo       INTEGER NOT NULL UNIQUE,   -- dentro del rango asignado
    folio_local             TEXT NOT NULL UNIQUE,      -- 'VEND01-000123' (impreso)
    visita_id               TEXT,
    cliente_id              TEXT NOT NULL REFERENCES clientes(id),
    carga_id                TEXT,
    tipo                    TEXT NOT NULL DEFAULT 'contado',
    estado                  TEXT NOT NULL DEFAULT 'confirmada',
    -- Se paga en el acto (ADR 0002 §81): 'efectivo' entra al arqueo del corte;
    -- 'transferencia' la confirma la oficina contra el banco.
    forma_pago              TEXT NOT NULL DEFAULT 'efectivo',
    referencia_pago         TEXT,
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
    -- Por qué la oficina canceló o corrigió esta venta. Viaja en el delta de la
    -- venta para que el vendedor lo LEA: que su venta cambie sin decirle por qué
    -- es la forma más rápida de que deje de confiar en el sistema.
    nota_oficina            TEXT,
    creado_en               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ventas_pendientes ON ventas(sincronizada) WHERE sincronizada = 0;
CREATE INDEX IF NOT EXISTS ix_ventas_cliente    ON ventas(cliente_id);

CREATE TABLE IF NOT EXISTS venta_partidas (
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

CREATE TABLE IF NOT EXISTS cobros (
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

CREATE TABLE IF NOT EXISTS mermas (
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

CREATE TABLE IF NOT EXISTS merma_detalle (
    id              TEXT PRIMARY KEY,
    merma_id        TEXT NOT NULL REFERENCES mermas(id) ON DELETE CASCADE,
    producto_id     TEXT NOT NULL REFERENCES productos(id),
    cantidad_base   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS no_drops (
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
-- EL CIERRE DEL DÍA (ADR 0002 §82): el corte del vendedor y la carga que pide
-- =============================================================================
-- Los dos nacen aquí, sin señal, y viajan por la cola (`corte.crear`,
-- `solicitud_carga.crear`). El corte es la palabra del vendedor —lo que contó
-- arriba del camión y el efectivo que entrega—; lo cierra la oficina al aceptar.
-- La solicitud vuelve por delta ('solicitud_carga') cuando la oficina la acepta
-- o la rechaza, con el folio de la carga.
--
-- El nombre del producto se guarda con el renglón: el ticket se vuelve a
-- compartir días después, y tiene que decir lo mismo aunque el catálogo cambie.
-- Por eso tampoco hay llave foránea a `productos`: un delta con un producto que
-- el teléfono ya no tiene no puede tumbar la tanda.
CREATE TABLE IF NOT EXISTS cortes_vendedor (
    id                  TEXT PRIMARY KEY,
    fecha_operativa     TEXT NOT NULL,
    carga_id            TEXT,
    efectivo_declarado  REAL NOT NULL,
    -- Lo vendido en efectivo según el teléfono al hacer el corte: es contra lo
    -- que el vendedor cuadró su bolsa, y lo que dice su ticket.
    efectivo_esperado   REAL NOT NULL DEFAULT 0,
    observaciones       TEXT,
    -- El ticket tal como se generó, para volver a compartirlo sin recalcular.
    texto_ticket        TEXT,
    fecha_dispositivo   TEXT NOT NULL,
    sincronizado        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS corte_vendedor_conteo (
    corte_id     TEXT NOT NULL REFERENCES cortes_vendedor(id) ON DELETE CASCADE,
    producto_id  TEXT NOT NULL,
    nombre       TEXT,
    cantidad     REAL NOT NULL,
    PRIMARY KEY (corte_id, producto_id)
);

CREATE TABLE IF NOT EXISTS solicitudes_carga (
    id                  TEXT PRIMARY KEY,
    corte_id            TEXT,
    -- PARA cuándo: el día siguiente al corte.
    fecha_operativa     TEXT NOT NULL,
    -- 'pendiente' | 'aceptada' | 'rechazada' | 'reemplazada', como en el servidor.
    estado              TEXT NOT NULL DEFAULT 'pendiente',
    carga_id            TEXT,
    carga_folio         TEXT,
    motivo              TEXT,
    observaciones       TEXT,
    resuelta_en         TEXT,
    fecha_dispositivo   TEXT NOT NULL,
    sincronizado        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS solicitud_carga_detalle (
    solicitud_id       TEXT NOT NULL REFERENCES solicitudes_carga(id) ON DELETE CASCADE,
    producto_id        TEXT NOT NULL,
    nombre             TEXT,
    unidad_codigo      TEXT NOT NULL,
    factor             REAL NOT NULL DEFAULT 1,
    bultos             REAL NOT NULL,
    cantidad           REAL NOT NULL,
    -- Lo que dejó la oficina al aceptar. NULL mientras no se resuelve.
    cantidad_aceptada  REAL,
    PRIMARY KEY (solicitud_id, producto_id)
);

-- =============================================================================
-- COMPRAS DEL GERENTE SIN SEÑAL (ADR 0002 §83)
-- =============================================================================
-- El gerente recibe mercancía de un proveedor en la calle, a veces sin señal. La
-- compra se guarda aquí con el id que le da el teléfono y se manda entera al
-- tener señal (`POST /v1/almacen/compras`). El servidor la reconoce por ese id:
-- reintentar no la suma dos veces.
--
-- No va por la `outbox` del vendedor a propósito: el gerente no tiene
-- dispositivo registrado ni folios, y su compra se manda con su sesión, como
-- todo lo de la oficina. Lo que comparten es la regla: se guarda primero, se
-- manda después, y se reconoce por su id.
CREATE TABLE IF NOT EXISTS compras_pendientes (
    id              TEXT PRIMARY KEY,
    -- El cuerpo tal como se va a mandar.
    payload         TEXT NOT NULL,
    -- 'pendiente' | 'enviada' | 'rechazada'
    estado          TEXT NOT NULL DEFAULT 'pendiente',
    folio           TEXT,               -- el de la entrada, cuando el servidor la recibe
    mensaje         TEXT,               -- lo que contestó el servidor
    creada_en       TEXT NOT NULL,
    enviada_en      TEXT
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

CREATE TABLE IF NOT EXISTS outbox (
    operacion_id    TEXT PRIMARY KEY,          -- uuidv7; llave de idempotencia
    tipo            TEXT NOT NULL,             -- 'venta.crear','merma.crear',...
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
CREATE INDEX IF NOT EXISTS ix_outbox_cola ON outbox(estado, secuencia);
CREATE UNIQUE INDEX IF NOT EXISTS ix_outbox_entidad ON outbox(tipo, entidad_id);

-- ---------------------------------------------------------------------------
-- Deltas que esta versión de la app no sabe aplicar
-- ---------------------------------------------------------------------------
-- Si el servidor empieza a mandar una entidad nueva y la app es más vieja, hay
-- dos malas salidas: atorar el cursor (el equipo no volvería a recibir NADA) o
-- descartar el delta en silencio (pérdida invisible). Se guarda el crudo y el
-- cursor avanza. Una versión futura de la app los reprocesa, y el inspector de
-- sync los muestra para que nadie descubra la pérdida por accidente.
CREATE TABLE IF NOT EXISTS deltas_desconocidos (
    cursor          INTEGER PRIMARY KEY,
    entidad         TEXT NOT NULL,
    entidad_id      TEXT NOT NULL,
    operacion       TEXT NOT NULL,
    payload         TEXT,
    -- Por qué no se pudo aplicar. NULL = entidad desconocida (una app vieja
    -- contra un servidor nuevo); con texto = el delta SÍ se reconoció y reventó
    -- al aplicarse. La distinción importa: lo primero se arregla actualizando la
    -- app, lo segundo es un defecto que hay que ir a ver.
    error           TEXT,
    recibido_en     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_bitacora (
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
CREATE VIEW IF NOT EXISTS v_pendientes_sync AS
SELECT
    (SELECT COUNT(*) FROM outbox WHERE estado = 'pendiente')   AS pendientes,
    (SELECT COUNT(*) FROM outbox WHERE estado = 'cuarentena')  AS en_cuarentena,
    (SELECT MIN(creado_en) FROM outbox WHERE estado = 'pendiente') AS mas_antiguo,
    (SELECT valor FROM sync_estado WHERE clave = 'ultima_sync_ok') AS ultima_sync_ok;

-- ---------------------------------------------------------------------------
-- Copia del último tablero que se pudo bajar (Fase 7)
-- ---------------------------------------------------------------------------
-- El tablero de Gerencia es lo único de la app que NO puede funcionar sin red:
-- su razón de existir es ver lo que están haciendo los otros, y eso no se sabe
-- sin preguntarle al servidor.
--
-- Lo que sí se puede hacer es guardar lo último que se vio, con la hora en que
-- se vio. Un gerente en la bodega, sin señal, lleva en el bolsillo las cifras
-- de hace una hora, y las cifras de hace una hora CON SU ETIQUETA sirven para
-- muchas decisiones. Una pantalla vacía que diga "sin conexión" no sirve para
-- ninguna.
--
-- Se guarda el JSON crudo y no columnas: el tablero crece tarjeta por tarjeta y
-- una tabla espejo obligaría a una migración del teléfono por cada cifra nueva.
-- Aquí no hay consultas que hacer —se lee completo y se vuelve a parsear— así
-- que el crudo no cuesta nada.
--
-- `recibido_en` es la hora del TELÉFONO al bajarlo, y es distinta del
-- `calculado_en` que viene dentro del JSON. Las dos se muestran: el servidor
-- calculó a las 10:05 y el teléfono lo bajó a las 10:40, así que la cifra tiene
-- 35 minutos de camino más los que tuviera al calcularse.
CREATE TABLE IF NOT EXISTS tablero_cache (
    clave           TEXT PRIMARY KEY,   -- 'tablero:2026-10-01' | 'mapa:2026-10-01'
    cuerpo          TEXT NOT NULL,      -- la respuesta JSON tal como llegó
    recibido_en     TEXT NOT NULL
);
''';
