-- =============================================================================
-- 0001 · Extensiones, identidad, RBAC y dispositivos
-- =============================================================================
-- Convenciones del proyecto:
--   · cantidad  numeric(14,3)   (permite fracciones de caja / granel)
--   · precio    numeric(14,4)   (precisión de lista antes de redondeo)
--   · importe   numeric(14,2)   (dinero; siempre MXN en v1)
--   · Toda tabla escrita por el dispositivo usa UUID generado EN EL DISPOSITIVO
--     como PK. El servidor no reasigna llaves. Eso es lo que hace idempotente
--     el ingest.
--   · fecha_dispositivo = reloj del teléfono (forense).
--     fecha_servidor    = reloj del servidor  (autoridad de negocio).
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS postgis;    -- geography(Point,4326)
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- detección de clientes duplicados
CREATE EXTENSION IF NOT EXISTS btree_gist; -- EXCLUDE sobre uuid + rango (0007)

-- -----------------------------------------------------------------------------
-- Estructura organizacional
-- -----------------------------------------------------------------------------
CREATE TABLE sucursales (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo          text NOT NULL UNIQUE,
    nombre          text NOT NULL,
    direccion       text,
    activo          boolean NOT NULL DEFAULT true,
    creado_en       timestamptz NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- Identidad y RBAC
-- -----------------------------------------------------------------------------
CREATE TABLE roles (
    codigo          text PRIMARY KEY
                    CHECK (codigo IN ('vendedor','supervisor','gerente','admin')),
    nombre          text NOT NULL,
    descripcion     text
);

CREATE TABLE permisos (
    codigo          text PRIMARY KEY,   -- p.ej. 'ventas.crear', 'inventario.ajustar'
    descripcion     text NOT NULL,
    modulo          text NOT NULL
);

CREATE TABLE roles_permisos (
    rol_codigo      text NOT NULL REFERENCES roles(codigo) ON DELETE CASCADE,
    permiso_codigo  text NOT NULL REFERENCES permisos(codigo) ON DELETE CASCADE,
    PRIMARY KEY (rol_codigo, permiso_codigo)
);

CREATE TABLE usuarios (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sucursal_id         uuid REFERENCES sucursales(id),
    codigo              text NOT NULL UNIQUE,      -- 'VEND01' — prefijo de folio local
    nombre              text NOT NULL,
    email               text UNIQUE,
    telefono            text,
    -- Argon2id. El MISMO hash se replica al dispositivo para permitir login offline.
    password_hash       text NOT NULL,
    rol_codigo          text NOT NULL REFERENCES roles(codigo),
    -- Almacén propio (camión) para perfiles de vendedor. NULL para gerencia.
    almacen_id          uuid,                      -- FK diferida a almacenes (0004)
    activo              boolean NOT NULL DEFAULT true,
    -- Días máximos que el dispositivo puede operar sin sincronizar antes de
    -- exigir login online. Ver §2.1 del documento de arquitectura.
    dias_max_offline    smallint NOT NULL DEFAULT 7 CHECK (dias_max_offline BETWEEN 1 AND 30),
    creado_en           timestamptz NOT NULL DEFAULT now(),
    actualizado_en      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_usuarios_rol ON usuarios(rol_codigo) WHERE activo;

-- Permisos extra o revocados a nivel usuario (excepciones sobre el rol).
CREATE TABLE usuarios_permisos (
    usuario_id      uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    permiso_codigo  text NOT NULL REFERENCES permisos(codigo) ON DELETE CASCADE,
    otorgado        boolean NOT NULL,   -- false = revocación explícita
    PRIMARY KEY (usuario_id, permiso_codigo)
);

-- -----------------------------------------------------------------------------
-- Dispositivos
-- -----------------------------------------------------------------------------
-- Un dispositivo es la unidad de confianza y el espacio de nombres de los
-- folios locales. Sin registro previo no se acepta sincronización.
-- -----------------------------------------------------------------------------
CREATE TABLE dispositivos (
    id                      uuid PRIMARY KEY,          -- generado en el dispositivo
    usuario_id              uuid NOT NULL REFERENCES usuarios(id),
    etiqueta                text NOT NULL,             -- 'Moto G54 — Bryan'
    modelo                  text,
    so_version              text,
    app_version             text,
    -- Perfil de impresora ESC/POS (ancho de papel, MAC emparejada).
    impresora_mac           text,
    impresora_ancho_mm      smallint CHECK (impresora_ancho_mm IN (58, 80)),
    estado                  text NOT NULL DEFAULT 'activo'
                            CHECK (estado IN ('activo','suspendido','revocado')),
    -- Lista de revocación: un equipo robado se marca aquí y sus tokens mueren.
    revocado_en             timestamptz,
    revocado_motivo         text,
    ultima_sync_push_en     timestamptz,
    ultima_sync_pull_en     timestamptz,
    ultimo_cursor_pull      bigint NOT NULL DEFAULT 0,
    registrado_en           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT dispositivo_revocado_con_fecha
        CHECK ((estado = 'revocado') = (revocado_en IS NOT NULL))
);

-- Un usuario opera un solo equipo a la vez (regla de negocio, no técnica).
CREATE UNIQUE INDEX uq_dispositivo_activo_por_usuario
    ON dispositivos(usuario_id) WHERE estado = 'activo';

CREATE TABLE sesiones (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id          uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    dispositivo_id      uuid REFERENCES dispositivos(id) ON DELETE CASCADE,
    refresh_token_hash  text NOT NULL,
    expira_en           timestamptz NOT NULL,
    revocada_en         timestamptz,
    ip                  inet,
    creada_en           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_sesiones_usuario ON sesiones(usuario_id) WHERE revocada_en IS NULL;

-- -----------------------------------------------------------------------------
-- Auditoría transversal
-- -----------------------------------------------------------------------------
CREATE TABLE auditoria (
    id              bigserial PRIMARY KEY,
    entidad         text NOT NULL,
    entidad_id      uuid,
    accion          text NOT NULL,      -- 'crear','cancelar','ajustar','revocar'...
    usuario_id      uuid REFERENCES usuarios(id),
    dispositivo_id  uuid REFERENCES dispositivos(id),
    datos_antes     jsonb,
    datos_despues   jsonb,
    motivo          text,
    ocurrido_en     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_auditoria_entidad ON auditoria(entidad, entidad_id);
CREATE INDEX idx_auditoria_fecha   ON auditoria(ocurrido_en DESC);
