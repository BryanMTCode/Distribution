/// Serialización canónica y hash de payloads — mitad Dart del contrato.
///
/// Espejo de `server/app/domain/canonico.py`. Las reglas están en
/// `contracts/README.md` §1 y los vectores en `contracts/canonical_vectors.json`,
/// que esta implementación debe pasar íntegros.
///
/// **Trampa principal:** `String.compareTo` de Dart ordena por unidades UTF-16,
/// no por punto de código. Para claves fuera del plano básico (emoji) eso da un
/// orden DISTINTO al de Python: 'ﬀ' (U+FB00) va antes que '🔒' (U+1F512) por
/// punto de código, pero después por UTF-16. Por eso aquí se compara por runas.
/// El vector `orden_claves_fuera_del_plano_basico` fija justo ese caso.
///
/// Lo valida `test/canonico_test.dart` contra los vectores compartidos, que son
/// los MISMOS que ejecuta la suite de Python.
library;

import 'dart:convert';

import 'package:crypto/crypto.dart';

const int escalaDinero = 2;
const int escalaCantidad = 3;

class PayloadNoCanonico implements Exception {
  final String mensaje;
  PayloadNoCanonico(this.mensaje);
  @override
  String toString() => 'PayloadNoCanonico: $mensaje';
}

/// Compara por punto de código Unicode, como `sorted()` de Python.
int compararPorPuntoDeCodigo(String a, String b) {
  final ra = a.runes.toList();
  final rb = b.runes.toList();
  final n = ra.length < rb.length ? ra.length : rb.length;
  for (var i = 0; i < n; i++) {
    if (ra[i] != rb[i]) return ra[i] < rb[i] ? -1 : 1;
  }
  return ra.length.compareTo(rb.length);
}

/// Dinero como string con exactamente 2 decimales: '250.00', '-125.50'.
///
/// Recibe String o int a propósito: aceptar `double` aquí sería reintroducir
/// justo el tipo que pierde centavos. Usa el paquete `decimal` para operar.
String formatearDinero(Object valor) => _formatearEscalaFija(valor, escalaDinero, 'dinero');

/// Cantidad como string con exactamente 3 decimales: '12.000', '1.375'.
String formatearCantidad(Object valor) =>
    _formatearEscalaFija(valor, escalaCantidad, 'cantidad');

String _formatearEscalaFija(Object valor, int escala, String etiqueta) {
  if (valor is double) {
    throw PayloadNoCanonico('el $etiqueta nunca se representa como double');
  }
  final texto = valor.toString();
  final negativo = texto.startsWith('-');
  final absoluto = negativo ? texto.substring(1) : texto;
  final partes = absoluto.split('.');
  if (partes.length > 2 || partes[0].isEmpty || !RegExp(r'^\d+$').hasMatch(partes[0])) {
    throw PayloadNoCanonico('$etiqueta mal formado: $texto');
  }
  var decimales = partes.length == 2 ? partes[1] : '';
  if (!RegExp(r'^\d*$').hasMatch(decimales)) {
    throw PayloadNoCanonico('$etiqueta mal formado: $texto');
  }
  if (decimales.length > escala) {
    throw PayloadNoCanonico(
        '$etiqueta con más de $escala decimales: $texto (redondea antes)');
  }
  decimales = decimales.padRight(escala, '0');
  return '${negativo ? '-' : ''}${partes[0]}.$decimales';
}

/// RFC 3339 en UTC con exactamente 3 decimales y sufijo 'Z'.
String formatearInstante(DateTime momento) {
  final utc = momento.toUtc();
  String dos(int n) => n.toString().padLeft(2, '0');
  final ms = utc.millisecond.toString().padLeft(3, '0');
  return '${utc.year.toString().padLeft(4, '0')}-${dos(utc.month)}-${dos(utc.day)}'
      'T${dos(utc.hour)}:${dos(utc.minute)}:${dos(utc.second)}.${ms}Z';
}

String _escapar(String texto) {
  final b = StringBuffer('"');
  for (final unidad in texto.codeUnits) {
    switch (unidad) {
      case 0x22:
        b.write(r'\"');
      case 0x5C:
        b.write(r'\\');
      case 0x08:
        b.write(r'\b');
      case 0x0C:
        b.write(r'\f');
      case 0x0A:
        b.write(r'\n');
      case 0x0D:
        b.write(r'\r');
      case 0x09:
        b.write(r'\t');
      default:
        if (unidad < 0x20) {
          b.write('\\u${unidad.toRadixString(16).padLeft(4, '0')}');
        } else {
          // Las unidades suplentes se reemiten tal cual y el par se reconstruye
          // al codificar en UTF-8. Acentos y emoji van literales.
          b.writeCharCode(unidad);
        }
    }
  }
  b.write('"');
  return b.toString();
}

String _canonizar(Object? valor, String ruta) {
  if (valor is bool) return valor ? 'true' : 'false';
  if (valor is String) return _escapar(valor);
  if (valor is int) return valor.toString();
  if (valor is double) {
    throw PayloadNoCanonico(
        '$ruta: los double están prohibidos en el payload; usa string con escala fija');
  }
  if (valor is DateTime) {
    throw PayloadNoCanonico('$ruta: convierte el DateTime con formatearInstante()');
  }
  if (valor is Map) {
    final claves = valor.keys.map((k) {
      if (k is! String) throw PayloadNoCanonico('$ruta: las claves deben ser String');
      return k;
    }).toList()
      ..sort(compararPorPuntoDeCodigo);
    final partes = <String>[];
    for (final clave in claves) {
      final interno = valor[clave];
      if (interno == null) continue; // regla 2: las claves nulas se omiten
      partes.add('${_escapar(clave)}:${_canonizar(interno, '$ruta.$clave')}');
    }
    return '{${partes.join(',')}}';
  }
  if (valor is List) {
    // El orden de un arreglo SÍ es significativo.
    final partes = <String>[];
    for (var i = 0; i < valor.length; i++) {
      partes.add(_canonizar(valor[i], '$ruta[$i]'));
    }
    return '[${partes.join(',')}]';
  }
  if (valor == null) {
    throw PayloadNoCanonico('$ruta: null solo es válido como valor de una clave');
  }
  throw PayloadNoCanonico('$ruta: tipo no canonizable (${valor.runtimeType})');
}

/// Forma canónica del payload. Determinista en Dart y en Python.
String aTextoCanonico(Map<String, Object?> payload) => _canonizar(payload, r'$');

/// SHA-256 hexadecimal en minúsculas de la forma canónica.
String hashPayload(Map<String, Object?> payload) =>
    sha256.convert(utf8.encode(aTextoCanonico(payload))).toString();
