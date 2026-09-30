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


---

## 10. El mapa es un lienzo relativo, no mosaicos descargados

- **Fecha:** 2026-09-30
- **Decidido por:** el dueño de la distribuidora

La pantalla de alta muestra un **radar relativo**: el punto donde está el
vendedor al centro, los clientes conocidos alrededor a su distancia y rumbo
reales, con anillos de distancia etiquetados. **No descarga nada.**

### Por qué no un mapa con calles

Un mapa de mosaicos necesita red, y **la corrección de coordenadas se hace justo
donde no hay** —dentro de un mercado techado, en una colonia sin cobertura—. Un
mapa que se queda en cuadros grises es peor que no tener mapa: ocupa la pantalla
y no dice nada.

Se descartó también pre-descargar mosaicos por ruta: el peso de descarga y el
mantenimiento (qué zona, cada cuánto, qué pasa cuando la ruta cambia) no se paga
contra lo que aporta. La referencia **relativa** es la que sirve para decidir:
"la tienda que ya tengo registrada está a 30 m al norte, entonces esta de enfrente
es otra".

### Consecuencias

- `Ubicacion.rumboA()` en el dominio: distancia y rumbo bastan para colocar cada
  punto sin cartografía.
- Al ajustar con los botones cardinales, **los vecinos se mueven en el lienzo**.
  Ese movimiento es la confirmación visual de que el ajuste va para el lado
  correcto, que es lo que faltaba en la prueba de campo del POCO M5s.
- El lienzo usa radio de 400 m; el aviso de duplicado sigue en 60 m. Son cosas
  distintas: uno orienta, el otro decide.
- Al agregar el lienzo, el aviso de duplicado quedó fuera de la pantalla. Se
  movió **arriba, pegado al nombre**: decidir si esta tienda es la misma que ya
  está registrada es lo más consecuente de la pantalla, y se decide mientras se
  escribe el nombre, no al final.

## 11. El ticket se genera hoy; la transmisión Bluetooth espera al equipo

- **Fecha:** 2026-09-30
- **Contexto:** la EC Line EC-MP200 (58 mm) quedó en otro Estado, sin acceso a
  corto plazo.

**Generar el ticket y transmitirlo son dos problemas distintos**, y se
desacoplaron. El primero es formato y aritmética: se prueba entero, en segundos,
sin hardware. El segundo es un socket que se cae, un equipo desemparejado, papel
que se acaba.

### Lo que ya está hecho y verificado

- Los bytes ESC/POS completos: `ESC @`, `ESC t`, `ESC a`, `ESC E`, `GS !`,
  `ESC d`, `GS V`, afirmados contra los valores del estándar.
- El diseño del ticket de 58 mm / 32 columnas.
- La interfaz `Impresora` con una **implementación simulada** que captura los
  bytes, los guarda en un archivo del teléfono, y **puede fallar a voluntad**
  (sin papel, desconectada, sin configurar). Esos caminos de falla no se pueden
  provocar con una impresora real en una prueba automatizada.
- Un **decodificador** que convierte los bytes de vuelta a texto con las mismas
  reglas que aplica la impresora, para poder *ver* el ticket: en la pantalla del
  teléfono, en las pruebas, y en `contracts/ticket_58mm_ejemplo.txt`, que se
  versiona y se revisa leyéndolo.

Falta una clase que abra el socket Bluetooth y empuje los bytes. Nada más cambia.

### Los acentos: PC437 con transliteración

Una impresora térmica no habla UTF-8; interpreta bytes con una tabla de códigos.
Se eligió **PC437** —la que soporta toda impresora ESC/POS— y lo que la tabla no
tiene se degrada: `Á → A`, y **los emoji se descartan**.

Eso último importa porque el sistema ya tiene emoji en nombres de clientes ("La
Esquina de Ñoño 🏪"): sin la degradación, serían cuatro símbolos sin sentido en
medio del nombre, en un papel que el cliente conserva.

PC850 está implementado y conserva las mayúsculas acentuadas; se cambia con un
parámetro **el día que se compruebe en el equipo real**. Sin poder probar,
**un ticket legible en cualquier impresora vale más que uno perfecto en una
sola**.

### El saldo NO se imprime

El saldo que trae el teléfono puede tener horas (§0.3). Imprimirlo en un papel
que el cliente conserva es crear una disputa: él sostiene el número impreso y la
oficina el suyo. El ticket dice el importe de **esta** venta y dónde consultar el
saldo.

---

## 12. El panel de oficina captura el catálogo; el teléfono nunca lo escribe

**Decisión.** Productos, presentaciones, precios y condiciones comerciales se
capturan **solo** en el panel web. El dispositivo los recibe por delta y los trata
como espejo de solo lectura.

Ya estaba en la tabla de propiedad del dato, pero hasta esta fase no había forma de
ejercerlo: el catálogo llegaba al teléfono por una migración de semilla o por el
Modo Demo. Ninguna de las dos sirve para operar.

### El delta lo publica la base, no la pantalla

Las escrituras del panel **no publican nada a mano**. Los disparadores de la
migración 0010 escriben en `change_log` por cada cambio en `productos`,
`producto_unidades`, `precios`, `listas_precios` y `clientes`.

Es deliberado, y es la razón de que los disparadores vivan en la base: si publicar
el delta fuera responsabilidad de quien escribe, cada pantalla nueva tendría que
acordarse, y **la que se olvide produce un catálogo que la oficina ve y el camión
no**. Así, incluso un `UPDATE` hecho a mano con psql llega a los teléfonos.

### Nada se borra: se desactiva

Un producto se marca `activo = false`; un cliente, `estatus = 'inactivo'`. Hay
ventas viejas que los referencian y puede haber una venta **de esta mañana que
todavía no ha sincronizado** y viene con esa clave. Borrar la fila mandaría esa
venta a cuarentena por una llave foránea: sería violar §0.1 desde la oficina.

### Cambiar un precio marca las ventas en vuelo, y eso está bien

Los equipos que ya salieron traen la lista de la mañana. Si el precio cambia a
mediodía, las ventas que hagan con el precio viejo entran marcadas
`precio_desactualizado`: el servidor respeta el importe del papel que firmó el
cliente y solo levanta la mano. La pantalla lo advierte al guardar, para que quien
captura decida si lo hace ahora o al cierre del día.

Guardar **el mismo** precio otra vez no mueve la `version` ni publica delta: un
delta idéntico haría que todos los teléfonos volvieran a bajar el catálogo sin que
nada hubiera cambiado.

### El precio de la pieza se deriva del de la caja, con cuatro decimales

El panel calcula la división y la ofrece con un botón. La caja de 24 a $296.00 da
$12.3333 la pieza; quien lo calcula en una hoja escribe $12.33, y desde ahí cada
caja vendida por pieza cobra $295.92 — ocho centavos menos, veinticuatro veces al
día. Con cuatro decimales, 24 × $12.3333 vuelve a dar $296.00, porque el redondeo
ocurre **una sola vez, sobre el importe** (§7).

Y **un precio en cero no se acepta**. Se vería idéntico a "regalado" en el ticket
del cliente. Si algún día hay producto gratis será una promoción, no un precio de
lista.

---

## 13. Un prospecto de calle lo confirma una persona, no un proceso

**Decisión.** El alta de cliente hecha en ruta nace `prospecto`, sin código, sin
lista de precios y con límite de crédito en cero. La oficina la confirma a mano, y
ese acto asigna el consecutivo y la lista.

### Por qué no se confirma solo

Aceptar una línea de crédito propuesta desde el teléfono sería dejar que el
vendedor se autorice su propia cartera. Y asignar el código en el INSERT gastaría
un número para un negocio que la oficina todavía no aceptó, que puede resultar ser
la misma tienda que registró el vendedor de la ruta de al lado.

### Confirmar y otorgar crédito son dos botones distintos

Confirmar que un negocio existe y decidir cuánto se le presta son dos juicios
distintos. Juntarlos hace que el segundo se tome sin pensarlo.

### El código sale de una secuencia, no de `max() + 1`

`max()+1` da el mismo número a dos personas que confirmen al mismo tiempo, y una
de las dos ve un error de UNIQUE que no significa nada para ella. La secuencia
`seq_codigo_cliente` (migración 0014) deja huecos cuando una transacción se
deshace, y eso está bien: el código **identifica** a un cliente, no cuenta
clientes.

### La georreferencia no se edita desde la oficina

La capturó el vendedor parado en la banqueta del negocio, con su precisión y su
origen (`gps` o `manual`) guardados. Es el mejor dato que va a existir de ese
domicilio. Corregirla desde una computadora a quince kilómetros sería sustituir un
dato medido por uno supuesto, y encima rompería la distancia con la que se marcan
las ventas fuera de geocerca.

### El saldo tampoco

Sale de `cuentas_por_cobrar`. Un campo editable de saldo sería una segunda verdad
que tarde o temprano contradice a la primera, y entonces nadie sabe cuál de las dos
cobrar. Se corrige con un cargo o un pago, que dejan rastro.

**Bajar un límite de crédito no perdona la deuda.** El saldo sigue igual; lo que se
va a cero es el disponible, y el teléfono corta la venta a crédito solo. La
pantalla lo dice con números cuando el límite nuevo queda por debajo del saldo,
porque quien lo escribe casi siempre cree lo contrario.

### Fusionar duplicados no se hace con un botón

Dos vendedores pueden levantar la misma tiendita el mismo día. El panel los
muestra y permite decir "son negocios distintos"; **fusionarlos no**. Fusionar
significa mover ventas, cuentas por cobrar y cobranza de un UUID a otro, y
cualquiera de esos renglones puede estar en un teléfono que todavía no sincroniza.
Necesita su propio diseño, no un botón al lado de una lista.
