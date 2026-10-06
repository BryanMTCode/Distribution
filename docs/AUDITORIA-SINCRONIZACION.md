# Auditoría estructural de la sincronización · octubre 2026

**Por qué ahora.** La oficina acaba de ganar la capacidad de cambiar el estado de
las cosas —cancelar y corregir ventas, ajustar el inventario de un camión, editar
y eliminar productos—. Hasta esa semana el flujo era casi de un solo sentido: el
teléfono producía documentos y el servidor producía catálogo. Ahora los dos lados
escriben sobre lo mismo, y cada conexión que no esté cerrada se convierte en dos
números distintos para la misma cosa.

Esta auditoría recorre **toda** la superficie de sincronización, no las pantallas
nuevas: el push, el pull, el acotamiento, la idempotencia, el orden, la retención y
los modos de falla. Lo que sigue es lo que se encontró, con lo que se arregló y lo
que queda pendiente de una decisión tuya.

---

## 1. El inventario: qué viaja y quién lo aplica

Catorce fuentes publican al teléfono, y el teléfono sabe aplicar las catorce. **No
hay entidades huérfanas en ninguna de las dos direcciones**, que es lo primero que se
revisó. (Eran trece al escribir esto; la decimocuarta —`traspasos`— nació al cerrar
el §6.2, con el mismo criterio.)

| Tabla del servidor | Entidad del delta | El teléfono la aplica en | Cómo se vuelve idempotente |
|---|---|---|---|
| `productos` | `producto` | `productos` | upsert; el borrado **da de baja** |
| `producto_unidades` | `producto_unidad` | `producto_unidades` | upsert |
| `precios` | `precio` | `precios` | upsert; el borrado quita el renglón |
| `listas_precios` | `lista_precios` | `listas_precios` | upsert; el borrado **da de baja** |
| `clientes` | `cliente` | `clientes` | upsert; el borrado **da de baja** |
| `cuentas_por_cobrar` | `cartera` | `clientes.saldo_cache` | el saldo agregado, recalculado |
| `cargas` | `carga` | `existencias_camion` | **suma**, marcada en `cargas_aplicadas` |
| `ventas` | `venta` | `ventas`, `venta_partidas`, `existencias_camion` | **compara** contra lo local |
| `ajustes_camion` | `ajuste_camion` | `existencias_camion` | **suma**, marcada en `ajustes_camion_aplicados` |
| `usuarios` (solo el camión) | `identidad` | `sync_estado` | upsert; si el camión cambia, reinicia el inventario local |
| `motivos_merma` | `motivo_merma` | `motivos_merma` | upsert |
| `motivos_no_drop` | `motivo_no_drop` | `motivos_no_drop` | upsert |
| `traspasos` | `traspaso` | `traspasos`, `traspaso_detalle` | upsert; **no toca ninguna existencia** |
| `promociones` | `promocion` | — | se acepta y se descarta (ver §5.3) |

Y en el sentido contrario, los seis documentos que el teléfono produce tienen su
manejador en el servidor: `cliente.crear`, `venta.crear`, `cobro.crear`,
`merma.crear`, `no_drop.crear`, `traspaso.crear`. Ninguno sin par.

### La regla que gobierna la columna de la derecha

Apareció al construir las herramientas de edición y conviene tenerla escrita,
porque elegir mal el mecanismo no falla en la prueba: falla en la calle, una vez,
y no se puede reconstruir.

> **Lo que viaja como diferencia se marca; lo que viaja como estado se compara.**

Un **estado** («el camión tiene 12 piezas») es más simple y se puede aplicar mil
veces, pero **envejece**: si llega cinco minutos tarde, borra las ventas de esos
cinco minutos. Una **diferencia** («súmale −18») sobrevive al retraso pero no se
puede aplicar dos veces, y un `pull` se repite cada vez que la red se corta a media
tanda — así que exige recordar qué se aplicó ya.

---

## 2. Hallazgo 1 · El teléfono podía dejar de sincronizar para siempre

**Severidad: crítica. Arreglado.**

Dos decisiones correctas por separado esconden la peor falla posible de este
sistema:

1. La tanda de deltas se aplica **en una transacción todo-o-nada** — para que no
   entre un catálogo a medias, con productos sin sus precios.
2. El cursor **solo avanza después de aplicar** — para que un corte de luz entre el
   pull y la escritura no se salte un tramo.

Juntas: un delta que lanza una excepción deshace la tanda, el cursor no avanza, la
siguiente corrida trae **la misma tanda**, revienta en el mismo renglón, y el
teléfono queda congelado en el tiempo.

**Y no se nota.** Desde afuera se ve igual que un día sin cambios: la cola de salida
sigue vaciándose, la pantalla no dice nada, el catálogo simplemente no cambia. Un
vendedor podría pasar una semana vendiendo con los precios de la semana pasada.

### El caso real que lo disparaba

El delta de **baja de un cliente** hacía `DELETE FROM clientes`. Y `ventas`,
`cobros`, `no_drops` y `carrito_borrador` apuntan a `clientes` con llave foránea:
con una sola venta del día todavía sin subir, ese DELETE lanza.

Lo irónico es que los vecinos ya lo tenían resuelto y escrito. `producto` y
`lista_precios` **dan de baja en vez de borrar**, con esta razón textual: «un
producto retirado del catálogo puede seguir apareciendo en ventas ya hechas que aún
no sincronizan». A `cliente` no se le aplicó el mismo criterio.

### Qué se hizo

- **`cliente` ya no borra: da de baja** (`clientes.activo = 0`), igual que sus dos
  vecinos. Las ventas y cobros sin subir sobreviven y se suben normal.
- **Cada delta va en su propio SAVEPOINT.** El que revienta se deshace solo, se
  guarda en `deltas_desconocidos` **con su mensaje de error**, y la tanda sigue. Un
  catálogo al que le falta un renglón es malo; un teléfono congelado es peor, y
  además invisible.
- `ResultadoAplicacion` distingue ahora `desconocidos` (entidades que esta versión
  de la app no sabe aplicar: se arreglan actualizando) de `fallidos` (deltas que SÍ
  se reconocieron y reventaron: son defectos, y hay que ir a verlos).

El SAVEPOINT no es decoración: el delta de venta borra las partidas antes de
insertar las nuevas, así que sin él un fallo en la segunda mitad dejaría la venta
sin renglones dentro de la transacción que sí se confirma. Hay una prueba para eso.

---

## 3. Hallazgo 2 · El cliente que cambia de ruta nunca se iba del teléfono viejo

**Severidad: alta. Arreglado.**

Cada delta de cliente va acotado por `ruta_id`, y el pull entrega solo lo de las
rutas del vendedor. Funciona para todo menos para una operación rutinaria:
reasignar un cliente a otra ruta.

1. La oficina cambia `clientes.ruta_id` de A a B.
2. El disparador publica el delta con el registro **nuevo**: va acotado a B.
3. El teléfono de A no lo recibe nunca. Para él ese cliente sigue siendo suyo: lo
   tiene en su lista, lo visita, le vende.

Y la venta que le haga **entra sin protestar** —el cliente existe en el servidor—,
así que no hay ninguna señal hasta que alguien compara las dos rutas a mano.

El mismo hueco con otra cara: dar un cliente de baja (`estatus = 'inactivo'` o
`'baja'`) sí viajaba en el payload, pero **el teléfono no guardaba `estatus` ni lo
filtraba**, y su consulta de la ruta no filtraba nada en absoluto. Seguía mandando
al vendedor a la puerta de un cliente que la empresa ya había dado por perdido.

### Qué se hizo

- Disparador propio para `clientes` (migración 0034): cuando la ruta cambia publica
  **dos** deltas — el alta para la ruta nueva y una **baja para la vieja**.
- El teléfono guarda el estatus traducido a lo único que necesita saber: si va en la
  lista de hoy. `prospecto` **sí va** —es el alta de la calle sin confirmar, y
  esconderlo sería lo contrario de para qué existe—; `inactivo` y `baja` no. Un
  estatus que la app no conozca cuenta como activo: perder un cliente por un valor
  nuevo sería peor que mostrar uno de más, porque lo segundo se nota de inmediato.
- La lista de la ruta filtra `activo = 1`.

---

## 4. Hallazgo 3 · La poda prometida, y el hueco silencioso que habría abierto

**Severidad: media (hoy), crítica (el día que se podara). Arreglado.**

La migración 0007 dice, con estas palabras: «el change_log crece sin límite. Un job
lo poda conservando lo necesario para el dispositivo más atrasado», y creó la vista
`v_change_log_retencion` para calcular el corte.

**Ese job no existía.** La tabla crecía sin límite — una molestia de disco en un VPS
de 2 GB, no un error.

Lo que sí habría sido un error es escribirlo sin más: un dispositivo cuyo cursor
quedara por debajo de lo podado recibiría los deltas siguientes y **nunca sabría que
le faltan los de en medio**. Catálogo incompleto, en silencio, para siempre. Es la
misma clase de falla que el hallazgo 1, por otro camino.

### Qué se hizo, y en qué orden

Primero la defensa: `sync_retencion` guarda el cursor más bajo que el `change_log`
todavía conserva, y el pull lo compara contra el cursor del dispositivo. Si quedó
por debajo, responde `resincronizar: true` y el teléfono vuelve a empezar desde
cero. Es caro y es lo único correcto.

Después el job, `podar_change_log`, con dos cautelas:

- **Un margen** por debajo del dispositivo más atrasado: un teléfono puede haber
  pulido un tramo y no haberlo aplicado todavía, y su `ultimo_cursor_pull` ya
  avanzó.
- **Un piso de días** (30 por omisión): un dispositivo que se reactiva después de un
  mes todavía alcanza a ponerse al día, y un respaldo restaurado también.

El borrado y la escritura del piso van **en la misma transacción**. Si se separaran,
un commit a medias dejaría un tramo podado sin piso que lo delate — otra vez el
hueco invisible.

> El job **no está encolado por nadie todavía**. Se corre a mano o se le pone un
> `cron` cuando el tamaño de la tabla lo pida; mientras el piso valga 0, nada
> cambia. Ver §6.

---

## 5. Lo que se revisó y NO hace falta tocar

Vale tanto como la lista de arreglos: es lo que no hay que volver a mirar.

### 5.1 El pull no entrega transacciones en vuelo

El cursor es un `BIGSERIAL` y la secuencia avanza **al INSERT, no al COMMIT**. El
pull filtra `xid < pg_snapshot_xmin(pg_current_snapshot())`, así que nunca entrega el
cursor 120 mientras el 119 sigue en vuelo. Sin eso, el 119 quedaría por debajo de la
marca de agua del dispositivo para siempre. Está bien resuelto y probado.

### 5.2 Un cobro que llega para una venta que la oficina canceló

No rompe nada, y es un buen ejemplo de la arquitectura funcionando: el manejador
aplica FIFO sobre **las facturas abiertas que haya** en ese momento. Si la venta se
canceló, su cuenta por cobrar ya no existe, el cobro se aplica a la siguiente
factura del cliente o queda como saldo a favor, y se marca para revisión con
`sin_deuda`. §0.1 cumplido: el dinero entró, el sistema lo acepta y lo marca.

### 5.3 Las promociones se aceptan y se descartan

El servidor las publica y el aplicador contesta `true` sin hacer nada, a propósito,
para no llenar `deltas_desconocidos` con algo que sí se sabe que viene. **Consecuencia
real: una promoción que se capture en el panel no hace nada en la calle.** No es un
defecto de sincronización, es una función sin construir; queda anotado para que no
se descubra el día que alguien la capture.

### 5.4 La cola de salida no se bloquea con un sobre malo

Un sobre que el servidor rechaza va a `sync_cuarentena` del lado del servidor y
queda marcado del lado del teléfono, pero **la cola sigue avanzando**: un documento
malo no detiene a los veinte que vienen detrás. Y la barra de pendientes del
vendedor es tocable para reintentar la cuarentena.

### 5.5 El borrado remoto viaja aunque la corrida termine mal

Las órdenes de borrado se entregan incluso cuando la sincronización falla por red.
Es deliberado y está comentado: un equipo con orden de borrado tiene que quedar
bloqueado también cuando se cayó la red a media entrega.

---

## 6. Lo que queda, y por qué no lo toqué

Cuatro cosas cuando se escribió esto. Las dos primeras ya están cerradas —cada una
con su nota de qué se hizo y qué encontré de paso—; las dos últimas siguen abiertas
a propósito.

### 6.1 ~~La credencial solo se refresca con un login en línea~~ · CERRADO, y con una corrección a esta auditoría

**Lo que esta sección decía estaba mal en su parte más alarmante.** Decía que una
credencial vieja dejaría ventas «estampadas con el camión anterior, descontando del
inventario equivocado». No puede pasar: el manejador de sincronización toma el
almacén **del token, nunca del payload**, con este comentario al lado —«si el
dispositivo pudiera declarar a nombre de quién vende, un equipo comprometido
escribiría en la ruta de cualquier otro»—. El servidor ya se defendía solo, y lo
mismo con `vendedor_id`.

Lo encontré al ir a arreglarlo, leyendo el manejador en vez de suponerlo. Queda
fijado con una prueba que documenta de dónde sale ese dato, para que la próxima
auditoría no vuelva a sospecharlo.

**El hueco real era más chico y estaba en el teléfono.** `existencias_camion` no
tiene columna de almacén —es «mi camión», implícito— y nada la reinicia, **por
diseño**: el camión es un almacén rodante y su saldo se arrastra de un día al
siguiente (ADR 0002 §17). Reasignar el camión es el único evento que tiene que
reiniciarla; sin eso, el teléfono mezclaría el sobrante del camión viejo con las
cargas del nuevo y le ofrecería al cliente mercancía que está en otro vehículo.

Qué se hizo (migración 0035):

- Entidad de delta nueva, `identidad`, acotada al propio vendedor, con el camión
  que la oficina le tiene asignado.
- El payload se arma **a mano, campo por campo**, y no con `to_jsonb(NEW)`:
  `usuarios` guarda el `password_hash` y el `change_log` es una tabla que el
  dispositivo **descarga**. Un disparador genérico aquí habría mandado el hash de la
  contraseña de un empleado por la red, a quedarse en el SQLite de un teléfono. Hay
  una prueba que afirma que el payload tiene exactamente dos campos.
- Cuando el camión **cambia**, el teléfono reinicia su inventario local, sus marcas
  de cargas aplicadas y su carga activa. Sus documentos sin subir **no se tocan**:
  describen lo que pasó en la calle, y eso no cambia porque la oficina le haya
  cambiado el vehículo.
- Cuando llega el **mismo** camión —un `pull` repetido— no se vacía nada, y el
  primer delta que recibe un teléfono recién vinculado tampoco.

**Lo que deliberadamente NO viaja:** los permisos siguen en la credencial, acotados
por `valida_hasta` —mandarlos por delta permitiría que un teléfono offline *ganara*
permisos sin volver a autenticarse, que es al revés de lo que se quiere— y el
`codigo` del vendedor tampoco, porque es el prefijo del folio impreso y cambiarlo a
media ruta haría que dos rangos compartieran prefijo en papel.

> **Nota de severidad, para que el registro sea honesto:** hoy el camión se asigna
> solo al **crear** al vendedor y no hay pantalla para reasignarlo, así que el único
> camino era un UPDATE a mano contra la base. Era un hueco latente, no un defecto
> que estuviera ocurriendo. Se cerró ahora porque la lista de capacidades de edición
> para gerencia sigue creciendo y «reasignar el camión» es la clase de botón que se
> pide.

### 6.2 ~~El traspaso camión → bodega no existe~~ · CERRADO

Cuando escribí el cierre del camión rodante dije que la mercancía que el vendedor
sí entrega «es un traspaso camión → bodega, con su propio documento y su propia
aceptación». **Las tablas existían (`traspasos`, `traspaso_detalle`, migración 0004)
y la pantalla no.** Me adelanté al describirlo como disponible, y quedó anotado aquí
en vez de en una conversación: la única forma de bajar mercancía de un camión era un
ajuste en cada lado, que no deja un documento que ate los dos. El día que alguien
preguntara «¿quién bajó esas 18 cajas y quién las recibió?», no había qué leer.

Ya existe (migración 0036), y las tres decisiones de diseño valen más que el código:

**Lo inicia el vendedor, no la oficina.** La 0004 imaginó el sentido contrario
—bodega → camión, que la oficina propone y el vendedor acepta— y para ese sentido es
correcto: nadie le mete mercancía al camión de alguien sin su consentimiento. Para
camión → bodega el dueño del origen es el vendedor, así que él lo captura, desde su
teléfono y **sin señal**: es el único que sabe que acaba de bajar 18 cajas, y
exigirle conexión haría que lo apuntara en papel. Mismo trato que una merma, misma
razón.

**Pasa por TRÁNSITO, no derecho a la bodega.** Si la declaración del vendedor subiera
la bodega, un faltante se podría cubrir escribiendo una devolución que nunca se
entregó: su camión baja, la bodega sube, y nadie contó nada. Sería la única operación
del sistema donde la palabra de una persona mueve dos almacenes. El almacén de paso
—`almacenes.tipo = 'transito'`, previsto desde la 0004 y que nadie había usado— se
crea solo, por sucursal, la primera vez que hace falta: rechazar el documento porque
nadie lo configuró convertiría una omisión de la oficina en un faltante del vendedor.

**La bodega cierra el documento CONTANDO, y lo que no cuadre se queda en tránsito.**
No hay botón de «aceptar». Lo que entra a la bodega es lo contado —aunque sea más de
lo declarado, que también es un hecho físico— y la diferencia queda con nombre y con
fecha en `traspaso_detalle.cantidad_recibida`, que nunca sobrescribe lo declarado.
Tampoco hay botón de «rechazar»: rechazar le devolvería 18 cajas a un camión que ya
no las trae, y la respuesta correcta a «no llegó nada» es **contar cero**.

Lo que el vendedor ve: el estado y lo contado llegan a su teléfono por un delta
acotado a él (`entidad = 'traspaso'`), que es su comprobante de que la mercancía dejó
de ser su responsabilidad. El delta viaja como **estado**, no como diferencia, y
puede aplicarse mil veces porque **no mueve ninguna existencia**: lo que la bodega no
contó NO regresa al camión.

Y la liquidación no necesitó un solo cambio, que es el detalle que más vale la pena
saber: la ecuación del cierre no tiene término para «traspasado», pero `inicial` se
**deduce** del saldo vivo del camión (ver `saldo_inicial`), así que bajar las
existencias baja `inicial` y baja `esperado` en la misma cantidad. El cierre siempre
compara lo contado contra lo que el sistema tiene AHORA. Es el mismo mecanismo que
absorbe los ajustes de la oficina.

La pantalla de recepción vive **dentro de Entradas**, no en una sección nueva: es la
misma acción física —alguien parado en la bodega contando mercancía— y lo único
distinto es que viene de un camión y que el documento ya existe.

### 6.3 Un producto borrado con una venta en vuelo

El borrado de producto exige que no aparezca en **ningún** documento del servidor,
pero no puede ver una venta que todavía está en el teléfono. Si se da esa carrera,
la venta llega, no puede escribirse —llave foránea— y cae en cuarentena con su
error. Es el resultado correcto (§0.1: se marca, no se pierde) y la red de
seguridad funciona; lo que no hay es una señal para quien borró el producto de que
causó eso. Lo dejo así: cerrarlo bien significa que el panel pregunte a los
teléfonos activos qué llevan pendiente, y eso es una función, no un arreglo.

### 6.4 Nadie encola la poda

El job existe y está probado, pero no hay un `cron` que lo dispare. Es deliberado:
encender una poda automática el mismo día que se escribe, sobre una base que lleva
meses creciendo, es la forma de descubrir un problema de retención en producción.
Córrelo a mano la primera vez, mira cuánto borra, y después lo programamos.

---

## 7. Las pruebas que dejan esto cerrado

31 pruebas nuevas, todas sobre el modo de falla y no sobre el camino feliz:

| Qué defiende | Dónde |
|---|---|
| La baja de cliente no mata la tanda ni borra la venta sin subir | `blindaje_sync_test.dart` |
| Un delta que revienta se aparta con su error y la tanda sigue | `blindaje_sync_test.dart` |
| El SAVEPOINT no deja escrituras a medias | `blindaje_sync_test.dart` |
| `prospecto` sigue en la ruta; `inactivo` y `baja` no | `blindaje_sync_test.dart` |
| Un estatus desconocido cuenta como activo | `blindaje_sync_test.dart` |
| La ruta que pierde al cliente recibe su baja, por el pull | `test_blindaje_sync.py` |
| El payload sigue sin la geografía de PostGIS | `test_blindaje_sync.py` |
| Un cursor por debajo del piso manda a resincronizar | `test_blindaje_sync.py` |
| Un cursor exactamente EN el piso no resincroniza | `test_blindaje_sync.py` |
| La poda escribe el piso en la misma transacción | `test_blindaje_sync.py` |
| La poda no se lleva lo reciente | `test_blindaje_sync.py` |
| El payload de identidad NO lleva el hash de la contraseña | `test_blindaje_sync.py` |
| Un cambio que no toca el almacén no publica nada | `test_blindaje_sync.py` |
| El servidor ya ignoraba el almacén del payload | `test_blindaje_sync.py` |
| Un camión distinto reinicia el inventario local | `identidad_delta_test.dart` |
| El mismo camión, o el primer delta, no vacían nada | `identidad_delta_test.dart` |
| Cambiar de camión no se lleva los documentos sin subir | `identidad_delta_test.dart` |

Las dos defensas del hallazgo 1 están verificadas con **mutación**: sin el SAVEPOINT
por delta, las pruebas se ponen rojas.

### Y las del traspaso camión → bodega (§6.2)

59 más, de los tres lados del documento:

| Qué defiende | Dónde |
|---|---|
| La bodega NO sube con la sola palabra del vendedor | `test_traspaso_ingesta.py` |
| Un solo asiento ata los dos almacenes, con quién lo bajó | `test_traspaso_ingesta.py` |
| El payload no puede elegir de qué camión sale | `test_traspaso_ingesta.py` |
| Reenviar el sobre no baja el camión dos veces | `test_traspaso_ingesta.py` |
| Sin existencia se registra igual (§0.1) | `test_traspaso_ingesta.py` |
| El tránsito se crea solo, y es de la sucursal del camión | `test_traspaso_ingesta.py` |
| Lo devuelto NO se le cobra en la liquidación | `test_traspaso_ingesta.py` |
| Un nombre de tránsito tomado por una BODEGA se rechaza | `test_traspaso_ingesta.py` |
| Un origen que no es camión se rechaza (quedaría varado) | `test_traspaso_ingesta.py` |
| Lo que entra a la bodega es lo CONTADO, no lo declarado | `test_panel_devolucion_camion.py` |
| Lo que faltó se queda en tránsito | `test_panel_devolucion_camion.py` |
| Contar cero es respuesta válida y cierra el documento | `test_panel_devolucion_camion.py` |
| En blanco se rechaza: no es lo mismo que cero | `test_panel_devolucion_camion.py` |
| Recibir dos veces no mete la mercancía dos veces | `test_panel_devolucion_camion.py` |
| No se puede recibir «en un camión» (§0.2) | `test_panel_devolucion_camion.py` |
| Sin el permiso no se recibe, y gerencia hoy SÍ lo tiene | `test_panel_devolucion_camion.py` |
| El vendedor recibe el delta con lo contado | `test_panel_devolucion_camion.py` |
| El delta NO le devuelve al camión lo que no se contó | `traspaso_test.dart` |
| Aplicarlo dos veces no cambia nada (es estado) | `traspaso_test.dart` |
| «Nadie lo contó» no es «contaron cero» | `traspaso_test.dart` |
| Un traspaso que este teléfono no tiene no se inventa | `traspaso_test.dart` |
| El camión baja al capturar, en piezas aunque se teclee en cajas | `devolver_a_bodega_test.dart` |
| «Todo el camión» carga el saldo en unidad base | `devolver_a_bodega_test.dart` |
| La pantalla dice «en tránsito», no «entregado» | `devolver_a_bodega_test.dart` |

Verificado con **mutación** lo que paga el diseño entero: si la recepción mete lo
declarado en vez de lo contado, siete pruebas se ponen rojas; si el traspaso mueve
camión → bodega en vez de camión → tránsito, cinco.

### Un defecto de las pruebas mismas, que la auditoría encontró de paso

`sync_retencion` no estaba en `TABLAS_VOLATILES`, así que la prueba del job de poda
dejaba el piso alto **para todas las pruebas que corrieran después**: el síntoma fue
una prueba de rutas fallando por un motivo que no tenía nada que ver con rutas. Es
el mismo tropiezo que con `roles_permisos` la semana pasada, y la regla que queda es:
**una tabla que guarda estado de la instalación se vacía entre pruebas; una que
guarda datos de referencia, no.**
