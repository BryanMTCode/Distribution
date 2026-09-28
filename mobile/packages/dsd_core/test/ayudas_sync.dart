/// Servidor falso para las pruebas de sincronización.
///
/// "Que se caiga la red a media tanda" no es algo que se pida a voluntad a un
/// servidor real. Aquí sí.
library;

import 'dart:convert';

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';

const ahoraFijo = '2026-09-28T10:00:00.000Z';

/// Qué debe pasar en la siguiente llamada.
sealed class Guion {
  const Guion();
}

class Responde extends Guion {
  const Responde(this.codigo, this.cuerpo);

  factory Responde.push({
    required List<SobreEnCola> sobres,
    String estado = 'aceptada',
    String? errorCodigo,
  }) {
    final resultados = sobres
        .map((s) => {
              'operacion_id': s.operacionId,
              'estado': estado,
              if (errorCodigo != null) 'error_codigo': errorCodigo,
              if (errorCodigo != null) 'error_mensaje': 'motivo de prueba',
            })
        .toList();
    return Responde(
      200,
      jsonEncode({
        'lote_id': 'lote-1',
        'aceptadas': estado == 'aceptada' ? sobres.length : 0,
        'duplicadas': estado == 'duplicada' ? sobres.length : 0,
        'rechazadas': estado == 'rechazada' ? sobres.length : 0,
        'resultados': resultados,
      }),
    );
  }

  factory Responde.pull({
    required List<Map<String, Object?>> cambios,
    bool hayMas = false,
    int? cursor,
  }) =>
      Responde(
        200,
        jsonEncode({
          'cursor': cursor ?? (cambios.isEmpty ? 0 : cambios.last['cursor']),
          'hay_mas': hayMas,
          'cambios': cambios,
        }),
      );

  final int codigo;
  final String cuerpo;
}

/// Responde al push reflejando los sobres que llegaron, como haría el
/// servidor real. Evita andamiajes frágiles que predicen el corte de tandas.
class AceptaLoEnviado extends Guion {
  const AceptaLoEnviado({this.estado = 'aceptada', this.errorCodigo});

  final String estado;
  final String? errorCodigo;
}

class SeCaeLaRed extends Guion {
  const SeCaeLaRed([this.mensaje = 'sin señal']);

  final String mensaje;
}

/// Transporte programable. Registra cada llamada para poder verificar el orden.
class TransporteFalso implements Transporte {
  TransporteFalso(this.guiones, {this.repiteUltimo = false});

  /// Respuestas en orden.
  final List<Guion> guiones;

  final List<String> llamadas = [];
  final List<Map<String, Object?>> cuerposEnviados = [];

  int _i = 0;

  /// Cuando se agotan los guiones se responde un pull vacío: así una prueba
  /// que solo le interesa el push no tiene que guionar el pull.
  ///
  /// `repiteUltimo` deja que un guion sirva para todas las llamadas siguientes,
  /// que es lo que se quiere al probar el corte en tandas.
  final bool repiteUltimo;

  Guion _siguiente() {
    if (_i >= guiones.length) {
      if (repiteUltimo && guiones.isNotEmpty) return guiones.last;
      return Responde.pull(cambios: const [], cursor: -1);
    }
    return guiones[_i++];
  }

  /// Arma la respuesta del push a partir de lo que realmente se envió.
  RespuestaHttp _reflejarPush(AceptaLoEnviado guion) {
    final sobres = (cuerposEnviados.last['sobres'] as List)
        .cast<Map<String, Object?>>();
    final resultados = sobres
        .map((s) => {
              'operacion_id': s['operacion_id'],
              'estado': guion.estado,
              if (guion.errorCodigo != null) 'error_codigo': guion.errorCodigo,
              if (guion.errorCodigo != null) 'error_mensaje': 'motivo de prueba',
            })
        .toList();
    return RespuestaHttp(
      200,
      jsonEncode({
        'lote_id': cuerposEnviados.last['lote_id'],
        'aceptadas': guion.estado == 'aceptada' ? sobres.length : 0,
        'duplicadas': guion.estado == 'duplicada' ? sobres.length : 0,
        'rechazadas': guion.estado == 'rechazada' ? sobres.length : 0,
        'resultados': resultados,
      }),
    );
  }

  Future<RespuestaHttp> _responder(String ruta) async {
    llamadas.add(ruta);
    final esPull = ruta.contains('pull');
    final guion = _siguiente();

    // Un guion de push no puede contestar un pull: el transporte conoce la
    // ruta, así que responde un delta vacío en vez de devolver la forma
    // equivocada. Es también lo que haría un servidor real.
    if (esPull && guion is AceptaLoEnviado) {
      return RespuestaHttp(200, jsonEncode({'cursor': 0, 'hay_mas': false, 'cambios': []}));
    }

    return switch (guion) {
      SeCaeLaRed(:final mensaje) => throw ErrorDeRed(mensaje),
      final AceptaLoEnviado g => _reflejarPush(g),
      Responde(:final codigo, :final cuerpo) => RespuestaHttp(codigo, cuerpo),
    };
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) {
    cuerposEnviados.add(cuerpo);
    return _responder(ruta);
  }

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) =>
      _responder('$ruta?${(parametros ?? {}).entries.map((e) => '${e.key}=${e.value}').join('&')}');
}

/// Base local lista, con el esquema aplicado.
Database baseLocal() {
  final db = sqlite3.openInMemory();
  db.execute(esquemaLocal);
  return db;
}

SobreLocal sobreDeCliente(String id, {int secuencia = 0}) => SobreLocal(
      operacionId: 'op-$id',
      secuencia: secuencia,
      visitaId: 'visita-$id',
      operaciones: [
        OperacionLocal(
          tipo: 'cliente.crear',
          entidadId: id,
          datos: {'nombre_comercial': 'Tienda $id'},
        ),
      ],
    );

/// Encola un alta de cliente como lo haría la pantalla.
void encolarAlta(Outbox outbox, Database db, String id, {int secuencia = 0}) {
  outbox.encolar(
    sobreDeCliente(id, secuencia: secuencia),
    escribirNegocio: (d) => d.execute(
      'INSERT INTO clientes (id, nombre_comercial, es_local, sincronizado) '
      'VALUES (?, ?, 1, 0)',
      [id, 'Tienda $id'],
    ),
    creadoEn: ahoraFijo,
  );
}

Map<String, Object?> deltaProducto(int cursor, String id, String sku) => {
      'cursor': cursor,
      'entidad': 'producto',
      'entidad_id': id,
      'operacion': 'upsert',
      'payload': {
        'id': id,
        'sku': sku,
        'nombre': 'Producto $sku',
        'unidad_base': 'PZA',
        'tasa_iva': '0.0000',
        'activo': true,
      },
    };

Map<String, Object?> deltaCliente(int cursor, String id, {String? nombre}) => {
      'cursor': cursor,
      'entidad': 'cliente',
      'entidad_id': id,
      'operacion': 'upsert',
      'payload': {
        'id': id,
        'codigo': 'CLI-$id',
        'nombre_comercial': nombre ?? 'Tienda $id',
        'permite_credito': true,
        'limite_credito': '5000.00',
        'bloqueado': false,
      },
    };

Map<String, Object?> deltaCartera(
  int cursor,
  String id, {
  String saldo = '1200.00',
  String limite = '5000.00',
}) =>
    {
      'cursor': cursor,
      'entidad': 'cartera',
      'entidad_id': id,
      'operacion': 'upsert',
      'payload': {
        'cliente_id': id,
        'saldo': saldo,
        'limite_credito': limite,
        'permite_credito': true,
        'bloqueado': false,
      },
    };

Sincronizador armarSincronizador(
  Database db,
  TransporteFalso transporte, {
  int maxSobresPorLote = 50,
}) =>
    Sincronizador(
      outbox: Outbox(db),
      cliente: ClienteSync(transporte),
      aplicador: AplicadorDeltas(db),
      ahora: () => ahoraFijo,
      maxSobresPorLote: maxSobresPorLote,
    );
