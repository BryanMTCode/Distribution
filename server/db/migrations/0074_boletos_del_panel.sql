-- =============================================================================
-- 0074 · El boleto para entrar al dashboard desde la app de la oficina
-- =============================================================================
-- Pedido de la dirección (octubre 2026, ADR 0002 §194): «una versión de la app en
-- que el administrador pueda hacer todos los movimientos que hace en el
-- dashboard, sin tener que actualizar la app a los vendedores».
--
-- La app de la oficina abre el mismo dashboard dentro de ella. Para no pedir la
-- contraseña otra vez, la app —que ya tiene su sesión— pide un boleto, y con él
-- el dashboard abre su sesión de siempre: la cookie, con su fila en `sesiones`.
--
-- El boleto sirve UNA vez y durante un minuto. Se guarda su hash, como las
-- sesiones: quien se lleve un respaldo no se lleva boletos vivos. Es una tabla
-- nueva y nada más: lo que usan los teléfonos de los vendedores no cambia.
-- =============================================================================

CREATE TABLE IF NOT EXISTS boletos_panel (
    hash        text PRIMARY KEY,
    usuario_id  uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    expira_en   timestamptz NOT NULL,
    usado_en    timestamptz,
    creado_en   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_boletos_panel_expira ON boletos_panel (expira_en);

COMMENT ON TABLE boletos_panel IS
    'Boletos de un solo uso, de un minuto, para que la app de la oficina abra el '
    'dashboard con la sesión que ya tiene (ADR 0002 §194). Se guarda el hash.';
