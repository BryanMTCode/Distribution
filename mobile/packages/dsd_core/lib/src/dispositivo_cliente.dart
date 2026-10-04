/// Los rangos de folio que el servidor le asigna a ESTE equipo.
///
/// ───────────────────────────────────────────────────────────────────────────
/// POR QUÉ ESTO EXISTE Y NO EXISTÍA
/// ───────────────────────────────────────────────────────────────────────────
/// `RepoFolios.guardar` estaba escrito y **nadie lo llamaba**: el único código
/// que ponía folios en el teléfono era el sembrador del modo demo. Sin rangos, el
/// vendedor entra a la app y no puede cerrar una venta — «este equipo no tiene
/// folios asignados» —, que es el comportamiento correcto y sin salida.
///
/// El endpoint es **idempotente**: si ya hay un rango activo para un tipo lo
/// devuelve en vez de asignar otro. Así que se puede pedir al vincularse y otra
/// vez cuando falten, sin quemar rangos.
library;

import 'dart:convert';

import 'folios.dart';
import 'sync_cliente.dart' show ServidorConProblemas;
import 'transporte.dart';

class ClienteDispositivo {
  const ClienteDispositivo(this._transporte);

  final Transporte _transporte;

  /// Pide los rangos de folio de los cuatro tipos de documento.
  ///
  /// Devuelve lo que el servidor asignó. Un `ErrorDeRed` sube tal cual: quien
  /// llama decide si reintentar, y no se inventa un rango local — dos equipos
  /// con el mismo folio es exactamente lo que el servidor existe para evitar.
  Future<List<RangoFolios>> pedirFolios(String dispositivoId) async {
    final respuesta = await _transporte.post(
      '/v1/dispositivos/$dispositivoId/folios',
      const {},
    );
    if (!respuesta.ok) {
      throw ServidorConProblemas(respuesta.codigo, respuesta.cuerpo);
    }
    final crudo = jsonDecode(respuesta.cuerpo) as List<Object?>;
    return [
      for (final fila in crudo.cast<Map<String, Object?>>())
        RangoFolios(
          tipo: fila['documento_tipo']! as String,
          desde: fila['desde']! as int,
          hasta: fila['hasta']! as int,
          consumidoHasta: fila['consumido_hasta']! as int,
        ),
    ];
  }
}
