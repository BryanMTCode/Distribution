# ADR 0002 — Reglas de negocio de la operación

- **Fecha:** 2026-09-23
- **Estado:** Aceptada
- **Decidido por:** el dueño de la distribuidora
- **Cierra:** las decisiones abiertas de `ARQUITECTURA.md` §4

---

## 1. Modelo de venta: autoventa

El vendedor lleva la mercancía en el camión, ofrece, entrega físicamente en el momento y cobra. El
inventario se descuenta del camión al momento de la venta. **No hay preventa.**

**Consecuencia:** el modelo de datos ya existente es exactamente este. No se agrega documento de pedido
ni ciclo de entrega diferida. El almacén `CAMION_XX` con dueño exclusivo sigue siendo la garantía que
hace seguro el offline (§0.2).

Si algún día entra preventa, será un documento nuevo y un ciclo nuevo, no una variante de la venta
actual.

## 2. Unidades: pieza y caja

Solo `PZA` y `CAJA`. **Sin granel**, así que ninguna unidad es fraccionable.

**Consecuencias:**

- `unidades_medida` se siembra con esas dos (migración 0009).
- La escala de `numeric(14,3)` en cantidades se conserva aunque hoy todas sean enteras: cambiarla
  después, con movimientos de inventario ya registrados, es caro; dejarla, gratis.
- La validación de presentaciones (`domain/catalogo.py`) exige que la unidad base esté entre las
  vendibles con factor 1, que haya exactamente una por defecto y que no se repitan factores. Cada una
  de esas reglas corresponde a una forma real de descuadrar caja contra pieza.

## 3. Crédito: límite en dinero con bloqueo automático

Ventas de contado y a crédito. Cada cliente tiene un **límite de saldo en dinero**. Al excederlo:

- se **bloquea la venta a crédito** hasta que abone;
- **se le sigue vendiendo de contado**.

**Ésta es la regla correcta además de la pedida:** negar la venta de contado no cobra la deuda vieja y
sí pierde la venta nueva.

### La trampa del offline

El teléfono valida contra un saldo en caché que puede tener horas. Si el cálculo usara solo ese número,
el vendedor podría hacer cinco ventas a crédito en la misma mañana —cada una por debajo del límite— y
dejar al cliente al triple de su línea, porque ninguna alcanzó a sincronizar.

El saldo efectivo cuenta también la cola local, en los dos sentidos:

```
saldo_efectivo = saldo_confirmado     (lo que dice el servidor)
               + cargos_pendientes    (ventas a crédito locales sin sincronizar)
               - abonos_pendientes    (cobros locales; liberan línea de inmediato)
```

Los abonos cuentan tan rápido como los cargos: si el vendedor acaba de cobrarle en efectivo, la línea se
libera en ese momento. Hacerlo esperar a la sincronización sería negarle una venta que ya pagó.

### Dónde corre la regla

La misma función (`domain/credito.py`) se aplica en dos lugares y hace cosas distintas:

| Dónde | Cuándo | Qué hace |
|---|---|---|
| **Teléfono** | Antes de cerrar el carrito | **Bloquea de verdad.** Es el único momento en que bloquear sirve: la mercancía todavía no sale. |
| **Servidor** | Al recibir la venta | Solo **marca** `requiere_revision`. La mercancía ya salió y el cliente tiene su remisión impresa (§0.1). |

Rechazar en el servidor una venta ya entregada descuadraría la caja y el inventario sin devolver el
producto.

### Soporte en datos

- `v_cartera_cliente` (migración 0009) entrega un renglón por cliente con saldo, límite, disponible y
  `credito_agotado`. Lo consume el pull de sincronización y el panel.
- El endpoint `POST /v1/clientes/{id}/credito/evaluar` aplica la regla con datos reales del servidor,
  para poder contrastar ambas implementaciones contra la misma entrada.

## 4. Comprobante: remisión no fiscal

La remisión impresa por Bluetooth es suficiente por ahora. **El CFDI queda para una fase posterior.**

**Consecuencia:** no se construye módulo fiscal ni integración con PAC en la v1. Los campos `tasa_iva`,
`tasa_ieps` y `clave_sat` se conservan en el catálogo porque capturarlos desde el inicio cuesta cero y
llenarlos después, con miles de productos, cuesta semanas.

Recordatorio que no cambia: **imprimir un ticket no es facturar.** Lo que sale de la impresora es una
remisión.

## 5. Dispositivos: equipos de la empresa

Android de gama baja proporcionados por la distribuidora. **No hay BYOD.**

**Consecuencias:**

- El modelo de dispositivo ligado a usuario, con un solo equipo activo y revocación consultada en cada
  petición, es el correcto y ya está implementado.
- Al ser equipos de la empresa, en la Fase 9 se puede imponer **MDM** y borrado remoto sin negociarlo
  con nadie.
- El cifrado en reposo con SQLCipher sigue siendo obligatorio: el equipo es de la empresa, pero se
  pierde y se roba igual.

---

## Lo que estas decisiones NO cambiaron

El esquema de datos de la Fase 0 **no necesitó una sola modificación**: los campos de crédito, las
unidades con factor de conversión y el ciclo de autoventa ya estaban modelados. Lo que se agregó fue
datos de referencia, una vista de cartera y las reglas de negocio en `domain/`.

Eso es consecuencia de haber empezado por el modelo de datos y no por el framework.

---

## Pendiente, no bloqueante

**Lotes y caducidad.** No se definió. El esquema lo soporta (`productos.maneja_lote`,
`dias_caducidad`, `movimientos_inventario.lote`), y hoy todos los productos se dan de alta con
`maneja_lote = false`.

Es una decisión operativa, no técnica: manejar lotes en el camión exige que el vendedor los distinga
físicamente al cargar y al liquidar. En abarrotes con caducidad corta suele valer la pena; en abarrotes
secos, casi nunca. Conviene decidirlo antes de la Fase 4 (inventario de camión), porque cambia la
pantalla de carga y la de liquidación.
