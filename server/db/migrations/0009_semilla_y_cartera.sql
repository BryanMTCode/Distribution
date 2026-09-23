-- =============================================================================
-- 0009 · Datos de referencia y vista de cartera
-- =============================================================================
-- Datos de referencia = los que el sistema necesita para arrancar y que no
-- captura el usuario: roles, permisos, unidades, motivos. Van en migración
-- porque el código depende de sus códigos literales.
--
-- Todo con ON CONFLICT DO NOTHING: reaplicar no duplica ni pisa cambios que la
-- oficina haya hecho después.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Roles
-- -----------------------------------------------------------------------------
INSERT INTO roles (codigo, nombre, descripcion) VALUES
 ('vendedor',   'Vendedor',      'Opera una ruta con su camión. Trabaja offline.'),
 ('supervisor', 'Supervisor',    'Autoriza cancelaciones y ajustes; ve varias rutas.'),
 ('gerente',    'Gerencia',      'Monitoreo y análisis. Solo lectura sobre la operación.'),
 ('admin',      'Administrador', 'Configuración del sistema. Todos los permisos.')
ON CONFLICT (codigo) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Permisos
-- -----------------------------------------------------------------------------
INSERT INTO permisos (codigo, descripcion, modulo) VALUES
 ('catalogo.ver',              'Consultar productos y precios',          'catalogo'),
 ('catalogo.administrar',      'Alta y edición de productos y precios',  'catalogo'),
 ('clientes.ver',              'Consultar clientes',                     'clientes'),
 ('clientes.crear',            'Dar de alta clientes en campo',          'clientes'),
 ('clientes.administrar',      'Editar condiciones comerciales',         'clientes'),
 ('clientes.fusionar',         'Resolver clientes duplicados',           'clientes'),
 ('ventas.crear',              'Registrar ventas',                       'ventas'),
 ('ventas.cancelar',           'Cancelar una venta con documento',       'ventas'),
 ('ventas.ver_todas',          'Ver ventas de cualquier ruta',           'ventas'),
 ('cobranza.registrar',        'Registrar abonos',                       'cobranza'),
 ('cobranza.ver',              'Consultar cartera',                      'cobranza'),
 ('inventario.ver',            'Consultar existencias',                  'inventario'),
 ('inventario.cargar',         'Confirmar cargas de camión',             'inventario'),
 ('inventario.liquidar',       'Cerrar liquidaciones',                   'inventario'),
 ('inventario.ajustar',        'Ajustes autorizados de inventario',      'inventario'),
 ('mermas.registrar',          'Registrar mermas y devoluciones',        'operaciones'),
 ('nodrops.registrar',         'Registrar visitas sin venta',            'operaciones'),
 ('sync.cuarentena',           'Resolver operaciones en cuarentena',     'sistema'),
 ('dispositivos.administrar',  'Registrar y revocar dispositivos',       'sistema'),
 ('usuarios.administrar',      'Alta y edición de usuarios',             'sistema'),
 ('analitica.ver',             'Acceder al laboratorio analítico',       'analitica')
ON CONFLICT (codigo) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Qué puede cada rol
-- -----------------------------------------------------------------------------
-- El vendedor NO tiene 'clientes.administrar': las condiciones de crédito son
-- propiedad del servidor (ver la tabla de propiedad del dato en §2.3). Tampoco
-- 'ventas.cancelar': una cancelación exige PIN de supervisor.
INSERT INTO roles_permisos (rol_codigo, permiso_codigo) VALUES
 ('vendedor', 'catalogo.ver'),
 ('vendedor', 'clientes.ver'),
 ('vendedor', 'clientes.crear'),
 ('vendedor', 'ventas.crear'),
 ('vendedor', 'cobranza.registrar'),
 ('vendedor', 'cobranza.ver'),
 ('vendedor', 'inventario.ver'),
 ('vendedor', 'mermas.registrar'),
 ('vendedor', 'nodrops.registrar'),

 ('supervisor', 'catalogo.ver'),
 ('supervisor', 'clientes.ver'),
 ('supervisor', 'clientes.crear'),
 ('supervisor', 'clientes.administrar'),
 ('supervisor', 'clientes.fusionar'),
 ('supervisor', 'ventas.crear'),
 ('supervisor', 'ventas.cancelar'),
 ('supervisor', 'ventas.ver_todas'),
 ('supervisor', 'cobranza.registrar'),
 ('supervisor', 'cobranza.ver'),
 ('supervisor', 'inventario.ver'),
 ('supervisor', 'inventario.cargar'),
 ('supervisor', 'inventario.liquidar'),
 ('supervisor', 'mermas.registrar'),
 ('supervisor', 'sync.cuarentena'),

 -- Gerencia es de SOLO LECTURA sobre la operación. Monitorea, no opera.
 ('gerente', 'catalogo.ver'),
 ('gerente', 'clientes.ver'),
 ('gerente', 'ventas.ver_todas'),
 ('gerente', 'cobranza.ver'),
 ('gerente', 'inventario.ver'),
 ('gerente', 'analitica.ver')
ON CONFLICT DO NOTHING;

-- El rol 'admin' no se enumera: el scope guard le concede todo (ver
-- app/api/deps.py, Actor.puede). Enumerarlo sería una lista que se olvida de
-- actualizar y que daría una falsa sensación de control.

-- -----------------------------------------------------------------------------
-- Unidades de medida
-- -----------------------------------------------------------------------------
-- Solo PIEZA y CAJA (ADR 0002). Sin granel: ningún producto se vende por peso,
-- así que ninguna unidad es fraccionable.
INSERT INTO unidades_medida (codigo, nombre, fraccionable, clave_sat) VALUES
 ('PZA',  'Pieza', false, 'H87'),
 ('CAJA', 'Caja',  false, 'XBX')
ON CONFLICT (codigo) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Canales de cliente
-- -----------------------------------------------------------------------------
INSERT INTO canales (codigo, nombre) VALUES
 ('TIENDITA',  'Tienda de abarrotes'),
 ('MERCADO',   'Puesto de mercado'),
 ('MAYORISTA', 'Mayorista'),
 ('ESCUELA',   'Escuela o cooperativa'),
 ('OTRO',      'Otro')
ON CONFLICT (codigo) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Motivos de no-drop  (CATÁLOGO CERRADO a propósito)
-- -----------------------------------------------------------------------------
-- Texto libre = datos que nunca vas a poder analizar. La categoría es lo que
-- después permite preguntar "¿cuántas visitas perdidas son culpa nuestra?".
-- Revísalos con los vendedores antes del piloto: el catálogo se escribe con
-- ellos, no en el escritorio.
INSERT INTO motivos_no_drop (codigo, nombre, categoria, requiere_nota, orden) VALUES
 ('CERRADO',            'Cerrado',                          'cliente',   false, 10),
 ('DUENO_AUSENTE',      'No estaba quien decide',           'cliente',   false, 20),
 ('SIN_DINERO',         'No tiene dinero hoy',              'cliente',   false, 30),
 ('TIENE_INVENTARIO',   'Todavía tiene producto',           'cliente',   false, 40),
 ('NO_LE_INTERESA',     'No le interesa el producto',       'cliente',   true,  50),
 ('SIN_CREDITO',        'Excedió su límite de crédito',     'operacion', false, 60),
 ('AGOTADO_EN_CAMION',  'No traigo lo que pidió',           'producto',  true,  70),
 ('PRECIO_ALTO',        'Le pareció caro',                  'producto',  true,  80),
 ('CLIENTE_NO_EXISTE',  'El negocio ya no existe',          'cliente',   true,  90),
 ('NO_SE_VISITO',       'No alcancé a visitarlo',           'vendedor',  true, 100)
ON CONFLICT (codigo) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Motivos de merma
-- -----------------------------------------------------------------------------
-- `afecta_vendedor` decide si la pérdida se le carga en la liquidación.
INSERT INTO motivos_merma (codigo, nombre, afecta_vendedor) VALUES
 ('CADUCADO',            'Producto caducado',            false),
 ('DANADO_TRANSPORTE',   'Dañado en el transporte',      true),
 ('DANADO_BODEGA',       'Dañado en bodega',             false),
 ('ROTO',                'Empaque roto',                 true),
 ('DEVOLUCION_CLIENTE',  'Devolución del cliente',       false),
 ('ROBO',                'Robo o extravío',              true),
 ('MUESTRA',             'Muestra o degustación',        false)
ON CONFLICT (codigo) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Lista de precios por defecto
-- -----------------------------------------------------------------------------
INSERT INTO listas_precios (codigo, nombre, es_default)
VALUES ('GENERAL', 'Precio general', true)
ON CONFLICT (codigo) DO NOTHING;

-- =============================================================================
-- Vista de cartera por cliente
-- =============================================================================
-- El dispositivo necesita UN número por cliente, no la lista de facturas: con
-- el saldo y el límite arma la validación local antes de cerrar el carrito.
-- Es también lo que alimenta el bloqueo automático (ADR 0002).
--
-- `saldo` puede ser negativo: es saldo a favor de un cliente que pagó de más,
-- y suma línea disponible.
-- =============================================================================
CREATE OR REPLACE VIEW v_cartera_cliente AS
SELECT
    c.id                                        AS cliente_id,
    c.codigo,
    c.nombre_comercial,
    c.ruta_id,
    c.permite_credito,
    c.bloqueado,
    c.limite_credito,
    c.dias_credito,
    COALESCE(cxc.saldo, 0)::numeric(14,2)       AS saldo,
    GREATEST(c.limite_credito - COALESCE(cxc.saldo, 0), 0)::numeric(14,2) AS disponible,
    -- Las tres banderas que el vendedor necesita ver en la pantalla del
    -- cliente antes de ofrecerle mercancía.
    (c.bloqueado OR NOT c.permite_credito
        OR COALESCE(cxc.saldo, 0) >= c.limite_credito)  AS credito_agotado,
    COALESCE(cxc.facturas_abiertas, 0)          AS facturas_abiertas,
    COALESCE(cxc.vencidas, 0)                   AS facturas_vencidas,
    COALESCE(cxc.saldo_vencido, 0)::numeric(14,2) AS saldo_vencido,
    cxc.vencimiento_mas_antiguo
FROM clientes c
LEFT JOIN LATERAL (
    SELECT
        SUM(x.saldo)                                              AS saldo,
        COUNT(*) FILTER (WHERE x.estado <> 'liquidada')           AS facturas_abiertas,
        COUNT(*) FILTER (WHERE x.estado IN ('abierta','parcial')
                           AND x.fecha_vencimiento < CURRENT_DATE) AS vencidas,
        SUM(x.saldo) FILTER (WHERE x.estado IN ('abierta','parcial')
                           AND x.fecha_vencimiento < CURRENT_DATE) AS saldo_vencido,
        MIN(x.fecha_vencimiento) FILTER (WHERE x.estado IN ('abierta','parcial')) AS vencimiento_mas_antiguo
    FROM cuentas_por_cobrar x
    WHERE x.cliente_id = c.id AND x.estado <> 'liquidada'
) cxc ON true
WHERE c.estatus <> 'baja';

COMMENT ON VIEW v_cartera_cliente IS
    'Un renglón por cliente con su situación de crédito. Lo consume el pull de '
    'sincronización y el panel de operación. El dispositivo suma encima sus '
    'propios cargos y abonos sin sincronizar: ver app/domain/credito.py.';
