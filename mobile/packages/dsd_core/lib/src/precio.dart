/// Precio unitario, cantidad y factor de conversión: exactos, en enteros.
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ EL PRECIO NO ES UN `Dinero`
/// ─────────────────────────────────────────────────────────────────────────
/// `Dinero` lleva **centavos**, que es la escala en la que se cobra. Pero el
/// precio por pieza sale de dividir el de la caja: una caja de 24 a $296.00 da
/// **$12.3333** por pieza. Si ese precio se guardara en centavos ($12.33), la
/// caja completa valdría $295.92 y el vendedor tendría que explicarle al
/// cliente por qué el ticket no cuadra con la lista.
///
/// Así que el precio lleva **cuatro** decimales —la misma escala que
/// `precios.precio` en PostgreSQL, `numeric(14,4)`— y el redondeo a centavos
/// ocurre **una sola vez**, en el importe de la línea.
///
/// Las tres clases son enteros por dentro. Dart no tiene decimal nativo, y su
/// `double` pierde exactitud justo donde no se puede perder.
library;

import 'dinero.dart';

/// Parsea un decimal de escala fija a entero, sin pasar por `double`.
///
/// Sin `trim()` a propósito, igual que `Dinero`: el contrato define la forma
/// exacta, y tolerar un espacio suelto aceptaría un payload que en Python
/// hashearía distinto, escondiendo justo la divergencia que los vectores
/// buscan.
int _aEntero(
  String texto,
  int decimales,
  String ejemplo,
  String queEs, {
  bool admiteSigno = false,
}) {
  final patron = admiteSigno
      ? '^(-?)(\\d+)\\.(\\d{$decimales})\$'
      : '^()(\\d+)\\.(\\d{$decimales})\$';
  final coincidencia = RegExp(patron).firstMatch(texto);
  if (coincidencia == null) {
    throw FormatException("$queEs mal formado: '$texto' (se esperaba p. ej. '$ejemplo')");
  }
  final escala = <int>[1, 10, 100, 1000, 10000][decimales];
  final signo = coincidencia.group(1) == '-' ? -1 : 1;
  return signo *
      (int.parse(coincidencia.group(2)!) * escala +
          int.parse(coincidencia.group(3)!));
}

String _aTexto(int valor, int decimales) {
  final escala = <int>[1, 10, 100, 1000, 10000][decimales];
  // El signo se saca aparte: con `valor ~/ escala` solo, un −0.500 daría "0.500"
  // —los enteros son cero y el truncado se lo come— y la cantidad cambiaría de
  // signo al convertirse a texto.
  final signo = valor < 0 ? '-' : '';
  final magnitud = valor.abs();
  final enteros = magnitud ~/ escala;
  final resto = (magnitud % escala).toString().padLeft(decimales, '0');
  return '$signo$enteros.$resto';
}

/// Precio unitario exacto. Cuatro decimales, inmutable.
class Precio implements Comparable<Precio> {
  const Precio._(this.diezmilesimos);

  /// Desde el string del contrato: '12.3333', '0.0000'.
  factory Precio.deTexto(String texto) =>
      Precio._(_aEntero(texto, 4, '12.3333', 'precio'));

  /// Desde el REAL de SQLite. El catálogo es zona espejo: llega del servidor
  /// con 4 decimales y cruza el límite del dominio una sola vez, aquí.
  factory Precio.deBase(num valor) => Precio.deTexto(valor.toStringAsFixed(4));

  factory Precio.dePesos(int pesos) => Precio._(pesos * 10000);

  static const cero = Precio._(0);

  final int diezmilesimos;

  bool get esCero => diezmilesimos == 0;

  /// El string del contrato. Siempre con cuatro decimales.
  String get texto => _aTexto(diezmilesimos, 4);

  /// Para mostrar en pantalla: dos decimales cuando el precio es exacto en
  /// centavos, cuatro cuando no. Mostrar '12.33' donde el precio es 12.3333
  /// sería mentirle al vendedor sobre lo que va a cobrar por caja.
  String get textoCorto =>
      diezmilesimos % 100 == 0 ? _aTexto(diezmilesimos ~/ 100, 2) : texto;

  @override
  int compareTo(Precio otro) => diezmilesimos.compareTo(otro.diezmilesimos);

  @override
  bool operator ==(Object other) =>
      other is Precio && other.diezmilesimos == diezmilesimos;

  @override
  int get hashCode => diezmilesimos.hashCode;

  @override
  String toString() => texto;
}

/// Cantidad vendida. Tres decimales, inmutable.
///
/// Tres y no cero porque el esquema admite unidades fraccionables (granel). En
/// abarrotes secos las cantidades son enteras, pero el tipo no puede asumirlo:
/// el día que entre una báscula, el redondeo silencioso ya estaría en
/// producción.
class Cantidad implements Comparable<Cantidad> {
  const Cantidad._(this.milesimos);

  /// Admite signo, y eso es deliberado.
  ///
  /// `existencias_camion.cant_actual` **puede quedar negativa**: una merma se
  /// registra aunque el conteo diga que no había (§0.1), y después el camión
  /// marca −12. Si este tipo no supiera leer ese número, la pantalla de merma
  /// reventaría al abrirse justo después del caso para el que existe.
  ///
  /// Que una cantidad concreta no pueda ser negativa —el renglón de una merma, la
  /// línea de un carrito— lo decide quien la valida, no el tipo. Es el mismo
  /// reparto que en `Dinero`, que admite el signo por el saldo a favor.
  factory Cantidad.deTexto(String texto) =>
      Cantidad._(_aEntero(texto, 3, '12.000', 'cantidad', admiteSigno: true));

  factory Cantidad.deBase(num valor) => Cantidad.deTexto(valor.toStringAsFixed(3));

  bool get esNegativa => milesimos < 0;

  factory Cantidad.deEnteros(int unidades) => Cantidad._(unidades * 1000);

  static const cero = Cantidad._(0);

  final int milesimos;

  bool get esCero => milesimos == 0;
  bool get esEntera => milesimos % 1000 == 0;

  /// Cuántas unidades enteras. Para los botones de +1 / −1 del carrito.
  int get enteros => milesimos ~/ 1000;

  Cantidad operator +(Cantidad otra) => Cantidad._(milesimos + otra.milesimos);
  Cantidad operator -(Cantidad otra) => Cantidad._(milesimos - otra.milesimos);
  bool operator >(Cantidad otra) => milesimos > otra.milesimos;
  bool operator >=(Cantidad otra) => milesimos >= otra.milesimos;
  bool operator <(Cantidad otra) => milesimos < otra.milesimos;
  bool operator <=(Cantidad otra) => milesimos <= otra.milesimos;

  /// El string del contrato. Siempre con tres decimales.
  String get texto => _aTexto(milesimos, 3);

  /// Para pantalla: '3' en vez de '3.000'.
  String get textoCorto => esEntera ? enteros.toString() : texto;

  @override
  int compareTo(Cantidad otra) => milesimos.compareTo(otra.milesimos);

  @override
  bool operator ==(Object other) => other is Cantidad && other.milesimos == milesimos;

  @override
  int get hashCode => milesimos.hashCode;

  @override
  String toString() => texto;
}

/// Cuántas unidades base tiene una presentación: 1 CAJA = 24.0000 PZA.
///
/// Tipo propio y no un `double` porque es el número que traduce entre lo que se
/// vende y lo que sale del camión. Un error aquí no se ve en el ticket: se ve
/// en la liquidación, al final del día, como un descuadre que nadie sabe de
/// dónde salió.
class Factor implements Comparable<Factor> {
  const Factor._(this.diezmilesimos);

  factory Factor.deTexto(String texto) =>
      Factor._(_aEntero(texto, 4, '24.0000', 'factor'));

  factory Factor.deBase(num valor) => Factor.deTexto(valor.toStringAsFixed(4));

  factory Factor.deEnteros(int unidades) => Factor._(unidades * 10000);

  /// La unidad base consigo misma: 1 PZA = 1 PZA.
  static const uno = Factor._(10000);

  final int diezmilesimos;

  bool get esUno => diezmilesimos == 10000;
  bool get esValido => diezmilesimos > 0;

  String get texto => _aTexto(diezmilesimos, 4);

  /// Para pantalla: '24' en vez de '24.0000'.
  String get textoCorto =>
      diezmilesimos % 10000 == 0 ? (diezmilesimos ~/ 10000).toString() : texto;

  @override
  int compareTo(Factor otro) => diezmilesimos.compareTo(otro.diezmilesimos);

  @override
  bool operator ==(Object other) =>
      other is Factor && other.diezmilesimos == diezmilesimos;

  @override
  int get hashCode => diezmilesimos.hashCode;

  @override
  String toString() => texto;
}

/// El importe de una partida: `cantidad × precio`, redondeado **una sola vez**.
///
/// Espejo exacto de `server/app/domain/importes.py`. Los tres cálculos —este,
/// el del servidor al recibir el sobre, y el CHECK de PostgreSQL— tienen que
/// dar el mismo centavo. Si no, una venta legítima cae en revisión por un
/// redondeo, y en cuanto eso pasa dos o tres veces la bandera de revisión deja
/// de significar algo y se ignora.
///
/// **Medio hacia arriba**, igual que `ROUND()` de PostgreSQL sobre `numeric`.
/// Se hace con enteros para no depender del modo de redondeo de Dart.
///
/// No recibe descuento, y no es un olvido: el vendedor no otorga descuentos en
/// la calle (ADR 0002 §7). No existe el parámetro para que no haya dónde
/// escribirlo.
Dinero importeDeLinea(Cantidad cantidad, Precio precio) {
  // milésimos (10⁻³) × diezmilésimos (10⁻⁴) = 10⁻⁷. A centavos (10⁻²) hay que
  // dividir entre 10⁵. En un entero de 64 bits alcanza para importes absurdos
  // antes de desbordar.
  final producto = cantidad.milesimos * precio.diezmilesimos;
  final centavos = (producto + 50000) ~/ 100000;
  return Dinero.deTexto(_aTexto(centavos, 2));
}

/// Cuántas unidades base salen del camión por esta línea.
///
/// Se congela en la partida porque es lo que se descontó del inventario: si
/// mañana la oficina corrige el factor de la caja, el movimiento de ayer no
/// puede cambiar de tamaño.
Cantidad cantidadBase(Cantidad cantidad, Factor factor) {
  final producto = cantidad.milesimos * factor.diezmilesimos;
  // 10⁻⁷ → 10⁻³ ⇒ dividir entre 10⁴, medio hacia arriba.
  return Cantidad._((producto + 5000) ~/ 10000);
}
