-- =============================================================================
-- 0052 · Que el teléfono se entere de que la base se puso en blanco
-- =============================================================================
-- Reporte de la dirección (octubre 2026, ADR 0002 §90): después de poner la
-- base en blanco, «aparecen tiendas en la app que se borraron, y en el
-- dashboard no están, como debe de ser».
--
-- La base en blanco vacía las tablas con TRUNCATE, que no publica bajas, y
-- quita de `change_log` los deltas de lo que ya no existe. El teléfono que ya
-- tenía datos nunca recibe un «esto se borró»: se queda con sus tiendas, sus
-- artículos y sus ventas de antes, y nada se los quita. Bajar lo nuevo tampoco
-- lo cura: el cursor avanza, pero lo viejo sigue ahí.
--
-- Así que la base en blanco marca cada teléfono vinculado, y el pull de un
-- teléfono marcado contesta «resincroniza desde cero, y olvida lo de antes»
-- (`resincronizar` + `base_en_blanco`). El teléfono —con su cola ya entregada—
-- vacía su copia y vuelve a pedir desde el cursor 0. **La marca se quita sola
-- cuando el teléfono pide desde 0**: esa petición es la prueba de que ya
-- empezó de cero, y no hay que confiar en que le llegó una respuesta.
--
-- Va por teléfono y no con el piso de la poda (0034) a propósito: el piso
-- compara cursores, y un teléfono que bajó lo nuevo después de la base en
-- blanco ya tiene un cursor «al día» y sigue con las tiendas de antes —que es
-- exactamente el caso del reporte—.
--
-- LA BASE QUE YA SE PUSO EN BLANCO: si ya se hizo antes de esta migración (hay
-- un `en_blanco` en la auditoría), se marcan aquí mismo los teléfonos
-- registrados antes de esa hora, para que se limpien en su próxima
-- sincronización sin volver a borrar nada en el servidor.
-- =============================================================================

ALTER TABLE dispositivos
    ADD COLUMN IF NOT EXISTS empezar_de_cero boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN dispositivos.empezar_de_cero IS
    'La base se puso en blanco después de que este teléfono bajó datos: su pull '
    'le pide resincronizar desde cero y olvidar lo de antes. Se apaga sola '
    'cuando el teléfono pide desde el cursor 0.';

UPDATE dispositivos
   SET empezar_de_cero = true
 WHERE registrado_en < (SELECT max(ocurrido_en) FROM auditoria WHERE accion = 'en_blanco');
