/// El cobro: del carrito al documento.
///
/// Estas pruebas recorren la pantalla completa —entrar, abrir la visita,
/// agregar, cobrar— porque es donde se junta todo lo que puede fallar en la
/// calle: el crédito, la existencia del camión, los folios y el GPS.
///
/// Y verifican la decisión de negocio de septiembre 2026: **la impresión no es
/// automática**. El vendedor toca "Imprimir" después de que la venta se guardó.
library;

import 'package:dsd_app/src/datos/base_local.dart';
import 'package:dsd_app/src/datos/servicio_ubicacion.dart';
import 'package:dsd_app/src/estado/alta.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

/// Entra, abre la visita, agrega cajas y va al pedido.
Future<BaseLocal> irAlPedido(
  WidgetTester tester, {
  int cajas = 1,
  bool conFolios = true,
  bool aCredito = false,
  double limite = 5000,
  double saldoCache = 0,
  bool permiteCredito = true,
  LecturaGps gps = const GpsSinLectura(),
  void Function(BaseLocal base)? sembrarExtra,
}) async {
  late BaseLocal base;
  base = await montarApp(
    tester,
    credencial: credencialDelServidor(),
    sembrar: (b) {
      sembrarEscenarioDeVenta(
        b,
        permiteCredito: permiteCredito,
        limite: limite,
        saldoCache: saldoCache,
      );
      if (conFolios) sembrarParaCobrar(b);
      sembrarExtra?.call(b);
    },
    extras: [
      servicioUbicacionProvider.overrideWithValue(
        ServicioUbicacionFalso.siempre(gps),
      ),
    ],
  );
  await entrarCon(tester, pinCorrecto);
  await tocar(tester, const Key('cliente_cliente-1'));

  await tocar(tester, const Key('agregar_p-sopa|CAJA'));
  for (var i = 1; i < cajas; i++) {
    await tocar(tester, const Key('mas_p-sopa|CAJA'));
  }
  await tocar(tester, const Key('boton_ver_carrito'));

  if (aCredito) {
    await tester.tap(find.text('Crédito'));
    await tester.pumpAndSettle();
  }
  return base;
}

void main() {
  group('cobrar de contado', () {
    testWidgets('la venta se guarda y aparece su folio, grande', (tester) async {
      final base = await irAlPedido(tester, cajas: 2);

      expect(find.text('Cobrar de contado'), findsOneWidget);
      await tocar(tester, const Key('boton_cobrar'));

      // El folio es lo que el cliente anota y lo que la oficina pide por
      // teléfono cuando algo se aclara.
      expect(
        textoDe(tester, const Key('folio_de_la_venta')),
        equals('VEND01-000001'),
      );
      expect(textoDe(tester, const Key('total_de_la_venta')), equals('\$592.00'));

      final db = BaseLocalDePrueba(base);
      expect(db.contar('ventas'), equals(1));
      expect(db.contar('venta_partidas'), equals(1));
      // Y su sobre en la cola, en la misma transacción.
      expect(db.contar("outbox WHERE estado = 'pendiente'"), equals(1));
    });
  });

  group('lo que el vendedor ve después', () {
    testWidgets('dice que está guardada y que se envía sola', (tester) async {
      await irAlPedido(tester);
      await tocar(tester, const Key('boton_cobrar'));

      expect(find.byKey(const Key('aviso_pendiente_de_enviar')), findsOneWidget);
      expect(textoQueContiene('cuando haya señal'), findsOneWidget);
    });

    testWidgets('el carrito quedó vacío: no se puede cobrar dos veces',
        (tester) async {
      // Tocar atrás y volver a cobrar sería facturar dos veces la misma
      // mercancía.
      final base = await irAlPedido(tester);
      await tocar(tester, const Key('boton_cobrar'));
      await tocar(tester, const Key('boton_terminar_visita'));

      expect(find.text('Mi ruta'), findsOneWidget);
      expect(BaseLocalDePrueba(base).contar('carrito_borrador'), equals(0));
      expect(BaseLocalDePrueba(base).contar('ventas'), equals(1));
    });

    testWidgets('avisa cuando quedan pocos folios', (tester) async {
      // Quedarse sin folios a media ruta significa no poder vender.
      await irAlPedido(
        tester,
        sembrarExtra: (b) => sembrarParaCobrar(b, hasta: 60, consumidoHasta: 20),
      );
      await tocar(tester, const Key('boton_cobrar'));

      expect(find.byKey(const Key('aviso_pocos_folios')), findsOneWidget);
      expect(textoQueContiene('39 folios'), findsOneWidget);
    });

    testWidgets('con folios de sobra no estorba con el aviso', (tester) async {
      await irAlPedido(tester);
      await tocar(tester, const Key('boton_cobrar'));
      expect(find.byKey(const Key('aviso_pocos_folios')), findsNothing);
    });
  });

  group('la impresión', () {
    testWidgets('NO es automática: hay que tocar el botón', (tester) async {
      // Decisión de negocio (ADR 0002 §8). Si imprimiera sola y la impresora
      // estuviera sin papel, la venta ya estaría escrita y el vendedor no
      // tendría un lugar obvio desde dónde reintentar.
      //
      // Lo que pasa AL TOCAR el botón —los bytes, el registro, los caminos de
      // falla— vive en `impresion_test.dart`, que inyecta una impresora simulada
      // con su carpeta propia. Aquí solo se comprueba que no imprima sola.
      final base = await irAlPedido(tester);
      await tocar(tester, const Key('boton_cobrar'));

      expect(
        BaseLocalDePrueba(base).unaFila('SELECT impreso FROM ventas')['impreso'],
        equals(0),
      );
      expect(find.text('Imprimir remisión'), findsOneWidget);
    });
  });

  group('el geosello', () {
    testWidgets('con GPS la venta se sella con la ubicación', (tester) async {
      final base = await irAlPedido(
        tester,
        gps: GpsObtenido(
          Ubicacion(
            lat: 19.4326,
            lng: -99.1332,
            origen: OrigenUbicacion.gps,
            precisionMetros: 8.5,
          ),
        ),
      );
      await tocar(tester, const Key('boton_cobrar'));

      final venta = BaseLocalDePrueba(base).unaFila(
        'SELECT lat, lng, ubicacion_precision_m FROM ventas',
      );
      expect(venta['lat'], closeTo(19.4326, 0.0000001));
      expect(venta['ubicacion_precision_m'], equals(8.5));
    });

    testWidgets('sin señal de satélite la venta se cierra igual', (tester) async {
      // Dentro de un mercado techado no hay satélite. Exigir coordenadas sería
      // impedir vender justo donde más se vende; el servidor la marca para
      // revisión y ya.
      final base = await irAlPedido(tester, gps: const GpsSinLectura());
      await tocar(tester, const Key('boton_cobrar'));

      expect(
        BaseLocalDePrueba(base).unaFila('SELECT lat FROM ventas')['lat'],
        isNull,
      );
      expect(find.byKey(const Key('folio_de_la_venta')), findsOneWidget);
    });
  });

  group('lo que impide cobrar', () {
    testWidgets('sin folios asignados se dice qué hacer', (tester) async {
      final base = await irAlPedido(tester, conFolios: false);
      await tocar(tester, const Key('boton_cobrar'));

      expect(textoQueContiene('no tiene folios asignados'), findsOneWidget);
      expect(BaseLocalDePrueba(base).contar('ventas'), equals(0));
      // Y el pedido sigue ahí: no se perdió nada.
      expect(find.byKey(const Key('total_pedido')), findsOneWidget);
    });

    testWidgets('con el crédito pasado, el botón está muerto', (tester) async {
      await irAlPedido(
        tester,
        cajas: 2,
        aCredito: true,
        limite: 500,
        saldoCache: 100,
      );

      final boton = tester.widget<ButtonStyleButton>(
        find.byKey(const Key('boton_cobrar')),
      );
      expect(boton.onPressed, isNull);
    });

    testWidgets('si la mercancía se acabó entre armar y cobrar, no queda nada',
        (tester) async {
      // El caso real: un pull refrescó la carga a media visita.
      final base = await irAlPedido(tester, cajas: 2);
      base.db.execute('UPDATE existencias_camion SET cant_actual = 10');

      await tocar(tester, const Key('boton_cobrar'));

      expect(textoQueContiene('Ya no hay esa mercancía'), findsOneWidget);
      final db = BaseLocalDePrueba(base);
      expect(db.contar('ventas'), equals(0));
      expect(db.contar("outbox WHERE estado = 'pendiente'"), equals(0));
      // La existencia no se tocó y el folio no se quemó.
      expect(
        db.unaFila('SELECT cant_actual FROM existencias_camion')['cant_actual'],
        equals(10.0),
      );
    });
  });

  group('la última caja del camión', () {
    testWidgets('se vende aunque el saldo traiga deriva de punto flotante',
        (tester) async {
      // Bug de campo, octubre 2026: «cuando me queda solo un artículo se queda
      // cargando y no avanza la venta».
      //
      // `cant_actual` es REAL. Restarle venta tras venta deja el saldo en
      // 23.999999999999996 en vez de 24. El catálogo lo lee con
      // `Cantidad.deBase`, que redondea a milésimas, y ofrece la caja; la guarda
      // del cierre comparaba el REAL crudo y la negaba. Entre las dos, el
      // vendedor no podía vender lo último que traía.
      final base = await irAlPedido(
        tester,
        sembrarExtra: (b) => b.db.execute(
          'UPDATE existencias_camion SET cant_actual = 23.999999999999996',
        ),
      );

      await tocar(tester, const Key('boton_cobrar'));

      expect(find.byKey(const Key('folio_de_la_venta')), findsOneWidget);
      final db = BaseLocalDePrueba(base);
      expect(db.contar('ventas'), equals(1));
      // Y el camión queda en cero limpio: la deriva no pasa al día siguiente.
      expect(
        db.unaFila('SELECT cant_actual FROM existencias_camion')['cant_actual'],
        equals(0),
      );
    });
  });

  group('cuando falla el equipo, no el negocio', () {
    testWidgets('el botón vuelve a servir: nunca se queda girando',
        (tester) async {
      // La otra mitad del bug de octubre 2026. Cualquier excepción que no fuera
      // `VentaRechazada` dejaba el estado en `CobroEnCurso`: botón girando,
      // desactivado, y el vendedor sin poder cobrar el resto del día sin matar
      // la app.
      //
      // Se provoca dejando la base tomada, que es lo que pasa cuando la
      // sincronización está a medio escribir en el momento de cobrar.
      final base = await irAlPedido(tester, cajas: 2);
      base.db.execute('BEGIN IMMEDIATE');

      await tocar(tester, const Key('boton_cobrar'));

      // Se le dice qué pasó, y en diálogo: tiene que leerlo antes de volver a
      // tocar el botón.
      expect(find.byKey(const Key('aviso_cobro_roto')), findsOneWidget);
      expect(textoQueContiene('no quedó nada registrado'), findsOneWidget);

      await tocar(tester, const Key('entendido_cobro_roto'));

      // Y lo que importa: el botón está vivo y el pedido sigue armado.
      final boton = tester.widget<ButtonStyleButton>(
        find.byKey(const Key('boton_cobrar')),
      );
      expect(boton.onPressed, isNotNull);
      expect(find.byKey(const Key('total_pedido')), findsOneWidget);

      final db = BaseLocalDePrueba(base);
      expect(db.contar('ventas'), equals(0));
      expect(db.contar('outbox'), equals(0));
    });

    testWidgets('el segundo intento cobra, con el mismo folio', (tester) async {
      final base = await irAlPedido(tester, cajas: 2);
      base.db.execute('BEGIN IMMEDIATE');
      await tocar(tester, const Key('boton_cobrar'));
      await tocar(tester, const Key('entendido_cobro_roto'));

      await tocar(tester, const Key('boton_cobrar'));

      expect(
        textoDe(tester, const Key('folio_de_la_venta')),
        equals('VEND01-000001'),
        reason: 'el intento fallido no quemó el folio',
      );
      expect(BaseLocalDePrueba(base).contar('ventas'), equals(1));
    });
  });

  group('el borrador del carrito', () {
    testWidgets('sobrevive a que el sistema mate la app', (tester) async {
      // Veinte minutos en un mercado con la app en segundo plano alcanzan para
      // que Android la mate. Sin esto, el vendedor rearma quince renglones
      // frente al cliente — o los apunta en papel y deja de usar la app.
      final base = await irAlPedido(tester, cajas: 3);

      final guardado = BaseLocalDePrueba(base)
          .unaFila('SELECT cliente_id, lineas_json FROM carrito_borrador');
      expect(guardado['cliente_id'], equals('cliente-1'));
      expect(guardado['lineas_json'], contains('p-sopa'));
      // Con el precio con el que se cotizó, no con el del catálogo de después.
      expect(guardado['lineas_json'], contains('296.0000'));
    });

    testWidgets('al cobrar se limpia', (tester) async {
      final base = await irAlPedido(tester);
      expect(BaseLocalDePrueba(base).contar('carrito_borrador'), equals(1));

      await tocar(tester, const Key('boton_cobrar'));
      expect(BaseLocalDePrueba(base).contar('carrito_borrador'), equals(0));
    });
  });
}
