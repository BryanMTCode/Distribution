/// El catálogo de una visita.
///
/// Lo que estas pruebas cuidan es lo que se rompe en la calle, no lo que se ve
/// bonito en el emulador: que el precio esté a la vista sin tocar nada, que la
/// existencia del camión baje mientras el vendedor agrega, y que cuando no
/// alcanza se le diga **cuánto sí cabe en la unidad que está usando**.
library;

import 'package:dsd_app/src/datos/base_local.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

/// Entra como vendedor y abre la visita del cliente sembrado.
Future<void> abrirCatalogo(
  WidgetTester tester, {
  void Function(BaseLocal base)? sembrarExtra,
  bool conListaEnCliente = true,
  double existenciaSopa = 240,
  bool permiteCredito = true,
  double limite = 5000,
  double saldoCache = 0,
  bool bloqueado = false,
  bool conCarga = true,
}) async {
  await montarApp(
    tester,
    credencial: credencialDelServidor(),
    sembrar: (base) {
      sembrarEscenarioDeVenta(
        base,
        conListaEnCliente: conListaEnCliente,
        existenciaSopa: conCarga ? existenciaSopa : 0,
        permiteCredito: permiteCredito,
        limite: limite,
        saldoCache: saldoCache,
        bloqueado: bloqueado,
      );
      if (!conCarga) {
        base.db.execute('DELETE FROM existencias_camion');
      }
      sembrarExtra?.call(base);
    },
  );
  await entrarCon(tester, pinCorrecto);
  await tocar(tester, const Key('cliente_cliente-1'));
}

void main() {
  group('abrir la visita', () {
    testWidgets('tocar un cliente abre su catálogo con su nombre',
        (tester) async {
      await abrirCatalogo(tester);

      // El catálogo es DE un cliente: el precio depende de su lista y el
      // crédito de su saldo. Un catálogo "en general" mostraría precios que no
      // son de nadie.
      expect(find.text('Abarrotes Doña Mary'), findsOneWidget);
      expect(find.byKey(const Key('lista_catalogo')), findsOneWidget);
    });

    testWidgets('el precio se ve sin tocar nada, en las dos presentaciones',
        (tester) async {
      await abrirCatalogo(tester);

      // La caja a precio exacto, y la pieza con sus cuatro decimales.
      expect(find.text('\$296.00'), findsOneWidget);
      // 12.3333 NO se acorta a 12.33: eso haría creer que la caja de 24 cuesta
      // 295.92 en vez de 296.00.
      expect(find.text('\$12.3333'), findsOneWidget);
    });

    testWidgets('muestra cuánto queda en el camión', (tester) async {
      await abrirCatalogo(tester, existenciaSopa: 240);
      expect(
        find.descendant(
          of: find.byKey(const Key('existencia_p-sopa')),
          matching: find.text('240'),
        ),
        findsOneWidget,
      );
    });
  });

  group('agregar al pedido', () {
    testWidgets('el total sale del precio de la lista, no de la pantalla',
        (tester) async {
      await abrirCatalogo(tester);
      await tocar(tester, const Key('agregar_p-sopa|CAJA'));

      // Una caja a 296.0000. No hay campo donde el vendedor pueda escribir otro
      // precio: el precio viaja dentro de la presentación (ADR 0002 §7).
      expect(
        textoDe(tester, const Key('total_catalogo')),
        equals('\$296.00'),
      );
    });

    testWidgets('sumar piezas usa el precio de 4 decimales y redondea al final',
        (tester) async {
      await abrirCatalogo(tester);
      // 24 piezas a 12.3333 = 296.00 exactos. Si el precio se hubiera guardado
      // en centavos daría 295.92.
      await tocar(tester, const Key('agregar_p-sopa|PZA'));
      for (var i = 0; i < 23; i++) {
        await tocar(tester, const Key('mas_p-sopa|PZA'));
      }

      expect(find.byKey(const Key('cantidad_p-sopa|PZA')), findsOneWidget);
      expect(
        textoDe(tester, const Key('total_catalogo')),
        equals('\$296.00'),
      );
    });

    testWidgets('la existencia baja mientras se agrega', (tester) async {
      await abrirCatalogo(tester, existenciaSopa: 240);
      await tocar(tester, const Key('agregar_p-sopa|CAJA'));

      // Se comprometieron 24 unidades base: quedan 216. Mostrar 240 todavía
      // dejaría al vendedor prometiendo mercancía que él mismo ya apartó.
      expect(
        find.descendant(
          of: find.byKey(const Key('existencia_p-sopa')),
          matching: find.text('216'),
        ),
        findsOneWidget,
      );
    });

    testWidgets('el contador reemplaza al botón de agregar', (tester) async {
      await abrirCatalogo(tester);
      expect(find.byKey(const Key('agregar_p-sopa|PZA')), findsOneWidget);

      await tocar(tester, const Key('agregar_p-sopa|PZA'));
      expect(find.byKey(const Key('agregar_p-sopa|PZA')), findsNothing);
      expect(find.byKey(const Key('cantidad_p-sopa|PZA')), findsOneWidget);
    });

    testWidgets('bajar a cero quita la línea y vuelve el botón', (tester) async {
      await abrirCatalogo(tester);
      await tocar(tester, const Key('agregar_p-sopa|PZA'));
      await tocar(tester, const Key('menos_p-sopa|PZA'));

      expect(find.byKey(const Key('agregar_p-sopa|PZA')), findsOneWidget);
      expect(
        textoDe(tester, const Key('total_catalogo')),
        equals('\$0.00'),
      );
    });
  });

  group('el camión no da crédito de mercancía', () {
    testWidgets('al no alcanzar, dice cuánto sí cabe en la unidad que se usa',
        (tester) async {
      // Quedan 100 piezas y el vendedor está vendiendo cajas de 24: caben 4.
      // Decirle "quedan 100" lo obliga a dividir de cabeza frente al cliente.
      await abrirCatalogo(tester, existenciaSopa: 100);

      // El primer toque es el botón de agregar; después ya es el contador.
      await tocar(tester, const Key('agregar_p-sopa|CAJA'));
      for (var i = 1; i < 4; i++) {
        await tocar(tester, const Key('mas_p-sopa|CAJA'));
      }
      // La quinta caja no cabe: 5 × 24 = 120 contra 100 disponibles.
      await tocar(tester, const Key('mas_p-sopa|CAJA'));

      expect(textoQueContiene('Solo quedan 4'), findsOneWidget);
      // Y el pedido se quedó en 4: un rechazo no deja rastro.
      expect(
        textoDe(tester, const Key('cantidad_p-sopa|CAJA')),
        equals('4'),
      );
    });

    testWidgets('un producto agotado no se puede agregar, pero se ve',
        (tester) async {
      await abrirCatalogo(tester, existenciaSopa: 0);

      // Se ve —el vendedor necesita saber que existe para pedirlo mañana— pero
      // el botón está muerto.
      expect(find.text('Sopa de fideo 70 g'), findsOneWidget);
      expect(find.text('Agotado'), findsOneWidget);
      final boton = tester.widget<ButtonStyleButton>(
        find.byKey(const Key('agregar_p-sopa|PZA')),
      );
      expect(boton.onPressed, isNull);
    });

    testWidgets('sin carga activa se dice con esas palabras', (tester) async {
      await abrirCatalogo(tester, conCarga: false);

      // No es un error de la app, y no se le presenta como tal.
      expect(find.byKey(const Key('aviso_sin_carga')), findsOneWidget);
      expect(textoQueContiene('no trae carga'), findsOneWidget);
    });
  });

  group('la lista de precios', () {
    testWidgets('un cliente recién dado de alta se cotiza con la general',
        (tester) async {
      // Este es EL caso: el vendedor registra una tienda en la calle y le quiere
      // vender en ese momento. El cliente nace sin lista —la asigna el servidor
      // al confirmarlo—, y negarle la venta sería lo contrario de para qué
      // existe el alta en campo.
      await abrirCatalogo(tester, conListaEnCliente: false);

      expect(find.byKey(const Key('aviso_lista_por_omision')), findsOneWidget);
      // Y sí puede venderle.
      await tocar(tester, const Key('agregar_p-sopa|CAJA'));
      expect(
        textoDe(tester, const Key('total_catalogo')),
        equals('\$296.00'),
      );
    });

    testWidgets('sin ninguna lista sincronizada se explica qué hacer',
        (tester) async {
      await montarApp(
        tester,
        credencial: credencialDelServidor(),
        sembrar: (base) {
          sembrarCliente(base, id: 'cliente-1', nombre: 'Tienda sin catálogo',
              secuencia: 1);
        },
      );
      await entrarCon(tester, pinCorrecto);
      await tocar(tester, const Key('cliente_cliente-1'));

      expect(find.byKey(const Key('aviso_sin_lista')), findsOneWidget);
      expect(textoQueContiene('Sincroniza una vez'), findsOneWidget);
    });
  });

  group('la búsqueda', () {
    testWidgets('filtra sin señal', (tester) async {
      await abrirCatalogo(tester, sembrarExtra: (base) {
        sembrarProducto(base,
            id: 'p-frijol', nombre: 'Frijol bayo 1 kg', precioPieza: 32.5);
        sembrarCarga(base, productoId: 'p-frijol', unidadesBase: 40);
      });

      expect(find.text('Frijol bayo 1 kg'), findsOneWidget);

      await tester.enterText(
        find.byKey(const Key('campo_busqueda_catalogo')),
        'frijol',
      );
      await tester.pumpAndSettle();

      expect(find.text('Frijol bayo 1 kg'), findsOneWidget);
      expect(find.text('Sopa de fideo 70 g'), findsNothing);
    });

    testWidgets('una búsqueda sin resultados lo dice', (tester) async {
      await abrirCatalogo(tester);
      await tester.enterText(
        find.byKey(const Key('campo_busqueda_catalogo')),
        'refrigerador',
      );
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('catalogo_vacio')), findsOneWidget);
    });
  });

  group('el carrito pertenece a la visita', () {
    testWidgets('cambiar de cliente empieza un pedido nuevo', (tester) async {
      // Arrastrar líneas de una tienda a la siguiente sería la forma más rápida
      // de facturarle a quien no pidió nada.
      await abrirCatalogo(tester, sembrarExtra: (base) {
        sembrarCliente(base,
            id: 'cliente-2',
            nombre: 'Tienda del Mercado',
            secuencia: 2,
            codigo: 'C-002');
        base.db.execute(
          "UPDATE clientes SET lista_precios_id = 'lista-general' "
          "WHERE id = 'cliente-2'",
        );
      });

      await tocar(tester, const Key('agregar_p-sopa|CAJA'));
      expect(
        textoDe(tester, const Key('total_catalogo')),
        equals('\$296.00'),
      );

      // Volver a la ruta y entrar con otro cliente.
      await tester.pageBack();
      await tester.pumpAndSettle();
      await tocar(tester, const Key('cliente_cliente-2'));

      expect(
        textoDe(tester, const Key('total_catalogo')),
        equals('\$0.00'),
      );
    });
  });

  group('el botón de ver el pedido', () {
    testWidgets('está muerto mientras no haya nada', (tester) async {
      await abrirCatalogo(tester);
      final boton = tester.widget<ButtonStyleButton>(
        find.byKey(const Key('boton_ver_carrito')),
      );
      expect(boton.onPressed, isNull);
    });

    testWidgets('con productos lleva al pedido', (tester) async {
      await abrirCatalogo(tester);
      await tocar(tester, const Key('agregar_p-sopa|CAJA'));
      await tocar(tester, const Key('boton_ver_carrito'));

      expect(find.text('Pedido'), findsOneWidget);
      expect(find.byKey(const Key('total_pedido')), findsOneWidget);
    });
  });
}
