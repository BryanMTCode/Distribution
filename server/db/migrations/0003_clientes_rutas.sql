-- =============================================================================
-- 0003 · Rutas, clientes y frecuencia de visita
-- =============================================================================
-- Los clientes son de propiedad MIXTA:
--   · Alta en calle  → dueño = DISPOSITIVO (UUID generado en el teléfono).
--   · Edición de crédito, lista de precios y estatus → dueño = SERVIDOR.
-- El dispositivo nunca edita campos de propiedad del servidor. Sin edición
-- concurrente sobre el mismo campo, no hay conflicto que resolver.
-- =============================================================================

CREATE TABLE rutas (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo          text NOT NULL UNIQUE,          -- 'R04'
    nombre          text NOT NULL,
    sucursal_id     uuid REFERENCES sucursales(id),
    vendedor_id     uuid REFERENCES usuarios(id),  -- titular actual
    activo          boolean NOT NULL DEFAULT true,
    creado_en       timestamptz NOT NULL DEFAULT now()
);

-- Alcance de datos por usuario. El scope guard del backend filtra por aquí.
CREATE TABLE usuarios_rutas (
    usuario_id      uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    ruta_id         uuid NOT NULL REFERENCES rutas(id) ON DELETE CASCADE,
    PRIMARY KEY (usuario_id, ruta_id)
);

CREATE TABLE canales (
    codigo          text PRIMARY KEY,   -- 'ABARROTES','TIENDITA','MERCADO','MAYORISTA'
    nombre          text NOT NULL
);

CREATE TABLE clientes (
    -- UUID generado en el DISPOSITIVO cuando el alta es en calle.
    -- gen_random_uuid() solo cuando el alta nace en el panel web.
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo                  text UNIQUE,            -- consecutivo asignado por el SERVIDOR
    nombre_comercial        text NOT NULL,
    razon_social            text,
    rfc                     text,
    canal_codigo            text REFERENCES canales(codigo),

    ruta_id                 uuid REFERENCES rutas(id),
    -- Orden de visita dentro de la ruta.
    secuencia               integer,

    -- Contacto
    contacto_nombre         text,
    telefono                text,

    -- Domicilio
    calle                   text,
    numero                  text,
    colonia                 text,
    municipio               text,
    estado                  text,
    codigo_postal           text,
    referencias             text,

    -- ------------------------------------------------------------------
    -- Georreferencia. origen='manual' significa que el vendedor ajustó las
    -- coordenadas porque el GPS falló. Es dato auditable, no una anomalía.
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
    ubicacion_origen        text CHECK (ubicacion_origen IN ('gps','manual','geocodificado')),
    ubicacion_capturada_en  timestamptz,

    -- ------------------------------------------------------------------
    -- Condiciones comerciales — PROPIEDAD DEL SERVIDOR.
    -- El dispositivo las lee; nunca las escribe.
    -- ------------------------------------------------------------------
    lista_precios_id        uuid REFERENCES listas_precios(id),
    permite_credito         boolean NOT NULL DEFAULT false,
    limite_credito          numeric(14,2) NOT NULL DEFAULT 0 CHECK (limite_credito >= 0),
    dias_credito            smallint NOT NULL DEFAULT 0 CHECK (dias_credito >= 0),
    bloqueado               boolean NOT NULL DEFAULT false,
    bloqueo_motivo          text,

    estatus                 text NOT NULL DEFAULT 'activo'
                            CHECK (estatus IN ('prospecto','activo','inactivo','baja')),

    -- ------------------------------------------------------------------
    -- Trazabilidad del alta offline
    -- ------------------------------------------------------------------
    origen_alta             text NOT NULL DEFAULT 'oficina'
                            CHECK (origen_alta IN ('oficina','campo')),
    creado_por              uuid REFERENCES usuarios(id),
    dispositivo_id          uuid REFERENCES dispositivos(id),
    fecha_dispositivo       timestamptz,
    creado_en               timestamptz NOT NULL DEFAULT now(),
    actualizado_en          timestamptz NOT NULL DEFAULT now(),

    -- Se marca cuando el alta de campo choca contra las reglas de la oficina
    -- (RFC duplicado, fuera de zona, etc.). Nunca se rechaza el alta.
    requiere_revision       boolean NOT NULL DEFAULT false,
    revision_motivo         text
);

CREATE INDEX idx_clientes_ruta      ON clientes(ruta_id, secuencia) WHERE estatus = 'activo';
CREATE INDEX idx_clientes_ubicacion ON clientes USING gist (ubicacion);
CREATE INDEX idx_clientes_nombre    ON clientes USING gin (nombre_comercial gin_trgm_ops);
CREATE INDEX idx_clientes_revision  ON clientes(creado_en DESC) WHERE requiere_revision;

-- -----------------------------------------------------------------------------
-- Cola de posibles duplicados
-- -----------------------------------------------------------------------------
-- Dos vendedores pueden dar de alta la misma tiendita el mismo día. NO se
-- fusionan automáticamente: se encolan para decisión humana en el panel web.
-- Fusionar por heurística destruye historial de ventas.
-- -----------------------------------------------------------------------------
CREATE TABLE clientes_posibles_duplicados (
    id              bigserial PRIMARY KEY,
    cliente_id      uuid NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    candidato_id    uuid NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    score           numeric(5,4) NOT NULL,     -- similitud trigram + distancia GPS
    distancia_m     numeric(10,2),
    estado          text NOT NULL DEFAULT 'pendiente'
                    CHECK (estado IN ('pendiente','fusionado','descartado')),
    resuelto_por    uuid REFERENCES usuarios(id),
    resuelto_en     timestamptz,
    detectado_en    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT no_autoduplicado CHECK (cliente_id <> candidato_id)
);

CREATE UNIQUE INDEX uq_duplicado_par
    ON clientes_posibles_duplicados (
        LEAST(cliente_id, candidato_id), GREATEST(cliente_id, candidato_id)
    );

-- -----------------------------------------------------------------------------
-- Frecuencia de visita (para medir efectividad y detectar clientes en riesgo)
-- -----------------------------------------------------------------------------
CREATE TABLE clientes_frecuencia (
    id              bigserial PRIMARY KEY,
    cliente_id      uuid NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    dia_semana      smallint NOT NULL CHECK (dia_semana BETWEEN 0 AND 6),  -- 0 = domingo
    semana_del_mes  smallint CHECK (semana_del_mes BETWEEN 1 AND 4)        -- NULL = todas las semanas
);

-- COALESCE en el índice, no en la PK: PostgreSQL no admite expresiones en
-- PRIMARY KEY, y NULL en semana_del_mes debe colisionar consigo mismo.
CREATE UNIQUE INDEX uq_cliente_frecuencia
    ON clientes_frecuencia (cliente_id, dia_semana, COALESCE(semana_del_mes, 0));
