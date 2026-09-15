-- =============================================================================
-- 0004 · Inventario multi-almacén: el camión es un almacén
-- =============================================================================
-- NÚCLEO DEL DISEÑO (§2.2 del documento de arquitectura):
--
--   1. movimientos_inventario es un LIBRO MAYOR INMUTABLE (append-only,
--      garantizado por trigger). Es la verdad auditable.
--   2. existencias es una CACHÉ TRANSACCIONAL actualizada en la MISMA
--      transacción que el movimiento. Un job nocturno reconcilia ambas.
--   3. El almacén CAMION_XX tiene UN SOLO DUEÑO: su vendedor. Cero
--      concurrencia ⇒ cero conflictos ⇒ el offline es seguro.
--   4. BODEGA_PRINCIPAL solo se mueve por carga y retorno, procesados por el
--      servidor. Una venta offline JAMÁS toca el stock de bodega.
-- =============================================================================

CREATE TABLE almacenes (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo          text NOT NULL UNIQUE,      -- 'BODEGA_PRINCIPAL', 'CAMION_01'
    nombre          text NOT NULL,
    tipo            text NOT NULL CHECK (tipo IN ('bodega','camion','transito','merma')),
    sucursal_id     uuid REFERENCES sucursales(id),
    -- Dueño exclusivo. Obligatorio para camiones: es la garantía de no
    -- concurrencia sobre la que descansa todo el modelo offline.
    responsable_id  uuid REFERENCES usuarios(id),
    activo          boolean NOT NULL DEFAULT true,
    creado_en       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT camion_requiere_responsable
        CHECK (tipo <> 'camion' OR responsable_id IS NOT NULL)
);

-- Ahora sí se puede cerrar la FK diferida de 0001.
ALTER TABLE usuarios
    ADD CONSTRAINT fk_usuarios_almacen
    FOREIGN KEY (almacen_id) REFERENCES almacenes(id);

-- -----------------------------------------------------------------------------
-- Libro mayor de movimientos (APPEND-ONLY)
-- -----------------------------------------------------------------------------
CREATE TABLE movimientos_inventario (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tipo                text NOT NULL CHECK (tipo IN (
                            'carga',        -- bodega  → camión
                            'retorno',      -- camión  → bodega (liquidación)
                            'venta',        -- camión  → (sale del sistema)
                            'devolucion',   -- (entra) → camión
                            'merma',        -- camión  → almacén de merma
                            'traspaso',     -- camión  → camión
                            'ajuste',       -- corrección autorizada
                            'compra'        -- proveedor → bodega
                        )),
    almacen_origen_id   uuid REFERENCES almacenes(id),
    almacen_destino_id  uuid REFERENCES almacenes(id),
    producto_id         uuid NOT NULL REFERENCES productos(id),
    -- SIEMPRE en unidad base del producto. La conversión se resuelve al
    -- capturar, nunca aquí. Ésta es la regla que evita el descuadre caja/pieza.
    cantidad            numeric(14,3) NOT NULL CHECK (cantidad > 0),
    lote                text,
    caducidad           date,

    -- Documento que originó el movimiento (venta, carga, merma...).
    documento_tipo      text NOT NULL,
    documento_id        uuid NOT NULL,

    usuario_id          uuid REFERENCES usuarios(id),
    dispositivo_id      uuid REFERENCES dispositivos(id),
    fecha_dispositivo   timestamptz,
    fecha_servidor      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT movimiento_tiene_almacen
        CHECK (almacen_origen_id IS NOT NULL OR almacen_destino_id IS NOT NULL),
    CONSTRAINT movimiento_origen_distinto_destino
        CHECK (almacen_origen_id IS DISTINCT FROM almacen_destino_id)
);

CREATE INDEX idx_mov_documento   ON movimientos_inventario(documento_tipo, documento_id);
CREATE INDEX idx_mov_producto    ON movimientos_inventario(producto_id, fecha_servidor DESC);
CREATE INDEX idx_mov_almacen_org ON movimientos_inventario(almacen_origen_id, fecha_servidor DESC);
CREATE INDEX idx_mov_almacen_dst ON movimientos_inventario(almacen_destino_id, fecha_servidor DESC);

-- Inmutabilidad real, no por convención. Un error se corrige con un
-- movimiento de 'ajuste', nunca editando el historial.
CREATE OR REPLACE FUNCTION fn_bloquear_mutacion() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'La tabla % es append-only: corrige con un documento compensatorio, no con % .',
        TG_TABLE_NAME, TG_OP;
END;
$$;

CREATE TRIGGER trg_movimientos_inmutables
    BEFORE UPDATE OR DELETE ON movimientos_inventario
    FOR EACH ROW EXECUTE FUNCTION fn_bloquear_mutacion();

-- -----------------------------------------------------------------------------
-- Existencias (caché transaccional del libro mayor)
-- -----------------------------------------------------------------------------
CREATE TABLE existencias (
    almacen_id      uuid NOT NULL REFERENCES almacenes(id),
    producto_id     uuid NOT NULL REFERENCES productos(id),
    cantidad        numeric(14,3) NOT NULL DEFAULT 0,
    actualizado_en  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (almacen_id, producto_id)
);

-- Deliberadamente NO hay CHECK (cantidad >= 0).
-- Una venta offline puede llegar cuando el camión ya marcaba cero (p. ej. una
-- devolución que aún no sincroniza). Rechazarla contradice §0.1: la mercancía
-- ya salió. Se permite el negativo y se detecta en la liquidación como faltante.
CREATE INDEX idx_existencias_negativas
    ON existencias(almacen_id, producto_id) WHERE cantidad < 0;

-- -----------------------------------------------------------------------------
-- Cargas / embarques  (bodega → camión)
-- -----------------------------------------------------------------------------
-- La carga confirmada es el SNAPSHOT BASE que el dispositivo descarga para
-- operar el día offline.
-- -----------------------------------------------------------------------------
CREATE TABLE cargas (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    folio               text NOT NULL UNIQUE,
    almacen_origen_id   uuid NOT NULL REFERENCES almacenes(id),
    almacen_destino_id  uuid NOT NULL REFERENCES almacenes(id),
    vendedor_id         uuid NOT NULL REFERENCES usuarios(id),
    ruta_id             uuid REFERENCES rutas(id),
    fecha_operativa     date NOT NULL,     -- "el día" del vendedor, no el timestamp
    estado              text NOT NULL DEFAULT 'borrador'
                        CHECK (estado IN ('borrador','confirmada','en_ruta','liquidada','cancelada')),
    -- Versión del snapshot: el dispositivo confirma qué versión recibió.
    version             integer NOT NULL DEFAULT 1,
    confirmada_en       timestamptz,
    confirmada_por      uuid REFERENCES usuarios(id),
    recibida_en_disp_en timestamptz,
    creado_en           timestamptz NOT NULL DEFAULT now()
);

-- Un vendedor no puede tener dos cargas abiertas el mismo día operativo.
CREATE UNIQUE INDEX uq_carga_vendedor_dia
    ON cargas(vendedor_id, fecha_operativa)
    WHERE estado NOT IN ('cancelada');

CREATE TABLE carga_detalle (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    carga_id        uuid NOT NULL REFERENCES cargas(id) ON DELETE CASCADE,
    producto_id     uuid NOT NULL REFERENCES productos(id),
    cantidad        numeric(14,3) NOT NULL CHECK (cantidad > 0),  -- unidad base
    lote            text,
    caducidad       date
);

-- Expresión en índice, no en constraint: PostgreSQL no admite COALESCE dentro
-- de UNIQUE(...) a nivel tabla.
CREATE UNIQUE INDEX uq_carga_detalle_producto_lote
    ON carga_detalle (carga_id, producto_id, COALESCE(lote, ''));

-- -----------------------------------------------------------------------------
-- Liquidación / cierre de día
-- -----------------------------------------------------------------------------
-- Aquí es donde los descuadres se atrapan CONTABLEMENTE:
--   carga − ventas − mermas − devoluciones − retorno = diferencia
-- -----------------------------------------------------------------------------
CREATE TABLE liquidaciones (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    folio                   text NOT NULL UNIQUE,
    carga_id                uuid NOT NULL REFERENCES cargas(id),
    vendedor_id             uuid NOT NULL REFERENCES usuarios(id),
    fecha_operativa         date NOT NULL,

    estado                  text NOT NULL DEFAULT 'abierta'
                            CHECK (estado IN ('abierta','cuadrada','con_diferencia','cerrada')),

    -- Arqueo de efectivo
    efectivo_esperado       numeric(14,2) NOT NULL DEFAULT 0,
    efectivo_entregado      numeric(14,2) NOT NULL DEFAULT 0,
    diferencia_efectivo     numeric(14,2)
                            GENERATED ALWAYS AS (efectivo_entregado - efectivo_esperado) STORED,

    -- Se cierra solo cuando el dispositivo no tiene pendientes. Ver §2.3:
    -- "no se puede iniciar una carga nueva con operaciones pendientes".
    sync_completa           boolean NOT NULL DEFAULT false,
    operaciones_pendientes  integer NOT NULL DEFAULT 0,

    cerrada_en              timestamptz,
    cerrada_por             uuid REFERENCES usuarios(id),
    observaciones           text,
    creado_en               timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_liquidacion_carga ON liquidaciones(carga_id);

CREATE TABLE liquidacion_detalle (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    liquidacion_id      uuid NOT NULL REFERENCES liquidaciones(id) ON DELETE CASCADE,
    producto_id         uuid NOT NULL REFERENCES productos(id),
    cant_cargada        numeric(14,3) NOT NULL DEFAULT 0,
    cant_vendida        numeric(14,3) NOT NULL DEFAULT 0,
    cant_merma          numeric(14,3) NOT NULL DEFAULT 0,
    cant_devuelta       numeric(14,3) NOT NULL DEFAULT 0,
    cant_retornada      numeric(14,3) NOT NULL DEFAULT 0,   -- contada físicamente
    -- Positivo = sobrante, negativo = faltante. Lo que se le cobra al vendedor.
    diferencia          numeric(14,3)
                        GENERATED ALWAYS AS (
                            cant_retornada
                            - (cant_cargada - cant_vendida - cant_merma + cant_devuelta)
                        ) STORED,
    UNIQUE (liquidacion_id, producto_id)
);

CREATE INDEX idx_liquidacion_faltantes
    ON liquidacion_detalle(liquidacion_id) WHERE diferencia <> 0;

-- -----------------------------------------------------------------------------
-- Traspasos pendientes (oficina → camión)
-- -----------------------------------------------------------------------------
-- La oficina NUNCA hace UPDATE sobre existencias de un camión. Propone un
-- traspaso; el vendedor lo acepta en la app. Así se preserva la propiedad
-- exclusiva del almacén.
-- -----------------------------------------------------------------------------
CREATE TABLE traspasos (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    folio               text NOT NULL UNIQUE,
    almacen_origen_id   uuid NOT NULL REFERENCES almacenes(id),
    almacen_destino_id  uuid NOT NULL REFERENCES almacenes(id),
    estado              text NOT NULL DEFAULT 'propuesto'
                        CHECK (estado IN ('propuesto','aceptado','rechazado','cancelado')),
    solicitado_por      uuid REFERENCES usuarios(id),
    resuelto_por        uuid REFERENCES usuarios(id),
    resuelto_en         timestamptz,
    motivo_rechazo      text,
    creado_en           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE traspaso_detalle (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    traspaso_id     uuid NOT NULL REFERENCES traspasos(id) ON DELETE CASCADE,
    producto_id     uuid NOT NULL REFERENCES productos(id),
    cantidad        numeric(14,3) NOT NULL CHECK (cantidad > 0),
    UNIQUE (traspaso_id, producto_id)
);
