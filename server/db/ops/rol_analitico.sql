-- =============================================================================
-- 0009 · Rol de solo lectura para el laboratorio analítico (Streamlit)
-- =============================================================================
-- Streamlit ejecuta consultas exploratorias escritas a vuelapluma. Con un rol
-- sin permisos de escritura, una consulta mal escrita en el laboratorio no
-- puede tocar la cartera ni el inventario.
--
-- La contraseña se inyecta al aplicar la migración:
--   psql -v clave_analitica="$DSD_CLAVE_ANALITICA" -f 0009_rol_analitico.sql
-- =============================================================================

\set clave_analitica :clave_analitica

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dsd_analitica') THEN
        CREATE ROLE dsd_analitica LOGIN;
    END IF;
END $$;

ALTER ROLE dsd_analitica WITH PASSWORD :'clave_analitica';

GRANT CONNECT ON DATABASE dsd TO dsd_analitica;
GRANT USAGE ON SCHEMA public TO dsd_analitica;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO dsd_analitica;

-- Las tablas que se creen después también nacen legibles, y solo legibles.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO dsd_analitica;

-- Sin INSERT/UPDATE/DELETE, sin uso de secuencias, sin ejecutar funciones que
-- escriban. Revocación explícita por si algún GRANT anterior fue más amplio.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public
    FROM dsd_analitica;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM dsd_analitica;

-- Una consulta exploratoria que se va de las manos no debe tumbar la operación.
ALTER ROLE dsd_analitica SET statement_timeout = '60s';
ALTER ROLE dsd_analitica SET idle_in_transaction_session_timeout = '30s';
