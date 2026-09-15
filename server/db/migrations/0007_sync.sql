-- =============================================================================
-- 0007 · Motor de sincronización
-- =============================================================================
-- Entrega "al menos una vez" + receptor idempotente = efecto de exactamente
-- una vez.
--
-- Una red mala entrega el mismo lote dos veces. Eso es NORMAL, no es un bug.
-- No se intenta evitar el duplicado en el envío: se hace que recibirlo dos
-- veces no cambie nada.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- PUSH · Registro de idempotencia
-- -----------------------------------------------------------------------------
CREATE TABLE sync_lotes (
    id                  uuid PRIMARY KEY,          -- lote_id generado en el dispositivo
    dispositivo_id      uuid NOT NULL REFERENCES dispositivos(id),
    usuario_id          uuid NOT NULL REFERENCES usuarios(id),
    total_operaciones   integer NOT NULL,
    aceptadas           integer NOT NULL DEFAULT 0,
    duplicadas          integer NOT NULL DEFAULT 0,
    rechazadas          integer NOT NULL DEFAULT 0,
    app_version         text,
    recibido_en         timestamptz NOT NULL DEFAULT now(),
    procesado_en        timestamptz,
    duracion_ms         integer
);

CREATE INDEX idx_sync_lotes_disp ON sync_lotes(dispositivo_id, recibido_en DESC);

-- ESTA TABLA ES LA GARANTÍA CONTRA DUPLICADOS.
-- Un operacion_id repetido devuelve el resultado guardado sin reprocesar.
CREATE TABLE sync_operaciones (
    operacion_id    uuid PRIMARY KEY,              -- generado en el dispositivo
    dispositivo_id  uuid NOT NULL REFERENCES dispositivos(id),
    lote_id         uuid REFERENCES sync_lotes(id),
    tipo            text NOT NULL,                 -- 'venta.crear','cobro.crear',...
    entidad_id      uuid,                          -- PK del documento resultante
    -- SHA-256 del payload canónico. Mismo id + hash distinto = ALARMA ROJA
    -- (bug del cliente o manipulación). Va a cuarentena, no se aplica.
    hash_payload    text NOT NULL,
    resultado       text NOT NULL
                    CHECK (resultado IN ('aceptada','duplicada','rechazada')),
    error_codigo    text,
    error_mensaje   text,
    procesado_en    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_sync_ops_disp   ON sync_operaciones(dispositivo_id, procesado_en DESC);
CREATE INDEX idx_sync_ops_entidad ON sync_operaciones(entidad_id) WHERE entidad_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- Cuarentena
-- -----------------------------------------------------------------------------
-- Una operación rechazada NUNCA bloquea la cola del dispositivo. Se guarda
-- íntegra aquí para revisión humana en el panel web. Una cola atorada es una
-- app inservible; un payload perdido es dinero perdido.
-- -----------------------------------------------------------------------------
CREATE TABLE sync_cuarentena (
    id              bigserial PRIMARY KEY,
    operacion_id    uuid NOT NULL,
    dispositivo_id  uuid NOT NULL REFERENCES dispositivos(id),
    usuario_id      uuid REFERENCES usuarios(id),
    tipo            text NOT NULL,
    payload         jsonb NOT NULL,
    hash_payload    text NOT NULL,
    error_codigo    text NOT NULL,
    error_mensaje   text NOT NULL,
    estado          text NOT NULL DEFAULT 'pendiente'
                    CHECK (estado IN ('pendiente','reprocesada','descartada')),
    resuelto_por    uuid REFERENCES usuarios(id),
    resuelto_en     timestamptz,
    nota_resolucion text,
    recibido_en     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_cuarentena_pendientes
    ON sync_cuarentena(recibido_en DESC) WHERE estado = 'pendiente';

-- -----------------------------------------------------------------------------
-- PULL · change_log con cursor monotónico
-- -----------------------------------------------------------------------------
-- NO se usa updated_at como cursor. Con relojes desincronizados y
-- transacciones concurrentes, un cursor por timestamp PIERDE REGISTROS EN
-- SILENCIO: una transacción que empezó antes puede hacer COMMIT después de
-- que el dispositivo ya avanzó su marca de agua.
--
-- Un BIGSERIAL tiene el mismo problema si se lee sin cuidado (huecos por
-- transacciones en vuelo), así que el endpoint de pull nunca entrega registros
-- por encima de pg_snapshot_xmin(pg_current_snapshot()): solo lee lo que ya
-- está confirmado para todos.
-- -----------------------------------------------------------------------------
CREATE TABLE change_log (
    cursor          bigserial PRIMARY KEY,
    entidad         text NOT NULL,          -- 'producto','precio','cliente','carga'
    entidad_id      uuid NOT NULL,
    operacion       text NOT NULL CHECK (operacion IN ('upsert','delete')),
    -- Alcance: a qué dispositivos les importa este cambio.
    -- NULL = a todos (catálogo global).
    ruta_id         uuid REFERENCES rutas(id),
    vendedor_id     uuid REFERENCES usuarios(id),
    -- Snapshot del registro. Evita un JOIN por entidad al armar el delta.
    payload         jsonb,
    xid             xid8 NOT NULL DEFAULT pg_current_xact_id(),
    creado_en       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_change_log_entidad  ON change_log(entidad, cursor);
CREATE INDEX idx_change_log_ruta     ON change_log(ruta_id, cursor) WHERE ruta_id IS NOT NULL;
CREATE INDEX idx_change_log_vendedor ON change_log(vendedor_id, cursor) WHERE vendedor_id IS NOT NULL;

COMMENT ON COLUMN change_log.xid IS
    'Id de transacción. El pull filtra xid < pg_snapshot_xmin(pg_current_snapshot()) '
    'para no entregar filas de transacciones aún en vuelo y evitar huecos.';

-- Retención: el change_log crece sin límite. Un job lo poda conservando lo
-- necesario para el dispositivo más atrasado.
CREATE OR REPLACE VIEW v_change_log_retencion AS
SELECT
    COALESCE(MIN(d.ultimo_cursor_pull), 0) AS cursor_minimo_dispositivos,
    (SELECT COALESCE(MAX(cursor), 0) FROM change_log) AS cursor_actual
FROM dispositivos d
WHERE d.estado = 'activo';

-- -----------------------------------------------------------------------------
-- Rangos de folio por dispositivo
-- -----------------------------------------------------------------------------
-- El dispositivo genera su consecutivo local, pero dentro de un RANGO que le
-- asigna el servidor. Si un teléfono se reinstala, recibe un rango nuevo y sus
-- folios nunca chocan con los del equipo anterior.
-- -----------------------------------------------------------------------------
CREATE TABLE folios_rangos (
    id              bigserial PRIMARY KEY,
    dispositivo_id  uuid NOT NULL REFERENCES dispositivos(id),
    documento_tipo  text NOT NULL CHECK (documento_tipo IN ('venta','cobro','merma','no_drop')),
    desde           integer NOT NULL,
    hasta           integer NOT NULL,
    consumido_hasta integer NOT NULL DEFAULT 0,
    asignado_en     timestamptz NOT NULL DEFAULT now(),
    agotado         boolean NOT NULL DEFAULT false,
    CONSTRAINT rango_valido CHECK (hasta > desde)
);

CREATE INDEX idx_folios_rango_activo
    ON folios_rangos(dispositivo_id, documento_tipo) WHERE NOT agotado;

-- Dos rangos del mismo tipo no pueden traslaparse en un mismo dispositivo.
ALTER TABLE folios_rangos
    ADD CONSTRAINT no_traslape_rangos
    EXCLUDE USING gist (
        dispositivo_id WITH =,
        documento_tipo WITH =,
        int4range(desde, hasta, '[]') WITH &&
    );

-- -----------------------------------------------------------------------------
-- Salud de sincronización (alimenta el panel de Gerencia)
-- -----------------------------------------------------------------------------
-- §0.3 — "tiempo real" es "tiempo real de lo sincronizado". Cada tarjeta del
-- dashboard muestra la antigüedad de estos datos.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_salud_sync AS
SELECT
    d.id                        AS dispositivo_id,
    d.etiqueta,
    u.id                        AS usuario_id,
    u.nombre                    AS vendedor,
    r.codigo                    AS ruta,
    d.ultima_sync_push_en,
    d.ultima_sync_pull_en,
    EXTRACT(EPOCH FROM (now() - d.ultima_sync_push_en))::int / 60 AS minutos_sin_sync,
    (SELECT COUNT(*) FROM sync_cuarentena c
      WHERE c.dispositivo_id = d.id AND c.estado = 'pendiente')   AS ops_en_cuarentena,
    CASE
        WHEN d.ultima_sync_push_en IS NULL                      THEN 'nunca'
        WHEN now() - d.ultima_sync_push_en < interval '30 min'   THEN 'al_dia'
        WHEN now() - d.ultima_sync_push_en < interval '4 hours'  THEN 'retrasado'
        ELSE 'critico'
    END                         AS estado_sync
FROM dispositivos d
JOIN usuarios u ON u.id = d.usuario_id
LEFT JOIN rutas r ON r.vendedor_id = u.id
WHERE d.estado = 'activo';
