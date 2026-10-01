/// Pide el tablero al servidor.
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ EL TABLERO SÍ NECESITA RED Y EL RESTO DE LA APP NO
/// ─────────────────────────────────────────────────────────────────────────
/// Todo lo del vendedor funciona sin señal porque **la venta tiene que poder
/// ocurrir** aunque no haya cobertura: la mercancía sale del camión igual. El
/// tablero es lo contrario: su razón de existir es ver lo que están haciendo
/// los OTROS, y eso no se puede saber sin red. Un tablero offline solo puede
/// mostrar lo último que vio.
///
/// Y eso es exactamente lo que hace, con la hora de cuándo lo vio. La
/// alternativa —una pantalla vacía que diga "sin conexión"— es peor: el gerente
/// en la bodega, sin señal, tiene en el bolsillo las cifras de hace una hora, y
/// las cifras de hace una hora con su etiqueta sirven para muchas decisiones.
/// Ver `RepoTablero` en la app, que es quien guarda la copia.
library;

import 'dart:convert';

import 'sync_cliente.dart' show SesionInvalida, ServidorConProblemas;
import 'tablero.dart';
import 'transporte.dart';

/// El servidor contestó que este usuario no tiene permiso de ver el tablero.
///
/// Se distingue de `SesionInvalida` a propósito. Las dos llegan como 403, pero
/// significan cosas opuestas para quien está mirando la pantalla: una se
/// arregla volviendo a entrar y la otra no se arregla nunca —hay que pedirle el
/// permiso a la oficina—. Mandar a alguien al login a que teclee su PIN otra
/// vez cuando el problema es un permiso es una pérdida de tiempo garantizada.
class SinPermisoDeTablero implements Exception {
  const SinPermisoDeTablero(this.detalle);

  final String detalle;

  @override
  String toString() => 'SinPermisoDeTablero: $detalle';
}

class ClienteTablero {
  const ClienteTablero(this._transporte);

  final Transporte _transporte;

  /// El tablero de un día. Sin fecha, el de hoy.
  Future<Tablero> ver({DateTime? fecha}) async {
    final respuesta = await _transporte.obtener(
      '/v1/tablero',
      parametros: {if (fecha != null) 'fecha': _soloFecha(fecha)},
    );
    _revisar(respuesta);
    return Tablero.deJson(
      jsonDecode(respuesta.cuerpo) as Map<String, Object?>,
    );
  }

  Future<MapaDelDia> mapa({DateTime? fecha}) async {
    final respuesta = await _transporte.obtener(
      '/v1/tablero/mapa',
      parametros: {if (fecha != null) 'fecha': _soloFecha(fecha)},
    );
    _revisar(respuesta);
    return MapaDelDia.deJson(
      jsonDecode(respuesta.cuerpo) as Map<String, Object?>,
    );
  }

  /// `YYYY-MM-DD` del día **local**.
  ///
  /// No `toIso8601String().substring(0, 10)` sobre un `DateTime` en UTC: en el
  /// centro de México son seis horas de diferencia, así que antes de las 6 de
  /// la mañana ese atajo pediría el tablero de AYER. El día operativo es el día
  /// local del vendedor.
  String _soloFecha(DateTime fecha) {
    final local = fecha.toLocal();
    final mes = local.month.toString().padLeft(2, '0');
    final dia = local.day.toString().padLeft(2, '0');
    return '${local.year}-$mes-$dia';
  }

  void _revisar(RespuestaHttp r) {
    if (r.ok) return;
    if (r.codigo == 401) throw SesionInvalida(r.codigo);
    if (r.codigo == 403) throw SinPermisoDeTablero(r.cuerpo);
    throw ServidorConProblemas(r.codigo, r.cuerpo);
  }
}
