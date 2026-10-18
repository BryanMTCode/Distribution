/// El mapa de Mazatlán: si está en el teléfono, y bajarlo (ADR 0002 §130).
library;

import 'dart:io';

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

import '../datos/mapa_offline.dart';
import 'sincronizacion.dart';

/// Dónde queda. En la carpeta de la app y no en la de documentos: no es algo
/// que el vendedor vaya a abrir, y Android no la ofrece al compartir.
final carpetaDeMapasProvider = FutureProvider<Directory>((_) async {
  final base = await getApplicationSupportDirectory();
  return Directory(p.join(base.path, 'mapas'));
});

final repoMapaProvider = FutureProvider<RepoMapa>(
  (ref) async => RepoMapa(await ref.watch(carpetaDeMapasProvider.future)),
);

/// El mapa que hay en el teléfono, o nulo.
final mapaGuardadoProvider = FutureProvider<MapaGuardado?>(
  (ref) async => (await ref.watch(repoMapaProvider.future)).guardado(),
);

final fuenteDelMapaProvider = Provider<FuenteDelMapa>(
  (ref) => FuenteDelMapaHttp(
    baseUrl: ref.watch(baseUrlProvider),
    transporte: () => ref.read(transporteProvider),
    leerToken: () => ref.read(tokenProvider),
    renovar: () => renovarAcceso(ref),
  ),
);

sealed class EstadoDescargaDelMapa {
  const EstadoDescargaDelMapa();
}

class DescargaInactiva extends EstadoDescargaDelMapa {
  const DescargaInactiva();
}

class DescargaPreguntando extends EstadoDescargaDelMapa {
  const DescargaPreguntando();
}

class DescargaEnCurso extends EstadoDescargaDelMapa {
  const DescargaEnCurso(this.recibidos, this.total);

  final int recibidos;
  final int? total;

  double? get fraccion => total == null || total == 0 ? null : recibidos / total!;
}

class DescargaLista extends EstadoDescargaDelMapa {
  const DescargaLista(this.mapa);

  final MapaGuardado mapa;
}

class DescargaFallida extends EstadoDescargaDelMapa {
  const DescargaFallida(this.mensaje);

  final String mensaje;
}

class DescargaDelMapa extends StateNotifier<EstadoDescargaDelMapa> {
  DescargaDelMapa(this._ref) : super(const DescargaInactiva());

  final Ref _ref;

  /// Quita el «Listo» de arriba del mapa.
  void cerrarAviso() => state = const DescargaInactiva();

  Future<void> descargar() async {
    if (state is DescargaPreguntando || state is DescargaEnCurso) return;
    state = const DescargaPreguntando();
    try {
      final fuente = _ref.read(fuenteDelMapaProvider);
      final info = await fuente.info();
      if (!info.disponible) {
        state = const DescargaFallida(
          'La oficina todavía no ha bajado el mapa al servidor. Avísale: es un '
          'comando que se corre una vez.',
        );
        return;
      }
      if (!mounted) return;
      state = DescargaEnCurso(0, info.bytes);
      final repo = await _ref.read(repoMapaProvider.future);
      final mapa = await repo.guardar(
        await fuente.bajar(),
        info,
        progreso: (recibidos) {
          // Cada medio MB basta para que la barra avance sin redibujar a cada
          // pedazo de 8 KB.
          final antes = state;
          if (mounted &&
              (antes is! DescargaEnCurso || recibidos - antes.recibidos >= 500000)) {
            state = DescargaEnCurso(recibidos, info.bytes);
          }
        },
      );
      _ref.invalidate(mapaGuardadoProvider);
      if (mounted) state = DescargaLista(mapa);
    } on SinSesionParaElMapa {
      state = const DescargaFallida(
        'Para bajar el mapa hace falta señal y que la app se haya conectado: '
        'sincroniza primero, con WiFi.',
      );
    } on ErrorDeRed {
      state = const DescargaFallida(
        'No hay conexión con el servidor. Conéctate a un WiFi y vuelve a intentar.',
      );
    } on ErrorDelMapa catch (e) {
      state = DescargaFallida(e.mensaje);
    } on FileSystemException {
      state = const DescargaFallida(
        'No se pudo guardar el mapa: revisa que el teléfono tenga espacio libre.',
      );
    } on Exception catch (e) {
      state = DescargaFallida('No se pudo bajar el mapa ($e). Vuelve a intentar.');
    }
  }
}

final descargaDelMapaProvider =
    StateNotifierProvider<DescargaDelMapa, EstadoDescargaDelMapa>(DescargaDelMapa.new);
