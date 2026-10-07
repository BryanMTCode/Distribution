-- =============================================================================
-- 0045 · El gerente también hace el corte del día
-- =============================================================================
-- Con el corte desde la app (octubre 2026), la misma regla que la 0044 para las
-- cargas: lo hacen los puestos de arriba. `inventario.liquidar` lo tenían el
-- admin (todo) y el supervisor; el gerente, que está por encima, no.
--
-- El vendedor sigue sin él: cortarse su propio camión sería firmar su propio
-- conteo.
-- =============================================================================

INSERT INTO roles_permisos (rol_codigo, permiso_codigo) VALUES
 ('gerente', 'inventario.liquidar')
ON CONFLICT DO NOTHING;
