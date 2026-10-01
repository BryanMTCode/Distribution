/// El modelo del tablero: leer el contrato sin perder centavos.
///
/// ─────────────────────────────────────────────────────────────────────────
/// QUÉ DEFIENDEN ESTAS PRUEBAS
/// ─────────────────────────────────────────────────────────────────────────
/// 1. **El dinero no pasa por `double`.** Un `as double` en una tarjeta no
///    revienta: muestra $42,179.99 donde el servidor dijo $42,180.00, y nadie
///    lo nota hasta que esa cifra se compara con el arqueo de la liquidación.
///
/// 2. **`minutos = null` no es `minutos = 0`.** El primero significa que el
///    tablero nunca se calculó —el worker no está corriendo— y el segundo que
///    se acaba de calcular. Si el parseo los confundiera, la pantalla diría
///    "recién actualizado" cuando no hay cifras.
///
/// 3. **Una ruta sin objetivo no inventa un avance.** `objetivo = null` tiene
///    que sobrevivir el parseo: un cero daría 100% con la primera venta.
///
/// 4. **La fecha que se pide es el día LOCAL.** En el centro de México son seis
///    horas de diferencia con UTC: antes de las 6 de la mañana, pedir el día de
///    un `DateTime` en UTC pediría el tablero de AYER.
library;

import 'dart:convert';

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

/// Un tablero completo como lo manda el servidor. Los importes son strings.
Map<String, Object?> tableroDeEjemplo({
  Object? calculadoEn = '2026-10-01T18:00:00Z',
  Object? minutos = 3,
  bool confiable = true,
  int equiposSinSincronizar = 0,
  Object? advertencia,
  Object? objetivo = '100000.00',
  Object? logrado = '42.5',
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
        'fecha': '2026-10-01',
        'total': '42180.00',
        'contado': '31180.00',
        'credito': '11000.00',
        'documentos': 34,
        'ticket_promedio': '1240.59',
        'calculado_en': '2026-10-01T18:00:00Z',
      },
      'visitas': {
        'visitas': 58,
        'con_venta': 34,
        'no_drops': 24,
        'no_drops_nuestros': 9,
        'efectividad': '58.6',
        'drop_size': '1240.59',
        'calculado_en': '2026-10-01T18:00:00Z',
      },
      'cobranza': {
        'cobrado_hoy': '18450.75',
        'cobrado_efectivo': '15200.00',
        'saldo_total': '312890.40',
        'saldo_vencido': '48220.10',
        'facturas_vencidas': 27,
        'clientes_vencidos': 14,
        'calculado_en': '2026-10-01T17:58:00Z',
      },
      'mermas': {
        'documentos': 3,
        'unidades': '12.500',
        'calculado_en': '2026-10-01T18:00:00Z',
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
        'periodo': '2026-10-01',
        'dia_del_mes': 1,
        'dias_del_mes': 31,
        'rutas': [
          {
            'ruta_id': 'r1',
            'codigo': 'R04',
            'nombre': 'Ruta 4',
            'venta_mes': '42500.00',
            'objetivo': objetivo,
            'logrado': logrado,
            'esperado': '3.2',
            'diferencia': logrado == null ? null : '39.3',
            'semaforo': objetivo == null ? 'sin_objetivo' : 'adelante',
            'visitas_mes': 58,
            'dias_con_venta': 1,
            'clientes_distintos': 34,
          },
        ],
        'venta_sin_ruta': '777.00',
        'documentos_sin_ruta': 1,
      },
    };

void main() {
  group('Tablero.deJson', () {
    test('los importes llegan como Dinero exacto, no como double', () {
      final tablero = Tablero.deJson(tableroDeEjemplo());

      expect(tablero.venta.total, Dinero.deTexto('42180.00'));
      expect(tablero.venta.total.centavos, 4218000);
      // El reparto cuadra: si uno de los tres pasara por double, no sumaría.
      expect(
        tablero.venta.contado + tablero.venta.credito,
        tablero.venta.total,
      );
      expect(tablero.cobranza.saldoVencido.texto, '48220.10');
    });

    test('las cantidades llevan tres decimales, no dos', () {
      final tablero = Tablero.deJson(tableroDeEjemplo());
      expect(tablero.mermas.unidades.texto, '12.500');
    });

    test('un importe mal formado falla en vez de pintar un número raro', () {
      final crudo = tableroDeEjemplo();
      (crudo['venta']! as Map<String, Object?>)['total'] = '42180';
      expect(() => Tablero.deJson(crudo), throwsFormatException);
    });

    test('la frescura viaja con las cifras', () {
      final tablero = Tablero.deJson(tableroDeEjemplo());
      expect(tablero.frescura.minutos, 3);
      expect(tablero.frescura.confiable, isTrue);
      expect(tablero.frescura.nuncaCalculado, isFalse);
      expect(tablero.frescura.advertencia, isNull);
    });

    test('un tablero nunca calculado no se ve recién calculado', () {
      final tablero = Tablero.deJson(
        tableroDeEjemplo(
          calculadoEn: null,
          minutos: null,
          confiable: false,
          advertencia: 'El tablero no se ha calculado todavía.',
        ),
      );
      expect(tablero.frescura.calculadoEn, isNull);
      expect(tablero.frescura.minutos, isNull);
      expect(tablero.frescura.nuncaCalculado, isTrue);
      expect(tablero.frescura.advertencia, contains('no se ha calculado'));
    });

    test('un equipo sin sincronizar vuelve la cifra un piso', () {
      final tablero = Tablero.deJson(
        tableroDeEjemplo(
          confiable: false,
          equiposSinSincronizar: 2,
          advertencia: '2 equipo(s) sin sincronizar hoy, así que estas cifras '
              'son un piso y no un total.',
        ),
      );
      expect(tablero.frescura.equiposSinSincronizar, 2);
      expect(tablero.frescura.confiable, isFalse);
      expect(tablero.frescura.advertencia, contains('piso'));
    });

    test('las visitas perdidas se derivan, no se mandan dos veces', () {
      final tablero = Tablero.deJson(tableroDeEjemplo());
      expect(tablero.visitas.perdidas, 24);
    });

    test('el vendedor sin actividad se puede distinguir', () {
      // Es el renglón más urgente del tablero: a media mañana significa que
      // algo pasó con el camión o con el teléfono.
      final tablero = Tablero.deJson(tableroDeEjemplo());
      expect(tablero.vendedores[0].sinActividad, isFalse);
      expect(tablero.vendedores[1].sinActividad, isTrue);
    });

    test('una ruta sin objetivo no inventa un avance', () {
      final tablero = Tablero.deJson(
        tableroDeEjemplo(objetivo: null, logrado: null),
      );
      final ruta = tablero.avance.rutas.single;
      expect(ruta.sinObjetivo, isTrue);
      expect(ruta.objetivo, isNull);
      expect(ruta.logrado, isNull);
      expect(ruta.semaforo, 'sin_objetivo');
      // La venta del mes sí llega: la ruta aparece, solo sin barra.
      expect(ruta.ventaMes, Dinero.deTexto('42500.00'));
    });

    test('la venta sin ruta se conserva para poder mostrarla', () {
      // Es la diferencia entre el total del mes y la suma de las barras.
      final tablero = Tablero.deJson(tableroDeEjemplo());
      expect(tablero.avance.hayVentaSinRuta, isTrue);
      expect(tablero.avance.ventaSinRuta, Dinero.deTexto('777.00'));
    });
  });

  group('MapaDelDia.deJson', () {
    Map<String, Object?> mapaCrudo({bool recortados = false}) => {
          'fecha': '2026-10-01',
          'recortados': recortados,
          'calculado_en': '2026-10-01T18:00:00Z',
          'puntos': [
            {
              'clase': 'venta',
              'lat': '20.6736000',
              'lng': '-103.3440000',
              'cliente': 'Abarrotes Doña Mary',
              'vendedor': 'VEND01',
              'importe': '1240.50',
              'motivo': null,
              'momento': '2026-10-01T16:12:00Z',
            },
            {
              'clase': 'no_drop',
              'lat': '20.6800000',
              'lng': '-103.3500000',
              'cliente': 'La Esquina',
              'vendedor': 'VEND01',
              'importe': null,
              'motivo': 'No traigo lo que pidió',
              'momento': '2026-10-01T16:40:00Z',
            },
          ],
        };

    test('separa ventas de visitas perdidas', () {
      final mapa = MapaDelDia.deJson(mapaCrudo());
      expect(mapa.puntos, hasLength(2));
      expect(mapa.ventas, 1);
      expect(mapa.perdidas, 1);
      expect(mapa.puntos[0].esVenta, isTrue);
      expect(mapa.puntos[0].importe, Dinero.deTexto('1240.50'));
      expect(mapa.puntos[1].importe, isNull);
      expect(mapa.puntos[1].motivo, 'No traigo lo que pidió');
    });

    test('el recorte se propaga', () {
      // Un mapa recortado en silencio haría que alguien contara visitas sobre
      // el dibujo y le faltaran.
      expect(MapaDelDia.deJson(mapaCrudo(recortados: true)).recortados, isTrue);
    });
  });

  group('ClienteTablero', () {
    test('pide el día LOCAL, no el de un DateTime en UTC', () async {
      // A las 2 de la mañana del 2 de octubre en Guadalajara (UTC-6), un
      // `DateTime` en UTC marca el día 2 a las 08:00 — pero si se pidiera el
      // día del `DateTime` sin convertir a local, un momento UTC de 02:00 del
      // día 2 sería el día 1 en México. Se pide el día local.
      final transporte = _TransporteDeTablero(tableroDeEjemplo());
      final cliente = ClienteTablero(transporte);

      final local = DateTime(2026, 10, 2, 7, 30);
      await cliente.ver(fecha: local);

      expect(transporte.ultimosParametros['fecha'], '2026-10-02');
    });

    test('sin fecha no manda el parámetro: el servidor decide que es hoy', () async {
      final transporte = _TransporteDeTablero(tableroDeEjemplo());
      await ClienteTablero(transporte).ver();
      expect(transporte.ultimosParametros.containsKey('fecha'), isFalse);
    });

    test('un 403 es falta de permiso, no sesión vencida', () async {
      // Las dos llegan como 403 y significan cosas opuestas: una se arregla
      // volviendo a entrar y la otra no se arregla nunca. Mandar al gerente al
      // login a teclear su PIN cuando el problema es un permiso es tiempo
      // perdido garantizado.
      final cliente = ClienteTablero(
        _TransporteDeTablero(null, codigo: 403, cuerpo: 'falta el permiso tablero.ver'),
      );
      await expectLater(cliente.ver(), throwsA(isA<SinPermisoDeTablero>()));
    });

    test('un 401 sí es sesión vencida', () async {
      final cliente = ClienteTablero(
        _TransporteDeTablero(null, codigo: 401, cuerpo: 'token vencido'),
      );
      await expectLater(cliente.ver(), throwsA(isA<SesionInvalida>()));
    });

    test('un 500 se distingue de los dos anteriores', () async {
      final cliente = ClienteTablero(
        _TransporteDeTablero(null, codigo: 503, cuerpo: 'no disponible'),
      );
      await expectLater(cliente.ver(), throwsA(isA<ServidorConProblemas>()));
    });

    test('sin red no se traduce a un tablero vacío', () async {
      // Un tablero en ceros por falta de señal se lee como "no se vendió
      // nada", que es la lectura más dañina posible.
      final cliente = ClienteTablero(_TransporteCaido());
      await expectLater(cliente.ver(), throwsA(isA<ErrorDeRed>()));
    });
  });
}

class _TransporteDeTablero implements Transporte {
  _TransporteDeTablero(this.cuerpoJson, {this.codigo = 200, this.cuerpo});

  final Map<String, Object?>? cuerpoJson;
  final int codigo;
  final String? cuerpo;

  Map<String, String> ultimosParametros = const {};

  @override
  Future<RespuestaHttp> obtener(
    String ruta, {
    Map<String, String>? parametros,
  }) async {
    ultimosParametros = parametros ?? const {};
    return RespuestaHttp(
      codigo,
      cuerpo ?? jsonEncode(cuerpoJson),
    );
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async =>
      throw UnimplementedError('el tablero solo lee');
}

class _TransporteCaido implements Transporte {
  @override
  Future<RespuestaHttp> obtener(
    String ruta, {
    Map<String, String>? parametros,
  }) async =>
      throw const ErrorDeRed('sin conexión');

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async =>
      throw const ErrorDeRed('sin conexión');
}
