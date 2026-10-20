/// El vendedor cancela su venta porque se equivocó (ADR 0002 §161).
///
/// ───────────────────────────────────────────────────────────────────────────
/// SIN SEÑAL, POR LA COLA, Y COMO LA CANCELA LA OFICINA
/// ───────────────────────────────────────────────────────────────────────────
/// La venta ya es un documento con folio —y quizá ya salió en papel—, así que no
/// se borra: se cancela. En el teléfono, en una sola transacción con su sobre:
///
/// · la mercancía vuelve al camión, para poder venderla otra vez ahí mismo;
/// · la venta queda `cancelada`, sin partidas y con su motivo;
/// · el sobre `venta.cancelar` viaja DETRÁS de su `venta.crear`, así que una
///   venta hecha y cancelada sin señal llega en orden.
///
/// El servidor la cancela igual que el panel, y su delta llega después sin
/// devolver nada otra vez: compara partidas contra partidas, y la cancelada ya no
/// tiene.
///
/// ───────────────────────────────────────────────────────────────────────────
/// DESPUÉS DEL CORTE, YA NO
/// ───────────────────────────────────────────────────────────────────────────
/// El corte cuadró el efectivo contra las ventas de ese día; cancelar una después
/// dejaría ese papel explicando otra aritmética. Ésa la cancela la oficina, que
/// decide qué hacer con el corte.
///
/// Por lo mismo, solo las del día de trabajo —hoy, o ayer antes de las 5 de la
/// mañana, como el corte (§157)—: la de un día pasado puede estar ya liquidada en
/// el servidor aunque este teléfono no tenga su corte, y el teléfono la habría
/// cancelado para nada.
library;

import 'package:sqlite3/sqlite3.dart';

import 'cierre_del_dia.dart' show horaDeCambioDeDia;
import 'dia_operativo.dart';
import 'outbox.dart';
import 'precio.dart';
import 'sobre.dart';

/// Lo mínimo que se pide de motivo, como en el panel: es lo que alguien va a leer
/// dentro de seis meses.
const largoMinimoDelMotivoDeCancelacion = 10;

enum MotivoNoCancelacion {
  noExiste('no_existe'),
  yaCancelada('ya_cancelada'),

  /// Ya se hizo el corte de ese día: la cancela la oficina.
  diaCortado('dia_cortado'),

  /// Es de un día que ya pasó: la cancela la oficina.
  otroDia('otro_dia'),

  /// El motivo llegó vacío o demasiado corto.
  faltaMotivo('falta_motivo');

  const MotivoNoCancelacion(this.codigo);

  final String codigo;
}

class CancelacionRechazada implements Exception {
  const CancelacionRechazada(this.motivo);

  final MotivoNoCancelacion motivo;

  @override
  String toString() => 'cancelación rechazada (${motivo.codigo})';
}

class CancelacionDeVenta {
  CancelacionDeVenta({
    required Database db,
    required Outbox outbox,
    required String Function() nuevoUuid,
    required DateTime Function() ahora,
  })  : _db = db,
        _outbox = outbox,
        _nuevoUuid = nuevoUuid,
        _ahora = ahora;

  final Database _db;
  final Outbox _outbox;
  final String Function() _nuevoUuid;
  final DateTime Function() _ahora;

  /// Por qué no se puede cancelar [ventaId], o nulo si se puede. La pantalla lo
  /// usa para decidir si ofrece el botón.
  MotivoNoCancelacion? porQueNo(String ventaId) {
    final filas = _db.select(
      'SELECT estado, fecha_operativa FROM ventas WHERE id = ?',
      [ventaId],
    );
    if (filas.isEmpty) return MotivoNoCancelacion.noExiste;
    final v = filas.single;
    if (v['estado'] != 'confirmada') return MotivoNoCancelacion.yaCancelada;

    final ahora = _ahora().toLocal();
    final hoy = diaOperativoDe(ahora);
    final ayer = diaOperativoDe(DateTime(ahora.year, ahora.month, ahora.day - 1, 12));
    final fecha = v['fecha_operativa'] as String;
    final delDiaDeTrabajo =
        fecha == hoy || (ahora.hour < horaDeCambioDeDia && fecha == ayer);
    if (!delDiaDeTrabajo) return MotivoNoCancelacion.otroDia;

    final cortado = _db.select(
      'SELECT 1 FROM cortes_vendedor WHERE fecha_operativa = ? LIMIT 1',
      [v['fecha_operativa']],
    );
    if (cortado.isNotEmpty) return MotivoNoCancelacion.diaCortado;
    return null;
  }

  /// Cancela [ventaId] con su [motivo]. Lanza [CancelacionRechazada] si no se
  /// puede, sin tocar nada.
  void cancelar(String ventaId, {required String motivo}) {
    final limpio = motivo.trim();
    if (limpio.length < largoMinimoDelMotivoDeCancelacion) {
      throw const CancelacionRechazada(MotivoNoCancelacion.faltaMotivo);
    }
    final razon = porQueNo(ventaId);
    if (razon != null) throw CancelacionRechazada(razon);

    final momento = _ahora().toUtc().toIso8601String();
    final sobre = SobreLocal(
      operacionId: _nuevoUuid(),
      secuencia: _outbox.siguienteSecuencia(),
      operaciones: [
        OperacionLocal(
          tipo: 'venta.cancelar',
          entidadId: ventaId,
          datos: {'motivo': limpio, 'fecha_dispositivo': momento},
        ),
      ],
    );

    _outbox.encolar(
      sobre,
      creadoEn: momento,
      escribirNegocio: (db) {
        // La mercancía vuelve al camión, por producto y en unidad base. Sin
        // `INSERT`: lo que salió del camión tiene su renglón.
        for (final f in db.select(
          'SELECT producto_id, SUM(cantidad_base) AS base FROM venta_partidas '
          ' WHERE venta_id = ? GROUP BY producto_id',
          [ventaId],
        )) {
          db.execute(
            'UPDATE existencias_camion SET cant_actual = cant_actual + ? '
            ' WHERE producto_id = ?',
            [Cantidad.deBase(f['base'] as num).milesimos / 1000, f['producto_id']],
          );
        }
        // Como la deja el delta del servidor: sin partidas, para que al llegar no
        // haya nada que devolver otra vez.
        db.execute('DELETE FROM venta_partidas WHERE venta_id = ?', [ventaId]);
        db.execute(
          "UPDATE ventas SET estado = 'cancelada', nota_oficina = ? WHERE id = ?",
          [limpio, ventaId],
        );
      },
    );
  }
}
