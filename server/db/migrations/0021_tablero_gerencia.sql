-- ===========================================================================
-- 0021 · Fase 7 — Modelos de lectura del tablero de Gerencia
-- ===========================================================================
-- El tablero de Gerencia (app móvil) tiene un requisito que ninguna otra
-- pantalla del sistema tiene: **se abre muchas veces al día y las cifras tienen
-- que salir en un segundo, en un teléfono, con el servidor recibiendo ocho
-- camiones a la vez**.
--
-- ---------------------------------------------------------------------------
-- POR QUÉ NO SE LEE DIRECTO DE `ventas`
-- ---------------------------------------------------------------------------
-- Las cifras del tablero son AGREGADOS: suma del día, visitas del día,
-- efectividad, avance del mes. Calcularlos al vuelo significa barrer `ventas`,
-- `venta_partidas`, `no_drops`, `cobros` y `cuentas_por_cobrar` **en cada
-- apertura de la pantalla**. Tres gerentes con la app abierta y un `pull to
-- refresh` nervioso son decenas de barridos por minuto sobre las mismas tablas
-- en las que están escribiendo los camiones, en una mini PC de oficina. El
-- síntoma no sería "el tablero va lento": sería que la SINCRONIZACIÓN va lenta,
-- y entonces el vendedor espera en la calle por una pantalla que alguien está
-- mirando en la oficina. Exactamente al revés de lo que importa.
--
-- ---------------------------------------------------------------------------
-- POR QUÉ NO SIRVEN LAS VISTAS MATERIALIZADAS DE LA FASE 8
-- ---------------------------------------------------------------------------
-- Ya existe un esquema estrella (migración 0020) y sería tentador leer de ahí.
-- No sirve, y la razón es la CADENCIA: esas vistas se refrescan al **cerrar la
-- liquidación**, que es cuando las cifras del día quedan firmes. Un gerente que
-- abre el tablero a las 11 de la mañana vería ceros, porque la última
-- liquidación cerrada es la de ayer.
--
-- Las dos cosas son correctas y distintas:
--
--     esquema estrella (0020)   cifras FIRMES para analizar  → al cerrar el día
--     tablero (este archivo)    cifras VIVAS para monitorear → al sincronizar
--
-- ---------------------------------------------------------------------------
-- CÓMO SE MANTIENE AL DÍA SIN VOLVERSE UN ETL INCREMENTAL
-- ---------------------------------------------------------------------------
-- El job `recalcular_tablero` NO carga "lo nuevo desde la última corrida" —ese
-- es el error que la 0020 documenta largamente, y en un DSD los datos llegan
-- tarde por diseño (§0.3)—. Lo que hace es:
--
--   1. Buscar qué DÍAS OPERATIVOS quedaron rancios: aquellos con algún
--      documento cuyo `fecha_servidor` es posterior al `calculado_en` de su
--      renglón. Una venta del lunes que sincroniza el jueves deja el LUNES
--      rancio, no el jueves.
--   2. Recalcular ese día COMPLETO desde la verdad transaccional.
--
-- La diferencia con un incremental es la que importa: `fecha_servidor` decide
-- QUÉ DÍAS recalcular, nunca qué renglones sumar. Cada día recalculado se suma
-- entero, así que su cifra siempre está completa respecto de lo que ha
-- sincronizado — y eso es todo lo que se puede prometer (§0.3).
--
-- Efecto lateral que importa: el job es AUTORREPARABLE. Si el worker estuvo
-- caído dos horas, la siguiente corrida encuentra los días rancios por sí sola;
-- nadie tiene que acordarse de encolar las fechas correctas.
--
-- ---------------------------------------------------------------------------
-- EL GRANO ES (DÍA, VENDEDOR) Y NO (DÍA, RUTA)
-- ---------------------------------------------------------------------------
-- Parece un detalle y no lo es: `ventas.ruta_id` y `no_drops.ruta_id` son
-- NULLABLE, y `mermas` no tiene ruta en absoluto —una merma pertenece a un
-- camión, no a una ruta; la caja se revienta entre dos tiendas—. Con grano por
-- ruta, el total del día sería la suma de los renglones MÁS un cajón de
-- "sin ruta" que alguien olvidaría sumar. Un total que no cuadra con su
-- desglose destruye la confianza en el tablero completo.
--
-- `vendedor_id` es NOT NULL en ventas, cobros, no_drops y mermas. El grano por
-- vendedor es COMPLETO: la suma de los renglones del día ES el día.
--
-- El avance por ruta vive en su propia tabla (`tablero_mes_ruta`) porque su
-- comparación es mensual —el objetivo es mensual— y porque ahí sí se acepta
-- perder los documentos sin ruta: se cuenta y se muestra cuántos fueron.
-- ===========================================================================


-- ===========================================================================
-- objetivos_ruta — contra qué se mide
-- ===========================================================================
-- Sin esta tabla, la tarjeta "avance vs objetivo" no puede existir. Es el mismo
-- hueco que tuvieron los catálogos de motivos en la Fase 6: la pantalla estaba
-- lista y nada publicaba el dato, así que la pantalla no servía.
--
-- El periodo es el PRIMER DÍA DEL MES, no un par (año, mes): así se compara con
-- `date_trunc('month', ...)` sin aritmética y el tipo impide un mes 13.
CREATE TABLE IF NOT EXISTS objetivos_ruta (
    ruta_id         uuid NOT NULL REFERENCES rutas(id) ON DELETE CASCADE,
    periodo         date NOT NULL,
    objetivo_venta  numeric(14,2) NOT NULL CHECK (objetivo_venta >= 0),
    -- Opcional a propósito: una ruta puede tener meta de dinero y no de
    -- visitas. Un 0 significaría "la meta es no visitar a nadie".
    objetivo_visitas integer CHECK (objetivo_visitas IS NULL OR objetivo_visitas > 0),
    nota            text,
    fijado_por      uuid REFERENCES usuarios(id),
    creado_en       timestamptz NOT NULL DEFAULT now(),
    actualizado_en  timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (ruta_id, periodo),
    -- El periodo tiene que ser el día 1. Sin esto, dos renglones del mismo mes
    -- (día 1 y día 15) convivirían y el avance se mediría contra uno de los dos
    -- al azar.
    CONSTRAINT periodo_es_inicio_de_mes
        CHECK (periodo = date_trunc('month', periodo)::date)
);

COMMENT ON TABLE objetivos_ruta IS
    'Objetivo mensual por ruta. El periodo es el día 1 del mes. Se fija desde '
    'el panel; sin él la tarjeta de avance del tablero no tiene contra qué '
    'comparar y no se muestra.';


-- ===========================================================================
-- tablero_dia — el flujo del día, por vendedor
-- ===========================================================================
-- Un renglón por (fecha operativa, vendedor). Lo recalcula
-- `app/workers/tablero.py` por día completo.
CREATE TABLE IF NOT EXISTS tablero_dia (
    fecha                   date NOT NULL,
    vendedor_id             uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,

    -- Venta ------------------------------------------------------------
    venta_total             numeric(14,2) NOT NULL DEFAULT 0,
    venta_contado           numeric(14,2) NOT NULL DEFAULT 0,
    venta_credito           numeric(14,2) NOT NULL DEFAULT 0,
    documentos_venta        integer NOT NULL DEFAULT 0,

    -- Visita: CLIENTE-DÍA, no documento (misma definición que
    -- `fact_visitas` y que la pantalla de efectividad; está escrita una sola
    -- vez, en app/domain/tablero.py).
    visitas                 integer NOT NULL DEFAULT 0,
    visitas_con_venta       integer NOT NULL DEFAULT 0,

    -- No-drops ---------------------------------------------------------
    no_drops                integer NOT NULL DEFAULT 0,
    -- Los que la empresa puede arreglar: categorías 'operacion', 'producto'
    -- y 'vendedor'. 'cliente' es el mundo.
    no_drops_nuestros       integer NOT NULL DEFAULT 0,

    -- Cobranza ---------------------------------------------------------
    cobrado_total           numeric(14,2) NOT NULL DEFAULT 0,
    -- El efectivo se separa porque es el único que entra al arqueo de la
    -- liquidación: una transferencia no se cuenta en la bolsa.
    cobrado_efectivo        numeric(14,2) NOT NULL DEFAULT 0,

    -- Mermas -----------------------------------------------------------
    mermas_documentos       integer NOT NULL DEFAULT 0,
    mermas_unidades         numeric(14,3) NOT NULL DEFAULT 0,

    -- Cuándo se calculó ESTE renglón. Es lo que la tarjeta muestra como
    -- antigüedad; no es decoración (ver el comentario de tablero_refrescos).
    calculado_en            timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (fecha, vendedor_id)
);

CREATE INDEX IF NOT EXISTS idx_tablero_dia_fecha ON tablero_dia (fecha DESC);

COMMENT ON TABLE tablero_dia IS
    'Modelo de lectura del tablero: flujo de un día operativo por vendedor. '
    'Grano completo (vendedor_id es NOT NULL en los cuatro documentos), así '
    'que la suma de sus renglones ES el día.';


-- ===========================================================================
-- tablero_mes_ruta — avance del mes por ruta, para comparar con el objetivo
-- ===========================================================================
CREATE TABLE IF NOT EXISTS tablero_mes_ruta (
    periodo                 date NOT NULL,     -- día 1 del mes
    ruta_id                 uuid NOT NULL REFERENCES rutas(id) ON DELETE CASCADE,

    venta_mes               numeric(14,2) NOT NULL DEFAULT 0,
    visitas_mes             integer NOT NULL DEFAULT 0,
    visitas_con_venta_mes   integer NOT NULL DEFAULT 0,
    -- Días en que esa ruta facturó algo. Sirve para proyectar: 20 días hábiles
    -- del mes y venta en 8 no es lo mismo que venta en 18.
    dias_con_venta          integer NOT NULL DEFAULT 0,
    clientes_distintos      integer NOT NULL DEFAULT 0,

    calculado_en            timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (periodo, ruta_id),
    CONSTRAINT periodo_mes_es_inicio_de_mes
        CHECK (periodo = date_trunc('month', periodo)::date)
);

COMMENT ON TABLE tablero_mes_ruta IS
    'Avance del mes por ruta. Grano PARCIAL: deja fuera los documentos sin '
    'ruta_id, y por eso el tablero muestra cuántos fueron en vez de callarlo.';


-- ===========================================================================
-- tablero_cartera — un solo renglón, porque la cartera es un SALDO
-- ===========================================================================
-- Aquí está la distinción que más confunde en un tablero, y que vale la pena
-- dejar escrita: `tablero_dia` guarda FLUJOS (lo que pasó ese día) y esta tabla
-- guarda un SALDO (lo que se debe AHORA). Un saldo no tiene fecha operativa:
-- guardarlo en el renglón de hoy haría que el "vencido del 3 de marzo" cambiara
-- cada vez que se recalcula marzo, y nadie podría explicar por qué.
--
-- Un solo renglón (`id = true`): la cartera es una sola. Partirla por ruta
-- obligaría a decidir qué hacer con los clientes sin ruta, y un vencido
-- incompleto es peor que no tenerlo.
CREATE TABLE IF NOT EXISTS tablero_cartera (
    id                  boolean PRIMARY KEY DEFAULT true CHECK (id),

    saldo_total         numeric(14,2) NOT NULL DEFAULT 0,
    saldo_vencido       numeric(14,2) NOT NULL DEFAULT 0,
    -- Tramos de antigüedad, los mismos que la pantalla de cobranza del panel.
    vencido_1_15        numeric(14,2) NOT NULL DEFAULT 0,
    vencido_16_30       numeric(14,2) NOT NULL DEFAULT 0,
    vencido_31_60       numeric(14,2) NOT NULL DEFAULT 0,
    vencido_61_mas      numeric(14,2) NOT NULL DEFAULT 0,

    facturas_abiertas   integer NOT NULL DEFAULT 0,
    facturas_vencidas   integer NOT NULL DEFAULT 0,
    clientes_con_saldo  integer NOT NULL DEFAULT 0,
    clientes_vencidos   integer NOT NULL DEFAULT 0,

    calculado_en        timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE tablero_cartera IS
    'Saldo de cartera al momento del cálculo. Un solo renglón: la cartera es un '
    'SALDO, no un flujo, y no pertenece a ninguna fecha operativa.';


-- ===========================================================================
-- tablero_refrescos — la antigüedad Y el estado del mundo
-- ===========================================================================
-- `calculado_en` sola es una media verdad peligrosa: "actualizado hace 1 min"
-- suena perfecto, y si en ese minuto dos teléfonos no habían subido su día, el
-- total de ventas es un PISO, no un total. Las dos cifras juntas son la única
-- forma honesta de presentar un número en un DSD:
--
--     "Venta de hoy: $42,180 · hace 2 min · 2 equipos sin sincronizar"
--
-- Es el mismo criterio de `analitica_refrescos` (0020), y vive en una tabla
-- aparte porque aquí hay un solo refresco global por corrida, no uno por vista.
CREATE TABLE IF NOT EXISTS tablero_refrescos (
    id                      boolean PRIMARY KEY DEFAULT true CHECK (id),
    calculado_en            timestamptz NOT NULL DEFAULT now(),
    duracion_ms             integer,
    dias_recalculados       integer NOT NULL DEFAULT 0,

    -- Equipos activos que no han hecho push HOY. Es el "no sabemos todavía".
    equipos_sin_sincronizar smallint,
    -- Sobres que los equipos reportaron tener pendientes de subir. Cada uno
    -- puede ser una venta que falta en el total de arriba.
    cola_reportada          integer,
    -- Operaciones que el servidor rechazó y están esperando revisión humana:
    -- dinero que ocurrió en la calle y no está en ninguna cifra.
    ops_en_cuarentena       integer
);

COMMENT ON TABLE tablero_refrescos IS
    'Cuándo se recalculó el tablero y en qué estado estaba el mundo en ese '
    'momento. Sin lo segundo, la antigüedad miente por omisión.';


-- ===========================================================================
-- Permisos
-- ===========================================================================
-- `tablero.ver` es un permiso propio y no un alias de `analitica.ver`: el
-- laboratorio es una herramienta de oficina con la cartera completa a la vista,
-- y el tablero es una pantalla de monitoreo. Que un supervisor pueda ver cómo
-- va el día no implica darle el laboratorio.
INSERT INTO permisos (codigo, descripcion, modulo) VALUES
 ('tablero.ver',           'Ver el tablero de monitoreo',            'analitica'),
 ('objetivos.administrar', 'Fijar objetivos mensuales de ruta',      'analitica')
ON CONFLICT (codigo) DO NOTHING;

-- Gerencia fija los objetivos. No contradice "gerencia es de solo lectura
-- SOBRE LA OPERACIÓN": un objetivo no es una operación —no mueve inventario ni
-- dinero— es el plan contra el que se mide la operación. Si la única persona
-- que mide no pudiera fijar contra qué, la tarjeta de avance quedaría vacía
-- esperando que la oficina se acordara.
INSERT INTO roles_permisos (rol_codigo, permiso_codigo) VALUES
 ('gerente',    'tablero.ver'),
 ('gerente',    'objetivos.administrar'),
 ('supervisor', 'tablero.ver')
ON CONFLICT DO NOTHING;
