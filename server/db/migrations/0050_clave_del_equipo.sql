-- =============================================================================
-- 0050 · La clave corta para vincular el teléfono
-- =============================================================================
-- Retroalimentación del piloto (octubre 2026, ADR 0002 §85). Para vincular un
-- teléfono había que teclear en él el id del equipo: 36 caracteres
-- («3f2a9c1e-…»), a mano, en la bodega. Se equivocaban y había que empezar de
-- nuevo.
--
-- Ahora la oficina le pone al equipo una clave corta que elige ella —«RUTA4»,
-- «BRYAN-1»— o deja que el panel invente una de seis letras. El teléfono la
-- manda en el login junto con el usuario y la contraseña del vendedor, y el
-- servidor le contesta con el id del equipo. El id largo sigue sirviendo.
--
-- No es un secreto: sin la contraseña del vendedor, y sin ser SU equipo, no
-- abre nada. Solo tiene que ser única —se guarda en mayúsculas, así que no
-- distingue—; al revocar un equipo su clave se borra y se puede volver a usar.
-- =============================================================================

ALTER TABLE dispositivos ADD COLUMN clave_vinculo text
    CHECK (clave_vinculo ~ '^[A-Z0-9-]{4,20}$');

COMMENT ON COLUMN dispositivos.clave_vinculo IS
    'Clave corta que la oficina eligió para vincular el teléfono (en mayúsculas). '
    'El login la cambia por el id del equipo.';

CREATE UNIQUE INDEX uq_dispositivos_clave_vinculo
    ON dispositivos (clave_vinculo) WHERE clave_vinculo IS NOT NULL;
