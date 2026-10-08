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

**Decisión (actualizada en octubre de 2026 por la dirección).** El camión es un
**almacén rodante**: la mercancía que no se vende se queda a dormir arriba y se
acumula con la carga del día siguiente. El cierre del día calcula

    esperado = inicial + cargado − vendido − merma + devuelto

de sus propios documentos, y lo compara contra un **conteo físico** de lo que se
quedó arriba del camión. La diferencia es lo único que importa del cierre.

Todas las demás pantallas **registran**. Esta compara, y es por eso la única que
puede atrapar un descuadre.

### El camión NO amanece en ceros, y por qué eso cambió todo

El modelo original era el opuesto en sus tres capas: la carga confirmada era «el
inventario completo del día», el cierre bajaba lo contado a la bodega y escribía un
ajuste para dejar el camión en cero, y el teléfono vaciaba `existencias_camion` al
recibir la carga liquidada.

Con mercancía durmiendo arriba del camión, ese modelo **le cobraba al vendedor como
faltante todo lo que no había vendido, cada noche**. Era mercancía fantasma: estaba
en el camión, se podía contar y tocar, y el sistema la declaraba perdida.

Lo que cambió (migración 0030):

1. La ecuación gana el término `inicial`. Sin él, todo el saldo de días anteriores
   aparecía como descuadre.
2. `cant_retornada` pasó a llamarse **`cant_contada`**: ya no baja mercancía a la
   bodega, se cuenta lo que se queda. Un nombre que miente sobre lo que guarda es el
   origen del siguiente error, y este renglón decide cuánto se le cobra a una
   persona.
3. Los renglones del cierre son los del **camión**, no los de la carga: la unión de
   lo que se cargó hoy y de todo lo que el camión trae con saldo distinto de cero.
   Si solo mirara la carga, el producto que lleva tres días arriba no tendría
   renglón, nadie lo contaría y nadie notaría si desapareciera.
4. El cierre deja el camión **en lo contado**, no en cero, y cuando el conteo cuadra
   **no escribe ningún movimiento**: no pasó nada físico que registrar.
5. El delta de la carga liquidada le lleva al teléfono **el ajuste**, no la orden de
   vaciar.

Cuando el vendedor sí entrega mercancía —cambia de ruta, se descontinúa un
producto— eso es un **traspaso** camión → bodega, que es un documento con su propia
huella. No es un efecto secundario del cierre del día. Cuando se escribió este
párrafo ese documento no existía todavía, aunque aquí se describiera como
disponible; existe desde la migración 0036, y el §50 explica por qué lo inicia el
vendedor y por qué pasa por un almacén de tránsito.

### El saldo inicial se deduce, no se fotografía

`inicial` sale de `saldo_inicial(en_camion, cargado, vendido, merma, devuelto)`, que
es la ecuación despejada: el saldo que el sistema tiene **ahora** menos lo que los
documentos de hoy le hicieron. No se guarda un snapshot al confirmar la carga, por
dos razones:

- Un snapshot envejece. Entre confirmar la carga y cerrar el día entran ventas que
  el teléfono sincroniza tarde, y el inicial tiene que seguir siendo el de esa
  mañana sin que nadie lo recalcule a mano.
- Un ajuste de oficina a media mañana —una corrección de inventario, un traspaso
  entre camiones— queda **absorbido** en el inicial, y eso es lo correcto: al
  vendedor se le cobra la diferencia entre su conteo y lo que el sistema tiene,
  nunca las correcciones que hizo la oficina.

La consecuencia útil es que `esperado` acaba siendo siempre el saldo vivo del
camión, así que `diferencia` es siempre «lo que conté menos lo que el sistema
tiene»: el único número que se le puede cobrar a una persona y defender frente a
ella.

### El devuelto suma, y es el signo que se escribe mal

Una devolución de cliente **entra** al camión, así que sigue arriba al contarlo.
Restarla haría aparecer un faltante del tamaño exacto de las devoluciones del día, y
el vendedor pagaría por mercancía que devolvió bien.

La ecuación vive en `app/domain/liquidacion.py`, en la columna generada
`liquidacion_detalle.diferencia` de PostgreSQL, y en el teléfono. Hay una prueba que
compara el módulo de dominio contra la columna generada con cuatro conteos distintos:
si divergieran, el vendedor y la oficina estarían discutiendo sobre dos números, cada
uno convencido de tener el del sistema.

### El conteo nace en cero, no en el esperado

Prellenarlo con el esperado haría que cerrar sin contar diera cuadre perfecto, y
entonces el cierre no significaría nada: sería un botón que dice que todo está
bien. Contar el camión es el único dato que esta pantalla no puede calcular, y es
justamente el que le da sentido a los demás.

Y un campo **vacío vale cero**, no "no lo cambies": al contar un camión, el
producto que no se anotó es el que no está arriba. Con la otra semántica, un producto
que se terminó quedaría con el conteo de un intento anterior y el faltante
desaparecería sin que nadie lo decidiera.

### Al cerrar, el camión queda EXACTAMENTE en lo contado

Tres cosas en una transacción:

1. Las cifras calculadas se **recalculan** desde los documentos. Es el mismo motivo
   por el que el arqueo recalcula el efectivo esperado, y aquí pesa más: lo que
   quedara viejo sería la cantidad de mercancía que se le cobra a una persona.
2. **`ajuste`** por la diferencia, para dejar el camión en lo contado. Si cuadra, no
   se escribe nada.
3. La carga pasa a **`liquidada`**, y ese `UPDATE` publica el delta que le lleva al
   teléfono el ajuste (§14).

El ajuste se escribe por el valor de `diferencia` —la columna generada—, que es el
mismo número que la pantalla le muestra al vendedor y el mismo que el teléfono va a
aplicar. Un número distinto en cualquiera de los tres lados es una discusión sin
árbitro.

Un faltante sale del sistema (`origen = camión`, `destino = NULL`) y es la pérdida
que se le carga al vendedor. Un sobrante entra al camión — si el signo estuviera al
revés, el teléfono mostraría mañana menos de lo que el vendedor trae.

### El teléfono SUMA la carga, y por eso recuerda cuáles ya sumó

Reemplazar era idempotente por naturaleza: escribir dos veces el mismo número da el
mismo número. Sumar no lo es, y un `pull` se repite cada vez que la red se corta a
media tanda. Sumar la misma carga dos veces le regalaría al camión una carga
completa: el vendedor la ofrecería, no la tendría, y el descuadre saldría en la
liquidación sin explicación.

De eso se encarga la tabla local `cargas_aplicadas`, con su gemela para el cierre:
una carga se suma una vez y un ajuste se aplica una vez.

### El cierre publica una DIFERENCIA, no un conteo

La oficina liquida lo de ayer a media mañana, con la carga de hoy ya encima del
camión y con ventas hechas. Un conteo de ayer aplicado como «el camión tiene esto»
borraría la carga de hoy y las ventas de la mañana. Una diferencia se suma al saldo
que haya y sigue siendo correcta cuando llega tarde — que es la forma de §0.3
aplicada a este delta.

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

---

## 34. Logs, métricas y Sentry: tres caminos por los que se fugan los datos

La Fase 9 añade observabilidad, y lo primero que hay que decir de las tres
piezas es lo que tienen en común: **sacan datos del sistema**. Un log rota en
disco y se manda por correo para depurar algo; una métrica se scrapea y se
guarda en una serie temporal que nadie protege como el panel; un evento de
Sentry viaja a un servidor de un tercero.

Si en alguno de los tres se cuela la cartera, el dato salió y no vuelve. Así que
las tres se construyeron al revés de lo habitual: primero el filtro de salida,
después la función.

### Lo que nunca entra en un log

`core/registro.py` redacta por nombre de clave, y la lista incluye lo obvio
—`authorization`, `password`, `token`— y dos que no lo son:

- **`payload` y `datos`.** Un lote de sincronización trae nombres de clientes,
  importes y saldos. Un `logger.info(payload)` descuidado es una copia de la
  cartera en texto plano, rotando en disco.
- **Las coordenadas.** Un log de lat/lng es un historial de dónde estuvo una
  persona. Si hace falta para depurar, está en la tabla del documento, con su
  control de acceso.

De lo que **sí** se registra —qué operación, de qué equipo, con qué resultado—
alcanza para contestar la pregunta real de un soporte de DSD: *«el vendedor dice
que la venta 000142 no llegó, ¿qué pasó con ella?»*. Y para eso hace falta la
otra mitad: el `peticion_id`.

### El `peticion_id` existe porque ocho camiones sincronizan a la vez

Con logs en texto y sin identificador, las líneas de ocho equipos vienen
intercaladas y no hay forma de saber cuáles pertenecen a la misma petición. Con
JSON y un `peticion_id` común, es un filtro.

Se devuelve en `X-Peticion-Id` y se respeta el que venga de fuera: Caddy o el
túnel pueden poner uno, y conservarlo permite cruzar sus logs con los de aquí.

El contexto vive en un `ContextVar`, y la razón importa: con asyncio hay decenas
de peticiones intercaladas en el mismo hilo, y una variable global haría que el
log de una venta saliera con el identificador de otra — que es **peor** que no
tenerlo, porque manda a investigar la petición equivocada. Y el `default` del
`ContextVar` es `None` y no `{}`: un diccionario como valor por omisión es UNO
para todo el proceso.

### Las métricas no llevan dinero, y es una decisión

Era tentador exponer la venta del día: ya está calculada en `tablero_dia` y
sería un renglón más. Es un error, y conviene dejar escrito por qué: un endpoint
de monitoreo termina scrapeado por un agente, guardado en una serie temporal y
graficado en un tablero que nadie protege con el mismo cuidado que el panel. La
facturación diaria del negocio no viaja por ahí.

Lo que se expone es **salud**: profundidad de colas, equipos rezagados,
operaciones en cuarentena, latencia y errores. Son los números que contestan
«¿está funcionando?», que es la pregunta que hace un monitor. Hay una prueba que
falla si aparece la palabra «venta» o «cartera» en la salida.

Dos detalles con su razón:

- **Apagado por omisión, y 404 cuando está apagado.** Un 401 confirma que hay
  métricas ahí y que solo falta la credencial. Un token equivocado también
  devuelve 404, porque si devolviera 401 el endpoint sería enumerable probando
  tokens.
- **La ruta se etiqueta con la plantilla**, no con la URL: un UUID de cliente en
  una etiqueta de métrica es un dato de negocio metido en el sistema de
  monitoreo, además de una explosión de cardinalidad.

### Sentry: el filtro propio es el que no depende de Sentry

`send_default_pii=False` y `include_local_variables=False` existen y están
puestos. Pero son opciones de Sentry, y pueden cambiar de nombre o de
comportamiento entre versiones. Así que hay un `before_send` propio que quita el
cuerpo de la petición, las cookies, las cabeceras secretas y las variables
locales de cada marco de la traza — en `procesar_lote`, las locales incluyen el
sobre completo.

Se prueba como función pura, y eso es el punto: es la última línea entre un
error de producción y una fuga, y no se puede verificar «mirando el panel de
Sentry a ver qué llegó». Cuando se mira, el dato ya salió.

Lo que sí viaja es el tipo de excepción, la traza sin valores, la ruta como
plantilla y el `peticion_id` — que es lo que permite ir al log del servidor
local y ahí sí ver el detalle, con su control de acceso.

Y está **apagado sin DSN**, con el paquete en un extra de `pyproject.toml`: una
instalación normal no lo trae. Mandar errores a un tercero es una decisión que se
toma a propósito, no un valor por omisión que viene activado.

---

## 35. RLS: la segunda cerradura, y los dos roles que la hacen posible

El filtrado por ruta ya existía y funcionaba: está en `deps.py` y en el `WHERE`
de cada consulta. La migración 0022 no lo reemplaza.

### Qué protege exactamente

No protege de un atacante con la contraseña de la base: quien tiene el rol dueño
salta las políticas por diseño, y tiene que poder — los workers y el CLI no
pertenecen a ninguna ruta. Protege de lo que de verdad pasa en un sistema que
sigue creciendo:

- **Una consulta nueva que olvida el filtro.** Es el error más común y el más
  silencioso: la pantalla funciona, se ve bien, y devuelve clientes de otra
  ruta. Con RLS devuelve vacío, que es un bug que alguien reporta el mismo día.
- **Una inyección SQL**, el día que entre por un parámetro mal tratado: lo que
  puede leer queda acotado al alcance de quien hizo la petición.
- **Un `JOIN` que se lleva más de lo que debía.**

Dicho de otra forma: el riesgo más probable de este sistema no es un atacante,
es un bug nuestro. RLS es la red para ese caso.

### `anonimo` no significa «sin restricción»

Es la decisión que hace que todo lo demás sirva. `obtener_sesion` fija el
alcance anónimo **al abrir la sesión**, antes de cualquier consulta, y las
políticas rechazan ese rol por completo. Dos consecuencias:

- una conexión reciclada del pool **nunca** conserva el alcance de la petición
  anterior;
- un endpoint que se olvide de autenticar no ve nada, en vez de verlo todo.

Fallo cerrado. Si `anonimo` significara «sin restricción», fijarlo sería peor que
no fijarlo, y hay una prueba dedicada a eso porque es el camino que nadie mira.

### `set_config(..., false)` y no `SET LOCAL`

`SET LOCAL` vive hasta el final de la transacción, y el alcance se perdería en
el primer `commit()`. Una petición que escribe y luego lee —el alta de un
cliente, el cierre de una liquidación— empezaría a recibir resultados vacíos
**después de guardar**. Ese fallo aparecería solo en algunos endpoints y sería
dificilísimo de atribuir.

El riesgo de ensuciar la conexión del pool se cierra en el otro extremo, con el
alcance anónimo del párrafo anterior.

### Dos roles de PostgreSQL

El dueño salta las políticas (salvo con `FORCE ROW LEVEL SECURITY`, que aquí no
se usa a propósito), y eso es exactamente lo que necesitan Alembic, los workers y
el CLI. Así que la API se conecta con `dsd_api`: no es dueño de nada, no tiene
`BYPASSRLS`, no puede hacer DDL.

Sin `DSD_DATABASE_URL_API` los dos son el mismo rol y **las políticas quedan
escritas y sin efecto**. Se permite en desarrollo, `/salud` lo reporta en `rls`,
y en producción **la API no arranca**: es el mismo criterio que el secreto JWT.
Un sistema que cree tener RLS y no lo tiene es peor que uno que sabe que no.

### Lo que no lleva políticas, y por qué

**Las tablas de identidad** (`usuarios`, `dispositivos`, `sesiones`, roles,
permisos). No es un descuido: la autenticación las lee **antes** de saber quién
manda la petición —es lo que averigua— con el alcance todavía en `anonimo`.
Ponerlas bajo política dejaría la API sin poder autenticar a nadie.

**Las vistas.** `v_cartera_cliente` se evalúa con los privilegios de su dueño,
así que las políticas de sus tablas base no se aplican al consultarla. Queda
escrito porque es contraintuitivo y porque marca la frontera de lo que la 0022
cubre: en ese camino, el guardia sigue siendo el `alcanza_ruta()` del endpoint.

### La prueba que ninguna de las otras 705 hacía

Toda la suite corre como el rol dueño, que salta las políticas. Es decir: las
políticas podrían estar escritas al revés y todo seguiría verde.

Así que `tests/test_rls.py` abre su propia conexión con `dsd_api` y **aplica
`db/ops/rol_api.sql` tal cual, sin copiarlo**. Si el archivo de operaciones se
separa de lo que la API necesita —una tabla nueva sin `GRANT`, un
`DEFAULT PRIVILEGES` olvidado— esas pruebas se ponen rojas antes de que el
síntoma aparezca al desplegar.

Y la fixture que más importa es la que monta **la API completa** sobre el rol
restringido, porque el modo de fallo de un despliegue con RLS no es una
excepción: es que el panel abra en blanco y el pull devuelva cero registros, sin
ningún error en el log. «No ver nada» es una respuesta válida.

### Un hallazgo: la lista de roles de oficina estaba escrita dos veces, distinta

Escribir las políticas obligó a declarar por tercera vez qué roles ven la
operación completa, y entonces se notó que las dos primeras no coincidían: el
filtro de la lista de clientes incluía a `supervisor` y `alcanza_ruta()` no. Un
supervisor veía a todos los clientes en la lista y recibía un 403 al abrir la
cartera de uno que no fuera de su ruta.

Nadie lo había notado porque las dos cosas se ven correctas por separado. Se
unificó en `ROLES_DE_OFICINA` incluyendo a `supervisor` — ese rol ya puede cerrar
la liquidación de cualquier ruta y leer el payload de cualquier equipo en
cuarentena, así que el límite por ruta era el que estaba fuera de lugar— y hay
una prueba que compara la constante de Python con la función de PostgreSQL.

---

## 36. El borrado remoto: nunca se borra lo que no se ha entregado

Revocar un equipo y borrarlo son dos cosas distintas, y mezclarlas cuesta dinero
real. `estado = 'revocado'` existía desde la 0001 y protege los datos del
**servidor**: mata los tokens. Lo que no hace es quitar la copia que el teléfono
lleva dentro — la cartera de su ruta, los precios, las ventas del día— que es
justo lo que importa cuando el equipo se perdió o la persona se fue.

### Por qué no se borra de inmediato

Un borrado inmediato parece lo más seguro y es la decisión más costosa que se
podría tomar, porque **el motivo real de un borrado casi nunca es un robo**:

- el vendedor renunció y hay que recuperar el equipo;
- se cambió de teléfono;
- el equipo se extravió y aún no aparece.

En los tres casos el aparato puede traer dentro un día de ventas sin
sincronizar. Borrarlas es perder dinero cobrado, sin registro de a quién se le
vendió ni cuánto, y sin forma de reconstruirlo.

Y en el caso que **sí** es un robo, borrar rápido no gana nada: la base local
está cifrada con SQLCipher, su llave vive en el Keystore detrás del PIN, y el PIN
se verifica con Argon2id de 64 MiB. Quien se lleva el teléfono no puede leer nada.

### El flujo, y la pieza que lo hace posible

```
ORDENAR → DRENAR → BORRAR → CONFIRMAR
```

La pieza es que **ordenar deja el equipo en `suspendido`, no en `revocado`**, y
que un equipo suspendido **puede hacer push y no puede hacer pull**: entrega lo
que trae y no recibe nada nuevo. Es la única razón de que ese estado exista como
algo más que una etiqueta.

La excepción se declara en el endpoint del push (`ActorQueEntregaDep`) y no en el
guardia, a propósito: si el relajado fuera el default, cada endpoint nuevo
nacería aceptando equipos suspendidos y nadie lo notaría.

Si el teléfono no logra entregar —sin señal, apagado— **no borra nada**: queda
bloqueado mostrando cuántas operaciones le faltan por subir. Ese número es la
razón de que el equipo no se haya borrado, y verlo convierte «mi teléfono se
bloqueó» en «tengo que conectarme». Un equipo bloqueado con datos dentro es
recuperable; uno borrado, no.

### Ordenar no es confirmar

Son dos columnas y no una. La orden prueba que alguien lo pidió; **solo la
confirmación prueba que ocurrió**, y entre las dos puede pasar una semana. Una
orden sin confirmar que lleva días no es un dato que esperar: es una decisión que
tomar — ir por el equipo.

El teléfono confirma **después** de borrar y no antes: si se cortara la red justo
ahí, el aparato ya está limpio y la oficina lo ve como «orden sin confirmar»,
que es el lado correcto del que equivocarse. Confirmar antes dejaría al servidor
creyendo que el equipo está limpio cuando aún tiene todo dentro.

Y se guarda `borrado_cola_al_confirmar`, que debe ser 0. No se rechaza un número
distinto: se registra. Si algún día llega uno, es que una versión del cliente se
saltó la regla, y esa columna es la única forma de notarlo.

### El canal de órdenes, y por qué no va en la respuesta del push

`GET /v1/dispositivos/mio` es un viaje aparte al empezar cada sincronización.
La razón es concreta: **un equipo al que se le ordenó el borrado y que no tiene
nada en la cola nunca haría push**, así que nunca recibiría la orden si viniera
dentro de esa respuesta. Sería la única orden del sistema imposible de entregar a
su destinatario.

De paso resuelve el otro aviso que no tenía dónde vivir: cuántos días le quedan a
la credencial. Enterarse a las 6 de la mañana, en la bodega, con el camión
cargado y sin señal, es un día de ruta perdido — y el servidor lo sabe con días
de antelación.

Lo que **no** es, es una dependencia dura: si esa consulta falla por cualquier
cosa que no sea un 401 —un 404 contra un servidor más viejo, un 500— se sigue
adelante sin órdenes. Convertir un endpoint nuevo en requisito para entregar
ventas sería cambiar una función de administración por la operación del día.

---

## 37. Tres defectos que solo aparecieron al construir la Fase 9

1. **EL BORRADO REVENTABA LA APP JUSTO DESPUÉS DE FUNCIONAR.** La primera
   versión de `BaseLocal.borrarTodo()` cerraba la base con `dispose()`. Los
   providers de la pantalla del vendedor —la cola pendiente, la lista de
   clientes— se recalculan en el mismo cuadro en que el árbol cambia a la
   pantalla de «equipo borrado», y leían una base ya cerrada: `Bad state: This
   database has already been closed`. En un teléfono real es una pantalla roja
   inmediatamente después de un borrado exitoso, que es el peor momento posible
   para una excepción porque **parece que el borrado falló**.

   La corrección no fue cerrar más tarde: fue **no cerrar**. Se vacían las tablas
   con `secure_delete` encendido, se compacta con `VACUUM` y se desenlazan los
   tres archivos, dejando el handle abierto sobre una base vacía. Las lecturas
   devuelven cero renglones, la transición es limpia, los archivos ya no existen
   para nadie más y el inodo desaparece cuando el proceso termina.

   De paso quedó claro que un `DELETE FROM` no bastaba por sí solo: SQLite
   conserva las páginas liberadas con su contenido, y el `-wal` es una copia de
   las últimas transacciones — justamente las ventas que se acababan de subir.

2. **UNA RESPUESTA INESPERADA EN LAS ÓRDENES ROMPÍA LA SINCRONIZACIÓN ENTERA.**
   El `try/on Exception` alrededor de la consulta de órdenes no cubría los
   `Error`, y un JSON con otra forma —la página de error de un proxy, el HTML de
   un portal cautivo, un servidor más viejo— produce un `TypeError`, que en Dart
   es un `Error` y no una `Exception`. El resultado: el vendedor no podía
   entregar sus ventas porque una consulta administrativa devolvió algo raro.

   Lo encontró una prueba de la app que ya existía, cuyo transporte falso
   contestaba la forma del pull a cualquier GET. Es uno de los dos o tres
   lugares del sistema donde `catch (_)` es lo correcto, y ahora está escrito al
   lado por qué.

3. **LAS FILAS COMPARTIDAS DE `change_log` SE VEÍAN SIN AUTENTICAR.** La
   política decía `ruta_id IS NULL OR ruta_id = ANY(...)`, y con alcance anónimo
   `ruta_id IS NULL` es verdadero: una conexión sin autenticar podía leer el
   catálogo y **la lista de precios completa**. Lo delató la prueba del alcance
   vacío, que recorre tabla por tabla esperando cero. Se añadió
   `dsd_usuario() IS NOT NULL` como puerta — un usuario cualquiera, no un rol
   concreto, porque cualquier perfil con dispositivo registrado hace pull.

---

## 38. El papel en paralelo es el patrón de medida, no un respaldo

El plan pide el piloto de la Fase 3 *«con el proceso de papel en paralelo»*, y
esa frase se puede leer de dos maneras. La equivocada: el papel está ahí por si
la app falla. La correcta: **el papel es el instrumento de medición.**

La diferencia no es filosófica, decide si el piloto sirve. Si el papel es un
respaldo, nadie lo mira cuando la app no falla, y al final de las dos semanas lo
único que hay es la impresión de que «se portó bien». Si el papel es el patrón,
se captura todos los días y se compara — y entonces el piloto produce cifras.

La razón de que haga falta un patrón externo es una sola, y es la frase que
justifica toda la migración 0024:

> **El sistema no puede medir la venta que no existe en el sistema.**

Ninguna métrica de la Fase 9, ningún log estructurado y ninguna tarjeta del
tablero ven ese hueco: todos leen lo que sí entró. Una venta que ocurrió en la
calle y nunca se capturó —porque la app estorbó, porque el vendedor la apuntó
«para luego», porque se cerró el carrito— es invisible desde adentro y es
exactamente la falla que el piloto viene a buscar.

De ahí que los dos instrumentos del piloto midan cosas distintas y no se puedan
sustituir:

| Instrumento | Lo mide | Detecta |
|---|---|---|
| El sistema sobre sí mismo | retraso de entrega, cancelaciones, cuarentena, liquidaciones | fallas **técnicas** |
| El papel capturado a mano | documentos, importe, cobranza, visitas | fallas de **adopción** |

### La diferencia que se cierra sola no es la misma falla

A las 8 de la mañana el papel dice 23 documentos y el sistema 21. A mediodía el
teléfono sincroniza y el sistema dice 23.

Esa diferencia **no era pérdida de datos**: era retraso de entrega, que es §0.3
funcionando como se diseñó. Y con una sola cifra guardada se ve idéntica a dos
ventas que no existen, que es la falla más grave del sistema.

Por eso `piloto_jornadas` guarda **dos cifras del sistema** por cada día: la que
decía al capturar —congelada, leída otra vez en el servidor al guardar y no
tomada del formulario— y la que dice hoy, calculada al abrir la pantalla.

    papel 23 · al capturar 21 · hoy 23   →  retraso. No detiene nada.
    papel 23 · al capturar 21 · hoy 21   →  DOS VENTAS QUE NO EXISTEN.

La cifra congelada se vuelve a leer al guardar y no se recibe del formulario,
porque si viniera de la pantalla mediría el tiempo que tardó alguien en teclear.

### Los umbrales se escriben antes, o no valen nada

Los doce criterios de salida viven en `piloto_criterios`, **sembrados por la
migración** con fecha anterior al primer día del piloto, y **no hay pantalla
para cambiarlos**. Eso no es una función que falte: es la propiedad que los hace
servir.

Si los criterios se deciden al final, se deciden mirando el resultado, y
entonces el piloto no decidió nada: justificó lo que ya se quería hacer. Las dos
semanas **van** a producir incidencias —para eso son— y en ese momento la
pregunta «¿esto es suficiente para seguir?» ya no se puede contestar con
honestidad, porque los teléfonos ya se quieren comprar. Si un umbral de verdad
estaba mal puesto, se cambia con una migración, que deja huella y fecha.

Dos de los umbrales parecen raros y son los más pensados:

- **«Cero bloqueos en la SEGUNDA semana»**, no cero en el piloto. La primera
  semana va a tener bloqueos: para eso es el piloto. Un umbral de cero sobre las
  dos semanas haría fracasar al piloto que funcionó. Lo que decide no es si algo
  se rompió, es si se dejó de romper.
- **La cobertura de captura es el primer criterio y es bloqueante.** Mide que el
  papel se haya capturado todos los días, y es el criterio del criterio: un
  piloto donde se dejó de capturar el día cuatro no midió nada, y es la forma más
  común de que un piloto no pruebe nada sin que nadie lo note.

### La bitácora se captura en el panel, no en la app

Era tentador poner una pantalla de «reportar problema» en el teléfono, con su
operación en el outbox. Sería un error: **no se le agregan funciones a la app que
se está poniendo a prueba.** Esa pantalla sería código nuevo sin piloto dentro
del piloto, con su propio camino de sincronización que puede fallar — y si falla,
se pierden justo los reportes de las fallas.

El argumento que cierra la discusión: la incidencia más importante que puede
ocurrir es *«la app no abrió»*, y en ese escenario ninguna pantalla de la app
puede reportarla. El canal es el que ya existe y no depende de nosotros — el
vendedor habla por teléfono y la oficina teclea con la hora.

### Y aquí sí va texto libre, al contrario que en los no-drops

La regla de la Fase 6 es «texto libre = datos inanalizables», y sigue en pie
para los motivos de no-venta: se capturan veinte veces al día y su valor está en
poder **contarlos**.

Una incidencia de piloto es lo contrario: pasa una vez y su valor es el
**detalle**. «Se cerró la app al agregar el tercer renglón del carrito con el
teclado abierto» no cabe en ningún catálogo y es justo lo que se necesita para
reproducirla. Así que se cierra el catálogo de lo que ya se sabe —categoría y
severidad, que son para contar— y se deja texto para lo que no se sabe.

**Un piloto cuyo formulario solo acepta opciones conocidas solo puede descubrir
lo que ya estaba previsto.**

### El defecto que encontró la construcción: el cero que premiaba no apuntar

Dos de los doce criterios leen la bitácora: los bloqueos de la segunda semana y
los minutos perdidos por jornada. Con la bitácora **vacía**, los dos salían en
verde — cero bloqueos, cero minutos.

Es decir que la forma más fácil de aprobar el piloto era **no registrar nada**, y
de paso la única que no deja rastro. Lo encontró una prueba escrita para otra
cosa: la que afirma que ningún criterio se pinta de verde sin datos.

Dos semanas de una app nueva con cero incidencias de cualquier tipo no es una app
perfecta: es un registro que nadie llevó. Siempre hay un «el teclado tapa el
total». Así que con la bitácora completamente vacía los dos criterios quedan
**sin medir**, que es la verdad, y basta una incidencia —la molestia más chica—
para que el conteo vuelva a ser legible. Lo que se comprueba no es que haya
problemas: es que alguien está preguntando.

La distinción fina, que la prueba obligó a escribir: los criterios que el
**sistema mide de sí mismo** sí pueden leer cero honestamente —la cuarentena se
llena sola— y los que dependen de que **una persona escriba algo**, no.

### Lo que el piloto no prueba, declarado como dato

`impresion_bluetooth` está en la tabla de criterios con `evaluable = false` y su
nota. `Impresora` solo tiene implementación simulada hasta que llegue la
EC-MP200, así que durante el piloto el comprobante del cliente sigue siendo la
nota de papel — que va en paralelo de todos modos.

No bloquea el arranque del piloto y sí bloquea el despliegue. Está en la tabla
para que **no se convierta en un supuesto** el día que se compren los teléfonos:
la pantalla lo muestra bajo «lo que este piloto no prueba», y hay una prueba que
exige que todo criterio no evaluable traiga su explicación escrita.

### El veredicto lo firma una persona

El sistema **sugiere** el veredicto a partir de los criterios y no lo guarda
solo. Poner esto en siete camiones es una decisión de negocio, y un veredicto
automático le quitaría a alguien la obligación de firmarla. `pilotos.veredicto`
exige su nota: es lo que se va a leer dentro de tres meses, cuando nadie recuerde
por qué se dijo lo que se dijo.

Y hay tres veredictos, no dos. **«Repetir»** existe porque es el resultado más
probable de un primer piloto honesto, y sin esa opción la única salida del «casi»
es aprobarlo.

---

## 39. La mercancía entra con un documento, y solo a una bodega

Durante nueve fases el sistema no tuvo **cómo meter mercancía**. El hueco no
rompía ninguna prueba y por eso duró: el libro mayor contemplaba `'compra'`
(proveedor → bodega) y `'ajuste'` desde la migración 0004, y el permiso
`inventario.ajustar` estaba definido desde la 0009 — sin que **ningún rol lo
tuviera** y sin que **ningún código lo pidiera**. Un permiso que nadie tiene y
nada consulta: la función se planeó hasta el nombre y nunca se construyó.

El motor estaba y la puerta no. Y la forma del fallo es la peor posible: **no
fallaba.** Cargar un camión desde una bodega sin existencia funciona, porque
`existencias` no lleva `CHECK (cantidad >= 0)` a propósito (§0.1: la mercancía ya
se movió en el mundo físico y rechazar el registro no la devuelve). Así que el
sistema no se quejaba — dejaba la bodega en negativo, y eso no se notaba hasta
abrir el filtro de negativos de la pantalla de inventario.

Mientras tanto, la única manera de operar era inyectar inventario con SQL a mano:
un `UPDATE existencias` del que el libro mayor no tiene nada que decir.

### Un documento, no un «sumar N piezas»

La pantalla rápida habría sido un formulario de un renglón: producto, cantidad,
guardar. Rompe la propiedad que sostiene todo el inventario de este sistema:
**cada movimiento del libro mayor apunta a un documento que lo explica**
(`documento_tipo`, `documento_id`).

El día que esta pantalla importa es el día de una auditoría de inventario, y
«¿de dónde salieron estas 240 piezas?» tiene que poder contestarse con una
remisión de proveedor, no con «alguien lo capturó».

Así que la entrada reutiliza el ciclo que la carga ya tenía probado:

    BORRADOR  →  (renglones)  →  CONFIRMADA  →  libro mayor + existencias

En borrador no mueve nada, y eso tampoco es ceremonia: capturar quince renglones
de una remisión toma veinte minutos, y un sistema que mueve inventario al primer
renglón obliga a terminar sin interrupciones o deja la bodega a medio recibir.
Confirmar es **idempotente por estado** con `FOR UPDATE`, por lo mismo que la
carga: un doble clic en una pantalla lenta duplicaría una remisión completa y el
sobrante solo aparecería semanas después, en un conteo físico, sin forma de saber
qué pasó.

### Tres motivos, dos tipos de asiento

| Motivo del documento | Asiento en el libro mayor | Qué es |
|---|---|---|
| `compra` | `compra` | llegó del proveedor, con su remisión |
| `inicial` | `ajuste` | lo que ya estaba el día que arrancó el sistema |
| `ajuste` | `ajuste` | el conteo físico encontró **más** de lo registrado |

`inicial` y `ajuste` comparten tipo porque ninguno se le compró a nadie —
escribir `compra` sería mentirle al libro mayor. Se distinguen por el documento,
y esa es la razón de que el movimiento apunte al documento y no al contrario: el
libro mayor se queda con sus ocho tipos y el detalle se recupera siempre.

El inventario inicial **exige nota**, con un `CHECK` en la tabla además de la
validación en la pantalla. Es el documento que explica de dónde salió todo el
inventario del arranque y se lee una sola vez en la vida del sistema: el día que
algo no cuadra. Sin nota, ese día no hay nada que leer.

### El destino es siempre una bodega, nunca un camión

No es una limitación de la pantalla: es **§0.2**, el almacén del camión tiene un
único dueño exclusivo. La oficina nunca escribe existencias de un camión — para
eso existe `traspasos`, que para bajar mercancía lo inicia el vendedor y la bodega
cierra contando (§50), y que para el sentido contrario sigue sin construirse porque
el documento correcto para subirle mercancía a un camión es una **carga**.

Una entrada directa a un camión le cambiaría el inventario bajo los pies a
alguien que está vendiendo offline con otra cifra en el teléfono, y el descuadre
le aparecería en su liquidación como un sobrante del que no sabe nada. Mercancía
nueva entra a la bodega y de ahí sube con una carga, que es el camino que el
teléfono ya sabe recibir.

Es una regla entre tablas, así que no puede ser un `CHECK`: la valida el router y
el formulario **solo ofrece bodegas** — ofrecer el camión sería invitar al error
que la validación rechaza.

### No hay costo, y es una decisión

Una compra tiene un costo y capturarlo era un campo más. Pero `productos` no
tiene columna de costo y no hay módulo de compras —es Fase 10 del plan—, así que
el número no alimentaría nada: ni margen, ni valuación, ni costo de lo vendido.

Un campo que se captura y nadie lee es peor que su ausencia, porque **parece** que
el sistema sabe el costo. Y elegir aquí entre costo promedio, último costo o PEPS
sería improvisar una regla contable en una pantalla de bodega. Lo que sí se
guarda es la referencia del papel: con la remisión a la mano, el costo se
recupera el día que exista dónde ponerlo.

### Lo que se captura y lo que se guarda son dos cifras

El renglón guarda la cantidad en **unidad base** —como el libro mayor— y además
**lo que la persona tecleó**: «10 CAJA». No es redundante. «240» no se puede
revisar contra una remisión que dice «10 cajas», y el renglón se tiene que poder
leer igual que el papel que se está capturando. Es el mismo razonamiento del
folio impreso frente al folio del servidor (§4).

Y cuando el mismo producto entra con dos presentaciones distintas, la cantidad
base se suma y la cifra capturada se deja en **nulo** en vez de inventar una
suma de cajas con piezas: la pantalla dice «varias presentaciones», que es la
verdad.

### Lo que esta pantalla NO hace, dicho aquí para que no se dé por hecho

**Las salidas por ajuste** se construyeron después, y tienen su propia sección
(§40): el documento de entrada solo suma, así que un conteo que encuentra
**menos** necesitaba uno en sentido contrario.

**El módulo de compras** (proveedores como catálogo, órdenes de compra, costos,
cuentas por pagar) sigue siendo Fase 10. El proveedor aquí es texto libre a
propósito: inventar la tabla obligaría a mantener un catálogo que nada más usa, y
lo que de verdad se necesita el día de la auditoría es poder leer de quién llegó
y con qué papel.

---

## 40. A lo que ya pasó se le cree; a lo que se está capturando se le revisa

La §39 dejó el inventario entrando y faltaba la otra dirección: un conteo físico
que encuentra **menos** de lo registrado. Y al construirla apareció la pregunta
que parecía contradecir el principio fundacional del sistema.

**§0.1 dice que el servidor marca y no rechaza**, porque el mundo físico ya
ocurrió: una venta offline que llega tarde, una merma del camión sin existencia.
El manejador de sincronización lo dice con todas sus letras — *«se MARCA, no se
rechaza, el cartón ya está roto»*— y hay pruebas de eso.

Entonces, ¿por qué una salida de bodega **sí** se rechaza cuando dejaría la
existencia en negativo?

Porque no son la misma clase de dato, y confundirlas es el error:

| | De dónde viene | Qué se hace |
|---|---|---|
| Venta o merma del camión | **ya pasó en la calle**, llega tarde por sync | se acepta y se **marca** |
| Salida de bodega | **se está tecleando ahora**, con el anaquel a la vista | se **revisa** |

Si el sistema dice 3 y alguien captura una salida de 5, no hay ningún hecho
físico que respaldar: **el anaquel no puede tener menos que nada.** Es un dedazo.
Y bloquearlo no niega la realidad — la protege, porque un asiento equivocado en
un libro *append-only* no se borra: se arrastra, y la corrección exige otro
documento que a su vez hay que explicar.

    A lo que ya pasó se le cree; a lo que se está capturando se le revisa.

La validación va **dentro** de la transacción y después de tomar el candado de
cada renglón de `existencias`, en orden de producto. Validarla al capturar sería
mirar un número que cualquier carga puede mover un segundo después: lo que
importa es la existencia en el instante en que se escribe el asiento. Y se suman
todos los renglones del mismo producto antes de comparar — dos lotes de 60
contra una existencia de 100 no pasan, aunque ninguno exceda por separado.

### En un conteo se captura lo que se contó, no la diferencia

Quien hace un conteo anota lo que ve en el anaquel: «80». No anota «faltan 20»,
porque eso exige restar a mano, a las siete de la mañana, producto por producto
— y la resta hecha a mano es exactamente de donde salen los errores que este
documento viene a corregir.

El renglón guarda las tres cifras y **la base de datos impone la aritmética**:

```sql
CONSTRAINT conteo_cuadra
    CHECK (contado IS NULL OR cantidad = existencia_al_capturar - contado)
```

Un bug en el panel no puede escribir un renglón de conteo que no cuadre con su
propia resta. Y si el conteo encuentra **más**, esto no es el documento: la
pantalla lo dice y manda a una entrada con motivo «ajuste», en vez de aceptar
una salida negativa.

Un conteo es **por producto y no por lote**, y eso tampoco es una limitación de
la pantalla: `existencias` guarda un número por `(almacén, producto)` y no tiene
dimensión de lote. Aceptar un lote prometería una precisión que la tabla contra
la que se compara no tiene. En una merma sí se captura, que es la que se
identifica por tarima.

### La cifra congelada solo vale contra el momento en que se congeló

El renglón de conteo guarda la existencia del momento de capturar. Si al
confirmar la existencia ya es otra —salió una carga entre el conteo y el
cierre—, la resta guardada **ya no describe nada**: el anaquel también perdió
esas piezas, así que aplicarla descontaría dos veces.

Se rechaza el documento diciendo exactamente eso, en vez de hacer la aritmética
equivocada en silencio. Es el mismo razonamiento que el cuadre del piloto (§38),
donde la cifra del sistema se congela al capturar el papel: **una cifra
congelada solo vale contra el momento en que se congeló.**

### Un faltante de conteo no tiene motivo, y no se le inventa uno

El catálogo de motivos ya existía y es cerrado desde la Fase 6 (`motivos_merma`:
`CADUCADO`, `DANADO_BODEGA`, `ROBO`, `MUESTRA`…), así que no se inventó uno
nuevo: se reutiliza el que el teléfono ya sincroniza.

Pero el motivo es **obligatorio en una merma y prohibido en un conteo**, y esa
asimetría es la decisión:

> Un faltante de conteo es, por definición, un faltante **cuya causa no se
> conoce**. Si se supiera, se habría capturado como merma el día que pasó.

Obligar a elegir un motivo haría que alguien marcara `ROBO` o `DANADO_BODEGA`
sin saber, y eso convierte un dato duro —«faltan 20 piezas»— en **una acusación
inventada** que después alguien va a leer como un hecho. En un negocio donde el
faltante se le puede descontar a una persona, esa diferencia no es académica.

Las dos clases **sí** exigen nota, por lo mismo que el inventario inicial de la
§39: en un conteo hace falta saber quién contó, y en una merma qué pasó más allá
del código.

### Contar dos veces reemplaza; mermar dos veces suma

Es la misma tabla, el mismo `ON CONFLICT`, y el comportamiento opuesto — porque
el significado es opuesto:

- **Contar dos veces el mismo producto** significa que la primera cuenta estaba
  mal. Se **reemplaza**: no hay el doble de faltante.
- **Mermar dos veces la misma tarima** son dos pérdidas distintas. Se **suma**,
  igual que en las entradas.

### Dos tipos del libro mayor, al contrario que en las entradas

En la §39, `inicial` y `ajuste` comparten el asiento `'ajuste'`. Aquí no:
`conteo` → `'ajuste'` y `merma` → `'merma'`. La diferencia entre **una pérdida
identificada** y **un descuadre sin explicar** es la que decide si hay algo que
arreglar en la bodega, y poder separarlas leyendo el libro mayor vale más que la
simetría con las entradas.

Y una merma de bodega viaja al **almacén de merma** si hay uno activo, igual que
la del camión: el libro mayor dice origen y destino, y las existencias de los
dos lados lo reflejan — si no, el almacén de merma quedaría siempre en cero. Sin
almacén de merma configurado, el destino queda en `NULL`, que el `CHECK` del
libro mayor permite porque solo exige uno de los dos lados.

### Un camión no se ajusta por aquí

Su faltante se descubre y se cobra en la **liquidación**, que compara lo cargado
contra lo retornado y ya tiene su pantalla. Ajustarlo por el panel registraría
el mismo faltante dos veces. Es la contraparte de la regla de la §39: a un
camión no se le mete mercancía desde la oficina (§0.2), y tampoco se le saca.

---

## 41. El costo no vive en `productos`, y el promedio es ponderado

El módulo de compras —Fase 10 del plan— se apoya en una sola pieza: **el
costo**. Y antes de construirlo, el sistema entero no sabía lo que cuesta nada.

La huella estaba a la vista desde la migración 0006.
`merma_detalle.costo_unitario` existe con el comentario *«Costo congelado al
momento, para valuar la pérdida»*, es **la única columna de costo del esquema
completo**, y ningún código la escribe jamás: un campo diseñado para valuar
pérdidas que nunca tuvo un costo que congelar. La misma huella que
`inventario.ajustar` antes de la §39.

Tres cosas que el sistema parecía poder hacer y no podía:

- el **laboratorio de la Fase 8 no tiene ni una métrica de margen**. No se
  olvidó: no había con qué calcularla;
- una merma de 300 piezas es «300 piezas», no una cantidad de dinero, así que no
  se puede comparar con nada ni priorizar;
- «¿cuánto dinero hay en el almacén?» no tenía respuesta.

### La fuga que lo obvio habría causado

Lo natural era `productos.costo_promedio`. Habría sido una fuga de datos del
negocio al teléfono de cada vendedor, y silenciosa.

`fn_registrar_cambio` (migración 0010) publica **`to_jsonb(NEW)` — la fila
completa** — en `change_log`, y el disparador de `productos` lo hace con
`ruta_id = NULL`, que significa *a todos los dispositivos*. Una columna de costo
ahí viajaría en el siguiente pull al SQLite de cada teléfono, y cualquier
vendedor vería el margen de cada producto de la empresa. En un aparato que se
pierde.

**Nadie lo habría notado revisando el diff:** la columna se agrega en un lugar y
el dato sale por otro, diecisiete migraciones más atrás. Es la misma clase de
defecto que la Fase 9 encontró en `change_log` con la lista de precios, y la
razón de que ahí la prueba recorra tabla por tabla esperando cero.

De ahí `producto_costos`: tabla aparte, **sin disparador de change_log**, con
una prueba que afirma que no lo tiene y otra que afirma que `productos` no tiene
columnas de costo. La separación no es organizativa — es la frontera entre lo
que el teléfono necesita (precio de venta) y lo que no debe salir de la oficina
(lo que nos cuesta).

### Promedio ponderado, y la elección se escribe

| Método | Por qué no, o por qué sí |
|---|---|
| Último costo | miente cada vez que el proveedor sube: revalúa de golpe todo lo viejo que sigue en el anaquel |
| PEPS por capas | el más exacto y el más caro: exige saber **en qué orden se consumieron las capas**, con el inventario en cinco camiones y ventas que llegan con horas de retraso |
| **Promedio ponderado** | una cifra por producto, sobrevive al consumo parcial sin rastrear nada, y es de las opciones que NIF C-4 permite |

La razón que decide es la segunda: con §0.3 —«tiempo real es el tiempo real de
lo que ha sincronizado»— el orden de consumo de las capas es una pregunta que
este sistema **no puede contestar con honestidad**. Un método de valuación que
depende de un dato que no se tiene produce cifras exactas y falsas.

El costo es **por producto y no por almacén**: un promedio por almacén
divergiría entre la bodega y cada camión, y entonces una carga —que no es una
compra— cambiaría el costo del producto solo por moverlo.

Se pondera contra las existencias de **todos los almacenes menos los de merma**:
un camión cargado trae inventario de la empresa aunque esté en la calle, y lo
que ya se mermó es pérdida, no inventario.

Y contra las unidades que había **antes** de la entrada. Ponderar contra la
existencia ya actualizada contaría las unidades que entran dos veces y el
promedio se quedaría a medio camino del costo nuevo: con 240@10 + 240@20 daría
16.67 en vez de 15.

### El centavo que rompía el pago

Es el defecto más caro que encontró la construcción, y aparece en la aritmética
más inocente:

```
10 cajas × $296.00        = $2,960.00   ← lo que dice la factura
296.00 / 24               = $12.3333…   ← el costo por pieza, inexacto
240 piezas × $12.3333     = $2,959.99   ← lo que daba el sistema
```

Un centavo. Y rompe la operación completa: la cuenta por pagar nace en
$2,959.99, alguien captura el pago de $2,960.00 que de verdad hizo, y el sistema
lo **rechaza por exceder el saldo** — con el `CHECK pago_no_excede_el_original`
esperando detrás.

Así que son dos verdades distintas y se guardan las dos:

| Campo | Qué es | Cómo se calcula |
|---|---|---|
| `importe` | lo que se le debe al proveedor | sobre lo **capturado**: bultos × costo por bulto |
| `costo_unitario` | con qué se valúa el inventario | por unidad base, para el promedio |

Es la misma regla del §2 —el importe redondea una sola vez y al final— aplicada
donde de verdad importa: **la multiplicación final es sobre las cajas que venían
en la factura, no sobre las piezas en que se convirtieron.**

La diferencia de un centavo entre «valor del inventario» y «lo que se pagó» no
desaparece y no debe: es inherente a cualquier costo por unidad, y en
contabilidad vive en una cuenta de redondeo. Lo que no puede diferir es la
cuenta por pagar contra la factura.

### Un costo nulo no es cero

`costo_unitario` nulo significa **«se valúa al promedio vigente»** y el promedio
no se mueve. Es obligatorio en una compra —ahí está la factura— y opcional en un
inventario inicial o un ajuste por conteo, donde nadie compró esas unidades.

Valuarlas al promedio es el tratamiento estándar de lo que aparece en un conteo.
Guardar un cero arrastraría el promedio a la baja con un costo que nadie pagó, y
es el error que un `COALESCE(costo, 0)` comete sin avisar. Y si no hay promedio
previo ni costo capturado, el producto **queda sin costo**: la pantalla de valor
de inventario lo cuenta aparte en vez de valuarlo en cero, que haría que el
total se viera bajo sin decir por qué.

### Un pago se aplica a una cuenta, sin FIFO

Y es deliberadamente **distinto de la cobranza**, donde el servidor aplica FIFO.

Un cobro del vendedor llega como «un abono» y hay que repartirlo: el cliente
paga lo que debe sin decir cuál factura. Un pago a proveedor es al revés — se
paga **la factura F-45821**, con su transferencia y su referencia. Inventar un
FIFO aquí repartiría un pago entre facturas que nadie quiso pagar, y después
nadie podría conciliar contra el estado de cuenta del proveedor.

### Dos permisos, porque son dos manos

`compras.administrar` para el catálogo y los costos; `compras.pagar` para
registrar un pago. Recibir mercancía y pagarla son actos distintos, y que la
misma persona pueda hacer las dos sin que nadie más lo vea es la receta de una
factura inventada. El supervisor tiene el primero —ya recibe mercancía, necesita
capturar su costo— y **solo gerencia tiene el segundo**.

Y aquí gerencia **sí escribe**, sin contradecir «gerencia es de solo lectura
sobre la operación» (§0): un proveedor y un costo de compra no son la operación
de la calle —no mueven inventario ni tocan una venta— son la relación comercial
de la empresa, que es precisamente lo que gerencia dirige.

### Lo que sigue faltando del módulo

**Las órdenes de compra.** Su valor es anticipar —«¿qué viene en camino?»— y
eso importa a una escala que este negocio todavía no tiene: con un puñado de
proveedores, quien ordena recuerda lo que ordenó. Además agrega frición a un
flujo que ya funciona sin ellas, porque la recepción no necesita una orden
previa. Cuando se construyan, la entrada las referenciará y lo que valdrá la
pena medir es **lo ordenado contra lo recibido**: el proveedor que entrega de
menos no se detecta de ninguna otra forma.

**Los anticipos.** Un pago mayor que el saldo se rechaza diciendo que eso es un
anticipo y que los anticipos no están construidos. Es mejor decirlo que
guardarlo mal.

**El CFDI y la integración contable** siguen siendo Fase 10 y no bloquean nada:
el RFC del proveedor ya se guarda, que es lo que hará falta el día que existan.

---

## 42. La regla del §2.3 estaba escrita y nada la imponía

`docs/ARQUITECTURA.md` §2.3, sobre el riesgo de restaurar un respaldo viejo del
teléfono, declara:

> **no se puede iniciar una carga nueva con operaciones pendientes del día
> anterior.**

`cargas.py` solo comprobaba que el vendedor no tuviera **ya** una carga del
**mismo** día (`uq_carga_vendedor_dia`). Nada revisaba la cola del equipo, ni la
cuarentena, ni la liquidación anterior. Una salvaguarda diseñada hasta la frase y
nunca construida: el cuarto caso de esta misma huella, después de
`inventario.ajustar` sin dueño, `merma_detalle.costo_unitario` sin quien lo
escriba, y `auditoria` sin una sola escritura.

### Qué pasa sin ella, y por qué nadie lo nota

El escenario no es exótico — es la primera semana de un piloto en una ruta con
mala señal:

1. El lunes el teléfono se queda con tres ventas sin subir.
2. El lunes se cierra la liquidación. `sync_completa` queda en `false` porque el
   equipo reportó cola: **eso el sistema ya lo registraba honestamente.**
3. El martes a las 6 am se carga el camión. La carga confirmada publica el delta
   y el teléfono reescribe sus existencias con el nuevo snapshot.
4. El teléfono sincroniza. Las tres ventas llegan con su `fecha_operativa` **del
   lunes**.
5. Los modelos de lectura recalculan días sucios (§0.3 funcionando como debe), la
   venta del lunes sube — y el `efectivo_esperado` de una liquidación **ya
   cerrada** se calculó antes de esas tres ventas.

El arqueo que alguien firmó el lunes deja de cuadrar con las ventas que el
sistema tiene del lunes. No es corrupción: cada dato es correcto por separado. Es
**un cierre firmado contra una cifra que después se movió**, y no hay nada en el
sistema que grite.

### Tres hechos, no una sospecha

El guardia reusa los mismos datos que el cierre de liquidación ya consultaba
(`_bloqueos_para_cerrar`), leídos antes de publicar el delta en vez de después:

| Bloqueo | De dónde sale |
|---|---|
| El equipo reporta cola pendiente | `dispositivos.cola_pendiente`, que el teléfono manda en cada push |
| Hay cuarentena sin atender | documentos que el servidor no pudo aplicar, del día anterior por definición |
| Una carga previa sin liquidación cerrada | `cargas` ⟕ `liquidaciones` |

El tercero **no está en el §2.3** y se escribe como lo que es: una consecuencia
que se sigue de él. Si el camión debe el conteo de un día previo, cargarlo hoy
mezcla las dos existencias y el retorno de ayer deja de ser calculable —
`cant_cargada` sería de ayer y lo contado físicamente tendría lo de hoy encima.

Ese tercer bloqueo tiene una trampa que esta implementación pisó y corrigió antes
de entregarse: el filtro se escribió primero como `estado = 'confirmada'`, y abrir
el arqueo mueve la carga a `'en_ruta'`. Es decir que la carga cuyo conteo **sí
empezó y nadie cerró** —la peor de las dos, porque ya hay un `efectivo_esperado`
calculado que las ventas faltantes van a desmentir— era justamente la que se
escapaba. El filtro correcto es `estado IN ('confirmada', 'en_ruta')`: al cerrar
el arqueo la carga pasa a `'liquidada'` y deja de aparecer sola, y hay una prueba
de cada lado —una liquidación abierta de ayer bloquea, una cerrada no— porque un
filtro de más apagaría la regla desde el segundo día de operación.

### Se bloquea al confirmar, no al crear el borrador

El borrador no mueve inventario ni publica delta: el disparador de la 0015 salta
explícitamente los borradores. Así que dejarlo existir no cuesta nada, y mientras
alguien captura quince renglones **el teléfono puede sincronizar en el patio y el
bloqueo desaparece solo**. Bloquear la creación detendría trabajo que muy seguido
se vuelve innecesario.

El borrador avisa de lo que va a impedir el confirmar: hay una prueba de que el
aviso aparece sin frenar la creación, y otra de que un equipo al día no bloquea
nada.

### Y se puede forzar, con su razón escrita

Mismo patrón que `confirmo_sincronizado` en el cierre: el servidor bloquea por
omisión y una persona puede pasar por encima dejando constancia.

No es debilidad, es la única forma de que la regla sobreviva al contacto con la
operación. **A las 6 am el camión tiene que salir**, y una regla que deja la ruta
en la bodega se desactiva a la semana — o se rodea con SQL, que es peor. Lo que
no puede pasar es que se fuerce en silencio.

La casilla **y** el motivo son obligatorios: la constancia es el texto, no el
clic.

### Dónde vive la razón, y por qué no en `cargas`

`cargas` **lleva disparador de change_log** y publica `to_jsonb(NEW)` —la fila
completa— al teléfono del vendedor cuando la carga se confirma. Una columna de
texto libre donde la oficina escribe *«el teléfono de Juan no prende»* viajaría
al SQLite de Juan. Es el mismo camino de fuga que la §41 evitó con el costo, en
otra tabla.

Quitar la columna del payload exigiría reproducir las 74 líneas del disparador
con un `CREATE OR REPLACE` en una migración nueva, y dos copias de esa lógica en
el repositorio es el defecto de «dos reglas iguales escritas dos veces» que ya se
pagó con `ROLES_DE_OFICINA`.

Así que la razón va a **`auditoria`**, que existe desde la migración 0001 con
exactamente la forma que hace falta —`entidad`, `entidad_id`, `accion`,
`usuario_id`, `motivo`, `datos_despues`—, no lleva disparador, y **hasta hoy
nadie la escribía.** Esta es su primera escritura, y va en la misma transacción
que el movimiento de inventario: si el commit se cae, no queda constancia de algo
que no pasó.

En `cargas` quedan dos campos que al teléfono no le estorban: un número
(`pendientes_al_confirmar`) y una bandera (`forzada`). Lo que no sale de la
oficina es el texto. Hay una prueba que lee el `payload` publicado y afirma que
la razón no está ahí.

Y lo que se guarda en la auditoría son **los bloqueos que había**, no solo que
hubo: dentro de un mes la pregunta no es «¿se forzó?» sino «¿a pesar de qué?».

### De paso: el cero que no era un dato

`liquidaciones.operaciones_pendientes` se escribía como `0` literal al cerrar.
`sync_completa` ya decía **si** el equipo estaba al día; esa columna debía decir
**cuánto** le faltaba, y en su lugar borraba el único número que contesta
«¿cuántas operaciones faltaban?» cuando alguien audita un sobrante meses después.

Ahora se escribe el conteo que reportaron los equipos, que ya se calculaba en
`_respaldo_de_sincronizacion` y se tiraba. La suma se calcula **una vez** y se
devuelve en las cinco salidas de esa función: si cada rama la calculara, la
próxima rama se olvidaría — y el campo que se olvida en una rama es el que acaba
guardando un cero que parece un dato.

---

## 43. El procedimiento de despliegue no levantaba, y nadie lo habría sabido hasta la oficina

`docs/ENTORNO-WINDOWS.md` §5 decía, en una línea:

> Ahí el despliegue es `cp .env.example .env`, rellenar, y `docker compose up -d`.

Seguido al pie de la letra, eso **no levanta** en una máquina limpia. Tres
defectos —el tercero apareció al revisar el arreglo de los dos primeros—, todos
invisibles en desarrollo porque en desarrollo nadie usa compose:

**1. `.env.example` no mencionaba `DSD_CLAVE_API`.** `docker-compose.yml` la exige
con `:?`, así que el primer comando del procedimiento falla nombrando una
variable que el archivo que te dicen copiar no tiene. Es el fallo benigno de los
dos: ruidoso e inmediato.

**2. Nada creaba los roles `dsd_api` ni `dsd_analitica`.** Este es el serio. `api`
se conecta con el primero y `analitica` con el segundo, y los dos roles existen
solo después de aplicar `db/ops/rol_api.sql` y `db/ops/rol_analitico.sql` — que el
despliegue nunca aplicaba. Los dos servicios se quedan reiniciándose con
«role "dsd_api" does not exist», con PostgreSQL arriba y las migraciones
aplicadas: el síntoma no apunta a lo que falta.

La misma huella de siempre, otra vez. El rol estaba diseñado, escrito, probado
(las pruebas de RLS aplican ese archivo tal cual) y documentado en tres lugares
—`SEGURIDAD-OPERATIVA.md`, `RESPALDOS.md`, el encabezado del propio SQL—, y el
camino que de verdad se iba a usar no lo ejecutaba.

### La decisión: un servicio, no un párrafo más

Se podía documentar el orden —«levanta postgres, corre las migraciones, aplica
los dos scripts, levanta el resto»— y habría sido correcto y frágil. Un
procedimiento manual de cuatro pasos se hace bien la primera vez, cuando se está
leyendo el documento, y mal la segunda.

Así que el orden lo impone compose: un servicio `roles` que depende de
`migraciones` y del que dependen `api` y `analitica`. `docker compose up -d`
vuelve a ser un comando.

Tres cosas de cómo quedó:

- **Corre en cada `up`, no una sola vez.** Los dos scripts son idempotentes a
  propósito, y repetirlos es lo que mantiene los permisos al día cuando una
  migración agrega tablas. De paso, **rotar una de las dos claves es cambiarla en
  `.env` y volver a levantar**, sin recordar ningún `psql`.
- **Monta `server/db/ops` de solo lectura y aplica esos archivos tal cual.** Las
  pruebas de RLS aplican los mismos (§35). Una copia dentro del compose
  permitiría que lo que se prueba y lo que se despliega se separaran sin que nada
  avisara.
- **Usa la imagen de PostgreSQL**, que ya trae `psql`, en vez de meter el cliente
  en la imagen de la aplicación por un servicio que corre tres segundos.

### El defecto que apareció al escribirlo

El `command` se escribió primero como cadena con `>`:

```yaml
command: >
  set -e;
  psql ... -f /ops/rol_api.sql;
  psql ... -f /ops/rol_analitico.sql
```

**Compose parte una cadena por espacios.** Con `entrypoint: ["/bin/bash", "-c"]`
eso llega al contenedor como `bash -c set -e`, que **sale con cero sin ejecutar un
solo `psql`**. El servicio puesto para evitar que `api` arranque contra un rol
inexistente habría reportado éxito y dejado pasar exactamente eso.

Lo delató `docker compose config`, que imprime el `command` ya interpretado —
`["set", "-e"]`—. La forma correcta es una lista de un elemento (`- |`), que
mantiene el script como un argumento. Hay una prueba de esa forma, porque el
fallo es silencioso y no se vuelve a ver a ojo.

### El tercer defecto: `openssl rand -base64`

`.env.example` decía `openssl rand -base64 24` para las tres claves de
PostgreSQL. Las tres se incrustan en una URL de conexión dentro de
`docker-compose.yml`, y base64 produce `/` y `+` **cerca de la mitad de las
veces**.

Lo que pasa entonces no se parece a lo que es:

```
OperationalError: failed to resolve host 'dsd_analitica':
    Servname not supported for ai_socktype
```

libpq parte mal una URI cuya contraseña trae `/` y acaba tomando el **nombre del
rol** por el nombre del servidor. Y lo que lo vuelve traicionero es la asimetría:
**SQLAlchemy sí la tolera**, así que la API arranca perfecta, `/salud` dice
`rls: true`, y solo el laboratorio analítico queda roto con un error que habla de
DNS. Quien despliegue tendría una posibilidad entre dos de toparse con eso y
ninguna pista de dónde mirar.

En hexadecimal el problema no existe, y es lo que `SEGURIDAD-OPERATIVA.md` ya
usaba para `clave_api` sin que la razón estuviera escrita en ninguna parte. Ahora
lo está, y hay dos pruebas: una exige `-hex` para esas tres claves, y la otra
comprueba que las tres sigan yendo dentro de una URL — para que si eso cambia,
alguien relea la exigencia en vez de arrastrarla.

Se encontró releyendo el propio diff, no operando: la sospecha era que `/`
rompería la URL de la API, y resultó estar equivocada ahí y acertada en el
laboratorio. Se comprobó conectando de verdad con una contraseña
`ab/cd+ef=gh` contra PostgreSQL, por las dos rutas.

### Y once pruebas, porque un documento se desactualiza solo

`tests/test_despliegue.py` lee `docker-compose.yml` y `.env.example` como texto y
afirma lo que el procedimiento necesita: que toda variable marcada `:?` esté en el
ejemplo; que el ejemplo no pida variables que nadie lee (con las excepciones
nombradas y justificadas); que **cualquier** servicio que se conecte con un rol
restringido dependa de `roles`; que `roles` vaya después de las migraciones, use
los archivos de `db/ops` y aborte al primer error; y la forma del `command`.

No comprueban por nombre de servicio sino por el hecho, así que un servicio nuevo
que use uno de esos roles rompe la prueba en vez de romper el despliegue.

Se lee el YAML como texto y no con `pyyaml` a propósito: `pyyaml` no es una
dependencia declarada de este proyecto —entra de rebote con `uvicorn[standard]`—
y una prueba del despliegue que dependa de un paquete que nadie pidió es otra
cosa que se puede romper sola.

### Dos cosas más que el procedimiento afirmaba y no eran ciertas

Salieron de releer lo que se acababa de escribir, no de operar:

- **`curl -s http://127.0.0.1:8000/salud` desde el servidor no funciona.** `api`
  **no publica puertos** a propósito: solo existe en la red interna de compose.
  Se pregunta desde dentro del contenedor, igual que hace su propio
  `healthcheck`, o desde fuera por el túnel. Un comando de verificación que falla
  por una razón ajena a lo que verifica es peor que no tenerlo: manda a buscar un
  problema inexistente.
- **`make piloto-listo` no se puede correr en el servidor como en desarrollo.**
  Necesita `psql` y la base, y la base tampoco publica puerto. Se corre desde
  dentro de la red con la imagen de PostgreSQL, que ya trae el cliente — y
  montando **el repositorio completo**, no solo `scripts/`: el script compara la
  migración aplicada contra la última de `server/db/alembic/versions/`, y con solo
  `scripts/` montado esa comparación daría vacío y reportaría un desfase
  inventado.

### Lo que esto no arregla

El `.env` del servidor sigue siendo un archivo con cuatro secretos en texto plano
en la mini PC. Un gestor de secretos es infraestructura que este negocio no tiene
y no va a tener pronto, así que lo que protege ese archivo es el disco y la puerta
de la oficina — y ahí hay un hueco que conviene nombrar en vez de dar por
cubierto: `SEGURIDAD-OPERATIVA.md` exige cifrado en el TELÉFONO y en los
RESPALDOS, y **no dice nada del disco de la mini PC**. Quien se lleve el equipo
—no es un escenario exótico en una oficina de distribución— se lleva el `.env`, la
base y los respaldos locales. Falta decidir y escribir eso; no lo arregla esta
sección y no se finge que sí.

> **Cerrado en la §46**: el disco va cifrado con LUKS y la llave sellada en el
> TPM, con la contraseña del BIOS y el arranque desde USB deshabilitado como la
> otra mitad. El `.env` sigue siendo texto plano con la máquina encendida —eso no
> cambia—, pero ya no viaja legible cuando el equipo sale del edificio.

---

## 44. El APK de producción se firmaba con la llave de depuración

`mobile/app/android/app/build.gradle.kts` traía, tal cual lo deja la plantilla de
Flutter:

```kotlin
release {
    // TODO: Add your own signing config for the release build.
    // Signing with the debug keys for now, so `flutter run --release` works.
    signingConfig = signingConfigs.getByName("debug")
}
```

Y eso **no falla**. Produce un APK de release instalable, que abre y funciona.
Por eso es la peor clase de defecto que tiene este repositorio: el daño no está
en el APK de hoy, está en el de dentro de tres meses.

### Lo que cuesta, y por qué se cobra en dinero

Android solo acepta actualizar una app instalada si el APK nuevo viene firmado
con **la misma llave**. La de depuración la genera Flutter sola en
`~/.android/debug.keystore`: es distinta en cada máquina y se regenera sin avisar.
Así que el día que se compile desde otra PC —o que se borre esa carpeta— el APK
nuevo no podrá actualizar al instalado. «App not installed», y el único camino es
desinstalar.

Desinstalar, en esta app, no es volver a empezar: **borra la base local del
vendedor**. Con ella se van las ventas, los cobros y las mermas que todavía no
hubiera subido — dinero que ocurrió en la calle y que ya no está en ninguna cifra
del sistema. Es el mismo bien que protegen §0.1 y el borrado remoto de la Fase 9,
perdido por la vía más tonta.

Y el momento en que se descubre es el peor posible: a media semana del piloto,
cuando haya que mandar una corrección.

### La decisión: que el build se detenga

Lo fácil era documentar el paso. Lo correcto es que no haya cómo saltárselo: la
firma de release sale de `android/key.properties` —que no se versiona— y **si no
está, el build de release se detiene con una excepción que explica por qué**.

Tres detalles de cómo quedó, cada uno por un fallo que habría tenido:

- **La comprobación va en `gradle.taskGraph.whenReady`, no al configurar.** Si
  saltara al configurar, `flutter run` y las pruebas dejarían de funcionar en
  cualquier máquina sin keystore — que es la mayoría, y está bien que lo sea. Se
  pregunta cuando ya se sabe QUÉ se va a construir.
- **Un valor vacío cuenta como ausente.** `key.properties.example` trae las dos
  contraseñas en blanco; una copia sin rellenar tiene que fallar aquí, nombrando
  los campos que faltan, y no doscientas líneas después con un error de keystore
  que no dice nada.
- **`rootProject.file` y no `file`.** El segundo resuelve lo relativo contra
  `android/app/`, que no es donde nadie esperaría apuntar un keystore.

Las cuatro ramas —debug sin keystore pasa; release sin keystore se detiene;
release con `key.properties` sin rellenar nombra los campos; release con keystore
de verdad pasa— se ejercitaron contra Gradle 8.14.3 de verdad, en un proyecto de
prueba armado con esta misma lógica, porque este contenedor no tiene el SDK de
Android y `flutter build apk` no puede correr aquí.

### El segundo agujero del mismo camino: un APK sin servidor

`DSD_BASE_URL` se fija al compilar y no en una pantalla de ajustes —un campo
editable es el camino para que un equipo robado mande la cartera a donde quiera
quien lo tenga—. Sin el define, el valor por omisión es `api.localhost`, que no
resuelve a ninguna parte.

Un `flutter build apk --release` a secas compila eso **sin una queja**. El APK se
instala bien, abre bien, y el login falla con un error de red. Y ahí está el
problema: «no hay internet» es lo que el vendedor va a reportar, porque es lo que
la pantalla de login le diría. Alguien pasaría la mañana revisando el túnel de
Cloudflare, el router y la señal del teléfono, buscando una falla que no está en
ninguno de los tres.

Dos capas:

- **`make apk` exige la dirección** y además que empiece con `https://`. Android
  prohíbe el tráfico sin TLS en release y el permiso para saltárselo vive **solo**
  en el manifiesto de debug, así que un APK de producción con `http://` compila
  bien y no puede conectarse a nada.
- **Y si alguien se salta el `make`**, la app no muestra el login: muestra una
  pantalla que dice que se compiló sin servidor, que no es falla de la señal, y el
  comando que lo arregla. Sin botones, porque desde el teléfono no hay nada que
  hacer y un botón que no sirve haría concluir que la app está rota.

Las dos partes de la guarda son `const` (`kReleaseMode && baseUrl == marcador`),
así que en depuración el compilador de Dart elimina la rama del árbol: `flutter
run` sin define sigue apuntando al marcador —que es lo correcto para el modo
demo— y las pruebas de widget no se enteran. Es el mismo doble cerrojo del modo
demo —los dos cerrojos que documenta `mobile/app/lib/src/demo.dart`—, usado aquí
para lo contrario: allá apaga un atajo en release, aquí enciende un aviso.

El marcador tiene **nombre propio** (`marcadorSinServidor`) y la guarda lo compara
contra esa constante, no contra una cadena escrita dos veces. Hay una prueba de
que `baseUrlPorOmision` sigue siendo igual al marcador cuando nadie pasa el
define: si alguien cambiara uno de los dos, la guarda dejaría de disparar y el
APK malo volvería a pasar en silencio.

### Una afirmación mía que estaba mal, corregida antes de entregarse

Escribí primero, en tres archivos, que Android «exige que el `versionCode` suba en
cada APK, y con el mismo número la instalación se rechaza». Lo segundo es falso:
con el **mismo** `versionCode`, `adb install -r` reinstala sin problema. Lo que
Android rechaza es un `versionCode` **menor** que el instalado.

La razón para subirlo sigue siendo buena, pero es otra, y conviene que esté dicha
bien porque es la que se usa para decidir: dos APK distintos con el mismo número
son **indistinguibles con el teléfono en la mano**, y «¿qué versión trae este
equipo?» deja de tener respuesta. En una flota de ocho teléfonos en la calle, eso
es lo que convierte un reporte de un vendedor en una adivinanza.

### Lo que `make apk` imprime, y por qué eso es parte del arreglo

`scripts/revisar_apk.sh` lee el APK terminado y dice tres cosas que no se ven
mirando el archivo: con qué llave está firmado —y **sale con error si es la de
depuración**, por si alguien construyó por otro camino—, qué `versionCode` trae, y
a qué servidor apunta.

Imprime además la **huella SHA-256 del certificado**, que es lo único con lo que se
puede comprobar que un APK nuevo va a poder actualizar a los que ya están en la
calle. Se apunta la primera vez y tiene que ser la misma para siempre.

Las cuatro ramas del script se probaron con un `apksigner` falso en el PATH y un
APK de relleno, porque tampoco hay build-tools de Android aquí.

### Lo que no puedo hacer yo, y por qué está bien así

**El keystore lo genera y lo guarda Bryan.** No es una limitación del entorno: es
que esa llave no debe existir en un contenedor efímero al que yo tengo acceso, ni
pasar por este repositorio. `.gitignore` cubre `key.properties`, `*.jks` y
`*.keystore`.

El procedimiento está escrito en `ENTORNO-WINDOWS.md` §4.2 con el paso que la
gente pospone puesto antes de firmar el primer APK: **respaldarlo**. Dos copias en
sitios distintos, la contraseña y el alias en papel con la frase del cifrado de
respaldos. `SEGURIDAD-OPERATIVA.md` lo suma a la tabla de rotación como el único
secreto que **nunca** se rota, y `PILOTO.md` lo agrega a la lista del día −1 como
el único punto que, si se hace mal, se cobra en dinero de la calle y no en tiempo.

---

## 45. El candado de dependencias que tres documentos daban por hecho

`docs/ARQUITECTURA.md` decía, en la tabla del stack:

> | Paquetes | **uv** con lockfile versionado | … el lockfile y Docker lo compensan. |

Y el `Dockerfile` del servidor, encima de la línea que instalaba:

```dockerfile
# uv: instalación determinista a partir del lockfile.
RUN uv pip install --system --no-cache .
```

**No había lockfile.** Los tres caminos de instalación —`make instalar`, el CI y
el Dockerfile— resolvían contra PyPI en cada corrida, contra los rangos `>=` de
`pyproject.toml`. El quinto caso de la misma huella de este repositorio, y el más
barato de arreglar —un comando—, lo que lo hace peor: estuvo escrito como hecho
durante nueve fases.

### Qué significaba en la práctica

- **El verde de ayer no decía nada del árbol de hoy.** Una dependencia que
  publicara una versión rota ponía rojo un commit que no había tocado nada, y
  habría costado una tarde entender que el problema no estaba en el diff.
- **Dos imágenes del mismo commit podían traer versiones distintas.** La de la
  mini PC de la oficina y la que se probó no eran necesariamente la misma.
- Y lo inverso, que es lo que de verdad importa en un sistema que maneja dinero:
  **no había forma de reproducir el entorno en el que una cifra salió mal.**

### Lo que se hizo

`server/uv.lock` versionado: 76 paquetes con versión exacta y 1 332 hashes. Los
hashes no son adorno — sin ellos el candado fija el número de versión pero no el
contenido, y un paquete re-subido con el mismo número pasaría igual.

Y los **tres** caminos lo usan, porque con que uno resuelva por su cuenta el
candado no sirve de nada: el que difiera será el de producción o el del CI, nunca
el que alguien está mirando.

| Camino | Antes | Ahora |
|---|---|---|
| `make instalar` | `uv pip install -e '.[dev,analitica]'` | `uv sync --locked --extra dev --extra analitica` |
| CI | lo mismo, resolviendo en cada corrida | el **mismo comando** que `make instalar` |
| `Dockerfile` | `uv pip install --system .` | `uv sync --locked --no-install-project` |

`--locked` es la parte que importa: **falla** si `uv.lock` no corresponde a
`pyproject.toml`, en vez de resolver por su cuenta y seguir. Un candado
desactualizado tiene que detener el build, no arreglarse solo en silencio.

Tres detalles del Dockerfile, cada uno por un fallo concreto:

- **`uv` pinneado a `0.8.17`, no `:latest`.** El instalador también es una
  dependencia: el resolvedor es lo que decide qué se instala, y con `:latest` dos
  builds del mismo commit pueden usar resolvedores distintos.
- **Sin `--extra`**, así que la imagen de la API no lleva pytest, ruff, streamlit
  ni pandas. Hay una prueba de eso, y hace falta: el `--extra` puede colarse en
  la línea de continuación del `RUN`.
- **`ENV PATH=/srv/.venv/bin:$PATH`.** `uv sync` deja el venv en `/srv/.venv`, y
  el `CMD` llama a `uvicorn` a secas mientras el servicio de migraciones llama a
  `alembic`. Sin el PATH se buscarían en el Python del sistema, donde ya no
  están — y eso falla al **arrancar** el contenedor, no al construirlo.

### El otro contenedor tenía la misma enfermedad

`analytics/requirements.txt` eran tres líneas con `>=`. El laboratorio analítico
corre en su propia imagen, así que arreglar solo el servidor habría dejado la
afirmación de ARQUITECTURA medio falsa otra vez.

Ahora `requirements.in` es lo que se escribe a mano y `requirements.txt` lo que
se instala, compilado con versiones exactas y hashes. El Dockerfile lo instala con
`--require-hashes`, que convierte en error cualquier línea sin hash: una
dependencia agregada a mano al archivo compilado detiene el build en vez de
instalarse sin verificar.

Y una prueba que no esperaba tener que escribir: **los cuatro paquetes que los dos
candados comparten tienen que coincidir** (`streamlit`, `pandas`, `psycopg`,
`numpy`). En local el laboratorio corre desde el venv del servidor; en producción,
desde su propia imagen. Si esas versiones se separan aparece el «en mi máquina
funciona» más caro de diagnosticar: la misma consulta devolviendo algo distinto
según dónde corra, con pandas de por medio.

### Actualizar un candado es un acto, no un efecto secundario

Son dos comandos y la diferencia importa:

- **`make candado`** aplica a los candados lo que cambió en `pyproject.toml` o en
  `requirements.in`, **sin mover las versiones ya fijadas**. Es lo que se corre al
  agregar una dependencia.
- **`make candado-subir`** sube todo a lo más nuevo que permiten los rangos. Es un
  acto deliberado, con tiempo para revisar el diff.

Los dos terminan diciendo qué sigue, en orden: mirar el diff, aplicarlo al venv,
correr las pruebas y el lint. Porque lo que sube de versión ahí es lo que va a
correr en el servidor de la oficina, y **un candado sin probar es peor que
ninguno** — da la impresión de que alguien verificó ese árbol.

Esta vez se hizo: el candado se generó, el venv se reconstruyó desde él (seis
paquetes cambiaron, entre ellos SQLAlchemy 2.1.1 → 2.1.3 y streamlit 1.64 → 1.65),
y las 972 pruebas y el lint se corrieron **contra lo que el candado pinea**, no
contra lo que había instalado de antes.

`make candado-revisar` es la otra mitad: falla si alguno de los dos candados no
corresponde a lo declarado. `uv lock --check` compara el del servidor contra
`pyproject.toml` sin resolver de nuevo, y el del laboratorio lo revisan las
pruebas, por estructura: que cada paquete del `.in` esté fijado en el compilado,
que el pin satisfaga su rango, y que la cabecera nombre ese `.in` como su origen.

### El primer diseño de esa revisión estaba mal, y es instructivo

La primera versión recompilaba `requirements.in` a un archivo temporal y lo
comparaba con el versionado. Pasó en verde, y habría sido una bomba de relojería:
**`uv pip compile` hacia un archivo nuevo no ve los pines viejos**, así que
resuelve a lo más reciente que permitan los rangos. El día que streamlit publicara
una versión, ese paso se habría puesto rojo en un commit que no tocó nada — que es
exactamente el problema que el candado viene a quitar. Lo habría puesto en CI, y
el síntoma habría llegado semanas después, sin relación visible con este cambio.

Se descubrió preguntándole al comando en vez de suponer: bajando un pin a mano y
recompilando, primero a otra ruta —resolvió a lo más nuevo— y luego sobre su
propio archivo, donde **sí** conservó el pin bajado. Esa asimetría es la que hace
que `make candado` sea estable y que comparar contra un temporal no lo sea.

Y de ahí salió también el `setup-uv` pinneado en CI: `setup-uv` sin versión
instala el uv más reciente, y el resolvedor es lo que decide qué se instala.
Dejarlo flotar es dejar flotar el candado por la puerta de atrás.

### De paso, una cuarta afirmación que tampoco era cierta

La misma tabla del stack listaba `testcontainers` entre las herramientas de
prueba. Nunca se usó: la base la levanta `make db` en local y el servicio de
PostgreSQL del workflow en CI. Se consideró en la Fase 0 y se descartó —agregaba
una capa para arrancar lo que esas dos vías ya arrancan—, pero el nombre se quedó
en la tabla. Ahora la tabla dice lo que hay.

### Y una de mis propias pruebas que no probaba nada, otra vez

`assert "uv pip install" not in _DOCKER_API` pasaba con el Dockerfile roto,
porque el comentario que explica el cambio **cita el comando viejo**. Es
exactamente el mismo fallo que la guarda de `https://` en §44: una prueba que lee
la prosa en vez del código.

Dos veces el mismo error en dos commits seguidos deja de ser un descuido y pasa a
ser un patrón, así que ahora hay un helper —`_sin_comentarios`— y la regla queda
escrita en su docstring: **toda afirmación negativa se lee contra las líneas que
se ejecutan**, nunca contra el archivo entero. El hermano `_comandos` además une
las continuaciones de línea, porque un `--extra dev` escrito debajo del `uv sync`
también se escapaba — y eso lo encontró la verificación por mutación, no la
lectura.

---

## 46. El disco del servidor: cifrarlo sin dejar la ruta esperando

> **Esta sección describe un servidor en la oficina, y el servidor se movió a un
> VPS un día después: ver §47.** El procedimiento de LUKS + TPM que sigue **ya no
> se aplica** —la §47 explica por qué un VPS no se cifra— y el guion que menciona
> se llama hoy `scripts/revisar_servidor.sh`. Se conserva tal cual porque el
> razonamiento vuelve a valer el día que haya un servidor propio, y porque las dos
> secciones juntas son el registro de cómo se decidió.

La §43 cerró el despliegue y dejó un hueco nombrado a propósito:

> `SEGURIDAD-OPERATIVA.md` exige cifrado en el TELÉFONO y en los RESPALDOS, y no
> dice nada del disco del servidor. Falta decidirlo y escribirlo.

Esto lo decide. En el disco de la mini PC quedan juntas tres cosas: la base
completa, el `.env` con cuatro secretos en texto plano y la copia local de los
respaldos (`~/respaldos-dsd`). Es **el único renglón del modelo de amenaza en el
que se pierde todo de golpe**: un teléfono robado trae la ruta de un vendedor, el
servidor trae la operación entera.

### Lo primero es qué protege, porque es lo que más se malentiende

**El cifrado de disco protege la máquina APAGADA.** Encendida —que es siempre— el
disco está abierto, porque el sistema lo necesita. Quien entre a la oficina con el
servidor prendido y consiga una cuenta con permisos lee todo, y el cifrado no
interviene.

Lo que impide es que alguien se lleve el equipo, o le saque el SSD, y lo lea en
otra parte. Que es el caso realista en una oficina de distribución: «se llevaron
la computadora», no «un atacante con tiempo quiso la cartera».

### La tensión que decide el diseño: el arranque

Un volumen LUKS pide su frase al arrancar. En un servidor sin pantalla ni teclado
y sin nadie en la oficina:

> Hay un apagón largo, el UPS se agota, el equipo se apaga. A las 6 de la mañana
> el vendedor sale a ruta y **el servidor sigue abajo**, esperando que alguien vaya
> a teclear una frase.

No es hipotético: es exactamente el escenario que el UPS existe para cubrir, y el
UPS solo cubre los cortes cortos. Así que cifrar el disco sin resolver esto cambia
un riesgo de probabilidad baja por una interrupción de operación de probabilidad
alta — y eso no es una mejora, es un intercambio malo disfrazado de buena práctica.

### La decisión: LUKS con la llave sellada en el TPM, más el BIOS cerrado

| Opción | ¿Protege el equipo apagado? | ¿Arranca solo? |
|---|---|---|
| Sin cifrar | No | Sí |
| LUKS + frase al arrancar | Sí, del todo | **No** |
| **LUKS + llave sellada en el TPM** | Sí si sacan el disco; no si arrancan el equipo | **Sí** |
| LUKS + TPM con PIN | Sí, del todo | No |

Se elige la tercera, y lo que la hace defendible es que **no va sola**. El hueco
que deja —arrancar el equipo robado— no se cierra en el disco sino en el firmware:
contraseña de BIOS y arranque desde USB deshabilitado.

La razón es concreta y vale escribirla porque es la parte que los tutoriales se
saltan: sellar contra **PCR 7** mide el *estado* del arranque seguro, no el binario
que arranca. Un Ubuntu en vivo firmado por Microsoft produce la misma medición, así
que el TPM entregaría la llave igual. Sellar también contra PCR 4 —que sí mide el
cargador y el kernel— lo cerraría, pero entonces **cada actualización de kernel
rompe el arranque automático**, que en un servidor desatendido es peor que el
problema.

De ahí que la respuesta sea el BIOS: sin arranque desde USB no hay live USB que
presentar. Y para saltarse la contraseña del BIOS hay que resetear el CMOS —
**que resetea el TPM, que borra la llave**. El disco queda cerrado. Las dos
medidas juntas funcionan; por separado, ninguna.

### La trampa que puede costar la operación completa

`systemd-cryptenroll --tpm2-device` **no reemplaza la frase: agrega una segunda
ranura de llave.** Un volumen bien armado tiene dos — la frase y el TPM.

Si alguien borra la ranura de la frase después de sellar el TPM, el sistema sigue
arrancando y nada avisa. Hasta el día en que una actualización de BIOS, un cambio
de tarjeta madre o una pila agotada resetean el TPM: **y entonces el disco no se
vuelve a abrir nunca**. No hay otra llave. Es la forma más silenciosa de perder la
operación completa, y sale de seguir un tutorial hasta el paso que dice
`--wipe-slot` creyendo que limpia algo.

Por eso `scripts/revisar_cifrado.sh` no cuenta ranuras: cuenta **ranuras menos
llaves automáticas**, que es el número de frases que una persona puede teclear. Si
da cero, es FALLA con el comando para arreglarlo.

Y ahí apareció un defecto del propio guion, releyéndolo contra el procedimiento que
yo mismo acababa de escribir: contaba solo los tokens `systemd-tpm2`, y el camino B
—`clevis`— deja un token `clevis`. En un equipo armado por el camino B eso habría
hecho **dos** cosas mal: decir que el disco no abre solo cuando sí abre, y —lo
grave— **contar la ranura del TPM como si fuera una frase**, reportando «hay camino
de vuelta» en el único caso en que no lo hay. El guion cuenta los dos tokens, y
están probadas las cuatro combinaciones: cada camino con frase, el camino B sin
frase, y la frase sola.

### Lo que no pude probar, y cómo está escrito por eso

Aquí no hay TPM, ni Ubuntu Server, ni `dm_mod` en el kernel. Lo verificado de
verdad:

- El guion contra un **volumen LUKS2 real** creado con `cryptsetup luksFormat` en
  un archivo de respaldo: la cuenta de ranuras es correcta y no confunde el
  `0: crypt` de *Data segments* con una ranura de llave.
- La detección de la cadena `lvm → crypt → part` contra la salida real de
  `lsblk -nsPo`, con un `lsblk` falso que reproduce lo que deja el instalador de
  Ubuntu. Se usa `-P` —pares `NAME="x" TYPE="y"`— y no columnas **porque en
  columnas lsblk dibuja el árbol y el nombre viene con glifos `└─` pegados
  delante**; de ahí salen los parseos que fallan solo en la máquina de alguien más.
- Las cuatro ramas del camino de recuperación, con los dos tipos de token:
  `systemd-tpm2` + frase, `clevis` + frase, `clevis` sin frase (FALLA), y la frase
  sola.

Lo que **no** se pudo ejercitar es el sellado en sí. Y como el initramfs de Ubuntu
LTS usa los scripts clásicos de `cryptsetup` y no siempre honra la opción
`tpm2-device=auto` —que es de `systemd-cryptsetup`—, el procedimiento da **dos
caminos**: `systemd-cryptenroll` primero y `clevis` como alternativa empaquetada
para Ubuntu, con la prueba que decide cuál hizo falta. Decirlo así vale más que
presentar uno solo como si estuviera comprobado: si no funciona, quien lo siga sabe
que el problema no es que lo hizo mal.

### Y la prueba que no la sustituye ningún guion

**Desenchufar el equipo.** Esperar un minuto. Volver a enchufarlo. Tiene que
levantar solo y `/salud` tiene que responder sin que nadie toque nada.

Es lo único que prueba que la ruta de mañana a las 6 no se va a quedar esperando, y
es la razón por la que el guion termina diciendo en voz alta que eso es justo lo que
él no puede comprobar. Un guion que revisara la configuración y callara esto daría
una confianza que no corresponde.

---

## 47. El servidor se mueve a un VPS

La §46 escribió el procedimiento de cifrado para una **mini PC en la oficina**, con
LUKS, TPM y contraseña de BIOS. Un día después el servidor cambió de lugar: va en
un VPS en la nube. Esto registra qué cambia y qué no, porque la mitad de la §46
deja de aplicar y conviene que se sepa por qué y no por omisión.

### Lo primero: el código no cambia

Vale decirlo porque no era obvio y porque determina el tamaño del cambio.

- La app apunta a un hostname fijado **al compilar** (`DSD_BASE_URL`), no a una IP
  de red local. Eso se decidió por seguridad —un campo editable es el camino para
  que un equipo robado mande la cartera a otra parte— y resulta que también hace al
  sistema indiferente a dónde viva el servidor.
- `deploy/Caddyfile` ya resolvía TLS solo contra Let's Encrypt. Con IP pública
  funciona tal cual; detrás del túnel también.
- Toda la sincronización es HTTPS. Nada asumía red local.

Cero líneas de dominio tocadas. Lo que cambió es infraestructura, documentación y
un defecto del compose.

### El defecto: el despliegue no arrancaba sin túnel

`docker-compose.yml` tenía el servicio del túnel como uno normal, con
`TUNNEL_TOKEN: ${DSD_TUNNEL_TOKEN:?falta el token del túnel}`.

Eso no era «el túnel no se levanta»: compose **falla al interpolar**, antes de
arrancar nada, pidiendo una variable que en un VPS no tiene sentido pedir. El
despliegue entero quedaba bloqueado por un servicio opcional.

Ahora va detrás de un perfil (`profiles: ["tunel"]`) y el token es opcional. El
token **no** puede llevar `:?` ni con el perfil puesto: compose interpola el
entorno de todos los servicios aunque no se vayan a levantar, así que exigirlo
volvería a romper el arranque sin túnel. Si alguien activa el perfil sin token,
cloudflared falla al instante y lo dice.

Se queda en el repositorio, no se borra: es lo que hace falta si el servidor vuelve
a la oficina, y ahí sí no hay IP fija ni puertos abiertos.

### Lo que el VPS se lleva

| | Por qué |
|---|---|
| **El UPS obligatorio** | Era la mitad del «asume que tú eres el SRE» de §1.4 |
| **«El disco de la mini PC muere» como riesgo #1** | Almacenamiento redundante del proveedor |
| **El cifrado del disco con LUKS + TPM** | Abajo, con su razonamiento |

### Por qué el disco NO va cifrado

Es una decisión y no un olvido, y se escribe porque es la pregunta que cualquiera
haría después de leer la §46.

El cifrado de disco protege la máquina **apagada**. En la oficina eso tenía un
sentido claro: alguien se lleva el equipo o le saca el SSD. En un VPS nadie se
lleva tu disco.

¿Y el proveedor? Puede leerlo, y **el cifrado tampoco lo evita**: la frase tiene
que entrar al arrancar, así que vive en la memoria de una máquina virtual que el
hipervisor controla. Cifrar el disco de un VPS protege de que alguien compre ese
disco usado dentro de diez años, no del operador.

Y el costo es real: la mayoría de los VPS no exponen TPM, así que LUKS significa
teclear la frase por la consola web del proveedor **en cada reinicio**, incluidos
los que el proveedor hace por mantenimiento del host, de noche y sin avisar. Eso
convierte un reinicio rutinario en una ruta que no sale a las 6 de la mañana.

**Un riesgo que el cifrado no cubre, a cambio de una interrupción que sí ocurre, no
es un buen cambio.** Lo que protege los datos fuera del servidor son los respaldos
cifrados, que ya existen y cubren el caso que importa: una copia que sale de la
máquina.

El procedimiento de LUKS + TPM no se borró de la historia: vive en el commit que lo
escribió, y recupera su sentido el día que haya un servidor propio.

### Lo que el VPS trae, y es la parte que sí es trabajo

**Una IP pública.** En la oficina, detrás del túnel, el servidor no tenía ni un
puerto abierto: nadie podía intentar nada contra él. Un VPS lo escanean todo el
día desde que existe. No es un ataque dirigido; es ruido de fondo de internet, y
basta una contraseña débil para que el ruido entre. En el modelo de amenaza ese
renglón pasó de no existir a **probabilidad alta**.

De ahí: SSH solo con llave, root sin acceso, ufw con 22/80/443 y nada más.

**Y la trampa que de verdad deja bases de datos públicas:** Docker escribe sus
**propias** reglas de iptables, por delante de las de ufw. Un `ports:` en compose
queda abierto a internet aunque ufw diga que ese puerto está cerrado, y
`ufw status` no lo menciona. El compose de este proyecto publica solo 80 y 443 a
propósito; el guion lo comprueba leyendo el compose —la fuente de la verdad, con el
stack arriba o abajo— y hay una prueba que se pone roja si alguien publica otro
puerto.

**Perder la cuenta del proveedor** es un modo de fallo que la oficina no tenía:
una suspensión por un cargo rechazado se lleva el servidor y los respaldos a la
vez. Por eso «fuera del edificio» pasa a ser **fuera del proveedor**: una copia en
el object storage del mismo proveedor no cuenta. Y los snapshots del proveedor no
son respaldos — un `DELETE` sin `WHERE` se replica al snapshot de esa noche.

**La hora en UTC por omisión.** Las imágenes de VPS vienen así, y en UTC−6 a partir
de las 18:00 locales `CURRENT_DATE` ya dice mañana: el tablero del día sale vacío
por la tarde y el arqueo compara el papel de un día contra las ventas de otro. Es
el defecto que encontró la Fase 7, y en un VPS **empieza activado**. El guion
compara la zona del servidor contra `DSD_ZONA`.

### Y una cosa empeora

**La oficina deja de poder operar sin internet.** Con el servidor en la oficina, un
enlace caído no impedía nada: los vendedores sincronizaban en la red local y la
oficina seguía capturando cargas y liquidando. Con un VPS, sin internet la oficina
no puede hacer nada.

El momento donde duele es específico: a las 6 de la mañana, cuando hay que capturar
y confirmar la carga para que el camión salga. Es el mismo escenario que el UPS
venía a cubrir, llegando por otra puerta.

Se mitiga con el **failover 4G** que §1.4 ya pedía y capturando la carga la noche
anterior —que además es mejor práctica—. Queda escrito en `DESPLIEGUE.md` §0 porque
es el tipo de cosa que no debe descubrirse el primer martes que se caiga el enlace.

### El guion cambió de nombre porque cambió de trabajo

`revisar_cifrado.sh` → `revisar_servidor.sh`, y `make cifrado-revisar` →
`make servidor-revisar`. El disco pasó de **FALLA** a nota informativa —es una
decisión tomada, no un defecto— y entraron las cuatro comprobaciones que un
servidor en internet necesita: cortafuegos, puertos publicados, SSH y hora.

Las ramas se probaron con binarios falsos en el PATH (`ufw`, `timedatectl`) y con
archivos de `sshd_config` de verdad, y la detección de puertos se comprobó
exponiendo `postgres` en el compose y viendo el guion ponerse rojo.

### Dos defectos que aparecieron al escribirlo

**El orden de `sshd_config` estaba invertido.** La primera versión leía el último
valor de la configuración de SSH. En OpenSSH **gana el primero obtenido**
(`sshd_config(5)`: «the first obtained value will be used»), y Ubuntu pone el
`Include /etc/ssh/sshd_config.d/*.conf` **arriba** del archivo — así que los
archivos de `.d/` ganan. El guion reportaba **lo contrario de la verdad** con un
drop-in que endurecía sobre un `sshd_config` permisivo, que es justo la forma en
que se endurece un Ubuntu. Lo delató probarlo con los dos archivos en desacuerdo,
no leerlo.

**Y la prueba del cortafuegos no probaba nada.** `assert "ufw" in texto` pasaba con
`command -v ufw` cambiado por otra cosa, porque los comentarios y los mensajes de
ayuda del guion también dicen `ufw`. Es la tercera vez en esta serie que una
afirmación negativa o de presencia lee la prosa en vez del código — y van tres
veces porque las tres salieron de la verificación por mutación y ninguna de la
lectura. Ahora las agujas son invocaciones (`command -v ufw`,
`valor_ssh PasswordAuthentication`, la resta `RANURAS - AUTO`) y se buscan contra
las líneas que se ejecutan.

### Lo que sigue sin probarse aquí

Ni VPS, ni `ufw`, ni `sshd`, ni demonio de Docker. El procedimiento está escrito
contra documentación y la lógica del guion está verificada con dobles, pero
**`docker compose up -d` en un VPS real sigue siendo la primera ejecución de
verdad** — igual que antes, con una diferencia a favor: el defecto del token del
túnel habría detenido ese primer intento en seco, y ya no está.

---

## 48. Un vendedor no podía entrar a la app, y el modo demo lo tapaba

Al escribir el guion del simulacro end-to-end apareció el hueco más grande de este
repositorio: **ningún vendedor podía iniciar sesión en un binario de producción.**

La cadena se cerraba sobre sí misma:

| Para | Hace falta | Dónde |
|---|---|---|
| Iniciar sesión como vendedor | `dispositivo_id` de un equipo registrado y suyo | `api/v1/auth.py` — rechaza con 400 si falta |
| Registrar el equipo | Un token válido, y lo crea a nombre de **quien llama** | `api/v1/dispositivos.py` |
| El token | El login de arriba | ↑ |

Y no había salida por los lados: ni el CLI ni el panel registraban un equipo a
nombre de un vendedor, y en la app un `grep dispositivoId` sobre todo
`mobile/app/lib` no devolvía **nada**.

### Cinco piezas escritas, ninguna conectada

Es la misma huella de las §42 a §47, en su versión más completa:

| Pieza | Servidor | Cliente Dart | App |
|---|---|---|---|
| Registrar el equipo | ✅ | ❌ nadie llamaba | ❌ |
| Login con `dispositivo_id` → `credencial_local` | ✅ | ✅ aceptaba el parámetro | ❌ no lo pasaba |
| Guardar la credencial local | — | ✅ `RepoCredencial.guardar` | ❌ solo el demo |
| Pedir rangos de folio | ✅ idempotente | ❌ nadie llamaba | ❌ |
| Guardar los rangos | — | ✅ `RepoFolios.guardar` | ❌ nadie llamaba |

**Lo que lo tapó durante nueve fases fue el modo demo.** Su sembrador escribe la
credencial, el `dispositivo_id` y un rango de folios directo en el SQLite del
teléfono — las cinco piezas de golpe. Y como los dos cerrojos de compilación lo
eliminan del binario de release, en producción el camino simplemente no existía.
Todo el desarrollo de la app se hizo entrando por ahí.

El propio docstring de `panel/equipos.py` lo decía sin cerrarlo: «Registrar y
revocar un equipo solo se podía hacer por la API, con `curl`». La pantalla añadió
suspender, revocar y borrar. Registrar, no.

### La decisión: lo vincula la oficina, no el teléfono

Se consideró dejar que el teléfono se registrara solo en su primer login, que es
más simple. Se descartó: exigiría relajar la regla del servidor que rechaza a un
vendedor sin dispositivo, y entonces **cualquiera con las credenciales de un
vendedor podría enrolar su propio aparato**. Los equipos son de la empresa y quién
usa cuál es una decisión de la oficina — es el mismo principio del modelo de
amenaza, donde el caso frecuente no es el robo sino el vendedor que se va.

Así que `POST /panel/equipos/registrar`: la oficina elige vendedor y etiqueta, el
servidor genera el id y la pantalla lo muestra para teclearlo **una vez** en el
teléfono. El id lo genera el servidor y no el dispositivo —al contrario que en la
API— porque aquí todavía no hay dispositivo: el teléfono no existe en el sistema
hasta que alguien lo vincula.

### El orden del guardado, que es la única decisión delicada

`vincularEquipo` guarda la credencial y el `dispositivo_id` **antes** de pedir los
folios. Si la red se corta en medio, el vendedor queda vinculado y puede entrar con
su PIN —le faltarán folios y la pantalla de cobro lo dirá— en vez de quedar fuera
de la app con la credencial a medio camino.

La asimetría es la razón: **pedir folios se reintenta con señal; recuperar una
credencial perdida, no.** Hay una prueba de que un fallo al traer folios deja el
equipo vinculado, y otra de que un login sin credencial no deja nada escrito.

Los folios se piden y no se asignan desde el panel porque tienen que acabar en el
SQLite del teléfono, y asignarlos en el servidor no los pone ahí. El endpoint es
idempotente —devuelve el rango activo si ya hay uno— así que se puede pedir al
vincularse y otra vez cuando falten, sin quemar rangos.

### Y el simulacro, que es lo que lo encontró

`docs/SIMULACRO.md` es un ciclo completo de un día con **cifras prescritas**: tres
productos, una carga en cajas, dos ventas de contado, una a crédito, un cobro, una
merma, una devolución, y los siete puntos de control con el número exacto que tiene
que salir.

Las cifras no se inventaron: se calcularon con las fórmulas del propio sistema
—`esperado_en_camion` y `efectivo_esperado = contado + cobros en efectivo`— y se
verificaron contra el código que las implementa. De paso salieron dos correcciones
a mi propio borrador:

- Afirmé que el cierre solo pone el camión en cero. En ese momento **también
  devolvía el retorno a la bodega**, y lo comprobé leyendo el handler en vez de
  suponerlo. (Las dos cosas cambiaron en octubre de 2026 con el camión rodante: hoy
  el cierre no baja mercancía y deja el camión en lo contado. Ver §17 y la
  migración 0030; los puntos de control del simulacro están recalculados.)
- La cifra final de la bodega para un producto estaba mal: sumé la existencia
  inicial en vez de la que quedó después de la carga. 48 + 39 = 87, no 135.

Un guion de pruebas con un número equivocado es peor que ninguno: manda a buscar un
defecto que no existe.

---

## 49. Gerencia edita, y lo que edita llega al teléfono

**Decisión (dirección, octubre de 2026).** Gerencia deja de ser de solo lectura
sobre la operación. Puede cancelar y corregir ventas, ajustar el inventario de un
camión y editar o eliminar productos. **La regla que gobierna las tres: lo que
gerencia cambia en la web tiene que llegar al teléfono del vendedor.**

La migración 0009 decía lo contrario con todas sus palabras —«Gerencia es de SOLO
LECTURA sobre la operación. Monitorea, no opera»— y los tres permisos ya existían
en el catálogo sin estar concedidos, así que esto es un cambio de política y no de
modelo. `inventario.liquidar` NO se concede: cerrar el día firma un arqueo con el
nombre de quien lo cierra.

Conviene saber qué abarcan esos permisos, porque son más amplios que las tres
pantallas: `catalogo.administrar` cubre los datos del producto **y sus precios**, y
`inventario.ajustar` cubre también las entradas y salidas de bodega. Partirlos
fragmentaría el modelo de permisos para una distinción que nadie pidió.

### La regla de los deltas, que quedó explícita al construir esto

Tres mecanismos distintos aparecieron, y la diferencia entre ellos no es de estilo:

| Lo que viaja | Idempotencia | Dónde se usa |
|---|---|---|
| **El estado completo** | se compara contra lo local | la venta corregida o cancelada |
| **Una diferencia firmada** | se marca lo aplicado | la carga, el ajuste del cierre, el ajuste del camión |
| **Un borrado identificado** | el DELETE es idempotente solo | quitar un precio |

Dicho corto: **lo que viaja como diferencia se marca; lo que viaja como estado se
compara.** Un estado es más simple pero envejece —aplicar «el camión tiene 12»
cinco minutos tarde borra las ventas de esos cinco minutos—; una diferencia
sobrevive al retraso pero no se puede aplicar dos veces, y un `pull` se repite cada
vez que la red se corta a media tanda.

### Corregir un documento que ya está impreso

La remisión salió de la impresora del camión y el cliente la tiene en la mano. Nada
de lo que pase en el panel cambia ese papel, y de ahí salen los límites:

- **Las cantidades solo bajan.** El papel es el techo de lo que se entregó;
  entregar más es mercancía que salió sin documento, y eso es una venta nueva.
- **Los precios no se tocan**, por lo mismo que §7: el descuento después del hecho
  desde un escritorio es el mismo descuento.
- **La venta no se borra**, se cancela. El folio está impreso y
  `movimientos_inventario` es append-only.
- **Una corrección deja huella visible** (`corregida_en`, `corregida_por`,
  `correccion_motivo`), y la pantalla lo dice con esas palabras: «esta venta ya no
  coincide con su remisión impresa». Un sistema que permite corregir sin dejar
  huella no es más flexible, es menos auditable.

Y dos bloqueos que no se negocian: **un día ya liquidado no se toca** —alguien firmó
ese cierre contra un conteo físico— y **una venta a crédito con cobros aplicados
tampoco**, porque quedaría un pago aplicado a una factura que no existe.

### El ajuste del camión es una excepción a §0.2, y se escribió como tal

La migración 0025 prohibió las entradas directas a un camión porque «le cambiaría el
inventario bajo los pies a alguien que está vendiendo offline con otra cifra en el
teléfono». La objeción sigue siendo correcta, y lo que la responde es el delta: el
teléfono converge. Lo que queda es la ventana entre escribir y sincronizar, que es
la misma que tiene todo lo demás (§0.3).

El ajuste **no se le cobra al vendedor**, y eso ya lo resolvía la 0030: el saldo
inicial del cierre se deduce del saldo vivo del camión, así que absorbe las
correcciones de oficina. Al vendedor se le cobra la diferencia entre su conteo y lo
que el sistema tiene.

### Eliminar un producto son dos cosas distintas

**Dar de baja** (`activo = false`) es lo que se quiere casi siempre: desaparece del
catálogo del teléfono, su historia queda intacta, y es reversible. **Borrar de
verdad** solo tiene sentido para el producto que se capturó por error y nunca se
usó, y solo entonces se puede: si aparece en una venta, el servidor dice en qué
documento y manda a darlo de baja. No se ofrece cascada, porque la cascada aquí
significaría borrar ventas.

El SKU sí se edita, con su unicidad validada y su cambio asentado en `auditoria`. La
razón por la que no se editaba era operativa —«es con lo que la bodega lo
identifica»— y esa razón se vuelve en contra el día que está mal escrito. La unidad
base sigue sin editarse, y ésa sí es de integridad: cambiarla convertiría en piezas
lo que se contó en cajas sin tocar un solo número.

### Y el defecto que apareció al construirlo

Quitar un precio en el panel **nunca llegaba al teléfono**. El delta de `precio` se
acota por `producto_id` y el disparador genérico manda el DELETE con el payload en
NULL, así que el teléfono no podía saber cuál de los precios del producto se había
quitado y no hacía nada. La pantalla decía «el vendedor ya no la verá» y era falso:
seguía ofreciéndola, al precio retirado, y la venta entraba marcada con
`precio_desactualizado` — una marca imposible de entender, porque el precio que el
teléfono usó ya no existía en ninguna lista.

Lo arregla la migración 0033 con un disparador propio para `precios` que, al borrar,
emite los tres campos que identifican el renglón.

---

## 50. La devolución a la bodega pasa por tránsito, porque una palabra no mueve dos almacenes

Hasta la migración 0036, bajar mercancía de un camión a la bodega eran **dos ajustes
independientes**, uno en cada almacén. Las dos cifras acaban bien y no queda nada que
ate los dos lados: el día que alguien pregunte «¿quién bajó esas 18 cajas y quién las
recibió?», no hay qué leer.

Las tablas estaban desde la 0004 y nada las escribía. Y en §17, al documentar el
camión rodante, yo describí este traspaso como si existiera. No existía; queda dicho
aquí.

### Quién lo inicia

**El vendedor, desde su teléfono y sin señal.** La 0004 imaginó el sentido contrario
—bodega → camión, que la oficina propone y el vendedor acepta— y para ese sentido es
correcto: nadie le mete mercancía al camión de alguien sin su consentimiento (§0.2).

Para camión → bodega el dueño del origen es el vendedor. Es el único que sabe que
acaba de bajar 18 cajas, y exigirle conexión para registrarlo haría que lo apuntara
en papel, que es exactamente cómo se pierde un sistema. Mismo trato que una merma
(§10), misma razón.

### Por qué no llega derecho a la bodega

Si la declaración del vendedor subiera la bodega, **un faltante se podría cubrir
escribiendo una devolución que nunca se entregó**: su camión baja, la bodega sube, y
nadie contó nada. Sería la única operación del sistema donde la palabra de una
persona mueve dos almacenes.

Así que la declaración mueve **camión → TRÁNSITO**, el tipo de almacén que el esquema
preveía desde la 0004 y que nadie había usado. La bodega sube cuando alguien recibe y
dice cuánto contó. **Lo que no cuadre se queda en tránsito**, con nombre y con fecha:
una diferencia visible en vez de una confianza invisible.

El tránsito se crea solo, uno por sucursal, la primera vez que hace falta. Rechazar
el documento porque nadie configuró un almacén de paso convertiría una omisión de la
oficina en un faltante del vendedor.

### Recibir es contar, y contar es la única respuesta

La pantalla de recepción **no tiene botón de «aceptar»**. Lo que entra a la bodega es
lo contado, renglón por renglón, y `cantidad_recibida` se guarda **al lado** de lo
declarado sin sobrescribirlo: la diferencia entre las dos es el dato que este
documento existe para producir.

Contar **más** de lo declarado también entra: el vendedor bajó una caja que no anotó,
y eso es un hecho físico (§0.1), no un error de captura.

Y **no tiene botón de «rechazar»**, aunque el estado exista en la tabla desde la
0004. Si el vendedor declaró 18 cajas y no llegó ninguna, rechazar le devolvería 18
cajas a un camión que ya no las trae. La respuesta correcta es **contar cero**: el
documento se cierra, la mercancía se queda en tránsito, y hay un nombre y una fecha
de quien fue a contar.

### Lo que ve el vendedor

El estado y lo contado le llegan por un delta acotado a él (`entidad = 'traspaso'`).
Es su comprobante de que la mercancía dejó de ser su responsabilidad, y la razón por
la que no se entera en la liquidación de que contaron 16 de las 18 que dejó.

El delta viaja como **estado** y no como diferencia, y puede aplicarse mil veces
porque **no mueve ninguna existencia**. En particular: lo que la bodega no contó
**no regresa al camión**. No está ahí.

### La liquidación no necesitó un solo cambio

La ecuación del cierre no tiene término para «traspasado» y aun así cuadra, porque
`inicial` se **deduce** del saldo vivo del camión (§17): bajar las existencias baja
`inicial` y baja `esperado` en la misma cantidad. El cierre siempre compara lo
contado contra lo que el sistema tiene AHORA. Es el mismo mecanismo que absorbe los
ajustes de la oficina, y la razón por la que esta operación no toca
`liquidacion_detalle`.

### Dónde vive la pantalla

**Dentro de Entradas**, no en una sección nueva. Es la misma acción física —alguien
parado en la bodega con mercancía enfrente, contándola— y lo único distinto es que
viene de un camión y que el documento ya existe. Una devolución sin recibir se avisa
arriba y con número, igual que un borrador sin confirmar: es mercancía que está en la
bodega física y no en el inventario, y una carga hecha con esa cifra deja el almacén
en negativo.

### Y una corrección a lo que este documento decía de los permisos

El comentario de la pantalla de Entradas afirmaba que gerencia **no** tiene
`inventario.ajustar`, «porque quien mide no es quien ajusta» (§0). Dejó de ser verdad
con la migración 0031, que se lo concedió por decisión de la dirección: en un negocio
de una sola plaza, la gerencia ES quien corrige el descuadre. Hoy lo tienen
supervisor, gerente y admin, y la separación sigue viva donde más importa —el cierre
de la liquidación, que gerencia no puede firmar—. Si un día esa concentración
estorba, se revoca por persona en `usuarios_permisos` con `otorgado = false`, sin
tocar el rol.

### El sentido bodega → camión sigue sin construirse

A propósito. La bodega carga el camión con una **carga**, que es el documento
correcto para eso y el que el teléfono ya sabe recibir. Un traspaso bodega → camión
solo haría falta para una resurtida a media ruta, que nadie ha pedido.

---

## 51. El desempeño del día se compara contra el mismo día de la semana, y dice de quién falta información

**Decisión.** Una pantalla del panel, «Desempeño» (`/panel/desempeno`), pegada al
Tablero en la navegación, que contesta la pregunta de la junta de las once: **qué
lleva cada vendedor hoy, contra qué, y a quién hay que llamar.** Y la misma lectura en
el tablero del teléfono del gerente, para que las dos pantallas digan lo mismo.

Ya existían tres mitades del problema y ninguna entera: el Tablero del panel cuenta
lo que necesita atención pero es global; Efectividad abre en siete días a propósito,
porque un porcentaje sobre veinte visitas es ruido; y el tablero del teléfono tenía
las cifras del día y un ranking, sin referencia y sin la columna que convierte una
cifra en una llamada.

### Contra el mismo día de la semana, no contra ayer

La ruta visita a los mismos clientes cada martes. «Hoy vendiste menos que ayer» mide
qué clientes tocaban; «hoy vendiste menos que tus últimos martes» mide cómo
trabajaste. La referencia es el promedio de los **cuatro** mismos días anteriores:
con cuatro, un cambio de precios o la pérdida de un cliente grande ya se refleja; con
doce, la referencia se defiende de la realidad y el tablero dice «vas bien» tres
meses después de que dejó de ser cierto.

Tres reglas la hacen honesta, y las tres tienen prueba:

1. **Los días que no trabajó no entran al promedio.** Un martes de vacaciones en cero
   bajaría su referencia y el tablero diría que hoy va de maravilla. La línea la da el
   propio modelo: un día trabajado deja rastro aunque no haya venta —visitas,
   no-drops, cobros—, así que «sin ninguna de las tres» es «no trabajó».
2. **Un día trabajado y malo SÍ entra.** Si se excluyera por tener venta en cero, la
   referencia se defendería de los días malos y nunca habría una flecha roja.
3. **Con menos de dos días de historia no hay flecha.** Un promedio de uno es una
   anécdota, y poner una flecha roja enfrente de alguien por eso es inventarle un
   argumento.

Y un margen de **±10%** en el que hoy y la referencia «van parejo»: la venta de un día
depende de quién tenía dinero ese día, y un tablero que pinta de rojo un 6% abajo
enseña a ignorar el rojo.

La aritmética vive en un solo lugar —`domain/tablero.Referencia`— y la usan el panel y
el endpoint del teléfono. Si cada pantalla calculara la suya, el gerente vería dos
verdades según cuál abriera.

### «No sincronizó» no es «no vendió»

Es lo más importante de la pantalla, y es §0.3 llevado hasta su consecuencia: si las
cifras del día son un piso y no un total, lo que importa no es solo cuánto falta,
sino **de quién** falta.

El tablero del teléfono decía «2 equipos sin sincronizar», y con eso no se puede hacer
nada: no se sabe a quién llamar. Y el renglón de un vendedor en cero decía «sin
movimiento todavía hoy» en dos casos muy distintos —uno que sincronizó y no trae nada,
y uno cuyo día entero sigue en su teléfono—. En el segundo **era falso**: no es que
no haya vendido, es que no sabemos. Ahora cada renglón lleva el último envío de su
teléfono y lo que reportó tener en cola, y la pantalla separa los dos casos en dos
avisos con nombres, porque piden dos llamadas distintas.

«Sin sincronizar» solo puede ser verdad de **hoy**. Para un día cerrado, «no ha
enviado hoy» no dice nada del martes pasado.

### El silencio se señala a partir de las once

Un vendedor que sincronizó y no trae una sola operación se lista arriba —pero solo
después de las 11:00—. Antes de esa hora todos los renglones están en cero, y un aviso
que sale todos los días a las siete de la mañana enseña a ignorar los avisos. No es la
hora en que debería haber vendido: es la hora a partir de la cual el silencio ya no se
explica solo.

### Todo sale de `tablero_dia`

Ninguna consulta agrega sobre `ventas`: es la regla de la migración 0021. La pantalla
se recarga toda la mañana, y si barriera las tablas de operación, tres gerentes con
ella abierta harían lenta la sincronización de los camiones. El precio es que muestra
lo que el worker ya recalculó, y por eso la frescura va **arriba de la primera
cifra**. Un día sin ningún renglón se dice con esas palabras —«el worker no está
corriendo»— y no se pinta como un día sin ventas: el primero es una falla del
sistema, el segundo es información, y confundirlos haría que una falla del sistema se
leyera como un problema de la gente.

### Dos definiciones que no se reinventaron

- **Drop size** es venta entre visitas *con venta*, no entre documentos: la misma de
  `analitica.SQL_DROP_SIZE`. Dividir entre remisiones premiaría al vendedor que parte
  un pedido en dos, y el panel y el laboratorio dirían dos números con el mismo
  nombre.
- **Efectivo a entregar** es contado más cobros en efectivo: la misma cuenta que el
  arqueo de la liquidación, con una prueba que las compara. Si no coincidieran, la
  pantalla prometería un número y la liquidación cobraría otro.

### Lo que encontró la primera captura de pantalla, con dieciocho pruebas en verde

Vale la pena dejarlo escrito, porque es una clase de defecto y no un accidente. La
pantalla pasó todas sus pruebas y la primera vez que se MIRÓ con datos decía cuatro
cosas falsas. Todas las pruebas buscaban las palabras correctas; ninguna buscaba las
incorrectas.

1. **«3 por debajo de sus tuesdays».** `strftime('%A')` usa el locale del proceso, y
   en el contenedor es el C. Los días se nombran ahora desde una tabla propia.
2. **«Pedro: -100% por debajo de sus martes»**, siendo Pedro el que no había
   sincronizado. Era exactamente la mentira que la pantalla existe para no decir. De
   ahí la lectura `incompleta` en `Referencia.lectura`: la variación sigue siendo un
   número verdadero, pero es la de un piso, y no se pinta. Vive en el dominio porque
   las dos pantallas tienen que aplicarla igual, y una app anterior que no conozca la
   palabra la lee como «sin referencia» — no dibuja flecha, que es la degradación
   correcta.
3. **El total de hoy pintado de ámbar, «-67%»**, cuando lo que faltaba era el día de
   un vendedor. Mismo arreglo: con alguien sin sincronizar, el total se dice como
   piso.
4. **Lupe en dos avisos**: «sin una sola operación» y «-100% por debajo». Las dos son
   verdad y la segunda no agrega nada; cada persona aparece ahora en UNO.

Y uno que no era de esta pantalla: **la regla `.aviso-caja.aviso` nunca existió.**
Siete pantallas del panel usaban el aviso ámbar y salía como un párrafo más, sin
fondo — «un borrador no mueve inventario», «la mercancía está en tránsito»—. Las dos
clases sueltas sí tenían estilo, así que una revisión clase por clase no lo veía. La
prueba nueva (`test_panel_estilos.py`) revisa **combinaciones**, y tiene a su vez una
prueba que demuestra que habría atrapado este caso.

Cada una de las cuatro correcciones tiene su prueba, y cada prueba se verificó
rompiendo su corrección.

### Lo que no se hizo

**El panel en un teléfono.** A 390 px de ancho la navegación y las tablas se cortan
por la derecha — en todas las pantallas, no solo en ésta: `base.html` no se pensó
para eso. El panel es la herramienta de la oficina, y para el gerente en la calle
está el tablero de la app, que ahora dice lo mismo.

**«Visitas planeadas».** La tabla `clientes_frecuencia` existe desde la migración 0003
y **nada la llena**: ninguna pantalla captura qué clientes tocan qué día. Una cifra de
«18 de 25 visitas planeadas» tendría siempre cero en el denominador. Cuando exista la
captura de la frecuencia, ése es el siguiente número de esta pantalla — y hasta
entonces, no se dibuja.

---

## 52. El panel en cinco módulos, y «modo dios» sin pantallas nuevas

**Decisión (octubre 2026).** El menú del panel pasó de dieciocho enlaces planos a
**cinco módulos expandibles** en un lateral, y las tablas que ya existían ganaron
botones de Editar, Eliminar y Ajustar. No se creó ninguna pantalla nueva.

### Los módulos, y por qué ésos

Se agrupan por **quién** usa la pantalla y **cuándo**, que es la pregunta con la que
alguien llega al panel:

| Módulo | Pantallas |
|---|---|
| Hoy | Tablero, Desempeño |
| Operación de rutas | Cargas, Ventas, Cobranza, Corte del día |
| Catálogos | Clientes, Productos |
| Almacén | Inventario, Entradas, Salidas, Compras |
| Administración | Efectividad, Objetivos, Usuarios y rutas, Teléfonos, Cuarentena, Piloto |

Los módulos son `<details>` de HTML: se abren y se cierran sin JavaScript, que es la
regla de la plantilla base. El del módulo de la pantalla actual se abre solo.

**«Liquidación» se llama ahora «Corte del día».** Es lo que la oficina dice en voz
alta. La URL no cambió (`/panel/liquidaciones`) para no romper enlaces guardados. Una
prueba revisa que toda pantalla declare una sección que exista en el menú: un
renombre que olvide una pantalla la deja huérfana —sin enlace marcado ni módulo
abierto— y nada más lo avisaría.

### Los botones llevan al formulario, no actúan de un clic

Editar, Cancelar y Eliminar en una tabla llevan a la sección correspondiente del
detalle, donde está el motivo, la nota o la casilla de confirmación. Una cancelación
de un clic en una tabla de doscientos renglones es como se cancela la venta de al
lado.

### «Eliminar» funciona siempre, y decide qué es seguro

La dirección pidió que el botón simplemente funcione. Para clientes y productos:

- **Sin ningún documento** se borra de verdad. El disparador publica un `delete`, y el
  teléfono lo aplica como baja local sin tocar sus documentos.
- **Con historia** se **da de baja** (`estatus = 'baja'` / `activo = false`). Para
  quien usa el panel el efecto es el que buscaba —sale de las listas y de los
  teléfonos en el siguiente pull— y sus ventas siguen en pie, porque borrarlo de
  verdad obligaría a borrar esos papeles. Se reactiva editando sus datos.
- Un **cliente que todavía debe** no se elimina: ocultarlo del teléfono dejaría al
  vendedor sin poder cobrarle.
- Un **producto con existencia** no se elimina: ocultarlo del catálogo del teléfono
  dejaría al vendedor sin poder vender lo que trae en el camión.

Antes, eliminar un producto con ventas se negaba y mandaba a quitar la casilla
«activo» a mano: la misma operación, con un paso de más.

### Ajustar la bodega, y el defecto que habría corrompido diez camiones

El ajuste manual de inventario (§49, migración 0032) era solo para camiones. Ahora
también ajusta bodegas, con el mismo documento y folio propio (`AB-` en vez de
`AC-`). El documento servía tal cual; **el disparador no**:

```sql
SELECT responsable_id INTO v_responsable FROM almacenes WHERE id = NEW.almacen_id;
INSERT INTO change_log (..., vendedor_id, ...) VALUES (..., v_responsable, ...);
```

Una bodega no tiene responsable, así que `vendedor_id` quedaba nulo, y en el pull un
`vendedor_id` nulo significa «para todos». Cada teléfono de la empresa habría recibido
el ajuste de la bodega y lo habría **sumado a su camión**. La migración 0037 hace que
el disparador solo publique cuando el almacén es un camión con dueño. La defensa vive
en la base y no en la pantalla, para que se cumpla también si otro camino inserta un
ajuste. Hay una prueba que lo inserta a mano, y se verificó restaurando el
disparador viejo.

### Lo que viaja al teléfono, y cómo no rompe su SQLite

Ninguna de estas ediciones necesitó un tipo de delta nuevo. Viajan por los que ya
existían y que la auditoría de sincronización dejó blindados:

| Edición | Delta | Qué hace el teléfono |
|---|---|---|
| Corregir o cancelar una venta | `venta` | compara partidas y devuelve la diferencia al camión |
| Editar un cliente | `cliente` (upsert) | actualiza sus datos |
| Eliminar un cliente | `cliente` (delete o baja) | lo da de baja **sin borrar** sus ventas sin subir |
| Editar o dar de baja un producto | `producto` | lo actualiza o lo desactiva; nunca lo borra |
| Ajustar un camión | `ajuste_camion` | suma el delta una sola vez |
| Ajustar una bodega | — | nada: la bodega no está en la calle |

El teléfono nunca hace un `DELETE` local de algo que una venta sin subir pueda
referenciar: lo desactiva. Un `DELETE` contra la llave foránea de `venta_partidas`
abortaría la tanda y el teléfono no volvería a sincronizar (ver la auditoría, §2).

## 53. La carga se arma desde lo que hay en la bodega, varios productos a la vez

**Decisión (octubre 2026).** El detalle de una carga en borrador ya no pide el SKU de
uno en uno. Lista **cada producto con existencia en la bodega de origen**, con cuánto
hay, cuánto va ya en esta carga y una casilla para escribir cuántos bultos suben.
Un solo botón agrega todos los renglones que traigan cantidad
(`POST /panel/cargas/{id}/renglones`).

- **La lista sale de la bodega, no del catálogo.** Lo que se puede subir al camión es
  lo que hay en ese anaquel. Lo que está en cero o en negativo no aparece; para eso
  queda la captura por SKU, plegada debajo, que además es la única que captura lote.
- **La presentación por omisión es la más grande** (la caja), no la marcada
  `es_default`: ésa es la de vender, y el teléfono vende por pieza. Con la de vender,
  quien escribe «10» pensando en cajas subiría diez piezas.
- **Se guarda lo bueno y se nombra lo malo.** Un renglón con dedazo («2.5» cajas, una
  presentación que no existe) no tumba a los demás: se guardan y el mensaje dice cuál
  no entró y por qué. Todo o nada haría recapturar veinte cantidades por un error en
  una.
- **Cada renglón pasa por las mismas validaciones** que la captura de uno en uno:
  bultos enteros y conversión con `cantidad_base`, la misma función del teléfono.
- **Agregar dos veces suma.** El mismo producto sin lote se acumula en su renglón
  (`ON CONFLICT … cantidad + excluded.cantidad`), igual que en la captura por SKU.
- Un filtro por nombre, SKU o código de barras acota la tabla cuando la bodega es
  grande; la tabla muestra a lo más 500 productos.

No cambia nada de lo que viaja al teléfono: la carga sigue publicando su delta al
confirmarse, como en la Fase 2.

## 54. El dinero que falta cerrar: transferencias por confirmar, la cuenta del vendedor y el cambio físico

**Decisión (octubre 2026).** Tres fugas que el diagnóstico de la operación encontró
abiertas, cerradas en un módulo, con las reglas que la dirección dejó por escrito:

1. **Una transferencia sin confirmar NO libera crédito.** El saldo del cliente se
   restaura hasta que la oficina confirma que el dinero está en firme.
2. **La mercancía se le cobra al vendedor a costo**, no a precio de venta: se recupera
   la pérdida real del inventario, no se gana margen con el error del empleado.
3. **El cambio físico** (fresco por caducado o dañado) saca el fresco del camión
   legalmente, el malo cuenta como merma, y al vendedor no se le descuadra el arqueo
   ni se le exige un cobro.

### Transferencias y cheques: por confirmar (migración 0038)

Antes, un cobro por transferencia se aplicaba a las facturas en cuanto sincronizaba,
con la palabra del vendedor. La fuga era directa: cobrar $5,000 en efectivo,
capturarlos como «transferencia», y la caja cuadraba —la transferencia no entra al
arqueo— mientras el cliente quedaba pagado.

Ahora lo que no es efectivo nace **`por_confirmar`** y **no se aplica**: la deuda del
cliente sigue completa. La regla vive en tres lugares a la vez, y los tres tienen
prueba:

- **En la base**: `cobro_sin_aplicar_hasta_confirmar` impide que un cobro por
  confirmar o rechazado tenga un peso abonado. Ningún camino —ni un `UPDATE` a mano—
  libera crédito sin confirmar.
- **En el teléfono**: el crédito local resta los cobros encolados **en efectivo**; una
  transferencia encolada no libera línea.
- **En la vista de cartera**: `por_confirmar` viaja aparte del saldo, para que el
  vendedor vea «$800 pagado, por confirmar» y no le vuelva a cobrar.

La oficina tiene tres salidas, en Cobranza → Por confirmar:

| Qué pasó | Qué hace el sistema |
|---|---|
| El dinero está en el banco | **Confirmar** (varios a la vez): se aplica en FIFO, con la misma función que el efectivo |
| No llegó, o el cheque rebotó | **Rechazar** con motivo: la deuda sigue completa; si ya estaba confirmado, la aplicación **se revierte** y las facturas vuelven a deber |
| El cliente sí pagó, pero el dinero no llegó a la empresa | **Abonar al cliente y cargar al vendedor**: el cliente tiene su recibo y pagó de buena fe |

Confirmar va en bloque porque así se concilia un estado de cuenta; rechazar va uno
por uno porque es acusar. Lo hace gerencia (`cobranza.confirmar`): el supervisor vigila
la ruta, y que la misma mano dé por buena la transferencia de su vendedor es la
separación que esto existe para crear.

El recibo impreso de una transferencia ya no dice «se abona a tu cuenta»: dice que se
abona cuando la oficina confirme el depósito.

### La cuenta del vendedor (migración 0039)

El Corte del día calculaba el faltante con nombre y apellido, y ahí terminaba. Ahora
el cierre escribe los cargos en `cuenta_vendedor`, en la misma transacción:

| Cargo | De dónde sale | Cómo se valúa |
|---|---|---|
| Faltante de mercancía | lo contado abajo de lo esperado | **costo promedio** (`producto_costos`, §41) |
| Merma a su cargo | mermas del día con motivo `afecta_vendedor` | costo promedio |
| Faltante de efectivo | entregó menos de lo esperado | el importe |
| Cobro que no llegó | la oficina lo decide en Cobranza | el importe |

Tres decisiones que no son obvias:

- **Un producto sin costo capturado no se cobra en cero a escondidas.** Queda en el
  detalle con `costo: null`, y el mensaje del cierre lo nombra para que se capture el
  costo y, si corresponde, se cargue a mano.
- **El efectivo se carga solo si alguien contó.** `efectivo_entregado` nace en 0, y un
  Corte cerrado sin arqueo habría cargado el efectivo completo del día. Con
  `liquidaciones.arqueo_en` el cierre distingue «entregó $0» de «nadie contó», y lo
  dice. Y el cierre **recalcula** el esperado: un cobro en efectivo que sincronizó
  después del arqueo entra a la cuenta.
- **Un sobrante no se le abona.** Casi siempre es una venta sin sincronizar; no es
  dinero del vendedor.

La cuenta es un **libro**: la base no deja editar ni borrar (`fn_bloquear_mutacion`,
la misma del libro mayor). Un cargo equivocado se compensa con una condonación que
dice por qué. Baja con descuento de nómina, pago o condonación; un abono no puede
dejarle saldo a favor. Ver: supervisor y gerencia. Mover: gerencia. No lleva
`change_log`: trae costos, y el costo no sale de la oficina (§41).

### El cambio físico (migración 0040)

En la calle el cliente enseña un producto caducado y el vendedor se lo cambia por uno
fresco. Antes no había cómo registrarlo: no es venta (no hay dinero), no es devolución
(la devolución mete mercancía al camión y aquí sale), y sin registrar el fresco salía
sin documento y el Corte lo encontraba como faltante — que ahora se cobra a costo.

Es un tipo más de `mermas`, **`cambio`**, desde el menú de la visita («Cambio
físico»):

- **Sale del camión**: lo que se va es el fresco. El malo va al almacén de merma si la
  empresa tiene uno, igual que en una merma.
- **Exige cliente**: un cambio sin a quién se le cambió es exactamente cómo se
  escondería mercancía robada. Lo impone el teléfono, el manejador y un `CHECK`.
- **Nunca se le carga al vendedor**, aunque el motivo diga `afecta_vendedor`: el
  producto se echó a perder en la tienda del cliente, no en su camión. La cuenta del
  vendedor solo carga `tipo = 'merma'`.
- **En el Corte cuenta en la columna «merma»**: salió del camión con documento, y la
  ecuación no necesita saber por qué.
- **No toca el arqueo**: no hay dinero esperado.

La oficina los ve en Efectividad → Cambios físicos, por cliente y producto: la
pregunta que importa es a quién le estamos cambiando seguido, y qué.

Un cambio es **del mismo producto, uno por uno**. Cambiar una caja de atún por una de
sardina es otra operación —una venta y una devolución— porque los dos productos no
valen lo mismo, y meterla aquí escondería esa diferencia.

## 55. Editar y eliminar la estructura: usuarios, rutas, almacenes, listas, proveedores y motivos

**Decisión (octubre 2026).** La dirección pidió poder editar y eliminar todo lo que
el panel muestra. Las pantallas de clientes, productos, ventas e inventario ya lo
hacían (§49, §52); la estructura no: cambiar el nombre de una ruta o el responsable
de un almacén exigía SQL. Cada renglón de Equipo y de Proveedores lleva ahora a su
ficha, y los motivos tienen su pantalla (Efectividad → Editar los catálogos).

### La misma regla de §52, decidida por la base

Eliminar funciona siempre y decide qué es seguro: **se borra** si nada lo usa, **se
da de baja** si tiene historia, y **se niega** cuando borrarlo dejaría algo roto en
la calle. Quién contesta «¿tiene historia?» es la base y no una lista de tablas en el
código: el DELETE se intenta en un punto de guardado (`borrar_si_nadie_lo_usa`), y si
alguna llave foránea lo impide se deshace solo ese punto y se da de baja. Una lista
de tablas se desactualiza en silencio con la siguiente migración; la base conoce
todas sus llaves.

| Qué | Se niega cuando | Se da de baja cuando | Se borra cuando |
|---|---|---|---|
| Usuario | es uno mismo, o el último administrador | firmó cualquier documento | nunca hizo nada |
| Ruta | tiene clientes (se ofrece moverlos ahí mismo) | ya tuvo ventas o cargas | nunca operó |
| Almacén | tiene mercancía | tiene movimientos | vacío y sin movimientos |
| Lista de precios | es la de omisión, o la usan clientes (se ofrece moverlos) | ya se vendió con ella | sin ventas (con sus precios) |
| Proveedor | — | tiene compras (sus cuentas siguen vivas) | sin compras |
| Motivo | — | **siempre** | nunca |

### Lo que no se edita, y por qué

- **El código de un usuario**: es el prefijo del folio impreso; hay tickets en la
  calle con él.
- **El responsable de un camión con mercancía**: el teléfono del nuevo vendedor
  reinicia su inventario local al cambiar de camión (delta `identidad`) y vería el
  camión en cero. Se devuelve o se ajusta la mercancía primero.
- **El tipo de un almacén que ya se movió**: una bodega que se vuelve camión
  reescribiría la historia del libro mayor y del Corte.
- **Un motivo nunca se borra**: viaja a todos los teléfonos y el teléfono no sabe
  borrarlo, solo dejar de ofrecerlo; además cada merma guarda su código. Eliminar es
  desactivar. Y `afecta_vendedor` —que desde §54 mueve dinero— se edita aquí, detrás
  de `catalogo.administrar`: lo decide la oficina, nunca el vendedor al capturar.

### Lo que sigue sin borrarse, a propósito

Los documentos ya confirmados —cargas, entradas, salidas, Cortes cerrados, la cuenta
del vendedor— no tienen botón de eliminar: se corrigen con un documento que compensa
(un ajuste, un traspaso, una condonación), y los dos quedan a la vista. Un borrador sí
se cancela.

## 56. El plan de visita: qué clientes tocan cada día

**Decisión (octubre 2026).** `clientes_frecuencia` existía desde la migración 0003 y
ningún código la usaba. Sin plan, una visita perdida solo podía contarse si dejaba
papel —una venta o un no-drop—; el cliente al que nadie fue no dejaba rastro, y un
vendedor que se saltaba ocho tiendas de treinta salía con efectividad perfecta.

### Dónde se captura

- **Operación de rutas → Plan de visita**: la ruta entera en una pantalla, siete
  casillas por cliente, el orden de visita y la cuenta por día arriba —para que el
  lunes no tenga cuarenta tiendas y el martes ocho—, y un solo botón.
- **La ficha del cliente → Días de visita**: lo mismo para un cliente, más las
  semanas del mes. Lunes de la 1ª y 3ª semana es una ruta quincenal. Esos clientes
  aparecen en la pantalla de la ruta como personalizados y no se aplanan desde ahí.

Guardar **no reescribe** un día que sigue igual: conserva su `desde`. Si se borrara y
se volviera a insertar, cada guardado movería la fecha desde la que el plan cuenta.

### Cómo llega al teléfono

Sin entidad nueva en la sincronización. Un disparador **por sentencia** (con tabla de
transición) copia el plan a `clientes.plan_visita`, y ese UPDATE publica el delta del
cliente que el teléfono ya aplica. Por sentencia porque planear una ruta escribe
cientos de renglones, y por renglón cada uno publicaría un delta. Es una vez por
cliente y por guardado, y solo si su plan cambió.

### Qué ve el vendedor

La lista abre en **«Hoy · N»**: los que tocan hoy, en su orden, con una palomita en
los que ya visitó y «Visitados 7 de 15». «Todos» sigue a un toque, y al buscar se
busca en todos —el cliente que llama pidiendo mercancía no tiene por qué tocar hoy—.
Sin plan en ningún cliente, la pantalla es la de siempre.

### La regla, una vez en cada lado

`toca_visita()` en SQL y `tocaVisita()` en Dart, con las mismas pruebas de borde:
domingo es 0 (`extract(dow)`, y en Dart `weekday % 7`), y la semana del mes es
`(día − 1) / 7 + 1`, así que del 29 en adelante es la 5ª, que ningún plan «solo la
semana N» pide.

### Qué cuenta Efectividad

En «Cumplimiento del plan de visita»: lo que tocaba, lo que se hizo y **lo que tocaba
y nadie visitó**, por ruta, y los clientes que más se saltan.

- Cuenta como visita **cualquier papel** de ese cliente ese día: venta (aunque después
  se cancelara), no-drop, cobro o devolución. El vendedor estuvo ahí.
- **Solo días completos**: hoy no se le reclama a las once lo que va a visitar a las
  cinco.
- **Solo desde que el plan existe** (`clientes_frecuencia.desde`): capturar el plan
  hoy no convierte el mes pasado en un mes de visitas perdidas.

## 57. El panel listo para operar: la auditoría panel → teléfono y lo que se simplificó

**Decisión (octubre 2026).** Antes de salir a la calle se revisó el panel completo con
dos preguntas: ¿todo lo que la oficina cambia le llega al teléfono?, y ¿la oficina
sabe por dónde empezar? La primera encontró cinco huecos reales, todos cerrados con
pruebas que fallan sin el arreglo; la segunda dejó tres piezas nuevas y ninguna
pantalla quitada. El detalle de cada hallazgo está en
`docs/AUDITORIA-SINCRONIZACION.md` §8.

### Quién eres se lee de la base en cada petición, no del token

El token de acceso dura 30 minutos y traía las rutas, el camión, el rol y los
permisos. Con esa foto, la oficina le daba la ruta R04 a Pedro y durante media hora
su pull seguía filtrando con las rutas viejas: los deltas de R04 de ese rato quedaban
atrás del cursor que avanzaba y **no le llegaban nunca**. Con el camión, peor: las
ventas se descontaban del camión que ya manejaba otro.

Ahora el guardia de la API lee todo eso del usuario en la base, igual que ya lo hacía
la sesión del panel y que la revocación del teléfono. El token conserva quién eres
(`sub`) y para qué equipo; lo demás es una consulta por llave por petición, que es
más barato que cualquiera de los dos descuadres.

### Cuando una ruta cambia de manos, sus clientes viajan con ella (migración 0042)

Cambiar el titular escribía `usuarios_rutas` y el `change_log` no se enteraba. El
teléfono nuevo tenía el cursor más allá de los deltas de esos clientes —publicados
cuando se dieron de alta— y **recibía la ruta vacía**; el anterior se quedaba con
todos y le podía vender a cada uno, directo a cuarentena.

Un disparador en `usuarios_rutas` republica, al dar la ruta, sus clientes y su
cartera **solo al vendedor que la recibe** (`vendedor_id`), con `ruta_id` nulo a
propósito para que le llegue sin depender de nada más; y al quitarla, un `delete` de
cada cliente solo al que la pierde. El payload es el mismo de siempre: el teléfono no
distingue un delta republicado de uno ordinario.

### La cartera viaja con el cliente

Un cliente con deuda que cambiaba de ruta llegaba al teléfono nuevo **con saldo cero
y toda su línea libre**: el saldo vive en el delta de `cartera`, que solo se
publicaba al cambiar el crédito. Ahora también al cambiar la ruta o el estatus.

Esa misma función no era `SECURITY DEFINER` —la 0029 corrigió las demás y no la
alcanzó—, así que en producción, con RLS de verdad, **subirle el límite a un cliente
desde el panel daba 500**. Se corrigió, y `test_rls.py` revisa ahora en el catálogo de
PostgreSQL que toda función que escribe el `change_log` corra como su dueño: la
tercera no va a pasar.

### Quitarle el camión al vendedor también es un cambio

El teléfono ignoraba el delta de identidad con camión nulo («por si algún día el
servidor lo manda»). El panel ya lo manda: al pasar un camión a otro vendedor o darlo
de baja. El teléfono seguía ofreciendo la mercancía de un camión que manejaba otro.
Ahora el nulo se guarda como «sin camión» —una fila con valor nulo, que no es lo mismo
que no tener fila— y vacía el inventario local; la app ya sabía decir «este equipo no
tiene camión asignado».

### La guardia entre los dos lenguajes

`test_guardia_servidor_telefono.py` lee los disparadores instalados en PostgreSQL y el
`switch` de `_aplicarUno` en Dart, y falla si el servidor publica una entidad que el
teléfono no sabe aplicar (caería en `deltas_desconocidos` y el vendedor nunca vería
el cambio). En el otro sentido, lee los `OperacionLocal(tipo: …)` del código Dart y
falla si alguno no tiene manejador en el servidor (iría a cuarentena con la mercancía
ya entregada). Hoy cuadran 14 de 14 y 6 de 6; la prueba es para el día que no.

### Lo que se simplificó para operar

- **¿Listo para operar?** (Hoy → Arranque): once pasos en el orden en que se hacen
  —bodega, lista por omisión, productos con precio, existencia inicial, costo,
  vendedores con camión y ruta, teléfono vinculado, rutas con titular, clientes con
  ruta, plan de visita, motivos—, cada uno con lo que falta en números y el botón a la
  pantalla donde se arregla. Ocho son bloqueantes; el costo y el plan de visita son
  recomendaciones. Mientras falte un bloqueante, el tablero lo avisa arriba. Se queda
  en el menú: dar de alta un vendedor nuevo es arrancar otra vez, en chiquito.
- **Pendientes de hoy**, arriba del tablero: lo que espera a alguien, en el orden del
  día —antes de que salgan los camiones, durante el día, al cierre— y **solo lo que
  tiene algo**. Una lista que siempre muestra diez renglones en cero se deja de leer
  el día tres. Las cifras del día siguen abajo, como estaban.
- **El menú solo ofrece lo que la persona puede abrir.** `PERMISO_DEL_MENU` dice qué
  pide cada pantalla, y una prueba entra con cada rol y revisa que lo visible se abra y
  lo escondido responda 403. Con los roles de hoy, gerente y supervisor ven todo; la
  diferencia aparece con las excepciones por usuario.

No se quitó ninguna pantalla: cada una tiene un dueño y un momento en la operación
(la tabla está en `docs/AUDITORIA-SINCRONIZACION.md` §8.4). Lo que faltaba no eran
menos pantallas sino quién dijera por dónde empezar.

## 58. El cuadre del camión: el teléfono se pone igual al servidor cuando no hay nada en vuelo

**Decisión (octubre 2026).** Lo reportó la operación: el teléfono decía 1 Maruchan y el
panel 0; la oficina sumó 5 desde el panel y el teléfono pasó a 6.

Lo que la oficina le hace al camión —un ajuste, el corte del día— viaja al teléfono
como **diferencia** («súmale 5»), y tiene que ser así: un estado («tiene 5») que llega
cinco minutos tarde borra las ventas de esos cinco minutos. El precio es que una
diferencia **no cura nada**: si el teléfono y el servidor ya pensaban distinto, cada
diferencia posterior se suma encima del error. Ni el corte lo arreglaba, porque su
ajuste es «lo contado menos lo que tenía el SERVIDOR», aplicado sobre lo que tenía el
TELÉFONO.

Una causa concreta de que se desvíen, encontrada al revisarlo: un teléfono que se
reinstala o se vuelve a vincular rearma su camión desde el cursor 0 sumando todas las
cargas y ajustes de la historia, pero **no resta las ventas viejas** (el aplicador
ignora las ventas que no tiene). Hay otras —un documento rechazado, una versión vieja
de la app—, y no hace falta enumerarlas todas para cerrarlas.

### El cuadre

Al terminar cada sincronización, el teléfono pide `GET /v1/sync/camion`: las
existencias del camión según el servidor y el cursor del último cambio de ese
teléfono que la foto ya refleja, leídos **en una sola sentencia** (una sola foto de la
base). Y escribe esas existencias tal cual, en una transacción que primero comprueba:

1. **La cola está vacía**: todo lo que el vendedor hizo ya está en la foto.
2. **Su cursor es exactamente el de la foto**: todo lo que la foto trae ya se aplicó
   aquí, y nada de lo aplicado aquí le falta a la foto. Si la foto trae un ajuste que
   el teléfono no ha traído, sumarlo después lo contaría dos veces.
3. **El servidor no tiene operaciones de ese teléfono en cuarentena**: esa mercancía
   ya se entregó en la calle y el servidor todavía la cuenta en el camión.
4. **Es el mismo camión** que el teléfono tiene asignado.

Si algo no se cumple no se escribe nada: es «todavía no», y la siguiente
sincronización lo vuelve a intentar. Como las órdenes del equipo, el cuadre nunca tumba
una sincronización: un servidor sin el endpoint, un vendedor sin camión (409) o una
respuesta rara se ignoran, porque las ventas ya quedaron entregadas.

Las diferencias se quedan: siguen siendo lo correcto mientras hay operaciones en
vuelo. El cuadre es lo que garantiza que, en cuanto deja de haberlas, el teléfono y
el panel vuelven a decir lo mismo.

## 59. Reprocesar la cuarentena desde el panel

**Decisión (octubre 2026).** Lo reportó la operación: un teléfono con la app nueva
mandó algo que el servidor —todavía sin actualizar— rechazó. Se actualizó el
servidor, el vendedor tocó «reintentar», y la barra siguió roja.

No era un fallo de la actualización: el servidor **recuerda** que rechazó cada sobre y
a un reenvío le contesta lo mismo sin volver a aplicarlo. Es lo correcto para un
rechazo por los datos —reintentar a ciegas solo llenaría la cuarentena de copias— y un
callejón sin salida para uno por causa nuestra o ya corregida. El panel solo podía
«descartar».

**Reprocesar** (en el detalle de la operación en cuarentena) vuelve a aplicar el
payload guardado, íntegro, con los **mismos manejadores y el mismo candado por equipo**
que el push, y con el alcance y el camión que el vendedor tiene HOY. Si pasa:

- la operación queda `aceptada` en `sync_operaciones` y `reprocesada` en la cuarentena;
- el siguiente reintento del teléfono recibe «duplicada» —ya está del otro lado—, la
  saca de su cola y se le quita lo rojo, sin aplicarse dos veces.

Si no pasa, no se aplica nada y la operación se queda pendiente con el motivo nuevo.
Lo que llegó con un contenido que no coincide con su firma (`hash_no_coincide`) no se
reprocesa nunca: no hay forma de saber cuál de las dos versiones es la legítima.

**La que se marcó como atendida (octubre 2026, el mismo día).** Marcarla como
atendida en el panel no le decía nada al teléfono: a cada reintento el servidor seguía
contestando el rechazo original, y el vendedor veía «1 con error» por algo que la
oficina ya había resuelto. Ahora, si la oficina la marcó como atendida y no queda otra
pendiente, el reintento recibe «duplicada» —ya está del otro lado—, el teléfono la
saca de su cola y se le quita lo rojo. Atendida **no es aplicada**: no entra nada.

Y una descartada todavía se puede **reprocesar**: antes del botón, marcarla como
atendida era la única salida, y una venta real descartada así nunca habría entrado. La
pantalla advierte que no se reprocese si ya se capturó a mano.

## 60. Los folios nunca retroceden: ni al volver a vincular, ni con un teléfono nuevo

**Decisión (octubre 2026).** Reportado en operación: «hice una venta y dice que no se
guardó». Dos defectos de folios, uno de cada lado:

- **Al volver a vincular el teléfono**, el servidor devolvía su rango vigente con un
  `consumido_hasta` que nunca avanzaba con las ventas, y el teléfono lo escribía
  encima. La siguiente venta tomaba un folio que ya tenía otra venta de ese teléfono;
  la base local la rechazaba por duplicada y el vendedor veía «No se guardó la
  venta» —una y otra vez, porque el contador nunca avanzaba—.
- **El teléfono nuevo de un vendedor** empezaba sus folios en 1. El folio impreso es
  «VEND01-000123» y no dice de qué teléfono salió, así que repetía los del anterior y
  el servidor rechazaba cada venta por folio duplicado: a cuarentena.

Ahora: el teléfono nunca toma un folio por debajo del último que ya escribió en sus
documentos, y el mismo rango que vuelve a llegar no retrocede lo consumido; el
servidor calcula lo consumido desde los documentos que recibió, y el rango de un
teléfono nuevo empieza después del más alto de **todos** los teléfonos del vendedor.

## 61. El tablero por periodo, los movimientos de cada vendedor y la bitácora de sincronización

**Decisión (octubre 2026).** Tres pedidos de la dirección para operar con confianza.

### El periodo, uno solo para todo el panel

`app/api/admin/periodo.py`: hoy, ayer, esta semana, semana pasada, este mes, mes
pasado o un rango a mano. La semana empieza en **lunes**, como la ruta y el plan de
visita, y vive en un solo lugar para que «esta semana» quiera decir lo mismo en el
tablero, en vendedores y en sincronizaciones. Un periodo mal escrito cae en hoy, uno
al revés se endereza y uno de más de un año se recorta.

### El tablero

Las cifras de dinero —ventas, efectivo, cobrado, contado y crédito, visitas sin
venta, clientes nuevos— son del **periodo elegido**, con el desglose por vendedor
(que lleva a sus movimientos con el mismo periodo) y por día. Lo que se atiende
—pendientes, cuarentena, por confirmar, cartera vencida— sigue siendo de **ahora**:
no tiene sentido preguntar cuánta cuarentena había el mes pasado.

### Vendedores (Operación de rutas → Vendedores)

La lista con el resumen del periodo de cada uno, y la **línea de tiempo** de un
vendedor: ventas, cobros, mermas y cambios, visitas sin venta, clientes que dio de
alta, cargas, devoluciones a bodega, cortes, ajustes de su camión y su cuenta, en
orden de hora, cada renglón con enlace a su pantalla de siempre. No hay tablas
nuevas: es una consulta que lee lo que ya existe, así que no puede contradecir a
ninguna otra pantalla.

### Sincronizaciones (Administración → Sincronizaciones)

Arriba, cada teléfono **ahora**: «al día» —todo subido, todo bajado, nada en
cuarentena, contacto en el último día— o lo que le falta. Abajo, la bitácora del
periodo: cada subida con lo que el servidor aceptó, ya tenía o rechazó (y su
detalle, operación por operación, con enlace al documento o a la cuarentena), y cada
bajada con cuántos cambios y de qué tipo.

Las subidas ya quedaban registradas. Las bajadas no: la migración 0043 crea
`sync_bajadas`, un renglón por cada pull que entregó algo, y se poda junto con el
`change_log`. El pull además actualiza la hora de la última bajada aunque no haya
nada nuevo: antes un teléfono al día parecía uno apagado.

## 62. El día es el de Mazatlán, en el teléfono y en el servidor

**Decisión (octubre 2026).** La operación es en **Mazatlán** (`America/Mazatlan`, UTC−7
todo el año, una hora detrás de la Ciudad de México), aunque el sistema se administre
desde la CDMX. «Hoy» es el día de quien vende.

- **«Mi día» en el teléfono** calculaba el día en UTC mientras los documentos se
  estampaban con el día local: de la tarde en adelante buscaba las ventas de mañana, y
  una venta que sí llegó al servidor no aparecía en el corte del vendedor. Ahora usa
  `diaOperativoDe`, la misma función que estampa la venta. (versionCode 19)
- **El servidor** toma la zona de `DSD_ZONA`, que ahora vale `America/Mazatlan` por
  omisión, y la API se la pide a PostgreSQL **en cada conexión**: la zona de la base
  la fija `initdb` el día que se crea el volumen, y cambiar la variable después movía
  la hora de Python sin mover la de `CURRENT_DATE`.

## 63. Lo que el servidor confirmó queda como subido en el teléfono

**Decisión (octubre 2026).** Reportado en operación: «Mi día» decía «1 documento no ha
subido, sincroniza antes de entregar» de una venta que el panel ya mostraba. Al
confirmar un sobre, la cola lo sacaba pero **nadie marcaba el documento**: la venta
se quedaba con `sincronizada = 0` para siempre.

Ahora `Outbox.confirmar` marca, en la misma transacción, cada documento que declara
el sobre (uno de visita trae venta y cobro): ventas, cobros, mermas, no-drops,
devoluciones y altas de cliente. Y en cada sincronización se repasa todo lo
confirmado, así que lo que se confirmó con la versión anterior también se corrige
solo. (versionCode 20)

## 64. «Mi día»: se ve lo que se vendió, y se entera de la sincronización

**Decisión (octubre 2026).** Dos pedidos de la prueba en campo:

- **Al tocar una venta se despliega lo que se le vendió**: cantidad, presentación,
  producto e importe, en el orden del ticket. Es lo que el vendedor necesita para
  contestar «¿qué me dejaste el martes?» sin reimprimir.
- **La venta pasaba a «subida» solo al reiniciar la app.** El provider de «Mi día»
  se quedaba con su primera lectura. Ahora escucha la cola y el camión, que se
  avisan al vender, cobrar, mermar y al terminar cada sincronización. (versionCode 21)

## 65. La sesión de la app se recuerda doce horas, y el acceso se renueva solo

**Decisión (octubre 2026).** Dos quejas de campo con la misma raíz, que la sesión
vivía solo en memoria:

- **«Si cierro la app me pide entrar otra vez.»** Android cierra la app en cuanto
  el vendedor abre la cámara o el WhatsApp. Ahora, al entrar con la contraseña,
  se guarda en el Keystore que hay sesión y **hasta cuándo: doce horas**
  (`vigenciaDeLaSesion`). Al abrir la app se entra sola mientras no hayan pasado;
  después pide la contraseña. Es un plazo FIJO desde que se tecleó la contraseña,
  no se alarga con el uso: un teléfono olvidado en una tienda no debe quedar
  abierto para siempre. «Salir» lo borra al instante, igual que el borrado remoto.
  La sesión recordada no salta la vigencia de la credencial: vencida, pide red.
- **«Gerencia me saca a la media hora» / «el vendedor ya no puede subir».** El
  access token dura 30 minutos y la app nunca lo renovaba aunque tenía el refresh
  token (30 días) guardado. `TransporteRenovable` envuelve al transporte: ante un
  401 renueva con el refresh y repite la petición UNA vez —seguro, porque el
  servidor es idempotente—, con una sola renovación aunque choquen dos peticiones.
  Si no se puede renovar (refresh revocado, equipo dado de baja, sin señal), cada
  pantalla dice lo que ya decía.

Reabrir no toca la red: deja el token vacío («hay sesión, acceso por renovar») y
la primera petición lo renueva. Así la app abre al instante en la bodega sin
datos, y el tablero sin señal muestra su copia.

## 66. El tablero de gerencia se calcula al pedirlo si nadie lo ha calculado

**Decisión (octubre 2026).** La gerencia entraba al tablero en la app y leía «el
servidor no ha calculado el tablero todavía». Lo calculaba solo el worker, al
entrar una sincronización: recién desplegado, caído o sin ventas que lo
dispararan, nadie lo pedía nunca. Ahora `/v1/tablero` llama a `asegurar_fresco`:
si el cálculo nunca se hizo o tiene más de dos minutos, se hace ahí, con un
candado que no espera (si otro ya está calculando, se lee lo que haya). El worker
y la pantalla comparten ese candado, así que no borran e insertan los mismos días
a la vez. Una falla al calcular no tumba la pantalla. El panel queda como estaba.

## 67. Las cargas del camión también desde la app, solo para los puestos de arriba

**Decisión (octubre 2026).** «Las cargas quiero hacerlas también en la app, pero
solo para los administradores y puestos de arriba, no para los vendedores.»

- **Quién:** `inventario.cargar`. Lo tenían admin y supervisor; la migración 0044
  se lo da también al **gerente**. El vendedor sigue sin él: cargarse su propio
  camión sería firmar su propia entrega. La app solo muestra el botón (camión en
  la barra del tablero) a quien lo tiene; el servidor contesta 403 a los demás.
- **Mismas reglas que el panel, no una copia:** `/v1/cargas` importa del panel la
  confirmación (`mover_y_confirmar`: libro mayor, existencias y delta al teléfono
  del vendedor, en una transacción), los bloqueos del §2.3 —que se fuerzan solo
  escribiendo el motivo, que queda en `auditoria`—, y la captura en bultos
  enteros convertidos a unidad base. Confirmar dos veces no carga dos veces.
- **Flujo en el teléfono:** Cargas → Nueva carga (vendedor y bodega) → escribir
  cuántas cajas de cada producto que hay en la bodega → Agregar → Confirmar.

## 68. La versión instalada, al pie de la pantalla de entrada

**Decisión (octubre 2026).** Con el teléfono en la mano no había forma de saber si
el APK nuevo de verdad quedó instalado. Ahora la pantalla de entrada dice abajo
«Versión 0.1.0+22», y esa misma versión viaja en cada subida, así que el panel la
muestra en Sincronizaciones. (versionCode 22)

## 69. El portal de la oficina en la app: lo mismo que el dashboard

**Decisión (octubre 2026).** «Que el gerente pueda ver en la app lo mismo que en el
dashboard: por días, por periodos y por vendedor.» El portal de gerencia del
teléfono se reorganiza con una barra abajo, como el menú del panel. Cada pestaña
aparece solo con su permiso:

- **Día** (`tablero.ver`): el tablero de siempre, ahora con **calendario** para
  ver cualquier día del último año. El tablero ya sabía pedir una fecha; no había
  con qué elegirla.
- **Periodo** (`tablero.ver`): hoy, ayer, la semana, la pasada, el mes, el pasado
  o **fechas a mano**. Lo vendido (contado y crédito), cobrado, clientes, visitas
  sin venta y mermas; **por vendedor** (tocar uno abre su ficha en ese periodo) y
  **por día** (tocar uno abre el tablero de ese día). `/v1/tablero/periodo` usa
  `cifras_del_periodo`, la MISMA función del tablero del panel.
- **Empresa** (`tablero.ver`): ver §70.
- **Vendedores** (`ventas.ver_todas`): cada vendedor con lo que vendió y cobró en
  el periodo, lo que debe en su cuenta y cuándo habló su teléfono. Su ficha trae
  TODO lo que hizo en orden de hora —ventas, cobros, mermas, visitas, cargas,
  cortes, su cuenta—, filtrable por tipo; **cada venta se abre** con lo que se le
  vendió a la tienda; y **su camión** producto por producto, con los negativos
  marcados. `/v1/vendedores` importa las consultas de la pantalla Vendedores del
  panel: los dos no pueden decir cosas distintas. El vendedor no tiene
  `ventas.ver_todas`: no ve a los demás ni el camión de otro.
- **Cargas** (`inventario.cargar`): §67.

Las pestañas se construyen al abrirse por primera vez: abrir la app no hace cinco
consultas para mostrar una.

## 70. El resumen de la empresa, en el panel y en la app

**Decisión (octubre 2026).** «Tanto en el dashboard como en la app de gerente
quiero un resumen de la empresa: cuántos clientes, vendedores, artículos…». Una
sola consulta (`app/api/admin/empresa.py`) para la pantalla **Empresa** del panel
(menú Hoy, `/panel/empresa`) y la pestaña Empresa de la app
(`/v1/tablero/empresa`): clientes activos, prospectos, de baja y nuevos del mes;
cartera y vencido; vendedores (y cuántos con camión), usuarios de oficina, rutas,
teléfonos; artículos activos y sin precio; piezas en bodegas y en camiones, y
renglones en negativo; lo vendido en el mes y en el año. No incluye el valor del
inventario: el costo es dato reservado de compras.

## 71. El día de los datos, siempre a la vista

**Decisión (octubre 2026).** «Que en Mi día salga el día del que son los datos.»
`diaEnPalabras` y `encabezadoDelDia` (en `dsd_core`) dicen la fecha como la dice la
gente: «Hoy, jueves 24 de septiembre», «Ayer, …», o «Del lunes 21 al jueves 24 de
septiembre». Se muestran en Mi día del vendedor, en el tablero (que antes decía
«Hoy» aunque se viera otro día), en Periodo, en Vendedores y en las cargas.

## 72. «No me deja cargar»: una carga por vendedor por día, y se explica

**Decisión (octubre 2026).** Cada vendedor lleva UNA carga por día operativo
(`uq_carga_vendedor_dia`, desde la migración 0004): el corte se cuadra contra esa
carga. Abrir otra devuelve la que ya existe; si ya estaba confirmada, la app la
mostraba sin botones y sin una razón. Ahora lo dice, y dice qué hacer: un ajuste
del camión en el panel si hoy necesita más mercancía, o cargarla mañana. También
se explica cuando la bodega no tiene existencias, y cuando el servidor todavía no
tiene la función (una ruta que no existe es «actualiza el servidor», no «Not
Found»).

## 73. La marca: Distribuciones SE

**Decisión (octubre 2026).** La app lleva la marca de la empresa: el nombre
**Distribuciones SE** bajo el ícono, el rojo del bordado (`#E0282E`) en barras y
botones, y el logo —óvalo blanco, «Distribuciones» y «SE» en rojo manuscrito— en
la pantalla de entrada y como ícono de la app. La letra es Dancing Script (Google
Fonts, licencia OFL en `mobile/app/assets/fuentes/OFL.txt`), dentro del APK para
que se vea igual en cualquier teléfono. (versionCode 23)

## 74. «Mi día» deja revisar días anteriores

**Decisión (octubre 2026).** «Que al vendedor en Mi día lo deje cambiar de día para
revisar días anteriores.» Flechas de día anterior y siguiente (hasta hoy, nunca
mañana) y un calendario de los últimos 90 días. El teléfono no borra los
documentos de días pasados, así que el corte de ayer se arma igual que el de hoy y
sin señal. Mirando otro día, la pantalla lo dice («Estás revisando el …») y el
efectivo se llama «Efectivo de ese día», para que no se confunda con lo que hay que
entregar hoy. Salir y volver a entrar abre otra vez en hoy.

## 75. El corte del día desde la app

**Decisión (octubre 2026).** El corte se hace con el camión enfrente, en el patio:
la app de la oficina lo hace completo (pestaña Camiones → Corte del día). Abrir
el corte de una carga, contar lo que se queda arriba (en piezas; el campo vacío
vale cero), el arqueo del efectivo y cerrar.

- **Mismas reglas que el panel, no una copia:** la lógica del corte se separó de
  la pantalla del panel en `abrir_corte`, `guardar_conteo`, `guardar_arqueo`,
  `cerrar_corte` y `datos_del_corte` (`app/api/admin/liquidaciones.py`), y la usan
  el panel y `/v1/cortes`. El cierre desde el teléfono deja el camión en lo
  contado, carga a la cuenta del vendedor lo que falta y le avisa a su teléfono
  igual que el del panel.
- No se cierra con operaciones del vendedor sin subir; sin un dato de que su
  teléfono terminó, hay que marcar la confirmación —igual que en el panel—.
- **Quién:** `inventario.liquidar`. La migración 0045 se lo da al gerente, con la
  regla de la 0044: los puestos de arriba cargan y cortan; el vendedor, no.

## 76. Los clientes en la app de la oficina

**Decisión (octubre 2026).** Pestaña Clientes (`ventas.ver_todas`), por
`/v1/oficina/clientes` —no es `/v1/clientes`, que es la cartera de ruta que el
vendedor baja para vender sin señal—:

- La lista abre con lo que más urge cobrar arriba (vencido, luego saldo) y filtra
  por todos, con saldo, vencidos, bloqueados y prospectos; con búsqueda.
- La ficha: contacto y dirección, crédito (límite, plazo, disponible), lo que debe
  **nota por nota** con su vencimiento y días vencida, sus compras (cada una se
  abre con lo que se le vendió) y sus abonos. Las cifras salen de
  `v_cartera_cliente`, la misma vista que el panel y el teléfono del vendedor.
- Bloquear o desbloquear el crédito con motivo, con `clientes.administrar` como
  en el panel. No impide el contado.

## 77. Día y periodo en un solo Tablero; la barra de la oficina

**Decisión (octubre 2026).** «¿Las barras de día y periodo no se pueden
combinar?» Sí: una sola pestaña **Tablero** con los botones arriba —Hoy, Ayer,
Esta semana, la pasada, Este mes, el pasado, «Un día…» y «Fechas…»—. Un solo
día se ve con el tablero completo (avance del mes, cartera, por vendedor, mapa);
varios días, con el resumen del periodo por vendedor y por día; tocar un día del
desglose lo abre completo. La barra de abajo queda en cinco: **Tablero, Empresa,
Vendedores, Clientes y Camiones** (Cargas y Corte del día, que se hacen con el
camión enfrente). (versionCode 24)

## 78. Las entradas de mercancía desde la app

**Decisión (octubre 2026).** «Quiero agregar mercancía desde la app: manejar todo
el negocio en modo gerencia». La app de la oficina captura entradas completas
(Almacén → Entradas): compra a proveedor, inventario inicial y ajuste por conteo
físico, por `/v1/almacen/entradas`.

- **Mismas reglas que el panel, no una copia:** la lógica se separó de la
  pantalla en `abrir_entrada`, `agregar_renglon_a_entrada`,
  `quitar_renglon_de_entrada`, `confirmar_entrada`, `cancelar_entrada` y
  `datos_de_la_entrada` (`app/api/admin/entradas.py`); el panel y la app las
  llaman igual. Bultos enteros; la compra exige costo por bulto en cada renglón y,
  con proveedor del catálogo, deja su cuenta por pagar con el promedio ponderado
  recalculado; el inventario inicial exige nota; el producto con lote exige lote;
  confirmar dos veces no mete dos veces (409); cancelar pide motivo; a un camión
  no se recibe (§0.2).
- **Quién:** ver, `inventario.ver` y ser de la oficina (el vendedor tiene
  `inventario.ver` para su camión, no para todos los almacenes). Capturar,
  `inventario.ajustar` —el mismo permiso que en el panel: admin, supervisor y
  gerente—. Sin él, la lista se ve sin botón de «nueva».

## 79. Traspasos entre bodegas

**Decisión (octubre 2026).** «Quiero traspasar mercancía entre almacenes desde la
app». Antes, mover de una bodega a otra eran dos documentos sueltos sin nada que
los atara. Ahora es un **traspaso** (`app/infra/traspasos.py`,
`POST /v1/almacen/traspasos`):

- **Solo entre bodegas.** El camión tiene dueño exclusivo (§0.2): sube con una
  carga y baja con la devolución del vendedor; la oficina no le mueve el
  inventario por su cuenta. Un camión como origen o destino se rechaza diciendo
  el camino correcto.
- **En un paso:** nace `aceptado`, con los dos lados —asiento `traspaso` en el
  libro mayor y las dos existencias— en la misma transacción. No pasa por
  tránsito: la devolución del camión sí, porque la declara una persona y la recibe
  otra; aquí quien mueve es la oficina y lo que mueve es su propio inventario.
- **No deja el origen en negativo.** Es captura, no un hecho que llega tarde
  (§0.1): si no alcanza, dice cuánto hay y cuánto se pidió, y no se mueve nada.
  Las existencias del origen se bloquean antes de comparar.
- **No se publica a ningún teléfono:** el disparador de `traspasos` publica
  cuando cambia el estado, y este nunca cambia. Folio `TR-`.

## 80. La pestaña Almacén

**Decisión (octubre 2026).** La pestaña Camiones de la app de la oficina se
volvió **Almacén**, con todo lo que mueve mercancía: Existencias (cada bodega y
cada camión, en piezas y en cajas, con los negativos en rojo; la del camión
avisa que es un piso, §0.3), Entradas, Traspasos, Cargas y Corte del día. Cada
parte sale con su permiso; con una sola, se muestra directo. El aviso de «bodega
vacía» de las cargas ya manda a Almacén → Entradas en vez de al panel.
(versionCode 25)
