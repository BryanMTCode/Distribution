/// El carrito de una visita. Lógica pura, sin Flutter y sin SQLite.
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL PRECIO NO ES UN PARÁMETRO
/// ─────────────────────────────────────────────────────────────────────────
/// Regla de negocio cerrada (ADR 0002 §7): **el vendedor no otorga descuentos
/// en la calle.** El precio unitario es el de la lista del cliente, sin
/// excepción, sin campo editable y sin flujo de autorización.
///
/// Eso no se implementa con una validación. Se implementa quitando el
/// parámetro: [Carrito.agregar] recibe una [PresentacionVendible] —que trae su
/// precio ya resuelto del catálogo— y una cantidad. **No hay forma de pasarle
/// un precio.** Una validación se puede saltar con un `if` mal puesto seis
/// meses después; un parámetro que no existe, no.
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL CAMIÓN NO DA CRÉDITO DE MERCANCÍA
/// ─────────────────────────────────────────────────────────────────────────
/// El carrito descuenta contra lo que queda **físicamente** en el camión, y
/// cuenta lo que ya está en el propio carrito. Vender 30 piezas cuando quedan
/// 24 no es un error de captura que se arregla en la oficina: es un camión que
/// llega a la siguiente tienda sin lo que prometió. Por el principio §0.2 —el
/// almacén del camión tiene un solo dueño— este cálculo es exacto y no admite
/// carrera con nadie.
library;

import 'credito.dart';
import 'dinero.dart';
import 'precio.dart';

/// Una presentación que se puede vender: producto + unidad + su precio ya
/// resuelto para la lista del cliente.
///
/// Sale del catálogo local. Si no hay precio para la lista del cliente, esta
/// presentación **no existe** para esa visita: no se construye un objeto con
/// precio cero, porque un renglón de $0.00 en un ticket es peor que un producto
/// que no aparece.
class PresentacionVendible {
  const PresentacionVendible({
    required this.productoId,
    required this.sku,
    required this.nombre,
    required this.unidadCodigo,
    required this.factor,
    required this.precio,
    required this.listaPreciosId,
    required this.listaPreciosVersion,
    this.tasaIva = 0,
    this.esDefault = false,
    this.codigoBarras,
  });

  final String productoId;
  final String sku;
  final String nombre;

  /// 'PZA', 'CAJA'.
  final String unidadCodigo;

  /// Cuántas unidades base sale del camión por cada una de estas.
  final Factor factor;

  /// El precio de la lista del cliente. Rígido.
  final Precio precio;

  final String listaPreciosId;

  /// Viaja en la venta para que el servidor sepa con qué versión de la lista se
  /// vendió, sin comparar importes uno a uno.
  final int listaPreciosVersion;

  /// Abarrotes: 0 o 0.16. Se guarda por producto, no por línea.
  final double tasaIva;

  final bool esDefault;
  final String? codigoBarras;

  /// Llave de la línea del carrito. Dos presentaciones del mismo producto son
  /// **líneas distintas**: 2 cajas y 3 piezas de la misma sopa se imprimen por
  /// separado, como las pide el cliente.
  String get llave => '$productoId|$unidadCodigo';

  @override
  String toString() => '$sku $unidadCodigo @ ${precio.texto}';
}

/// Una línea del carrito.
class LineaCarrito {
  const LineaCarrito({required this.presentacion, required this.cantidad});

  final PresentacionVendible presentacion;
  final Cantidad cantidad;

  String get llave => presentacion.llave;

  /// `cantidad × precio`, redondeado una sola vez. Ver [importeDeLinea].
  Dinero get importe => importeDeLinea(cantidad, presentacion.precio);

  /// Lo que sale del camión por esta línea, en unidades base.
  Cantidad get enUnidadesBase => cantidadBase(cantidad, presentacion.factor);

  LineaCarrito conCantidad(Cantidad nueva) =>
      LineaCarrito(presentacion: presentacion, cantidad: nueva);
}

/// Por qué no se pudo agregar o subir una cantidad.
enum MotivoRechazo {
  /// Cantidad cero o negativa.
  cantidadInvalida('cantidad_invalida'),

  /// No queda suficiente en el camión.
  sinExistencia('sin_existencia'),

  /// El camión no trae ese producto en la carga del día.
  noVaEnLaCarga('no_va_en_la_carga');

  const MotivoRechazo(this.codigo);

  final String codigo;
}

/// Resultado de tocar el carrito. Nunca lanza por una cantidad que el vendedor
/// tecleó: se le dice qué pasó y cuánto sí se puede.
class ResultadoCarrito {
  const ResultadoCarrito.ok(this.carrito)
      : aceptado = true,
        motivo = null,
        disponibleEnCamion = null;

  const ResultadoCarrito.rechazado(
    this.carrito,
    this.motivo, {
    this.disponibleEnCamion,
  }) : aceptado = false;

  final bool aceptado;
  final Carrito carrito;
  final MotivoRechazo? motivo;

  /// Cuánto sí cabe, en la unidad que se intentó vender. Es lo que se le
  /// ofrece al vendedor: "solo quedan 4 cajas".
  final Cantidad? disponibleEnCamion;
}

/// Lo que el camión trae hoy, en unidades base por producto.
///
/// Un producto ausente del mapa no es "cantidad desconocida": es un producto
/// que **no va en la carga**, y por lo tanto no se puede vender. Tratarlo como
/// desconocido y permitirlo sería devolver el descuadre a la liquidación del
/// final del día, que es donde ya no se puede investigar.
class ExistenciasCamion {
  const ExistenciasCamion(this._porProducto);

  const ExistenciasCamion.vacio() : _porProducto = const {};

  final Map<String, Cantidad> _porProducto;

  bool trae(String productoId) => _porProducto.containsKey(productoId);

  Cantidad disponible(String productoId) =>
      _porProducto[productoId] ?? Cantidad.cero;

  bool get estaVacio => _porProducto.isEmpty;
}

/// El carrito de una visita. Inmutable: cada operación devuelve uno nuevo.
///
/// Inmutable a propósito. El carrito es lo que se va a convertir en un
/// documento fiscalmente irrepetible y en un movimiento de inventario; que dos
/// partes de la interfaz puedan mutarlo a la vez es la clase de bug que produce
/// un ticket con una línea de más.
class Carrito {
  const Carrito({this.lineas = const [], this.aCredito = false});

  final List<LineaCarrito> lineas;

  /// A crédito o de contado. Cambia la evaluación, no los importes.
  final bool aCredito;

  bool get estaVacio => lineas.isEmpty;
  int get cuantasLineas => lineas.length;

  /// Suma de los importes de las líneas. Cada línea ya viene redondeada a
  /// centavos, así que la suma es exacta y no vuelve a redondear.
  Dinero get subtotal =>
      lineas.fold(Dinero.cero, (acumulado, l) => acumulado + l.importe);

  /// Los precios de lista de abarrotes ya incluyen IVA cuando aplica, así que
  /// el impuesto no se suma encima: el total es el subtotal.
  ///
  /// Se deja el getter porque `ventas` tiene la columna y el CHECK del servidor
  /// exige `total = subtotal - descuento + impuestos`. Cuando entre el CFDI
  /// habrá que desglosar, y entonces este es el único lugar que cambia.
  Dinero get impuestos => Dinero.cero;

  /// Sin descuento porque no hay descuentos (ADR 0002 §7).
  Dinero get descuento => Dinero.cero;

  Dinero get total => subtotal - descuento + impuestos;

  /// Cuántas unidades base de este producto ya están comprometidas en el
  /// carrito, sumando todas sus presentaciones. Dos cajas y tres piezas de la
  /// misma sopa compiten por la misma existencia.
  Cantidad comprometidoDe(String productoId) => lineas
      .where((l) => l.presentacion.productoId == productoId)
      .fold(Cantidad.cero, (acumulado, l) => acumulado + l.enUnidadesBase);

  LineaCarrito? lineaDe(String llave) {
    for (final l in lineas) {
      if (l.llave == llave) return l;
    }
    return null;
  }

  Cantidad cantidadDe(String llave) => lineaDe(llave)?.cantidad ?? Cantidad.cero;

  /// Agrega o **suma** a una línea existente.
  ///
  /// Sumar y no reemplazar es lo que hace el vendedor: pasa el producto, el
  /// cliente pide otro, lo pasa otra vez.
  ResultadoCarrito agregar(
    PresentacionVendible presentacion,
    Cantidad cantidad, {
    required ExistenciasCamion existencias,
  }) {
    if (cantidad.milesimos <= 0) {
      return ResultadoCarrito.rechazado(this, MotivoRechazo.cantidadInvalida);
    }
    return fijarCantidad(
      presentacion,
      cantidadDe(presentacion.llave) + cantidad,
      existencias: existencias,
    );
  }

  /// Deja la línea en exactamente esta cantidad. Cero la quita.
  ResultadoCarrito fijarCantidad(
    PresentacionVendible presentacion,
    Cantidad cantidad, {
    required ExistenciasCamion existencias,
  }) {
    if (cantidad.milesimos < 0) {
      return ResultadoCarrito.rechazado(this, MotivoRechazo.cantidadInvalida);
    }
    if (cantidad.esCero) return ResultadoCarrito.ok(quitar(presentacion.llave));

    if (!existencias.trae(presentacion.productoId)) {
      return ResultadoCarrito.rechazado(this, MotivoRechazo.noVaEnLaCarga);
    }

    // Lo comprometido por las OTRAS líneas del mismo producto: al fijar esta
    // línea, su cantidad anterior se libera.
    final otrasLineas = lineas.where((l) =>
        l.presentacion.productoId == presentacion.productoId &&
        l.llave != presentacion.llave);
    final comprometidoPorOtras = otrasLineas.fold(
      Cantidad.cero,
      (acumulado, l) => acumulado + l.enUnidadesBase,
    );

    final libre =
        existencias.disponible(presentacion.productoId) - comprometidoPorOtras;
    final pedido = cantidadBase(cantidad, presentacion.factor);

    if (pedido > libre) {
      return ResultadoCarrito.rechazado(
        this,
        MotivoRechazo.sinExistencia,
        // Lo que sí cabe, expresado en la unidad que el vendedor está usando:
        // decirle "quedan 24 piezas" cuando está vendiendo cajas de 12 lo
        // obliga a dividir de cabeza frente al cliente.
        disponibleEnCamion: _cuantasCaben(libre, presentacion.factor),
      );
    }

    final nueva = LineaCarrito(presentacion: presentacion, cantidad: cantidad);
    final existente = lineaDe(presentacion.llave);
    final actualizadas = existente == null
        ? [...lineas, nueva]
        : lineas.map((l) => l.llave == nueva.llave ? nueva : l).toList();

    return ResultadoCarrito.ok(
      Carrito(lineas: actualizadas, aCredito: aCredito),
    );
  }

  Carrito quitar(String llave) => Carrito(
        lineas: lineas.where((l) => l.llave != llave).toList(),
        aCredito: aCredito,
      );

  Carrito vaciar() => Carrito(aCredito: aCredito);

  Carrito conFormaDePago({required bool aCredito}) =>
      Carrito(lineas: lineas, aCredito: aCredito);

  /// Si esta venta procede con el crédito del cliente.
  ///
  /// De contado siempre procede, aunque el cliente deba hasta la camisa: negarla
  /// no cobra la deuda vieja y sí pierde la venta nueva.
  ResultadoCredito evaluar(EstadoCredito credito) =>
      evaluarVenta(credito, total, aCredito: aCredito);

  /// Cuántas unidades enteras de esta presentación caben en `libre`.
  ///
  /// Trunca hacia abajo, siempre: ofrecer "3.7 cajas" no sirve, y ofrecer 4
  /// cuando solo alcanzan 3 mandaría al vendedor a chocar con el mismo límite.
  static Cantidad _cuantasCaben(Cantidad libre, Factor factor) {
    if (libre.milesimos <= 0 || factor.diezmilesimos <= 0) return Cantidad.cero;
    // libre (10⁻³) / factor (10⁻⁴) → unidades. Se escala para no perder la
    // fracción antes de truncar a milésimos.
    final milesimos = (libre.milesimos * 10000) ~/ factor.diezmilesimos;
    return Cantidad.deTexto(
      '${milesimos ~/ 1000}.${(milesimos % 1000).toString().padLeft(3, '0')}',
    );
  }
}
