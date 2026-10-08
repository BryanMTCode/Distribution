/// La ubicación del cliente: con GPS o escrita a mano (ADR 0002 §84).
///
/// La regla de qué es una coordenada válida es la MISMA que la del servidor: si
/// el teléfono aceptara algo que él rechaza, la operación se iría a cuarentena y
/// el vendedor creería que la guardó.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

void main() {
  group('la regla', () {
    test('una coordenada buena', () {
      final u = leerCoordenadas(' 23.24941 ', '-106.41114');
      expect(u.latTexto, '23.2494100');
      expect(u.lngTexto, '-106.4111400');
      expect(u.origen, OrigenUbicacion.manual);
    });

    for (final (lat, lng, dice) in [
      ('0', '0', 'mar'),
      ('-106.41', '23.24', 'al revés'),
      ('23.24', '106.41', 'negativa'),
      ('91', '-106', 'entre −90 y 90'),
      ('23.2', '-181', 'entre −180 y 180'),
      ('veintitrés', '-106', 'no es una latitud'),
      ('', '-106', 'Falta la latitud'),
    ]) {
      test('«$lat, $lng» no se guarda: $dice', () {
        expect(
          () => leerCoordenadas(lat, lng),
          throwsA(isA<UbicacionInvalida>().having((e) => e.mensaje, 'mensaje', contains(dice))),
        );
      });
    }

    test('lo que copia un mapa se separa en sus dos partes', () {
      expect(separarCoordenadas('23.2494, -106.4111'), ('23.2494', '-106.4111'));
      expect(separarCoordenadas('23.2494,-106.4111'), ('23.2494', '-106.4111'));
      expect(separarCoordenadas('23.2494'), isNull);
    });
  });

  group('desde el teléfono del vendedor', () {
    late Database db;
    setUp(() {
      db = sqlite3.openInMemory();
      aplicarEsquemaLocal(db);
      db.execute("INSERT INTO clientes (id, nombre_comercial) VALUES ('c1', 'La Esquina')");
    });
    tearDown(() => db.dispose());

    test('se guarda al momento y viaja por la cola', () {
      var n = 0;
      RegistroDeUbicacion(
        outbox: Outbox(db),
        dispositivoId: 'd-poco',
        nuevoUuid: () => 'op-${++n}',
        ahora: () => DateTime.utc(2026, 10, 8, 1),
      ).fijar(
        'c1',
        Ubicacion(lat: 23.24941, lng: -106.41114, origen: OrigenUbicacion.gps,
            precisionMetros: 8.5),
      );
      final c = db.select('SELECT lat, lng, ubicacion_origen, ubicacion_precision_m '
          'FROM clientes').single;
      expect(c['lat'], 23.24941);
      expect(c['ubicacion_origen'], 'gps');
      expect(c['ubicacion_precision_m'], 8.5);

      final op = (Outbox(db).siguienteLote().single.payload['operaciones']! as List).single
          as Map<String, Object?>;
      expect(op['tipo'], 'cliente.ubicar');
      expect(op['entidad_id'], 'c1');
      expect(op['datos'], {
        'dispositivo_id': 'd-poco',
        'fecha_dispositivo': '2026-10-08T01:00:00.000Z',
        'lat': '23.2494100',
        'lng': '-106.4111400',
        'ubicacion_origen': 'gps',
        'ubicacion_precision_m': '8.50',
      });
    });
  });
}
