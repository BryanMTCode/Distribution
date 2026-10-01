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

---

## 14. La carga del camión: el momento en que la mercancía cambia de dueño

**Decisión.** La carga se arma como **borrador** en el panel, y al **confirmarla**
se mueve el inventario y se publica al teléfono. Las dos cosas, en una sola
transacción, y una sola vez.

Hasta esta fase el `existencias_camion` del teléfono solo se podía sembrar en Modo
Demo. La venta estaba construida, el carrito descontaba inventario y el ticket
salía — sobre mercancía inventada.

### El borrador no sale de la oficina

Mientras se arma, la carga tiene renglones que alguien puede corregir o quitar. Si
llegara al teléfono, el vendedor vería —y podría vender— mercancía que la bodega
todavía no le entregó. El disparador de `change_log` **descarta el estado
`borrador`** (migración 0015); el delta sale con el `UPDATE` que confirma, y para
entonces el detalle ya está completo.

### El detalle viaja DENTRO del delta

Es el único delta que lo hace, y es deliberado: el teléfono necesita la carga
**completa o nada**. Con un delta por renglón, una tanda cortada a la mitad
dejaría el camión con cinco de los doce productos que trae, y el vendedor
descubriría el faltante frente al cliente.

La cantidad viaja como **string de tres decimales** (`"240.000"`), que es la regla
del contrato (`contracts/README.md` §1.4). El precio, que sale de un `to_jsonb`
crudo, todavía viaja como número; el detalle de la carga se construye a mano y sí
cumple, así que `Cantidad.deTexto` lo consume sin que ningún `double` toque el
número en el camino.

### Reaplicar el delta no puede revivir lo vendido

Un `pull` se repite tras un corte de red. Si la segunda aplicación volviera a
escribir `cant_actual = cant_cargada`, el camión recuperaría en la base la
mercancía que ya salió físicamente, el vendedor la volvería a vender, y el
descuadre aparecería en la liquidación como un faltante inexplicable.

El `ON CONFLICT` del aplicador lleva un `WHERE`: un renglón que **ya pertenece a
esta carga** no se toca. Solo se sobrescribe el que viene de otra carga —el
sobrante de ayer— o el que no tenía ninguna.

### Cerrar una carga vieja no puede borrar la de hoy

La oficina liquida lo de ayer a media mañana, con el camión ya en la calle, así que
ese delta llega **después** del de hoy. Tratarlo como "éste es el inventario
vigente" borraría el de hoy y repondría el de ayer.

La regla: un estado terminal (`liquidada`, `cancelada`) borra **solo sus propios
renglones**. Si la carga de hoy ya los reemplazó, no borra nada.

### La conversión caja → pieza ocurre al capturar

Quien carga el camión cuenta **cajas**, porque es lo que levanta con las manos. El
inventario se lleva en **unidad base**. La multiplicación ocurre una vez, en el
panel, con la misma función que usa el teléfono al armar una partida
(`cantidad_base`). Es la regla del §2.2 de arquitectura y es lo que evita el
descuadre clásico: media empresa contando cajas y la otra media piezas.

Y se capturan **bultos completos**: un `2.5` se rechaza. Nadie sube media caja a
un camión, y si se aceptara, la conversión lo volvería 60 piezas con cara de dato
bueno.

### Se permite dejar la bodega en negativo

Si la bodega marca 8 cajas y el almacenista está subiendo 10, **el sistema está
mal, no el mundo**. Rechazar la carga significaría que el camión sale con
mercancía que el sistema no registró, que es infinitamente peor que un número
negativo en una caché.

Es el §0.1 aplicado dentro de la oficina, y es la razón por la que `existencias` no
tiene `CHECK (cantidad >= 0)` y sí tiene un índice para encontrar los negativos. La
pantalla lo advierte con el número exacto y deja pasar.

### Confirmar dos veces no duplica la carga

Un doble clic en una pantalla lenta duplicaría la carga del día, y el faltante
aparecería en la liquidación como si el vendedor se hubiera llevado el doble. La
confirmación es idempotente por estado: solo una carga en `borrador` se puede
confirmar.

### Una carga confirmada no se edita ni se cancela

Lo que ya salió de la bodega se corrige con un **traspaso** o un **ajuste**, que
son documentos con su propia huella. Lo que regresa al final del día es un
**retorno**, que es el documento de la liquidación — no una cancelación que finge
que el día no pasó.

Y el movimiento del libro mayor **no se puede editar**: `movimientos_inventario`
es append-only por disparador, no por convención.

### El camión no se elige

Es el almacén del vendedor. Un desplegable de almacén destino permitiría cargarle
el camión de otro, y el dueño exclusivo del almacén es la garantía sobre la que
descansa todo el modelo offline (§0.2): sin ella vuelven los conflictos de
concurrencia que este diseño existe para no tener.

---

## 15. No hay usuario por omisión, y el primero se crea desde el servidor

**Decisión.** Ninguna migración siembra usuarios. El primero se crea con
`make usuario`, un comando que pregunta la contraseña por `getpass`. Los demás se
dan de alta desde el panel.

### Las tres formas de resolver el huevo y la gallina, y por qué esta

Los usuarios se dan de alta en el panel, y al panel se entra con un usuario. Una
base recién migrada no tiene ninguno.

1. **Sembrar `admin / admin123` en una migración.** Es la que todo el mundo
   escribe. El problema no es el desarrollo: es que esa fila viaja a producción,
   nadie se acuerda de cambiarla, y queda un usuario con todos los permisos cuya
   contraseña está publicada en el repositorio. Con el panel detrás del túnel de
   Cloudflare, eso es acceso a la operación completa desde internet.
2. **Una pantalla de "primer arranque" sin autenticar.** Funciona, y deja una ruta
   que crea administradores sin credenciales. Basta que alguien recree la base, o
   que la condición "¿ya hay usuarios?" se evalúe mal una vez, para que esa puerta
   quede abierta.
3. **Un comando que corre quien tiene acceso al servidor.** Quien puede ejecutarlo
   ya está dentro de la máquina, así que no concede nada nuevo, y no deja ninguna
   fila ni ninguna ruta de más cuando termina.

La contraseña se teclea y no se pasa como argumento: un `--password` queda en el
historial del shell y en la lista de procesos.

Hay una prueba que afirma que **ninguna migración siembra usuarios**. Corre sin el
fixture de semilla, contra la base tal como la dejan las migraciones, para que
nadie reintroduzca la opción 1 por comodidad.

### Mínimo doce caracteres, porque el hash viaja al teléfono

`usuarios.password_hash` se replica al dispositivo para permitir el login sin red.
Eso significa que una contraseña se puede atacar **con el equipo en la mano**, sin
límite de intentos y sin conexión. Doce caracteres no es una cifra mágica: es
donde una frase corta ("camion rojo 14") ya resiste fuerza bruta offline con los
parámetros de Argon2id de este sistema.

### Un usuario no se borra, y desactivarlo corta su sesión de verdad

Tiene ventas, cobros y movimientos de inventario firmados con su id. Se desactiva,
y en el mismo paso se revocan sus sesiones del panel: eso es posible **porque la
sesión tiene fila en la base y no es un JWT firmado**, que seguiría siendo válido
hasta expirar haga lo que haga la oficina.

Dos cosas que el panel no permite, porque dejarían el sistema sin salida: que
alguien se desactive a sí mismo, y desactivar al último administrador activo. En
los dos casos el arreglo saldría por línea de comandos en el servidor.

### Cambiar una contraseña no bloquea el teléfono de inmediato

El hash nuevo llega al dispositivo cuando sincroniza. Hasta entonces el vendedor
entra con la anterior, y eso es correcto: si el cambio cortara el acceso offline
al instante, cambiar una contraseña dejaría a alguien sin poder trabajar a media
ruta.

---

## 16. El titular de una ruta y el alcance de datos son dos cosas

**Decisión.** Asignar un vendedor a una ruta escribe **las dos** filas:
`rutas.vendedor_id` y `usuarios_rutas`.

`rutas.vendedor_id` dice quién es el titular — es información de negocio.
`usuarios_rutas` es lo que el *scope guard* consulta, y es lo que el filtro del
pull usa: `ruta_id = ANY(:rutas)`.

Con solo la primera, el vendedor aparece como dueño de la ruta en todas las
pantallas y **su teléfono no recibe un solo cliente**. No falla, no avisa: la
sincronización reporta éxito y llega vacía. Es el error que se comete una vez y
cuesta una tarde de depuración con el teléfono en la mano, así que el panel
escribe las dos y hay una prueba que lo afirma.

Al cambiar de titular, al anterior **se le quita** el alcance: si se le dejara, su
teléfono seguiría recibiendo —y pudiendo venderle a— los clientes de una ruta que
ya no trabaja. Los clientes no se mueven: siguen siendo de la ruta.

### El camión se crea junto con el vendedor

`camion_requiere_responsable` (migración 0004) obliga a que el usuario exista
antes del almacén, y además el almacén tiene que quedar como `usuarios.almacen_id`
para que la carga encuentre a dónde ir. Son tres escrituras en un orden que quien
da de alta a un empleado no tiene por qué conocer: el panel las hace en un paso.

---

## 17. La liquidación: la única pantalla que compara

**Decisión.** El cierre del día calcula `esperado = cargado − vendido − merma +
devuelto` de sus propios documentos, y lo compara contra un **conteo físico** del
camión. La diferencia es lo único que importa del cierre.

Todas las demás pantallas **registran**. Esta compara, y es por eso la única que
puede atrapar un descuadre.

### El devuelto suma, y es el signo que se escribe mal

Una devolución de cliente **entra** al camión, así que tiene que volver a la
bodega. Restarla haría aparecer un faltante del tamaño exacto de las devoluciones
del día, y el vendedor pagaría por mercancía que devolvió bien.

La ecuación vive en `app/domain/liquidacion.py`, en la columna generada
`liquidacion_detalle.diferencia` de PostgreSQL, y —cuando llegue la Fase 6— en el
teléfono. Hay una prueba que compara el módulo de dominio contra la columna
generada con cuatro conteos distintos: si divergieran, el vendedor y la oficina
estarían discutiendo sobre dos números, cada uno convencido de tener el del
sistema.

### El conteo nace en cero, no en el esperado

Prellenarlo con el esperado haría que cerrar sin contar diera cuadre perfecto, y
entonces el cierre no significaría nada: sería un botón que dice que todo está
bien. Contar el camión es el único dato que esta pantalla no puede calcular, y es
justamente el que le da sentido a los demás.

Y un campo **vacío vale cero**, no "no lo cambies": al contar un camión, el
producto que no se anotó es el que no venía. Con la otra semántica, un producto que
se terminó quedaría con el conteo de un intento anterior y el faltante
desaparecería sin que nadie lo decidiera.

### Al cerrar, el camión queda EXACTAMENTE en cero

Tres cosas en una transacción:

1. **`retorno`** camión → bodega por lo contado. Es el movimiento físico.
2. **`ajuste`** por el faltante o el sobrante. Es el paso que se olvida: sin él el
   camión arrastra un saldo fantasma para siempre, el faltante de hoy queda como
   existencia, y el cierre de mañana empieza con un sobrante que nadie puso ahí.
3. La carga pasa a **`liquidada`**, y ese `UPDATE` publica el delta que **vacía
   `existencias_camion` en el teléfono** (§14). Sin esto el vendedor saldría mañana
   con el inventario de ayer en la pantalla.

El ajuste se calcula leyendo la existencia **después** del retorno, no deduciéndola
de `diferencia`: si las dos no coincidieran, el que tiene razón es el inventario, y
el objetivo es dejar el camión en cero.

Un faltante sale del sistema (`origen = camión`, `destino = NULL`) y es la pérdida
que se le carga al vendedor. Un sobrante entra al camión, para que el retorno ya
registrado cuadre — si el signo estuviera al revés, el camión quedaría al doble en
negativo.

### No se cierra con operaciones pendientes, y por una razón concreta

Un sobrante casi siempre es **una venta que el teléfono no ha sincronizado**. Una
venta que entra después del cierre convierte ese sobrante en un cuadre, y el cierre
ya dijo lo contrario por escrito, con el nombre del vendedor. Es §2.3 de
arquitectura, y la regla que más tienta a saltarse cuando el vendedor tiene prisa.

Lo que el servidor **puede** verificar, y bloquea:

- **sobres en cuarentena** de ese equipo: cada uno es una operación que no entró, y
  cualquiera puede ser la venta que explica la diferencia;
- que el equipo **haya sincronizado** desde el día de la carga.

Lo que **no** puede verificar: cuántas operaciones le quedan en la bandeja de
salida al teléfono. Eso solo lo sabe el teléfono. Hasta que lo reporte, la pantalla
pide una confirmación explícita y la guarda en `sync_completa`. Poner esa columna en
`true` sin un dato que lo respalde sería peor que no tenerla.

### El esperado de efectivo se recalcula al guardar el arqueo

Entre abrir y cerrar pueden entrar ventas de contado y cobros que el teléfono
sincronizó tarde. Usar el número calculado al abrir haría aparecer un faltante de
efectivo del tamaño exacto de lo que llegó en medio.

Solo suman las ventas de **contado** y los cobros en **efectivo**: una venta a
crédito no cobró nada, y una transferencia no viene en la bolsa.

---

## 18. El inventario se puede ver sin abrir una carga

**Decisión.** Una pantalla de existencias por almacén, con el libro mayor de cada
producto a un clic.

Las existencias estaban visibles solo de refilón, al armar un borrador de carga.
Para saber qué había en la bodega había que empezar a cargar un camión — absurdo, y
además peligroso: se abre un borrador para consultar y alguien lo confirma.

### La pantalla compara las dos tablas, porque la caché no se puede auditar sola

`existencias` es una caché transaccional; `movimientos_inventario` es el libro mayor
append-only. Si la suma del libro no cuadra con la caché, hay un bug en alguna
transacción que escribió una y no la otra — y eso **no se puede detectar mirando la
caché**, por definición: el número está ahí y se ve razonable. El job de
reconciliación nocturno existe para esto; la pantalla permite verlo sin esperar a
la noche, y dice cuál de las dos tiene razón.

### El saldo corriente va en orden cronológico

Así se ve **en qué movimiento** el inventario se fue a negativo, que es una pregunta
distinta de si hoy está negativo — y la que de verdad se hace al investigar. La
tabla se muestra al revés, con lo más reciente arriba, pero el saldo se calculó
hacia adelante.

---

## 19. La cobranza en la calle: el papel importa más que en la venta

**Decisión.** El vendedor registra un abono desde la lista de ruta, en su propio
camino —no como paso del carrito—, y le entrega al cliente un recibo impreso.

Una venta entrega mercancía que se cuenta al final del día. Un cobro recibe
**efectivo**, y el efectivo no se cuenta: se cuadra. Si un cobro no queda
registrado, el dinero existe en la bolsa del vendedor y no en el sistema, y en la
liquidación aparece como un descuadre que nadie puede explicar — o peor, como un
faltante del cliente que sí pagó.

### El cobro es su propio camino

El cliente puede pagar **sin comprar nada**, y es el caso más común del día de
cobranza. Colgarlo del carrito obligaría a abrir una venta vacía.

El botón solo aparece en los clientes que **deben algo**: ofrecerlo siempre
llenaría la lista de botones inertes, y el día de cobranza lo que se busca es lo
contrario — encontrar rápido a quién cobrarle.

### Cobrar más de lo que debe NO se rechaza

El cliente puede liquidar y dejar anticipo, o ya haber pagado parte por otra vía.
**El dinero está sobre el mostrador.** Si la pantalla lo impidiera, el vendedor se
guardaría efectivo sin documento — que es exactamente el descuadre que esta
pantalla existe para evitar.

Es el §0.1 aplicado al dinero. El servidor lo registra como `saldo_a_favor` y lo
marca para que la oficina decida si es anticipo o devolución.

### El teléfono NO decide a qué factura se aplica

El dispositivo registra **un abono por un importe**, y nada más. El FIFO lo
resuelve el servidor, por **vencimiento más antiguo** — lo que reduce el riesgo
real de la cartera.

La razón es que el teléfono no conoce la cartera completa: trae un saldo en caché
que puede tener horas y que no incluye los cobros que otros equipos hicieron hoy.
Si decidiera la aplicación, dos dispositivos cobrando al mismo cliente aplicarían
los dos abonos a la misma factura, y el servidor tendría que deshacer una decisión
que ya está impresa en un papel.

Lo que sí viaja es `saldo_cache_disp`: lo que el teléfono **creía**. Es forense, no
autoridad — permite explicar después por qué el vendedor cobró lo que cobró. Una
diferencia grande contra el saldo real se marca, porque significa que el equipo
llevaba horas sin sincronizar.

### El saldo en caché del cliente no se toca al cobrar

Es zona espejo: la escribe el delta de cartera. Si el cobro la bajara, el siguiente
`pull` la volvería a subir —porque el servidor todavía no tiene el abono— y el
vendedor vería la deuda reaparecer a media ruta.

El número que ve se **compone al leer**, restando los cobros encolados. Así baja de
inmediato y ningún delta lo contradice. Y en pantalla va **con su antigüedad**: un
número sin fecha se trata como la verdad, y éste es una caché (§0.3).

### El recibo no imprime el saldo

Por la misma razón que la remisión (§11). El teléfono solo trae una caché que puede
tener horas y que no incluye los cobros de otros equipos. Imprimir "le quedan
$1,500" en un papel que el cliente conserva es crear una disputa donde él sostiene
el número impreso y la oficina el suyo.

Lo que sí es un hecho de este cobro —cuánto entregó, cuándo, a quién, con qué
folio— es exactamente lo que va en el papel.

### Lo que no es efectivo exige referencia

Sin ella una transferencia es imposible de conciliar con el banco: la oficina
tendría un abono registrado y ninguna forma de encontrarlo en el estado de cuenta.
El efectivo no la necesita porque el papel **es** la prueba.

Y la forma de pago es un catálogo cerrado porque el arqueo de la liquidación suma
**solo el efectivo** —una transferencia no viene en la bolsa—. Con texto libre,
`"efectivo "` con un espacio quedaría fuera de la suma y el cuadre fallaría por un
dato que se ve bien.

### Los folios de cobro son su propia serie

Comparten el formato del prefijo con las ventas pero no el contador. Si
compartieran serie, un recibo y una remisión podrían traer el mismo número
impreso, y una aclaración por teléfono sería imposible de resolver.

---

## 20. Dos defectos que solo aparecieron al construir la cobranza

Los dos estaban en la Fase 3, los dos eran silenciosos, y los dos se vuelven daño
real en cuanto existe un cobro.

### Una venta a crédito no creaba su cuenta por cobrar

**Nada** insertaba en `cuentas_por_cobrar`. Una venta a crédito quedaba registrada
en `ventas` y la deuda no existía en ningún lado. Consecuencias, todas calladas:

- **El límite de crédito nunca se alcanzaba.** La validación sumaba una cartera
  vacía, así que un cliente con límite de $5,000 podía llevarse $50,000.
- **Un cobro no tenía a qué aplicarse** y caía entero como saldo a favor de un
  cliente que sí debía.
- El panel y el delta de cartera mostraban cero.

Ahora la cuenta por cobrar se crea en la **misma transacción** que la venta, con su
vencimiento calculado desde los días de crédito del cliente. Una venta a crédito
sin su deuda es mercancía entregada que el sistema cree regalada.

### El límite de crédito no contaba las facturas parciales

La validación filtraba `estado = 'abierta'`. Una factura pasa a `'parcial'` en
cuanto el cliente abona algo, así que **el resto de esa factura dejaba de contar
contra su límite**: abonaba un peso y recuperaba toda su línea de crédito.

Era invisible hasta esta fase, porque sin cobranza nada producía el estado
`'parcial'`. Ahora usa `estado <> 'liquidada'`, el mismo criterio que
`v_cartera_cliente` y el manejador de cobro.

**Los dos los encontró el contrato de sobres**, no una prueba de unidad: el caso 5
manda una venta a crédito y su cobro en el mismo sobre, y el cobro llegó con
`importe_aplicado = 0`. Es la clase de defecto que una prueba de unidad no ve,
porque cada pieza por separado hace exactamente lo que dice.

## 21. Mermas y no-drops: los dos documentos que explican una diferencia

Ninguno de los dos mueve dinero, y por eso es fácil tratarlos como papeleo. No lo
son: son los únicos documentos que explican **por qué el cierre no cuadra**.

Sin la merma, la caja que se revienta en el camión llega a la liquidación como
faltante, y un faltante sin explicación se le carga al vendedor. Es el caso que le
duele a un vendedor honesto y el único que no puede corregir después: para el
cierre, el cartón roto ya se tiró.

Sin el no-drop, un día de 20 visitas con 12 ventas se ve igual que uno de 12
visitas con 12 ventas. Desde la oficina son indistinguibles, y el primero tiene
ocho clientes que necesitan algo.

### Dos signos, un documento

    merma               la mercancía SALE del camión
    devolucion_cliente  la mercancía ENTRA al camión

Es la misma tabla porque son el mismo hecho visto al revés —mercancía que cambia de
manos sin dinero de por medio— y porque la liquidación los necesita juntos:

    esperado = cargado − vendido − merma + devuelto

Equivocar el signo produce un descuadre del **doble** del tamaño de la operación.

Lo mermado va a un almacén de tipo `merma` si la empresa configuró uno, para que la
pérdida quede contabilizada donde se puede contar. Si no existe, la mercancía sale
del sistema y queda solo el movimiento de salida: lo que no se puede perder es el
registro de que salió.

### La merma es la regla OPUESTA a la de la venta

La venta exige existencia (`cant_actual >= cantidad`) porque la mercancía todavía no
cambió de manos: si el catálogo está desfasado, el vendedor puede revisar y no
vender.

La merma no la exige porque **el cartón ya está roto**. Si el camión marca 2 y se
rompieron 3, el que está mal es el conteo, no el mundo. Bloquearla haría que la
pérdida no se registrara, y entonces aparece en la liquidación como faltante del
vendedor — exactamente lo que este documento existe para evitar. El servidor la
marca con `merma_sin_existencia` y la registra; la existencia del camión **queda
negativa a propósito**, porque mentir sobre el inventario es peor que admitir que el
conteo está mal.

La pantalla muestra lo que el camión dice que trae —sirve para notar un dedazo— y
deja capturar más.

### La única excepción a «marcar, no rechazar» de todo el sistema

El §0.1 dice que el servidor acepta y marca, porque el hecho físico ya ocurrió y
negarlo no lo deshace. Un no-drop sin ubicación es el único caso distinto: **no hay
hecho que preservar.** Lo único que afirma el documento es "estuve ahí y no compró",
y sin coordenadas es indistinguible de "no fui". Guardarlo marcado metería una
visita no verificable a cada reporte de efectividad.

Y no se pierde nada: el rechazo manda el sobre completo a cuarentena, con su payload
íntegro, donde la oficina lo ve y decide. El dispositivo ni lo produce —el registro
local exige `Ubicacion` y `no_drops.lat` es NOT NULL en las dos bases—, así que
llegar ahí sin ella significa un cliente viejo o alterado, que es justo lo que la
cuarentena existe para atrapar.

La pantalla no habilita el botón sin lectura de GPS, y explica cada falla donde se
resuelve: el permiso en los ajustes, el GPS apagado prendiéndolo, el satélite que no
respondió saliendo del techado. Un "no se pudo obtener la ubicación" no lleva a
ninguna de las tres.

### Los motivos son catálogo cerrado, y el catálogo viaja

Texto libre son datos que nunca se van a poder analizar: "cerrado", "estaba
cerrado", "cerrado!!" y "crrado" son cuatro categorías para cualquier reporte.

`motivos_merma` trae `afecta_vendedor`, que decide si la pérdida se le descuenta en
la liquidación. **Lo decide la oficina en el catálogo, nunca el vendedor al
capturar** —dejarlo en sus manos sería pedirle que elija si se le cobra—, pero el
teléfono se lo **muestra**: enterarse en la liquidación de que ese motivo se le
descuenta es lo que rompe la confianza.

`motivos_no_drop` trae `categoria` (cliente, operación, producto, vendedor), que es
lo que después permite preguntar cuántas visitas perdidas son culpa nuestra, y
`orden`, porque en la calle, con el cliente esperando, un catálogo alfabético obliga
a leer diez opciones para encontrar "cerrado".

## 22. Tres defectos que solo aparecieron al construir la Fase 6

### Los catálogos de motivos nunca llegaban al teléfono

`motivos_merma` y `motivos_no_drop` existían en las dos bases y **ningún disparador
los publicaba**. Las pantallas de merma y no-drop habrían abierto con la lista
vacía, y como el motivo es obligatorio, no se habría podido registrar nada: la
pérdida como faltante del vendedor y la visita perdida fuera de todo reporte.

La migración 0017 agrega los disparadores con una `entidad_id` derivada del código
(`md5(codigo)::uuid`, porque la llave primaria de esas tablas es texto y `change_log`
pide un UUID) y hace el relleno inicial, idempotente.

### `activo` no viajaba, así que desactivar un motivo no servía de nada

El disparador publicaba el motivo completo, pero el aplicador de Dart ignoraba
`activo` y el esquema local ni siquiera tenía la columna. Un motivo que la oficina
retiraba seguía apareciendo en la pantalla del vendedor: **para él la desactivación
nunca había pasado.** Y el no-drop lo rechazaba, mandando a cuarentena la visita de
un vendedor que no hizo nada mal.

Ahora `activo` viaja, el catálogo local lo filtra, y el servidor cambió de criterio:
un motivo **inactivo** se acepta y se marca con `motivo_fuera_de_catalogo`, porque el
teléfono le ofreció ese motivo y castigarlo por una edición de escritorio sería
injusto; uno que **no existe** sigue rechazándose, porque no hay nada a lo que
mapearlo.

### `Cantidad` no sabía leer un número negativo

Y la existencia del camión **puede quedar negativa**, justo después de la merma que
el conteo tenía mal. `Cantidad.deTexto` exigía `^(\d+)\.(\d{3})$`, así que
`Cantidad.deBase(-12)` lanzaba `FormatException` y la pantalla de merma reventaba al
abrirse **inmediatamente después del caso para el que existe**.

Ahora admite signo, igual que `Dinero` por el saldo a favor. Que una cantidad
concreta no pueda ser negativa —el renglón de una merma, la línea de un carrito— lo
decide quien la valida, no el tipo. `Precio` y `Factor` siguen rechazándolo: un
precio negativo no significa nada, y aceptarlo convertiría un dedazo del catálogo en
una venta que paga la empresa.

El `_aTexto` también se arregló: con `valor ~/ escala` solo, un −0.500 salía como
"0.500" —los enteros son cero y el truncado se come el signo— y la cantidad cambiaba
de sentido al convertirse a texto.

## 23. La cobranza en el panel: mirar es la otra mitad de marcar

`cobro.crear` es el manejador más permisivo del sistema, y a propósito: en un cobro
el dinero ya está sobre el mostrador. Cobrar más de lo que el cliente debía se
registra como saldo a favor; cobrarle a quien no debía nada se registra igual; un
saldo de caché muy desfasado se registra igual. Las tres cosas se **marcan**.

Marcar sin que nadie mire convierte la bandera en ruido, y entonces el permiso del
manejador deja de ser una decisión y se vuelve un agujero. Hasta esta pantalla, esos
cobros marcados solo se alcanzaban con SQL a mano, que es lo mismo que no
alcanzarlos.

### Solo el efectivo entra al arqueo

El corte del día separa `efectivo` de todo lo demás. Una transferencia entra al
sistema pero no a la bolsa del vendedor, y sumarlas haría que la caja nunca cuadre y
que el descuadre se le atribuyera a la persona equivocada.

### La antigüedad se cuenta desde el vencimiento

La pregunta que importa no es cuánto nos deben sino desde cuándo: $40,000 al
corriente y $40,000 a noventa días son dos empresas distintas, y el total solo no las
distingue. Los tramos salen de `fecha_vencimiento`, no de la emisión: un cliente a 30
días no está vencido el día 15, y contarlo así haría que la pantalla gritara todos
los días.

### Lo que el panel NO puede hacer

No cancela cobros ni reasigna aplicaciones. El dinero entró y el reparto lo decidió
el FIFO sobre la cartera real; un botón para moverlo permitiría maquillar una cartera
sin que quede rastro. Lo que sí puede es **dar por revisado**, que es un acto de
auditoría y no una corrección: deja quién lo vio y cuándo, y conserva el motivo
original al lado en vez de borrarlo, porque por qué se marcó es parte del historial.

## 24. El teléfono reporta su cola, y `sync_completa` deja de ser una casilla

`liquidaciones.sync_completa` existía desde la migración 0004 y hasta ahora se
escribía en `true` porque una persona marcaba una casilla antes de cerrar. Era lo
único honesto que se podía hacer —el servidor no tiene forma de ver la bandeja de
salida de un teléfono— pero el dato que dejaba era indistinguible de un hecho: al
auditar un cierre con sobrante, lo primero que se pregunta es si el equipo estaba al
día, y ese `true` contestaba con una afirmación disfrazada de hecho.

Ahora el push lo trae. El dispositivo manda cuántos sobres quedan en su cola
**después** del lote que está entregando, y el servidor lo guarda en
`dispositivos.cola_pendiente` con su hora.

### `NULL` y `0` no son lo mismo

    NULL  este equipo nunca lo ha reportado (app vieja, o nunca sincronizó)
    0     el equipo dijo que no le queda nada

De esa diferencia depende si el cierre puede descansar en un dato o tiene que
seguir pidiendo la confirmación de una persona. Un `DEFAULT 0` habría borrado la
distinción y habría hecho que cada equipo con app vieja pareciera estar al día
desde el primer día.

Por la misma razón el push lo trae **opcional**: la primera consecuencia de este
cambio no puede ser un vendedor que no puede subir sus ventas por no haber
actualizado. Y un lote sin el campo **no borra** lo último reportado: el `COALESCE`
va sobre el parámetro, no sobre la columna.

### El número se mide antes de mandar, y se resta la tanda

El dispositivo calcula `pendientes − tamaño de la tanda` antes del envío, en vez de
medirlo después: el servidor necesita el número que corresponde al estado en que lo
deja **ese** push, y medirlo después obligaría a un segundo viaje solo para decirlo.
Si la tanda acaba en cuarentena el número sigue valiendo, porque esos sobres también
salen de `pendiente`.

Es un dato con la honestidad del §0.3: dice lo que el teléfono sabía en ese momento.
Una venta levantada un segundo después ya no está contada, y **por eso se guarda la
hora junto al número**.

### Qué cambió en el cierre

- Si algún equipo activo del vendedor reportó pendientes > 0, **bloquea**. Ya no es
  una sospecha: cada sobre que le queda puede ser la venta que explica el sobrante
  que el cierre está por declarar por escrito.
- Si **todos** reportaron cero **después** del día de la carga, la casilla
  desaparece y `sync_completa` queda en `true`. Un cero de anteayer no sirve: el
  equipo pudo levantar veinte ventas desde entonces.
- Si no hay respaldo, se sigue pidiendo la confirmación —cerrar el día no puede
  quedar atorado por una actualización pendiente— pero `sync_completa` queda en
  `false` y la liquidación cerrada lo dice con esas palabras.

El campo va con `strict=True` en Pydantic. Sin eso aceptaría `"3"` y también `true`
—`bool` es subclase de `int` en Python, así que una cola de «sí» valdría una
operación pendiente— y el contrato se volvería una sugerencia en el único campo del
que depende el cierre.

## 25. Efectividad de visita: capturar sin leer es el mismo problema que marcar sin mirar

El no-drop existe porque sin él un día de 20 visitas con 12 ventas se ve igual que
uno de 12 visitas con 12 ventas. Pero capturar el dato y no leerlo deja el problema
intacto, y añade uno: el vendedor dedica tiempo a registrar visitas perdidas, nadie
las mira, y en cuanto eso se nota deja de registrarlas. Es exactamente lo que pasa
con una venta marcada para revisión que nadie revisa.

### La pregunta que contesta, y que ningún otro reporte puede

No es cuánto vendimos —eso lo dice el reporte de ventas— sino **cuántas visitas
perdidas podemos arreglar nosotros**. La categoría del motivo es lo que lo permite:

    cliente    cerrado, no estaba quien decide, no tiene dinero hoy
    operación  se le acabó el crédito
    producto   no traigo lo que pidió, le pareció caro
    vendedor   no alcancé a visitarlo

Las tres últimas son nuestras. Un día con ocho no-drops por «no traigo lo que
pidió» no es un problema de ventas: es un problema de carga, y se arregla en la
bodega a la mañana siguiente. Sin la categoría, las ocho se ven como «no compró» y
nadie cambia nada.

### Una visita es un cliente visitado, no un documento

No hay tabla `visitas` y no hace falta: la venta y el no-drop cubren los dos
desenlaces posibles de pararse frente a una tienda. Pero se cuenta por **(vendedor,
cliente, día)**, no por documento: dos remisiones al mismo cliente el mismo día son
una visita, y contarlas como dos premiaría al vendedor que parte un pedido en dos.

Las visitas perdidas se cuentan por cliente-día y los motivos por documento, así
que las dos cifras pueden diferir y las dos están bien. Pasa cuando se pasó dos veces
por el mismo negocio el mismo día: si a la segunda compró, la visita cuenta como
vendida —el desenlace fue la venta— pero su no-drop sigue contando como causa,
porque la primera vez no compró; si no compró ninguna de las dos, es una visita
perdida con dos motivos. La pantalla lo explica en vez de esconderlo: un total que no
cuadra con su desglose sin explicación destruye la confianza en todo el reporte.

El `FULL OUTER JOIN` implícito —un `UNION ALL` agrupado— no es un detalle: con un
`JOIN` normal entre ventas y no-drops, el vendedor que vendió a todos desaparecería
del reporte **por haber tenido un día perfecto**.

### El rango por omisión son siete días

Una efectividad del 60% sobre 20 visitas y una del 60% sobre 140 son dos cosas
distintas, y la primera es ruido: con 20 visitas, dos clientes cerrados mueven el
número diez puntos. Abrir en «hoy» invitaría a decidir sobre esa clase de número
justo cuando el día todavía no termina de sincronizar. Por eso el porcentaje se
muestra siempre **con su base**.

### Las mermas comparten la pantalla, y la razón

Tenían el mismo problema: el vendedor captura el motivo y nadie lo sumaba. Y el dato
que de verdad importa no es cuánto se perdió, sino **cuánto se le está cobrando a
alguien**: `afecta_vendedor` decide si la pérdida sale de su bolsa en la
liquidación, así que la tabla separa las dos cifras.

Las devoluciones de cliente no están ahí —son mercancía vendible que regresa, no
pérdida— y la pantalla dice **dónde sí** se ven. Decir solo que no están haría que
la ausencia pareciera un hueco del reporte y alguien pediría que se sumen, que es
exactamente lo que no debe pasar.

## 26. El laboratorio analítico: una definición, escrita una vez

El riesgo del laboratorio no es que una consulta sea lenta. Es que **la misma
pregunta se contesta distinto cada vez que alguien la hace.** "Drop size" puede
significar tres cosas, y dos reportes del mismo mes con dos números destruyen la
confianza en los dos — incluido el que estaba bien.

Por eso las definiciones viven en `server/app/domain/analitica.py`, con el resto
del dominio, y el Streamlit las **importa** en vez de llevar su propio SQL. Si la
definición cambia, cambia en un lugar.

### Vistas materializadas, no un ETL incremental

Es la decisión que más importa, y la razón es propia de un DSD.

Un ETL incremental carga "lo creado desde la última corrida". En un sistema normal
funciona. Aquí **los datos llegan tarde por diseño**: una venta del lunes puede
sincronizar el jueves porque el teléfono no tuvo señal. Un incremental por
`creado_en` la cargaría con fecha de jueves; uno por `fecha_operativa` no la
cargaría nunca. El lunes quedaría subreportado para siempre, y nada lo avisaría.

Un `REFRESH MATERIALIZED VIEW` completo recalcula desde la verdad transaccional:
es **imposible** que se desincronice. A miles de tickets diarios cuesta segundos.
Cuando el volumen lo pida, el camino es particionar por fecha, no volverse
incremental.

Dos trampas de `CONCURRENTLY`, las dos verificadas contra PostgreSQL 16:

1. **Exige un índice UNIQUE sin WHERE.** Por eso cada vista lleva el suyo, y no es
   decorativo.
2. **No funciona sobre una vista nunca poblada.** La migración las crea CON DATOS,
   y el job además lo comprueba con `pg_class.relispopulated` y cae a un refresh
   simple si hiciera falta. No existe forma de que falle.

Y una tercera que resultó un no-problema, pero que había que comprobar porque es
contraintuitiva: `ALTER DEFAULT PRIVILEGES ... GRANT SELECT ON TABLES` **sí**
alcanza a las vistas materializadas, aunque su `relkind` sea `'m'` y no `'r'`. El
rol de solo lectura ya las cubría sin tocar nada.

### Una visita es un cliente-día, no un documento

La misma definición que la pantalla de efectividad, ahora materializada en
`fact_visitas` con el grano `(vendedor, cliente, día)` **como índice único**. Si
ese índice fallara al refrescar, la definición estaría mal y todo lo que cuelga de
ella también — así que el refresh es la prueba.

Dos remisiones al mismo cliente el mismo día son una visita. Contar documentos
infla la efectividad, desinfla el drop size, y premia al vendedor que parte un
pedido en dos.

### Las tres cosas que se miden mal en un DSD

1. **Drop size sobre documentos.** $1,500 en dos visitas con venta son $750, no
   $500. Y la efectividad sale 50 %, no 60 %.
2. **Promediar sobre los días que hubo venta.** Un vendedor que vendió $10,000 en
   tres de cinco días promedió $2,000, no $3,333. Por eso `dim_tiempo` es una
   tabla y no un `generate_series`: los días sin venta tienen que existir en el
   reporte, o el promedio se reparte entre menos días de los que hubo.
3. **Un umbral fijo de abandono.** "Sin comprar en 30 días" marca como perdido al
   cliente que siempre compró cada 45, y deja pasar al que compraba cada semana y
   lleva 20 — que es el que de verdad se está yendo. El riesgo se mide contra la
   **cadencia propia** de cada cliente: en riesgo a 2× su mediana, perdido a 3×.
   Con menos de tres compras no hay cadencia, y el cliente aparece como «nuevo» en
   vez de mezclarse con los que se van: una lista larga de falsos positivos se
   deja de leer.

### La rotación histórica sale del libro mayor, no de fotos del inventario

No hay snapshots diarios de existencias. `existencias` es una caché del valor
**actual**, así que no sirve para el pasado.

Pero `movimientos_inventario` es append-only por disparador, así que la existencia
de cualquier fecha se reconstruye sumando los movimientos hasta ahí. El acumulado
va sobre **todo** el libro (`m.fecha <= t.fecha`) y no sobre el periodo
consultado: si arrancara en el primer día del rango, un reporte de la segunda
quincena daría existencias negativas, porque vería las ventas sin la carga que las
precedió.

La decisión de que el libro fuera append-only se tomó por auditoría. Resulta que
paga dos veces.

Y la rotación **se lee junto a «días con existencia»**: una rotación altísima
sobre un producto que estuvo tres días en el camión no es éxito de ventas, es
desabasto. Sin esa columna, la métrica premia justo lo que hay que corregir.

### Cada cifra con su antigüedad, y con el estado del mundo

§0.3 otra vez, y aquí es más agudo que en ninguna otra pantalla: las cifras salen
de una foto de cuando corrió el job. `analitica_refrescos` guarda, por vista,
cuándo se recalculó **y cuántos equipos no habían sincronizado en ese momento**.

Las dos cosas se muestran juntas porque juntas son la advertencia. «Actualizado
hace 1 min» suena perfecto; si en ese minuto un teléfono no había subido su día,
el total de ventas es un **piso**, no un total. Es la parte que se olvida, y la
que hace que alguien decida con una cifra incompleta creyendo que está completa.

### El refresh se dispara al cerrar la liquidación

Es el momento natural: al cerrar, las cifras del día quedan firmes. La
`clave_unica` por día lo hace idempotente —cerrar ocho rutas encola UN refresh, no
ocho— y el job va en la misma transacción que el cierre: si el cierre se deshace,
no queda encolado un recálculo de algo que no pasó.

Para el arranque, después de restaurar un respaldo y para la noche, está
`make refrescar-analitica`, que refresca de frente y espera: quien lo ejecuta a
mano quiere saber si salió bien, no que el resultado aparezca en el log del worker
media hora después.

### Por qué el laboratorio no escribe nunca

Hay una razón más fuerte que la prudencia del rol de solo lectura: **Streamlit
re-ejecuta el script completo en cada interacción**, y todo este sistema está
construido alrededor de no duplicar documentos. Un botón que escribiera ahí se
dispararía de nuevo al mover un filtro.

Y una que no es de arquitectura sino de pruebas: **un Streamlit roto devuelve HTTP
200.** El servidor sirve una página vacía y el script corre después, al abrir el
websocket; una excepción ahí no aparece en ningún código de estado. Por eso el
laboratorio se prueba con `AppTest`, que ejecuta el script como lo haría el
navegador — comprobar que "levanta" no comprueba nada.

---

## 27. El tablero de Gerencia: otra cadencia, otras tablas

La Fase 7 pedía un «dashboard sobre modelos de lectura precalculados, nunca sobre tablas
transaccionales». La primera reacción al leerlo fue que ya existían: la Fase 8 había dejado un esquema
estrella entero. **No servía**, y entender por qué es la decisión central de esta fase.

### La cadencia es lo que separa las dos cosas

Las vistas materializadas de la migración 0020 se refrescan **al cerrar la liquidación**, que es cuando
las cifras del día quedan firmes. Un gerente que abre el tablero a las once de la mañana vería ceros,
porque la última liquidación cerrada es la de ayer.

Son dos preguntas distintas y necesitan dos modelos:

| | pregunta | se recalcula |
|---|---|---|
| esquema estrella (0020) | «¿cómo nos fue?» — cifras **firmes** para analizar | al cerrar el día |
| tablero (0021) | «¿cómo va?» — cifras **vivas** para monitorear | al sincronizar |

### Por qué tampoco se lee directo de `ventas`

Porque las cifras del tablero son agregados, y calcularlos al vuelo significa barrer `ventas`,
`venta_partidas`, `no_drops`, `cobros` y `cuentas_por_cobrar` **en cada apertura de la pantalla**. Tres
gerentes con la app abierta y un *pull to refresh* nervioso son decenas de barridos por minuto sobre las
mismas tablas en las que están escribiendo los camiones, en una mini PC de oficina.

El síntoma no sería «el tablero va lento»: sería que **la sincronización** va lenta, y entonces el
vendedor espera en la calle por una pantalla que alguien está mirando en la oficina. Exactamente al
revés de lo que importa.

### Días rancios, no un ETL incremental

El job `recalcular_tablero` no carga «lo nuevo desde la última corrida» —ese es el error que la Fase 8
documentó largamente, y en un DSD los datos llegan tarde por diseño (§0.3)—. Lo que hace es:

1. buscar qué **días operativos** quedaron rancios: aquellos con algún documento cuyo `fecha_servidor`
   es posterior al `calculado_en` de su renglón;
2. recalcular ese día **completo** desde la verdad transaccional.

La diferencia con un incremental es la que importa: `fecha_servidor` decide **qué días** recalcular,
nunca qué renglones sumar. Una venta del lunes que sincroniza el jueves deja rancio el **lunes**, y el
lunes se recalcula entero.

Efecto lateral que paga solo: el job es **autorreparable**. Si el worker estuvo caído dos horas, la
siguiente corrida encuentra los días rancios por sí sola; nadie tiene que acordarse de encolar las
fechas correctas. Y por lo mismo la `clave_unica` es fija y sin fecha: ocho camiones subiendo a la vez
encolan **un** job, no ocho.

Hay un caso que no deja huella y hubo que cubrir aparte: **cancelar una venta no toca
`ventas.fecha_servidor`**. Sin la rama de `ventas_cancelaciones` en la detección, el total de un día
nunca bajaría — el tablero seguiría contando una venta que ya no existe. Hay prueba de eso.

### El grano es (día, vendedor), no (día, ruta)

Parece un detalle y no lo es. `ventas.ruta_id` y `no_drops.ruta_id` son **nullable**, y `mermas` no tiene
ruta en absoluto —una merma pertenece a un camión, no a una ruta; la caja se revienta entre dos tiendas—.
Con grano por ruta, el total del día sería la suma de los renglones **más** un cajón de «sin ruta» que
alguien olvidaría sumar, y un total que no cuadra con su desglose destruye la confianza en el tablero
completo.

`vendedor_id` es `NOT NULL` en los cuatro documentos. El grano por vendedor es **completo**: la suma de
sus renglones *es* el día, y la prueba que lo afirma suma el desglose y lo compara con el total.

El avance por ruta vive en su propia tabla porque su comparación es mensual —el objetivo es mensual— y
ahí sí se acepta perder los documentos sin ruta: se cuentan, se muestran, y el tablero dice en palabras
que esa cifra no está en ninguna barra.

### Un flujo y un saldo no se guardan igual

`tablero_dia` guarda **flujos** (lo que pasó ese día) y `tablero_cartera` guarda un **saldo** (lo que se
debe ahora). Un saldo no tiene fecha operativa: meterlo en el renglón de hoy haría que el «vencido del 3
de marzo» cambiara cada vez que se recalcula marzo, y nadie podría explicar por qué. Por eso es una tabla
de un solo renglón, y la tarjeta lo dice: *«al momento del cálculo, no del día que estás viendo»*.

### Borrar e insertar, no `ON CONFLICT DO UPDATE`

Si la única venta de un vendedor se cancela, su renglón tiene que **desaparecer**. Con un update
quedaría ahí con la cifra anterior, y un tablero que muestra la venta de ayer como la de hoy es peor que
uno vacío. Hay prueba de eso también.

Lo contrario pasa con el día de **hoy**, que se «sella» con los vendedores activos en cero: sin eso, el
tablero de las siete de la mañana no podría distinguir *«nadie ha vendido»* de *«el tablero no se ha
calculado»*. La primera es información y la segunda es una falla del worker. Un día **pasado** no se
sella: inventaría renglones en cero para vendedores que entraron después.

---

## 28. El objetivo de ruta existe porque sin él la tarjeta no puede tener datos

La Fase 6 dejó una lección cara: se construyó la captura de motivos de merma y no-drop, y **nada
publicaba los catálogos al dispositivo**. La pantalla estaba perfecta y no servía.

La tarjeta «avance vs objetivo» tenía el mismo riesgo exacto. No existía ninguna tabla de objetivos en
las veinte migraciones anteriores, así que la tarjeta se habría quedado en blanco para siempre sin que
nadie supiera si era un error o si faltaba capturar algo. Por eso la fase incluye `objetivos_ruta` **y**
su pantalla en el panel, y el tablero dice «sin objetivo» con esas palabras en las rutas que no lo
tienen, en vez de pintar una barra vacía.

Cuatro decisiones del objetivo:

- **Es mensual.** Un objetivo diario obligaría a mantener un calendario de días hábiles por ruta —y a
  decidir qué pasa con un puente, o con el día que el camión estuvo en el taller—. Nadie mantiene eso, y
  un objetivo que nadie mantiene es peor que ninguno: el tablero pintaría rojo todos los domingos y en
  dos semanas la gente dejaría de mirar el color.
- **El periodo es el día 1 del mes**, con un `CHECK` que lo exige. Sin él, dos renglones del mismo mes
  (día 1 y día 15) convivirían y el avance se mediría contra uno de los dos al azar.
- **Vacío borra el renglón; no guarda cero.** «Sin objetivo» y «objetivo $0» son dos cosas distintas, y
  la segunda daría 100 % de avance con la primera venta.
- **Se puede copiar del mes anterior sin pisar lo ya ajustado.** Si fijar ocho rutas cuesta ocho
  capturas cada mes, el mes que haya prisa no se van a fijar — y el tablero mentiría todo ese mes
  mostrando «sin objetivo» en rutas que sí tienen una meta en la cabeza de alguien.

### Gerencia fija los objetivos, y eso no contradice «solo lectura»

El rol `gerente` se definió como *«monitoreo y análisis; solo lectura sobre la operación»*. Fijar un
objetivo no es una operación: no mueve inventario ni dinero. Es el **plan contra el que se mide** la
operación, y si la única persona que mide no pudiera fijar contra qué, la tarjeta quedaría vacía
esperando que la oficina se acordara.

`tablero.ver` y `objetivos.administrar` son permisos **separados** a propósito. Un supervisor puede ver
cómo va el mes sin poder mover la meta: cambiar la meta cambia cómo se juzga a todo el equipo.

Y `tablero.ver` no es un alias de `analitica.ver`: el laboratorio es una herramienta de oficina con la
cartera completa a la vista, y el tablero es una pantalla de monitoreo. Que un supervisor pueda ver cómo
va el día no implica darle el laboratorio.

---

## 29. Gerencia entra en línea, y su teléfono no guarda credencial

Al construir la pantalla apareció un hueco que no estaba en el plan: **la app no tenía login en línea**.
El vendedor entra sin señal contra el hash Argon2id guardado en el Keystore, y ese era el único camino
implementado. El `tokenProvider` existía y nadie lo llenaba nunca.

Sin token, el tablero no podía consultar nada. Era el mismo defecto de la Fase 6 a punto de repetirse:
una pantalla impecable alimentada por un dato que no llega.

La solución no es darle credencial offline a Gerencia, y la razón es de diseño, no de comodidad:

- **No le serviría.** El tablero existe para ver lo que están haciendo **los otros**, y eso no se puede
  saber sin preguntarle al servidor. Un login offline lo dejaría entrar a una pantalla vacía.
- **No debería tenerla.** Un teléfono de gerencia no guarda cartera ni opera inventario. Guardar en él
  una credencial que permite entrar sin red es superficie de ataque a cambio de nada.

El servidor ya empujaba en esa dirección por su cuenta: `credencial_local` solo se devuelve cuando el
login trae un dispositivo registrado, y un login de rol `vendedor` **sin** `dispositivo_id` se rechaza
con 400. El camino en línea está cerrado para el vendedor sin que el cliente tenga que colaborar.

Tres detalles que importan:

- **`SesionDeGerencia` es un estado propio**, no un `SesionAbierta` con credencial nula. `SesionAbierta`
  implica que hay credencial en el Keystore y que el equipo puede volver a entrar sin red; confundir los
  dos casos haría que algún día una pantalla del vendedor leyera una credencial que no existe.
- **Se guarda el refresh token, y solo ese**, para que abrir la app por la mañana no exija teclear la
  contraseña. Y **salir lo borra**: dejarlo haría que la siguiente apertura reabriera la sesión sola, y
  eso convierte un botón de seguridad en un adorno.
- **El 403 del tablero no manda al login.** Un 401 y un 403 llegan igual y significan cosas opuestas:
  el primero se arregla volviendo a entrar y el segundo no se arregla nunca —hay que pedir el permiso en
  la oficina—. Mandar a alguien a teclear su contraseña cuando el problema es un permiso es tiempo
  perdido garantizado, así que la pantalla de «sin permiso» **no ofrece reintentar**.

---

## 30. Sin señal, las cifras viejas con su etiqueta valen más que una pantalla vacía

El tablero necesita red. Pero hay dos formas de fallar cuando no hay señal, y una es mucho peor:

- pantalla vacía con «sin conexión» → no sirve para nada;
- las cifras de hace una hora, **con su etiqueta** → sirven para casi todo lo que se decide con un
  tablero.

La segunda exige disciplina: la antigüedad tiene que estar a la vista y ser imposible de confundir con
una cifra fresca. De ahí la copia local (`tablero_cache`), que guarda el JSON tal como llegó más la hora
del **teléfono** al recibirlo.

Son dos horas distintas y las dos se muestran: el servidor calculó a las 10:05 y el teléfono lo bajó a
las 10:40, así que la cifra arrastra 35 minutos de camino **más** los que tuviera al calcularse.

`ErrorDeRed` es lo único que cae a la copia. Un 401 o un 403 se propagan: mostrar cifras viejas a alguien
a quien le quitaron el permiso sería exactamente lo contrario de lo que el permiso significa.

### La marca de frescura va arriba, antes de la primera cifra

Y hay una prueba que compara las coordenadas en pantalla para afirmarlo. Debajo de las tarjetas se
leería como nota al pie de algo que ya se dio por cierto.

La marca dice **dos** cosas, porque una sola es una media verdad peligrosa: «hace 2 minutos» suena
perfecto, y si en ese minuto dos teléfonos no habían subido su día, el total de ventas es un **piso**, no
un total. La advertencia enumera todos los motivos y no solo el primero — un tablero que dice «2 equipos
sin sincronizar» y se calla las 5 operaciones en cuarentena deja a quien lo lee creyendo que ya sabe todo
lo que falta.

---

## 31. El mapa es un lienzo, no un mapa

Misma decisión que el radar de la pantalla de alta de cliente (§13), con una razón que se suma: **la
pregunta no necesita calles**. «¿Se está cubriendo la zona o el vendedor se quedó en tres cuadras?» y
«¿las visitas perdidas están juntas en un rumbo?» se contestan con la forma de la nube de puntos. Las
calles no añaden nada a esas dos preguntas, y a cambio cuestan una llave de API que mantener, una
factura que crece con el uso y un dibujo que se queda en cuadros grises justo en la bodega sin cobertura.

Dos cosas del dibujo que no son obvias:

- **El coseno de la latitud.** A 20° de latitud un grado de longitud mide ~94 % de lo que mide uno de
  latitud. Sin esa corrección la nube sale estirada en horizontal, suficiente para que dos puntos que
  están uno encima del otro parezcan separados.
- **Un semilado mínimo (~275 m).** Con un rango de veinte metros, escalar al lienzo completo convertiría
  el **ruido del GPS** en un mapa, y alguien leería dispersión donde solo hay imprecisión.

Y una venta sin coordenadas **no aparece**, que es información: significa que el GPS no respondió.
Dibujarla en el centro o en 0,0 sería inventarle una ubicación. Los no-drops sí tienen `lat/lng` NOT NULL
por diseño, así que ahí no hay huecos.

---

## 32. Dos defectos que solo aparecieron al construir la Fase 7

1. **`:periodo::date` llegaba a PostgreSQL sin sustituir.** El `::` de PostgreSQL choca con la sintaxis
   de parámetros de SQLAlchemy y la consulta salía con el `:periodo` literal: `syntax error at or near
   ":"`. Dos de las trece consultas del tablero lo tenían. Se arreglaron con `CAST(:periodo AS date)` —
   el mismo motivo por el que `ingesta.py` ya usaba `CAST` en su `COALESCE`. **Se descubrió ejecutando
   las trece consultas contra PostgreSQL real antes de escribir una sola prueba**, que es la costumbre
   que la Fase 8 dejó.

2. **Las tarjetas se desbordaban en un teléfono angosto.** Un `GridView.count` exige una relación de
   aspecto **fija**, y el detalle de cada tarjeta mide lo que mide su texto —tres renglones en una, cinco
   en otra, y más si el tamaño de letra del sistema está subido—. La tarjeta más larga se desbordaba y
   Flutter pintaba la franja amarilla y negra encima de la cifra. Lo encontró la primera prueba de
   widget, no una revisión visual. Se cambió a filas de `IntrinsicHeight` con `Expanded`: la fila mide
   lo que necesita la tarjeta más alta y nunca se desborda.

Y una tercera que no es un defecto del código sino de las pruebas, y que vale igual: **las tablas nuevas
no estaban en `TABLAS_VOLATILES` de `conftest.py`**. `tablero_refrescos` y `tablero_cartera` no tienen
llave foránea a nada, así que el `TRUNCATE ... CASCADE` de las demás no se las llevaba, y el renglón que
dejaba una prueba sobrevivía a la siguiente: la prueba de «el tablero nunca se ha calculado» veía la hora
de la corrida anterior. Es el mismo defecto que las cachés globales de Streamlit en la Fase 8, con otra
cara — y las dos veces lo delató una prueba que afirmaba un estado inicial.

---

## 33. La zona horaria del servidor es una regla de negocio, no un ajuste

Apareció al revisar el tablero y vale para todo el sistema, así que queda escrito aquí.

**«Hoy» lo deciden `date.today()` en Python y `CURRENT_DATE` en PostgreSQL**, y los dos usan la zona del
sistema. El `fecha_operativa` de cada documento, en cambio, lo pone el **teléfono** con su día local.
Para que las dos cosas coincidan, el servidor tiene que correr en la hora de la operación.

Con el reloj en UTC y la operación en UTC−6, **a partir de las 18:00 locales «hoy» pasa a ser mañana**:

- el tablero de Gerencia muestra el día siguiente, vacío;
- la cobranza del día abre sin cobros;
- y el arqueo de la liquidación no cuadra con el efectivo que el vendedor tiene en la mano.

Lo que vuelve esto peligroso es que **a las once de la mañana todo funciona**. El síntoma aparece
justo en el momento del día en que se cierra la operación, que es cuando menos tiempo hay para
investigarlo.

No es una dependencia nueva —el panel, la cobranza y las cargas ya la tenían— pero estaba implícita en
trece lugares y en ninguno escrita. Ahora:

- `docker-compose.yml` fija `TZ` en los cinco servicios (`DSD_ZONA` lo cambia si algún día hace falta
  otra zona);
- `make db` crea el contenedor con `TZ`, y la variable `ZONA` del Makefile lo controla;
- `make doctor` compara la hora del sistema con la del contenedor **y falla si no coinciden**, porque
  una mezcla hace que `CURRENT_DATE` y `date.today()` discrepen: el renglón se escribe con una fecha y
  se lee con otra.

El contenedor toma su zona **al crearse**, no al arrancar, así que arreglar el sistema no basta si el
contenedor ya existía. `docs/ARRANQUE-DIARIO.md` §1.2-bis tiene los tres comandos que lo recrean sin
perder los datos.
