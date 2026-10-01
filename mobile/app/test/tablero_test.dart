/// El tablero de Gerencia en pantalla.
///
/// ─────────────────────────────────────────────────────────────────────────
/// QUÉ DEFIENDEN ESTAS PRUEBAS
/// ─────────────────────────────────────────────────────────────────────────
/// 1. **La antigüedad se ve, siempre, y antes de la primera cifra.** Es la
///    regla que gobierna la pantalla: en un DSD las cifras del día son un piso
///    (§0.3). Si la marca desapareciera por un refactor, el tablero seguiría
///    viéndose perfecto y empezaría a mentir.
///
/// 2. **Sin señal se muestran las cifras viejas DICIENDO que son viejas.** Las
///    dos mitades importan: una pantalla vacía no sirve, y unas cifras viejas
///    sin etiqueta son peor que no tenerlas.
///
/// 3. **"Sin permiso" no ofrece reintentar.** No se arregla reintentando, y el
///    botón sería una invitación a perder el tiempo.
///
/// 4. **Una ruta sin objetivo aparece, sin barra.** La que no tiene meta es
///    justo la que hay que notar; con un filtro desaparecería.
///
/// 5. **El vendedor sin movimiento se marca.** A media mañana es el renglón más
///    urgente, y un orden por venta lo pondría al final.
library;

import 'dart:convert';

import 'package:dsd_app/src/estado/sesion.dart';
import 'package:dsd_app/src/estado/sincronizacion.dart';
import 'package:dsd_app/src/pantallas/gerencia/comunes.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

/// Un tablero como lo manda el servidor. Los importes son strings.
Map<String, Object?> tableroDelServidor({
  String total = '42180.00',
  String? calculadoEn = '2026-09-24T06:57:00.000Z',
  int? minutos = 3,
  bool confiable = true,
  int equiposSinSincronizar = 0,
  String? advertencia,
  String? objetivo = '100000.00',
  String? logrado = '42.5',
  bool vendedorSinMovimiento = true,
}) =>
    {
      'frescura': {
        'calculado_en': calculadoEn,
        'minutos': minutos,
        'confiable': confiable,
        'equipos_sin_sincronizar': equiposSinSincronizar,
        'cola_reportada': 0,
        'ops_en_cuarentena': 0,
        'advertencia': advertencia,
      },
      'venta': {
        'fecha': '2026-09-24',
        'total': total,
        'contado': '31180.00',
        'credito': '11000.00',
        'documentos': 34,
        'ticket_promedio': '1240.59',
        'calculado_en': calculadoEn,
      },
      'visitas': {
        'visitas': 58,
        'con_venta': 34,
        'no_drops': 24,
        'no_drops_nuestros': 9,
        'efectividad': '58.6',
        'drop_size': '1240.59',
        'calculado_en': calculadoEn,
      },
      'cobranza': {
        'cobrado_hoy': '18450.75',
        'cobrado_efectivo': '15200.00',
        'saldo_total': '312890.40',
        'saldo_vencido': '48220.10',
        'facturas_vencidas': 27,
        'clientes_vencidos': 14,
        'calculado_en': calculadoEn,
      },
      'mermas': {
        'documentos': 2,
        'unidades': '12.500',
        'calculado_en': calculadoEn,
      },
      'vendedores': [
        {
          'vendedor_id': 'aaaa',
          'codigo': 'VEND01',
          'nombre': 'Juan Pérez',
          'venta': '22180.00',
          'documentos': 18,
          'visitas': 30,
          'con_venta': 18,
          'no_drops': 12,
          'cobrado': '9000.00',
          'efectividad': '60.0',
        },
        if (vendedorSinMovimiento)
          {
            'vendedor_id': 'bbbb',
            'codigo': 'VEND02',
            'nombre': 'Luis Soto',
            'venta': '0.00',
            'documentos': 0,
            'visitas': 0,
            'con_venta': 0,
            'no_drops': 0,
            'cobrado': '0.00',
            'efectividad': '0.0',
          },
      ],
      'avance': {
        'periodo': '2026-09-01',
        'dia_del_mes': 24,
        'dias_del_mes': 30,
        'venta_sin_ruta': '0.00',
        'documentos_sin_ruta': 0,
        'rutas': [
          {
            'ruta_id': 'r1',
            'codigo': 'R04',
            'nombre': 'Ruta 4',
            'venta_mes': '42500.00',
            'objetivo': objetivo,
            'logrado': logrado,
            'esperado': '80.0',
            'diferencia': logrado == null ? null : '-37.5',
            'semaforo': objetivo == null ? 'sin_objetivo' : 'atras',
            'visitas_mes': 58,
            'dias_con_venta': 18,
            'clientes_distintos': 34,
          },
        ],
      },
    };

Map<String, Object?> mapaDelServidor({bool recortados = false}) => {
      'fecha': '2026-09-24',
      'recortados': recortados,
      'calculado_en': '2026-09-24T06:57:00.000Z',
      'puntos': [
        {
          'clase': 'venta',
          'lat': '20.6736000',
          'lng': '-103.3440000',
          'cliente': 'Abarrotes Doña Mary',
          'vendedor': 'VEND01',
          'importe': '1240.50',
          'motivo': null,
          'momento': '2026-09-24T16:12:00.000Z',
        },
        {
          'clase': 'no_drop',
          'lat': '20.6800000',
          'lng': '-103.3500000',
          'cliente': 'La Esquina',
          'vendedor': 'VEND01',
          'importe': null,
          'motivo': 'No traigo lo que pidió',
          'momento': '2026-09-24T16:40:00.000Z',
        },
      ],
    };

/// Transporte que contesta el tablero y el mapa, o se cae a voluntad.
class TransporteDeGerencia implements Transporte {
  TransporteDeGerencia({
    this.tablero,
    this.mapa,
    this.codigo = 200,
    this.cuerpoDeError,
    this.sinRed = false,
  });

  Map<String, Object?>? tablero;
  Map<String, Object?>? mapa;
  int codigo;
  String? cuerpoDeError;
  bool sinRed;

  final List<String> rutasPedidas = [];

  @override
  Future<RespuestaHttp> obtener(
    String ruta, {
    Map<String, String>? parametros,
  }) async {
    rutasPedidas.add(ruta);
    if (sinRed) throw const ErrorDeRed('sin conexión');
    if (codigo != 200) {
      return RespuestaHttp(codigo, cuerpoDeError ?? '{"detail":"error"}');
    }
    final cuerpo = ruta.contains('mapa') ? mapa : tablero;
    return RespuestaHttp(200, jsonEncode(cuerpo));
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async =>
      RespuestaHttp(404, '{}');
}

/// Monta la app con sesión de gerencia ya abierta y el transporte dado.
///
/// Se entra por el estado y no por la pantalla de login a propósito: lo que
/// estas pruebas ejercen es el tablero, y recorrer el login en cada una
/// añadiría medio segundo de Argon2 por prueba sin probar nada nuevo. El camino
/// del login tiene sus propias pruebas.
Future<void> montarTablero(
  WidgetTester tester,
  TransporteDeGerencia transporte, {
  DateTime? ahora,
}) async {
  await montarApp(
    tester,
    ahora: ahora ?? DateTime.utc(2026, 9, 24, 7),
    extras: [
      tokenProvider.overrideWith((_) => 'token-de-prueba'),
      transporteProvider.overrideWithValue(transporte),
      sesionProvider.overrideWith(() => _SesionDeGerenciaFija()),
    ],
  );
  await tester.pumpAndSettle();
}

class _SesionDeGerenciaFija extends ControladorSesion {
  @override
  Sesion build() => const SesionDeGerencia(
        Perfil(
          usuarioId: 'g1',
          codigo: 'GER01',
          nombre: 'Bryan',
          rol: 'gerente',
          permisos: ['tablero.ver'],
        ),
      );
}

void main() {
  group('las cifras', () {
    testWidgets('la venta del día se ve con su desglose', (tester) async {
      await montarTablero(
        tester,
        TransporteDeGerencia(tablero: tableroDelServidor()),
      );

      expect(find.byKey(const Key('tablero_cifras')), findsOneWidget);
      expect(find.text(r'$42,180'), findsOneWidget);
      expect(find.text('vendido hoy'), findsOneWidget);
      expect(
        find.textContaining('34 remisiones'),
        findsOneWidget,
      );
    });

    testWidgets('el efectivo se separa porque es lo que entra al arqueo',
        (tester) async {
      await montarTablero(
        tester,
        TransporteDeGerencia(tablero: tableroDelServidor()),
      );
      expect(find.textContaining(r'$15,200.00 en efectivo'), findsOneWidget);
      expect(find.textContaining('arqueo'), findsOneWidget);
    });

    testWidgets('los no-drops nuestros se marcan como lo accionable',
        (tester) async {
      await montarTablero(
        tester,
        TransporteDeGerencia(tablero: tableroDelServidor()),
      );
      expect(find.text('visitas perdidas que son nuestras'), findsOneWidget);
      expect(find.text('9'), findsOneWidget);
      expect(find.textContaining('De 24 no-drops'), findsOneWidget);
    });

    testWidgets('el vencido de cartera se marca', (tester) async {
      await montarTablero(
        tester,
        TransporteDeGerencia(tablero: tableroDelServidor()),
      );
      await tester.scrollUntilVisible(find.text('vencido'), 200);
      expect(find.text(r'$48,220'), findsOneWidget);
      expect(
        find.textContaining('27 documentos de 14 clientes'),
        findsOneWidget,
      );
    });
  });

  group('la antigüedad', () {
    testWidgets('se ve antes de la primera cifra', (tester) async {
      await montarTablero(
        tester,
        TransporteDeGerencia(tablero: tableroDelServidor()),
      );

      final marca = tester.getRect(find.byKey(const Key('marca_de_frescura')));
      final primeraCifra = tester.getRect(find.text('vendido hoy'));
      expect(
        marca.top,
        lessThan(primeraCifra.top),
        reason: 'la marca de frescura tiene que estar arriba de las cifras: '
            'debajo se leería como nota al pie de algo que ya se dio por cierto',
      );
    });

    testWidgets('dice cuándo lo calculó el servidor y cuándo lo bajó el teléfono',
        (tester) async {
      // El servidor calculó a las 06:57 y el teléfono lo bajó a las 07:00: la
      // cifra arrastra los minutos del cálculo MÁS los del camino, y las dos
      // cosas se muestran porque son distintas.
      await montarTablero(
        tester,
        TransporteDeGerencia(tablero: tableroDelServidor()),
      );
      expect(find.textContaining('Calculado en el servidor'), findsOneWidget);
      expect(find.textContaining('bajado'), findsOneWidget);
    });

    testWidgets('un equipo sin sincronizar sale como advertencia',
        (tester) async {
      await montarTablero(
        tester,
        TransporteDeGerencia(
          tablero: tableroDelServidor(
            confiable: false,
            equiposSinSincronizar: 2,
            advertencia: '2 equipo(s) sin sincronizar hoy, así que estas '
                'cifras son un piso y no un total.',
          ),
        ),
      );
      expect(find.textContaining('piso y no un total'), findsOneWidget);
    });

    testWidgets('un tablero que el servidor nunca calculó lo dice',
        (tester) async {
      await montarTablero(
        tester,
        TransporteDeGerencia(
          tablero: tableroDelServidor(
            calculadoEn: null,
            minutos: null,
            confiable: false,
            advertencia: 'El tablero no se ha calculado todavía.',
          ),
        ),
      );
      expect(
        find.textContaining('no ha calculado el tablero todavía'),
        findsOneWidget,
      );
    });
  });

  group('sin señal', () {
    testWidgets('con copia guardada muestra las cifras DICIENDO que son viejas',
        (tester) async {
      final transporte = TransporteDeGerencia(tablero: tableroDelServidor());
      await montarTablero(tester, transporte);
      expect(find.text(r'$42,180'), findsOneWidget);

      // Se cae la red y se vuelve a pedir.
      transporte.sinRed = true;
      await tester.tap(find.byKey(const Key('boton_refrescar_tablero')));
      await tester.pumpAndSettle();

      // Las cifras siguen: una pantalla vacía no sirve para nada.
      expect(find.text(r'$42,180'), findsOneWidget);
      // Y están etiquetadas: unas cifras viejas sin etiqueta son peor que
      // no tenerlas.
      expect(find.textContaining('Sin conexión'), findsOneWidget);
      expect(find.textContaining('última copia'), findsOneWidget);
    });

    testWidgets('la copia conserva TODAS las cifras, no solo la primera',
        (tester) async {
      // La copia se guarda reserializando el objeto a la forma del contrato, y
      // ahí caben los errores que no se ven: cambiar contado por crédito,
      // perder un decimal, dejar un campo fuera. Se afirman cifras de los
      // cuatro bloques y de los dos renglones, una por una.
      final transporte = TransporteDeGerencia(tablero: tableroDelServidor());
      await montarTablero(tester, transporte);
      transporte.sinRed = true;
      await tester.tap(find.byKey(const Key('boton_refrescar_tablero')));
      await tester.pumpAndSettle();

      expect(find.textContaining(r'$11,000.00 del día salió a crédito'),
          findsOneWidget);
      expect(find.textContaining(r'($31,180.00 de contado)'), findsOneWidget);
      expect(find.text('58.6%'), findsOneWidget);
      expect(find.textContaining('34 de 58 visitas'), findsOneWidget);
      expect(find.textContaining(r'$15,200.00 en efectivo'), findsOneWidget);

      await tester.scrollUntilVisible(find.text('vencido'), 200);
      expect(find.text(r'$48,220'), findsOneWidget);
      expect(find.text(r'$312,890'), findsOneWidget);

      await tester.scrollUntilVisible(find.byKey(const Key('ruta_R04')), 200);
      expect(find.textContaining('42.5% de'), findsOneWidget);
      expect(find.textContaining('esperado 80.0%'), findsOneWidget);

      await tester.scrollUntilVisible(
        find.byKey(const Key('vendedor_VEND01')),
        200,
      );
      expect(find.text(r'$22,180'), findsOneWidget);
      expect(find.textContaining('12.500 unidades'), findsOneWidget);
    });

    testWidgets('sin copia dice que nunca se ha podido bajar', (tester) async {
      await montarTablero(
        tester,
        TransporteDeGerencia(sinRed: true),
      );
      expect(find.byKey(const Key('tablero_sin_nada')), findsOneWidget);
      expect(find.byKey(const Key('boton_reintentar_tablero')), findsOneWidget);
    });
  });

  group('lo que el servidor contesta', () {
    testWidgets('sin permiso no se ofrece reintentar', (tester) async {
      // No se arregla reintentando: hay que pedir el permiso en la oficina.
      await montarTablero(
        tester,
        TransporteDeGerencia(
          codigo: 403,
          cuerpoDeError: '{"detail":"falta el permiso tablero.ver"}',
        ),
      );
      expect(find.byKey(const Key('tablero_sin_permiso')), findsOneWidget);
      expect(find.byKey(const Key('boton_reintentar_tablero')), findsNothing);
      expect(find.textContaining('tablero.ver'), findsOneWidget);
    });

    testWidgets('la sesión vencida manda a volver a entrar', (tester) async {
      await montarTablero(
        tester,
        TransporteDeGerencia(codigo: 401, cuerpoDeError: '{"detail":"vencido"}'),
      );
      expect(find.byKey(const Key('tablero_sesion_vencida')), findsOneWidget);
      expect(find.byKey(const Key('boton_volver_a_entrar')), findsOneWidget);
    });

    testWidgets('un error del servidor sí se puede reintentar', (tester) async {
      await montarTablero(
        tester,
        TransporteDeGerencia(codigo: 503, cuerpoDeError: 'no disponible'),
      );
      expect(find.byKey(const Key('tablero_con_error')), findsOneWidget);
      expect(find.byKey(const Key('boton_reintentar_tablero')), findsOneWidget);
    });
  });

  group('el avance del mes', () {
    testWidgets('muestra el logrado junto a lo esperado', (tester) async {
      await montarTablero(
        tester,
        TransporteDeGerencia(tablero: tableroDelServidor()),
      );
      await tester.scrollUntilVisible(find.byKey(const Key('ruta_R04')), 200);
      // 42.5% el día 24 de 30 no es lo mismo que el día 5, y el número es el
      // mismo: sin el esperado al lado la cifra se lee mal.
      expect(find.textContaining('42.5% de'), findsOneWidget);
      expect(find.textContaining('esperado 80.0%'), findsOneWidget);
    });

    testWidgets('una ruta sin objetivo aparece y dice que le falta meta',
        (tester) async {
      // Con un filtro desaparecería justo la ruta que hay que notar.
      await montarTablero(
        tester,
        TransporteDeGerencia(
          tablero: tableroDelServidor(objetivo: null, logrado: null),
        ),
      );
      await tester.scrollUntilVisible(find.byKey(const Key('ruta_R04')), 200);
      expect(find.textContaining('Sin objetivo este mes'), findsOneWidget);
      expect(find.text(r'$42,500'), findsOneWidget);
    });

    testWidgets('dice que el prorrateo es por días naturales', (tester) async {
      // Porque en domingo todas las rutas se van a ver atrás, y sin la nota eso
      // parece un problema de la operación.
      await montarTablero(
        tester,
        TransporteDeGerencia(tablero: tableroDelServidor()),
      );
      await tester.scrollUntilVisible(
        find.textContaining('días naturales'),
        200,
      );
      expect(find.textContaining('días naturales'), findsOneWidget);
    });
  });

  group('por vendedor', () {
    testWidgets('el que no ha hecho nada se marca', (tester) async {
      await montarTablero(
        tester,
        TransporteDeGerencia(tablero: tableroDelServidor()),
      );
      await tester.scrollUntilVisible(
        find.byKey(const Key('vendedor_VEND02')),
        200,
      );
      expect(find.text('Sin movimiento todavía hoy'), findsOneWidget);
    });

    testWidgets('el que sí trabajó muestra su efectividad', (tester) async {
      await montarTablero(
        tester,
        TransporteDeGerencia(tablero: tableroDelServidor()),
      );
      await tester.scrollUntilVisible(
        find.byKey(const Key('vendedor_VEND01')),
        200,
      );
      expect(
        find.textContaining('30 visitas · 18 con venta · 60%'),
        findsOneWidget,
      );
    });
  });

  group('el mapa', () {
    testWidgets('se abre desde el tablero y pinta los dos tipos de punto',
        (tester) async {
      await montarTablero(
        tester,
        TransporteDeGerencia(
          tablero: tableroDelServidor(),
          mapa: mapaDelServidor(),
        ),
      );
      await tester.scrollUntilVisible(find.byKey(const Key('boton_mapa')), 200);
      await tester.tap(find.byKey(const Key('boton_mapa')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('mapa_del_dia')), findsOneWidget);
      expect(
        find.textContaining('1 ventas y 1 visitas perdidas'),
        findsOneWidget,
      );
      expect(find.text('venta'), findsOneWidget);
      expect(find.text('visita perdida'), findsOneWidget);
    });

    testWidgets('un recorte se avisa en vez de callarse', (tester) async {
      // Un mapa recortado en silencio haría que alguien contara visitas sobre
      // el dibujo y le faltaran.
      await montarTablero(
        tester,
        TransporteDeGerencia(
          tablero: tableroDelServidor(),
          mapa: mapaDelServidor(recortados: true),
        ),
      );
      await tester.scrollUntilVisible(find.byKey(const Key('boton_mapa')), 200);
      await tester.tap(find.byKey(const Key('boton_mapa')));
      await tester.pumpAndSettle();
      expect(find.textContaining('dejó puntos fuera'), findsOneWidget);
    });

    testWidgets('un día sin visitas con GPS lo explica', (tester) async {
      await montarTablero(
        tester,
        TransporteDeGerencia(
          tablero: tableroDelServidor(),
          mapa: {
            'fecha': '2026-09-24',
            'recortados': false,
            'calculado_en': null,
            'puntos': const <Object?>[],
          },
        ),
      );
      await tester.scrollUntilVisible(find.byKey(const Key('boton_mapa')), 200);
      await tester.tap(find.byKey(const Key('boton_mapa')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('mapa_vacio')), findsOneWidget);
      // Y dice por qué una venta puede no estar: dibujarla en algún lado sería
      // inventarle una ubicación.
      expect(find.textContaining('sin GPS no aparecen'), findsOneWidget);
    });
  });

  group('antigüedadEnPalabras', () {
    final base = DateTime.utc(2026, 9, 24, 12);

    test('dice los minutos, no la hora exacta', () {
      // A las 12:00, "11:24" no se lee como "hace 36 minutos": se lee como
      // "reciente", y obliga a restar mentalmente.
      expect(
        antiguedadEnPalabras(base.subtract(const Duration(minutes: 36)),
            ahora: base),
        'hace 36 min',
      );
      expect(
        antiguedadEnPalabras(base.subtract(const Duration(minutes: 1)),
            ahora: base),
        'hace 1 min',
      );
      expect(
        antiguedadEnPalabras(base.subtract(const Duration(hours: 3)),
            ahora: base),
        'hace 3 h',
      );
      expect(
        antiguedadEnPalabras(base.subtract(const Duration(days: 1)),
            ahora: base),
        'ayer',
      );
    });

    test('un momento nulo es "nunca", no "hace 0 minutos"', () {
      expect(antiguedadEnPalabras(null, ahora: base), 'nunca');
    });

    test('un reloj del teléfono atrasado no imprime minutos negativos', () {
      // Pasa de verdad: el reloj del equipo va atrás del servidor. "hace -3
      // minutos" es peor que admitir que no se sabe.
      expect(
        antiguedadEnPalabras(base.add(const Duration(minutes: 3)), ahora: base),
        'hace un momento',
      );
    });
  });

  group('pesos', () {
    test('formatea desde los centavos enteros, sin pasar por double', () {
      expect(pesos(Dinero.deTexto('42180.00')), r'$42,180.00');
      expect(pesos(Dinero.deTexto('42180.00'), conCentavos: false), r'$42,180');
      expect(pesos(Dinero.deTexto('0.05')), r'$0.05');
      expect(pesos(Dinero.deTexto('1234567.89')), r'$1,234,567.89');
      expect(pesos(Dinero.deTexto('-250.50')), r'-$250.50');
    });
  });
}
