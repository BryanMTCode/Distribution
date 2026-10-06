-- =============================================================================
-- 0036 · El vendedor devuelve mercancía a la bodega
-- =============================================================================
-- La migración 0004 creó `traspasos` y `traspaso_detalle` y escribió la regla que
-- los justifica: «La oficina NUNCA hace UPDATE sobre existencias de un camión.
-- Propone un traspaso; el vendedor lo acepta en la app.» Las tablas llevan dos
-- años ahí sin que nada las escriba, y yo las describí como disponibles al
-- documentar el cierre del camión rodante — no lo eran.
--
-- Hoy la única forma de bajar mercancía de un camión a la bodega son dos ajustes
-- independientes, uno en cada almacén. Las dos cifras acaban bien y **no queda
-- ningún documento que ate los dos lados**: el día que alguien pregunte «¿quién
-- bajó esas 18 cajas y quién las recibió?», no hay qué leer.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- QUIÉN LO INICIA, Y POR QUÉ NO ES LA OFICINA
-- ─────────────────────────────────────────────────────────────────────────────
-- La 0004 imaginó el sentido contrario —bodega → camión, que la oficina propone y
-- el vendedor acepta— y para ese sentido es correcto: la oficina no puede meterle
-- mercancía al camión de alguien sin su consentimiento.
--
-- Para camión → bodega el dueño del origen es el vendedor, así que **él lo
-- inicia**, desde su teléfono y sin señal: es el único que sabe que acaba de bajar
-- 18 cajas, y exigirle conexión para registrarlo haría que lo apuntara en papel.
-- Es el mismo trato que una merma, por la misma razón.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- Y POR QUÉ PASA POR TRÁNSITO EN VEZ DE LLEGAR DERECHO A LA BODEGA
-- ─────────────────────────────────────────────────────────────────────────────
-- Si la declaración del vendedor moviera la mercancía directo a la bodega, un
-- vendedor podría cubrir un faltante escribiendo una devolución que nunca entregó:
-- su camión baja, la bodega sube, y nadie contó nada. Sería la única operación del
-- sistema donde la palabra de una persona mueve dos almacenes.
--
-- Así que la declaración mueve **camión → TRÁNSITO**, que es el almacén que el
-- esquema ya tenía previsto (`almacenes.tipo IN ('bodega','camion','transito',
-- 'merma')`) y nadie había usado. La bodega sube cuando **alguien la recibe y dice
-- cuánto contó**. Lo que no cuadre se queda en tránsito, con nombre y con fecha:
-- es una diferencia visible en vez de una confianza invisible.
--
-- §0.1 se respeta de los dos lados: la mercancía ya salió del camión —eso pasó, y
-- se registra sin pedir permiso— y lo que la bodega recibió es lo que contó.
-- =============================================================================

CREATE SEQUENCE IF NOT EXISTS seq_folio_traspaso AS bigint START WITH 1;

COMMENT ON SEQUENCE seq_folio_traspaso IS
    'Folio del traspaso. Lo asigna el SERVIDOR al recibir el documento, no el '
    'teléfono: un traspaso no se le entrega a un cliente, así que no necesita un '
    'folio impreso offline y no vale la pena darle un rango de folios propio.';

ALTER TABLE traspasos
    ADD COLUMN IF NOT EXISTS dispositivo_id    uuid REFERENCES dispositivos(id),
    ADD COLUMN IF NOT EXISTS fecha_dispositivo timestamptz,
    ADD COLUMN IF NOT EXISTS fecha_operativa   date,
    ADD COLUMN IF NOT EXISTS observaciones     text;

-- Quien recibe y cuándo NO llevan columnas nuevas: son `resuelto_por` y
-- `resuelto_en`, que la 0004 puso justo para la transición propuesto → aceptado.
-- La primera versión de esta migración agregaba `recibido_por`/`recibido_en` al
-- lado de ellas, y dos pares de columnas con el mismo significado son dos
-- verdades que un día no coinciden.
COMMENT ON COLUMN traspasos.resuelto_por IS
    'Quién recibió la mercancía y la contó. Es el mismo campo que resuelve un '
    'traspaso en el sentido bodega → camión: recibir ES resolver.';
COMMENT ON COLUMN traspasos.resuelto_en IS
    'Cuándo se recibió y contó. En la app del vendedor se lee «recibida el…».';

-- El folio deja de ser obligatorio al nacer: lo pone el servidor en el mismo
-- INSERT, pero un traspaso que llegue sin él no debe romper la ingesta.
ALTER TABLE traspasos ALTER COLUMN folio DROP NOT NULL;

COMMENT ON COLUMN traspasos.fecha_dispositivo IS
    'Cuándo lo capturó el vendedor en su teléfono. Puede ser horas antes de que '
    'llegue al servidor: el documento nace offline.';

-- Lo que la bodega CONTÓ al recibir. NULL mientras está en tránsito.
--
-- Se guarda aparte de `cantidad` —lo declarado— y no se sobrescribe: la diferencia
-- entre las dos es el dato que el cierre de este documento existe para producir.
ALTER TABLE traspaso_detalle
    ADD COLUMN IF NOT EXISTS cantidad_recibida numeric(14,3)
        CHECK (cantidad_recibida IS NULL OR cantidad_recibida >= 0);

CREATE INDEX IF NOT EXISTS idx_traspasos_en_transito
    ON traspasos(almacen_destino_id, creado_en DESC) WHERE estado = 'propuesto';

-- -----------------------------------------------------------------------------
-- El delta hacia el teléfono: «lo que entregaste ya se recibió»
-- -----------------------------------------------------------------------------
-- El vendedor declaró 18 cajas y las dejó en la bodega. Lo que necesita ver
-- después es si alguien las recibió y cuántas contó — es su comprobante de que la
-- mercancía dejó de ser su responsabilidad.
--
-- Solo se publica cuando el ESTADO cambia. El INSERT no: el documento nace en el
-- teléfono y devolvérselo sería un eco. Y acotado al vendedor que lo hizo: el
-- traspaso de Juan no le importa al teléfono de Pedro.
CREATE OR REPLACE FUNCTION fn_registrar_cambio_traspaso() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_detalle jsonb;
BEGIN
    SELECT COALESCE(
             jsonb_agg(
               jsonb_build_object(
                 'producto_id',       d.producto_id,
                 -- Cantidades como TEXTO con sus tres decimales (contracts §1.4).
                 'cantidad',          d.cantidad::text,
                 'cantidad_recibida', d.cantidad_recibida::text
               )
               ORDER BY d.producto_id
             ),
             '[]'::jsonb
           )
      INTO v_detalle
      FROM traspaso_detalle d
     WHERE d.traspaso_id = NEW.id;

    INSERT INTO change_log (entidad, entidad_id, operacion, vendedor_id, payload)
    VALUES (
        'traspaso',
        NEW.id,
        'upsert',
        NEW.solicitado_por,
        jsonb_build_object(
            'id',             NEW.id,
            'folio',          NEW.folio,
            'estado',         NEW.estado,
            'resuelto_en',    NEW.resuelto_en,
            'motivo_rechazo', NEW.motivo_rechazo,
            'detalle',        v_detalle
        )
    );
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_cambio_traspaso ON traspasos;
CREATE TRIGGER trg_cambio_traspaso
    AFTER UPDATE ON traspasos
    FOR EACH ROW
    WHEN (OLD.estado IS DISTINCT FROM NEW.estado)
    EXECUTE FUNCTION fn_registrar_cambio_traspaso();

-- -----------------------------------------------------------------------------
-- LO QUE AQUÍ NO SE PUSO
-- -----------------------------------------------------------------------------
-- **Un CHECK que obligue a que `cantidad_recibida <= cantidad`.** La bodega puede
-- contar MÁS de lo declarado —el vendedor bajó una caja extra sin anotarla— y eso
-- es un hecho físico, no un error de captura. Se registra y queda la diferencia.
--
-- **El sentido bodega → camión.** Los estados `propuesto`/`aceptado` de la 0004
-- siguen ahí para él, y sigue sin construirse: hoy la bodega carga el camión con
-- una CARGA, que es el documento correcto para eso. Un traspaso bodega → camión
-- solo haría falta para una resurtida a media ruta, que nadie ha pedido.
