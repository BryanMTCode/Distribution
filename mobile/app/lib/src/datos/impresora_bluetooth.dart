/// La impresora térmica de verdad, por Bluetooth (ADR 0002 §139).
///
/// Del lado de Android (`ImpresoraBluetooth.kt`) se abre el socket con la
/// impresora ya emparejada y se le mandan los bytes. Aquí solo se habla con ese
/// canal y se traduce cada respuesta a un `ResultadoImpresion`, el mismo que
/// usaba la simulada: ni el ticket ni las pantallas cambian.
library;

import 'package:flutter/services.dart';

import 'impresora.dart';

const canalImpresora = MethodChannel('dsd/impresora');

/// Una impresora emparejada en los Ajustes de Android.
class ImpresoraEmparejada {
  const ImpresoraEmparejada({
    required this.nombre,
    required this.direccion,
    this.pareceImpresora = false,
  });

  factory ImpresoraEmparejada.deMapa(Map<Object?, Object?> m) => ImpresoraEmparejada(
        nombre: (m['nombre'] as String?) ?? (m['direccion'] as String? ?? ''),
        direccion: m['direccion']! as String,
        pareceImpresora: m['es_impresora'] == true,
      );

  final String nombre;

  /// La dirección Bluetooth («66:22:A1:3F:00:12»): es lo que la identifica.
  final String direccion;

  /// Si se anuncia como impresora. Solo sirve para ponerla primero.
  final bool pareceImpresora;

  Map<String, Object?> aMapa() => {'nombre': nombre, 'direccion': direccion};
}

/// Lo que contestó el teléfono al buscar las emparejadas.
class Emparejadas {
  const Emparejadas(this.impresoras) : problema = null;
  const Emparejadas.problema(String this.problema) : impresoras = const [];

  final List<ImpresoraEmparejada> impresoras;

  /// Por qué no se pudo buscar, dicho para el vendedor.
  final String? problema;
}

/// Las emparejadas, las que parecen impresora primero.
Future<Emparejadas> buscarEmparejadas({MethodChannel canal = canalImpresora}) async {
  try {
    final r = await canal.invokeMapMethod<String, Object?>('emparejadas');
    if (r == null) return const Emparejadas.problema('El teléfono no contestó.');
    if (r['codigo'] != 'ok') return Emparejadas.problema(_explicar(r));
    final lista = [
      for (final m in (r['impresoras'] as List? ?? const []))
        ImpresoraEmparejada.deMapa(m as Map<Object?, Object?>),
    ]..sort((a, b) {
        if (a.pareceImpresora != b.pareceImpresora) return a.pareceImpresora ? -1 : 1;
        return a.nombre.toLowerCase().compareTo(b.nombre.toLowerCase());
      });
    return Emparejadas(lista);
  } on MissingPluginException {
    return const Emparejadas.problema('Este equipo no puede usar una impresora Bluetooth.');
  } on PlatformException catch (e) {
    return Emparejadas.problema('No se pudo buscar: ${e.message ?? e.code}.');
  }
}

/// La impresora elegida, por su dirección Bluetooth.
class ImpresoraBluetooth implements Impresora {
  const ImpresoraBluetooth({
    required this.elegida,
    this.canal = canalImpresora,
  });

  final ImpresoraEmparejada elegida;
  final MethodChannel canal;

  @override
  String get nombre => elegida.nombre;

  @override
  Future<ResultadoImpresion> imprimir(List<int> bytes) async {
    try {
      final r = await canal.invokeMapMethod<String, Object?>('imprimir', {
        'direccion': elegida.direccion,
        'bytes': Uint8List.fromList(bytes),
      });
      if (r == null) return const ImpresionFallida('el teléfono no contestó');
      return switch (r['codigo']) {
        'ok' => ImpresionHecha(bytesEnviados: bytes.length),
        // Apagada, sin batería o lejos: es lo que más pasa en la calle.
        'no_conecta' => const ImpresoraDesconectada(),
        _ => ImpresionFallida(_explicar(r)),
      };
    } on MissingPluginException {
      return const ImpresionFallida('este equipo no puede usar una impresora Bluetooth');
    } on PlatformException catch (e) {
      return ImpresionFallida(e.message ?? e.code);
    }
  }
}

String _explicar(Map<String, Object?> r) => switch (r['codigo']) {
      'bluetooth_apagado' => 'El Bluetooth del teléfono está apagado: préndelo y vuelve a intentar.',
      'sin_permiso' => 'La app no tiene permiso de usar el Bluetooth. Dáselo en Ajustes del '
          'teléfono → Aplicaciones → Distribuciones SE → Permisos → Dispositivos cercanos.',
      'sin_bluetooth' => 'Este teléfono no tiene Bluetooth.',
      'error_escritura' => 'Se cortó la conexión a media impresión. Acerca la impresora y '
          'vuelve a intentar.',
      _ => '${r['detalle'] ?? 'algo falló'}.',
    };
