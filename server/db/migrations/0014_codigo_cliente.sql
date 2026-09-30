-- =============================================================================
-- 0014 · El consecutivo del código de cliente
-- =============================================================================
-- Un cliente dado de alta en la calle nace SIN código: `clientes.codigo` es
-- "consecutivo asignado por el SERVIDOR" (0003) y el teléfono no puede
-- asignarlo sin arriesgarse a repetirlo. Nace como `prospecto` y espera a que
-- alguien en la oficina lo confirme.
--
-- Hasta hoy nada podía confirmarlo: no existía de dónde sacar el consecutivo.
-- Un prospecto se quedaba prospecto para siempre, y con él su límite de
-- crédito en cero.
--
-- POR QUÉ UNA SECUENCIA Y NO max(codigo) + 1
-- ------------------------------------------------------------------------
-- `max() + 1` da el mismo número a dos personas que confirmen al mismo tiempo,
-- y el UNIQUE de `codigo` hace que una de las dos vea un error que no entiende.
-- Una secuencia entrega números distintos sin bloquear a nadie.
--
-- Deja huecos cuando una transacción se deshace, y eso está bien: el código
-- identifica a un cliente, no cuenta clientes. Un consecutivo sin huecos exige
-- serializar las altas, que es un precio absurdo por una estética.
--
-- POR QUÉ NO ES UN DEFAULT DE LA COLUMNA
-- ------------------------------------------------------------------------
-- Si `codigo` se asignara solo al insertar, el alta de campo gastaría un número
-- para un negocio que la oficina todavía no aceptó —y que puede resultar
-- duplicado del de la esquina de enfrente. El código se asigna en el momento de
-- la decisión humana, no en el del INSERT.
-- =============================================================================

CREATE SEQUENCE IF NOT EXISTS seq_codigo_cliente AS bigint START WITH 1;

-- Arranca después del mayor código con la forma C00001 que ya exista, para que
-- una base con clientes cargados a mano no choque contra el UNIQUE.
-- El tercer argumento en `false` significa "el siguiente nextval devuelve
-- exactamente este número": sin él la secuencia se saltaría el C00001.
SELECT setval(
    'seq_codigo_cliente',
    (SELECT COALESCE(MAX(substring(codigo FROM 2)::bigint), 0) + 1
       FROM clientes
      WHERE codigo ~ '^C[0-9]+$'),
    false
);

COMMENT ON SEQUENCE seq_codigo_cliente IS
    'Consecutivo de clientes/codigo. Se consume al CONFIRMAR un prospecto en el panel.';
