/// La ubicación del cliente: con GPS o escrita a mano (ADR 0002 §84).
///
/// En el perfil del cliente hay dos formas: un botón que toma el GPS —si se
/// está parado en el negocio— y los campos de latitud y longitud, para meterla o
/// corregirla a mano (copiada de un mapa, dictada por teléfono).
///
/// La regla de qué es una coordenada válida es la MISMA que la del servidor
/// (`app/infra/ubicacion_cliente.py`): si el teléfono aceptara algo que el
/// servidor rechaza, la operación se iría a cuarentena y el vendedor creería
/// que la guardó.
library;

import 'outbox.dart';
import 'sobre.dart';
import 'ubicacion.dart';

/// «23.2494, -106.4111» —como lo copia un mapa— separado en sus dos partes.
/// Nulo si no trae dos números.
(String, String)? separarCoordenadas(String pegado) {
  final m = RegExp(r'^\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*$')
      .firstMatch(pegado);
  return m == null ? null : (m.group(1)!, m.group(2)!);
}

/// Dos textos a una ubicación escrita a mano, o `UbicacionInvalida` con un
/// mensaje que se le puede leer a una persona.
Ubicacion leerCoordenadas(String lat, String lng, {OrigenUbicacion origen = OrigenUbicacion.manual}) {
  double numero(String texto, String nombre) {
    final crudo = texto.trim().replaceAll(' ', '');
    if (crudo.isEmpty) throw UbicacionInvalida('Falta la $nombre.');
    final v = double.tryParse(crudo);
    if (v == null || v.isNaN || v.isInfinite) {
      throw UbicacionInvalida('«$texto» no es una $nombre.');
    }
    return v;
  }

  final la = numero(lat, 'latitud');
  final lo = numero(lng, 'longitud');
  if (la < -90 || la > 90) {
    if (la >= -180 && la <= 180 && lo >= -90 && lo <= 90) {
      throw const UbicacionInvalida(
        'Parece que la latitud y la longitud están al revés: la latitud va entre '
        '−90 y 90 (en Mazatlán, 23.2…) y la longitud es la negativa (−106.4…).',
      );
    }
    throw UbicacionInvalida('La latitud va entre −90 y 90, no $la.');
  }
  if (lo < -180 || lo > 180) {
    throw UbicacionInvalida('La longitud va entre −180 y 180, no $lo.');
  }
  if (la == 0 && lo == 0) {
    throw const UbicacionInvalida(
      '0, 0 es el mar frente a África: es lo que deja un GPS que no leyó.',
    );
  }
  if (lo > 0 && lo >= 86 && lo <= 118 && la >= 14 && la <= 33) {
    throw UbicacionInvalida('La longitud en México es negativa: ¿quisiste decir −$lo?');
  }
  return Ubicacion(lat: la, lng: lo, origen: origen);
}

/// Fija la ubicación de un cliente desde el teléfono del vendedor, sin señal.
class RegistroDeUbicacion {
  RegistroDeUbicacion({
    required Outbox outbox,
    required String dispositivoId,
    required String Function() nuevoUuid,
    required DateTime Function() ahora,
  })  : _outbox = outbox,
        _dispositivoId = dispositivoId,
        _nuevoUuid = nuevoUuid,
        _ahora = ahora;

  final Outbox _outbox;
  final String _dispositivoId;
  final String Function() _nuevoUuid;
  final DateTime Function() _ahora;

  /// La guarda en el teléfono —la geocerca de la venta la usa ya— y la encola
  /// para el servidor (`cliente.ubicar`).
  void fijar(String clienteId, Ubicacion ubicacion) {
    final momento = _ahora().toUtc().toIso8601String();
    final sobre = SobreLocal(
      operacionId: _nuevoUuid(),
      secuencia: _outbox.siguienteSecuencia(),
      operaciones: [
        OperacionLocal(
          tipo: 'cliente.ubicar',
          entidadId: clienteId,
          datos: {
            'dispositivo_id': _dispositivoId,
            'fecha_dispositivo': momento,
            ...ubicacion.aPayload(),
          },
        ),
      ],
    );
    _outbox.encolar(
      sobre,
      creadoEn: momento,
      escribirNegocio: (db) => db.execute(
        'UPDATE clientes SET lat = ?, lng = ?, ubicacion_origen = ?, '
        '       ubicacion_precision_m = ? WHERE id = ?',
        [
          double.parse(ubicacion.latTexto),
          double.parse(ubicacion.lngTexto),
          ubicacion.origen.codigo,
          ubicacion.precisionMetros,
          clienteId,
        ],
      ),
    );
  }
}
