-- =============================================================================
-- 0062 · El código de un usuario se puede cambiar, y el viejo queda reservado
-- =============================================================================
-- Pedido de la dirección (octubre 2026, ADR 0002 §132): «quiero poder cambiar la
-- cuenta de los usuarios que ya tengo registrados: si antes se llamaban VEND000,
-- ahora cambiarla a la que yo quiera, todo desde el dashboard».
--
-- Hasta aquí no se podía (0001, 0035, `panel/equipo`): el código es el prefijo
-- del folio impreso, `VEND01-000077`. El NÚMERO es consecutivo por vendedor
-- —cada uno empieza en 1—, así que lo que hace único al folio es el prefijo. Si
-- Juan suelta «VEND01» y luego lo toma Pedro, el primer ticket de Pedro sale
-- «VEND01-000001», igual a uno de Juan: la base lo rechaza por duplicado y en la
-- calle hay dos papeles con el mismo número.
--
-- Se puede cambiar si el código viejo **se queda apartado para esa persona**:
--
--   · Juan pasa de VEND01 a JUAN; sus folios siguen su misma cuenta, ya sea con
--     el prefijo viejo (su teléfono, hasta que vuelva a entrar con señal) o con
--     el nuevo. Ninguno se repite.
--   · Nadie más puede tomar VEND01, ni al cambiar su código ni al darse de alta.
--   · Juan sí puede volver a VEND01: su numeración sigue donde iba.
--
-- Esta tabla es esa reserva, y de paso el historial de cada quién. Se vacía
-- con la base en blanco para operar (§131): sin folios no hay qué proteger.
-- =============================================================================

CREATE TABLE IF NOT EXISTS usuarios_codigos_anteriores (
    codigo        text PRIMARY KEY,
    usuario_id    uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    hasta         timestamptz NOT NULL DEFAULT now(),
    cambiado_por  uuid REFERENCES usuarios(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_codigos_anteriores_usuario
    ON usuarios_codigos_anteriores (usuario_id);

COMMENT ON TABLE usuarios_codigos_anteriores IS
    'Códigos que un usuario tuvo antes. Quedan apartados para él: son el prefijo '
    'de folios que ya están impresos (ADR 0002 §132).';
