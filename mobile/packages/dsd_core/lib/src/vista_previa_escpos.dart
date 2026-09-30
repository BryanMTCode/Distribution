/// Vista previa: los bytes ESC/POS de vuelta a texto legible.
///
/// ─────────────────────────────────────────────────────────────────────────
/// PARA QUÉ SIRVE ESTO
/// ─────────────────────────────────────────────────────────────────────────
/// La impresora está en otro Estado y no va a llegar pronto. Sin una forma de
/// **ver** el ticket, el diseño se quedaría sin revisar hasta entonces: nadie
/// puede opinar de un arreglo de 800 bytes.
///
/// Este decodificador interpreta el flujo igual que lo haría la impresora
/// —alineación, negritas, tamaño doble, saltos— y devuelve las líneas como
/// saldrían en el papel, con su ancho real. Con eso:
///
/// · La app muestra el ticket en pantalla, en monoespaciado de 32 columnas.
/// · Las pruebas afirman sobre TEXTO y no sobre bytes, así que cuando algo
///   cambia el diff se lee.
/// · El archivo `contracts/ticket_58mm_ejemplo.txt` queda versionado: cualquier
///   cambio de diseño aparece en el diff del repositorio.
///
/// No sustituye probar en el equipo real —la densidad térmica, el papel, el
/// cortador—, pero sí el 90% de lo que se puede equivocar: importes
/// desalineados, nombres cortados a media palabra, acentos rotos, un total que
/// no cabe.
library;

import 'escpos.dart';

/// Una línea del ticket tal como saldría impresa.
class LineaImpresa {
  const LineaImpresa({
    required this.texto,
    required this.alineacion,
    required this.negrita,
    required this.anchoDoble,
    required this.altoDoble,
  });

  /// El contenido, sin relleno.
  final String texto;
  final Alineacion alineacion;
  final bool negrita;
  final bool anchoDoble;
  final bool altoDoble;

  /// Cuántas columnas ocupa realmente en el papel.
  int get columnasOcupadas => texto.length * (anchoDoble ? 2 : 1);

  @override
  String toString() => texto;
}

/// El ticket decodificado.
class VistaPrevia {
  const VistaPrevia({
    required this.lineas,
    required this.columnas,
    required this.tabla,
    required this.desconocidos,
  });

  final List<LineaImpresa> lineas;
  final int columnas;

  /// La tabla que el flujo pidió con `ESC t n`. Si es `null`, el ticket no la
  /// fijó — y entonces la impresora usa la que trae de fábrica, que puede no ser
  /// la que el ticket supone. Eso es un defecto, no un detalle.
  final TablaDeCodigos? tabla;

  /// Comandos que el decodificador no conoció. Se cuentan en vez de ignorarse:
  /// un comando inesperado en medio del flujo suele significar que el generador
  /// escribió un byte de más.
  final List<String> desconocidos;

  /// El ticket como se vería en el papel, con cada línea rellenada a su ancho.
  ///
  /// Esto es lo que se muestra en pantalla y lo que se versiona en el repo.
  String render() {
    final salida = StringBuffer();
    for (final l in lineas) {
      salida.writeln(_rellenar(l));
    }
    return salida.toString();
  }

  /// Igual que [render], pero con un marco que hace visible el ancho del papel.
  ///
  /// Sin el marco, una línea que se desborda dos columnas no se distingue de una
  /// que cabe justo.
  String renderConMarco() {
    final borde = '+${'-' * columnas}+';
    final salida = StringBuffer()..writeln(borde);
    for (final l in lineas) {
      final contenido = _rellenar(l);
      // Una línea más larga que el papel se marca: es un error de diseño que en
      // la impresora se vería como texto continuado en la línea siguiente.
      final marca = l.columnasOcupadas > columnas ? '>' : '|';
      salida.writeln('|${contenido.padRight(columnas)}$marca');
    }
    salida.writeln(borde);
    return salida.toString();
  }

  /// Coloca la línea donde caería en el papel.
  ///
  /// El cálculo usa las columnas que **ocupa**, no los caracteres que tiene: en
  /// tamaño doble cada carácter vale dos columnas, así que 'TOTAL' centrado
  /// arranca en la columna 11 y no en la 13. Usar la longitud del texto
  /// mostraría en la vista previa un centrado que el papel no tiene.
  ///
  /// El texto se deja en una sola anchura porque duplicar los caracteres
  /// ("TT OO TT AA LL") sería ilegible; lo que se respeta es la POSICIÓN.
  String _rellenar(LineaImpresa l) {
    // En ancho doble los caracteres se separan con un espacio. Es la
    // representación en texto de una letra que ocupa dos columnas, y sin ella la
    // vista previa MIENTE: un total alineado a la derecha aparecería a media
    // línea, y quien revise el diseño creería que está mal cuando está bien.
    final visible = l.anchoDoble ? l.texto.split('').join(' ') : l.texto;
    // El margen se calcula con las columnas que ocupa EN EL PAPEL, no con el
    // largo de la representación: el espaciado deja un hueco de menos al final.
    final sobra = columnas - l.columnasOcupadas;
    if (sobra <= 0) return visible;

    return switch (l.alineacion) {
      Alineacion.izquierda => visible,
      Alineacion.centro => '${' ' * (sobra ~/ 2)}$visible',
      Alineacion.derecha => '${' ' * sobra}$visible',
    };
  }

  /// Todo el texto junto, sin formato. Para buscar dentro en una prueba.
  String get texto => lineas.map((l) => l.texto).join('\n');
}

/// Decodifica un flujo ESC/POS.
///
/// Solo entiende los comandos que el generador emite. Cualquier otro se registra
/// en [VistaPrevia.desconocidos] en vez de tragarse en silencio: si el generador
/// empieza a emitir algo que el decodificador no conoce, la vista previa dejaría
/// de corresponder al papel y nadie se enteraría.
VistaPrevia decodificar(
  List<int> bytes, {
  int columnas = columnas58mm,
  List<TablaDeCodigos> tablasConocidas = const [
    TablaDeCodigos.pc437,
    TablaDeCodigos.pc850,
  ],
}) {
  final lineas = <LineaImpresa>[];
  final desconocidos = <String>[];
  final actual = <int>[];

  TablaDeCodigos? tabla;
  var alineacion = Alineacion.izquierda;
  var negrita = false;
  var anchoDoble = false;
  var altoDoble = false;

  void cerrarLinea() {
    lineas.add(
      LineaImpresa(
        texto: _aTexto(actual, tabla ?? TablaDeCodigos.pc437),
        alineacion: alineacion,
        negrita: negrita,
        anchoDoble: anchoDoble,
        altoDoble: altoDoble,
      ),
    );
    actual.clear();
  }

  var i = 0;
  while (i < bytes.length) {
    final b = bytes[i];

    if (b == 0x0A) {
      cerrarLinea();
      i++;
      continue;
    }

    if (b == 0x1B) {
      // ESC
      final siguiente = i + 1 < bytes.length ? bytes[i + 1] : -1;
      switch (siguiente) {
        case 0x40: // ESC @ — reinicio
          alineacion = Alineacion.izquierda;
          negrita = false;
          anchoDoble = false;
          altoDoble = false;
          i += 2;
        case 0x74: // ESC t n — tabla de códigos
          final n = i + 2 < bytes.length ? bytes[i + 2] : -1;
          tabla = tablasConocidas.where((t) => t.comando == n).firstOrNull;
          if (tabla == null) desconocidos.add('ESC t $n (tabla desconocida)');
          i += 3;
        case 0x61: // ESC a n — alineación
          final n = i + 2 < bytes.length ? bytes[i + 2] : 0;
          alineacion = switch (n) {
            1 => Alineacion.centro,
            2 => Alineacion.derecha,
            _ => Alineacion.izquierda,
          };
          i += 3;
        case 0x45: // ESC E n — negritas
          negrita = (i + 2 < bytes.length ? bytes[i + 2] : 0) != 0;
          i += 3;
        case 0x64: // ESC d n — avanzar líneas
          final n = i + 2 < bytes.length ? bytes[i + 2] : 0;
          if (actual.isNotEmpty) cerrarLinea();
          for (var k = 0; k < n; k++) {
            lineas.add(
              LineaImpresa(
                texto: '',
                alineacion: alineacion,
                negrita: negrita,
                anchoDoble: anchoDoble,
                altoDoble: altoDoble,
              ),
            );
          }
          i += 3;
        default:
          desconocidos.add('ESC 0x${siguiente.toRadixString(16)}');
          i += 2;
      }
      continue;
    }

    if (b == 0x1D) {
      // GS
      final siguiente = i + 1 < bytes.length ? bytes[i + 1] : -1;
      switch (siguiente) {
        case 0x21: // GS ! n — tamaño
          final n = i + 2 < bytes.length ? bytes[i + 2] : 0;
          anchoDoble = ((n >> 4) & 0x0F) >= 1;
          altoDoble = (n & 0x0F) >= 1;
          i += 3;
        case 0x56: // GS V m — corte
          if (actual.isNotEmpty) cerrarLinea();
          i += 3;
        default:
          desconocidos.add('GS 0x${siguiente.toRadixString(16)}');
          i += 2;
      }
      continue;
    }

    actual.add(b);
    i++;
  }

  if (actual.isNotEmpty) cerrarLinea();

  return VistaPrevia(
    lineas: lineas,
    columnas: columnas,
    tabla: tabla,
    desconocidos: desconocidos,
  );
}

/// Bytes de la tabla de códigos → texto Unicode.
String _aTexto(List<int> bytes, TablaDeCodigos tabla) {
  // El mapa va de carácter a byte; para decodificar hace falta al revés.
  final inverso = {for (final e in tabla.mapa.entries) e.value: e.key};
  final salida = StringBuffer();
  for (final b in bytes) {
    if (b >= 0x20 && b <= 0x7E) {
      salida.writeCharCode(b);
    } else {
      salida.write(inverso[b] ?? '·');
    }
  }
  return salida.toString();
}
