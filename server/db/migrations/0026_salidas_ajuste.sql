-- ===========================================================================
-- 0026 · Salidas de bodega: el conteo que encuentra menos, y la merma
-- ===========================================================================
-- La mitad que faltaba de la 0025. El documento de entrada solo suma, así que
-- un conteo físico que encuentra MENOS de lo registrado no tenía cómo
-- corregirse — y un faltante que no se puede registrar se queda en la
-- existencia para siempre, inflando lo que la bodega cree tener y dejando que
-- se cargue un camión con mercancía que no está.
--
-- ---------------------------------------------------------------------------
-- LA REGLA QUE SEPARA ESTO DE §0.1, Y ES LA DECISIÓN CENTRAL
-- ---------------------------------------------------------------------------
-- Una salida NO PUEDE dejar la existencia en negativo. Y eso parece contradecir
-- el principio fundacional del sistema —«el mundo físico ya ocurrió, el
-- servidor marca y no rechaza»—, pero es justo lo contrario: lo confirma.
--
-- §0.1 habla de hechos que YA PASARON EN LA CALLE y llegan tarde: una venta
-- offline, una merma del camión. Rechazarlas no devuelve la mercancía, así que
-- se aceptan y se marcan (`MOTIVO_SIN_EXISTENCIA_PARA_MERMA` en el manejador de
-- sincronización: «se MARCA, no se rechaza, el cartón ya está roto»).
--
-- Una salida de bodega es otra cosa por completo: la está TECLEANDO alguien en
-- la oficina, ahora, con el anaquel a la vista. No es un hecho que llega tarde,
-- es una corrección que se está escribiendo. Si el sistema dice 3 y alguien
-- captura una salida de 5, no hay ningún hecho físico que respaldar: el anaquel
-- no puede tener menos que nada. Es un dedazo, y bloquearlo no niega la
-- realidad — la protege, porque un asiento equivocado en un libro append-only
-- no se borra, se arrastra.
--
-- Dicho corto: **a lo que ya pasó se le cree; a lo que se está capturando se le
-- revisa.**
--
-- ---------------------------------------------------------------------------
-- EN UN CONTEO SE CAPTURA LO QUE SE CONTÓ, NO LA DIFERENCIA
-- ---------------------------------------------------------------------------
-- Quien hace un conteo físico anota lo que ve en el anaquel: «80». No anota
-- «faltan 20» — eso exige restar a mano, a las siete de la mañana, con una
-- tabla en la mano, por cada producto. Y la resta hecha a mano es exactamente
-- de donde salen los errores que este documento viene a corregir.
--
-- Así que el renglón guarda las tres cifras y la BASE DE DATOS impone la
-- aritmética (`CONSTRAINT conteo_cuadra`): un bug en Python no puede escribir
-- un renglón de conteo que no cuadre.
--
--     existencia_al_capturar  −  contado  =  cantidad que sale
--
-- Y si el conteo encuentra MÁS de lo registrado, esto no es el documento: es
-- una entrada con motivo 'ajuste' (migración 0025). La pantalla lo dice y
-- manda para allá en vez de aceptar una salida negativa, que el CHECK
-- `cantidad > 0` tampoco permitiría.
--
-- ---------------------------------------------------------------------------
-- UN FALTANTE DE CONTEO NO TIENE MOTIVO, Y NO SE LE INVENTA UNO
-- ---------------------------------------------------------------------------
-- El catálogo de motivos ya existe y es cerrado desde la Fase 6
-- (`motivos_merma`: CADUCADO, DANADO_BODEGA, ROBO, MUESTRA...), así que no se
-- inventa uno nuevo: se reutiliza el que el sistema ya sincroniza.
--
-- Pero el motivo es OBLIGATORIO en una merma y PROHIBIDO en un conteo, y esa
-- asimetría es deliberada. Un faltante de conteo es, por definición, un
-- faltante CUYA CAUSA NO SE CONOCE: si se supiera, se habría capturado como
-- merma el día que pasó. Obligar a elegir un motivo haría que alguien marcara
-- 'ROBO' o 'DANADO_BODEGA' sin saber, y eso convierte un dato duro —«faltan 20
-- piezas»— en una acusación inventada que después alguien va a leer como un
-- hecho.
-- ===========================================================================


CREATE SEQUENCE IF NOT EXISTS seq_folio_salida AS bigint START WITH 1;

COMMENT ON SEQUENCE seq_folio_salida IS
    'Consecutivo del folio de salida de bodega. Se consume al crear el '
    'borrador en el panel.';


CREATE TABLE IF NOT EXISTS salidas (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    folio               text NOT NULL UNIQUE,
    almacen_origen_id   uuid NOT NULL REFERENCES almacenes(id),

    -- Dos tipos, y cada uno con su asiento en el libro mayor:
    --
    --   conteo → movimiento 'ajuste' · faltante sin causa conocida
    --   merma  → movimiento 'merma'  · pérdida identificada, con su motivo
    --
    -- Aquí sí se usan dos tipos distintos del libro mayor (y no como en la
    -- 0025, donde 'inicial' y 'ajuste' comparten uno): la diferencia entre una
    -- pérdida identificada y un descuadre sin explicar es la que decide si hay
    -- algo que arreglar en la bodega, y poder separarlas leyendo el libro mayor
    -- vale más que la simetría con las entradas.
    tipo                text NOT NULL CHECK (tipo IN ('conteo','merma')),

    -- Del catálogo cerrado de la Fase 6, el mismo que usa el teléfono.
    motivo_codigo       text REFERENCES motivos_merma(codigo),

    estado              text NOT NULL DEFAULT 'borrador'
                        CHECK (estado IN ('borrador','confirmada','cancelada')),

    fecha_operativa     date NOT NULL DEFAULT CURRENT_DATE,
    nota                text,

    creado_en           timestamptz NOT NULL DEFAULT now(),
    creado_por          uuid REFERENCES usuarios(id),
    confirmada_en       timestamptz,
    confirmada_por      uuid REFERENCES usuarios(id),
    cancelada_en        timestamptz,
    cancelada_por       uuid REFERENCES usuarios(id),
    cancelacion_motivo  text,

    -- La asimetría del motivo. Ver el encabezado: a un faltante de conteo no se
    -- le inventa una causa.
    CONSTRAINT salida_merma_lleva_motivo
        CHECK (tipo <> 'merma' OR motivo_codigo IS NOT NULL),
    CONSTRAINT salida_conteo_sin_motivo
        CHECK (tipo <> 'conteo' OR motivo_codigo IS NULL),

    -- Un conteo sin nota no sirve de nada dentro de seis meses: hace falta
    -- saber quién contó y cuándo. Es el mismo CHECK que el inventario inicial
    -- de la 0025, por la misma razón — es el documento que alguien va a leer el
    -- día que la cifra no cuadre.
    CONSTRAINT salida_conteo_lleva_nota
        CHECK (tipo <> 'conteo' OR nota IS NOT NULL),

    CONSTRAINT salida_confirmada_con_huella
        CHECK ((estado <> 'confirmada') OR
               (confirmada_en IS NOT NULL AND confirmada_por IS NOT NULL)),
    CONSTRAINT salida_cancelada_con_motivo
        CHECK ((estado <> 'cancelada') OR
               (cancelada_en IS NOT NULL AND cancelacion_motivo IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_salidas_almacen
    ON salidas(almacen_origen_id, fecha_operativa DESC);
CREATE INDEX IF NOT EXISTS idx_salidas_borrador
    ON salidas(creado_en DESC) WHERE estado = 'borrador';

COMMENT ON TABLE salidas IS
    'Salida de mercancía de una bodega: ajuste por conteo físico o merma '
    'identificada. En borrador no mueve nada; al confirmar escribe el libro '
    'mayor y las existencias, y nunca deja una existencia en negativo.';


CREATE TABLE IF NOT EXISTS salida_detalle (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    salida_id           uuid NOT NULL REFERENCES salidas(id) ON DELETE CASCADE,
    producto_id         uuid NOT NULL REFERENCES productos(id),

    -- Lo que SALE, en unidad base. Siempre positivo: la dirección la da el
    -- documento, no el signo — igual que en el libro mayor.
    cantidad            numeric(14,3) NOT NULL CHECK (cantidad > 0),

    -- Solo en un conteo: lo que se contó en el anaquel y lo que el sistema
    -- decía en ese momento.
    contado                numeric(14,3) CHECK (contado IS NULL OR contado >= 0),
    existencia_al_capturar numeric(14,3),

    -- Y lo que la persona tecleó, igual que en las entradas: «240» no se puede
    -- revisar contra una hoja de conteo que dice «10 cajas».
    unidad_codigo       text,
    unidades_capturadas numeric(14,3) CHECK (unidades_capturadas IS NULL
                                             OR unidades_capturadas > 0),
    lote                text,

    -- Las dos cifras del conteo van juntas o no van.
    CONSTRAINT conteo_guarda_las_dos_cifras
        CHECK ((contado IS NULL) = (existencia_al_capturar IS NULL)),

    -- LA ARITMÉTICA LA IMPONE LA BASE. Un bug en Python no puede escribir un
    -- renglón de conteo que no cuadre con su propia resta.
    CONSTRAINT conteo_cuadra
        CHECK (contado IS NULL OR cantidad = existencia_al_capturar - contado),

    -- Un conteo es POR PRODUCTO y no por lote, porque `existencias` no tiene
    -- dimensión de lote: hay un número por (almacén, producto). Aceptar un lote
    -- aquí prometería una precisión que la tabla contra la que se compara no
    -- tiene. En una merma sí, que es la que se identifica por tarima.
    CONSTRAINT conteo_sin_lote
        CHECK (contado IS NULL OR lote IS NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_salida_detalle_producto_lote
    ON salida_detalle(salida_id, producto_id, COALESCE(lote, ''));

COMMENT ON TABLE salida_detalle IS
    'Renglones de una salida, en unidad base. En un conteo guarda además lo '
    'contado y la existencia de ese momento, y la base impone que la resta '
    'cuadre.';


-- ===========================================================================
-- El permiso, el mismo
-- ===========================================================================
-- `inventario.ajustar`, que la 0025 le otorgó al supervisor. Una salida es el
-- ajuste de inventario por antonomasia, y es la operación con la que se puede
-- tapar un robo: si alguien se lleva mercancía, un faltante de conteo lo
-- absorbe sin dejar más rastro que este documento con su nota y su firma.
--
-- Por eso el documento guarda quién confirmó y cuándo, y por eso gerencia NO
-- lleva el permiso: quien mide no ajusta (ADR 0002 §0).
--
-- Sin políticas por renglón, por lo mismo que la 0024 y la 0025: documentos de
-- oficina detrás de un permiso, sin dueño de ruta. Y el libro mayor sigue
-- siendo append-only por disparador — una salida confirmada no se deshace
-- editando el historial.
-- ===========================================================================
