-- =============================================================================
-- 0002 · Catálogo: productos, unidades, conversiones, listas de precios
-- =============================================================================
-- Propiedad del dato: SERVIDOR. En el dispositivo estas tablas son de SOLO
-- LECTURA. El servidor siempre gana. Por eso no requieren resolución de
-- conflictos.
-- =============================================================================

CREATE TABLE categorias (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo          text NOT NULL UNIQUE,
    nombre          text NOT NULL,
    padre_id        uuid REFERENCES categorias(id),
    orden           smallint NOT NULL DEFAULT 0,
    activo          boolean NOT NULL DEFAULT true
);

CREATE TABLE marcas (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nombre          text NOT NULL UNIQUE,
    activo          boolean NOT NULL DEFAULT true
);

CREATE TABLE unidades_medida (
    codigo          text PRIMARY KEY,           -- 'PZA','CAJA','KG','DISPLAY'
    nombre          text NOT NULL,
    fraccionable    boolean NOT NULL DEFAULT false,   -- KG sí, PZA no
    clave_sat       text                              -- para CFDI futuro
);

CREATE TABLE productos (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sku                 text NOT NULL UNIQUE,
    codigo_barras       text UNIQUE,
    nombre              text NOT NULL,
    descripcion         text,
    categoria_id        uuid REFERENCES categorias(id),
    marca_id            uuid REFERENCES marcas(id),
    -- Unidad base: TODO el inventario se lleva en esta unidad. Las conversiones
    -- viven en producto_unidades. Esta es la decisión que evita descuadres por
    -- cajas vs piezas.
    unidad_base         text NOT NULL REFERENCES unidades_medida(codigo),
    peso_gramos         integer,
    tasa_iva            numeric(5,4) NOT NULL DEFAULT 0.0000,  -- abarrotes: 0 o 0.16
    tasa_ieps           numeric(5,4) NOT NULL DEFAULT 0.0000,
    maneja_lote         boolean NOT NULL DEFAULT false,
    dias_caducidad      integer,
    activo              boolean NOT NULL DEFAULT true,
    creado_en           timestamptz NOT NULL DEFAULT now(),
    actualizado_en      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_productos_categoria ON productos(categoria_id) WHERE activo;
CREATE INDEX idx_productos_nombre_trgm ON productos USING gin (nombre gin_trgm_ops);

-- Presentaciones vendibles: 1 CAJA = 24 PZA, 1 DISPLAY = 6 PZA...
CREATE TABLE producto_unidades (
    producto_id     uuid NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    unidad_codigo   text NOT NULL REFERENCES unidades_medida(codigo),
    factor          numeric(14,4) NOT NULL CHECK (factor > 0),  -- cuántas unidades base
    es_default      boolean NOT NULL DEFAULT false,
    activo          boolean NOT NULL DEFAULT true,
    PRIMARY KEY (producto_id, unidad_codigo)
);

CREATE UNIQUE INDEX uq_producto_unidad_default
    ON producto_unidades(producto_id) WHERE es_default;

-- -----------------------------------------------------------------------------
-- Listas de precios
-- -----------------------------------------------------------------------------
-- El precio se resuelve en el DISPOSITIVO con la lista sincronizada. El
-- servidor revalida al recibir: si el precio cambió, NO rechaza la venta;
-- marca requiere_revision. Ver §0.1 del documento de arquitectura.
-- -----------------------------------------------------------------------------
CREATE TABLE listas_precios (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo          text NOT NULL UNIQUE,
    nombre          text NOT NULL,       -- 'Mayoreo', 'Menudeo', 'Cadena'
    es_default      boolean NOT NULL DEFAULT false,
    vigente_desde   date NOT NULL DEFAULT CURRENT_DATE,
    vigente_hasta   date,
    activo          boolean NOT NULL DEFAULT true,
    CONSTRAINT vigencia_coherente CHECK (vigente_hasta IS NULL OR vigente_hasta >= vigente_desde)
);

CREATE UNIQUE INDEX uq_lista_precios_default ON listas_precios(es_default) WHERE es_default;

CREATE TABLE precios (
    lista_id        uuid NOT NULL REFERENCES listas_precios(id) ON DELETE CASCADE,
    producto_id     uuid NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    unidad_codigo   text NOT NULL REFERENCES unidades_medida(codigo),
    precio          numeric(14,4) NOT NULL CHECK (precio >= 0),
    precio_minimo   numeric(14,4) CHECK (precio_minimo IS NULL OR precio_minimo >= 0),
    -- Versión que viaja al dispositivo; permite detectar si la venta se hizo
    -- con una lista obsoleta sin comparar importes.
    version         integer NOT NULL DEFAULT 1,
    actualizado_en  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (lista_id, producto_id, unidad_codigo),
    FOREIGN KEY (producto_id, unidad_codigo)
        REFERENCES producto_unidades(producto_id, unidad_codigo)
);

-- Promociones: se aplican offline con las reglas sincronizadas.
CREATE TABLE promociones (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo              text NOT NULL UNIQUE,
    nombre              text NOT NULL,
    tipo                text NOT NULL
                        CHECK (tipo IN ('descuento_pct','descuento_monto','nxm','regalo')),
    -- Parámetros según tipo. nxm => {"compra":3,"paga":2}
    parametros          jsonb NOT NULL DEFAULT '{}'::jsonb,
    producto_id         uuid REFERENCES productos(id),
    categoria_id        uuid REFERENCES categorias(id),
    lista_precios_id    uuid REFERENCES listas_precios(id),
    vigente_desde       date NOT NULL,
    vigente_hasta       date NOT NULL,
    activo              boolean NOT NULL DEFAULT true,
    CONSTRAINT promo_tiene_alcance
        CHECK (producto_id IS NOT NULL OR categoria_id IS NOT NULL),
    CONSTRAINT promo_vigencia CHECK (vigente_hasta >= vigente_desde)
);
