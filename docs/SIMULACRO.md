# Simulacro maestro: un día completo de operación

Un ciclo end-to-end en tu máquina, con el teléfono en la mano, **antes de rentar
el VPS**. Si esto corre sin errores, lo que falla en producción será
infraestructura y no el sistema.

No es el piloto (`PILOTO.md`): eso son dos semanas con un vendedor real. Esto son
tres horas contigo haciendo los cuatro papeles —almacén, oficina, vendedor y
cierre— con cifras prescritas para que puedas declarar **cuadró** o **no cuadró**
sin interpretar nada.

> **Las cantidades y los precios de este guion no son ejemplos: son el examen.**
> Están elegidos para que la aritmética sea verificable a mano y para que caiga en
> los casos que duelen — el precio de 4 decimales, la conversión caja↔pieza, una
> venta a crédito que NO entra al efectivo, una merma y una devolución que mueven
> el retorno en direcciones opuestas.

---

## 0. Antes de empezar

```bash
make db && make migrar && make doctor
make usuario        # el primero de oficina, rol gerente
make api            # déjalo corriendo
```

`make doctor` tiene que salir sin FALLA. Si marca la zona horaria, arréglalo
ahora: en UTC−6, a partir de las 18:00 locales `CURRENT_DATE` ya dice mañana, y el
cierre compararía el papel de un día contra las ventas de otro.

**La hora del simulacro importa.** Hazlo entre la mañana y las 17:00 locales. Si
empiezas a las 21:00 vas a cruzar la medianoche operativa a media ruta y vas a
pasar la noche depurando algo que no está roto.

### El teléfono en tu red local

El teléfono y la PC en el **mismo WiFi**. Averigua la IP de la PC (`ip -4 addr` en
WSL, `hostname -I`) y compila **en depuración**:

```bash
cd mobile/app
flutter run --dart-define=DSD_BASE_URL=http://192.168.1.50:8000
```

Con `http://` **tiene que ser un build de depuración**: el permiso de tráfico sin
TLS vive solo en el manifiesto de debug, y en release Android lo prohíbe — el APK
compilaría bien y no se conectaría a nada.

Y la API tiene que escuchar en la red, no solo en localhost:

```bash
cd server && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Comprueba desde el navegador del **teléfono**: `http://192.168.1.50:8000/salud`.
Si eso no responde, nada de lo que sigue va a funcionar y el problema es el
firewall de Windows, no el sistema.

---

## 1. La bodega: el inventario que no existía

**Panel → Productos.** Captura los tres, con sus dos presentaciones:

| SKU | Nombre | Pieza | Caja | Piezas por caja |
|---|---|---|---|---|
| `SOPA-70G` | Sopa de fideo 70 g | **12.3333** | 296.00 | 24 |
| `FRIJOL-1K` | Frijol bayo 1 kg | 32.5000 | 620.00 | 20 |
| `ACEITE-900` | Aceite vegetal 900 ml | 38.9000 | 445.50 | 12 |

> `12.3333` no es un adorno: 296.00 ÷ 24 no es exacto, y es el caso que justifica
> los 4 decimales de precio. Si lo capturas como `12.33`, la caja de 24 va a dar
> 295.92 y el simulacro va a «fallar» por tu redondeo.

**Panel → Entradas → Nueva entrada.**

- Motivo: **Inventario inicial**
- Destino: la bodega central
- Nota: obligatoria. *«Conteo de arranque del simulacro, 4 de octubre.»*

Captura **en bultos**, que es como está en el anaquel:

| SKU | Cantidad | Unidad |
|---|---|---|
| `SOPA-70G` | 20 | CAJA |
| `FRIJOL-1K` | 10 | CAJA |
| `ACEITE-900` | 8 | CAJA |

**Confirmar.**

### ✅ Punto de control 1

**Panel → Inventario**, bodega central:

| SKU | Tiene que decir |
|---|---|
| `SOPA-70G` | **480** pz (20 × 24) |
| `FRIJOL-1K` | **200** pz (10 × 20) |
| `ACEITE-900` | **96** pz (8 × 12) |

Si dice 20, 10 y 8, capturaste piezas en vez de cajas. Si dice otra cosa, el
factor de conversión del producto está mal.

---

## 2. El vendedor, su camión y su teléfono

**Panel → Usuarios y rutas → Nuevo usuario**, rol `vendedor`, con la casilla de
**crearle también su camión** marcada. Después, en la misma pantalla, **crea su
ruta y asígnalo como titular**.

La pantalla marca en rojo arriba los vendedores sin camión y sin ruta. No sigas
hasta que esa alerta esté limpia: sin camión no hay a dónde cargar, y sin ruta su
teléfono llega sin un solo cliente.

**Panel → Clientes → Nuevo.** Tres clientes en su ruta, y **uno con crédito**
(límite 5 000) — lo necesita la venta a crédito del paso 4.

**Panel → Teléfonos → Vincular un teléfono.** Elige al vendedor, ponle etiqueta
(*«Moto G54 — simulacro»*) y guarda. **Copia el identificador que aparece en el
aviso verde**: es lo que se teclea en el teléfono, una sola vez.

### ✅ Punto de control 2

En el teléfono, pantalla de login → **Vincular este equipo**: código del vendedor,
su contraseña, y el identificador. Tiene que entrar.

Después **cierra la app y vuelve a abrirla**: ahora entra con **PIN, sin señal**.
Apaga el WiFi del teléfono para comprobarlo. Eso es lo que el vendedor va a hacer
todos los días.

> Si dice «dispositivo no registrado» el identificador está mal copiado. Si dice
> «no se pudieron traer los folios», quedó vinculado pero sin folios: entra con el
> PIN, prende el WiFi y sincroniza antes de seguir — sin folios no se puede cerrar
> una venta, y es el comportamiento correcto.

---

## 3. Cargar el camión

**Panel → Cargas → Abrir borrador.** Vendedor, bodega central, fecha de hoy.

Captura **en cajas**:

| SKU | Cantidad | Unidad |
|---|---|---|
| `SOPA-70G` | 10 | CAJA |
| `FRIJOL-1K` | 5 | CAJA |
| `ACEITE-900` | 4 | CAJA |

**Confirmar la carga.**

### ✅ Punto de control 3

| Dónde | Qué |
|---|---|
| Panel → Inventario, bodega | `SOPA-70G` **240**, `FRIJOL-1K` **100**, `ACEITE-900` **48** — bajó exactamente lo cargado |
| Panel → Inventario, camión | `SOPA-70G` **240**, `FRIJOL-1K` **100**, `ACEITE-900` **48** |
| **Teléfono** | Sincroniza. El catálogo tiene que mostrar los tres con esas existencias |

Que el camión reciba la carga **en el teléfono** es la mitad del simulacro: ahí se
prueba que el delta salió, viajó y se aplicó.

> Si el panel se niega a confirmar diciendo que el equipo tiene operaciones sin
> subir, es el §2.3 funcionando. Sincroniza el teléfono y vuelve a intentar.

---

## 4. La ruta, con el teléfono y **sin WiFi**

**Apaga el WiFi del teléfono.** Todo esto es offline: es el punto del sistema.

| # | Qué | Detalle |
|---|---|---|
| 1 | **Venta de contado** al cliente 1 | `SOPA-70G` **2 CAJA** + `ACEITE-900` **6 PZA** → total **825.40** |
| 2 | **Venta de contado** al cliente 2 | `FRIJOL-1K` **1 CAJA** → total **620.00** |
| 3 | **Venta a crédito** al cliente con crédito | `SOPA-70G` **1 CAJA** + `FRIJOL-1K` **10 PZA** → total **621.00** |
| 4 | **Cobro en efectivo** de **500.00** | sobre el saldo del cliente a crédito |
| 5 | **Merma** de `ACEITE-900`, **3 PZA** | motivo DANADO_BODEGA |
| 6 | **Devolución** del cliente 1, `SOPA-70G` **1 CAJA** | 24 pz regresan al camión |
| 7 | **Un no-drop** en el cliente 3 | el motivo que quieras del catálogo |
| 8 | **Alta de un cliente nuevo** con GPS | en la calle, con la ubicación real |

### ✅ Punto de control 4, en el teléfono y todavía sin señal

| Qué | Tiene que decir |
|---|---|
| Venta 1 | **825.40** — si dice 825.32, capturaste el precio con 2 decimales |
| Venta 3 | **621.00**, y el **disponible del cliente bajó 621.00 sin sincronizar** |
| Existencia de `ACEITE-900` en el camión | **39** pz (48 − 6 vendidas − 3 merma) |
| Existencia de `SOPA-70G` | **192** pz (240 − 72 vendidas + 24 devueltas) |
| La cola | **7 u 8 operaciones pendientes** |

El renglón del crédito es el que más vale: el disponible se descuenta **con la
venta en la cola**, sin servidor. Si no baja, la regla de crédito offline no está
funcionando y eso sí es un defecto.

---

## 5. La sincronización final

**Prende el WiFi** y sincroniza desde el teléfono. Espera a que la cola llegue a
**cero**.

### ✅ Punto de control 5

| Dónde | Qué |
|---|---|
| Panel → **Cuarentena** | **vacía**. Un solo documento aquí y el cierre se bloquea — y debe bloquearse |
| Panel → **Ventas** | las tres, con sus totales exactos |
| Panel → **Cobranza** | el cliente a crédito con saldo **121.00** (621.00 − 500.00) |
| Panel → **Clientes** | el prospecto nuevo, pendiente de confirmar |
| Panel → **Teléfonos** | el equipo **al día**, cola en 0 |

---

## 6. La liquidación y el arqueo

**Panel → Liquidación → Abrir**, sobre la carga de hoy.

Captura el **retorno contado físicamente**. Pon exactamente esto:

| SKU | Contado |
|---|---|
| `SOPA-70G` | **192** |
| `FRIJOL-1K` | **70** |
| `ACEITE-900` | **39** |

Y el **efectivo entregado**: **1 945.40**

### ✅ Punto de control 6 — el que decide todo

| Qué | Tiene que decir | De dónde sale |
|---|---|---|
| Diferencia de `SOPA-70G` | **0** | 240 − 72 − 0 + 24 = 192 |
| Diferencia de `FRIJOL-1K` | **0** | 100 − 30 − 0 + 0 = 70 |
| Diferencia de `ACEITE-900` | **0** | 48 − 6 − 3 + 0 = 39 |
| **Efectivo esperado** | **1 945.40** | 825.40 + 620.00 de contado + 500.00 de cobro |
| **Diferencia de efectivo** | **0.00** | 1 945.40 entregado − 1 945.40 esperado |

**La venta a crédito (621.00) NO entra al efectivo esperado.** Si el panel dice
2 566.40, está sumando el crédito y eso es un defecto. Si dice 1 445.40, no está
sumando el cobro.

**Cierra la liquidación.** Tiene que dejarte, porque la cola está en cero y la
cuarentena vacía.

### ✅ Punto de control 7 — el camión en cero y la bodega completa

El cierre hace dos cosas en la misma transacción: mueve el retorno de vuelta a la
bodega (`tipo = 'retorno'`) y después deja el camión **exactamente en cero**. Si
el conteo cuadró, ese segundo paso no tiene nada que ajustar.

Panel → Inventario, **camión** del vendedor: los tres productos en **0**.

Panel → Inventario, **bodega**:

| SKU | Tiene que decir | De dónde sale |
|---|---|---|
| `SOPA-70G` | **432** | 240 que quedaron + 192 del retorno |
| `FRIJOL-1K` | **170** | 100 + 70 |
| `ACEITE-900` | **87** | 48 + 39 |

Y en Panel → Inventario → movimientos tiene que haber un asiento `retorno` por
producto. El libro mayor es append-only: ahí queda el rastro de los dos pasos.

---

## 7. Las dos pruebas negativas, que valen tanto como las positivas

Un sistema que solo pasa el camino feliz no está probado. Dos minutos más:

**A. Que el cierre se niegue con operaciones pendientes.** Abre otra liquidación
(carga nueva, otro día operativo), haz UNA venta en el teléfono sin sincronizar, e
intenta cerrar. **Tiene que negarse** nombrando el equipo y cuántas operaciones le
faltan. Eso es lo que impide firmar un arqueo contra una cifra que va a moverse.

**B. Que una carga nueva se niegue con el día anterior abierto.** Con la
liquidación de A sin cerrar, intenta confirmar otra carga para el mismo vendedor.
**Tiene que negarse** (§2.3), y tiene que dejarte forzarla escribiendo el motivo.
Después revisa:

```sql
SELECT accion, motivo, datos_despues FROM auditoria WHERE entidad = 'carga';
```

El motivo tiene que estar ahí. Y **no** en el teléfono:

```sql
SELECT payload FROM change_log WHERE entidad = 'carga' ORDER BY cursor DESC LIMIT 1;
```

---

## El veredicto

Si los siete puntos de control dan exactamente las cifras de arriba y las dos
pruebas negativas se niegan como deben, **el sistema cuadra de punta a punta**: el
inventario baja y sube donde debe, el dinero se separa de contado y crédito como
debe, y lo que el teléfono hizo sin señal llegó completo.

Lo que **este simulacro no prueba**, y conviene tenerlo presente al dar el salto:

- **La impresora.** Los bytes del ticket están verificados (59 pruebas) pero el
  transporte Bluetooth no existe todavía. El ticket de este simulacro es la vista
  previa en pantalla.
- **Ocho teléfonos a la vez.** Esto es uno. La cola con `FOR UPDATE SKIP LOCKED` y
  la idempotencia están probadas con pruebas de caos, no con ocho aparatos reales.
- **Catorce días seguidos.** El piloto es el que encuentra lo que se acumula:
  rangos de folio que se agotan, credenciales que caducan, días sucios.

### Si algo no cuadra

Anota **el número que salió y el que esperabas**, y en qué punto de control. Con
esas dos cifras el defecto se localiza en minutos; con «no cuadró» no.
