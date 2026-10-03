-- ===========================================================================
-- 0028 · La regla del §2.3, que estaba escrita y nada imponía
-- ===========================================================================
-- `docs/ARQUITECTURA.md` §2.3 declara, sobre el riesgo de restaurar un respaldo
-- viejo del teléfono:
--
--     «no se puede iniciar una carga nueva con operaciones pendientes del día
--      anterior.»
--
-- Nada la imponía. `cargas.py` solo comprobaba que ese vendedor no tuviera YA
-- una carga del MISMO día (`uq_carga_vendedor_dia`). Una salvaguarda diseñada
-- hasta la frase y nunca construida — la misma huella que `inventario.ajustar`
-- sin dueño y `merma_detalle.costo_unitario` sin quien lo escriba.
--
-- ---------------------------------------------------------------------------
-- QUÉ PASA SIN ELLA, Y POR QUÉ ES SILENCIOSO
-- ---------------------------------------------------------------------------
-- El escenario es ordinario en una ruta con mala señal, no exótico:
--
--   1. El lunes el teléfono se queda con tres ventas sin subir.
--   2. El lunes se cierra la liquidación. `sync_completa` queda en `false`
--      porque el equipo reportó cola — eso ya se registra honestamente.
--   3. El martes a las 6 am se carga el camión. La carga confirmada publica el
--      delta y el teléfono reescribe sus existencias con el nuevo snapshot.
--   4. El teléfono sincroniza. Las tres ventas llegan con su `fecha_operativa`
--      DEL LUNES.
--   5. Los modelos de lectura recalculan días sucios, así que la venta del lunes
--      sube — y el `efectivo_esperado` de una liquidación YA CERRADA se calculó
--      antes de esas tres ventas.
--
-- El arqueo que alguien firmó el lunes deja de cuadrar con las ventas que el
-- sistema tiene del lunes. No es corrupción —cada dato es correcto por
-- separado— es un cierre firmado contra una cifra que después se movió.
--
-- ---------------------------------------------------------------------------
-- SE BLOQUEA AL CONFIRMAR, NO AL CREAR EL BORRADOR
-- ---------------------------------------------------------------------------
-- El borrador no mueve inventario ni publica delta —el disparador de la 0015 lo
-- salta explícitamente— así que dejarlo existir no cuesta nada. Y mientras
-- alguien captura quince renglones, el teléfono puede sincronizar en el patio y
-- el bloqueo desaparece solo. Bloquear la creación detendría trabajo que muy
-- seguido se vuelve innecesario.
--
-- ---------------------------------------------------------------------------
-- Y SE PUEDE FORZAR, CON SU RAZÓN ESCRITA
-- ---------------------------------------------------------------------------
-- Mismo patrón que el cierre de liquidación con `confirmo_sincronizado`: el
-- servidor bloquea por omisión y una persona puede pasar por encima dejando
-- constancia. No es debilidad: es la única forma de que la regla sobreviva al
-- contacto con la operación. A las 6 am el camión TIENE que salir, y una regla
-- que deja la ruta en la bodega se desactiva a la semana.
--
-- Lo que no puede pasar es que se fuerce en silencio.
--
-- ---------------------------------------------------------------------------
-- DÓNDE SE GUARDA LA RAZÓN, Y POR QUÉ NO EN `cargas`
-- ---------------------------------------------------------------------------
-- `cargas` LLEVA disparador de change_log (`fn_registrar_cambio_carga`, 0015) y
-- publica `to_jsonb(NEW)` — la fila completa — al teléfono del vendedor cuando
-- la carga se confirma. Una columna de texto libre donde la oficina escribe «el
-- teléfono de Juan no sincronizó y el camión tiene que salir» viajaría al SQLite
-- de Juan. Es el mismo camino de fuga que la 0027 evitó con el costo.
--
-- Quitar la columna del disparador exigiría reproducir sus 74 líneas con un
-- `CREATE OR REPLACE` en esta migración, y dos copias de esa lógica en el
-- repositorio es el defecto de «dos reglas iguales escritas dos veces» que ya se
-- pagó una vez con `ROLES_DE_OFICINA`.
--
-- Así que la razón va a `auditoria`, que existe desde la migración 0001 con
-- exactamente la forma que hace falta —`entidad`, `entidad_id`, `accion`,
-- `usuario_id`, `motivo`, `datos_despues`—, no lleva disparador, y hasta hoy
-- NADIE LA ESCRIBÍA. Es el tercer huérfano de este repositorio, y esta es su
-- primera escritura.
--
-- En `cargas` quedan solo dos campos que al teléfono no le estorban: un número y
-- una bandera. Lo que no debe salir de la oficina es el texto.
-- ===========================================================================

-- Cuántas operaciones reportaron los equipos del vendedor al confirmar. Se
-- escribe SIEMPRE, incluso en cero: es el número que contesta «¿estaba el equipo
-- al día cuando se cargó?» sin depender de que alguien se acuerde. Cero es un
-- dato, no una ausencia de dato.
ALTER TABLE cargas
    ADD COLUMN IF NOT EXISTS pendientes_al_confirmar integer
    CHECK (pendientes_al_confirmar IS NULL OR pendientes_al_confirmar >= 0);

-- La bandera, para que la lista del panel pueda señalar las cargas que alguien
-- forzó sin consultar la auditoría en cada renglón.
ALTER TABLE cargas
    ADD COLUMN IF NOT EXISTS forzada boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN cargas.pendientes_al_confirmar IS
    'Operaciones sin subir que reportaron los equipos del vendedor al confirmar '
    'la carga. Cero es un dato, no una ausencia de dato.';

COMMENT ON COLUMN cargas.forzada IS
    'Se confirmó a pesar de los bloqueos del §2.3. La razón está en `auditoria` '
    'con accion = ''carga_forzada'': esta tabla se publica al teléfono y el texto '
    'no debe salir de la oficina.';

-- Las cargas forzadas son las que alguien va a querer revisar después.
CREATE INDEX IF NOT EXISTS idx_cargas_forzadas
    ON cargas(fecha_operativa DESC) WHERE forzada;
