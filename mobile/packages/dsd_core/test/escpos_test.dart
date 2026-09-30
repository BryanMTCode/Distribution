/// ESC/POS: los bytes.
///
/// La impresora está en otro Estado, así que estas pruebas son **lo único** que
/// verifica que los bytes sean correctos antes de que llegue. Se afirma contra
/// los valores del estándar, uno por uno, no contra lo que el código produce.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

void main() {
  group('comandos del estándar', () {
    test('ESC @ reinicia, y va primero', () {
      // Sin el reinicio, el ticket hereda el estado del anterior: si el previo
      // terminó en negritas dobles, este empieza así.
      final b = (ConstructorEscPos()..inicializar()).bytes;
      expect(b.sublist(0, 2), equals([0x1B, 0x40]));
    });

    test('ESC t n fija la tabla de códigos', () {
      final b437 = (ConstructorEscPos()..inicializar()).bytes;
      expect(b437.sublist(2, 5), equals([0x1B, 0x74, 0]));

      final b850 =
          (ConstructorEscPos(tabla: TablaDeCodigos.pc850)..inicializar()).bytes;
      expect(b850.sublist(2, 5), equals([0x1B, 0x74, 2]));
    });

    test('ESC a n alinea', () {
      for (final (a, n) in [
        (Alineacion.izquierda, 0),
        (Alineacion.centro, 1),
        (Alineacion.derecha, 2),
      ]) {
        final b = (ConstructorEscPos()..alinear(a)).bytes;
        expect(b, equals([0x1B, 0x61, n]));
      }
    });

    test('ESC E n enciende y apaga las negritas', () {
      expect((ConstructorEscPos()..negrita(true)).bytes, equals([0x1B, 0x45, 1]));
      expect((ConstructorEscPos()..negrita(false)).bytes, equals([0x1B, 0x45, 0]));
    });

    test('GS ! n codifica ancho en el nibble alto y alto en el bajo', () {
      // Es el comando que más fácil se escribe al revés, y al revés imprime
      // letras altas y flacas en vez de anchas.
      expect(
        (ConstructorEscPos()..tamano()).bytes,
        equals([0x1D, 0x21, 0x00]),
      );
      expect(
        (ConstructorEscPos()..tamano(ancho: 2, alto: 2)).bytes,
        equals([0x1D, 0x21, 0x11]),
      );
      expect(
        (ConstructorEscPos()..tamano(ancho: 2, alto: 1)).bytes,
        equals([0x1D, 0x21, 0x10]),
      );
      expect(
        (ConstructorEscPos()..tamano(ancho: 1, alto: 2)).bytes,
        equals([0x1D, 0x21, 0x01]),
      );
      expect(
        (ConstructorEscPos()..tamano(ancho: 8, alto: 8)).bytes,
        equals([0x1D, 0x21, 0x77]),
      );
    });

    test('un tamaño fuera de rango es un error de programación', () {
      expect(() => ConstructorEscPos().tamano(ancho: 0), throwsArgumentError);
      expect(() => ConstructorEscPos().tamano(ancho: 9), throwsArgumentError);
      expect(() => ConstructorEscPos().tamano(alto: 0), throwsArgumentError);
    });

    test('ESC d n avanza papel', () {
      expect((ConstructorEscPos()..avanzar(4)).bytes, equals([0x1B, 0x64, 4]));
      expect(() => ConstructorEscPos().avanzar(-1), throwsArgumentError);
      expect(() => ConstructorEscPos().avanzar(256), throwsArgumentError);
    });

    test('GS V m corta', () {
      expect((ConstructorEscPos()..cortar()).bytes, equals([0x1D, 0x56, 0x00]));
    });

    test('cada línea termina en LF', () {
      final b = (ConstructorEscPos()..linea('AB')).bytes;
      expect(b, equals([0x41, 0x42, 0x0A]));
    });
  });

  group('la tabla de códigos', () {
    test('los acentos del español salen como su byte, no como UTF-8', () {
      // Mandar UTF-8 a una impresora térmica imprime basura donde va una ñ: son
      // dos bytes que la impresora interpreta como dos símbolos.
      expect(codificar('ñ'), equals([0xA4]));
      expect(codificar('Ñ'), equals([0xA5]));
      expect(codificar('á'), equals([0xA0]));
      expect(codificar('é'), equals([0x82]));
      expect(codificar('í'), equals([0xA1]));
      expect(codificar('ó'), equals([0xA2]));
      expect(codificar('ú'), equals([0xA3]));
      expect(codificar('ü'), equals([0x81]));
      expect(codificar('¿'), equals([0xA8]));
      expect(codificar('°'), equals([0xF8]));
    });

    test('el ASCII imprimible pasa tal cual', () {
      expect(codificar('Abarrotes 123 \$.'),
          equals('Abarrotes 123 \$.'.codeUnits));
    });

    test('LOS EMOJI SE DESCARTAN, no se convierten en basura', () {
      // El caso real: "La Esquina de Ñoño 🏪" está en los datos de prueba. Un
      // emoji son cuatro bytes UTF-8 que en PC437 salen como cuatro símbolos sin
      // sentido en medio del nombre del cliente, en un papel que el cliente
      // conserva.
      final bytes = codificar('La Esquina de Ñoño 🏪');
      expect(bytes, equals(codificar('La Esquina de Ñoño ')));
      // Y no queda ningún byte por encima de la tabla.
      expect(bytes.every((b) => b <= 0xFF), isTrue);
    });

    test('las mayúsculas acentuadas que PC437 no tiene se transliteran', () {
      // PC437 solo trae É de las mayúsculas acentuadas. Un ticket legible en
      // cualquier impresora vale más que uno perfecto en una sola.
      expect(codificar('Á'), equals('A'.codeUnits));
      expect(codificar('Í'), equals('I'.codeUnits));
      expect(codificar('Ó'), equals('O'.codeUnits));
      expect(codificar('Ú'), equals('U'.codeUnits));
      // La É sí está en la tabla, así que NO se transliteral.
      expect(codificar('É'), equals([0x90]));
    });

    test('PC850 sí tiene las mayúsculas acentuadas', () {
      const t = TablaDeCodigos.pc850;
      expect(codificar('Á', tabla: t), equals([0xB5]));
      expect(codificar('Í', tabla: t), equals([0xD6]));
      expect(codificar('Ó', tabla: t), equals([0xE0]));
      expect(codificar('Ú', tabla: t), equals([0xE9]));
      expect(codificar('ñ', tabla: t), equals([0xA4]));
    });

    test('la tipografía de un procesador de textos se degrada a ASCII', () {
      // Nombres capturados copiando y pegando traen comillas curvas y guiones
      // largos. En la impresora son bytes que no existen.
      expect(codificar('“Doña” — sí'), equals(codificar('"Doña" - sí')));
      expect(codificar('…'), equals('...'.codeUnits));
    });

    test('el ancho cuenta columnas IMPRESAS, no caracteres', () {
      // Si el alineado usara la longitud del string, los importes quedarían
      // desalineados justo en los nombres con emoji.
      expect(ancho('Doña'), equals(4));
      expect(ancho('Tienda 🏪'), equals(7), reason: 'el emoji no ocupa columnas');
      expect(ancho('…'), equals(3), reason: 'se transliteral a tres puntos');
    });
  });

  group('el alineado de dos columnas', () {
    String render(String izq, String der, {int columnas = 32}) {
      final b = (ConstructorEscPos(columnas: columnas)..dosColumnas(izq, der))
          .bytes;
      return decodificar(b, columnas: columnas).lineas.single.texto;
    }

    test('el valor queda pegado al margen derecho', () {
      final l = render('Subtotal', '592.00');
      expect(l.length, equals(32));
      expect(l.endsWith('592.00'), isTrue);
      expect(l.startsWith('Subtotal'), isTrue);
    });

    test('un nombre con emoji NO desalinea el importe', () {
      // Es la razón de que el ancho se cuente transliterado.
      final conEmoji = render('Tienda 🏪', '100.00');
      final sinEmoji = render('Tienda ', '100.00');
      expect(conEmoji.length, equals(sinEmoji.length));
      expect(conEmoji.endsWith('100.00'), isTrue);
    });

    test('cuando no caben, se recorta la etiqueta y NUNCA el importe', () {
      // Un importe a medias es peor que ningún importe.
      final l = render(
        'Refresco de cola sabor original 600 ml retornable',
        '1234.56',
      );
      expect(l.length, lessThanOrEqualTo(32));
      expect(l.endsWith('1234.56'), isTrue);
    });

    test('en tamaño doble el ancho útil es la mitad', () {
      final b = (ConstructorEscPos()
            ..tamano(ancho: 2)
            ..dosColumnas('TOTAL', '592.00'))
          .bytes;
      final linea = decodificar(b).lineas.single;
      // 16 columnas útiles: 'TOTAL' + relleno + '592.00' = 16 caracteres.
      expect(linea.texto.length, equals(16));
      expect(linea.anchoDoble, isTrue);
    });
  });

  group('partir en líneas', () {
    test('corta entre palabras, no a la mitad', () {
      final lineas = partirEnLineas('Sopa de fideo marca La Moderna 70 g', 20);
      expect(lineas.every((l) => l.length <= 20), isTrue);
      // Ninguna línea empieza o termina con un pedazo de palabra.
      expect(lineas.join(' '), equals('Sopa de fideo marca La Moderna 70 g'));
    });

    test('una palabra más larga que la línea se parte por fuerza', () {
      // Dejarla desbordar haría que la impresora la continúe sola y el resto del
      // ticket se corra.
      final lineas = partirEnLineas('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 10);
      expect(lineas, equals(['ABCDEFGHIJ', 'KLMNOPQRST', 'UVWXYZ']));
    });

    test('el texto vacío da una línea vacía, no una lista vacía', () {
      // Una lista vacía haría desaparecer el renglón del ticket sin avisar.
      expect(partirEnLineas('', 20), equals(['']));
      expect(partirEnLineas('   ', 20), equals(['']));
    });

    test('respeta los acentos al medir', () {
      final lineas = partirEnLineas('Piñata Ñoño Añejo Cañón', 12);
      expect(lineas.every((l) => ancho(l) <= 12), isTrue);
    });

    test('con cero columnas no se cuelga', () {
      expect(partirEnLineas('lo que sea', 0), isEmpty);
    });
  });

  group('recortar', () {
    test('respeta el ancho impreso', () {
      expect(recortar('Abarrotes', 5), equals('Abarr'));
      expect(ancho(recortar('Piñata Ñoño', 6)), equals(6));
    });

    test('un emoji no consume columnas al recortar', () {
      // 🏪 no ocupa nada, así que caben más letras.
      expect(recortar('🏪Tienda', 6), equals('🏪Tienda'));
    });

    test('con cero o menos devuelve vacío', () {
      expect(recortar('lo que sea', 0), isEmpty);
      expect(recortar('lo que sea', -3), isEmpty);
    });
  });

  group('la vista previa', () {
    test('devuelve las líneas con su formato', () {
      final b = (ConstructorEscPos()
            ..inicializar()
            ..alinear(Alineacion.centro)
            ..negrita(true)
            ..linea('ABARROTES')
            ..negrita(false)
            ..alinear(Alineacion.izquierda)
            ..linea('normal'))
          .bytes;

      final vista = decodificar(b);
      expect(vista.lineas, hasLength(2));
      expect(vista.lineas[0].texto, equals('ABARROTES'));
      expect(vista.lineas[0].negrita, isTrue);
      expect(vista.lineas[0].alineacion, equals(Alineacion.centro));
      expect(vista.lineas[1].negrita, isFalse);
      expect(vista.lineas[1].alineacion, equals(Alineacion.izquierda));
      expect(vista.tabla?.nombre, equals('PC437'));
    });

    test('los acentos vuelven de sus bytes', () {
      // Ida y vuelta: si la tabla estuviera mal, aquí saldría otro carácter.
      final b = (ConstructorEscPos()..inicializar()..linea('Doña Mary Ñuñez')).bytes;
      expect(decodificar(b).texto, contains('Doña Mary Ñuñez'));
    });

    test('centra contando las columnas que OCUPA, no los caracteres', () {
      // En tamaño doble cada carácter vale dos columnas: un total centrado
      // arranca en la columna 3, no en la 9.
      final b = (ConstructorEscPos()
            ..inicializar()
            ..alinear(Alineacion.centro)
            ..tamano(ancho: 2, alto: 2)
            ..linea('TOTAL'))
          .bytes;

      final render = decodificar(b).render().trimRight();
      // 'TOTAL' ocupa 10 columnas de 32 ⇒ 11 espacios a la izquierda. Los
      // caracteres se separan para representar el ancho doble: sin eso, la vista
      // previa mostraría un centrado que el papel no tiene.
      expect(render, equals('${' ' * 11}T O T A L'));
    });

    test('ESC d deja líneas en blanco, no desaparece', () {
      final b = (ConstructorEscPos()..linea('fin')..avanzar(3)).bytes;
      final vista = decodificar(b);
      expect(vista.lineas, hasLength(4));
      expect(vista.lineas.last.texto, isEmpty);
    });

    test('un comando desconocido se reporta, no se traga', () {
      // Si el generador empieza a emitir algo que el decodificador no conoce, la
      // vista previa dejaría de corresponder al papel y nadie se enteraría.
      final vista = decodificar([0x1B, 0x99, 0x41, 0x0A]);
      expect(vista.desconocidos, isNotEmpty);
      expect(vista.desconocidos.first, contains('99'));
    });

    test('el marco hace visible una línea que se desborda', () {
      final b = (ConstructorEscPos(columnas: 10)..linea('0123456789ABCDE')).bytes;
      final marco = decodificar(b, columnas: 10).renderConMarco();
      // La marca '>' en vez de '|' al final señala el desborde.
      expect(marco, contains('>'));
    });

    test('un ticket sin ESC t lo delata', () {
      // Sin fijar la tabla, la impresora usa la que trae de fábrica y los
      // acentos salen como les toque. Es un defecto, no un detalle.
      final vista = decodificar((ConstructorEscPos()..linea('hola')).bytes);
      expect(vista.tabla, isNull);
    });
  });
}
