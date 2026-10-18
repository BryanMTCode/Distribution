/// El mapa de Mazatlán guardado en el teléfono (ADR 0002 §130).
///
/// El servidor lo baja una vez de OpenStreetMap (`python -m app.cli
/// mapa-mazatlan`) y el teléfono se lo pide con su credencial, con WiFi. Desde
/// ese momento el mapa se dibuja con lo que hay en el teléfono: sin señal no
/// se queda en blanco, porque no le pide nada a nadie.
///
/// Se baja a un archivo `.parcial` y solo se cambia por el bueno cuando llegó
/// completo y su SHA-256 es el que dijo el servidor. Una descarga cortada en el
/// camino —lo normal con la señal de la ruta— deja el mapa que había, entero.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:http/http.dart' as http;
import 'package:path/path.dart' as p;

/// Lo que el servidor dice del mapa que tiene.
class MapaDelServidor {
  const MapaDelServidor({
    required this.disponible,
    this.version,
    this.bytes,
    this.sha256,
    this.atribucion,
  });

  factory MapaDelServidor.desdeJson(Map<String, Object?> j) => MapaDelServidor(
        disponible: j['disponible'] == true,
        version: j['version'] as String?,
        bytes: (j['bytes'] as num?)?.toInt(),
        sha256: j['sha256'] as String?,
        atribucion: j['atribucion'] as String?,
      );

  final bool disponible;
  final String? version;
  final int? bytes;
  final String? sha256;
  final String? atribucion;

  /// «23.4 MB», para decírselo antes de bajarlo.
  String get tamanoTexto => bytes == null ? '' : megas(bytes!);
}

String megas(int bytes) => '${(bytes / 1000000).toStringAsFixed(1)} MB';

/// El archivo, como va llegando.
class ArchivoEnCamino {
  const ArchivoEnCamino(this.codigo, this.pedazos);

  final int codigo;
  final Stream<List<int>> pedazos;
}

/// Sin credencial no se puede pedir: el vendedor entró sin señal y todavía no
/// ha sincronizado.
class SinSesionParaElMapa implements Exception {
  const SinSesionParaElMapa();
}

/// El servidor respondió otra cosa.
class ErrorDelMapa implements Exception {
  const ErrorDelMapa(this.mensaje);

  final String mensaje;

  @override
  String toString() => mensaje;
}

abstract interface class FuenteDelMapa {
  Future<MapaDelServidor> info();

  Future<ArchivoEnCamino> bajar();
}

/// La de verdad: `GET /v1/mapa` con el transporte de siempre, y el archivo por
/// pedazos (no cabe en la memoria de un teléfono barato como un solo cuerpo).
class FuenteDelMapaHttp implements FuenteDelMapa {
  FuenteDelMapaHttp({
    required this.baseUrl,
    required this.transporte,
    required this.leerToken,
    required this.renovar,
    http.Client? cliente,
  }) : _cliente = cliente ?? http.Client();

  final String baseUrl;
  final Transporte? Function() transporte;
  final String? Function() leerToken;
  final Future<bool> Function() renovar;
  final http.Client _cliente;

  @override
  Future<MapaDelServidor> info() async {
    var t = transporte();
    if (t == null && await renovar()) t = transporte();
    if (t == null) throw const SinSesionParaElMapa();
    final r = await t.obtener('/v1/mapa');
    if (!r.ok) throw ErrorDelMapa('El servidor respondió ${r.codigo}.');
    return MapaDelServidor.desdeJson(jsonDecode(r.cuerpo) as Map<String, Object?>);
  }

  @override
  Future<ArchivoEnCamino> bajar() async {
    try {
      var r = await _pedir();
      if (r.statusCode == 401 && await renovar()) {
        await r.stream.drain<void>();
        r = await _pedir();
      }
      if (r.statusCode == 401) throw const SinSesionParaElMapa();
      return ArchivoEnCamino(r.statusCode, r.stream);
    } on SocketException catch (e) {
      throw ErrorDeRed('sin conexión: ${e.message}');
    } on http.ClientException catch (e) {
      throw ErrorDeRed('falló la petición: ${e.message}');
    } on TimeoutException {
      throw const ErrorDeRed('el servidor no respondió a tiempo');
    }
  }

  Future<http.StreamedResponse> _pedir() {
    final token = leerToken() ?? '';
    final peticion = http.Request('GET', Uri.parse('$baseUrl/v1/mapa/archivo'));
    if (token.isNotEmpty) peticion.headers['Authorization'] = 'Bearer $token';
    return _cliente.send(peticion).timeout(const Duration(seconds: 60));
  }
}

/// El mapa que ya está en el teléfono.
class MapaGuardado {
  const MapaGuardado({required this.ruta, required this.version, required this.bytes});

  final String ruta;
  final String version;
  final int bytes;
}

class RepoMapa {
  RepoMapa(this.carpeta);

  final Directory carpeta;

  static const _archivo = 'mazatlan.pmtiles';
  static const _ficha = 'mazatlan.json';

  File get _final => File(p.join(carpeta.path, _archivo));
  File get _parcial => File(p.join(carpeta.path, '$_archivo.parcial'));
  File get _fichaArchivo => File(p.join(carpeta.path, _ficha));

  /// El que hay, o nulo. Una ficha sin su archivo (o al revés) no cuenta.
  MapaGuardado? guardado() {
    try {
      if (!_final.existsSync() || !_fichaArchivo.existsSync()) return null;
      final j = jsonDecode(_fichaArchivo.readAsStringSync()) as Map<String, Object?>;
      return MapaGuardado(
        ruta: _final.path,
        version: j['version'] as String,
        bytes: _final.lengthSync(),
      );
    } on Object {
      return null;
    }
  }

  /// Guarda lo que va llegando y, si llegó completo y es el que dijo el
  /// servidor, lo pone en lugar del que había.
  Future<MapaGuardado> guardar(
    ArchivoEnCamino archivo,
    MapaDelServidor info, {
    void Function(int recibidos)? progreso,
  }) async {
    if (archivo.codigo == 404) {
      throw const ErrorDelMapa('La oficina todavía no ha bajado el mapa al servidor.');
    }
    if (archivo.codigo != 200) {
      throw ErrorDelMapa('El servidor respondió ${archivo.codigo}.');
    }
    // Escritura directa, pedazo por pedazo: son unos KB cada vez y así no hay
    // un búfer de 25 MB en la memoria de un teléfono sencillo.
    carpeta.createSync(recursive: true);
    final salida = _parcial.openSync(mode: FileMode.write);
    final suma = _UltimaSuma();
    final hash = sha256.startChunkedConversion(suma);
    var recibidos = 0;
    try {
      await for (final pedazo in archivo.pedazos) {
        salida.writeFromSync(pedazo);
        hash.add(pedazo);
        recibidos += pedazo.length;
        progreso?.call(recibidos);
      }
    } on Object {
      salida.closeSync();
      _borrar(_parcial);
      rethrow;
    }
    salida.closeSync();
    hash.close();
    final completo = info.bytes == null || recibidos == info.bytes;
    final igual = info.sha256 == null || suma.valor.toString() == info.sha256;
    if (!completo || !igual) {
      _borrar(_parcial);
      throw ErrorDelMapa(!completo
          ? 'La descarga se cortó (llegaron ${megas(recibidos)} de '
              '${megas(info.bytes!)}). Vuelve a intentar con WiFi.'
          : 'El mapa llegó dañado. Vuelve a intentar con WiFi.');
    }
    _parcial.renameSync(_final.path);
    _fichaArchivo.writeAsStringSync(jsonEncode({
      'version': info.version ?? '',
      'sha256': info.sha256,
      'atribucion': info.atribucion,
    }));
    return guardado()!;
  }

  void borrar() {
    _borrar(_final);
    _borrar(_fichaArchivo);
    _borrar(_parcial);
  }

  static void _borrar(File f) {
    if (f.existsSync()) f.deleteSync();
  }
}

class _UltimaSuma implements Sink<Digest> {
  Digest? valor;

  @override
  void add(Digest data) => valor = data;

  @override
  void close() {}
}
