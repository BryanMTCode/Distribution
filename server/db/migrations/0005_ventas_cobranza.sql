-- =============================================================================
-- 0005 · Ventas, crédito y cobranza
-- =============================================================================
-- Propiedad del dato: DISPOSITIVO. El servidor acepta, nunca reescribe.
--
-- REGLAS INVIOLABLES:
--   1. `ventas.id` es un UUID generado EN EL TELÉFONO. Es la PK. El ingest usa
--      ON CONFLICT (id) DO NOTHING ⇒ reenviar el mismo lote no duplica nada.
--   2. DOS FOLIOS: folio_local (impreso en el papel, único por dispositivo) y
--      folio_servidor (consecutivo global, asignado al recibir). El teléfono
--      NUNCA adivina un consecutivo global.
--   3. Una venta jamás se edita. Se cancela con un documento compensatorio.
--   4. El servidor no rechaza por reglas de negocio: marca requiere_revision.
-- =============================================================================

-- Consecutivo global del servidor. Se asigna al ingresar, no en el dispositivo.
CREATE SEQUENCE seq_folio_venta START 1;

CREATE TABLE ventas (
    -- UUID v7 generado en el dispositivo. NO usar DEFAULT: si el servidor
    -- generara la llave, el reenvío duplicaría el ticket.
    id                      uuid PRIMARY KEY,

    -- Folio impreso en el ticket físico: 'VEND01-000123'
    dispositivo_id          uuid NOT NULL REFERENCES dispositivos(id),
    folio_consecutivo       integer NOT NULL CHECK (folio_consecutivo > 0),
    folio_local             text NOT NULL,
    -- Asignado por el servidor al recibir.
    folio_servidor          bigint NOT NULL DEFAULT nextval('seq_folio_venta'),

    cliente_id              uuid NOT NULL REFERENCES clientes(id),
    vendedor_id             uuid NOT NULL REFERENCES usuarios(id),
    ruta_id                 uuid REFERENCES rutas(id),
    almacen_id              uuid NOT NULL REFERENCES almacenes(id),   -- el camión
    carga_id                uuid REFERENCES cargas(id),
    visita_id               uuid,   -- agrupa venta + cobro + alta del mismo sobre

    tipo                    text NOT NULL DEFAULT 'contado'
                            CHECK (tipo IN ('contado','credito')),
    estado                  text NOT NULL DEFAULT 'confirmada'
                            CHECK (estado IN ('confirmada','cancelada')),

    lista_precios_id        uuid REFERENCES listas_precios(id),
    -- Versión de la lista con la que el dispositivo calculó. Permite detectar
    -- ventas hechas con precios obsoletos sin comparar importes uno a uno.
    lista_precios_version   integer,

    subtotal                numeric(14,2) NOT NULL DEFAULT 0,
    descuento               numeric(14,2) NOT NULL DEFAULT 0,
    impuestos               numeric(14,2) NOT NULL DEFAULT 0,
    total                   numeric(14,2) NOT NULL,

    -- ------------------------------------------------------------------
    -- Geo-sello de la visita: la mejor herramienta antifraude. Dice si el
    -- vendedor realmente estuvo en el punto de venta.
    -- ------------------------------------------------------------------
    lat                     numeric(10,7),
    lng                     numeric(10,7),
    ubicacion               geography(Point,4326)
                            GENERATED ALWAYS AS (
                                CASE WHEN lat IS NOT NULL AND lng IS NOT NULL
                                     THEN ST_SetSRID(ST_MakePoint(lng::float8, lat::float8), 4326)::geography
                                END
                            ) STORED,
    ubicacion_precision_m   numeric(8,2),
    -- Distancia al domicilio registrado del cliente. La calcula el servidor al
    -- ingresar; > umbral ⇒ requiere_revision.
    distancia_cliente_m     numeric(10,2),

    -- ------------------------------------------------------------------
    -- Relojes: el del teléfono miente. Se guardan los dos.
    -- ------------------------------------------------------------------
    fecha_dispositivo       timestamptz NOT NULL,
    fecha_servidor          timestamptz NOT NULL DEFAULT now(),
    fecha_operativa         date NOT NULL,
    desfase_reloj_seg       integer,

    -- Trazabilidad del ticket impreso.
    impreso                 boolean NOT NULL DEFAULT false,
    reimpresiones           smallint NOT NULL DEFAULT 0,

    -- ------------------------------------------------------------------
    -- §0.1 — El mundo físico ya ocurrió. Nunca se rechaza; se marca.
    -- ------------------------------------------------------------------
    requiere_revision       boolean NOT NULL DEFAULT false,
    revision_motivos        text[] NOT NULL DEFAULT '{}',
    -- p.ej. {'precio_desactualizado','excede_limite_credito','fuera_de_geocerca'}

    sincronizada_en         timestamptz NOT NULL DEFAULT now(),
    observaciones           text,

    CONSTRAINT venta_total_coherente
        CHECK (total = subtotal - descuento + impuestos),
    CONSTRAINT venta_total_no_negativo CHECK (total >= 0)
);

-- ESTA es la barrera física contra tickets duplicados.
CREATE UNIQUE INDEX uq_venta_folio_dispositivo
    ON ventas(dispositivo_id, folio_consecutivo);
CREATE UNIQUE INDEX uq_venta_folio_local ON ventas(folio_local);

CREATE INDEX idx_ventas_cliente   ON ventas(cliente_id, fecha_servidor DESC);
CREATE INDEX idx_ventas_vendedor  ON ventas(vendedor_id, fecha_operativa DESC);
CREATE INDEX idx_ventas_operativa ON ventas(fecha_operativa DESC) WHERE estado = 'confirmada';
CREATE INDEX idx_ventas_revision  ON ventas(fecha_servidor DESC) WHERE requiere_revision;
CREATE INDEX idx_ventas_visita    ON ventas(visita_id) WHERE visita_id IS NOT NULL;
CREATE INDEX idx_ventas_ubicacion ON ventas USING gist (ubicacion);

CREATE TABLE venta_partidas (
    id                  uuid PRIMARY KEY,           -- también del dispositivo
    venta_id            uuid NOT NULL REFERENCES ventas(id) ON DELETE CASCADE,
    linea               smallint NOT NULL,
    producto_id         uuid NOT NULL REFERENCES productos(id),

    -- Unidad en que se VENDIÓ (caja, pieza) y su factor a unidad base.
    unidad_codigo       text NOT NULL REFERENCES unidades_medida(codigo),
    factor_unidad       numeric(14,4) NOT NULL CHECK (factor_unidad > 0),
    cantidad            numeric(14,3) NOT NULL CHECK (cantidad > 0),
    -- Congelado al vender: es lo que descontó del camión.
    cantidad_base       numeric(14,3) NOT NULL CHECK (cantidad_base > 0),

    precio_unitario     numeric(14,4) NOT NULL CHECK (precio_unitario >= 0),
    descuento           numeric(14,2) NOT NULL DEFAULT 0 CHECK (descuento >= 0),
    promocion_id        uuid REFERENCES promociones(id),
    tasa_iva            numeric(5,4) NOT NULL DEFAULT 0,
    importe             numeric(14,2) NOT NULL,
    lote                text,

    UNIQUE (venta_id, linea)
);

CREATE INDEX idx_partidas_producto ON venta_partidas(producto_id);

-- -----------------------------------------------------------------------------
-- Cancelaciones (documento compensatorio — nunca UPDATE sobre la venta)
-- -----------------------------------------------------------------------------
CREATE TABLE ventas_cancelaciones (
    id                  uuid PRIMARY KEY,
    venta_id            uuid NOT NULL REFERENCES ventas(id),
    motivo              text NOT NULL,
    reingresa_stock     boolean NOT NULL DEFAULT true,
    autorizado_por      uuid REFERENCES usuarios(id),   -- PIN de supervisor
    usuario_id          uuid NOT NULL REFERENCES usuarios(id),
    dispositivo_id      uuid REFERENCES dispositivos(id),
    fecha_dispositivo   timestamptz,
    fecha_servidor      timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_cancelacion_venta ON ventas_cancelaciones(venta_id);

-- =============================================================================
-- Cartera y cobranza
-- =============================================================================
-- El teléfono NUNCA calcula el saldo definitivo: registra el abono y muestra
-- un saldo en caché con su marca de antigüedad. El servidor es la única
-- autoridad sobre la cartera.
-- =============================================================================

CREATE TABLE cuentas_por_cobrar (
    venta_id            uuid PRIMARY KEY REFERENCES ventas(id),
    cliente_id          uuid NOT NULL REFERENCES clientes(id),
    importe_original    numeric(14,2) NOT NULL CHECK (importe_original > 0),
    importe_pagado      numeric(14,2) NOT NULL DEFAULT 0 CHECK (importe_pagado >= 0),
    saldo               numeric(14,2)
                        GENERATED ALWAYS AS (importe_original - importe_pagado) STORED,
    fecha_emision       date NOT NULL,
    fecha_vencimiento   date NOT NULL,
    estado              text NOT NULL DEFAULT 'abierta'
                        CHECK (estado IN ('abierta','parcial','liquidada','incobrable')),
    actualizado_en      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pago_no_excede_original CHECK (importe_pagado <= importe_original)
);

CREATE INDEX idx_cxc_cliente ON cuentas_por_cobrar(cliente_id) WHERE estado <> 'liquidada';
CREATE INDEX idx_cxc_vencidas
    ON cuentas_por_cobrar(fecha_vencimiento) WHERE estado IN ('abierta','parcial');

-- Cobro = documento del dispositivo. Su aplicación a facturas la decide el
-- servidor (FIFO), no el teléfono.
CREATE TABLE cobros (
    id                  uuid PRIMARY KEY,          -- UUID del dispositivo
    dispositivo_id      uuid NOT NULL REFERENCES dispositivos(id),
    folio_consecutivo   integer NOT NULL CHECK (folio_consecutivo > 0),
    folio_local         text NOT NULL,

    cliente_id          uuid NOT NULL REFERENCES clientes(id),
    vendedor_id         uuid NOT NULL REFERENCES usuarios(id),
    visita_id           uuid,

    importe             numeric(14,2) NOT NULL CHECK (importe > 0),
    forma_pago          text NOT NULL DEFAULT 'efectivo'
                        CHECK (forma_pago IN ('efectivo','transferencia','cheque')),
    referencia          text,

    -- Saldo que el teléfono TENÍA en caché al cobrar. Forense, no autoridad:
    -- si difiere del real, la diferencia es visible en el panel.
    saldo_cache_disp    numeric(14,2),

    -- Sobrante cuando el abono excede la deuda ⇒ saldo a favor, nunca error.
    importe_aplicado    numeric(14,2) NOT NULL DEFAULT 0,
    saldo_a_favor       numeric(14,2) NOT NULL DEFAULT 0,

    lat                 numeric(10,7),
    lng                 numeric(10,7),
    estado              text NOT NULL DEFAULT 'confirmado'
                        CHECK (estado IN ('confirmado','cancelado')),

    fecha_dispositivo   timestamptz NOT NULL,
    fecha_servidor      timestamptz NOT NULL DEFAULT now(),
    fecha_operativa     date NOT NULL,
    impreso             boolean NOT NULL DEFAULT false,
    requiere_revision   boolean NOT NULL DEFAULT false,
    revision_motivos    text[] NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX uq_cobro_folio_dispositivo
    ON cobros(dispositivo_id, folio_consecutivo);
CREATE UNIQUE INDEX uq_cobro_folio_local ON cobros(folio_local);
CREATE INDEX idx_cobros_cliente  ON cobros(cliente_id, fecha_servidor DESC);
CREATE INDEX idx_cobros_operativa ON cobros(fecha_operativa DESC) WHERE estado = 'confirmado';

-- Aplicación FIFO calculada por el SERVIDOR.
CREATE TABLE cobros_aplicaciones (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cobro_id        uuid NOT NULL REFERENCES cobros(id) ON DELETE CASCADE,
    venta_id        uuid NOT NULL REFERENCES cuentas_por_cobrar(venta_id),
    importe         numeric(14,2) NOT NULL CHECK (importe > 0),
    aplicado_en     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (cobro_id, venta_id)
);
