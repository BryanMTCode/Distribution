/// Registrar el pago de un cliente, de punta a punta en la pantalla.
///
/// Estas pruebas recorren el camino que el vendedor hace de verdad: entra, ve en
/// la lista de ruta a quién le debe, toca cobrar, teclea el importe y entrega el
/// recibo.
///
/// Lo que defienden, y que no se ve probando solo el dominio:
///
/// · Que el botón de cobrar **solo aparezca cuando el cliente debe**. El día de
///   cobranza lo que se busca es encontrar rápido a quién cobrarle.
/// · Que el saldo se muestre **con su antigüedad**. Un número sin fecha se trata
///   como la verdad, y éste es una caché que puede tener horas (§0.3).
/// · Que **se pueda cobrar más de lo que dice que debe**: el dinero está sobre el
///   mostrador, y si la pantalla lo impidiera el vendedor se guardaría efectivo
///   sin documento.
/// · Que el recibo se imprima **a un toque, después de guardar**, y que el
///   reintento esté en la misma pantalla.
library;

import 'dart:io';

import 'package:dsd_app/src/datos/base_local.dart';

import 'package:dsd_app/src/datos/impresora.dart';
import 'package:dsd_app/src/estado/carrito.dart';
import 'package:dsd_app/src/pantallas/vendedor/abono.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

/// Entra y abre la pantalla de pago del cliente sembrado.
///
/// `carpeta` es para las pruebas que imprimen: la impresora simulada escribe un
/// archivo, y sin una carpeta propia usaría `path_provider`, que en un entorno de
/// pruebas no tiene canal de plataforma. El síntoma es una impresión que "falla"
/// sin razón aparente y un `impreso` que se queda en cero.
Future<BaseLocal> irAlPago(
  WidgetTester tester, {
  double saldoCache = 2000,
  bool conFoliosDeCobro = true,
  int foliosRestantes = 500,
  Directory? carpeta,
}) async {
  return montarApp(
    tester,
    credencial: credencialDelServidor(),
    sembrar: (b) {
      sembrarEscenarioDeVenta(b, saldoCache: saldoCache);
      sembrarParaCobrar(b);
      if (conFoliosDeCobro) {
        sembrarFoliosDeCobro(
          b,
          hasta: foliosRestantes,
          consumidoHasta: 0,
        );
      }
      // La antigüedad del saldo: se muestra siempre, y la pantalla la lee de aquí.
      b.db.execute(
        "UPDATE clientes SET saldo_cache_en = '2026-09-24T07:00:00.000Z'",
      );
    },
    extras: [
      if (carpeta != null)
        impresoraProvider.overrideWithValue(ImpresoraSimulada(carpeta: carpeta)),
    ],
  ).then((base) async {
    await entrarCon(tester, pinCorrecto);
    await tocar(tester, const Key('cobrar_cliente-1'));
    return base;
  });
}

/// Entra, abre el pago y registra el importe.
Future<BaseLocal> pagar(
  WidgetTester tester,
  String importe, {
  double saldoCache = 2000,
  Directory? carpeta,
}) async {
  final base = await irAlPago(tester, saldoCache: saldoCache, carpeta: carpeta);
  await escribirEn(tester, const Key('campo_importe'), importe);
  await tocar(tester, const Key('registrar_pago'));
  return base;
}

void main() {
  late Directory temporal;

  setUp(() {
    // La impresora simulada escribe archivos. Sin una carpeta propia usaría
    // `path_provider`, que en pruebas no tiene canal de plataforma.
    temporal = Directory.systemTemp.createTempSync('recibos_de_prueba');
  });

  tearDown(() {
    if (temporal.existsSync()) temporal.deleteSync(recursive: true);
  });

  group('llegar a la pantalla', () {
    testWidgets('el botón de cobrar aparece cuando el cliente DEBE', (tester) async {
      await montarApp(
        tester,
        credencial: credencialDelServidor(),
        sembrar: (b) {
          sembrarEscenarioDeVenta(b, saldoCache: 2000);
          sembrarParaCobrar(b);
          sembrarFoliosDeCobro(b);
        },
      );
      await entrarCon(tester, pinCorrecto);

      expect(find.byKey(const Key('cobrar_cliente-1')), findsOneWidget);
    });

    testWidgets('y NO aparece cuando no debe nada', (tester) async {
      // Ofrecerlo siempre llenaría la lista de botones que no hacen nada en la
      // mayoría de los renglones, y el día de cobranza se busca lo contrario.
      await montarApp(
        tester,
        credencial: credencialDelServidor(),
        sembrar: (b) {
          sembrarEscenarioDeVenta(b, saldoCache: 0);
          sembrarParaCobrar(b);
          sembrarFoliosDeCobro(b);
        },
      );
      await entrarCon(tester, pinCorrecto);

      expect(find.byKey(const Key('cobrar_cliente-1')), findsNothing);
    });
  });

  group('lo que el vendedor ve antes de cobrar', () {
    testWidgets('el saldo va CON su antigüedad', (tester) async {
      // Un número sin fecha se trata como la verdad. Éste es una caché: no
      // incluye los cobros de otros equipos ni lo que la oficina capturó a mano.
      await irAlPago(tester);

      expect(find.text('\$2000.00'), findsOneWidget);
      expect(find.textContaining('Actualizado hace'), findsOneWidget);
    });

    testWidgets('ofrece cobrar todo como SUGERENCIA', (tester) async {
      await irAlPago(tester);

      await tester.tap(find.textContaining('Cobrar todo'));
      await tester.pumpAndSettle();

      expect(find.text('2000.00'), findsOneWidget);
    });

    testWidgets('dice que se puede cobrar de más', (tester) async {
      await irAlPago(tester);
      expect(
        find.textContaining('Se puede cobrar más de lo que dice que debe'),
        findsOneWidget,
      );
    });

    testWidgets('avisa cuando quedan pocos folios de cobro', (tester) async {
      // Quedarse sin folios a media ruta deja al vendedor sin poder recibir
      // dinero, y la única salida es encontrar señal.
      await irAlPago(tester, foliosRestantes: 20);
      expect(find.textContaining('folios de cobro'), findsOneWidget);
    });
  });

  group('registrar el pago', () {
    testWidgets('se guarda y aparece su folio, grande', (tester) async {
      await irAlPago(tester);

      await escribirEn(tester, const Key('campo_importe'), '500');
      await tocar(tester, const Key('registrar_pago'));

      // El folio es lo que el cliente anota y lo que la oficina pide por teléfono.
      expect(find.text('VEND01-000001'), findsOneWidget);
      expect(find.textContaining('500.00'), findsWidgets);
      expect(find.text('Pago registrado'), findsOneWidget);
    });

    testWidgets('"500" se lee como 500.00', (tester) async {
      // Quien teclea 500 quiere decir 500.00. `Dinero.deTexto` exige dos
      // decimales exactos, así que la pantalla normaliza antes — y ningún `double`
      // toca el número.
      await irAlPago(tester);
      await escribirEn(tester, const Key('campo_importe'), '500');
      await tocar(tester, const Key('registrar_pago'));

      expect(find.textContaining('\$500.00'), findsWidgets);
    });

    testWidgets('"1,250.5" se lee como 1250.50', (tester) async {
      await irAlPago(tester);
      await escribirEn(tester, const Key('campo_importe'), '1,250.5');
      await tocar(tester, const Key('registrar_pago'));

      expect(find.textContaining('\$1250.50'), findsWidgets);
    });

    testWidgets('COBRAR MÁS DE LO QUE DEBE SE PERMITE', (tester) async {
      // El dinero está sobre el mostrador. Si la pantalla lo impidiera, el
      // vendedor se guardaría efectivo sin documento.
      await irAlPago(tester, saldoCache: 2000);
      await escribirEn(tester, const Key('campo_importe'), '5000');
      await tocar(tester, const Key('registrar_pago'));

      expect(find.text('Pago registrado'), findsOneWidget);
      expect(find.textContaining('\$5000.00'), findsWidgets);
    });

    testWidgets('un importe vacío se explica y no guarda nada', (tester) async {
      await irAlPago(tester);
      await tocar(tester, const Key('registrar_pago'));

      expect(find.text('Escribe cuánto está pagando.'), findsOneWidget);
      expect(find.text('Pago registrado'), findsNothing);
    });

    testWidgets('un importe en cero se explica', (tester) async {
      await irAlPago(tester);
      await escribirEn(tester, const Key('campo_importe'), '0');
      await tocar(tester, const Key('registrar_pago'));

      expect(find.text('El importe tiene que ser mayor que cero.'), findsOneWidget);
    });

    testWidgets('sin folios de cobro lo dice y manda a sincronizar', (tester) async {
      await irAlPago(tester, conFoliosDeCobro: false);
      await escribirEn(tester, const Key('campo_importe'), '500');
      await tocar(tester, const Key('registrar_pago'));

      expect(find.textContaining('no tiene folios de cobro'), findsOneWidget);
      expect(find.textContaining('Sincroniza'), findsWidgets);
    });
  });

  group('la forma de pago', () {
    testWidgets('una transferencia pide referencia', (tester) async {
      await irAlPago(tester);
      await tester.tap(find.text('Transferencia'));
      await tester.pumpAndSettle();

      expect(find.textContaining('encontrar el pago en el banco'), findsOneWidget);
    });

    testWidgets('una transferencia SIN referencia no se guarda', (tester) async {
      // Sin referencia la oficina tendría un abono registrado y ninguna forma de
      // encontrarlo en el estado de cuenta.
      await irAlPago(tester);
      await tester.tap(find.text('Transferencia'));
      await tester.pumpAndSettle();
      await escribirEn(tester, const Key('campo_importe'), '500');
      await tocar(tester, const Key('registrar_pago'));

      expect(find.textContaining('Escribe la referencia'), findsOneWidget);
      expect(find.text('Pago registrado'), findsNothing);
    });

    testWidgets('con referencia se guarda y sale en el recibo', (tester) async {
      await irAlPago(tester);
      await tester.tap(find.text('Transferencia'));
      await tester.pumpAndSettle();

      await escribirEn(tester, const Key('campo_importe'), '500');
      await escribirEn(tester, const Key('campo_referencia'), 'SPEI-77231');
      await tocar(tester, const Key('registrar_pago'));

      expect(find.text('Pago registrado'), findsOneWidget);
      expect(find.textContaining('SPEI-77231'), findsWidgets);
      expect(find.textContaining('Transferencia'), findsWidgets);
    });

    testWidgets('el efectivo no pide referencia', (tester) async {
      await irAlPago(tester);
      expect(find.textContaining('encontrar el pago en el banco'), findsNothing);
    });
  });

  group('el recibo', () {
    testWidgets('LA IMPRESIÓN NO ES AUTOMÁTICA', (tester) async {
      // Igual que la venta (ADR 0002 §8). Si fuera automática y la impresora
      // estuviera sin papel, el cobro ya estaría escrito y el vendedor se quedaría
      // sin un lugar obvio desde dónde reintentar, con el cliente enfrente.
      final base = await pagar(tester, '500');

      // Guardado, y NO impreso.
      final fila = base.db.select('SELECT impreso FROM cobros').single;
      expect(fila['impreso'], equals(0));
      expect(find.text('Imprimir recibo'), findsOneWidget);
    });

    testWidgets('al imprimir se marca y ofrece otra copia', (tester) async {
      final base = await pagar(tester, '500', carpeta: temporal);
      await tocarConProgreso(tester, const Key('imprimir_recibo'));

      final fila = base.db.select('SELECT impreso, ticket_escpos FROM cobros').single;
      expect(fila['impreso'], equals(1));
      expect(fila['ticket_escpos'], isNotNull);
      // El reintento es el mismo botón, tantas veces como haga falta.
      expect(find.text('Imprimir otra copia'), findsOneWidget);
    });

    testWidgets('se puede ver el recibo en pantalla sin impresora', (tester) async {
      await irAlPago(tester);
      await escribirEn(tester, const Key('campo_importe'), '500');
      await tocar(tester, const Key('registrar_pago'));

      await tester.tap(find.text('Ver el recibo en pantalla'));
      await tester.pumpAndSettle();

      expect(find.textContaining('RECIBO DE PAGO'), findsWidgets);
    });

    testWidgets('el recibo NO muestra el saldo resultante', (tester) async {
      // Por la misma razón que la remisión (§11): el teléfono solo trae una caché.
      // Un saldo viejo en un papel que el cliente conserva es una disputa.
      await irAlPago(tester, saldoCache: 2000);
      await escribirEn(tester, const Key('campo_importe'), '500');
      await tocar(tester, const Key('registrar_pago'));
      await tester.tap(find.text('Ver el recibo en pantalla'));
      await tester.pumpAndSettle();

      expect(find.textContaining('1500'), findsNothing);
      expect(find.textContaining('Consulta tu saldo'), findsWidgets);
    });
  });

  group('lo que queda en la cola', () {
    testWidgets('el cobro encolado baja el saldo que ve el vendedor', (tester) async {
      // El `saldo_cache` NO se toca —es zona espejo—, pero el número que se
      // muestra sí baja, porque se compone restando los cobros sin sincronizar.
      // Sin eso el vendedor volvería a cobrarle al mismo cliente.
      final base = await pagar(tester, '500');
      await tester.tap(find.text('Listo'));
      await tester.pumpAndSettle();

      // La caché del servidor, intacta.
      expect(
        base.db.select('SELECT saldo_cache FROM clientes').single['saldo_cache'],
        equals(2000.0),
      );
      // Y el sobre, en la cola.
      final sobre = base.db.select('SELECT tipo FROM outbox').single;
      expect(sobre['tipo'], equals('cobro.crear'));
    });
  });

  group('la antigüedad del saldo', () {
    test('se dice en minutos, horas o días, y siempre se dice', () {
      final ahora = DateTime.parse('2026-09-29T12:00:00Z');

      expect(antiguedadDelSaldo(null), contains('Nunca'));
      expect(
        antiguedadDelSaldo('2026-09-29T11:59:30Z', ahora: ahora),
        contains('ahora'),
      );
      expect(
        antiguedadDelSaldo('2026-09-29T11:20:00Z', ahora: ahora),
        contains('40 min'),
      );
      expect(
        antiguedadDelSaldo('2026-09-29T07:00:00Z', ahora: ahora),
        contains('5 h'),
      );
      // Con un día o más, el aviso cambia de tono: ya no es "puede haber
      // cambiado", es "no discutas el saldo con el cliente".
      final viejo = antiguedadDelSaldo('2026-09-27T12:00:00Z', ahora: ahora);
      expect(viejo, contains('2 días'));
      expect(viejo, contains('Sincroniza'));
    });
  });
}
