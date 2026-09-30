/// La impresión, con la impresora simulada.
///
/// Lo valioso de estas pruebas es que ejercen **los caminos de falla**: sin
/// papel, desconectada, sin configurar. Con una impresora real, quedarse sin
/// papel a media prueba no se puede provocar, así que esos caminos se quedarían
/// sin probar hasta que le pasara a un vendedor en la calle.
library;

import 'dart:io';

import 'package:dsd_app/src/datos/base_local.dart';
import 'package:dsd_app/src/datos/impresora.dart';
import 'package:dsd_app/src/datos/servicio_ubicacion.dart';
import 'package:dsd_app/src/estado/alta.dart';
import 'package:dsd_app/src/estado/carrito.dart';
import 'package:dsd_app/src/pantallas/vendedor/vista_ticket.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

/// Entra, arma un pedido de dos cajas y cobra.
Future<(BaseLocal, ImpresoraSimulada)> cobrar(
  WidgetTester tester, {
  ResultadoImpresion? falla,
  Directory? carpeta,
}) async {
  final impresora = ImpresoraSimulada(falla: falla, carpeta: carpeta);
  late BaseLocal base;

  base = await montarApp(
    tester,
    credencial: credencialDelServidor(),
    sembrar: (b) {
      sembrarEscenarioDeVenta(b);
      sembrarParaCobrar(b);
    },
    extras: [
      impresoraProvider.overrideWithValue(impresora),
      servicioUbicacionProvider.overrideWithValue(
        ServicioUbicacionFalso.siempre(const GpsSinLectura()),
      ),
    ],
  );

  await entrarCon(tester, pinCorrecto);
  await tocar(tester, const Key('cliente_cliente-1'));
  await tocar(tester, const Key('agregar_p-sopa|CAJA'));
  await tocar(tester, const Key('mas_p-sopa|CAJA'));
  await tocar(tester, const Key('boton_ver_carrito'));
  await tocar(tester, const Key('boton_cobrar'));
  return (base, impresora);
}

void main() {
  late Directory temporal;

  setUp(() {
    // Carpeta propia: la impresora simulada escribe archivos, y sin esto las
    // pruebas dependerían del almacenamiento de la app, que en un entorno de
    // pruebas no existe.
    temporal = Directory.systemTemp.createTempSync('tickets_de_prueba');
  });

  tearDown(() {
    if (temporal.existsSync()) temporal.deleteSync(recursive: true);
  });

  group('imprimir con éxito', () {
    testWidgets('manda los bytes del ticket y lo registra', (tester) async {
      final (base, impresora) = await cobrar(tester, carpeta: temporal);
      await tocarConProgreso(tester, const Key('boton_imprimir'));

      expect(impresora.enviados, hasLength(1));
      final vista = decodificar(impresora.enviados.single);
      // El ticket real: el folio, la aritmética y el total.
      expect(vista.texto, contains('VEND01-000001'));
      expect(vista.texto, contains('2 CAJA x 296.00'));
      expect(vista.texto, contains('592.00'));
      expect(vista.texto, contains('DOCUMENTO NO FISCAL'));
      expect(vista.desconocidos, isEmpty);

      final venta = BaseLocalDePrueba(base)
          .unaFila('SELECT impreso, reimpresiones, ticket_escpos FROM ventas');
      expect(venta['impreso'], equals(1));
      expect(venta['reimpresiones'], equals(0));
      expect(venta['ticket_escpos'], isNotNull);
    });

    testWidgets('el nombre del cliente va en el papel, sin su emoji',
        (tester) async {
      final (_, impresora) = await cobrar(tester, carpeta: temporal);
      await tocarConProgreso(tester, const Key('boton_imprimir'));

      final texto = decodificar(impresora.enviados.single).texto;
      expect(texto, contains('Abarrotes Doña Mary'));
      // El decodificador pone '·' donde hay un byte sin traducción: si el emoji
      // hubiera pasado, aparecería.
      expect(texto, isNot(contains('·')));
    });

    testWidgets('deja el ticket en un archivo que se puede ir a leer',
        (tester) async {
      // Es la forma de revisar el diseño sin impresora, desde la computadora.
      await cobrar(tester, carpeta: temporal);
      await tocarConProgreso(tester, const Key('boton_imprimir'));

      // Se espera a que el contenido esté, en vez de confiar en una pausa fija:
      // la escritura a disco no tarda lo mismo en cada máquina, y una prueba que
      // depende de eso falla en el CI cada tantas corridas sin razón aparente.
      // Se espera por 'DOCUMENTO NO FISCAL' y no por el folio: el folio se
      // imprime en tamaño doble, y en la vista previa los caracteres de ancho
      // doble van separados por un espacio para que la POSICIÓN corresponda al
      // papel. Buscar 'VEND01-000001' ahí no lo encuentra, y no porque falte.
      final archivo = File('${temporal.path}/ultimo.txt');
      final contenido =
          await esperarContenido(tester, archivo, 'DOCUMENTO NO FISCAL');

      expect(contenido, contains('2 CAJA x 296.00'));
      expect(contenido, contains('592.00'));
      // El folio sí está, espaciado por el ancho doble.
      expect(contenido, contains('V E N D 0 1'));
      // Con el marco que hace visible el ancho del papel.
      expect(contenido, contains('+---'));

      // Y los bytes en crudo, para mandarlos tal cual a una impresora real.
      final crudos = temporal
          .listSync()
          .where((f) => f.path.endsWith('.escpos'))
          .toList();
      expect(crudos, hasLength(1));
    });

    testWidgets('el botón cambia de texto tras la primera', (tester) async {
      await cobrar(tester, carpeta: temporal);
      expect(find.text('Imprimir remisión'), findsOneWidget);

      await tocarConProgreso(tester, const Key('boton_imprimir'));
      expect(find.text('Imprimir otra copia'), findsOneWidget);
      expect(find.byKey(const Key('aviso_impresa')), findsOneWidget);
    });
  });

  group('las reimpresiones', () {
    testWidgets('reusan los bytes del original y le ponen el aviso de copia',
        (tester) async {
      // Las dos propiedades a la vez: el papel dice lo mismo que el que firmó el
      // cliente, y se distingue para que nadie cobre dos veces con él.
      final (base, impresora) = await cobrar(tester, carpeta: temporal);
      await tocarConProgreso(tester, const Key('boton_imprimir'));
      await tocarConProgreso(tester, const Key('boton_imprimir'));

      expect(impresora.enviados, hasLength(2));
      final original = impresora.enviados[0];
      final copia = impresora.enviados[1];

      // El original, intacto, al final de la copia.
      expect(copia.sublist(copia.length - original.length), equals(original));
      expect(decodificar(copia).texto, contains('COPIA 1'));
      expect(decodificar(original).texto, isNot(contains('COPIA')));

      expect(
        BaseLocalDePrueba(base)
            .unaFila('SELECT reimpresiones FROM ventas')['reimpresiones'],
        equals(1),
      );
    });

    testWidgets('el ticket congelado no se recalcula', (tester) async {
      // Si el catálogo cambia entre la venta y la reimpresión, recalcular daría
      // un papel distinto del que tiene el cliente en la mano.
      final (base, impresora) = await cobrar(tester, carpeta: temporal);
      await tocarConProgreso(tester, const Key('boton_imprimir'));

      base.db.execute("UPDATE precios SET precio = 999 WHERE unidad_codigo = 'CAJA'");
      await tocarConProgreso(tester, const Key('boton_imprimir'));

      expect(decodificar(impresora.enviados[1]).texto, contains('296.00'));
      expect(decodificar(impresora.enviados[1]).texto, isNot(contains('999')));
    });
  });

  group('cuando la impresora falla', () {
    testWidgets('sin papel se dice qué hacer, y la venta sigue guardada',
        (tester) async {
      final (base, _) = await cobrar(
        tester,
        falla: const ImpresoraSinPapel(),
        carpeta: temporal,
      );
      await tocarConProgreso(tester, const Key('boton_imprimir'));

      expect(find.byKey(const Key('aviso_error_impresion')), findsOneWidget);
      expect(textoQueContiene('no tiene papel'), findsOneWidget);
      expect(textoQueContiene('La venta ya está guardada'), findsOneWidget);

      // Y NO quedó marcada como impresa: la oficina tiene que poder distinguir
      // un ticket que nunca salió de uno que se perdió.
      expect(
        BaseLocalDePrueba(base).unaFila('SELECT impreso FROM ventas')['impreso'],
        equals(0),
      );
    });

    testWidgets('desconectada dice otra cosa', (tester) async {
      // Cada falla se resuelve distinto: poner papel no es lo mismo que
      // emparejar el equipo. Un mensaje genérico deja al vendedor sin saber qué
      // hacer.
      await cobrar(
        tester,
        falla: const ImpresoraDesconectada(),
        carpeta: temporal,
      );
      await tocarConProgreso(tester, const Key('boton_imprimir'));
      expect(textoQueContiene('no está conectada'), findsOneWidget);
    });

    testWidgets('sin impresora configurada lo dice sin rodeos', (tester) async {
      await cobrar(
        tester,
        falla: const ImpresoraNoConfigurada(),
        carpeta: temporal,
      );
      await tocarConProgreso(tester, const Key('boton_imprimir'));
      expect(textoQueContiene('no tiene impresora configurada'), findsOneWidget);
    });

    testWidgets('se puede reintentar, y a la segunda sí sale', (tester) async {
      // El reintento es el mismo botón: es la razón por la que la impresión no
      // es automática (ADR 0002 §8).
      final impresora = ImpresoraSimulada(carpeta: temporal);
      await montarApp(
        tester,
        credencial: credencialDelServidor(),
        sembrar: (b) {
          sembrarEscenarioDeVenta(b);
          sembrarParaCobrar(b);
        },
        extras: [
          impresoraProvider.overrideWithValue(impresora),
          servicioUbicacionProvider.overrideWithValue(
            ServicioUbicacionFalso.siempre(const GpsSinLectura()),
          ),
        ],
      );
      await entrarCon(tester, pinCorrecto);
      await tocar(tester, const Key('cliente_cliente-1'));
      await tocar(tester, const Key('agregar_p-sopa|CAJA'));
      await tocar(tester, const Key('boton_ver_carrito'));
      await tocar(tester, const Key('boton_cobrar'));

      await tocarConProgreso(tester, const Key('boton_imprimir'));
      expect(find.byKey(const Key('aviso_impresa')), findsOneWidget);
      expect(impresora.enviados, hasLength(1));
    });
  });

  group('la vista previa en el teléfono', () {
    testWidgets('muestra el ticket con el ancho del papel marcado',
        (tester) async {
      await cobrar(tester, carpeta: temporal);
      await tocarConProgreso(tester, const Key('boton_imprimir'));
      await tocar(tester, const Key('boton_ver_ticket'));

      expect(find.byKey(const Key('papel_del_ticket')), findsOneWidget);
      // El folio va espaciado por el ancho doble (ver la nota de arriba), así
      // que se busca una línea de ancho normal.
      expect(textoQueContiene('DOCUMENTO NO FISCAL'), findsOneWidget);
      expect(textoQueContiene('2 CAJA x 296.00'), findsOneWidget);
      // La ficha de bytes y columnas.
      expect(textoQueContiene('32 columnas'), findsOneWidget);
      expect(textoQueContiene('PC437'), findsOneWidget);
    });

    testWidgets('no ofrece ver el ticket antes de armarlo', (tester) async {
      // Antes de la primera impresión no hay bytes que mostrar.
      await cobrar(tester, carpeta: temporal);
      expect(find.byKey(const Key('boton_ver_ticket')), findsNothing);
    });

    testWidgets('delata un ticket sin tabla de códigos', (tester) async {
      // Sin ESC t los acentos salen como le toque a la impresora. La pantalla lo
      // marca en rojo en vez de dejar que se descubra en el papel.
      await montarApp(tester, credencial: credencialDelServidor());
      await entrarCon(tester, pinCorrecto);

      // Se monta la pantalla directo con un ticket mal armado.
      await tester.pumpWidget(
        MaterialApp(
          home: PantallaVistaTicket(
            bytes: (ConstructorEscPos()..linea('sin tabla')).bytes,
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('aviso_sin_tabla')), findsOneWidget);
    });
  });
}

/// Espera a que un archivo contenga lo que se busca.
///
/// La escritura a disco no tarda lo mismo en cada máquina. Una prueba que espera
/// una pausa fija pasa en la laptop y falla en el CI cada tantas corridas, y ese
/// tipo de fallo intermitente acaba con que alguien desactive la prueba.
Future<String> esperarContenido(
  WidgetTester tester,
  File archivo,
  String fragmento, {
  Duration limite = const Duration(seconds: 5),
}) async {
  var contenido = '';
  await tester.runAsync(() async {
    final hasta = DateTime.now().add(limite);
    while (DateTime.now().isBefore(hasta)) {
      if (archivo.existsSync()) {
        contenido = archivo.readAsStringSync();
        if (contenido.contains(fragmento)) return;
      }
      await Future<void>.delayed(const Duration(milliseconds: 25));
    }
  });
  return contenido;
}
