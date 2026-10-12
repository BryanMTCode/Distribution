-- =============================================================================
-- 0058 · La revisión de la app, punto por punto
-- =============================================================================
-- Pedido de la dirección (octubre 2026, ADR 0002 §112): «agrega al dashboard un
-- checklist de todo lo que debo checar en la app para ver que las
-- funcionalidades están bien».
--
-- La lista de puntos vive en código (`app/infra/revision_app.py`): describe las
-- pantallas de cada versión y cambia junto con ellas. Aquí solo van las MARCAS:
-- por versión de la app («0.1.0+35») y por punto, si salió bien o falló, con su
-- nota, quién y cuándo. Una versión nueva empieza en blanco.
--
-- No es operación: «poner la base en blanco» no la toca.
-- =============================================================================

CREATE TABLE IF NOT EXISTS revision_app (
    version       text NOT NULL,
    -- La clave estable del punto en `revision_app.py`.
    punto         text NOT NULL,
    estado        text NOT NULL CHECK (estado IN ('bien', 'falla')),
    -- Obligatoria en una falla: es lo que hay que arreglar.
    nota          text,
    revisado_por  uuid REFERENCES usuarios(id) ON DELETE SET NULL,
    revisado_en   timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (version, punto),
    CONSTRAINT revision_falla_con_nota CHECK (estado <> 'falla' OR nota IS NOT NULL)
);

COMMENT ON TABLE revision_app IS
    'Las marcas de la revisión de la app: por versión y punto (la lista está en '
    'app/infra/revision_app.py), bien o falla con su nota. ADR 0002 §112.';
