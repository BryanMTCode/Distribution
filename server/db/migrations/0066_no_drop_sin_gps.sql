-- =============================================================================
-- 0066 · La visita sin venta, también sin GPS
-- =============================================================================
-- «Quiero que cuando no les compran puedan cerrar al cliente sin GPS: recuerda
-- que van a trabajar offline» (octubre 2026, ADR 0002 §158).
--
-- El no-drop era el único documento que se rechazaba sin ubicación (0006): sin
-- coordenadas no se distinguía de una visita que nunca se hizo. Pero en la calle
-- el GPS no siempre lee —bajo techo, dentro del mercado, con el teléfono
-- viejo— y entonces el vendedor no podía cerrar al cliente: la tienda se quedaba
-- en su lista como pendiente y el día salía con una visita de menos.
--
-- Ahora entra igual que la venta sin GPS: aceptada y marcada `sin_ubicacion`
-- para que la oficina la vea en revisión. Las dos coordenadas van juntas o
-- ninguna; `ubicacion` (generada) queda nula sin ellas.
-- =============================================================================

ALTER TABLE no_drops ALTER COLUMN lat DROP NOT NULL;
ALTER TABLE no_drops ALTER COLUMN lng DROP NOT NULL;

ALTER TABLE no_drops DROP CONSTRAINT IF EXISTS no_drop_lat_lng_juntas;
ALTER TABLE no_drops ADD CONSTRAINT no_drop_lat_lng_juntas
    CHECK ((lat IS NULL) = (lng IS NULL));
