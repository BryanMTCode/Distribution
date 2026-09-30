/// La impresora: la interfaz, y la simulada que la sustituye mientras llega.
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ ESTO ES UNA INTERFAZ
/// ─────────────────────────────────────────────────────────────────────────
/// La EC Line EC-MP200 está en otro Estado y no va a llegar pronto. Sin esta
/// abstracción, la única forma de avanzar habría sido escribir código Bluetooth
/// a ciegas y no poder probarlo.
///
/// Con ella, **el 90% del problema queda resuelto y verificado hoy**: el diseño
/// del ticket, los bytes ESC/POS, el registro de la impresión, el flujo de la
/// pantalla, y los casos de error. Lo que falta cuando llegue el equipo es una
/// implementación de `Impresora` que abra un socket y empuje los bytes —unas
/// cuantas líneas— sin tocar nada de lo demás.
///
/// Los casos de falla también se vuelven probables a voluntad, que con una
/// impresora real es casi imposible: quedarse sin papel a media ruta no se puede
/// provocar en una prueba automatizada.
library;

import 'dart:async';
import 'dart:io';

import 'package:dsd_core/dsd_core.dart';
import 'package:path_provider/path_provider.dart';

/// Cómo terminó el intento de imprimir.
///
/// Cada caso pide algo distinto del vendedor, y ninguno debe parecerse a los
/// otros en pantalla: "sin papel" se resuelve poniendo papel, "desconectada" se
/// resuelve emparejando, y "sin impresora" es que este equipo no tiene ninguna.
sealed class ResultadoImpresion {
  const ResultadoImpresion();
}

class ImpresionHecha extends ResultadoImpresion {
  const ImpresionHecha({required this.bytesEnviados, this.donde});

  final int bytesEnviados;

  /// Dónde quedó el ticket, cuando la impresora es simulada. Se muestra en
  /// pantalla para poder ir a buscarlo con `adb pull`.
  final String? donde;
}

class ImpresoraSinPapel extends ResultadoImpresion {
  const ImpresoraSinPapel();
}

class ImpresoraDesconectada extends ResultadoImpresion {
  const ImpresoraDesconectada();
}

class ImpresoraNoConfigurada extends ResultadoImpresion {
  const ImpresoraNoConfigurada();
}

class ImpresionFallida extends ResultadoImpresion {
  const ImpresionFallida(this.detalle);

  final String detalle;
}

abstract interface class Impresora {
  /// Manda los bytes. No lanza: cada falla es un caso del resultado, porque
  /// todas son situaciones normales de la calle y ninguna debe tumbar la app con
  /// el cliente enfrente.
  Future<ResultadoImpresion> imprimir(List<int> bytes);

  /// Cómo se llama la impresora en pantalla.
  String get nombre;
}

/// La impresora simulada.
///
/// Guarda cada ticket en un archivo dentro del almacenamiento de la app, con su
/// vista previa en texto **y** los bytes en crudo. El texto es para revisar el
/// diseño en el teléfono; los bytes son para el día que haya una impresora y se
/// quieran mandar tal cual.
///
///     adb exec-out run-as com.distribuidora.dsd_app \
///       cat files/tickets/ultimo.txt
class ImpresoraSimulada implements Impresora {
  ImpresoraSimulada({this.carpeta, this.falla});

  /// Dónde guardar. Si es nula, se usa el directorio de documentos de la app.
  final Directory? carpeta;

  /// Para probar los caminos de error sin hardware. En producción es nula.
  final ResultadoImpresion? falla;

  /// Todo lo que se ha "impreso" en esta sesión. Lo consumen las pruebas y la
  /// pantalla de vista previa.
  final List<List<int>> enviados = [];

  @override
  String get nombre => 'Impresora simulada';

  @override
  Future<ResultadoImpresion> imprimir(List<int> bytes) async {
    if (falla != null) return falla!;

    enviados.add(List.unmodifiable(bytes));

    try {
      final destino = carpeta ??
          Directory('${(await getApplicationDocumentsDirectory()).path}/tickets');
      await destino.create(recursive: true);

      final marca = DateTime.now().toUtc().toIso8601String().replaceAll(':', '-');
      final vista = decodificar(bytes);

      // El texto: para revisar el diseño en el teléfono, sin cable.
      await File('${destino.path}/ultimo.txt').writeAsString(
        '${vista.renderConMarco()}\n'
        '${bytes.length} bytes · ${marca}Z\n'
        '${vista.desconocidos.isEmpty ? '' : 'COMANDOS DESCONOCIDOS: '
            '${vista.desconocidos.join(", ")}\n'}',
      );
      // Los bytes en crudo: para mandarlos a una impresora real tal cual.
      await File('${destino.path}/$marca.escpos').writeAsBytes(bytes);

      return ImpresionHecha(
        bytesEnviados: bytes.length,
        donde: '${destino.path}/ultimo.txt',
      );
    } on Object catch (e) {
      // Ni siquiera un fallo al guardar debe tumbar la app: la venta ya está
      // registrada y lo único que se pierde es la copia de respaldo.
      return ImpresionFallida('no se pudo guardar el ticket: $e');
    }
  }

  /// La vista previa del último ticket, para mostrarla en pantalla.
  VistaPrevia? get ultimaVistaPrevia =>
      enviados.isEmpty ? null : decodificar(enviados.last);
}

/// La que va cuando no hay ninguna configurada.
///
/// Existe para que la app **nunca** tenga una impresora nula: un `null` obliga a
/// preguntarse en cada punto del código qué hacer, y tarde o temprano alguien
/// olvida preguntarlo.
class SinImpresora implements Impresora {
  const SinImpresora();

  @override
  String get nombre => 'Sin impresora';

  @override
  Future<ResultadoImpresion> imprimir(List<int> bytes) async =>
      const ImpresoraNoConfigurada();
}
