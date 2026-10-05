-- =============================================================================
-- Los disparadores de `change_log` tienen que correr como su dueño
-- =============================================================================
-- CORRIGE UN FALLO QUE DEJABA EL PANEL INUTILIZABLE EN PRODUCCIÓN.
--
-- La migración 0022 habilitó RLS en `change_log` con una política de SELECT y
-- ninguna de INSERT, y lo justificó así:
--
--   "Sin política de INSERT: al change_log solo escriben los disparadores, que
--    corren con los privilegios del dueño de la tabla y por lo tanto saltan RLS."
--
-- **Esa afirmación es falsa.** En PostgreSQL una función de disparador corre con
-- los privilegios de QUIEN INVOCA, no del dueño de la tabla, salvo que se
-- declare `SECURITY DEFINER` — y ninguna de las cuatro lo estaba. Así que:
--
--   1. la API se conecta como `dsd_api` (sin BYPASSRLS, es el punto de 0022);
--   2. el panel inserta en `productos`;
--   3. el disparador AFTER INSERT corre TAMBIÉN como `dsd_api`;
--   4. intenta insertar en `change_log`, que tiene RLS y ninguna política que
--      permita INSERT;
--   5. PostgreSQL lo rechaza con «new row violates row-level security policy for
--      table "change_log"» y la petición termina en 500.
--
-- Lo que quedaba roto, todo por la misma causa:
--
--   fn_registrar_cambio          clientes, listas_precios, precios,
--                                producto_unidades, productos, promociones
--   fn_registrar_cambio_carga    cargas          ← el camión no podía salir
--   fn_registrar_cambio_cartera  cuentas_por_cobrar ← UNA VENTA A CRÉDITO no
--                                podía sincronizar desde el teléfono
--   fn_registrar_cambio_catalogo_texto  motivos_merma, motivos_no_drop
--
-- El de la cartera es el peor: no se ve en la oficina, se ve en la calle, y el
-- vendedor habría reportado «no sube una venta» sin más detalle.
--
-- Nada de esto aparecía en desarrollo porque ahí la API se conecta con el rol
-- DUEÑO, que salta las políticas de sus propias tablas. Es decir: el fallo solo
-- existe donde RLS está de verdad en uso, que es producción.
--
-- ---------------------------------------------------------------------------
-- EL ARREGLO
-- ---------------------------------------------------------------------------
-- `SECURITY DEFINER` y no una política de INSERT en `change_log`, a propósito:
-- la intención de 0022 —que la API NO pueda escribir en el libro de cambios
-- directamente— es correcta y se conserva. Lo que cambia es el mecanismo por el
-- que el disparador sí puede.
--
-- `SET search_path` no es opcional en una función SECURITY DEFINER: sin él, un
-- rol que pueda crear objetos podría poner su propia tabla `change_log` en un
-- esquema que vaya antes en el search_path y hacer que la función escriba ahí
-- con privilegios de dueño. `pg_temp` va AL FINAL por la misma razón.
--
-- El dueño de estas funciones es quien corre las migraciones, que es el dueño de
-- `change_log`. Un dueño salta las políticas de su tabla salvo que esté
-- `FORCE ROW LEVEL SECURITY`, y no lo está en ninguna tabla de este esquema.
-- =============================================================================

ALTER FUNCTION fn_registrar_cambio() SECURITY DEFINER;
ALTER FUNCTION fn_registrar_cambio() SET search_path = public, pg_temp;

ALTER FUNCTION fn_registrar_cambio_carga() SECURITY DEFINER;
ALTER FUNCTION fn_registrar_cambio_carga() SET search_path = public, pg_temp;

ALTER FUNCTION fn_registrar_cambio_cartera() SECURITY DEFINER;
ALTER FUNCTION fn_registrar_cambio_cartera() SET search_path = public, pg_temp;

ALTER FUNCTION fn_registrar_cambio_catalogo_texto() SECURITY DEFINER;
ALTER FUNCTION fn_registrar_cambio_catalogo_texto() SET search_path = public, pg_temp;

COMMENT ON FUNCTION fn_registrar_cambio() IS
    'Alimenta change_log. SECURITY DEFINER: `change_log` tiene RLS sin política '
    'de INSERT, y un disparador corre como quien invoca —no como el dueño de la '
    'tabla— así que sin esto la API con rol restringido no puede escribir el '
    'catálogo. El cursor del pull es el BIGSERIAL de esa tabla, nunca un '
    'timestamp: con relojes desincronizados y transacciones concurrentes, un '
    'cursor por fecha pierde registros en silencio.';
