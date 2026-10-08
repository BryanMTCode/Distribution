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

import 'dinero.dart';
import 'forma_de_pago.dart';
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
    this.unidadBaseCodigo = 'PZA',
    this.tasaIva = 0,
    this.esDefault = false,
    this.codigoBarras,
  });

  final String productoId;
  final String sku;
  final String nombre;

  /// 'PZA', 'CAJA'.
  final String unidadCodigo;

  /// La unidad en la que se lleva el inventario del camión: 'PZA'. Sirve para
  /// decirle al vendedor "2 cajas y 6 **piezas**" cuando no alcanza.
  final String unidadBaseCodigo;

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
        disponibleEnCamion = null,
        desglose = null;

  const ResultadoCarrito.rechazado(
    this.carrito,
    this.motivo, {
    this.disponibleEnCamion,
    this.desglose,
  }) : aceptado = false;

  final bool aceptado;
  final Carrito carrito;
  final MotivoRechazo? motivo;

  /// Cuánto sí cabe, en la unidad que se intentó vender. Puede traer fracción.
  final Cantidad? disponibleEnCamion;

  /// Lo mismo, partido en unidades enteras y resto.
  ///
  /// Es lo que se le dice al vendedor. Probado en campo (POCO M5s, septiembre
  /// 2026): **"2 cajas y 6 piezas" se entiende, "2.500 cajas" no.** Media caja
  /// no existe en un camión; lo que existe son 2 cajas y 6 piezas sueltas, y eso
  /// es exactamente lo que el vendedor le va a decir al cliente.
  final DesgloseDisponible? desglose;
}

/// Lo que cabe, partido como se carga físicamente: unidades enteras y sueltas.
class DesgloseDisponible {
  const DesgloseDisponible({
    required this.enteras,
    required this.sueltasEnBase,
    required this.unidadCodigo,
    required this.unidadBaseCodigo,
  });

  /// Cuántas presentaciones completas caben (2 cajas).
  final int enteras;

  /// Lo que sobra, en unidad base (6 piezas).
  final Cantidad sueltasEnBase;

  /// 'CAJA'. Cuando la presentación ES la unidad base, `enteras` ya lo dice todo
  /// y `sueltasEnBase` es cero.
  final String unidadCodigo;

  /// 'PZA'.
  final String unidadBaseCodigo;

  bool get hayEnteras => enteras > 0;
  bool get haySueltas => !sueltasEnBase.esCero;
  bool get nadaCabe => !hayEnteras && !haySueltas;
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
  const Carrito({
    this.lineas = const [],
    this.formaDePago = FormaDePago.efectivo,
    this.referenciaPago,
  });

  final List<LineaCarrito> lineas;

  /// Cómo paga el cliente, en el acto: no hay crédito (ADR 0002 §81). Cambia lo
  /// que el vendedor entrega en el corte, no los importes.
  final FormaDePago formaDePago;

  /// La clave de rastreo o folio de la transferencia, si el cliente la da. Es lo
  /// que la oficina busca en el estado de cuenta.
  final String? referenciaPago;

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
        desglose: _desglosar(libre, presentacion),
      );
    }

    final nueva = LineaCarrito(presentacion: presentacion, cantidad: cantidad);
    final existente = lineaDe(presentacion.llave);
    final actualizadas = existente == null
        ? [...lineas, nueva]
        : lineas.map((l) => l.llave == nueva.llave ? nueva : l).toList();

    return ResultadoCarrito.ok(_con(actualizadas));
  }

  Carrito _con(List<LineaCarrito> nuevas) =>
      Carrito(lineas: nuevas, formaDePago: formaDePago, referenciaPago: referenciaPago);

  Carrito quitar(String llave) =>
      _con(lineas.where((l) => l.llave != llave).toList());

  /// Vacío y en efectivo: la forma de pago es de ESTE cliente, y el siguiente
  /// empieza de nuevo.
  Carrito vaciar() => const Carrito();

  /// La referencia solo existe con transferencia: al volver a efectivo se borra,
  /// para que no viaje un folio de banco pegado a una venta en efectivo.
  Carrito conFormaDePago(FormaDePago forma, {String? referencia}) => Carrito(
        lineas: lineas,
        formaDePago: forma,
        referenciaPago: forma == FormaDePago.transferencia ? _limpia(referencia) : null,
      );

  static String? _limpia(String? texto) {
    final t = texto?.trim() ?? '';
    return t.isEmpty ? null : t;
  }

  /// Parte lo que cabe como se carga físicamente: presentaciones completas y
  /// unidades sueltas.
  ///
  /// Media caja no existe en un camión. Lo que existe son 2 cajas y 6 piezas, y
  /// eso es lo que el vendedor le va a decir al cliente.
  static DesgloseDisponible _desglosar(
    Cantidad libre,
    PresentacionVendible presentacion,
  ) {
    final f = presentacion.factor.diezmilesimos;
    final disponibleBase = libre.milesimos < 0 ? 0 : libre.milesimos;
    // libre está en milésimos de unidad base; el factor en diezmilésimos.
    // enteras = libre_base / factor, truncado.
    final enteras = f <= 0 ? 0 : (disponibleBase * 10000) ~/ (f * 1000);
    final consumidoEnBase = (enteras * f) ~/ 10;
    return DesgloseDisponible(
      enteras: enteras,
      sueltasEnBase: Cantidad.deTexto(
        _milesimosATexto(disponibleBase - consumidoEnBase),
      ),
      unidadCodigo: presentacion.unidadCodigo,
      unidadBaseCodigo: presentacion.unidadBaseCodigo,
    );
  }

  static String _milesimosATexto(int milesimos) {
    final v = milesimos < 0 ? 0 : milesimos;
    return '${v ~/ 1000}.${(v % 1000).toString().padLeft(3, '0')}';
  }

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
