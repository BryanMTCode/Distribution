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

## 6. Lotes y caducidad: apagados

**No se manejan lotes.** El giro es abarrote seco: rastrearlos alentaría la carga del camión y la
liquidación sin dar beneficio en la calle.

**Consecuencias:**

- Los productos se dan de alta con `maneja_lote = false` y las pantallas de carga y liquidación no
  piden lote.
- **El esquema conserva el soporte** (`productos.maneja_lote`, `dias_caducidad`,
  `movimientos_inventario.lote`, `carga_detalle.lote`). Dejarlo cuesta cero; agregarlo después, con
  movimientos de inventario ya registrados, cuesta una migración delicada.
- Si algún día entra una línea con caducidad corta —lácteos, pan—, se enciende por producto sin tocar
  el modelo.

---

## 7. Precio rígido: el vendedor NO otorga descuentos en la calle

- **Fecha:** 2026-09-29
- **Decidido por:** el dueño de la distribuidora
- **Carácter:** regla inmutable de negocio

El precio unitario es **estrictamente** el de la lista de precios asignada a ese cliente. **No hay
descuentos, no hay campos editables de precio, y no hay flujo de autorización.** Todo cálculo de
importe es rígido.

### Cuál columna es "el precio"

El esquema tiene dos: `precios.precio` (precio de venta) y `precios.precio_minimo` (piso, nullable).
Con cero descuentos los dos conceptos **colapsan en un solo número**, y ese número es **`precios.precio`**
— es el que existe siempre, el que el servidor emite en el delta y el que se imprime.

`precio_minimo` queda **obsoleto para esta operación**. La columna se conserva porque el servidor la
emite y quitarla no aporta nada; el dispositivo la ignora.

### Cómo se implementa: estructuralmente, en tres capas

Una regla de este peso no se implementa con una validación. Una validación se puede saltar con un `if`
mal puesto seis meses después, por alguien que no leyó este documento. Se implementa quitando la
posibilidad:

1. **El teléfono no tiene dónde escribir un precio.** `Carrito.agregar()` recibe una
   `PresentacionVendible` —que trae su precio ya resuelto del catálogo— y una cantidad. **El parámetro
   de precio no existe**, ni el de descuento. La pantalla del pedido no tiene un solo `TextField`, y
   hay una prueba de widget que lo verifica.
2. **El servidor recalcula al recibir** (`app/domain/importes.py`).
3. **PostgreSQL lo verifica al escribir** (migración 0012,
   `CHECK (importe = ROUND(cantidad * precio_unitario, 2) - descuento)`).

Los tres cálculos deben dar **el mismo centavo**. Si no, una venta legítima cae en revisión por un
redondeo, y una bandera de revisión que se enciende sola se acaba ignorando — que es peor que no
tenerla. El árbitro es `contracts/importes_de_ejemplo.json`, el quinto contrato ejecutable.

### Consecuencia técnica: el precio necesita 4 decimales

**No es un detalle de implementación, es una consecuencia directa de esta regla.** El precio por pieza
sale de dividir el de la caja:

| | Precio unitario | 24 piezas |
|---|---|---|
| Con 2 decimales | $12.33 | **$295.92** ❌ |
| Con 4 decimales | $12.3333 | **$296.00** ✅ |

Una caja de 24 a $296.00 da $12.3333 por pieza. Con el precio guardado en centavos, la caja completa
valdría $295.92 y el cliente lo reclamaría con la lista en la mano — y el vendedor **no podría
corregirlo**, porque no puede alterar precios. La rigidez obliga a que el número sea exacto de origen.

De ahí el tipo `Precio` (diezmilésimos) separado de `Dinero` (centavos), con la misma escala que
`numeric(14,4)` del servidor, y **un solo redondeo, al final**, en el importe de la línea.

### Lo que esta regla NO prohíbe

`venta_partidas.descuento` se conserva y el CHECK lo admite en la fórmula. La regla sellada es **"el
vendedor no decide el precio"**, no "el descuento no existe": el día que la **oficina** active una
promoción (`promociones` ya está modelada: `nxm`, `regalo`, `descuento_pct`), el descuento lo pondrá
ella, la aritmética tiene que seguir cuadrando, y un CHECK que exigiera `descuento = 0` rechazaría
ventas legítimas — violando §0.1.

La diferencia es quién decide: la oficina sí, el vendedor nunca.


---

## 8. La remisión se imprime a un toque, no sola

- **Fecha:** 2026-09-29
- **Decidido por:** el dueño de la distribuidora

Al confirmar la venta, **la remisión NO se imprime automáticamente**. El vendedor
ve la venta ya guardada —con su folio grande— y toca **"Imprimir"**.

### Por qué no automática

La impresión automática ahorra un toque. Lo que cuesta es peor: cuando la
impresora está sin papel, apagada o desemparejada —y en ruta pasa—, la venta ya
quedó escrita y el vendedor se queda **sin un lugar obvio desde dónde
reintentar**. Tendría que buscar la venta en otra pantalla, con el cliente
esperando enfrente.

Con el botón, el reintento es el mismo botón, tantas veces como haga falta.

### Consecuencias

- La primera impresión marca `ventas.impreso` y **congela el payload ESC/POS**
  en `ticket_escpos`. Las siguientes cuentan como `reimpresiones` y **no
  recalculan el ticket**: una reimpresión tiene que salir idéntica al original,
  marcada como copia.
- Una venta sin imprimir es un estado válido y visible. La oficina puede
  preguntar por qué, que es justo lo que no podría hacer si la impresión fuera
  un efecto secundario invisible del guardado.

## 9. El aviso de existencia se dice en piezas, no en decimales

- **Fecha:** 2026-09-29 (prueba de campo en POCO M5s)

Cuando no alcanza la mercancía, el aviso dice **"Solo quedan 2 cajas y 6 piezas
en el camión"**, no *"solo quedan 2.500 cajas"*.

El decimal obliga al vendedor a traducirlo de cabeza frente al cliente, y **media
caja no existe en un camión**: lo que existe son 2 cajas y 6 piezas sueltas, que
es exactamente lo que le va a decir al cliente. El dominio devuelve el desglose
(`DesgloseDisponible`) y la pantalla lo redacta; la unidad se nombra en español
según su código.
