-- =============================================================================
-- Rol restringido con el que la API habla con PostgreSQL (Fase 9)
-- =============================================================================
-- Es el rol que hace que las políticas por renglón de la migración 0022 SIRVAN.
--
-- En PostgreSQL, el dueño de una tabla salta sus propias políticas. Los
-- workers, el CLI y Alembic se conectan con el rol dueño y tienen que seguir
-- haciéndolo: son procesos de confianza que no pertenecen a ninguna ruta y que
-- deben ver todo. La API es otra cosa — atiende peticiones de teléfonos en la
-- calle— y se conecta con `dsd_api`, que:
--
--   · NO es dueño de nada,
--   · NO tiene BYPASSRLS,
--   · NO puede crear ni alterar objetos,
--   · y sí puede leer y escribir los datos de operación, dentro del alcance que
--     le fije cada petición.
--
-- Cómo se aplica:
--
--   psql -d dsd -v clave_api="$DSD_CLAVE_API" -f db/ops/rol_api.sql
--
-- Y luego, en el .env del despliegue:
--
--   DSD_DATABASE_URL_API=postgresql+psycopg://dsd_api:LA_CLAVE@postgres:5432/dsd
--
-- Sin esa variable la API usa el rol dueño y RLS queda escrito pero sin efecto.
-- `/salud` lo reporta en `rls: false`, y en producción la API no arranca.
-- =============================================================================

\set clave_api :clave_api

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dsd_api') THEN
        CREATE ROLE dsd_api LOGIN;
    END IF;
END $$;

ALTER ROLE dsd_api WITH PASSWORD :'clave_api';

-- NOBYPASSRLS explícito. Es el valor por omisión, y se escribe porque es la
-- propiedad de la que depende todo lo demás: con BYPASSRLS, este rol vería la
-- cartera completa y la migración 0022 sería decoración.
ALTER ROLE dsd_api WITH NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE;

-- `:"DBNAME"` y no 'dsd' literal: la base de pruebas se llama `dsd_test`, y
-- este archivo se aplica tal cual en las pruebas de RLS para que lo que se
-- prueba sea ESTE archivo y no una copia suya que puede separarse.
GRANT CONNECT ON DATABASE :"DBNAME" TO dsd_api;
GRANT USAGE ON SCHEMA public TO dsd_api;

-- Lectura y escritura de datos, nada de DDL.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO dsd_api;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO dsd_api;

-- Las tablas y secuencias que cree una migración futura nacen con el mismo
-- acceso. Sin esto, la primera migración después de crear el rol dejaría la API
-- con un "permission denied" en una tabla nueva — y el síntoma aparecería en
-- producción, al desplegar, no en desarrollo.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dsd_api;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO dsd_api;

-- TRUNCATE no, ni aquí ni por omisión: no hay ningún camino de la API que deba
-- vaciar una tabla, y es la operación que no deja rastro en el libro mayor.
REVOKE TRUNCATE ON ALL TABLES IN SCHEMA public FROM dsd_api;

-- -----------------------------------------------------------------------------
-- Las funciones del alcance
-- -----------------------------------------------------------------------------
-- `EXECUTE` sobre las tres funciones que leen `dsd.*`. En PostgreSQL las
-- funciones nacen con EXECUTE para PUBLIC, así que esto es explícito más que
-- necesario — y lo es a propósito: si algún día se revoca PUBLIC en bloque,
-- este GRANT es lo que evita que la API se quede sin poder evaluar sus propias
-- políticas y vea TODO vacío.
GRANT EXECUTE ON FUNCTION dsd_rol() TO dsd_api;
GRANT EXECUTE ON FUNCTION dsd_usuario() TO dsd_api;
GRANT EXECUTE ON FUNCTION dsd_rutas() TO dsd_api;
GRANT EXECUTE ON FUNCTION dsd_ve_todo() TO dsd_api;

-- -----------------------------------------------------------------------------
-- Límites
-- -----------------------------------------------------------------------------
-- Una petición de la API que tarde más de 30 segundos ya falló para quien la
-- hizo: el teléfono cortó a los 30 (ver `TransporteHttp.tiempoLimite`). Sin
-- este tope, la consulta seguiría ocupando una conexión del pool y un CPU del
-- servidor de oficina mucho después de que nadie esperara su resultado.
ALTER ROLE dsd_api SET statement_timeout = '30s';

-- Una transacción abierta y ociosa bloquea `VACUUM` y retiene versiones de
-- fila. Un minuto es holgado para cualquier petición y corta el caso de una
-- conexión que quedó a medias por un corte de red.
ALTER ROLE dsd_api SET idle_in_transaction_session_timeout = '60s';

-- -----------------------------------------------------------------------------
-- Comprobación
-- -----------------------------------------------------------------------------
-- Lo que debe salir: rolbypassrls = f. Si sale t, el rol salta las políticas y
-- hay que arreglarlo antes de seguir.
SELECT rolname, rolbypassrls, rolsuper, rolcreatedb
  FROM pg_roles WHERE rolname = 'dsd_api';
