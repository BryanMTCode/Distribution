-- =============================================================================
-- 0032 · La oficina puede ajustar el camión, y el teléfono se entera
-- =============================================================================
-- Hasta aquí el inventario de un camión solo lo movían tres cosas: la carga, los
-- documentos del vendedor (venta, merma, devolución) y el cierre de la
-- liquidación. La oficina no tenía forma de corregir un número, y el único
-- camino era esperar al cierre del día.
--
-- Eso deja descuadres vivos durante horas. El vendedor llama: «traigo 12 cajas de
-- sopa y el sistema dice 30». Hoy la respuesta es «lo vemos en la liquidación», y
-- mientras tanto el catálogo del teléfono le ofrece 30 cajas a los clientes.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- ESTO ES UNA EXCEPCIÓN A §0.2, Y HAY QUE DECIRLO ASÍ
-- ─────────────────────────────────────────────────────────────────────────────
-- §0.2 dice que el almacén del camión tiene un **único dueño exclusivo**, y la
-- migración 0025 lo escribió con todas sus palabras al prohibir las entradas
-- directas a un camión:
--
--   «Una entrada directa a un camión le cambiaría el inventario bajo los pies a
--    alguien que está vendiendo offline con otra cifra en el teléfono, y el
--    descuadre le aparecería en su liquidación como un sobrante del que no sabe
--    nada.»
--
-- La objeción sigue siendo correcta, y es exactamente la que esta migración
-- resuelve en vez de ignorar: **el ajuste se publica como delta**, así que el
-- teléfono lo aplica y los dos saldos convergen. Lo que queda es la ventana entre
-- escribirlo y sincronizar, que es la misma ventana que tiene todo lo demás en
-- este sistema (§0.3: «tiempo real» es el tiempo real de lo que ha sincronizado).
--
-- Y no es la primera excepción: el cierre de la liquidación ya escribe ajustes
-- directos sobre el camión desde la 0030. Esta es la segunda, con nombre y con
-- documento.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- UN DOCUMENTO PROPIO, Y NO `entradas`/`salidas`
-- ─────────────────────────────────────────────────────────────────────────────
-- Habría sido más corto relajar `TIPOS_DE_DESTINO` y dejar que las pantallas de
-- bodega aceptaran camiones. Se descartó por tres razones:
--
--   1. Esas pantallas son de la bodega hasta en el nombre de sus funciones, y sus
--      pruebas afirman que un camión no entra ahí. Relajar la constante volvería
--      falsa esa afirmación en dos pantallas para habilitar una tercera.
--   2. Ninguna de las dos publica deltas, que es la mitad del requisito.
--   3. Una excepción a §0.2 tiene que ser fácil de encontrar y de auditar. Una
--      tabla que se llama `ajustes_camion` lo es; un renglón más en la tabla de
--      entradas de bodega, no.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- SE CAPTURA LO QUE HAY, Y LA BASE CALCULA EL DELTA
-- ─────────────────────────────────────────────────────────────────────────────
-- Es la doctrina de la 0026, y aquí vale igual: quien corrige un descuadre sabe
-- lo que hay («12 cajas»), no la diferencia («faltan 18»). La resta a mano es de
-- donde salen los errores que este documento viene a corregir, así que se guardan
-- las tres cifras y el `CHECK` impone la aritmética — un bug en Python no puede
-- escribir un ajuste que no cuadre.
--
-- El delta va FIRMADO, en una sola columna, al contrario que en bodega —donde una
-- entrada y una salida son dos documentos distintos—. En el camión los dos
-- sentidos son el mismo acto («el número está mal, aquí está el correcto») y
-- partirlo en dos pantallas obligaría a quien corrige a decidir primero el signo.
-- =============================================================================

CREATE TABLE IF NOT EXISTS ajustes_camion (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    folio                   text NOT NULL UNIQUE,

    -- El camión. Lo valida la pantalla: aquí no se restringe por tipo porque un
    -- ajuste a una bodega también sería legítimo —solo que para eso ya existen
    -- las entradas y salidas, que llevan más control—.
    almacen_id              uuid NOT NULL REFERENCES almacenes(id),
    producto_id             uuid NOT NULL REFERENCES productos(id),

    tipo                    text NOT NULL
                            CHECK (tipo IN ('conteo','merma','entrada')),

    -- Las tres cifras del conteo. `contado` es NULL en merma y entrada: ahí no se
    -- contó el camión, se registró un movimiento conocido.
    existencia_al_capturar  numeric(14,3) NOT NULL,
    contado                 numeric(14,3) CHECK (contado IS NULL OR contado >= 0),

    -- Lo que se le SUMA al camión. Negativo baja, positivo sube, cero no existe:
    -- un ajuste que no ajusta nada es un renglón que solo estorba al auditar.
    delta                   numeric(14,3) NOT NULL CHECK (delta <> 0),

    -- Obligatorio en una merma, por lo mismo que en la 0026: una pérdida
    -- identificada y un descuadre sin explicar son cosas distintas, y el catálogo
    -- de motivos ya existe y ya se sincroniza.
    motivo_codigo           text REFERENCES motivos_merma(codigo),

    -- Obligatoria SIEMPRE, y es la diferencia más importante con un ajuste de
    -- bodega: esto cambia el inventario del camión de una persona, que va a tener
    -- que explicarlo en su liquidación. Sin nota, el día que lo discuta no hay
    -- nada que leer.
    nota                    text NOT NULL CHECK (length(btrim(nota)) >= 10),

    usuario_id              uuid NOT NULL REFERENCES usuarios(id),
    creado_en               timestamptz NOT NULL DEFAULT now(),

    -- La aritmética del conteo, impuesta por la base.
    CONSTRAINT conteo_cuadra
        CHECK (contado IS NULL OR delta = contado - existencia_al_capturar),
    -- Un conteo captura lo contado; los otros dos no.
    CONSTRAINT conteo_trae_contado
        CHECK ((tipo = 'conteo') = (contado IS NOT NULL)),
    CONSTRAINT merma_exige_motivo
        CHECK (tipo <> 'merma' OR motivo_codigo IS NOT NULL),
    -- Una merma solo puede bajar y una entrada solo puede subir. Si el signo
    -- pudiera ir al revés, «merma» dejaría de significar algo al leer el historial.
    CONSTRAINT merma_resta CHECK (tipo <> 'merma' OR delta < 0),
    CONSTRAINT entrada_suma CHECK (tipo <> 'entrada' OR delta > 0)
);

CREATE INDEX IF NOT EXISTS idx_ajustes_camion_almacen
    ON ajustes_camion(almacen_id, creado_en DESC);

CREATE SEQUENCE IF NOT EXISTS seq_folio_ajuste_camion AS bigint START WITH 1;

COMMENT ON TABLE ajustes_camion IS
    'Correcciones de la oficina al inventario de un camión. Excepción documentada '
    'a §0.2: el saldo lo escribe la oficina y el delta viaja al teléfono para que '
    'el dueño del almacén converja. Ver el encabezado de la migración 0032.';

-- -----------------------------------------------------------------------------
-- El delta hacia el teléfono
-- -----------------------------------------------------------------------------
-- Viaja el DELTA FIRMADO, no el saldo resultante, y la razón es la misma que en
-- el cierre de la liquidación (0030): el vendedor puede estar vendiendo mientras
-- la oficina corrige. Un saldo —«el camión tiene 12»— aplicado cinco minutos
-- después borraría las ventas de esos cinco minutos. Un delta se suma a lo que
-- haya y sigue siendo correcto cuando llega tarde.
--
-- El precio de esa elección es que sumar dos veces está mal, y un `pull` se
-- repite cuando la red se corta. De eso se encarga el teléfono con su tabla de
-- ajustes ya aplicados, igual que con las cargas.
--
-- Se acota al vendedor responsable del camión: el ajuste del camión de Juan no le
-- importa al teléfono de Pedro.
CREATE OR REPLACE FUNCTION fn_registrar_ajuste_camion() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_responsable uuid;
BEGIN
    SELECT responsable_id INTO v_responsable
      FROM almacenes WHERE id = NEW.almacen_id;

    INSERT INTO change_log (entidad, entidad_id, operacion, vendedor_id, payload)
    VALUES (
        'ajuste_camion',
        NEW.id,
        'upsert',
        v_responsable,
        jsonb_build_object(
            'id',          NEW.id,
            'folio',       NEW.folio,
            'producto_id', NEW.producto_id,
            -- Cantidad como TEXTO con sus tres decimales
            -- (contracts/README.md §1.4), para que `Cantidad.deTexto` la consuma
            -- sin que ningún `double` toque el número.
            'delta',       NEW.delta::text,
            'tipo',        NEW.tipo,
            'nota',        NEW.nota
        )
    );
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_ajuste_camion ON ajustes_camion;
CREATE TRIGGER trg_ajuste_camion
    AFTER INSERT ON ajustes_camion
    FOR EACH ROW EXECUTE FUNCTION fn_registrar_ajuste_camion();

-- -----------------------------------------------------------------------------
-- LO QUE AQUÍ NO SE PUSO
-- -----------------------------------------------------------------------------
-- **Un CHECK que impida dejar el camión en negativo.** Un camión SÍ puede quedar
-- negativo y el esquema lo permite desde la 0004: una venta offline puede entrar
-- cuando el conteo ya marcaba cero (§0.1). Lo que la pantalla sí impide es que el
-- ajuste lo EMPUJE a negativo, que es distinto — eso ya no es un hecho que llegó
-- tarde, es alguien capturando de más.
--
-- **Cancelar un ajuste.** El libro mayor es append-only y un ajuste es un asiento:
-- lo que corrige a un ajuste es otro ajuste, con su nota diciendo por qué.
