# El piloto de campo: dos semanas, un vendedor, el papel en paralelo

> **La única frase que importa de este documento:**
> el papel en paralelo no es un respaldo por si la app falla — es el patrón de
> medida, y si nadie lo captura y nadie compara, el vendedor hizo doble trabajo
> durante dos semanas para producir una anécdota.

El plan (`docs/ARQUITECTURA.md` §3, Fase 3) pide *«un piloto con UN vendedor en
UNA ruta durante 2 semanas, con el proceso de papel en paralelo»*. Dos de las
3.5 semanas de la fase son esto, y **no se pueden acelerar: su valor es el
calendario.**

Lo que sí se puede hacer antes es decidir qué se va a medir y contra qué. Eso
está construido: la pantalla **Piloto** del panel, las tablas de la migración
0024 y los doce criterios de salida, sembrados con fecha anterior al primer día.

---

## 0. Por qué este paso vale más que las dos fases siguientes

Hay 1003 pruebas de Python, 509 de Dart y 248 de widget. Todas prueban que el
sistema hace lo que decidimos que hiciera.

**Ninguna prueba que lo que decidimos sea lo correcto.** Eso solo lo puede
decir un vendedor con el teléfono en la mano, en la calle, con un cliente
esperando y sin señal. El piloto es el único paso del plan que puede invalidar
una decisión de diseño — y por eso es el único que no se puede saltar.

Lo que el piloto puede descubrir y nada más puede:

- Que el carrito necesita tres toques donde el papel necesitaba uno, y a la
  visita número veinte eso son veinte minutos.
- Que el vendedor cobra y apunta primero, y captura en la app al final del día
  «para no hacer esperar al cliente» — lo que convierte todo el diseño offline
  en un capturista nocturno.
- Que el catálogo tiene el producto con otro nombre del que usa la tienda.
- Que la pantalla se ve blanca al sol de las dos de la tarde.

---

## 1. La lista del día −1

Todo esto antes del primer lunes. Un piloto que arranca sin esto gasta su
primera semana descubriendo problemas de instalación en vez de problemas de la
app, y esa semana no se recupera.

| # | Qué | Cómo se comprueba |
|---|---|---|
| 1 | Respaldo probado, no solo configurado | `make simulacro` termina en verde (`docs/RESPALDOS.md` §2) |
| 2 | Zona horaria del servidor = zona de la operación | `make doctor` no marca la sección de zona horaria |
| 3 | Migraciones al día | `make migrar` y `make doctor` |
| 4 | RLS activa | `/salud` responde `"rls": true` (`db/ops/rol_api.sql`) |
| 5 | El vendedor existe, con su ruta y su camión | panel → *Usuarios y rutas* |
| 6 | Sus clientes están cargados, con coordenadas | panel → *Clientes* |
| 7 | La lista de precios cubre lo que carga | panel → *Productos* |
| 8 | El teléfono registrado y activo | panel → *Teléfonos* |
| 9 | `dias_max_offline` razonable para esa ruta | panel → *Usuarios y rutas* |
| 10 | La carga del primer día, capturada | panel → *Cargas* |
| 11 | El vendedor entrenado: una jornada completa de práctica | ver §2 |
| 12 | El piloto definido en el panel | panel → *Piloto* |
| 13 | El APK del teléfono firmado con la llave de PRODUCCIÓN | ver abajo |

Hay un atajo para los nueve primeros:

```bash
make piloto-listo VENDEDOR=VEND01
```

Revisa lo que una consulta puede revisar y **dice qué falta y cómo se arregla**.
Lo que no puede revisar —los puntos 11 y 13— es justamente lo que más cuesta
cuando falta.

### El punto 13, que solo se puede comprobar antes de empezar

El teléfono del piloto tiene que traer un APK firmado con la llave de producción,
no con la de depuración. No es una formalidad: Android solo acepta actualizar una
app instalada si el APK nuevo trae **la misma firma**, y la llave de depuración es
distinta en cada máquina. Si el piloto arranca con un APK firmado así, la primera
corrección que haya que mandar a media semana no se va a poder instalar encima —
habrá que desinstalar, y eso **borra la base local** con las ventas que el
vendedor no hubiera subido.

Dicho de otro modo: es el único punto de esta lista que, si se hace mal, se cobra
en dinero de la calle y no en tiempo.

```bash
make apk DSD_BASE_URL=https://api.tudominio.com
```

Ese comando no deja construir el APK sin keystore ni sin servidor, y al terminar
imprime la huella SHA-256 del certificado. **Apúntala el día −1**: es la que
tienen que traer todos los APK que la empresa reparta después. El procedimiento
completo —crear el keystore y respaldarlo antes de firmar nada— está en
[ENTORNO-WINDOWS §4.2](ENTORNO-WINDOWS.md#42-el-apk-de-producción-y-la-llave-que-no-se-puede-perder).

### El entrenamiento, que no es una demostración

Media jornada de práctica con el vendedor **haciendo**, no mirando: diez ventas
de prueba a clientes reales de su ruta, un cobro, un no-drop, una merma, una
cancelación, y la liquidación al final. Con la base de pruebas, no la de
producción.

Lo que de verdad se entrena no es la app, son las dos reglas que van a chocar
con su costumbre:

1. **El precio no se negocia en la calle.** No hay campo para cambiarlo y no es
   un descuido: es la regla sellada del proyecto (ADR 0002 §0). Si el vendedor
   espera poder bajar $2 en una caja, se va a enterar frente al cliente, y esa
   es la peor forma de enterarse.
2. **Lo que no se captura no existe.** La app no es la libreta de la noche: una
   venta capturada seis horas después es una venta que el sistema no tenía
   cuando la oficina la necesitaba.

---

## 2. La rutina diaria

Son veinte minutos al día de la oficina. Si se vuelven cuarenta, el piloto se
abandona al día cuatro — y entonces no mide nada (de ahí que la cobertura de
captura sea el **primer criterio y sea bloqueante**).

### Mañana, antes de que salga el camión (15 min)

1. Abre **Piloto** en el panel.
2. **Captura el cuadre de ayer** con la hoja del vendedor en la mano: documentos,
   importe, cobranza y visitas. Cuatro cifras.
3. Lee el aviso que sale al guardar. Si dice que al sistema le faltan
   documentos, **no lo investigues todavía**: mira si siguen faltando mañana.
   Lo que falta hoy y aparece mañana era retraso de entrega (§0.3 funcionando);
   lo que sigue faltando al cierre es una venta que no existe.
4. Pregúntale al vendedor: **«¿qué te estorbó ayer?»** No «¿todo bien?», que se
   contesta «sí» por educación.
5. Registra lo que diga en la bitácora, con la hora si la recuerda.

### Tarde, cuando regresa el camión (5 min)

6. Cierra la liquidación del día (panel → *Liquidación*).
7. Si hubo algo grave, regístralo el mismo día: a las seis de la tarde todavía
   se acuerda del detalle, y el detalle es lo que permite reproducirlo.

### El viernes de la primera semana (30 min)

8. Abre **Piloto** y lee los criterios. Es el único momento en que se puede
   corregir el rumbo: lo que esté en rojo el viernes y no se arregle el fin de
   semana va a estar en rojo el viernes siguiente.
9. Resuelve las incidencias abiertas, o decide explícitamente que no se
   resuelven. Una incidencia abierta sin decisión es una que se va a repetir.

---

## 3. Por qué la bitácora se captura en el panel y no en la app

Era tentador: una pantalla de «reportar problema» en el teléfono, con su
operación en el outbox.

Y habría sido un error. **No se le agregan funciones a la app que se está
poniendo a prueba.** Esa pantalla sería código nuevo sin piloto, dentro del
piloto, con su propio camino de sincronización que puede fallar — y si falla, se
pierden justo los reportes de las fallas. Peor: la incidencia más importante que
puede ocurrir es *«la app no abrió»*, y en ese escenario ninguna pantalla de la
app puede reportarla.

El canal es el que ya existe y no depende de nosotros: el vendedor habla por
teléfono y la oficina teclea. Vale más una bitácora que funciona que una digital
que comparte el destino de lo que vigila.

### Aquí sí va texto libre, y en los no-drops no

La regla de la Fase 6 es «texto libre = datos inanalizables», y sigue en pie
para los motivos de no-venta: se capturan veinte veces al día y su valor está en
poder **contarlos**.

Una incidencia de piloto es lo contrario: pasa una vez y su valor es el
**detalle**. «Se cerró la app al agregar el tercer renglón del carrito con el
teclado abierto» no cabe en ningún catálogo, y es exactamente lo que se necesita
para reproducirla.

Así que se cierra el catálogo de lo que ya se sabe —categoría y severidad, que
son para contar— y se deja texto para lo que no se sabe. Un piloto cuyo
formulario solo acepta opciones conocidas solo puede descubrir lo que ya estaba
previsto.

---

## 4. Los doce criterios de salida

Están en la tabla `piloto_criterios`, sembrados por la migración 0024, y **no
hay pantalla para cambiarlos**. Eso no es una función que falte: es la propiedad
que los hace servir.

> Si los criterios se deciden al final, se deciden mirando el resultado, y
> entonces el piloto no decidió nada: justificó lo que ya se quería hacer.

Las dos semanas **van** a producir incidencias —para eso son—, y en ese momento
la pregunta «¿esto es suficiente para seguir?» ya no se puede contestar con
honestidad, porque los teléfonos ya se quieren comprar. Si un umbral de verdad
estaba mal puesto, se cambia con una migración, que deja huella y fecha.

### Los nueve bloqueantes

| Criterio | Umbral | Qué falla si no se cumple |
|---|---|---|
| El papel se capturó todos los días | ≥ 100 % | **Nada se midió.** Es el criterio del criterio |
| No falta ninguna venta del papel | 0 documentos | Dinero cobrado sin saber a quién se le vendió |
| El importe cuadra | ≤ 0.5 % | Se cobra distinto de lo que se registra |
| La cobranza cuadra | ≤ 0.5 % | El arqueo del día no va a cuadrar |
| La segunda semana, sin bloqueos | 0 | Lo que se rompe no se está arreglando |
| La app no le cuesta la jornada | ≤ 15 min/día | La ruta paga el sistema con tiempo |
| Las ventas llegan el mismo día | ≤ 24 h | La liquidación se hace con datos incompletos |
| Nada quedó en cuarentena | 0 | El servidor no pudo aplicar algo, y es la excepción |
| Cada jornada cerró su liquidación | ≥ 100 % | No se demostró el ciclo completo |

### Los dos informativos

**Cancelaciones ≤ 5 %** — una cancelación es un camino legítimo, pero si una de
cada diez ventas se cancela, la captura es incómoda y eso se arregla en la app.

**Lo que faltaba al capturar y llegó después ≤ 10 %** — no es pérdida, es
retraso: mide §0.3, no una falla. Se vigila porque un retraso que crece día con
día es el síntoma temprano de un problema de red o de cola.

### Y uno que este piloto NO prueba

**La impresión Bluetooth.** `Impresora` solo tiene implementación simulada hasta
que llegue la **EC-MP200**: los bytes del ticket están generados y verificados
byte a byte (59 pruebas), pero no hay socket.

Eso **no bloquea el arranque del piloto** —el papel va en paralelo de todos
modos, así que el comprobante del cliente sigue siendo la nota de siempre— pero
sí bloquea el despliegue, y se valida aparte. Está declarado en la tabla y
aparece en la pantalla bajo *«lo que este piloto no prueba»* para que no se
convierta en un supuesto el día que se compren los teléfonos.

### Dos umbrales que parecen raros y no lo son

**«Cero bloqueos en la segunda semana» y no «cero en el piloto».** La primera
semana va a tener bloqueos: para eso es el piloto. Un umbral de cero sobre las
dos semanas haría fracasar al piloto que funcionó. Lo que decide es si se
dejaron de repetir.

**La bitácora completamente vacía no aprueba nada.** Los dos criterios que la
leen quedan *sin medir*, no en verde. Dos semanas con una app nueva y cero
incidencias de cualquier tipo no es una app perfecta: es un registro que nadie
llevó, y si el cero contara como verde, la forma más fácil de aprobar el piloto
sería no apuntar nada. Basta una incidencia —la molestia más chica— para que el
conteo vuelva a ser legible.

---

## 5. Cuándo abortar antes de las dos semanas

No todo se aguanta catorce días. Para antes y arregla si pasa cualquiera de
estas:

1. **Se perdió una venta y no se sabe por qué.** No «llegó tarde»: desapareció.
   Es la única falla que cuesta dinero del cliente y confianza del vendedor al
   mismo tiempo.
2. **Dos días seguidos sin poder operar.** El vendedor ya volvió al papel en su
   cabeza, y lo que se mide a partir de ahí es el papel con una app encima.
3. **El vendedor dejó de capturar en el momento** y pasó a capturar de noche.
   El piloto dejó de medir el sistema que se diseñó.
4. **La oficina dejó de capturar el papel tres días.** Ya no hay patrón de
   medida; seguir es gastar las dos semanas sin poder decidir.

Abortar no es fracasar: es dejar de gastar calendario en una medición que ya no
mide. Se cierra con veredicto **«repetir»**, se arregla lo encontrado y se
vuelve a pilotar — el historial del piloto que no pasó es justo lo que explica
por qué el siguiente se hizo distinto.

---

## 6. La junta del día 15

Media hora, con la pantalla **Piloto** abierta y nada más. El orden importa:

1. **Lee los criterios en voz alta**, con sus cifras. No las impresiones.
2. **Lee la bitácora completa.** Incluidas las molestias: tres molestias de la
   misma cosa son un problema de diseño, no tres quejas.
3. **Pregúntale al vendedor una sola cosa:** *«¿quieres seguir con el teléfono o
   prefieres el papel?»* La respuesta no decide, pero si es «el papel» y los doce
   criterios están en verde, hay algo que la medición no vio.
4. **Firma el veredicto en el panel**, con la razón escrita. Es lo que se va a
   leer dentro de tres meses, cuando nadie recuerde por qué se dijo lo que se
   dijo.

| Veredicto | Qué significa |
|---|---|
| **Adelante** | Se despliega a las demás rutas |
| **Repetir** | Se arregla lo encontrado y se vuelve a pilotar |
| **Alto** | El enfoque no funciona como está |

El sistema **sugiere** el veredicto a partir de los criterios y no lo dicta: un
veredicto automático le quitaría a una persona la obligación de firmar la
decisión de poner esto en siete camiones.

---

## 7. Lo que el piloto no va a contestar

Decirlo ahora evita que alguien lo dé por contestado después:

- **Si aguanta ocho camiones a la vez.** Un vendedor no produce concurrencia. Lo
  que sí dice el piloto es que el camino funciona; el volumen se prueba con las
  pruebas de caos de la Fase 2, que ya están.
- **Si el ticket impreso sale bien.** Falta la EC-MP200 (§4).
- **Si el pronóstico de demanda sirve.** Necesita meses de datos, no dos semanas.
  Es lo que falta de la Fase 8 y por eso va **después** del piloto.
- **Si otro vendedor lo va a usar igual.** Un vendedor es una persona, y la más
  dispuesta suele ser la que se elige para el piloto. Lo que el piloto prueba es
  que el sistema no estorba a alguien que quiere usarlo; la resistencia de quien
  no quiere es un problema distinto y se resuelve distinto.

---

## 8. Después del «adelante»

En este orden, y no todos a la vez:

1. **La impresora**, con la EC-MP200: media semana, y es lo único que el piloto
   dejó declaradamente sin probar.
2. **La segunda ruta**, una semana con el papel en paralelo otra vez — pero ya
   sin la bitácora diaria: lo que se vigila ahí es el cuadre, no la usabilidad.
3. **Las demás rutas**, sin papel en paralelo.
4. **Los modelos de la Fase 8** (pronóstico y cohortes), con meses de datos
   reales.

Y una cosa que no es código: la decisión de qué pasa con las notas de papel
cuando dejen de existir. Hoy son el comprobante del cliente, el respaldo del
vendedor y el documento de la oficina al mismo tiempo. Quitarlas quita las tres
cosas, y las tres necesitan un sustituto distinto.
