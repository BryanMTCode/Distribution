-- ===========================================================================
-- 0024 · Fase 3 — El instrumento del piloto
-- ===========================================================================
-- El plan pide «un piloto con UN vendedor en UNA ruta durante 2 semanas, con el
-- proceso de papel en paralelo». Esta migración no acelera esas dos semanas
-- —son calendario y su valor ES el calendario— sino que construye la única
-- parte del piloto que sí es código: CON QUÉ SE MIDE.
--
-- ---------------------------------------------------------------------------
-- POR QUÉ EL PAPEL EN PARALELO NO SIRVE DE NADA POR SÍ SOLO
-- ---------------------------------------------------------------------------
-- Llevar el papel al mismo tiempo que la app es doble trabajo para el vendedor,
-- y si nadie compara las dos cosas, ese doble trabajo compró exactamente cero
-- información. El papel en paralelo no es un respaldo: es el PATRÓN DE MEDIDA.
-- Sin la comparación diaria, el piloto produce una anécdota («se portó bien»),
-- y una anécdota no puede decidir si se compran siete teléfonos más.
--
-- ---------------------------------------------------------------------------
-- LOS DOS INSTRUMENTOS MIDEN FALLAS DISTINTAS, Y POR ESO SON DOS
-- ---------------------------------------------------------------------------
-- 1. **Lo que el sistema mide de sí mismo** (gratis, sin que nadie escriba
--    nada): el retraso entre `fecha_operativa` y `fecha_servidor`, las
--    cancelaciones, la cuarentena, los días sin sincronizar, la liquidación que
--    no cerró. Detecta fallas TÉCNICAS.
--
-- 2. **Lo que solo el papel puede atrapar**: la venta que ocurrió y NUNCA
--    entró a la app, el importe que se cobró distinto, el cliente que se visitó
--    y no se registró. Detecta fallas de ADOPCIÓN, que son el motivo real de
--    hacer un piloto.
--
-- La diferencia entre los dos es la frase que justifica toda esta migración:
-- **el sistema no puede medir la venta que no existe en el sistema.** Ninguna
-- métrica, ningún log y ningún tablero ven ese hueco. El papel sí.
--
-- ---------------------------------------------------------------------------
-- LA DIFERENCIA QUE SE CIERRA SOLA NO ES LA MISMA FALLA
-- ---------------------------------------------------------------------------
-- A las 8 de la mañana el papel dice 23 documentos y el sistema 21. A mediodía
-- el teléfono sincroniza y el sistema dice 23. Esa diferencia NO era pérdida de
-- datos: era retraso de entrega, que es §0.3 funcionando como debe.
--
-- Son dos hallazgos opuestos y se ven idénticos si solo se guarda una cifra.
-- Por eso `piloto_jornadas` guarda lo que decía el sistema EN EL MOMENTO DE
-- CAPTURAR —congelado— y la pantalla calcula además lo que dice HOY. La
-- primera mide el retraso; la segunda, la pérdida. Solo la segunda es grave.
-- ===========================================================================


-- ===========================================================================
-- pilotos — qué vendedor, qué ruta, qué dos semanas
-- ===========================================================================
-- Un piloto a la vez, y eso no es una limitación técnica: si el piloto pasa, el
-- segundo vendedor no es otro piloto, es el despliegue. Si no pasa, se arregla
-- y se REPITE —de ahí que la tabla admita varios renglones y solo uno activo—,
-- porque el historial de un piloto fallido es justo lo que explica por qué el
-- siguiente se hizo distinto.
CREATE TABLE IF NOT EXISTS pilotos (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo          text NOT NULL UNIQUE
                    CHECK (codigo ~ '^[a-z0-9][a-z0-9._-]{1,29}$'),
    vendedor_id     uuid NOT NULL REFERENCES usuarios(id),
    ruta_id         uuid NOT NULL REFERENCES rutas(id),
    inicio          date NOT NULL,
    fin             date NOT NULL,
    activo          boolean NOT NULL DEFAULT true,

    -- El veredicto se GUARDA, con su fecha y su nota. Un piloto que termina sin
    -- decisión escrita se convierte en el argumento de quien grite más fuerte
    -- tres meses después.
    cerrado_en      timestamptz,
    veredicto       text CHECK (veredicto IN ('adelante','repetir','alto')),
    veredicto_nota  text,

    creado_en       timestamptz NOT NULL DEFAULT now(),
    creado_por      uuid REFERENCES usuarios(id),

    -- Una semana es el piso: con menos no hay segunda semana contra la que
    -- comparar la primera, y la comparación entre semanas es el hallazgo más
    -- importante del piloto (ver `piloto_criterios`). El plan pide dos.
    CONSTRAINT piloto_dura_al_menos_una_semana
        CHECK (fin >= inicio + 6),

    -- Cerrar y dictar veredicto son el mismo acto. Separarlos dejaría pilotos
    -- "cerrados" sin decisión, que es el estado en el que mueren los pilotos.
    CONSTRAINT piloto_cerrado_lleva_veredicto
        CHECK ((cerrado_en IS NULL) = (veredicto IS NULL))
);

-- Uno activo. El índice parcial es la única forma de decirlo en el esquema: sin
-- él, dos pilotos activos harían que cada pantalla eligiera uno al azar.
CREATE UNIQUE INDEX IF NOT EXISTS uq_piloto_activo
    ON pilotos(activo) WHERE activo;

COMMENT ON TABLE pilotos IS
    'El piloto de la Fase 3: un vendedor, una ruta, dos semanas. Uno activo a '
    'la vez. El veredicto se guarda con su fecha.';


-- ===========================================================================
-- piloto_jornadas — el cuadre diario contra el papel
-- ===========================================================================
-- Un renglón por día del piloto, capturado por la oficina a la mañana
-- siguiente con el papel del vendedor en la mano.
--
-- EL SIGNO: las diferencias son `sistema - papel`, así que **negativo significa
-- que al sistema le faltan ventas**, que es la dirección peligrosa. Se escribe
-- aquí porque un signo al revés en un reporte de cuadre convierte un hallazgo
-- grave en un «sobró», y nadie investiga un sobrante.
CREATE TABLE IF NOT EXISTS piloto_jornadas (
    piloto_id       uuid NOT NULL REFERENCES pilotos(id) ON DELETE CASCADE,
    fecha_operativa date NOT NULL,

    -- Lo que dice el PAPEL. Es el patrón de medida.
    documentos_papel   integer NOT NULL CHECK (documentos_papel >= 0),
    importe_papel      numeric(14,2) NOT NULL CHECK (importe_papel >= 0),
    cobranza_papel     numeric(14,2) NOT NULL DEFAULT 0 CHECK (cobranza_papel >= 0),
    -- Opcional: no todo papel de ruta trae las visitas sin venta. Un 0 diría
    -- "no visitó a nadie", que es distinto de "el papel no lo trae".
    visitas_papel      integer CHECK (visitas_papel IS NULL OR visitas_papel >= 0),

    -- Lo que decía el SISTEMA al capturar. Congelado a propósito: ver el
    -- encabezado. Puede ser negativo en cobranza (una cancelación), así que no
    -- lleva CHECK de signo.
    documentos_sistema integer NOT NULL CHECK (documentos_sistema >= 0),
    importe_sistema    numeric(14,2) NOT NULL,
    cobranza_sistema   numeric(14,2) NOT NULL,

    diferencia_documentos integer
        GENERATED ALWAYS AS (documentos_sistema - documentos_papel) STORED,
    diferencia_importe    numeric(14,2)
        GENERATED ALWAYS AS (importe_sistema - importe_papel) STORED,
    diferencia_cobranza   numeric(14,2)
        GENERATED ALWAYS AS (cobranza_sistema - cobranza_papel) STORED,

    capturado_en    timestamptz NOT NULL DEFAULT now(),
    capturado_por   uuid NOT NULL REFERENCES usuarios(id),
    observaciones   text,

    PRIMARY KEY (piloto_id, fecha_operativa)
);

CREATE INDEX IF NOT EXISTS idx_piloto_jornadas_con_diferencia
    ON piloto_jornadas(piloto_id)
    WHERE diferencia_documentos <> 0 OR diferencia_importe <> 0;

COMMENT ON TABLE piloto_jornadas IS
    'Cuadre diario del piloto: lo que dice el papel contra lo que decía el '
    'sistema al capturar. Las diferencias son sistema - papel: negativo es que '
    'al sistema le faltan ventas.';


-- ===========================================================================
-- piloto_incidencias — la bitácora
-- ===========================================================================
-- POR QUÉ AQUÍ SÍ HAY TEXTO LIBRE, SI LA FASE 6 LO PROHIBIÓ
--
-- La regla de la Fase 6 es «texto libre = datos inanalizables», y sigue en pie
-- para los no-drops: un motivo de no-venta se captura veinte veces al día y su
-- valor está en poder CONTARLO.
--
-- Una incidencia de piloto es lo contrario: pasa una vez y su valor está en el
-- DETALLE. «Se cerró la app al agregar el tercer renglón del carrito con el
-- teclado abierto» no cabe en ningún catálogo, y es exactamente lo que se
-- necesita para reproducirla.
--
-- Así que se cierra el catálogo de lo que ya se sabe —categoría y severidad,
-- que son para contar— y se deja texto para lo que no se sabe, que es el
-- motivo de hacer un piloto. Un piloto cuyo formulario solo acepta opciones
-- conocidas solo puede descubrir lo que ya estaba previsto.
CREATE TABLE IF NOT EXISTS piloto_incidencias (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    piloto_id       uuid NOT NULL REFERENCES pilotos(id) ON DELETE CASCADE,
    fecha_operativa date NOT NULL,
    -- Opcional: a las 6 de la tarde el vendedor ya no se acuerda de la hora, y
    -- exigirla haría que se inventara una. Una hora inventada es peor que
    -- ninguna: manda a buscar en el log a la hora equivocada.
    hora            time,

    categoria       text NOT NULL CHECK (categoria IN (
                        'app',            -- la pantalla: se cerró, no dejó, se vio mal
                        'sincronizacion', -- no subió, no bajó, tardó
                        'impresora',      -- el ticket
                        'datos',          -- precio, cliente o producto mal en el catálogo
                        'proceso',        -- la forma de trabajar no encaja con la app
                        'equipo'          -- el teléfono: batería, pantalla, señal
                    )),

    -- Tres niveles, y el que decide todo es el primero. Un 'bloqueo' es que NO
    -- SE PUDO VENDER: es lo único que puede detener un despliegue.
    severidad       text NOT NULL CHECK (severidad IN (
                        'bloqueo',   -- no se pudo hacer la operación
                        'estorbo',   -- se pudo, con trabajo o con un rodeo
                        'molestia'   -- se pudo; está mal pero no costó nada
                    )),

    que_hacia       text NOT NULL,
    que_paso        text NOT NULL,

    -- Separado de la severidad a propósito: hay bloqueos que no cuestan una
    -- venta (no pudo consultar una cartera) y estorbos que sí (cobró en papel).
    costo_una_venta boolean NOT NULL DEFAULT false,
    hubo_que_usar_papel boolean NOT NULL DEFAULT false,

    -- Los minutos son la cifra que decide si la app le CUESTA dinero a la ruta.
    -- Una app impecable que agrega media hora diaria a un vendedor no se puede
    -- desplegar, y sin este número nadie lo nota hasta el tercer mes.
    minutos_perdidos integer NOT NULL DEFAULT 0
                     CHECK (minutos_perdidos BETWEEN 0 AND 600),

    resuelta_en     timestamptz,
    resolucion      text,

    registrado_en   timestamptz NOT NULL DEFAULT now(),
    registrado_por  uuid NOT NULL REFERENCES usuarios(id),

    CONSTRAINT incidencia_resuelta_lleva_como
        CHECK ((resuelta_en IS NULL) = (resolucion IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_piloto_incidencias_dia
    ON piloto_incidencias(piloto_id, fecha_operativa);

CREATE INDEX IF NOT EXISTS idx_piloto_incidencias_abiertas
    ON piloto_incidencias(piloto_id) WHERE resuelta_en IS NULL;

COMMENT ON TABLE piloto_incidencias IS
    'Bitácora del piloto. Catálogo cerrado para contar (categoría, severidad) y '
    'texto libre para entender: una incidencia pasa una vez y su valor es el '
    'detalle.';


-- ===========================================================================
-- piloto_criterios — la decisión, escrita ANTES de ver el dato
-- ===========================================================================
-- Esta tabla es la razón de que la migración valga la pena, y es la parte que
-- se salta todo el mundo.
--
-- Si los criterios de salida se deciden AL FINAL, se deciden mirando el
-- resultado, y entonces el piloto no decidió nada: justificó lo que ya se
-- quería hacer. Las dos semanas van a producir incidencias —para eso son—, y
-- en ese momento la pregunta «¿esto es suficiente para seguir?» ya no se puede
-- contestar con honestidad, porque los teléfonos ya se quieren comprar.
--
-- Así que los umbrales se siembran AQUÍ, en la migración, con fecha de antes
-- del primer día. Y **no hay pantalla para cambiarlos**: eso no es una función
-- que falte, es la propiedad que hace que sirvan. Cambiar un umbral a media
-- medición es cambiar la regla del juego con el partido empezado; si de verdad
-- estaba mal, se cambia con una migración, que deja huella y fecha.
CREATE TABLE IF NOT EXISTS piloto_criterios (
    codigo      text PRIMARY KEY,
    nombre      text NOT NULL,
    pregunta    text NOT NULL,
    umbral      numeric(14,4) NOT NULL,
    unidad      text NOT NULL CHECK (unidad IN
                    ('porcentaje','pesos','minutos','horas','cuenta')),
    comparacion text NOT NULL CHECK (comparacion IN ('maximo','minimo')),

    -- Bloqueante: si no se cumple, NO se despliega. Lo no bloqueante se mide
    -- porque informa, y se marca así para que no se use como excusa para
    -- detener un despliegue ni para aprobarlo.
    bloqueante  boolean NOT NULL DEFAULT true,

    -- Lo que este piloto NO puede evaluar, dicho en voz alta. Un criterio que
    -- no se puede medir y no se declara se convierte en un supuesto.
    evaluable   boolean NOT NULL DEFAULT true,

    nota        text,
    orden       integer NOT NULL
);

COMMENT ON TABLE piloto_criterios IS
    'Criterios de salida del piloto, sembrados por la migración ANTES de que '
    'empiece. No hay pantalla para editarlos: un umbral se escribe antes de ver '
    'el dato o no vale nada.';

INSERT INTO piloto_criterios
    (codigo, nombre, pregunta, umbral, unidad, comparacion, bloqueante, evaluable, nota, orden)
VALUES
 ('cobertura_papel',
  'El papel se capturó todos los días',
  '¿Qué porcentaje de las jornadas con venta en el sistema tiene su cuadre de papel capturado?',
  100, 'porcentaje', 'minimo', true, true,
  'Es el criterio del criterio: un piloto donde se dejó de capturar el papel el '
  'día cuatro no mide nada, y es la forma más común de que un piloto no pruebe '
  'nada sin que nadie lo note.', 10),

 ('faltantes_definitivos',
  'No falta ninguna venta del papel',
  '¿Cuántos documentos del papel siguen HOY sin aparecer en el sistema?',
  0, 'cuenta', 'maximo', true, true,
  'La falla que no perdona. Una venta que ocurrió y no está es dinero cobrado '
  'sin saber a quién se le vendió, y el sistema no puede detectarla solo.', 20),

 ('importe_cuadra',
  'El importe cuadra con el papel',
  '¿Cuánto se desvía el importe acumulado del sistema contra el del papel?',
  0.5, 'porcentaje', 'maximo', true, true,
  'Medio punto cubre el redondeo y algún error de dedo al capturar. Más que eso '
  'es que se está cobrando distinto de lo que se registra.', 30),

 ('cobranza_cuadra',
  'La cobranza cuadra con el papel',
  '¿Cuánto se desvía la cobranza acumulada del sistema contra la del papel?',
  0.5, 'porcentaje', 'maximo', true, true,
  'Va aparte del importe porque es otro problema: la cobranza es efectivo en la '
  'mano, y una diferencia ahí aparece en el arqueo del día.', 40),

 ('bloqueos_segunda_semana',
  'La segunda semana, sin bloqueos',
  '¿Cuántas incidencias que impidieron operar hubo a partir del día 8?',
  0, 'cuenta', 'maximo', true, true,
  'Y no "cero bloqueos en el piloto": la primera semana VA a tener bloqueos, '
  'para eso es el piloto. Lo que decide es si se arreglaron. Un umbral de cero '
  'sobre las dos semanas haría fracasar al piloto que funcionó.', 50),

 ('minutos_por_jornada',
  'La app no le cuesta la jornada',
  '¿Cuántos minutos al día, en promedio, perdió el vendedor por la app?',
  15, 'minutos', 'maximo', true, true,
  'Una app impecable que agrega media hora diaria no se puede desplegar: son '
  'tres horas a la semana de un vendedor, que valen más que el sistema.', 60),

 ('retraso_entrega',
  'Las ventas llegan el mismo día',
  '¿Cuántas horas después del cierre de su día operativo llegó la venta más tardía?',
  24, 'horas', 'maximo', true, true,
  'Se mide contra el CIERRE del día operativo, no contra el reloj del teléfono '
  '—que es el dato del que `desfase_reloj_seg` existe para desconfiar—. Cero '
  'significa que todo llegó dentro de su propio día; 24, que algo llegó un día '
  'tarde. El retraso en sí es normal y por diseño (§0.3); lo que no es normal '
  'es que la liquidación del día siguiente se haga con datos incompletos.', 70),

 ('cuarentena',
  'Nada quedó en cuarentena',
  '¿Cuántas operaciones del equipo del piloto quedaron en cuarentena?',
  0, 'cuenta', 'maximo', true, true,
  'El servidor marca y no rechaza (§0.1), así que la cuarentena es el camino de '
  'excepción. Una sola operación ahí, en un piloto de un vendedor, es un '
  'hallazgo que hay que entender antes de poner siete camiones.', 80),

 ('liquidaciones_cerradas',
  'Cada jornada cerró su liquidación',
  '¿Qué porcentaje de las jornadas del piloto tiene su liquidación cerrada y su cola vacía?',
  100, 'porcentaje', 'minimo', true, true,
  'Es la prueba de punta a punta: carga, venta, sincronización y cierre. Una '
  'jornada sin liquidación cerrada es una jornada que no se demostró completa.', 90),

 ('cancelaciones',
  'Las cancelaciones no son la forma de corregir',
  '¿Qué porcentaje de las ventas del piloto se canceló?',
  5, 'porcentaje', 'maximo', false, true,
  'Informativo, no bloqueante: una cancelación es un camino legítimo. Pero si '
  'una de cada diez ventas se cancela, la captura es incómoda y eso se arregla '
  'en la app, no en el proceso.', 100),

 ('faltantes_al_capturar',
  'Lo que faltaba en la mañana y llegó después',
  '¿Qué porcentaje de los documentos faltaba al capturar el cuadre y llegó más tarde?',
  10, 'porcentaje', 'maximo', false, true,
  'No es pérdida, es retraso: mide §0.3, no una falla. Se vigila porque un '
  'retraso que crece día con día es el síntoma temprano de un problema de red o '
  'de cola — el mismo número que, al final del piloto, dice si la oficina puede '
  'confiar en la cifra de la mañana.', 110),

 ('impresion_bluetooth',
  'El ticket impreso',
  '¿Imprime el ticket en la impresora del camión?',
  0, 'cuenta', 'maximo', false, false,
  'ESTE PILOTO NO LO PRUEBA. `Impresora` solo tiene implementación simulada '
  'hasta que llegue la EC-MP200, así que durante el piloto el comprobante del '
  'cliente sigue siendo la nota de papel. No bloquea el arranque del piloto '
  '—el papel va en paralelo de todos modos— pero sí bloquea el despliegue, y '
  'se valida aparte. Está en esta tabla para que no se convierta en un '
  'supuesto.', 120)
ON CONFLICT (codigo) DO NOTHING;


-- ===========================================================================
-- Permiso
-- ===========================================================================
-- Un permiso propio y no `ventas.ver_todas`: estas pantallas no son de
-- operación, son el instrumento de una decisión, y quien captura el papel está
-- escribiendo el patrón contra el que se mide el sistema.
INSERT INTO permisos (codigo, descripcion, modulo) VALUES
 ('piloto.administrar', 'Capturar y evaluar el piloto de campo', 'operacion')
ON CONFLICT (codigo) DO NOTHING;

-- Gerencia y supervisión, además de admin. El supervisor es quien ve al
-- vendedor cada mañana y trae el papel en la mano: si tuviera que pedirle a
-- otra persona que lo teclee, no se teclea. Es la misma razón por la que los
-- objetivos se copian del mes anterior con un botón.
INSERT INTO roles_permisos (rol_codigo, permiso_codigo) VALUES
 ('gerente',    'piloto.administrar'),
 ('supervisor', 'piloto.administrar')
ON CONFLICT DO NOTHING;


-- ===========================================================================
-- Sin políticas por renglón, y por la razón que ya escribió la 0022
-- ===========================================================================
-- La 0022 dejó fuera «las tablas de infraestructura y las de analítica y
-- tablero» porque no tienen dueño de ruta y están protegidas por permiso. Estas
-- cuatro son del mismo tipo: agregados de oficina detrás de
-- `piloto.administrar`, sin renglones de clientes. Poner una política por ruta
-- aquí sería inventar un dueño que estas tablas no tienen.
--
-- El rol `dsd_api` las alcanza por el `ALTER DEFAULT PRIVILEGES` de
-- `db/ops/rol_api.sql`, que existe justo para que una tabla nueva no deje a la
-- API con un "permission denied" en producción.
-- ===========================================================================
