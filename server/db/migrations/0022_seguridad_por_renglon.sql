-- ===========================================================================
-- 0022 · Fase 9 — Seguridad por renglón (RLS)
-- ===========================================================================
-- El filtrado por ruta ya existe y funciona: está en `app/api/deps.py` y en el
-- `WHERE` de cada consulta. Esto no lo reemplaza. Es la SEGUNDA cerradura.
--
-- ---------------------------------------------------------------------------
-- QUÉ PROTEGE, EXACTAMENTE
-- ---------------------------------------------------------------------------
-- No protege de un atacante con la contraseña de la base: quien tiene el rol
-- dueño salta las políticas por diseño. Protege de lo que de verdad pasa en un
-- sistema que sigue creciendo:
--
--   · **Una consulta nueva que olvida el filtro.** Es el error más común y el
--     más silencioso: la pantalla funciona, se ve bien, y devuelve clientes de
--     otra ruta. Con RLS devuelve vacío, que es un bug que alguien reporta el
--     mismo día.
--   · **Una inyección SQL.** Si algún día una entra por un parámetro mal
--     tratado, lo que puede leer queda acotado al alcance de quien hizo la
--     petición, no a la cartera completa.
--   · **Un `JOIN` que se lleva más de lo que debía.** Las políticas se aplican
--     a cada tabla del JOIN por separado.
--
-- ---------------------------------------------------------------------------
-- CÓMO SABE POSTGRESQL QUIÉN PREGUNTA
-- ---------------------------------------------------------------------------
-- Por tres variables de sesión que la API fija en cada petición
-- (`app/core/db.py:fijar_alcance`):
--
--     dsd.rol          'vendedor' | 'supervisor' | 'gerente' | 'admin' | 'anonimo'
--     dsd.usuario_id   el UUID del usuario
--     dsd.rutas        los UUID de sus rutas, separados por coma
--
-- Y hay un detalle que decide si esto sirve o no: **`anonimo` no significa "sin
-- restricción", significa "nada"**. `obtener_sesion` fija el alcance anónimo al
-- abrir la sesión, ANTES de cualquier consulta, así que una conexión reciclada
-- del pool nunca conserva el alcance de la petición anterior, y un endpoint que
-- se olvide de autenticar no ve nada en vez de verlo todo. Fallo cerrado.
--
-- ---------------------------------------------------------------------------
-- POR QUÉ HACEN FALTA DOS ROLES DE BASE DE DATOS
-- ---------------------------------------------------------------------------
-- En PostgreSQL, **el dueño de una tabla salta sus políticas** (salvo con FORCE
-- ROW LEVEL SECURITY, que aquí no se usa a propósito). Eso es exactamente lo
-- que se quiere para los workers, el CLI y Alembic: son procesos de confianza,
-- no pertenecen a ninguna ruta, y tienen que ver todo.
--
-- Así que la API se conecta con un rol distinto, `dsd_api`, que NO es dueño de
-- nada y NO tiene BYPASSRLS. Se crea con `db/ops/rol_api.sql` y se pasa por
-- `DSD_DATABASE_URL_API`. Sin esa variable las políticas quedan escritas y sin
-- efecto — se permite en desarrollo, `/salud` lo reporta en `rls: false`, y en
-- producción la API **no arranca** (ver `app/core/config.py`).
--
-- ---------------------------------------------------------------------------
-- QUÉ NO LLEVA POLÍTICAS, Y POR QUÉ
-- ---------------------------------------------------------------------------
-- **Las tablas de identidad** (`usuarios`, `dispositivos`, `sesiones`, `roles`,
-- `permisos`, `roles_permisos`, `usuarios_rutas`, `usuarios_permisos`). No es
-- un descuido: la autenticación las lee **antes** de saber quién manda la
-- petición —es lo que averigua—, así que con el alcance todavía en `anonimo`.
-- Ponerlas bajo política dejaría la API sin poder autenticar a nadie. Y no
-- cargan datos de clientes: lo peor que contienen es un hash Argon2id, que es
-- lo que el teléfono necesita para el login offline.
--
-- **El catálogo** (productos, precios, unidades, listas). Lo ve todo el mundo
-- por diseño: el vendedor necesita el catálogo completo en el camión.
--
-- **Las tablas de infraestructura** (`jobs`, `sync_lotes`, `sync_operaciones`,
-- `sync_cuarentena`, `auditoria`) y **las de analítica y tablero**. Las primeras
-- no tienen dueño de ruta; las segundas están protegidas por permiso
-- (`analitica.ver`, `tablero.ver`) y son agregados, no renglones de clientes.
--
-- **Las vistas.** `v_cartera_cliente` se evalúa con los privilegios de su
-- dueño, así que las políticas de sus tablas base NO se aplican al consultarla.
-- Está documentado aquí porque es contraintuitivo y porque es la frontera de lo
-- que esta migración cubre: en ese camino, el guardia sigue siendo el
-- `alcanza_ruta()` del endpoint. Cambiarlo a `security_invoker` es posible y se
-- deja para cuando el pull de sincronización pueda probarse bajo el rol
-- restringido de punta a punta.
-- ===========================================================================


-- ===========================================================================
-- Las tres funciones que leen el alcance
-- ===========================================================================
-- `current_setting(..., true)` con el segundo argumento en `true`: devuelve
-- NULL si la variable no está puesta en vez de lanzar un error. Sin eso,
-- cualquier conexión que no fijara el alcance —un `psql` manual, una
-- herramienta de respaldo— fallaría con un error oscuro en vez de simplemente
-- no ver nada.
--
-- `STABLE` y no `IMMUTABLE`: el valor depende de la sesión. Marcarla IMMUTABLE
-- dejaría que el planificador la cachee entre peticiones distintas de la misma
-- conexión, que es el bug más peligroso que podría tener este archivo.
--
-- `SECURITY INVOKER` (el valor por omisión, explícito aquí para que se lea):
-- una función de política que corriera como su dueño podría usarse para leer lo
-- que la política niega.
CREATE OR REPLACE FUNCTION dsd_rol() RETURNS text
    LANGUAGE sql STABLE SECURITY INVOKER
    AS $$ SELECT COALESCE(current_setting('dsd.rol', true), 'anonimo') $$;

CREATE OR REPLACE FUNCTION dsd_usuario() RETURNS uuid
    LANGUAGE sql STABLE SECURITY INVOKER
    AS $$
    SELECT CASE
             WHEN COALESCE(current_setting('dsd.usuario_id', true), '') = '' THEN NULL
             ELSE current_setting('dsd.usuario_id', true)::uuid
           END
    $$;

CREATE OR REPLACE FUNCTION dsd_rutas() RETURNS uuid[]
    LANGUAGE sql STABLE SECURITY INVOKER
    AS $$
    SELECT CASE
             WHEN COALESCE(current_setting('dsd.rutas', true), '') = ''
             THEN ARRAY[]::uuid[]
             ELSE string_to_array(current_setting('dsd.rutas', true), ',')::uuid[]
           END
    $$;

-- Quién ve la operación completa.
--
-- La lista es la misma que `Actor.alcanza_ruta()` en `deps.py` más 'admin', y
-- esa duplicación es deliberada: si las dos se separaran, la de aquí es la que
-- manda, porque es la que PostgreSQL aplica. Hay una prueba que compara las dos
-- listas para que no se separen en silencio.
CREATE OR REPLACE FUNCTION dsd_ve_todo() RETURNS boolean
    LANGUAGE sql STABLE SECURITY INVOKER
    AS $$ SELECT dsd_rol() IN ('admin', 'gerente', 'supervisor') $$;

COMMENT ON FUNCTION dsd_ve_todo() IS
    'Roles de oficina: ven la operación completa. La lista debe coincidir con '
    'Actor.alcanza_ruta() en app/api/deps.py; hay una prueba que lo verifica.';


-- ===========================================================================
-- clientes
-- ===========================================================================
-- El vendedor ve los clientes de SUS rutas. Un cliente sin ruta no lo ve nadie
-- más que la oficina — y eso ya era así antes de RLS: el filtro de la API usa
-- `ruta_id IN (...)`, que también excluye el NULL. Un prospecto de calle nace
-- con la ruta del vendedor que lo capturó, así que no cae en ese hueco.
ALTER TABLE clientes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS clientes_alcance ON clientes;
CREATE POLICY clientes_alcance ON clientes
    FOR SELECT
    USING (dsd_ve_todo() OR ruta_id = ANY (dsd_rutas()));

DROP POLICY IF EXISTS clientes_alta ON clientes;
CREATE POLICY clientes_alta ON clientes
    FOR INSERT
    -- `ruta_id IS NULL` se permite al insertar: un alta de oficina puede no
    -- tener ruta todavía. Lo que no se permite es dar de alta un cliente EN
    -- OTRA ruta, que es el caso que importa.
    WITH CHECK (dsd_ve_todo() OR ruta_id IS NULL OR ruta_id = ANY (dsd_rutas()));

DROP POLICY IF EXISTS clientes_cambio ON clientes;
CREATE POLICY clientes_cambio ON clientes
    FOR UPDATE
    USING (dsd_ve_todo() OR ruta_id = ANY (dsd_rutas()))
    WITH CHECK (dsd_ve_todo() OR ruta_id = ANY (dsd_rutas()));

-- Sin política de DELETE: nadie borra clientes. Se marcan de baja
-- (`estatus = 'baja'`), y la ausencia de política es una negación.


-- ===========================================================================
-- ventas y sus partidas
-- ===========================================================================
-- Dos caminos de alcance, unidos con OR, y los dos hacen falta:
--
--   · `vendedor_id = dsd_usuario()` — sus propias ventas, aunque la venta no
--     traiga ruta (es nullable).
--   · `ruta_id = ANY(dsd_rutas())` — las de su ruta aunque las haya capturado
--     otro, que pasa cuando alguien cubre una ruta ajena.
ALTER TABLE ventas ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS ventas_alcance ON ventas;
CREATE POLICY ventas_alcance ON ventas
    FOR SELECT
    USING (
        dsd_ve_todo()
        OR vendedor_id = dsd_usuario()
        OR ruta_id = ANY (dsd_rutas())
    );

DROP POLICY IF EXISTS ventas_alta ON ventas;
CREATE POLICY ventas_alta ON ventas
    FOR INSERT
    -- Al INSERTAR se exige que la venta sea del propio vendedor. Es la mitad
    -- que importa: sin esto, un equipo comprometido podría escribir ventas a
    -- nombre de otro, y la auditoría señalaría a la persona equivocada.
    WITH CHECK (dsd_ve_todo() OR vendedor_id = dsd_usuario());

DROP POLICY IF EXISTS ventas_cambio ON ventas;
CREATE POLICY ventas_cambio ON ventas
    FOR UPDATE
    USING (dsd_ve_todo() OR vendedor_id = dsd_usuario())
    WITH CHECK (dsd_ve_todo() OR vendedor_id = dsd_usuario());

-- Las partidas heredan el alcance de su venta. El EXISTS se evalúa con el
-- mismo usuario, así que la política de `ventas` también se aplica dentro: una
-- partida es visible exactamente cuando su venta lo es.
ALTER TABLE venta_partidas ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS venta_partidas_alcance ON venta_partidas;
CREATE POLICY venta_partidas_alcance ON venta_partidas
    FOR ALL
    USING (
        dsd_ve_todo()
        OR EXISTS (SELECT 1 FROM ventas v WHERE v.id = venta_partidas.venta_id)
    )
    WITH CHECK (
        dsd_ve_todo()
        OR EXISTS (SELECT 1 FROM ventas v WHERE v.id = venta_partidas.venta_id)
    );


-- ===========================================================================
-- cobranza
-- ===========================================================================
-- Un cobro no tiene ruta: tiene vendedor. El alcance es «lo que yo cobré».
ALTER TABLE cobros ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS cobros_alcance ON cobros;
CREATE POLICY cobros_alcance ON cobros
    FOR SELECT USING (dsd_ve_todo() OR vendedor_id = dsd_usuario());

DROP POLICY IF EXISTS cobros_alta ON cobros;
CREATE POLICY cobros_alta ON cobros
    FOR INSERT WITH CHECK (dsd_ve_todo() OR vendedor_id = dsd_usuario());

DROP POLICY IF EXISTS cobros_cambio ON cobros;
CREATE POLICY cobros_cambio ON cobros
    FOR UPDATE
    USING (dsd_ve_todo() OR vendedor_id = dsd_usuario())
    WITH CHECK (dsd_ve_todo() OR vendedor_id = dsd_usuario());

ALTER TABLE cobros_aplicaciones ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS cobros_aplicaciones_alcance ON cobros_aplicaciones;
CREATE POLICY cobros_aplicaciones_alcance ON cobros_aplicaciones
    FOR ALL
    USING (
        dsd_ve_todo()
        OR EXISTS (SELECT 1 FROM cobros c WHERE c.id = cobros_aplicaciones.cobro_id)
    )
    WITH CHECK (
        dsd_ve_todo()
        OR EXISTS (SELECT 1 FROM cobros c WHERE c.id = cobros_aplicaciones.cobro_id)
    );

-- La cartera de un cliente es visible si el cliente lo es. El EXISTS pasa por
-- la política de `clientes`, así que no hay que repetir aquí la regla de rutas.
ALTER TABLE cuentas_por_cobrar ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS cuentas_alcance ON cuentas_por_cobrar;
CREATE POLICY cuentas_alcance ON cuentas_por_cobrar
    FOR ALL
    USING (
        dsd_ve_todo()
        OR EXISTS (SELECT 1 FROM clientes c WHERE c.id = cuentas_por_cobrar.cliente_id)
    )
    WITH CHECK (
        dsd_ve_todo()
        OR EXISTS (SELECT 1 FROM clientes c WHERE c.id = cuentas_por_cobrar.cliente_id)
    );


-- ===========================================================================
-- Operaciones secundarias
-- ===========================================================================
ALTER TABLE no_drops ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS no_drops_alcance ON no_drops;
CREATE POLICY no_drops_alcance ON no_drops
    FOR SELECT
    USING (
        dsd_ve_todo()
        OR vendedor_id = dsd_usuario()
        OR ruta_id = ANY (dsd_rutas())
    );

DROP POLICY IF EXISTS no_drops_alta ON no_drops;
CREATE POLICY no_drops_alta ON no_drops
    FOR INSERT WITH CHECK (dsd_ve_todo() OR vendedor_id = dsd_usuario());

DROP POLICY IF EXISTS no_drops_cambio ON no_drops;
CREATE POLICY no_drops_cambio ON no_drops
    FOR UPDATE
    USING (dsd_ve_todo() OR vendedor_id = dsd_usuario())
    WITH CHECK (dsd_ve_todo() OR vendedor_id = dsd_usuario());

ALTER TABLE mermas ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS mermas_alcance ON mermas;
CREATE POLICY mermas_alcance ON mermas
    FOR SELECT USING (dsd_ve_todo() OR vendedor_id = dsd_usuario());

DROP POLICY IF EXISTS mermas_alta ON mermas;
CREATE POLICY mermas_alta ON mermas
    FOR INSERT WITH CHECK (dsd_ve_todo() OR vendedor_id = dsd_usuario());

DROP POLICY IF EXISTS mermas_cambio ON mermas;
CREATE POLICY mermas_cambio ON mermas
    FOR UPDATE
    USING (dsd_ve_todo() OR vendedor_id = dsd_usuario())
    WITH CHECK (dsd_ve_todo() OR vendedor_id = dsd_usuario());

ALTER TABLE merma_detalle ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS merma_detalle_alcance ON merma_detalle;
CREATE POLICY merma_detalle_alcance ON merma_detalle
    FOR ALL
    USING (
        dsd_ve_todo()
        OR EXISTS (SELECT 1 FROM mermas m WHERE m.id = merma_detalle.merma_id)
    )
    WITH CHECK (
        dsd_ve_todo()
        OR EXISTS (SELECT 1 FROM mermas m WHERE m.id = merma_detalle.merma_id)
    );


-- ===========================================================================
-- change_log: lo que el teléfono se lleva en el pull
-- ===========================================================================
-- Es la tabla más importante de esta migración, porque es la que un teléfono
-- lee ENTERA si el filtro falla: `change_log` contiene el payload de cada
-- cliente, precio y saldo que el servidor publica.
--
-- La política repite exactamente el `WHERE` del pull (`app/api/v1/sync.py`), y
-- esa duplicación es el punto: si alguien edita la consulta y se le cae una de
-- las dos condiciones, PostgreSQL sigue aplicando las dos.
--
-- `ruta_id IS NULL` pasa porque son las filas compartidas —catálogo, precios,
-- motivos—: el vendedor necesita el catálogo completo en el camión.
ALTER TABLE change_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS change_log_alcance ON change_log;
CREATE POLICY change_log_alcance ON change_log
    FOR SELECT
    USING (
        dsd_ve_todo()
        OR (
            -- `dsd_usuario() IS NOT NULL` es la puerta, y la encontró una
            -- prueba. Sin ella, las filas compartidas —catálogo y PRECIOS—
            -- pasaban con alcance anónimo: `ruta_id IS NULL` es verdadero y
            -- `vendedor_id IS NULL` también, así que una conexión sin
            -- autenticar podía leer la lista de precios completa.
            --
            -- Se exige un usuario y no un rol concreto a propósito: cualquier
            -- perfil con dispositivo registrado hace pull, no solo 'vendedor'.
            dsd_usuario() IS NOT NULL
            AND (ruta_id IS NULL OR ruta_id = ANY (dsd_rutas()))
            AND (vendedor_id IS NULL OR vendedor_id = dsd_usuario())
        )
    );

-- Sin política de INSERT: al `change_log` solo escriben los disparadores, que
-- corren con los privilegios del dueño de la tabla y por lo tanto saltan RLS.
-- Que la API no pueda insertar ahí directamente es correcto.
