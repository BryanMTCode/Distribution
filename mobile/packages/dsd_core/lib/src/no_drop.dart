/// El no-drop: la visita que no terminó en venta.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LO QUE MIDE, Y POR QUÉ NO ES PAPELEO
/// ─────────────────────────────────────────────────────────────────────────
/// Sin no-drops, un día de 20 visitas y 12 ventas se ve igual que un día de 12
/// visitas y 12 ventas. El primero tiene 8 clientes que necesitan algo y el
/// segundo tiene un vendedor que se fue temprano, y **desde la oficina son
/// indistinguibles**.
///
/// Con ellos se puede preguntar lo que de verdad importa: cuántas visitas
/// perdidas son culpa nuestra (no traía lo que pidió, le pareció caro) y
/// cuántas del cliente (cerrado, no estaba quien decide).
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL MOTIVO ES CATÁLOGO CERRADO
/// ─────────────────────────────────────────────────────────────────────────
/// Texto libre son datos que nunca se van a poder analizar: "cerrado",
/// "estaba cerrado", "cerrado!!" y "crrado" son cuatro categorías distintas para
/// cualquier reporte. El catálogo viene sincronizado del servidor y cada motivo
/// trae su **categoría** —cliente, operación, producto, vendedor— que es lo que
/// después permite agrupar.
///
/// Algunos motivos exigen nota (`requiere_nota`): "no le interesa el producto" sin
/// explicación no sirve para nada, y "no alcancé a visitarlo" sin ella es una
/// excusa en blanco.
///
/// ─────────────────────────────────────────────────────────────────────────
/// SIN GPS TAMBIÉN SE REGISTRA (ADR 0002 §158)
/// ─────────────────────────────────────────────────────────────────────────
/// Antes era el único documento que exigía ubicación: sin ella, «estuve ahí y no
/// compró» no se distingue de «no fui». Pero el vendedor trabaja sin señal y
/// muchas veces bajo techo —dentro del mercado—, donde el GPS no lee, y exigirlo
/// lo dejaba sin poder cerrar al cliente: la tienda seguía pendiente en su lista.
///
/// Ahora se registra igual, como la venta: va con la ubicación si la hay, y sin
/// ella el servidor lo marca «Sin GPS» para que la oficina lo vea en revisión.
library;

import 'package:sqlite3/sqlite3.dart';

import 'dia_operativo.dart';
import 'folios.dart';
import 'outbox.dart';
import 'sobre.dart';
import 'ubicacion.dart';

/// Un motivo del catálogo sincronizado.
class MotivoDeNoDrop {
  const MotivoDeNoDrop({
    required this.codigo,
    required this.nombre,
    required this.categoria,
    required this.requiereNota,
  });

  final String codigo;
  final String nombre;

  /// `cliente`, `operacion`, `producto` o `vendedor`. Es lo que permite preguntar
  /// cuántas visitas perdidas son culpa nuestra.
  final String categoria;

  final bool requiereNota;

  /// Si la causa es algo que la empresa puede arreglar.
  bool get esNuestraCulpa =>
      categoria == 'operacion' || categoria == 'producto' || categoria == 'vendedor';
}

enum MotivoNoNoDrop {
  motivoDesconocido('motivo_desconocido'),

  /// Este motivo exige nota y llegó vacía.
  faltaNota('falta_nota'),

  /// Ya no se usa: desde la +52 el no-drop sin GPS se registra igual (ADR 0002
  /// §158). Se queda para no romper a quien todavía lo nombre.
  faltaUbicacion('falta_ubicacion'),

  sinRangoDeFolios('sin_rango_de_folios'),
  sinFolios('sin_folios'),

  /// La visita se reprograma a un día después de hoy y a lo más a un mes
  /// (ADR 0002 §103).
  reprogramacionInvalida('reprogramacion_invalida');

  const MotivoNoNoDrop(this.codigo);

  final String codigo;
}

class NoDropRechazado implements Exception {
  const NoDropRechazado(this.motivo, [this.detalle]);

  final MotivoNoNoDrop motivo;
  final String? detalle;

  @override
  String toString() =>
      'no-drop rechazado (${motivo.codigo})${detalle == null ? '' : ': $detalle'}';
}

class NoDropGuardado {
  const NoDropGuardado({
    required this.id,
    required this.folioConsecutivo,
    required this.visitaId,
    required this.clienteId,
    required this.motivoCodigo,
    required this.ubicacion,
    required this.fechaDispositivo,
    required this.fechaOperativa,
    required this.foliosRestantes,
    this.nota,
    this.reprogramadaPara,
  });

  final String id;
  final int folioConsecutivo;
  final String visitaId;
  final String clienteId;
  final String motivoCodigo;
  final String? nota;

  /// Nula si el GPS no leyó: se registró igual (ADR 0002 §158).
  final Ubicacion? ubicacion;
  final String fechaDispositivo;
  final String fechaOperativa;
  final int foliosRestantes;

  /// «Ven mañana»: el día al que se reprogramó la visita, o nulo.
  final String? reprogramadaPara;
}

/// Hasta cuántos días adelante se puede reprogramar una visita: lo que un
/// cliente pide de verdad. El servidor tiene el mismo tope.
const diasMaximosParaReprogramar = 31;

class RegistroDeNoDrop {
  RegistroDeNoDrop({
    required Database db,
    required String vendedorId,
    required String dispositivoId,
    required Outbox outbox,
    required RepoFolios folios,
    required String Function() nuevoUuid,
    required DateTime Function() ahora,
    this.rutaId,
  })  : _db = db,
        _vendedorId = vendedorId,
        _dispositivoId = dispositivoId,
        _outbox = outbox,
        _folios = folios,
        _nuevoUuid = nuevoUuid,
        _ahora = ahora;

  final Database _db;
  final String _vendedorId;
  final String _dispositivoId;
  final String? rutaId;
  final Outbox _outbox;
  final RepoFolios _folios;
  final String Function() _nuevoUuid;
  final DateTime Function() _ahora;

  /// Los motivos sincronizados, en el orden que definió la oficina.
  ///
  /// `orden` existe para que los más frecuentes queden arriba: en la calle, con el
  /// cliente esperando, un catálogo alfabético obliga a leer diez opciones para
  /// encontrar "cerrado".
  List<MotivoDeNoDrop> motivos() => _db
      .select(
        'SELECT codigo, nombre, categoria, requiere_nota FROM motivos_no_drop '
        'WHERE activo = 1 ORDER BY orden, nombre',
      )
      .map(
        (f) => MotivoDeNoDrop(
          codigo: f['codigo'] as String,
          nombre: f['nombre'] as String,
          categoria: f['categoria'] as String,
          requiereNota: (f['requiere_nota'] as int) == 1,
        ),
      )
      .toList();

  NoDropGuardado registrar({
    required String clienteId,
    required String motivoCodigo,
    required Ubicacion? ubicacion,
    String? nota,
    String? visitaId,
    String? reprogramadaPara,
  }) {
    final catalogo = {for (final m in motivos()) m.codigo: m};
    final motivo = catalogo[motivoCodigo];
    if (motivo == null) {
      throw NoDropRechazado(MotivoNoNoDrop.motivoDesconocido, motivoCodigo);
    }

    final limpia = (nota ?? '').trim();
    if (motivo.requiereNota && limpia.isEmpty) {
      throw NoDropRechazado(
        MotivoNoNoDrop.faltaNota,
        '«${motivo.nombre}» no dice nada sin explicación',
      );
    }

    // Sin GPS también se registra (ADR 0002 §158): en la calle el GPS no siempre
    // lee —bajo techo, dentro del mercado— y el vendedor tiene que poder cerrar
    // al cliente. El servidor lo marca «Sin GPS» para que la oficina lo vea.

    final rango = _folios.leer('no_drop');
    if (rango == null) {
      throw const NoDropRechazado(MotivoNoNoDrop.sinRangoDeFolios);
    }
    if (rango.agotado) {
      throw NoDropRechazado(
        MotivoNoNoDrop.sinFolios,
        'el rango terminaba en ${rango.hasta}',
      );
    }

    // Una sola lectura del reloj: dos llamadas podrían caer en segundos
    // distintos y estampar un documento con hora de un día y fecha de otro.
    final instante = _ahora();
    final momento = instante.toUtc().toIso8601String();
    final noDropId = _nuevoUuid();
    final visita = visitaId ?? _nuevoUuid();
    // El día LOCAL, no el de `momento` —que está en UTC—: en UTC−6 ese
    // atajo mandaba las ventas de la tarde al día siguiente. Ver
    // `diaOperativoDe`.
    final fechaOperativa = diaOperativoDe(instante);

    // «Ven mañana» (ADR 0002 §103): un día después de hoy, y no más allá de un
    // mes. Se revisa antes de tomar el folio, para no gastar uno en balde.
    if (reprogramadaPara != null) {
      final hoy = DateTime.parse(fechaOperativa);
      final dia = DateTime.tryParse(reprogramadaPara);
      if (dia == null ||
          !dia.isAfter(hoy) ||
          dia.difference(hoy).inDays > diasMaximosParaReprogramar) {
        throw NoDropRechazado(MotivoNoNoDrop.reprogramacionInvalida, reprogramadaPara);
      }
    }
    final consecutivo = rango.tomar();

    final sobre = SobreLocal(
      operacionId: _nuevoUuid(),
      secuencia: _outbox.siguienteSecuencia(),
      visitaId: visita,
      operaciones: [
        OperacionLocal(
          tipo: 'no_drop.crear',
          entidadId: noDropId,
          datos: {
            'folio_consecutivo': consecutivo,
            'cliente_id': clienteId,
            'vendedor_id': _vendedorId,
            'dispositivo_id': _dispositivoId,
            'ruta_id': rutaId,
            'visita_id': visita,
            'motivo_codigo': motivoCodigo,
            'nota': limpia.isEmpty ? null : limpia,
            'fecha_dispositivo': momento,
            'fecha_operativa': fechaOperativa,
            if (reprogramadaPara != null) 'reprogramada_para': reprogramadaPara,
            ...?ubicacion?.aPayload(),
          },
        ),
      ],
    );

    _outbox.encolar(
      sobre,
      creadoEn: momento,
      escribirNegocio: (db) {
        db.execute(
          '''
          INSERT INTO no_drops (id, folio_consecutivo, visita_id, cliente_id,
                                motivo_codigo, nota, lat, lng,
                                ubicacion_precision_m, ubicacion_origen,
                                fecha_dispositivo, fecha_operativa, sincronizado,
                                reprogramada_para)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
          ''',
          [
            noDropId,
            consecutivo,
            visita,
            clienteId,
            motivoCodigo,
            limpia.isEmpty ? null : limpia,
            ubicacion?.lat,
            ubicacion?.lng,
            ubicacion?.precisionMetros,
            ubicacion?.origen.codigo ?? 'gps',
            momento,
            fechaOperativa,
            reprogramadaPara,
          ],
        );

        db.execute(
          'UPDATE folios_rangos SET consumido_hasta = ? WHERE tipo = ?',
          [consecutivo, 'no_drop'],
        );
      },
    );

    return NoDropGuardado(
      id: noDropId,
      folioConsecutivo: consecutivo,
      visitaId: visita,
      clienteId: clienteId,
      motivoCodigo: motivoCodigo,
      nota: limpia.isEmpty ? null : limpia,
      ubicacion: ubicacion,
      fechaDispositivo: momento,
      fechaOperativa: fechaOperativa,
      foliosRestantes: rango.restantes,
      reprogramadaPara: reprogramadaPara,
    );
  }
}
