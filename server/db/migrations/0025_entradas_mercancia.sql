-- ===========================================================================
-- 0025 · Entradas de mercancía a la bodega
-- ===========================================================================
-- El hueco que esta migración cierra es de los que dejan un sistema inoperable
-- sin que ninguna prueba se ponga roja: **no había forma de meter mercancía al
-- sistema.**
--
-- El libro mayor (`movimientos_inventario`, migración 0004) ya contemplaba los
-- dos tipos que hacían falta: 'compra' (proveedor → bodega) y 'ajuste'. Y el
-- permiso `inventario.ajustar` está DEFINIDO desde la 0009... y no lo tenía
-- ningún rol, y ningún código lo consultaba nunca. Un permiso que nadie tiene y
-- nada pide: la función se planeó hasta el nombre y no se construyó. El motor
-- estaba, la puerta no.
--
-- La consecuencia práctica: la bodega nacía vacía y se quedaba vacía. Se podía
-- cargar un camión desde una bodega sin existencia —`existencias` no lleva CHECK
-- de signo a propósito (§0.1)— así que el sistema no fallaba: dejaba la bodega
-- en negativo y nadie lo notaba hasta que alguien abría la pantalla de
-- inventario con el filtro de negativos.
--
-- ---------------------------------------------------------------------------
-- UN DOCUMENTO, NO UN FORMULARIO DE UN RENGLÓN
-- ---------------------------------------------------------------------------
-- La tentación era una pantalla de "sumar N piezas al producto X". Habría sido
-- más rápido de construir y habría roto la propiedad que sostiene todo el
-- inventario de este sistema: **cada movimiento del libro mayor apunta a un
-- documento que lo explica** (`documento_tipo`, `documento_id`).
--
-- Un movimiento sin documento es un número que apareció, y en una auditoría de
-- inventario —que es el día en que esta tabla importa— «¿de dónde salieron estas
-- 240 piezas?» tiene que poder contestarse con una factura de proveedor, no con
-- «alguien lo capturó».
--
-- Así que la entrada es un documento con el mismo ciclo que la carga:
--
--     BORRADOR  →  (renglones)  →  CONFIRMADA  →  libro mayor + existencias
--
-- y en borrador no mueve nada. Capturar quince renglones de una remisión toma
-- veinte minutos, y un sistema que mueve inventario al primer renglón obliga a
-- terminar sin interrupciones o deja la bodega a medio recibir.
--
-- ---------------------------------------------------------------------------
-- EL DESTINO ES SIEMPRE UNA BODEGA, NUNCA UN CAMIÓN
-- ---------------------------------------------------------------------------
-- No es una limitación de la pantalla: es §0.2, el almacén del camión tiene un
-- único dueño exclusivo. La oficina NUNCA escribe existencias de un camión
-- —`traspasos` (0004) existe precisamente para eso: la oficina propone y el
-- vendedor acepta en la app—.
--
-- Una entrada directa a un camión desde el panel le cambiaría el inventario
-- bajo los pies a un vendedor que está vendiendo offline con otra cifra en el
-- teléfono, y el descuadre aparecería en su liquidación como un sobrante del que
-- no sabe nada. Mercancía nueva entra a la bodega; de ahí sube al camión con una
-- carga, que es el camino que ya existe y que el teléfono sabe recibir.
--
-- Es una regla entre tablas, así que no puede ser un CHECK: la valida
-- `app/api/admin/entradas.py` y hay una prueba que lo afirma.
-- ===========================================================================


-- ---------------------------------------------------------------------------
-- El folio
-- ---------------------------------------------------------------------------
-- Una secuencia y no `max(folio)+1`, por lo mismo que el folio de carga (0015):
-- dos personas recibiendo dos remisiones a la vez es el caso normal de una
-- mañana de bodega, no la excepción.
CREATE SEQUENCE IF NOT EXISTS seq_folio_entrada AS bigint START WITH 1;

COMMENT ON SEQUENCE seq_folio_entrada IS
    'Consecutivo del folio de entrada de mercancía. Se consume al crear el '
    'borrador en el panel.';


CREATE TABLE IF NOT EXISTS entradas (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    folio               text NOT NULL UNIQUE,
    almacen_destino_id  uuid NOT NULL REFERENCES almacenes(id),

    -- Tres motivos, catálogo cerrado, y cada uno mapea a un tipo del libro
    -- mayor (ver `app/domain/entradas.py`):
    --
    --   compra   → movimiento 'compra'  · llegó del proveedor, con su remisión
    --   inicial  → movimiento 'ajuste'  · el inventario que ya estaba el día
    --                                     que arrancó el sistema. No se compró:
    --                                     se declaró.
    --   ajuste   → movimiento 'ajuste'  · el conteo físico encontró MÁS de lo
    --                                     que decía el sistema.
    --
    -- 'inicial' y 'ajuste' caen en el mismo tipo del libro mayor y se
    -- distinguen por el documento. Es la razón de que el movimiento apunte al
    -- documento y no al contrario: el libro mayor se queda con ocho tipos y el
    -- detalle se recupera siempre.
    motivo              text NOT NULL CHECK (motivo IN ('compra','inicial','ajuste')),

    -- Texto libre, y es una decisión: no hay catálogo de proveedores porque no
    -- hay módulo de compras (es Fase 10 del plan). Inventar la tabla aquí
    -- obligaría a mantener un catálogo que nada más usa, y lo que de verdad se
    -- necesita el día de la auditoría es poder leer de quién llegó y con qué
    -- papel.
    proveedor           text,
    referencia          text,          -- la factura o remisión del proveedor

    estado              text NOT NULL DEFAULT 'borrador'
                        CHECK (estado IN ('borrador','confirmada','cancelada')),

    -- La fecha con la que entra al libro mayor. Se puede atrasar —la remisión
    -- del viernes capturada el lunes— y no adelantar: una entrada con fecha
    -- futura es mercancía que todavía no llegó.
    fecha_operativa     date NOT NULL DEFAULT CURRENT_DATE,
    nota                text,

    creado_en           timestamptz NOT NULL DEFAULT now(),
    creado_por          uuid REFERENCES usuarios(id),
    confirmada_en       timestamptz,
    confirmada_por      uuid REFERENCES usuarios(id),
    cancelada_en        timestamptz,
    cancelada_por       uuid REFERENCES usuarios(id),
    cancelacion_motivo  text,

    -- Confirmar y cancelar dejan su huella o no ocurrieron. Sin esto, un
    -- documento podría quedar 'confirmada' sin saber quién ni cuándo, que es
    -- justo lo que una auditoría de inventario viene a preguntar.
    CONSTRAINT entrada_confirmada_con_huella
        CHECK ((estado <> 'confirmada') OR
               (confirmada_en IS NOT NULL AND confirmada_por IS NOT NULL)),
    CONSTRAINT entrada_cancelada_con_motivo
        CHECK ((estado <> 'cancelada') OR
               (cancelada_en IS NOT NULL AND cancelacion_motivo IS NOT NULL)),

    -- Una compra sin referencia se acepta —hay proveedores que entregan sin
    -- papel y luego lo mandan— pero un inventario inicial sin nota no: es el
    -- documento que explica de dónde salió TODO el inventario del arranque, y
    -- se lee una vez en la vida del sistema, cuando algo no cuadra.
    CONSTRAINT entrada_inicial_con_nota
        CHECK (motivo <> 'inicial' OR nota IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_entradas_almacen
    ON entradas(almacen_destino_id, fecha_operativa DESC);
CREATE INDEX IF NOT EXISTS idx_entradas_borrador
    ON entradas(creado_en DESC) WHERE estado = 'borrador';

COMMENT ON TABLE entradas IS
    'Entrada de mercancía a una bodega: compra, inventario inicial o ajuste por '
    'conteo. En borrador no mueve nada; al confirmar escribe el libro mayor y '
    'las existencias en una sola transacción.';


CREATE TABLE IF NOT EXISTS entrada_detalle (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entrada_id          uuid NOT NULL REFERENCES entradas(id) ON DELETE CASCADE,
    producto_id         uuid NOT NULL REFERENCES productos(id),

    -- SIEMPRE en unidad base, como el libro mayor. La conversión caja→pieza se
    -- resuelve al capturar con `cantidad_base()`, la misma función que usa el
    -- teléfono al armar una partida.
    cantidad            numeric(14,3) NOT NULL CHECK (cantidad > 0),

    -- Y además, lo que la persona TECLEÓ. No es redundante: «240» no se puede
    -- revisar contra una remisión que dice «10 cajas», y el renglón se tiene que
    -- poder leer igual que el papel que se está capturando. Es el mismo
    -- razonamiento del folio impreso frente al folio del servidor.
    unidad_codigo       text,
    unidades_capturadas numeric(14,3) CHECK (unidades_capturadas IS NULL
                                             OR unidades_capturadas > 0),

    lote                text,
    caducidad           date
);

-- Mismo índice que `carga_detalle`: el mismo producto con el mismo lote se SUMA
-- en vez de duplicar el renglón, que es lo que espera quien captura de dos
-- tarimas. `COALESCE(lote,'')` porque en PostgreSQL dos NULL no colisionan y sin
-- eso un producto sin lote entraría tantas veces como se teclee.
CREATE UNIQUE INDEX IF NOT EXISTS uq_entrada_detalle_producto_lote
    ON entrada_detalle(entrada_id, producto_id, COALESCE(lote, ''));

COMMENT ON TABLE entrada_detalle IS
    'Renglones de una entrada, en unidad base, guardando también la '
    'presentación con la que se capturaron para poder revisarlos contra la '
    'remisión.';


-- ===========================================================================
-- El permiso, que existía sin dueño
-- ===========================================================================
-- `inventario.ajustar` se define en la 0009 y la 0009 no se lo da a nadie. Con
-- la pantalla construida y sin este GRANT, recibir mercancía sería posible solo
-- para `admin` —que salta los permisos por código— y el supervisor, que es
-- quien está físicamente en la bodega a las seis de la mañana, no podría.
--
-- Se le da al supervisor porque ya tiene `inventario.cargar` e
-- `inventario.liquidar`: recibir es el mismo trabajo de bodega, y quien puede
-- subir mercancía a un camión y cerrar su liquidación ya está adentro de la
-- operación de inventario.
INSERT INTO roles_permisos (rol_codigo, permiso_codigo) VALUES
 ('supervisor', 'inventario.ajustar')
ON CONFLICT DO NOTHING;

-- Y NO se le da a gerencia, a propósito. Gerencia es de solo lectura sobre la
-- operación (ADR 0002 §0): una entrada de mercancía crea inventario de la nada
-- desde el punto de vista del sistema, y es la operación con la que se puede
-- tapar un faltante. Quien mide no es quien ajusta.
-- ===========================================================================


-- ===========================================================================
-- Sin políticas por renglón, por la razón que ya escribió la 0022
-- ===========================================================================
-- `entradas` y `entrada_detalle` son documentos de oficina detrás de un
-- permiso, sin dueño de ruta y sin renglones de clientes — la misma categoría
-- que la 0022 dejó fuera a propósito. Una política por ruta aquí inventaría un
-- dueño que una remisión de proveedor no tiene.
--
-- El rol `dsd_api` las alcanza por el `ALTER DEFAULT PRIVILEGES` de
-- `db/ops/rol_api.sql`, que existe justo para que una tabla nueva no deje a la
-- API con un "permission denied" descubierto en producción.
--
-- Y el libro mayor tampoco cambia de régimen: `movimientos_inventario` sigue
-- siendo append-only por disparador, así que una entrada confirmada no se puede
-- deshacer editando el historial ni desde aquí ni desde `psql`.
-- ===========================================================================
