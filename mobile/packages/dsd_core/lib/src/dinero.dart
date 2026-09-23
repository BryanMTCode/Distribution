/// Dinero exacto en el dispositivo.
///
/// Dart no tiene un tipo decimal nativo: su `double` es IEEE-754 y pierde
/// centavos en cuanto se suman importes. En un sistema de cobranza con saldos
/// que se arrastran meses, eso no es una imprecisión teórica.
///
/// Se representa como **centavos en un entero**. Es exacto, rápido, sin
/// dependencias, y convierte sin pérdida desde y hacia el string de 2 decimales
/// que exige el contrato (contracts/README.md §1).
///
/// El entero de Dart en móvil es de 64 bits: alcanza para ~92 billones de pesos.
library;

/// Importe exacto. Inmutable.
///
/// Clase y no `extension type` porque un extension type no puede declarar
/// `toString`, y un importe que se imprime como un entero de centavos es una
/// trampa esperando en el primer log de producción.
class Dinero implements Comparable<Dinero> {
  const Dinero._(this.centavos);

  /// Desde el string del contrato: '250.00', '-125.50'.
  factory Dinero.deTexto(String texto) {
    // Sin trim a propósito: el contrato define la forma exacta del importe.
    // Tolerar un espacio suelto aceptaría un payload que en Python hashearía
    // distinto, escondiendo justo la divergencia que los vectores buscan.
    final coincidencia = RegExp(r'^(-?)(\d+)\.(\d{2})$').firstMatch(texto);
    if (coincidencia == null) {
      throw FormatException(
        "importe mal formado: '$texto' (se esperaba p. ej. '250.00')",
      );
    }
    final signo = coincidencia.group(1) == '-' ? -1 : 1;
    final enteros = int.parse(coincidencia.group(2)!);
    final decimales = int.parse(coincidencia.group(3)!);
    return Dinero._(signo * (enteros * 100 + decimales));
  }

  factory Dinero.dePesos(int pesos) => Dinero._(pesos * 100);

  static const cero = Dinero._(0);

  final int centavos;

  Dinero operator +(Dinero otro) => Dinero._(centavos + otro.centavos);
  Dinero operator -(Dinero otro) => Dinero._(centavos - otro.centavos);
  bool operator >(Dinero otro) => centavos > otro.centavos;
  bool operator >=(Dinero otro) => centavos >= otro.centavos;
  bool operator <(Dinero otro) => centavos < otro.centavos;
  bool operator <=(Dinero otro) => centavos <= otro.centavos;

  Dinero operator -() => Dinero._(-centavos);

  bool get esNegativo => centavos < 0;
  bool get esCero => centavos == 0;

  Dinero get maximoConCero => centavos > 0 ? this : cero;

  /// El string del contrato. Siempre con dos decimales.
  String get texto {
    final signo = centavos < 0 ? '-' : '';
    final absoluto = centavos.abs();
    final enteros = absoluto ~/ 100;
    final decimales = (absoluto % 100).toString().padLeft(2, '0');
    return '$signo$enteros.$decimales';
  }

  @override
  int compareTo(Dinero other) => centavos.compareTo(other.centavos);

  @override
  bool operator ==(Object other) => other is Dinero && other.centavos == centavos;

  @override
  int get hashCode => centavos.hashCode;

  @override
  String toString() => texto;
}
