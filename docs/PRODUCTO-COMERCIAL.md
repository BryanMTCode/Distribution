# Venderla como producto

> «¿Crees que esta app serviría para venderla?» (octubre de 2026)

**Sí tiene valor para venderse, pero todavía no está lista como producto.** Hoy es un sistema hecho
a la medida de Distribuciones SE. Venderla a otras distribuidoras es posible, con trabajo de por
medio. Este documento junta la evaluación, lo que falta, cómo se vendería, un plan por etapas y todo
lo de la Play Store.

Lo que dice aquí de reglas de Google es lo que estaba vigente al escribirlo. Google las cambia
seguido: antes de cada paso, confírmalo en la Play Console.

---

## 1. Lo que tiene a favor

- **Resuelve un problema real y difícil.** Es venta en ruta desde el camión que funciona **sin señal**:
  carga, venta, corte, mermas y la cuenta de cada vendedor. Hacer bien la parte sin señal es lo que
  más le cuesta a cualquiera, y aquí ya está resuelto.
- **Está probada en operación de verdad**, no es una maqueta. Los vendedores la usan todos los días,
  y cada problema real (cortes, tiendas, sincronización) se arregló y quedó con su prueba.
- **Está cuidada por dentro.** Tiene unas **2,770 pruebas automáticas** (1630 del servidor, 742 de
  Dart y 398 de pantallas) y documentación de instalación, respaldos, arranque diario y piloto. Cada
  decisión de negocio está escrita en el [ADR 0002](adr/0002-reglas-de-negocio.md) con su porqué.
- **Tiene controles que muchos sistemas no tienen:**
  - cada vendedor solo ve lo suyo, y la base de datos lo hace cumplir;
  - todo lo que se elimina queda registrado y se puede rastrear;
  - el corte cuadra solo contra lo cargado y lo vendido;
  - el costo y la ganancia los ve solo el administrador.
- **Tu empresa es el caso de éxito.** Poder decir «la uso yo con tantos vendedores desde octubre de
  2026» vende más que cualquier presentación.

## 2. Lo que falta para venderla

| # | Qué falta | Por qué importa | Trabajo |
|---|-----------|-----------------|---------|
| 1 | **Varias empresas** | Hoy sirve para una sola. Cada cliente necesitaría su propio servidor, o habría que rehacer la base para que quepan varias en uno. | Grande |
| 2 | **Quitar lo fijo de tu empresa** | «Distribuciones SE» y Mazatlán aparecen en unos 18 archivos: el nombre de la app, el ticket, el mapa sin señal, el catálogo inicial, la zona horaria. Hay que convertirlo en configuración. | Chico a mediano |
| 3 | **Facturación (CFDI)** | No hay timbrado. Muchas distribuidoras en México lo van a pedir, al menos para el mayoreo. | Mediano a grande |
| 4 | **Instalación sencilla** | Hoy se instala con comandos en un servidor y el APK se construye en tu PC. Un cliente necesita la app en la Play Store, actualizaciones solas y un servidor que alguien más administre. | Mediano |
| 5 | **Tiempo en operación** | Hace poco salió el error del corte en 0, que solo aparecía con varios teléfonos sincronizando a la vez ([§178](adr/0002-reglas-de-negocio.md)). Unos meses estable en tu empresa evitan ese tipo de sorpresas con un cliente. | Tiempo |
| 6 | **Soporte** | Cada cliente va a pedir cambios. Hay que decidir qué se adapta y qué no, quién contesta cuando algo falla a las 6 de la mañana, y en cuánto tiempo. | Organización |
| 7 | **Lo legal** | Contrato de servicio, aviso de privacidad (guarda datos de clientes y ubicaciones), registro de la marca y revisión de las licencias del software que usa. | Chico, con abogado |

## 3. Cómo se vendería

- **Por suscripción mensual por vendedor activo**, con el servidor administrado por ti, mejor que
  vender el programa. Se puede sumar una cuota de arranque por la configuración, la carga del
  catálogo y la capacitación.
- **A quién:** distribuidoras chicas y medianas como la tuya, de 5 a 30 vendedores, de abarrotes,
  botanas o bebidas, que hoy trabajan con papel o con Excel.
- **La diferencia:** ya existen otros sistemas de venta en ruta. Lo que esta tiene probado es que
  **funciona sin señal todo el día** y que **el corte cuadra solo**, con el faltante a la cuenta del
  vendedor y todo lo eliminado registrado.
- **El precio** debe cubrir el servidor, el soporte y tu tiempo, y compararse con lo que le cuesta a
  una distribuidora un faltante que no detecta o una tarde haciendo cortes a mano.

## 4. El plan, en orden

Ninguna etapa toca lo que ya funciona en tu empresa: se hace en una copia y se prueba antes.

0. **Estabilizar en tu empresa (2 a 3 meses).** Operar sin cambios grandes y juntar números: cortes
   que cuadran, incidentes por semana, cuánto tarda la sincronización. Ese es el caso de éxito.
1. **Quitar lo fijo de tu empresa** *(chico a mediano)*. Nombre, logo, datos del ticket, zona
   horaria y la región del mapa sin señal, todo como configuración; el catálogo inicial, como
   archivo que se carga.
2. **La app en la Play Store** *(mediano)*. Ver la sección 5. Se puede empezar ya para tus propios
   teléfonos, con la prueba interna.
3. **Varios clientes** *(grande)*. La decisión es entre dos caminos:
   - **Un servidor por cliente.** Es lo más simple, porque ya se instala con `docker compose`. Cuesta
     más operarlo y se automatiza la instalación.
   - **Un solo servidor para todos.** Cada tabla lleva su empresa y la seguridad por renglón separa
     una de otra. Es un cambio grande.

   La recomendación es empezar con un servidor por cliente y pasar a uno solo cuando haya más de
   unos 10 clientes.
4. **Facturación** *(mediano a grande, si el mercado la pide)*. Timbrado con un proveedor
   autorizado (PAC), los catálogos del SAT y el certificado de sello digital de cada cliente.
5. **Operación del servicio** *(mediano)*:
   - respaldos automáticos fuera del servidor;
   - alertas cuando algo falla (ya hay registro de errores);
   - actualizaciones del servidor sin comandos a mano;
   - un horario y un canal de soporte.
6. **Lo legal y lo comercial** *(chico, con abogado y contador)*. Contrato, aviso de privacidad,
   marca y forma de cobrar.
7. **Primeros clientes piloto.** Dos o tres distribuidoras conocidas, con precio de lanzamiento a
   cambio de su opinión, y el [protocolo de piloto](PILOTO.md) que ya se usó contigo.

---

## 5. La Play Store

### 5.1 Lo que la app ya tiene a favor

| Qué | Cómo está hoy | Para la Play Store |
|-----|---------------|--------------------|
| Llave de firma | Propia, `dsd-release.jks` ([ENTORNO-WINDOWS §4.2](ENTORNO-WINDOWS.md)), no la de depuración | Es la clave para pasar los teléfonos actuales a Play **sin desinstalar** (5.3) |
| Versión de Android | Apunta a Android 16 (API 36), por Flutter 3.35 | Cumple: Google exige apuntar a una versión reciente |
| Permisos | Ubicación solo con la app abierta, Bluetooth solo para conectarse a impresoras ya emparejadas, internet | Pocos y justificados. Sin ubicación en segundo plano, la revisión es mucho más sencilla |
| Número de versión | Sube en cada versión (`+59`) | Play lo exige igual |
| Identificador | `com.distribuidora.dsd_app` | No se puede cambiar nunca una vez publicado (5.2) |
| Nombre visible | «Distribuciones SE» | Se cambia cuando quieras |
| Servidor | Su dirección va dentro del APK (`DSD_BASE_URL`) | Para varios clientes hay que pedir un «código de empresa» (5.2) |

### 5.2 Decisiones antes de subir nada

- **Cuenta de desarrollador:**
  - cuesta **25 dólares, una sola vez**, y Google verifica tu identidad;
  - **personal:** con tu identificación oficial. Si la cuenta es nueva, antes de publicar al público
    Google exige una **prueba cerrada con al menos 12 personas durante 14 días seguidos**;
  - **de organización:** a nombre de la empresa. Pide un **número D-U-N-S**, que es gratis pero
    puede tardar semanas, y no exige la prueba de 12 personas. Si vas a venderla como negocio, es la
    indicada.
- **El identificador** (`com.distribuidora.dsd_app`) no se puede cambiar nunca en Play. Es lo bastante
  general para quedarse. Si quieres uno con tu marca, este es el momento. Cambiarlo después obliga
  a desinstalar e instalar de nuevo en cada teléfono, una vez y con todo sincronizado.
- **El nombre que se ve** en el teléfono y en la tienda sí se cambia cuando quieras: puede ser tu
  marca comercial en vez de «Distribuciones SE».
- **Una app para todos los clientes.** Hoy la dirección del servidor va dentro del APK. En la Play
  Store hay una sola app para todos, así que la primera vez la app tiene que pedir un **código de
  empresa** y, con él, saber a qué servidor hablar. Hacer una app por cliente choca con las reglas de
  Google contra apps repetidas, salvo como apps privadas (5.6). Para tus propios teléfonos no hace
  falta todavía.

### 5.3 La firma, y cómo pasar los teléfonos de hoy sin borrar nada

Google firma las apps de la Play Store con su sistema, **Play App Signing**, que es obligatorio. Al
crear la app te deja escoger entre dos caminos, y es la decisión más importante de todo el trámite:

- **Subir tu llave actual (recomendado).** Google te da una herramienta para exportar
  `dsd-release.jks` de forma segura. Así la versión de la Play Store va firmada igual que la que ya
  tienen los vendedores, y **se instala encima sin borrar nada**: ni ventas sin subir, ni el mapa,
  ni la configuración.
- **Dejar que Google cree una llave nueva.** Así la versión de Play no puede instalarse encima de la
  de hoy. Cada teléfono tendría que desinstalar primero, y desinstalar borra lo que no se haya subido.
  Solo vale con todo sincronizado y teléfono por teléfono.

Además, Google te pide una **llave de subida**, que es con la que firmas lo que le mandas. Si se
pierde, Google la puede reemplazar. La de la app no. **El respaldo de `dsd-release.jks` sigue siendo
igual de importante.**

### 5.4 Lo que pide la Play Console, paso a paso

1. **Construir un AAB en vez de un APK.** La tienda solo acepta el formato AAB:
   `flutter build appbundle`. Se agregaría un `make aab` junto al `make apk` de hoy, con las mismas
   revisiones.
2. **La ficha de la tienda:**
   - nombre (hasta 30 letras), descripción corta (80) y larga (4,000);
   - ícono de 512 × 512 y una imagen de portada de 1024 × 500;
   - al menos 2 capturas de pantalla del teléfono;
   - categoría «Empresa» y un correo de contacto.
3. **Política de privacidad:** una página pública con su dirección web. Es obligatoria porque la app
   usa la ubicación y guarda datos de clientes (nombres, teléfonos, direcciones).
4. **Seguridad de los datos:** un formulario que dice:
   - qué recopila: la ubicación precisa al vender y al dar de alta, los datos de las tiendas y las
     ventas;
   - para qué: para que la app funcione;
   - que viaja cifrado (HTTPS) y no se comparte con nadie;
   - cómo se pide que se borre.
5. **El permiso de ubicación:** conviene una pantalla que explique, antes de pedirlo, para qué se usa
   («para guardar dónde está cada tienda y confirmar la visita»). Google revisa que se avise. Como no
   se usa en segundo plano, no hace falta el trámite largo de ubicación en segundo plano.
6. **Acceso para la revisión:** la app pide usuario y contraseña, así que hay que darle a Google una
   **cuenta de prueba en un servidor de demostración con datos inventados**, con instrucciones para
   entrar. Sin esto rechazan la app. Puede salir del modo demo que ya existe para depuración.
7. **Clasificación del contenido** (un cuestionario), **público:** mayores de 18, **sin anuncios**
   y **sin compras dentro de la app**.
8. **El cobro, por fuera.** No pongas botones de «comprar» ni «suscribirse» dentro de la app. Cobra a
   las empresas por transferencia o factura. Una app «solo para quien ya tiene cuenta» se permite sin
   usar los pagos de Google, y así Google no se queda con comisión.

### 5.5 Las pruebas y la publicación

| Etapa | Para qué | Quién la ve |
|-------|----------|-------------|
| **Prueba interna** | Llega en minutos. Sirve desde ya como canal privado para tus vendedores, con actualizaciones automáticas | Hasta 100 personas, por correo |
| **Prueba cerrada** | La que exige Google a las cuentas personales nuevas (12 personas, 14 días) | Las que invites |
| **Producción** | Pública en la tienda. Conviene el **lanzamiento escalonado**: primero un porcentaje de los teléfonos, y si no hay fallas, el resto | Todos |

La primera revisión puede tardar varios días; las siguientes, de horas a un par de días.

### 5.6 Cómo cambian las actualizaciones

- **Se acaba «instalar encima» a mano:** la Play Store baja cada versión sola.
- **Cuidado con que llegue a media ruta.** Una actualización que se instala mientras el vendedor está
  vendiendo lo puede interrumpir. Conviene:
  - publicar en la tarde o la noche;
  - usar el lanzamiento escalonado;
  - si hace falta, que la app avise «hay una versión nueva» y deje actualizar al terminar el día.
- **Cada teléfono necesita una cuenta de Google.** Mejor una de la empresa que la personal del
  vendedor, para que no se la lleve si se va.
- **Para clientes que administran sus teléfonos** con un sistema de administración (MDM / Android
  Enterprise), Google tiene **apps privadas** en la Play Store administrada. La app no aparece al
  público, y es la forma correcta de una app distinta por cliente.

### 5.7 Lo que viene aunque no la subas a la Play Store

Google anunció en agosto de 2025 que las apps en teléfonos Android certificados tendrán que venir de
**desarrolladores verificados**, también las que se instalan desde un APK como hoy. Según ese
anuncio, empieza en septiembre de 2026 en Brasil, Indonesia, Singapur y Tailandia, y se extiende al
resto del mundo desde 2027. Las fechas pueden moverse, así que hay que revisarlas.

En la práctica, para seguir instalando el APK a mano en México, en algún momento habrá que
registrarse como desarrollador y registrar la app y su llave. Es casi el mismo trámite que la
cuenta de la Play Store, así que abrirla ya sirve para las dos cosas.

### 5.8 Costos y tiempos

| Qué | Costo | Tiempo |
|-----|-------|--------|
| Cuenta de desarrollador | 25 dólares, una vez | Días, por la verificación |
| Número D-U-N-S (cuenta de organización) | Gratis | Hasta unas semanas |
| Prueba cerrada (cuenta personal nueva) | — | 14 días |
| Primera revisión de Google | — | Varios días |
| Trabajo técnico: AAB, aviso de ubicación, cuenta de revisión, ficha y, para vender, el código de empresa | — | Mediano |

### 5.9 Lista para tachar

- [ ] Decidir cuenta personal o de organización; si es de organización, pedir el D-U-N-S.
- [ ] Abrir la cuenta de desarrollador y verificar la identidad.
- [ ] Decidir si el identificador `com.distribuidora.dsd_app` se queda (no se cambia después).
- [ ] Decidir el nombre comercial que se ve en la tienda.
- [ ] Confirmar que el respaldo de `dsd-release.jks` está fuera de la PC y se puede abrir.
- [ ] Agregar `make aab` y construir el primer AAB.
- [ ] Al crear la app: **subir la llave actual** a Play App Signing.
- [ ] Publicar la política de privacidad en una página propia.
- [ ] Llenar la seguridad de los datos, la clasificación del contenido y el público objetivo.
- [ ] Agregar la pantalla que explica la ubicación antes de pedirla.
- [ ] Montar el servidor de demostración y la cuenta para la revisión de Google.
- [ ] Preparar el ícono, la portada, las capturas y las descripciones.
- [ ] Subir a la prueba interna y pasar ahí los teléfonos de tus vendedores.
- [ ] Para vender: el código de empresa en el primer arranque, y luego la producción con
      lanzamiento escalonado.
