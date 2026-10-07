-- =============================================================================
-- 0044 · El gerente también carga camiones
-- =============================================================================
-- Pedido en operación (octubre 2026): «las cargas quiero hacerlas también en la
-- app, pero solo para los administradores y puestos de arriba, no para los
-- vendedores».
--
-- `inventario.cargar` lo tenían el admin (todo) y el supervisor (migración
-- 0009). El gerente, que está por encima del supervisor, no: podía ver el
-- inventario y ajustarlo, pero no subirle mercancía a un camión. Se le da.
--
-- El vendedor sigue sin él, y es la regla de siempre: cargarse su propio camión
-- sería firmar su propia entrega.
-- =============================================================================

INSERT INTO roles_permisos (rol_codigo, permiso_codigo) VALUES
 ('gerente', 'inventario.cargar')
ON CONFLICT DO NOTHING;
