/// ESC/POS: los bytes que entiende una impresora térmica.
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ ESTO ES UN MÓDULO PURO, SIN BLUETOOTH
/// ─────────────────────────────────────────────────────────────────────────
/// Generar el ticket y transmitirlo son dos problemas distintos. El primero es
/// aritmética y formato —se prueba entero, en segundos, sin hardware—. El
/// segundo es un socket que se cae, un equipo desemparejado, papel que se acaba.
///
/// Mezclarlos habría dejado el diseño del ticket sin probar hasta tener la
/// impresora en la mano. Separados, el ticket se verifica hoy y lo único que
/// falta cuando llegue el equipo es empujar los bytes por el socket.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LA TRAMPA: LOS ACENTOS Y LOS EMOJI
/// ─────────────────────────────────────────────────────────────────────────
/// Una impresora térmica **no habla UTF-8**. Recibe bytes y los interpreta con
/// una tabla de códigos de un byte. Mandarle UTF-8 directo imprime basura donde
/// va una ñ.
///
/// Y el sistema **ya tiene emoji en los nombres de clientes** ("La Esquina de
/// Ñoño 🏪" está en los datos de prueba). Un emoji son cuatro bytes UTF-8 que en
/// PC437 salen como cuatro símbolos sin sentido en medio del nombre del cliente,
/// en un papel que el cliente se queda.
///
/// Aquí se resuelve transliterando: lo que la tabla tiene se mapea, y lo que no
/// —Á, emoji, cualquier cosa fuera de Latin-1— se degrada a su equivalente sin
/// acento o se descarta. **Un ticket legible en cualquier impresora vale más que
/// uno perfecto en una sola**, sobre todo sin poder probar en hardware.
library;

import 'dart:convert';

/// Ancho del papel, en caracteres de la fuente A.
///
/// 58 mm de papel dan 384 puntos imprimibles; la fuente A ocupa 12 puntos por
/// carácter ⇒ 32 columnas. La fuente B (9 puntos) daría 42, pero a pleno sol y
/// con lectores de 50 años la A se lee y la B no.
const columnas58mm = 32;

/// Ancho de la fuente A en 80 mm, por si algún día entra una impresora de
/// escritorio en la oficina.
const columnas80mm = 48;

// ---------------------------------------------------------------------------
// Comandos
// ---------------------------------------------------------------------------
// Se nombran como los nombra el estándar (ESC @, GS !) para que quien compare
// con la hoja de datos de la impresora encuentre lo mismo.

const _esc = 0x1B;
const _gs = 0x1D;
const _lf = 0x0A;

/// Tabla de códigos de la impresora.
///
/// `comando` es el número que va en `ESC t n`. PC437 es el que TODA impresora
/// ESC/POS soporta; PC850 tiene además las vocales acentuadas mayúsculas, pero
/// no todas las impresoras lo traen.
class TablaDeCodigos {
  const TablaDeCodigos({
    required this.nombre,
    required this.comando,
    required this.mapa,
  });

  final String nombre;
  final int comando;

  /// Carácter Unicode → byte en esta tabla.
  final Map<String, int> mapa;

  /// PC437, el original de IBM. **La opción por omisión.**
  ///
  /// Tiene las minúsculas acentuadas del español y la ñ, pero de las mayúsculas
  /// acentuadas solo É. Las demás se transliteran (Á→A), que en un ticket de
  /// abarrotes no le quita nada a nadie.
  static const pc437 = TablaDeCodigos(
    nombre: 'PC437',
    comando: 0,
    mapa: {
      'Ç': 0x80, 'ü': 0x81, 'é': 0x82, 'â': 0x83, 'ä': 0x84, 'à': 0x85,
      'å': 0x86, 'ç': 0x87, 'ê': 0x88, 'ë': 0x89, 'è': 0x8A, 'ï': 0x8B,
      'î': 0x8C, 'ì': 0x8D, 'Ä': 0x8E, 'Å': 0x8F, 'É': 0x90, 'æ': 0x91,
      'Æ': 0x92, 'ô': 0x93, 'ö': 0x94, 'ò': 0x95, 'û': 0x96, 'ù': 0x97,
      'ÿ': 0x98, 'Ö': 0x99, 'Ü': 0x9A, '¢': 0x9B, '£': 0x9C, '¥': 0x9D,
      'á': 0xA0, 'í': 0xA1, 'ó': 0xA2, 'ú': 0xA3, 'ñ': 0xA4, 'Ñ': 0xA5,
      'ª': 0xA6, 'º': 0xA7, '¿': 0xA8, '¬': 0xAA, '½': 0xAB, '¼': 0xAC,
      '¡': 0xAD, '«': 0xAE, '»': 0xAF, 'ß': 0xE1, 'µ': 0xE6, '±': 0xF1,
      '÷': 0xF6, '°': 0xF8, '·': 0xFA, '²': 0xFD,
    },
  );

  /// PC850, Latin-1 multilingüe. Agrega Á Í Ó Ú y más.
  ///
  /// Mejor fidelidad, menos portabilidad: si la impresora no lo soporta, los
  /// acentos salen como símbolos raros. Se cambia con un parámetro el día que se
  /// compruebe en el equipo real.
  static const pc850 = TablaDeCodigos(
    nombre: 'PC850',
    comando: 2,
    mapa: {
      'Ç': 0x80, 'ü': 0x81, 'é': 0x82, 'â': 0x83, 'ä': 0x84, 'à': 0x85,
      'å': 0x86, 'ç': 0x87, 'ê': 0x88, 'ë': 0x89, 'è': 0x8A, 'ï': 0x8B,
      'î': 0x8C, 'ì': 0x8D, 'Ä': 0x8E, 'Å': 0x8F, 'É': 0x90, 'æ': 0x91,
      'Æ': 0x92, 'ô': 0x93, 'ö': 0x94, 'ò': 0x95, 'û': 0x96, 'ù': 0x97,
      'ÿ': 0x98, 'Ö': 0x99, 'Ü': 0x9A, 'ø': 0x9B, '£': 0x9C, 'Ø': 0x9D,
      'á': 0xA0, 'í': 0xA1, 'ó': 0xA2, 'ú': 0xA3, 'ñ': 0xA4, 'Ñ': 0xA5,
      'ª': 0xA6, 'º': 0xA7, '¿': 0xA8, '®': 0xA9, '¬': 0xAA, '½': 0xAB,
      '¼': 0xAC, '¡': 0xAD, '«': 0xAE, '»': 0xAF, 'Á': 0xB5, 'Â': 0xB6,
      'À': 0xB7, '©': 0xB8, 'ã': 0xC6, 'Ã': 0xC7, 'ð': 0xD0, 'Ð': 0xD1,
      'Ê': 0xD2, 'Ë': 0xD3, 'È': 0xD4, 'Í': 0xD6, 'Î': 0xD7, 'Ï': 0xD8,
      'Ó': 0xE0, 'ß': 0xE1, 'Ô': 0xE2, 'Ò': 0xE3, 'õ': 0xE4, 'Õ': 0xE5,
      'µ': 0xE6, 'þ': 0xE7, 'Þ': 0xE8, 'Ú': 0xE9, 'Û': 0xEA, 'Ù': 0xEB,
      'ý': 0xEC, 'Ý': 0xED, '±': 0xF1, '°': 0xF8, '·': 0xFA, '²': 0xFD,
    },
  );
}

/// Lo que se hace con un carácter que la tabla no tiene.
///
/// El orden importa: primero se busca en la tabla, y solo si no está se
/// transliteral. Así una ñ sale como ñ y no como n.
const _transliteracion = {
  'Á': 'A', 'À': 'A', 'Â': 'A', 'Ã': 'A', 'Ä': 'A',
  'Í': 'I', 'Ì': 'I', 'Î': 'I', 'Ï': 'I',
  'Ó': 'O', 'Ò': 'O', 'Ô': 'O', 'Õ': 'O', 'Ö': 'O',
  'Ú': 'U', 'Ù': 'U', 'Û': 'U',
  'É': 'E', 'È': 'E', 'Ê': 'E', 'Ë': 'E',
  'Ñ': 'N', 'Ç': 'C', 'Ü': 'U',
  '—': '-', '–': '-', '−': '-',
  '“': '"', '”': '"', '„': '"',
  '‘': "'", '’': "'",
  '…': '...',
  '€': 'EUR', '™': 'TM',
  ' ': ' ', // espacio duro
};

/// Codifica un texto para la impresora.
///
/// Los caracteres que ni la tabla ni la transliteración cubren —emoji, kanji,
/// símbolos raros— **se descartan**. Poner un '?' por cada byte de un emoji
/// dejaría "La Esquina de Nono ????" en el papel; descartarlo deja
/// "La Esquina de Nono", que es lo que el cliente espera leer.
List<int> codificar(String texto, {TablaDeCodigos tabla = TablaDeCodigos.pc437}) {
  final salida = <int>[];
  for (final runa in texto.runes) {
    final ch = String.fromCharCode(runa);

    final directo = tabla.mapa[ch];
    if (directo != null) {
      salida.add(directo);
      continue;
    }
    if (runa >= 0x20 && runa <= 0x7E) {
      salida.add(runa); // ASCII imprimible
      continue;
    }
    final equivalente = _transliteracion[ch];
    if (equivalente != null) {
      // La transliteración puede dar varios caracteres ('…' → '...').
      salida.addAll(equivalente.codeUnits);
      continue;
    }
    // Emoji y todo lo demás: se descarta en silencio.
  }
  return salida;
}

/// Cuántas columnas ocupa un texto **ya transliterado**.
///
/// No es `texto.length`: un emoji descartado ocupa cero y un '…' ocupa tres. Si
/// el alineado usara la longitud original, los importes quedarían desalineados
/// justo en los nombres con emoji.
int ancho(String texto, {TablaDeCodigos tabla = TablaDeCodigos.pc437}) =>
    codificar(texto, tabla: tabla).length;

/// Una imagen de un bit por punto: lo que imprime `GS v 0` (ADR 0002 §155).
///
/// Cada renglón ocupa `bytesPorRenglon` bytes; el bit más alto de cada byte es
/// el punto de la izquierda, y un 1 es un punto negro.
class ImagenMonocroma {
  const ImagenMonocroma({required this.ancho, required this.alto, required this.puntos});

  /// En puntos de la cabeza (384 caben en 58 mm).
  final int ancho;
  final int alto;
  final List<int> puntos;

  int get bytesPorRenglon => (ancho + 7) ~/ 8;
}

/// Arma el flujo de bytes de un ticket.
///
/// Cada método devuelve `this` para poder encadenar, que es como se lee mejor un
/// documento: en el orden en que sale del papel.
class ConstructorEscPos {
  ConstructorEscPos({
    this.columnas = columnas58mm,
    this.tabla = TablaDeCodigos.pc437,
  });

  final int columnas;
  final TablaDeCodigos tabla;
  final List<int> _bytes = [];

  /// Multiplicador de ancho vigente (1 o 2). Afecta cuántas columnas caben.
  int _escalaAncho = 1;

  List<int> get bytes => List.unmodifiable(_bytes);

  /// Columnas disponibles con la escala actual.
  int get columnasUtiles => columnas ~/ _escalaAncho;

  /// `ESC @` — reinicia la impresora y `ESC t n` — fija la tabla de códigos.
  ///
  /// Lo primero de todo ticket. Sin el reinicio, el ticket hereda el estado que
  /// dejó el anterior: si el previo terminó en negritas dobles, este empieza así.
  ConstructorEscPos inicializar() {
    _bytes.addAll([_esc, 0x40]);
    _bytes.addAll([_esc, 0x74, tabla.comando]);
    _escalaAncho = 1;
    return this;
  }

  /// `ESC a n` — 0 izquierda, 1 centro, 2 derecha.
  ConstructorEscPos alinear(Alineacion a) {
    _bytes.addAll([_esc, 0x61, a.codigo]);
    return this;
  }

  /// `ESC E n` — énfasis (negritas).
  ConstructorEscPos negrita(bool encendida) {
    _bytes.addAll([_esc, 0x45, encendida ? 1 : 0]);
    return this;
  }

  /// `GS ! n` — tamaño. El nibble alto es el ancho, el bajo el alto.
  ConstructorEscPos tamano({int ancho = 1, int alto = 1}) {
    if (ancho < 1 || ancho > 8 || alto < 1 || alto > 8) {
      throw ArgumentError('el tamaño va de 1 a 8, llegó $ancho x $alto');
    }
    _bytes.addAll([_gs, 0x21, ((ancho - 1) << 4) | (alto - 1)]);
    _escalaAncho = ancho;
    return this;
  }

  /// Una línea de texto, tal cual, con su salto.
  ConstructorEscPos linea([String texto = '']) {
    _bytes.addAll(codificar(texto, tabla: tabla));
    _bytes.add(_lf);
    return this;
  }

  /// Texto largo partido en varias líneas, cortando entre palabras.
  ///
  /// Partir a media palabra es lo que hace ilegible un nombre de producto en un
  /// papel de 32 columnas.
  ConstructorEscPos parrafo(String texto) {
    for (final l in partirEnLineas(texto, columnasUtiles, tabla: tabla)) {
      linea(l);
    }
    return this;
  }

  /// Etiqueta a la izquierda, valor a la derecha, rellenando en medio.
  ///
  /// Es la línea que más se usa en un ticket, y la que más fácil se desalinea:
  /// el relleno se calcula con el ancho **ya transliterado**, no con la longitud
  /// del string original.
  ConstructorEscPos dosColumnas(String izquierda, String derecha) {
    final anchoIzq = ancho(izquierda, tabla: tabla);
    final anchoDer = ancho(derecha, tabla: tabla);
    final hueco = columnasUtiles - anchoIzq - anchoDer;

    if (hueco < 1) {
      // No caben juntas: se recorta la izquierda, que es la descriptiva. El
      // valor NUNCA se recorta — un importe a medias es peor que ningún importe.
      final disponible = columnasUtiles - anchoDer - 1;
      final recortada =
          disponible <= 0 ? '' : recortar(izquierda, disponible, tabla: tabla);
      final relleno = columnasUtiles - ancho(recortada, tabla: tabla) - anchoDer;
      return linea('$recortada${' ' * (relleno < 1 ? 1 : relleno)}$derecha');
    }
    return linea('$izquierda${' ' * hueco}$derecha');
  }

  /// Una línea de guiones de ancho completo.
  ConstructorEscPos separador([String caracter = '-']) =>
      linea(caracter * columnasUtiles);

  /// Texto centrado a mano.
  ///
  /// Se usa cuando hace falta centrar dentro de una línea que también lleva
  /// otra cosa; para una línea entera es mejor `alinear(centro)`, que lo hace la
  /// impresora y sale exacto.
  ConstructorEscPos centrado(String texto) {
    final sobra = columnasUtiles - ancho(texto, tabla: tabla);
    final izq = sobra <= 0 ? 0 : sobra ~/ 2;
    return linea('${' ' * izq}$texto');
  }

  /// `GS v 0 m xL xH yL yH d…` — una imagen de puntos, en tamaño normal.
  ///
  /// La centra `ESC a 1`, como al texto. Va en un solo comando y no en tiras
  /// (`ESC *`) porque las térmicas de ruta lo entienden todas, y así la vista
  /// previa y el margen (`conMargenIzquierdo`) saben dónde termina.
  ConstructorEscPos imagen(ImagenMonocroma imagen) {
    final porRenglon = imagen.bytesPorRenglon;
    if (imagen.puntos.length != porRenglon * imagen.alto) {
      throw ArgumentError(
        'la imagen dice ${imagen.ancho}×${imagen.alto} y trae ${imagen.puntos.length} bytes',
      );
    }
    _bytes.addAll([
      _gs, 0x76, 0x30, 0x00,
      porRenglon & 0xFF, (porRenglon >> 8) & 0xFF,
      imagen.alto & 0xFF, (imagen.alto >> 8) & 0xFF,
      ...imagen.puntos,
    ]);
    return this;
  }

  /// `ESC d n` — avanza n líneas. Es lo que deja papel para cortar a mano.
  ConstructorEscPos avanzar(int lineas) {
    if (lineas < 0 || lineas > 255) {
      throw ArgumentError('avanzar espera 0..255, llegó $lineas');
    }
    _bytes.addAll([_esc, 0x64, lineas]);
    return this;
  }

  /// `GS V m` — corte de papel.
  ///
  /// Las impresoras móviles de 58 mm **no traen cortador**: el vendedor arranca
  /// el papel. Se deja el método porque una impresora de escritorio en la
  /// oficina sí lo usaría, pero el ticket de ruta no lo manda.
  ConstructorEscPos cortar() {
    _bytes.addAll([_gs, 0x56, 0x00]);
    return this;
  }
}

/// Puntos que mide una letra de la fuente normal (A, 12×24) en una térmica de
/// 203 ppp: 32 letras son los 384 puntos de una cabeza de 58 mm.
const puntosPorLetra = 12;

/// `GS L nL nH` — el margen izquierdo, en puntos.
List<int> margenIzquierdo(int puntos) =>
    [_gs, 0x4C, puntos & 0xFF, (puntos >> 8) & 0xFF];

/// El ticket con el margen izquierdo puesto después de CADA `ESC @` (ADR 0002
/// §141).
///
/// Hay impresoras —la EC-MP200 de la dirección— cuya cabeza empieza antes del
/// borde del papel: la primera letra de cada renglón sale fuera. El margen se
/// pone aquí, al mandar, y no al armar el ticket, por dos razones: la venta
/// guarda los bytes de su ticket y la reimpresión los reusa tal cual (con el
/// margen de la impresora de HOY), y `ESC @` lo reinicia —la reimpresión trae
/// dos: el del aviso de copia y el del original—.
///
/// Recorre el flujo comando por comando para no confundir con un `ESC @` el
/// argumento de otro comando. Sin `ESC @`, el margen va al principio.
List<int> conMargenIzquierdo(List<int> bytes, int puntos) {
  if (puntos <= 0) return bytes;
  final margen = margenIzquierdo(puntos);
  final salida = <int>[];
  var puesto = false;
  var i = 0;
  while (i < bytes.length) {
    final largo = _largoDelComando(bytes, i);
    salida.addAll(bytes.sublist(i, i + largo));
    if (largo == 2 && bytes[i] == _esc && bytes[i + 1] == 0x40) {
      salida.addAll(margen);
      puesto = true;
    }
    i += largo;
  }
  return puesto ? salida : [...margen, ...bytes];
}

/// Cuántos bytes mide el comando (o el carácter) que empieza en [i].
int _largoDelComando(List<int> b, int i) {
  int hasta(int n) => n < b.length - i ? n : b.length - i;
  final siguiente = i + 1 < b.length ? b[i + 1] : -1;
  if (b[i] == _esc) {
    return switch (siguiente) {
      0x40 => hasta(2),
      0x74 || 0x61 || 0x45 || 0x64 || 0x21 || 0x2D || 0x4D => hasta(3),
      _ => hasta(2),
    };
  }
  if (b[i] == _gs) {
    // `GS v 0`: la imagen trae sus puntos detrás, y entre ellos puede haber un
    // 1B 40 que no es un `ESC @`. Se salta entera.
    if (siguiente == 0x76 && i + 7 < b.length) {
      final porRenglon = b[i + 4] | (b[i + 5] << 8);
      final renglones = b[i + 6] | (b[i + 7] << 8);
      return hasta(8 + porRenglon * renglones);
    }
    return switch (siguiente) {
      0x21 => hasta(3),
      0x56 => hasta(i + 2 < b.length && (b[i + 2] == 0x41 || b[i + 2] == 0x42) ? 4 : 3),
      0x4C || 0x57 => hasta(4),
      _ => hasta(2),
    };
  }
  return 1;
}

enum Alineacion {
  izquierda(0),
  centro(1),
  derecha(2);

  const Alineacion(this.codigo);

  final int codigo;
}

/// Parte un texto en líneas de a lo más [columnas], cortando entre palabras.
///
/// Una palabra más larga que la línea se parte por fuerza: dejarla desbordar
/// haría que la impresora la continúe en la siguiente línea sin control, y el
/// resto del ticket se corre.
List<String> partirEnLineas(
  String texto,
  int columnas, {
  TablaDeCodigos tabla = TablaDeCodigos.pc437,
}) {
  if (columnas <= 0) return const [];

  final lineas = <String>[];
  var actual = '';

  for (final palabra in texto.split(RegExp(r'\s+')).where((p) => p.isNotEmpty)) {
    final candidata = actual.isEmpty ? palabra : '$actual $palabra';
    if (ancho(candidata, tabla: tabla) <= columnas) {
      actual = candidata;
      continue;
    }
    if (actual.isNotEmpty) {
      lineas.add(actual);
      actual = '';
    }
    // La palabra sola puede seguir sin caber.
    var resto = palabra;
    while (ancho(resto, tabla: tabla) > columnas) {
      final trozo = recortar(resto, columnas, tabla: tabla);
      lineas.add(trozo);
      resto = resto.substring(trozo.length);
    }
    actual = resto;
  }
  if (actual.isNotEmpty) lineas.add(actual);
  return lineas.isEmpty ? [''] : lineas;
}

/// Recorta un texto a [columnas] contando el ancho **impreso**.
String recortar(
  String texto,
  int columnas, {
  TablaDeCodigos tabla = TablaDeCodigos.pc437,
}) {
  if (columnas <= 0) return '';
  final buffer = StringBuffer();
  var usadas = 0;
  for (final runa in texto.runes) {
    final ch = String.fromCharCode(runa);
    final cuesta = ancho(ch, tabla: tabla);
    if (usadas + cuesta > columnas) break;
    buffer.write(ch);
    usadas += cuesta;
  }
  return buffer.toString();
}

/// Para inspeccionar bytes en un log o en un mensaje de error.
String enHexadecimal(List<int> bytes) =>
    bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join(' ');

/// Los bytes en base64, que es como se guardan en la base y viajan en un log.
String enBase64(List<int> bytes) => base64Encode(bytes);
