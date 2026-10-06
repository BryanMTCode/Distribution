-- =============================================================================
-- 0035 · El camión asignado viaja al teléfono
-- =============================================================================
-- Hallazgo 5 de la auditoría de sincronización (§6.1), el único que quedaba
-- pendiente de decisión.
--
-- El teléfono estampa cada venta con `almacen_id` —su camión— y ese dato vive en
-- la credencial guardada, que **solo se reescribe cuando el vendedor entra con
-- señal**. Si la oficina le reasigna el camión mientras él trabaja offline, sus
-- ventas siguen saliendo con el camión anterior y descuentan del inventario
-- equivocado: el camión viejo se vacía sin que nadie lo cargue y el nuevo nunca
-- baja. El descuadre aparece en DOS liquidaciones y no se parece a nada.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- HOY NO PUEDE PASAR, Y SE CIERRA DE TODAS FORMAS
-- ─────────────────────────────────────────────────────────────────────────────
-- El camión se asigna al **crear** al vendedor (`panel/equipo`) y no hay pantalla
-- para reasignarlo, así que el único camino es un UPDATE a mano contra la base.
-- Es un hueco latente, no un defecto que esté ocurriendo.
--
-- Se cierra ahora por dos razones concretas: la lista de capacidades de edición
-- para gerencia sigue creciendo —«reasignar el camión» es la clase de botón que se
-- pide— y porque mientras el dato solo exista en la credencial, «la identidad del
-- equipo la manda el servidor» es casi cierto, y casi cierto es la peor forma de
-- una invariante.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- EL PAYLOAD LLEVA DOS CAMPOS, Y ESO ES DELIBERADO
-- ─────────────────────────────────────────────────────────────────────────────
-- `usuarios` guarda el `password_hash`. Un disparador genérico aquí volcaría la
-- fila entera —`to_jsonb(NEW)`— al `change_log`, que es una tabla que el
-- dispositivo **descarga**: el hash de la contraseña de un empleado viajaría por
-- la red y se quedaría en el SQLite de un teléfono. Así que este disparador no
-- usa `to_jsonb`: arma el objeto a mano con los dos campos que el teléfono
-- necesita, y cualquier campo nuevo tiene que agregarse aquí a propósito.
--
-- Tampoco viajan los permisos ni el código del vendedor:
--
--   · los **permisos** se quedan en la credencial, acotados por `valida_hasta`.
--     Mandarlos por delta significaría que un teléfono offline pueda ganar
--     permisos sin volver a autenticarse, que es al revés de lo que se quiere.
--   · el **código** (`VEND01`) es el prefijo del folio impreso. Cambiarlo a media
--     ruta haría que dos rangos de folios distintos compartieran prefijo en papel.
--     Si algún día hay que cambiarlo, es con el teléfono en la mano y los folios
--     cerrados, no con un delta.
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_registrar_cambio_identidad() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    -- Acotado al propio usuario: la identidad de Juan no le importa al teléfono
    -- de Pedro, y `vendedor_id` es justo el filtro que el pull ya aplica.
    INSERT INTO change_log (entidad, entidad_id, operacion, vendedor_id, payload)
    VALUES (
        'identidad',
        NEW.id,
        'upsert',
        NEW.id,
        -- A mano, nunca `to_jsonb(NEW)`: ver el encabezado. Aquí no entra el hash.
        jsonb_build_object(
            'usuario_id', NEW.id,
            'almacen_id', NEW.almacen_id
        )
    );
    RETURN NULL;
END;
$$;

COMMENT ON FUNCTION fn_registrar_cambio_identidad() IS
    'Publica al teléfono el camión que la oficina le asignó a su vendedor. Arma '
    'el payload a mano con dos campos: `usuarios` guarda el password_hash y el '
    'change_log es una tabla que el dispositivo descarga.';

DROP TRIGGER IF EXISTS trg_cambio_identidad ON usuarios;
CREATE TRIGGER trg_cambio_identidad
    AFTER UPDATE ON usuarios
    FOR EACH ROW
    WHEN (OLD.almacen_id IS DISTINCT FROM NEW.almacen_id)
    EXECUTE FUNCTION fn_registrar_cambio_identidad();

-- -----------------------------------------------------------------------------
-- LO QUE AQUÍ NO SE PUSO
-- -----------------------------------------------------------------------------
-- **El disparador en el INSERT.** Un usuario que acaba de nacer no tiene teléfono
-- vinculado, así que el delta no tendría a quién llegar; y cuando vincula, su
-- credencial trae el camión al día porque el vínculo exige estar en línea.
--
-- **Las rutas.** `usuarios_rutas` decide qué clientes le llegan, y eso ya lo
-- resuelve el acotamiento del pull en cada llamada: el servidor calcula las rutas
-- del token, no el teléfono. Un delta de rutas sería una segunda fuente de la
-- misma verdad.
