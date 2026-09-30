/// Alta de cliente desde la pantalla.
///
/// Las dos cosas que se prueban y no se pueden probar en otro lado:
///
/// 1. Que el GPS **nunca** bloquea el alta. Dentro de un mercado techado el
///    satélite no aparece, y la tienda existe igual: si la pantalla lo exigiera,
///    el vendedor simplemente no registraría al cliente.
/// 2. Que el aviso de posible duplicado llega ANTES de crearlo. En la calle el
///    vendedor sabe si la tienda de al lado es la misma; en la oficina, dos
///    semanas después y con dos historiales ya separados, nadie puede saberlo.
library;

import 'package:dsd_app/src/datos/servicio_ubicacion.dart';
import 'package:dsd_app/src/estado/alta.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

Ubicacion enLaCalle({double? precision = 8}) => Ubicacion(
      lat: 19.4326,
      lng: -99.1332,
      origen: OrigenUbicacion.gps,
      precisionMetros: precision,
    );

void main() {
  /// Abre la app, entra y navega al alta.
  Future<BaseLocalDePrueba> abrirAlta(
    WidgetTester tester, {
    required LecturaGps gps,
    void Function(dynamic base)? sembrar,
  }) async {
    var contador = 0;
    final base = await montarApp(
      tester,
      credencial: credencialDelServidor(),
      sembrar: sembrar,
      extras: [
        servicioUbicacionProvider
            .overrideWithValue(ServicioUbicacionFalso.siempre(gps)),
        uuidProvider.overrideWithValue(() => 'id-${++contador}'),
      ],
    );
    await entrarCon(tester, pinCorrecto);
    await tester.tap(find.byKey(const Key('boton_nuevo_cliente')));
    await tester.pumpAndSettle();
    return BaseLocalDePrueba(base);
  }

  // -------------------------------------------------------------------------
  // El formulario
  // -------------------------------------------------------------------------

  testWidgets('sin nombre no se guarda', (tester) async {
    // Es el único campo sin el que el registro no sirve para nada.
    final base = await abrirAlta(tester, gps: GpsObtenido(enLaCalle()));

    await tocar(tester, const Key('boton_guardar'));

    expect(find.text('Escribe el nombre del negocio'), findsOneWidget);
    expect(base.contar('clientes'), equals(0));
  });

  testWidgets('con nombre y GPS se guarda y encola', (tester) async {
    final base = await abrirAlta(tester, gps: GpsObtenido(enLaCalle()));

    await tester.enterText(
        find.byKey(const Key('campo_nombre')), 'Abarrotes Doña Mary');
    await escribirEn(tester, const Key('campo_telefono'), '5512345678');
    await escribirEn(tester, const Key('campo_calle'), 'Av. Hidalgo');
    await escribirEn(tester, const Key('campo_numero'), '145');
    await tocar(tester, const Key('boton_guardar'));

    // Volvió a la ruta, el cliente aparece, y la cola creció.
    expect(find.text('Mi ruta'), findsOneWidget);
    expect(find.text('Abarrotes Doña Mary'), findsOneWidget);
    expect(base.contar('outbox'), equals(1));
    expect(textoQueContiene('Por enviar: 1'), findsOneWidget);

    final c = base.unaFila('SELECT * FROM clientes');
    expect(c['direccion'], equals('Av. Hidalgo 145'));
    expect(c['ubicacion_origen'], equals('gps'));
    expect(c['es_local'], equals(1));
  });

  testWidgets('se avisa que se enviará al haber señal', (tester) async {
    await abrirAlta(tester, gps: GpsObtenido(enLaCalle()));
    await escribirEn(tester, const Key('campo_nombre'), 'Doña Mary');
    await tocar(tester, const Key('boton_guardar'));
    expect(textoQueContiene('Se enviará al haber señal'), findsOneWidget);
  });

  // -------------------------------------------------------------------------
  // El GPS no bloquea
  // -------------------------------------------------------------------------

  testWidgets('sin señal de GPS se guarda igual', (tester) async {
    final base = await abrirAlta(tester, gps: const GpsSinLectura());

    expect(textoQueContiene('No hay señal de GPS aquí'), findsOneWidget);
    expect(textoQueContiene('Puedes guardar sin coordenadas'), findsOneWidget);

    await escribirEn(tester, const Key('campo_nombre'), 'Del mercado');
    await tocar(tester, const Key('boton_guardar'));

    expect(base.contar('clientes'), equals(1));
    final c = base.unaFila('SELECT lat, ubicacion_origen FROM clientes');
    expect(c['lat'], isNull);
    expect(c['ubicacion_origen'], isNull);
  });

  testWidgets('sin permiso se explica qué hacer, y se puede guardar',
      (tester) async {
    final base = await abrirAlta(
      tester,
      gps: const GpsSinPermiso(definitivo: true),
    );
    expect(textoQueContiene('Actívalo en los ajustes'), findsOneWidget);

    await escribirEn(tester, const Key('campo_nombre'), 'Sin permiso');
    await tocar(tester, const Key('boton_guardar'));
    expect(base.contar('clientes'), equals(1));
  });

  testWidgets('con la ubicación apagada se dice cómo encenderla', (tester) async {
    await abrirAlta(tester, gps: const GpsApagado());
    expect(textoQueContiene('La ubicación está apagada'), findsOneWidget);
    expect(textoQueContiene('barra de notificaciones'), findsOneWidget);
  });

  testWidgets('una señal débil se marca sin impedir nada', (tester) async {
    await abrirAlta(tester, gps: GpsObtenido(enLaCalle(precision: 180)));
    expect(textoQueContiene('Señal débil'), findsOneWidget);
    expect(textoQueContiene('±180 m'), findsOneWidget);
    // Y el botón sigue activo.
    final boton = tester.widget<FilledButton>(find.byKey(const Key('boton_guardar')));
    expect(boton.onPressed, isNotNull);
  });

  // -------------------------------------------------------------------------
  // Corrección manual, sin mapa
  // -------------------------------------------------------------------------

  testWidgets('mover el punto cambia el origen a manual', (tester) async {
    final base = await abrirAlta(tester, gps: GpsObtenido(enLaCalle(precision: 200)));

    await tocar(tester, const Key('mover_norte'));
    expect(textoQueContiene('Movido 10 m'), findsOneWidget);
    expect(textoQueContiene('Puesta a mano'), findsOneWidget);

    await escribirEn(tester, const Key('campo_nombre'), 'Corregida');
    await tocar(tester, const Key('boton_guardar'));

    final c = base.unaFila(
        'SELECT ubicacion_origen, ubicacion_precision_m FROM clientes');
    // Que fue una corrección humana queda registrado, y la precisión del
    // satélite ya no describe este punto.
    expect(c['ubicacion_origen'], equals('manual'));
    expect(c['ubicacion_precision_m'], isNull);
  });

  testWidgets('el paso de 50 m mueve más', (tester) async {
    await abrirAlta(tester, gps: GpsObtenido(enLaCalle()));

    await tester.ensureVisible(find.text('50 m'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('50 m'));
    await tester.pumpAndSettle();
    await tocar(tester, const Key('mover_este'));

    expect(textoQueContiene('Movido 50 m'), findsOneWidget);
  });

  testWidgets('se puede volver al punto del GPS', (tester) async {
    await abrirAlta(tester, gps: GpsObtenido(enLaCalle()));

    await tocar(tester, const Key('mover_norte'));
    expect(textoQueContiene('Movido 10 m'), findsOneWidget);

    await tocar(tester, const Key('boton_deshacer_ajuste'));
    expect(textoQueContiene('Movido'), findsNothing);
    expect(textoQueContiene('Buena señal'), findsOneWidget);
  });

  testWidgets('sin coordenadas no hay nada que ajustar', (tester) async {
    await abrirAlta(tester, gps: const GpsSinLectura());
    expect(find.byKey(const Key('mover_norte')), findsNothing);
  });

  // -------------------------------------------------------------------------
  // Duplicados: atajarlos en la calle
  // -------------------------------------------------------------------------

  testWidgets('avisa de un cliente a la vuelta y pide confirmar', (tester) async {
    final base = await abrirAlta(
      tester,
      gps: GpsObtenido(enLaCalle()),
      sembrar: (b) => sembrarCliente(
        b,
        id: 'existente',
        nombre: 'Abarrotes Mary',
        lat: 19.4327,
        lng: -99.1332,
      ),
    );

    expect(find.byKey(const Key('aviso_cercanos')), findsOneWidget);
    expect(textoQueContiene('Abarrotes Mary'), findsWidgets);

    // El primer intento de guardar no crea nada: pide confirmación.
    await escribirEn(tester, const Key('campo_nombre'), 'Otra tienda');
    await tocar(tester, const Key('boton_guardar'));
    expect(base.contar('outbox'), equals(0));
    expect(find.text('Mi ruta'), findsNothing);

    // Confirmando que es distinta, sí guarda.
    await tocar(tester, const Key('boton_es_nueva'));
    await tocar(tester, const Key('boton_guardar'));
    expect(base.contar('outbox'), equals(1));
  });

  testWidgets('un cliente lejano no dispara el aviso', (tester) async {
    await abrirAlta(
      tester,
      gps: GpsObtenido(enLaCalle()),
      // ~330 m al norte: otra zona de la ruta.
      sembrar: (b) => sembrarCliente(
        b,
        id: 'lejano',
        nombre: 'De otra cuadra',
        lat: 19.4356,
        lng: -99.1332,
      ),
    );
    expect(find.byKey(const Key('aviso_cercanos')), findsNothing);
  });

  testWidgets('sin coordenadas no se opina de duplicados', (tester) async {
    // Comparar por nombre daría falsos positivos constantes: "Abarrotes María"
    // hay uno por cuadra.
    await abrirAlta(
      tester,
      gps: const GpsSinLectura(),
      sembrar: (b) => sembrarCliente(
        b,
        id: 'existente',
        nombre: 'Abarrotes Mary',
        lat: 19.4326,
        lng: -99.1332,
      ),
    );
    expect(find.byKey(const Key('aviso_cercanos')), findsNothing);
  });
}
