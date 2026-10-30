# Venderla como producto

> «¿Crees que esta app serviría para venderla?» (octubre de 2026)

**Sí tiene valor para venderse, pero todavía no está lista como producto.** Hoy es un sistema hecho
a la medida de Distribuciones SE. Venderla a otras distribuidoras es posible, con trabajo de por
medio. Este documento junta la evaluación, lo que falta, cómo se vendería y un plan por etapas. Lo
de la Play Store está aparte, en [PLAY-STORE.md](PLAY-STORE.md).

---

## 1. Lo que tiene a favor

- **Resuelve un problema real y difícil.** Es venta en ruta desde el camión que funciona **sin señal**:
  carga, venta, corte, mermas y la cuenta de cada vendedor. Hacer bien la parte sin señal es lo que
  más le cuesta a cualquiera, y aquí ya está resuelto.
- **Está probada en operación de verdad**, no es una maqueta. Los vendedores la usan todos los días,
  y cada problema real (cortes, tiendas, sincronización) se arregló y quedó con su prueba.
- **Está cuidada por dentro.** Tiene unas **2,779 pruebas automáticas** (1639 del servidor, 742 de
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
2. **La app en la Play Store** *(mediano)*. Ver [PLAY-STORE.md](PLAY-STORE.md). Se puede empezar ya
   para tus propios teléfonos, con la prueba interna.
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
