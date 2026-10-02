-- ===========================================================================
-- 0027 · Compras: proveedores, costo promedio y cuentas por pagar
-- ===========================================================================
-- La Fase 10 del plan («Opcionales») lista «Compras/recepción». La recepción se
-- construyó en la 0025; esto es el resto, y la pieza que lo sostiene todo es
-- UNA SOLA: el costo.
--
-- ---------------------------------------------------------------------------
-- EL COSTO ERA EL HUECO MÁS CARO DEL SISTEMA
-- ---------------------------------------------------------------------------
-- `merma_detalle.costo_unitario` existe desde la migración 0006 con el
-- comentario «Costo congelado al momento, para valuar la pérdida». Es la ÚNICA
-- columna de costo del esquema entero, y **ningún código la escribe jamás**:
-- un campo diseñado para valuar pérdidas que nunca tuvo un costo que congelar.
-- La misma huella que `inventario.ajustar` antes de la 0025.
--
-- Sin costo, tres cosas que el sistema parece poder hacer y no puede:
--
--   · el laboratorio de la Fase 8 no tiene NI UNA métrica de margen —no se le
--     olvidó, no había con qué calcularla—;
--   · una merma de 300 piezas es «300 piezas», no una cantidad de dinero, así
--     que no se puede comparar con nada ni priorizar;
--   · el inventario de la bodega no tiene valor, así que la pregunta «¿cuánto
--     dinero hay en el almacén?» no tiene respuesta.
--
-- ---------------------------------------------------------------------------
-- Y POR ESO EL COSTO **NO** VA EN `productos`
-- ---------------------------------------------------------------------------
-- Lo obvio era `productos.costo_promedio`. Sería una fuga de datos del negocio
-- al teléfono de cada vendedor, y silenciosa.
--
-- `fn_registrar_cambio` (migración 0010) publica `to_jsonb(NEW)` —LA FILA
-- COMPLETA— en `change_log`, y el disparador de `productos` lo hace con
-- `ruta_id = NULL`, que significa «a todos los dispositivos». Una columna de
-- costo en esa tabla viajaría en el siguiente pull a todos los teléfonos, se
-- guardaría en el SQLite de cada uno, y cualquier vendedor podría ver el margen
-- de cada producto de la empresa. En un aparato que se pierde.
--
-- Nadie lo habría notado al revisar el diff: la columna se agrega en un lugar
-- y el dato sale por otro, tres migraciones más atrás.
--
-- Así que el costo vive en `producto_costos`, una tabla aparte, **sin
-- disparador de change_log**. La separación no es organizativa: es la frontera
-- entre lo que el teléfono necesita (precio de venta) y lo que no debe salir de
-- la oficina (lo que nos cuesta).
--
-- ---------------------------------------------------------------------------
-- PROMEDIO PONDERADO, Y ESA ELECCIÓN SE ESCRIBE AQUÍ
-- ---------------------------------------------------------------------------
-- Hay tres formas de valuar inventario y elegir en silencio sería lo peor:
--
--   último costo   simple, y miente cada vez que el proveedor sube el precio:
--                  revalúa de golpe todo lo viejo que sigue en el anaquel.
--   PEPS (capas)   el más exacto y el más caro: exige rastrear capas y en qué
--                  orden se consumen, con el inventario ya en cinco camiones.
--   PROMEDIO       una cifra por producto, sobrevive al consumo parcial sin
--   PONDERADO      rastrear nada, y es de las opciones que NIF C-4 permite.
--
-- Se eligió el promedio ponderado:
--
--     nuevo = (unidades_en_mano × costo_actual + unidades_que_entran × costo_entrada)
--             ───────────────────────────────────────────────────────────────────
--                        unidades_en_mano + unidades_que_entran
--
-- `unidades_en_mano` es la suma de existencias del producto en TODOS los
-- almacenes menos los de merma: un camión cargado tiene inventario de la
-- empresa aunque esté en la calle, y lo que ya se mermó es pérdida, no
-- inventario. Y por eso el costo es **por producto y no por almacén**: el
-- producto cuesta lo que cuesta, y una carga de bodega a camión no es una
-- compra.
--
-- Con cero o menos unidades en mano, el promedio es simplemente el costo que
-- entra: no hay nada que ponderar, y ponderar contra un negativo daría un costo
-- negativo con cara de dato bueno.
-- ===========================================================================


-- ===========================================================================
-- proveedores
-- ===========================================================================
-- Deja de ser texto libre en `entradas`. El catálogo no se inventó en la 0025 a
-- propósito —no había módulo que lo usara y un catálogo que nada usa no se
-- mantiene— y ahora sí hay quien lo use: el crédito del proveedor es lo que
-- decide cuándo vence lo que le debemos.
CREATE TABLE IF NOT EXISTS proveedores (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo          text NOT NULL UNIQUE,
    nombre          text NOT NULL,

    -- El RFC se guarda en mayúsculas y sin validar su forma. Un CHECK de
    -- formato rechazaría un RFC extranjero o uno con homoclave rara justo
    -- cuando alguien está capturando una factura real, y el dato no alimenta
    -- ningún cálculo: es para encontrar al proveedor y para el día que haya
    -- CFDI.
    rfc             text,
    contacto        text,
    telefono        text,

    -- Los días que da de crédito. CERO significa contado, y es el valor por
    -- omisión: el proveedor que no da crédito es el caso normal de un abarrote
    -- chico. De aquí sale el vencimiento de la cuenta por pagar.
    dias_credito    integer NOT NULL DEFAULT 0
                    CHECK (dias_credito BETWEEN 0 AND 365),

    activo          boolean NOT NULL DEFAULT true,
    notas           text,
    creado_en       timestamptz NOT NULL DEFAULT now(),
    creado_por      uuid REFERENCES usuarios(id),
    actualizado_en  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_proveedores_activos
    ON proveedores(nombre) WHERE activo;

COMMENT ON TABLE proveedores IS
    'Catálogo de proveedores. `dias_credito` decide el vencimiento de la cuenta '
    'por pagar; cero es contado.';


-- ===========================================================================
-- producto_costos — el costo promedio, FUERA de `productos`
-- ===========================================================================
-- Tabla aparte y SIN disparador de change_log. Ver el encabezado: `productos`
-- se publica completa a todos los dispositivos, así que una columna de costo
-- ahí sería el margen de la empresa en el teléfono de cada vendedor.
--
-- Si algún día alguien agrega un disparador de change_log a esta tabla, el
-- costo empieza a viajar. Hay una prueba que afirma que no lo tiene.
CREATE TABLE IF NOT EXISTS producto_costos (
    producto_id         uuid PRIMARY KEY REFERENCES productos(id) ON DELETE CASCADE,

    -- Cuatro decimales, la escala de `Precio` (ADR 0002): una caja de 24 a
    -- $296.00 cuesta $12.3333 la pieza, y a dos decimales las 24 piezas
    -- sumarían $295.92.
    costo_promedio      numeric(14,4) NOT NULL CHECK (costo_promedio >= 0),

    -- El último costo pagado y cuándo. No se usa para valuar: se guarda porque
    -- «el promedio dice 12.33 y la última compra fue a 15.80» es la cifra con
    -- la que alguien decide subir el precio de venta, y el promedio solo se
    -- mueve despacio.
    ultimo_costo        numeric(14,4) CHECK (ultimo_costo IS NULL OR ultimo_costo >= 0),
    ultima_compra_en    date,

    -- Las unidades contra las que se ponderó la última vez. Forense: permite
    -- entender un promedio que se ve raro sin reconstruir el historial.
    unidades_al_ponderar numeric(14,3),

    actualizado_en      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE producto_costos IS
    'Costo promedio ponderado por producto. Vive FUERA de `productos` porque '
    'esa tabla se publica completa a todos los dispositivos y el costo no debe '
    'salir de la oficina. No lleva disparador de change_log.';


-- ===========================================================================
-- Lo que la entrada gana: proveedor y costo
-- ===========================================================================
ALTER TABLE entradas
    ADD COLUMN IF NOT EXISTS proveedor_id uuid REFERENCES proveedores(id);

-- `entradas.proveedor` (texto) se queda, y no es redundancia: guarda el NOMBRE
-- CON EL QUE SE COMPRÓ. Si el proveedor se renombra o se da de baja, la entrada
-- de hace dos años sigue diciendo a quién se le compró — el mismo razonamiento
-- por el que la venta congela `lista_precios_version`.
COMMENT ON COLUMN entradas.proveedor IS
    'Nombre del proveedor al momento de comprar. Se conserva aunque el catálogo '
    'cambie; `proveedor_id` apunta al catálogo cuando existe.';

-- El importe del documento, congelado al confirmar. Es el importe de la factura,
-- y tiene que seguir diciendo lo mismo si mañana alguien corrige un costo.
ALTER TABLE entradas
    ADD COLUMN IF NOT EXISTS importe_total numeric(14,2)
    CHECK (importe_total IS NULL OR importe_total >= 0);

ALTER TABLE entrada_detalle
    ADD COLUMN IF NOT EXISTS costo_unitario numeric(14,4)
    CHECK (costo_unitario IS NULL OR costo_unitario >= 0);

-- El IMPORTE del renglón, congelado: lo que dice la factura.
--
-- Parece redundante con `cantidad × costo_unitario` y no lo es, y la diferencia
-- cuesta dinero: 10 cajas a $296.00 son $2,960.00 en la factura, pero el costo
-- por pieza es 296/24 = 12.3333… que no es exacto, y 240 × 12.3333 da $2,959.99.
--
-- Un centavo, y rompe la operación: la cuenta por pagar nacería en $2,959.99,
-- alguien capturaría el pago de $2,960.00 que de verdad hizo, y el sistema lo
-- rechazaría por exceder el saldo — con el CHECK `pago_no_excede_el_original`
-- esperando detrás.
--
-- Así que son dos verdades distintas y se guardan las dos:
--
--     importe         lo que se le debe al proveedor  → se calcula sobre lo
--                                                        CAPTURADO (bultos ×
--                                                        costo por bulto)
--     costo_unitario  con qué se valúa el inventario  → por unidad base, para
--                                                        el promedio ponderado
--
-- Es la misma regla del ADR 0002 §2 —el importe redondea una sola vez y al
-- final— aplicada donde de verdad importa: la multiplicación final es sobre las
-- cajas que venían en la factura, no sobre las piezas en que se convirtieron.
ALTER TABLE entrada_detalle
    ADD COLUMN IF NOT EXISTS importe numeric(14,2)
    CHECK (importe IS NULL OR importe >= 0);

COMMENT ON COLUMN entrada_detalle.importe IS
    'Importe del renglón según la factura: unidades capturadas × costo por '
    'bulto, redondeado una sola vez. NO es cantidad × costo_unitario — esa '
    'cuenta pierde centavos cuando el factor no divide exacto.';


-- NULO significa «se valúa al promedio actual», no «cuesta cero».
--
-- Es obligatorio en una compra —ahí está la factura en la mano— y opcional en
-- un inventario inicial o un ajuste por conteo, donde nadie compró esas
-- unidades. Valuarlas al promedio vigente deja el promedio intacto, que es el
-- tratamiento estándar de lo que aparece en un conteo; guardarlas en cero
-- arrastraría el promedio a la baja con un costo que nadie pagó.
COMMENT ON COLUMN entrada_detalle.costo_unitario IS
    'Costo unitario pagado, a cuatro decimales. NULO = se valúa al promedio '
    'vigente y el promedio no se mueve. Obligatorio en motivo compra.';

CREATE INDEX IF NOT EXISTS idx_entradas_proveedor
    ON entradas(proveedor_id, fecha_operativa DESC)
    WHERE proveedor_id IS NOT NULL;


-- ===========================================================================
-- cuentas_por_pagar — el espejo de cuentas_por_cobrar
-- ===========================================================================
-- Mismo diseño que `cuentas_por_cobrar` (0005), incluido el saldo como columna
-- GENERADA: un saldo calculado en la aplicación se desincroniza el día que dos
-- caminos distintos actualicen el pagado.
--
-- Y SIN disparador de change_log, al contrario que `cuentas_por_cobrar`: el
-- vendedor necesita saber cuánto le debe su cliente, y no tiene nada que hacer
-- con lo que la empresa le debe a sus proveedores.
CREATE TABLE IF NOT EXISTS cuentas_por_pagar (
    entrada_id          uuid PRIMARY KEY REFERENCES entradas(id),
    proveedor_id        uuid NOT NULL REFERENCES proveedores(id),

    importe_original    numeric(14,2) NOT NULL CHECK (importe_original > 0),
    importe_pagado      numeric(14,2) NOT NULL DEFAULT 0 CHECK (importe_pagado >= 0),
    saldo               numeric(14,2)
                        GENERATED ALWAYS AS (importe_original - importe_pagado) STORED,

    fecha_emision       date NOT NULL,
    fecha_vencimiento   date NOT NULL,
    referencia          text,          -- la factura, copiada de la entrada

    estado              text NOT NULL DEFAULT 'abierta'
                        CHECK (estado IN ('abierta','parcial','liquidada','cancelada')),
    actualizado_en      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT pago_no_excede_el_original
        CHECK (importe_pagado <= importe_original),
    CONSTRAINT vencimiento_no_antes_de_emision
        CHECK (fecha_vencimiento >= fecha_emision)
);

CREATE INDEX IF NOT EXISTS idx_cxp_proveedor
    ON cuentas_por_pagar(proveedor_id) WHERE estado <> 'liquidada';
CREATE INDEX IF NOT EXISTS idx_cxp_vencidas
    ON cuentas_por_pagar(fecha_vencimiento) WHERE estado IN ('abierta','parcial');

COMMENT ON TABLE cuentas_por_pagar IS
    'Lo que se le debe a cada proveedor, una por entrada de compra. Espejo de '
    'cuentas_por_cobrar, y sin change_log: al teléfono no le incumbe.';


-- ===========================================================================
-- pagos_proveedor
-- ===========================================================================
-- UN pago se aplica a UNA cuenta, y eso es distinto de la cobranza.
--
-- Un cobro del vendedor se aplica FIFO por el servidor porque el cliente paga
-- «lo que debe» sin decir cuál factura — llega un abono y hay que repartirlo.
-- Un pago a proveedor es al revés: se paga LA factura F-45821, con su
-- transferencia y su referencia. Inventar un FIFO aquí repartiría un pago entre
-- facturas que nadie quiso pagar, y después nadie podría conciliar con el
-- estado de cuenta del proveedor.
CREATE TABLE IF NOT EXISTS pagos_proveedor (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entrada_id      uuid NOT NULL REFERENCES cuentas_por_pagar(entrada_id)
                    ON DELETE CASCADE,
    importe         numeric(14,2) NOT NULL CHECK (importe > 0),
    forma_pago      text NOT NULL DEFAULT 'transferencia'
                    CHECK (forma_pago IN ('efectivo','transferencia','cheque')),
    referencia      text,
    fecha_pago      date NOT NULL DEFAULT CURRENT_DATE,
    nota            text,
    registrado_en   timestamptz NOT NULL DEFAULT now(),
    registrado_por  uuid REFERENCES usuarios(id)
);

CREATE INDEX IF NOT EXISTS idx_pagos_proveedor_cuenta
    ON pagos_proveedor(entrada_id, fecha_pago DESC);

COMMENT ON TABLE pagos_proveedor IS
    'Pagos a proveedor, uno a una cuenta. No hay aplicación FIFO: a un '
    'proveedor se le paga una factura concreta, no "lo que se le debe".';


-- ===========================================================================
-- Permisos
-- ===========================================================================
-- Dos, y separados a propósito: recibir mercancía y pagarla son dos manos
-- distintas. Quien captura la entrada registra lo que llegó; quien paga mueve
-- dinero de la empresa, y que la misma persona pueda hacer las dos cosas sin
-- que nadie más lo vea es la receta de una factura inventada.
--
-- `compras.administrar` cubre el catálogo de proveedores y el costo.
-- `compras.pagar` cubre registrar pagos.
INSERT INTO permisos (codigo, descripcion, modulo) VALUES
 ('compras.administrar', 'Proveedores y costos de compra',        'compras'),
 ('compras.pagar',       'Registrar pagos a proveedores',         'compras')
ON CONFLICT (codigo) DO NOTHING;

-- Al gerente las dos: es quien negocia con el proveedor y autoriza el pago.
--
-- Y aquí gerencia SÍ escribe, al contrario que en inventario, sin contradecir
-- «gerencia es de solo lectura SOBRE LA OPERACIÓN» (ADR 0002 §0): un proveedor
-- y un costo de compra no son la operación de la calle — no mueven inventario
-- ni tocan una venta. Son la relación comercial de la empresa, que es
-- precisamente lo que gerencia dirige.
--
-- Al supervisor solo `compras.administrar`: ya recibe mercancía con
-- `inventario.ajustar`, así que necesita poder capturar el costo de lo que
-- recibe. Pagar, no: es la separación de manos de arriba.
INSERT INTO roles_permisos (rol_codigo, permiso_codigo) VALUES
 ('gerente',    'compras.administrar'),
 ('gerente',    'compras.pagar'),
 ('supervisor', 'compras.administrar')
ON CONFLICT DO NOTHING;


-- ===========================================================================
-- Sin políticas por renglón, y sin change_log
-- ===========================================================================
-- Las cinco tablas de aquí son de oficina, detrás de permiso, sin dueño de
-- ruta: la misma categoría que la 0022 dejó fuera.
--
-- Lo que SÍ importa repetir: ninguna lleva disparador de `change_log`. El costo
-- y la deuda con proveedores no tienen nada que hacer en el teléfono de un
-- vendedor, y el camino por el que se escaparían no es una consulta olvidada —
-- es un disparador que publica la fila completa.
-- ===========================================================================
