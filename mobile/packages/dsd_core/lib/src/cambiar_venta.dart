/// El vendedor cambia su venta desde el teléfono: la cancela porque se equivocó
/// (ADR 0002 §161) o le agrega lo que le faltó (§162).
///
/// ───────────────────────────────────────────────────────────────────────────
/// SIN SEÑAL, POR LA COLA, Y COMO LO HACE LA OFICINA
/// ───────────────────────────────────────────────────────────────────────────
/// La venta ya es un documento con folio —y quizá ya salió en papel—, así que no
/// se borra ni se reescribe: se cancela, o se le agregan renglones. Cada cambio
/// se escribe en una sola transacción con su sobre (`venta.cancelar`,
/// `venta.agregar`), que viaja DETRÁS de su `venta.crear`: una venta hecha y
/// cambiada sin señal llega en orden. El servidor lo aplica como el panel, deja
/// la pista y marca la venta para que la oficina la mire.
///
/// ───────────────────────────────────────────────────────────────────────────
/// SOLO SE AGREGA, Y POR QUÉ
/// ───────────────────────────────────────────────────────────────────────────
/// Bajar una cantidad o quitar un renglón «regresaría» mercancía al camión sin
/// que nadie la viera: el cliente se la llevó, el vendedor cobró, y el sistema
/// cree que sigue arriba. Es el hueco por donde se escaparía un cobro. Así que
/// el vendedor solo agrega —lo agregado sale del camión y se revisa contra lo
/// que trae—, y para bajar cancela y la vuelve a hacer (queda marcada) o lo
/// corrige la oficina.
///
/// ───────────────────────────────────────────────────────────────────────────
/// SOLO EL DÍA DE TRABAJO, Y ANTES DEL CORTE
/// ───────────────────────────────────────────────────────────────────────────
/// El corte cuadró el efectivo contra las ventas de ese día; cambiar una
/// después dejaría ese papel explicando otra aritmética. Y la de un día pasado
/// puede estar ya liquidada en el servidor aunque este teléfono no tenga su
/// corte. Ésas las cambia la oficina.
library;

import 'package:sqlite3/sqlite3.dart';

import 'carrito.dart';
import 'cierre_del_dia.dart' show horaDeCambioDeDia;
import 'dia_operativo.dart';
import 'dinero.dart';
import 'outbox.dart';
import 'precio.dart';
import 'sobre.dart';
import 'ubicacion.dart';

/// Lo mínimo que se pide de motivo, como en el panel: es lo que alguien va a leer
/// dentro de seis meses.
const largoMinimoDelMotivo = 10;

enum MotivoNoSeToca {
  noExiste('no_existe'),
  yaCancelada('ya_cancelada'),

  /// Ya se hizo el corte de ese día: la cambia la oficina.
  diaCortado('dia_cortado'),

  /// Es de un día que ya pasó: la cambia la oficina.
  otroDia('otro_dia'),

  /// El motivo llegó vacío o demasiado corto.
  faltaMotivo('falta_motivo'),

  /// No hay nada que agregar, o una cantidad no es positiva.
  sinRenglones('sin_renglones'),

  /// El camión no trae lo que se quiere agregar.
  sinExistencia('sin_existencia');

  const MotivoNoSeToca(this.codigo);

  final String codigo;
}

class CambioRechazado implements Exception {
  const CambioRechazado(this.motivo, [this.detalle]);

  final MotivoNoSeToca motivo;
  final String? detalle;

  @override
  String toString() =>
      'cambio rechazado (${motivo.codigo})${detalle == null ? '' : ': $detalle'}';
}

/// Por qué el vendedor ya no puede cambiar [ventaId] desde el teléfono, o nulo
/// si puede. Lo usan la cancelación, el agregado y la pantalla, para ofrecer el
/// botón o decir por qué no.
MotivoNoSeToca? porQueNoSeToca(Database db, String ventaId, DateTime ahora) {
  final filas = db.select(
    'SELECT estado, fecha_operativa FROM ventas WHERE id = ?',
    [ventaId],
  );
  if (filas.isEmpty) return MotivoNoSeToca.noExiste;
  final v = filas.single;
  if (v['estado'] != 'confirmada') return MotivoNoSeToca.yaCancelada;

  // El día de trabajo: hoy, o ayer antes de las 5 de la mañana, como el corte
  // (§157).
  final local = ahora.toLocal();
  final hoy = diaOperativoDe(local);
  final ayer = diaOperativoDe(DateTime(local.year, local.month, local.day - 1, 12));
  final fecha = v['fecha_operativa'] as String;
  if (fecha != hoy && !(local.hour < horaDeCambioDeDia && fecha == ayer)) {
    return MotivoNoSeToca.otroDia;
  }

  final cortado = db.select(
    'SELECT 1 FROM cortes_vendedor WHERE fecha_operativa = ? LIMIT 1',
    [fecha],
  );
  if (cortado.isNotEmpty) return MotivoNoSeToca.diaCortado;
  return null;
}

/// Lo que el teléfono sabe del ticket antes del cambio, para la pista: cambiar
/// una venta después de entregar el papel es lo que más hay que mirar.
Map<String, Object?> _ticketAntes(Database db, String ventaId) {
  final v = db.select(
    'SELECT impreso, reimpresiones FROM ventas WHERE id = ?',
    [ventaId],
  ).single;
  return {
    'ticket_impreso': (v['impreso'] as int? ?? 0) == 1,
    'reimpresiones': v['reimpresiones'] as int? ?? 0,
  };
}

String _motivoLimpio(String motivo) {
  final limpio = motivo.trim();
  if (limpio.length < largoMinimoDelMotivo) {
    throw const CambioRechazado(MotivoNoSeToca.faltaMotivo);
  }
  return limpio;
}

/// La venta se cancela (ADR 0002 §161): la mercancía vuelve al camión ahí
/// mismo, para venderla otra vez, y la venta queda sin partidas y con su motivo.
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

  MotivoNoSeToca? porQueNo(String ventaId) => porQueNoSeToca(_db, ventaId, _ahora());

  /// Cancela [ventaId] con su [motivo]. Lanza [CambioRechazado] si no se puede,
  /// sin tocar nada. [ubicacion] es dónde estaba el vendedor, para la pista.
  void cancelar(String ventaId, {required String motivo, Ubicacion? ubicacion}) {
    final limpio = _motivoLimpio(motivo);
    final razon = porQueNo(ventaId);
    if (razon != null) throw CambioRechazado(razon);

    final momento = _ahora().toUtc().toIso8601String();
    final sobre = SobreLocal(
      operacionId: _nuevoUuid(),
      secuencia: _outbox.siguienteSecuencia(),
      operaciones: [
        OperacionLocal(
          tipo: 'venta.cancelar',
          entidadId: ventaId,
          datos: {
            'motivo': limpio,
            'fecha_dispositivo': momento,
            ..._ticketAntes(_db, ventaId),
            ...?ubicacion?.aPayload(),
          },
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

/// A la venta se le agrega lo que le faltó (ADR 0002 §162): renglones nuevos,
/// que salen del camión. Nunca baja ni quita (ver arriba).
class AgregadoAVenta {
  AgregadoAVenta({
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

  MotivoNoSeToca? porQueNo(String ventaId) => porQueNoSeToca(_db, ventaId, _ahora());

  /// Agrega [lineas] a [ventaId] y devuelve el total nuevo. Lanza
  /// [CambioRechazado] si no se puede, sin tocar nada.
  Dinero agregar(
    String ventaId, {
    required List<LineaCarrito> lineas,
    required String motivo,
    Ubicacion? ubicacion,
  }) {
    final limpio = _motivoLimpio(motivo);
    if (lineas.isEmpty || lineas.any((l) => l.cantidad.milesimos <= 0)) {
      throw const CambioRechazado(MotivoNoSeToca.sinRenglones);
    }
    final razon = porQueNo(ventaId);
    if (razon != null) throw CambioRechazado(razon);

    // Lo agregado sale del camión: no se vende lo que no trae.
    final pide = <String, int>{};
    for (final l in lineas) {
      pide.update(
        l.presentacion.productoId,
        (previo) => previo + l.enUnidadesBase.milesimos,
        ifAbsent: () => l.enUnidadesBase.milesimos,
      );
    }
    for (final MapEntry(key: producto, value: milesimos) in pide.entries) {
      final fila = _db.select(
        'SELECT cant_actual FROM existencias_camion WHERE producto_id = ?',
        [producto],
      );
      final trae = fila.isEmpty ? 0 : Cantidad.deBase(fila.single['cant_actual'] as num).milesimos;
      if (milesimos > trae) {
        final nombre = lineas
            .firstWhere((l) => l.presentacion.productoId == producto)
            .presentacion
            .nombre;
        throw CambioRechazado(
          MotivoNoSeToca.sinExistencia,
          '$nombre: el camión trae ${Cantidad.deBase(trae / 1000).textoCorto}',
        );
      }
    }

    final ultima = _db.select(
      'SELECT COALESCE(MAX(linea), 0) AS n FROM venta_partidas WHERE venta_id = ?',
      [ventaId],
    ).single['n'] as int;
    final renglones = [
      for (final (i, l) in lineas.indexed) (id: _nuevoUuid(), linea: ultima + i + 1, l: l),
    ];
    final agregado = lineas.fold(Dinero.cero, (acc, l) => acc + l.importe);
    final antes = _db.select('SELECT total FROM ventas WHERE id = ?', [ventaId]).single;
    final totalNuevo =
        Dinero.deTexto(((antes['total'] as num).toDouble()).toStringAsFixed(2)) + agregado;

    final momento = _ahora().toUtc().toIso8601String();
    final sobre = SobreLocal(
      operacionId: _nuevoUuid(),
      secuencia: _outbox.siguienteSecuencia(),
      operaciones: [
        OperacionLocal(
          tipo: 'venta.agregar',
          entidadId: ventaId,
          datos: {
            'motivo': limpio,
            'fecha_dispositivo': momento,
            ..._ticketAntes(_db, ventaId),
            ...?ubicacion?.aPayload(),
            'partidas': [
              for (final r in renglones)
                {
                  'id': r.id,
                  'linea': r.linea,
                  'producto_id': r.l.presentacion.productoId,
                  'unidad_codigo': r.l.presentacion.unidadCodigo,
                  'factor_unidad': r.l.presentacion.factor.texto,
                  'cantidad': r.l.cantidad.texto,
                  'cantidad_base': r.l.enUnidadesBase.texto,
                  'precio_unitario': r.l.presentacion.precio.texto,
                  'descuento': Dinero.cero.texto,
                  'importe': r.l.importe.texto,
                },
            ],
          },
        ),
      ],
    );

    _outbox.encolar(
      sobre,
      creadoEn: momento,
      escribirNegocio: (db) {
        for (final r in renglones) {
          final l = r.l;
          db.execute(
            '''
            INSERT INTO venta_partidas (id, venta_id, linea, producto_id,
                                        unidad_codigo, factor_unidad, cantidad,
                                        cantidad_base, precio_unitario, descuento,
                                        tasa_iva, importe)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            ''',
            [
              r.id,
              ventaId,
              r.linea,
              l.presentacion.productoId,
              l.presentacion.unidadCodigo,
              l.presentacion.factor.diezmilesimos / 10000,
              l.cantidad.milesimos / 1000,
              l.enUnidadesBase.milesimos / 1000,
              l.presentacion.precio.diezmilesimos / 10000,
              l.presentacion.tasaIva,
              l.importe.centavos / 100,
            ],
          );
        }
        for (final MapEntry(key: producto, value: milesimos) in pide.entries) {
          db.execute(
            'UPDATE existencias_camion SET cant_actual = cant_actual - ? '
            ' WHERE producto_id = ?',
            [milesimos / 1000, producto],
          );
        }
        // El ticket de antes ya no dice lo que la venta dice: el siguiente que
        // salga es el de la venta modificada, y lo dice (ver `ReimpresionDeVentas`).
        db.execute(
          'UPDATE ventas SET subtotal = subtotal + ?1, total = total + ?1, '
          '       modificaciones = modificaciones + 1, impreso = 0, '
          '       reimpresiones = 0, ticket_escpos = NULL '
          ' WHERE id = ?2',
          [agregado.centavos / 100, ventaId],
        );
      },
    );
    return totalNuevo;
  }
}
