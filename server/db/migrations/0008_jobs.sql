-- =============================================================================
-- 0008 · Cola de trabajos en PostgreSQL
-- =============================================================================
-- Sin Redis en la v1 (ver ADR 0001). `FOR UPDATE SKIP LOCKED` da una cola con
-- múltiples consumidores sin duplicar trabajo, y —lo que ningún broker externo
-- puede ofrecer— **encolar el job queda dentro de la misma transacción que la
-- escritura de negocio**. O se guardó la venta y se encoló su procesamiento, o
-- no ocurrió ninguna de las dos cosas.
-- =============================================================================

CREATE TABLE jobs (
    id              bigserial PRIMARY KEY,
    tipo            text NOT NULL,
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb,

    estado          text NOT NULL DEFAULT 'pendiente'
                    CHECK (estado IN ('pendiente','ejecutando','hecho','fallido')),
    prioridad       smallint NOT NULL DEFAULT 100,   -- menor = antes

    intentos        integer NOT NULL DEFAULT 0,
    max_intentos    integer NOT NULL DEFAULT 5,
    ejecutar_en     timestamptz NOT NULL DEFAULT now(),   -- backoff exponencial

    -- Encolado idempotente: 'refrescar_vistas:2026-09-15' no se duplica aunque
    -- el evento que lo dispara llegue tres veces.
    clave_unica     text,

    tomado_por      text,          -- identificador del worker, para forense
    tomado_en       timestamptz,
    ultimo_error    text,

    creado_en       timestamptz NOT NULL DEFAULT now(),
    terminado_en    timestamptz
);

-- El índice que sirve al SELECT ... FOR UPDATE SKIP LOCKED.
CREATE INDEX idx_jobs_cola
    ON jobs(prioridad, ejecutar_en, id)
    WHERE estado = 'pendiente';

CREATE INDEX idx_jobs_fallidos ON jobs(creado_en DESC) WHERE estado = 'fallido';

-- Un job pendiente o en ejecución no se duplica; los ya terminados no estorban
-- para volver a encolar la misma clave mañana.
CREATE UNIQUE INDEX uq_jobs_clave_activa
    ON jobs(clave_unica)
    WHERE clave_unica IS NOT NULL AND estado IN ('pendiente','ejecutando');

COMMENT ON TABLE jobs IS
    'Cola de trabajos. Se toma con SELECT ... FOR UPDATE SKIP LOCKED; ver '
    'app/workers/cola.py. Un job ejecutándose cuyo worker murió se recupera '
    'por tiempo (tomado_en), no por heartbeat.';
