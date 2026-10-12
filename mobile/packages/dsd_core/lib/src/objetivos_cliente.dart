/// Los objetivos de cualquier periodo, desde la app de la oficina: el cliente
/// de `/v1/objetivos` (ADR 0002 §104).
///
/// «Quiero poder agregar los objetivos en la app; los objetivos pueden ser por
/// el periodo que yo decida, incluso por día.» En línea, como el tablero: el
/// avance lo calcula el servidor con las ventas del rango.
library;

import 'dart:convert';

import 'oficina_rechazo.dart';
import 'transporte.dart';

class ObjetivoConAvance {
  const ObjetivoConAvance({
    required this.id,
    required this.clase,
    required this.ruta,
    required this.desde,
    required this.hasta,
    required this.dias,
    required this.estado,
    required this.objetivoVenta,
    required this.venta,
    required this.visitas,
    required this.esperado,
    required this.semaforo,
    this.rutaId,
    this.objetivoVisitas,
    this.nota,
    this.logrado,
  });

  factory ObjetivoConAvance.deJson(Map<String, Object?> j) => ObjetivoConAvance(
        id: j['id']! as String,
        clase: j['clase']! as String,
        rutaId: j['ruta_id'] as String?,
        ruta: j['ruta'] as String?,
        desde: j['desde']! as String,
        hasta: j['hasta']! as String,
        dias: (j['dias']! as num).toInt(),
        estado: j['estado']! as String,
        objetivoVenta: j['objetivo_venta']! as String,
        objetivoVisitas: (j['objetivo_visitas'] as num?)?.toInt(),
        nota: j['nota'] as String?,
        venta: j['venta']! as String,
        visitas: (j['visitas']! as num).toInt(),
        logrado: j['logrado'] as String?,
        esperado: j['esperado']! as String,
        semaforo: j['semaforo']! as String,
      );

  final String id;

  /// 'mes' (el del mes por ruta, la barra del tablero) o 'periodo'.
  final String clase;
  final String? rutaId;

  /// Nulo = toda la empresa.
  final String? ruta;
  final String desde;
  final String hasta;
  final int dias;

  /// 'por_empezar', 'en_curso' o 'terminado'.
  final String estado;
  final String objetivoVenta;
  final int? objetivoVisitas;
  final String? nota;
  final String venta;
  final int visitas;

  /// Porcentaje logrado («50.0»), y el esperado a la fecha.
  final String? logrado;
  final String esperado;

  /// 'adelante', 'cerca', 'atras' o 'sin_objetivo'.
  final String semaforo;
}

class RutaDeObjetivo {
  const RutaDeObjetivo(this.id, this.codigo, this.nombre);

  final String id;
  final String codigo;
  final String nombre;
}

class Objetivos {
  const Objetivos({
    required this.objetivos,
    required this.rutas,
    required this.puedeFijar,
    required this.hoy,
  });

  factory Objetivos.deJson(Map<String, Object?> j) => Objetivos(
        objetivos: [
          for (final o in j['objetivos']! as List)
            ObjetivoConAvance.deJson((o as Map).cast()),
        ],
        rutas: [
          for (final r in j['rutas']! as List)
            RutaDeObjetivo(
              (r as Map)['id']! as String,
              r['codigo']! as String,
              r['nombre']! as String,
            ),
        ],
        puedeFijar: j['puede_fijar']! as bool,
        hoy: j['hoy']! as String,
      );

  final List<ObjetivoConAvance> objetivos;
  final List<RutaDeObjetivo> rutas;
  final bool puedeFijar;
  final String hoy;
}

class ClienteObjetivos {
  const ClienteObjetivos(this._transporte);

  final Transporte _transporte;

  Future<Objetivos> listar() async {
    final r = await _transporte.obtener('/v1/objetivos');
    revisarRespuestaDeOficina(r);
    return Objetivos.deJson((jsonDecode(r.cuerpo) as Map).cast());
  }

  /// [rutaId] nulo = toda la empresa. Fechas `YYYY-MM-DD`; un día es
  /// `desde == hasta`. El dinero va como texto, como en todo el contrato.
  Future<Objetivos> fijar({
    required String? rutaId,
    required String desde,
    required String hasta,
    required String objetivoVenta,
    int? objetivoVisitas,
    String? nota,
  }) async {
    final r = await _transporte.post('/v1/objetivos', {
      'ruta_id': rutaId,
      'desde': desde,
      'hasta': hasta,
      'objetivo_venta': objetivoVenta,
      if (objetivoVisitas != null) 'objetivo_visitas': objetivoVisitas,
      if (nota != null && nota.trim().isNotEmpty) 'nota': nota.trim(),
    });
    revisarRespuestaDeOficina(r);
    return Objetivos.deJson((jsonDecode(r.cuerpo) as Map).cast());
  }

  Future<Objetivos> borrar(String id) async {
    final r = await _transporte.post('/v1/objetivos/$id/borrar', const {});
    revisarRespuestaDeOficina(r);
    return Objetivos.deJson((jsonDecode(r.cuerpo) as Map).cast());
  }
}
