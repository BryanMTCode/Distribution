# La app en la Play Store

> «Dame más detalles sobre lo de meterla a la Play Store» (octubre de 2026)

Qué tiene ya la app, qué hay que decidir, cómo pasar los teléfonos de hoy sin borrar nada, qué pide
Google y cuánto cuesta. Se puede empezar ya para los teléfonos de tus vendedores, con la prueba
interna, aunque todavía no se venda a nadie. Lo de venderla como producto está en
[PRODUCTO-COMERCIAL.md](PRODUCTO-COMERCIAL.md).

Lo que dice aquí de las reglas de Google es lo que estaba vigente al escribirlo. Google las cambia
seguido: antes de cada paso, confírmalo en la Play Console.

## 1. Lo que la app ya tiene a favor

| Qué | Cómo está hoy | Para la Play Store |
|-----|---------------|--------------------|
| Llave de firma | Propia, `dsd-release.jks` ([ENTORNO-WINDOWS §4.2](ENTORNO-WINDOWS.md)), no la de depuración | Es la clave para pasar los teléfonos actuales a Play **sin desinstalar** (punto 3) |
| Versión de Android | Apunta a Android 16 (API 36), por Flutter 3.35 | Cumple: Google exige apuntar a una versión reciente |
| Permisos | Ubicación solo con la app abierta, Bluetooth solo para conectarse a impresoras ya emparejadas, internet | Pocos y justificados. Sin ubicación en segundo plano, la revisión es mucho más sencilla |
| Número de versión | Sube en cada versión (`+59`) | Play lo exige igual |
| Identificador | `com.distribuidora.dsd_app` | No se puede cambiar nunca una vez publicado (punto 2) |
| Nombre visible | «Distribuciones SE» | Se cambia cuando quieras |
| Servidor | Su dirección va dentro del APK (`DSD_BASE_URL`) | Para varios clientes hay que pedir un «código de empresa» (punto 2) |

## 2. Decisiones antes de subir nada

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
  Google contra apps repetidas, salvo como apps privadas (punto 6). Para tus propios teléfonos no hace
  falta todavía.

## 3. La firma, y cómo pasar los teléfonos de hoy sin borrar nada

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

## 4. Lo que pide la Play Console, paso a paso

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

## 5. Las pruebas y la publicación

| Etapa | Para qué | Quién la ve |
|-------|----------|-------------|
| **Prueba interna** | Llega en minutos. Sirve desde ya como canal privado para tus vendedores, con actualizaciones automáticas | Hasta 100 personas, por correo |
| **Prueba cerrada** | La que exige Google a las cuentas personales nuevas (12 personas, 14 días) | Las que invites |
| **Producción** | Pública en la tienda. Conviene el **lanzamiento escalonado**: primero un porcentaje de los teléfonos, y si no hay fallas, el resto | Todos |

La primera revisión puede tardar varios días; las siguientes, de horas a un par de días.

## 6. Cómo cambian las actualizaciones

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

## 7. Lo que viene aunque no la subas a la Play Store

Google anunció en agosto de 2025 que las apps en teléfonos Android certificados tendrán que venir de
**desarrolladores verificados**, también las que se instalan desde un APK como hoy. Según ese
anuncio, empieza en septiembre de 2026 en Brasil, Indonesia, Singapur y Tailandia, y se extiende al
resto del mundo desde 2027. Las fechas pueden moverse, así que hay que revisarlas.

En la práctica, para seguir instalando el APK a mano en México, en algún momento habrá que
registrarse como desarrollador y registrar la app y su llave. Es casi el mismo trámite que la
cuenta de la Play Store, así que abrirla ya sirve para las dos cosas.

## 8. Costos y tiempos

| Qué | Costo | Tiempo |
|-----|-------|--------|
| Cuenta de desarrollador | 25 dólares, una vez | Días, por la verificación |
| Número D-U-N-S (cuenta de organización) | Gratis | Hasta unas semanas |
| Prueba cerrada (cuenta personal nueva) | — | 14 días |
| Primera revisión de Google | — | Varios días |
| Trabajo técnico: AAB, aviso de ubicación, cuenta de revisión, ficha y, para vender, el código de empresa | — | Mediano |

## 9. Lista para tachar

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
