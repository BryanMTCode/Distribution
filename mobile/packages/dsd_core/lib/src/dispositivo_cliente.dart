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
///
/// ───────────────────────────────────────────────────────────────────────────
/// Y CUANDO SE ACABAN (ADR 0002 §171)
/// ───────────────────────────────────────────────────────────────────────────
/// Solo se pedían al vincular. El aviso decía «sincroniza para pedir más» y la
/// sincronización no pedía nada: a las mil ventas, el vendedor no podía vender.
/// Ahora la sincronización llama a [pedirFoliosSiFaltan], que pide solo cuando a
/// un tipo le quedan [foliosParaPedirMas] o menos —o no tiene rango—, y el
/// servidor alarga el MISMO rango mil folios más.
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
  ///
  /// [consumido] es lo que este teléfono lleva de cada tipo, contando lo que no
  /// ha subido: con eso el servidor sabe que el rango se está acabando aunque
  /// todavía no le hayan llegado esas ventas.
  Future<List<RangoFolios>> pedirFolios(
    String dispositivoId, {
    Map<String, int> consumido = const {},
  }) async {
    final respuesta = await _transporte.post(
      '/v1/dispositivos/$dispositivoId/folios',
      {if (consumido.isNotEmpty) 'consumido': consumido},
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

/// Los tipos que el teléfono numera hoy. El cobro ya no (solo contado, §81).
const tiposConFolio = ['venta', 'no_drop', 'merma'];

/// Con cuántos por delante se piden más. El servidor usa el mismo número
/// (`UMBRAL_PARA_RENOVAR`): el teléfono pide y el servidor alarga el rango.
const foliosParaPedirMas = 300;

/// Pide folios si a algún tipo le hacen falta, y guarda lo que llegue.
///
/// No hace nada —ni un viaje al servidor— mientras todos tengan más de
/// [foliosParaPedirMas] por delante. Devuelve si pidió. Un `ErrorDeRed` o un
/// [ServidorConProblemas] suben tal cual: quien llama lo reintenta en la
/// siguiente sincronización.
Future<bool> pedirFoliosSiFaltan({
  required Transporte transporte,
  required RepoFolios repo,
  required String dispositivoId,
  required String ahora,
}) async {
  final consumido = <String, int>{};
  var hacenFalta = false;
  for (final tipo in tiposConFolio) {
    final rango = repo.leer(tipo);
    if (rango == null) {
      hacenFalta = true;
      continue;
    }
    consumido[tipo] = rango.consumidoHasta;
    if (rango.restantes <= foliosParaPedirMas) hacenFalta = true;
  }
  if (!hacenFalta) return false;

  final rangos = await ClienteDispositivo(transporte)
      .pedirFolios(dispositivoId, consumido: consumido);
  for (final rango in rangos) {
    repo.guardar(rango, asignadoEn: ahora);
  }
  return true;
}
