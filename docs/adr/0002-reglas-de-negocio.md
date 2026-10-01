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
