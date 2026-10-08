-- =============================================================================
-- 0049 · La ubicación del cliente, con GPS o escrita a mano
-- =============================================================================
-- Retroalimentación del piloto (octubre 2026, ADR 0002 §84). En el perfil del
-- cliente hay dos formas de dar su ubicación: un botón que toma el GPS —si se
-- está parado en el negocio— y los campos de latitud y longitud, para meterla
-- o corregirla a mano (copiada de un mapa, dictada por teléfono).
--
-- Lo hace el vendedor desde su teléfono —sin señal, por la cola, solo de los
-- clientes de su ruta— y la oficina desde el panel o desde su app. La columna
-- `ubicacion` es generada (0003): basta con escribir `lat` y `lng`.
-- =============================================================================

INSERT INTO permisos (codigo, descripcion, modulo) VALUES
 ('clientes.ubicar', 'Fijar o corregir la ubicación de un cliente', 'clientes')
ON CONFLICT (codigo) DO NOTHING;

INSERT INTO roles_permisos (rol_codigo, permiso_codigo) VALUES
 ('admin',      'clientes.ubicar'),
 ('supervisor', 'clientes.ubicar'),
 ('gerente',    'clientes.ubicar'),
 ('vendedor',   'clientes.ubicar')
ON CONFLICT DO NOTHING;
