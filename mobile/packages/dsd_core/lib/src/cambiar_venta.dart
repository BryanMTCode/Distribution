/// El vendedor cambia su venta desde el teléfono: la cancela porque se equivocó
/// (ADR 0002 §161) o la modifica —sube, baja o quita artículos— (§162).
///
/// ───────────────────────────────────────────────────────────────────────────
/// SIN SEÑAL, POR LA COLA, Y COMO LO HACE LA OFICINA
/// ───────────────────────────────────────────────────────────────────────────
/// La venta ya es un documento con folio —y quizá ya salió en papel—, así que no
/// se borra: se cancela o se modifica. Cada cambio se escribe en una sola
/// transacción con su sobre (`venta.cancelar`, `venta.modificar`), que viaja
/// DETRÁS de su `venta.crear`: una venta hecha y cambiada sin señal llega en
/// orden. El servidor lo aplica como el panel, deja la pista y marca la venta
/// para que la oficina la mire.
///
/// ───────────────────────────────────────────────────────────────────────────
/// EL CAMIÓN SE AJUSTA, Y QUEDA LA PISTA
/// ───────────────────────────────────────────────────────────────────────────
/// Lo que sube sale del camión y se revisa contra lo que trae; lo que baja o se
/// quita vuelve al camión ahí mismo. Que se pueda bajar abre la puerta a decir
/// que algo «regresó» cuando el cliente se lo llevó, así que cada cambio lleva
/// a la oficina si el ticket ya se había impreso y dónde estaba el vendedor, y
/// el camión se cuadra al volver a la bodega.
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

  /// La venta quedaría sin renglones (eso es cancelar), o una cantidad no es
  /// positiva.
  sinRenglones('sin_renglones'),

  /// No cambió nada.
  sinCambios('sin_cambios'),

  /// El camión no trae lo que se quiere subir.
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

/// Un renglón de la venta como debe quedar. [partidaId] es el del renglón que ya
/// estaba —solo cambia de cantidad—, o nulo para uno nuevo.
class RenglonDeVenta {
  const RenglonDeVenta({this.partidaId, required this.linea});

  final String? partidaId;
  final LineaCarrito linea;
}

/// La venta se modifica (ADR 0002 §162): sube, baja o quita artículos. Lo que
/// sube sale del camión; lo que baja o se quita vuelve.
class ModificacionDeVenta {
  ModificacionDeVenta({
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

  /// La venta como está, en el orden del ticket: el punto de partida para
  /// modificarla. El precio es el de la venta, no el del catálogo de hoy.
  List<RenglonDeVenta> renglonesDe(String ventaId) => [
        for (final f in _db.select(
          '''
          SELECT vp.id, vp.producto_id, vp.unidad_codigo, vp.factor_unidad,
                 vp.cantidad, vp.precio_unitario, vp.tasa_iva,
                 COALESCE(p.nombre, vp.producto_id) AS nombre,
                 COALESCE(p.sku, '') AS sku, COALESCE(p.unidad_base, 'PZA') AS base,
                 v.lista_precios_id, v.lista_precios_version
            FROM venta_partidas vp
            JOIN ventas v ON v.id = vp.venta_id
            LEFT JOIN productos p ON p.id = vp.producto_id
           WHERE vp.venta_id = ?
           ORDER BY vp.linea
          ''',
          [ventaId],
        ))
          RenglonDeVenta(
            partidaId: f['id'] as String,
            linea: LineaCarrito(
              cantidad: Cantidad.deBase(f['cantidad'] as num),
              presentacion: PresentacionVendible(
                productoId: f['producto_id'] as String,
                sku: f['sku'] as String,
                nombre: f['nombre'] as String,
                unidadCodigo: f['unidad_codigo'] as String,
                unidadBaseCodigo: f['base'] as String,
                factor: Factor.deBase(f['factor_unidad'] as num),
                precio: Precio.deBase(f['precio_unitario'] as num),
                listaPreciosId: f['lista_precios_id'] as String? ?? '',
                listaPreciosVersion: f['lista_precios_version'] as int? ?? 0,
                tasaIva: (f['tasa_iva'] as num?)?.toDouble() ?? 0,
              ),
            ),
          ),
      ];

  /// Deja [ventaId] como dicen [renglones] y devuelve el total nuevo. Lanza
  /// [CambioRechazado] si no se puede, sin tocar nada.
  Dinero modificar(
    String ventaId, {
    required List<RenglonDeVenta> renglones,
    required String motivo,
    Ubicacion? ubicacion,
  }) {
    final limpio = _motivoLimpio(motivo);
    if (renglones.isEmpty || renglones.any((r) => r.linea.cantidad.milesimos <= 0)) {
      throw const CambioRechazado(MotivoNoSeToca.sinRenglones);
    }
    final razon = porQueNo(ventaId);
    if (razon != null) throw CambioRechazado(razon);

    final actuales = {for (final r in renglonesDe(ventaId)) r.partidaId!: r.linea};
    var linea = _db.select(
      'SELECT COALESCE(MAX(linea), 0) AS n FROM venta_partidas WHERE venta_id = ?',
      [ventaId],
    ).single['n'] as int;

    // Cómo queda: el renglón que ya estaba conserva su producto, su unidad y su
    // precio, y solo cambia de cantidad.
    final quedan = <({String id, bool nuevo, LineaCarrito l})>[];
    for (final r in renglones) {
      final antes = r.partidaId == null ? null : actuales[r.partidaId];
      if (r.partidaId != null && antes == null) {
        throw ArgumentError('el renglón ${r.partidaId} no es de la venta $ventaId');
      }
      quedan.add(
        antes == null
            ? (id: _nuevoUuid(), nuevo: true, l: r.linea)
            : (id: r.partidaId!, nuevo: false, l: antes.conCantidad(r.linea.cantidad)),
      );
    }
    final ids = {for (final q in quedan) q.id};
    final quitados = [for (final id in actuales.keys) if (!ids.contains(id)) id];
    final cambiados = [
      for (final q in quedan)
        if (!q.nuevo && q.l.cantidad != actuales[q.id]!.cantidad) q,
    ];
    if (quitados.isEmpty && cambiados.isEmpty && quedan.every((q) => !q.nuevo)) {
      throw const CambioRechazado(MotivoNoSeToca.sinCambios);
    }

    // El camión, por producto: lo que sube tiene que estar arriba.
    final diferencia = <String, int>{};
    for (final l in actuales.values) {
      diferencia.update(l.presentacion.productoId, (d) => d - l.enUnidadesBase.milesimos,
          ifAbsent: () => -l.enUnidadesBase.milesimos);
    }
    for (final q in quedan) {
      diferencia.update(q.l.presentacion.productoId, (d) => d + q.l.enUnidadesBase.milesimos,
          ifAbsent: () => q.l.enUnidadesBase.milesimos);
    }
    for (final MapEntry(key: producto, value: sube) in diferencia.entries) {
      if (sube <= 0) continue;
      final fila = _db.select(
        'SELECT cant_actual FROM existencias_camion WHERE producto_id = ?',
        [producto],
      );
      final trae =
          fila.isEmpty ? 0 : Cantidad.deBase(fila.single['cant_actual'] as num).milesimos;
      if (sube > trae) {
        final p = quedan.firstWhere((q) => q.l.presentacion.productoId == producto).l.presentacion;
        throw CambioRechazado(
          MotivoNoSeToca.sinExistencia,
          '${p.nombre}: el camión trae ${Cantidad.deBase(trae / 1000).textoCorto} '
          '${p.unidadBaseCodigo}',
        );
      }
    }

    final suma = quedan.fold(Dinero.cero, (acc, q) => acc + q.l.importe);
    final momento = _ahora().toUtc().toIso8601String();
    final numeradas = [
      for (final q in quedan) (q: q, linea: q.nuevo ? ++linea : 0),
    ];
    final sobre = SobreLocal(
      operacionId: _nuevoUuid(),
      secuencia: _outbox.siguienteSecuencia(),
      operaciones: [
        OperacionLocal(
          tipo: 'venta.modificar',
          // Cada modificación es su propio documento: la cola no admite dos
          // operaciones del mismo tipo sobre la misma entidad, y una venta se
          // puede modificar más de una vez.
          entidadId: _nuevoUuid(),
          datos: {
            'venta_id': ventaId,
            'motivo': limpio,
            'fecha_dispositivo': momento,
            ..._ticketAntes(_db, ventaId),
            ...?ubicacion?.aPayload(),
            // La venta COMO DEBE QUEDAR; el que no viene, se quita.
            'partidas': [
              for (final (q: q, linea: n) in numeradas)
                {
                  'id': q.id,
                  if (q.nuevo) 'linea': n,
                  'producto_id': q.l.presentacion.productoId,
                  'unidad_codigo': q.l.presentacion.unidadCodigo,
                  'factor_unidad': q.l.presentacion.factor.texto,
                  'cantidad': q.l.cantidad.texto,
                  'cantidad_base': q.l.enUnidadesBase.texto,
                  'precio_unitario': q.l.presentacion.precio.texto,
                  'descuento': Dinero.cero.texto,
                  'importe': q.l.importe.texto,
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
        for (final id in quitados) {
          db.execute('DELETE FROM venta_partidas WHERE id = ?', [id]);
        }
        for (final q in cambiados) {
          db.execute(
            'UPDATE venta_partidas SET cantidad = ?, cantidad_base = ?, importe = ? '
            ' WHERE id = ?',
            [
              q.l.cantidad.milesimos / 1000,
              q.l.enUnidadesBase.milesimos / 1000,
              q.l.importe.centavos / 100,
              q.id,
            ],
          );
        }
        for (final (q: q, linea: n) in numeradas) {
          if (!q.nuevo) continue;
          final l = q.l;
          db.execute(
            '''
            INSERT INTO venta_partidas (id, venta_id, linea, producto_id,
                                        unidad_codigo, factor_unidad, cantidad,
                                        cantidad_base, precio_unitario, descuento,
                                        tasa_iva, importe)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            ''',
            [
              q.id,
              ventaId,
              n,
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
        // Lo que sube sale del camión; lo que baja o se quita vuelve.
        for (final MapEntry(key: producto, value: sube) in diferencia.entries) {
          if (sube == 0) continue;
          db.execute(
            'UPDATE existencias_camion SET cant_actual = cant_actual - ? '
            ' WHERE producto_id = ?',
            [sube / 1000, producto],
          );
        }
        // El ticket de antes ya no dice lo que la venta dice: el siguiente que
        // salga es el de la venta modificada, y lo dice (ver `ReimpresionDeVentas`).
        db.execute(
          'UPDATE ventas SET subtotal = ?1, total = ?1 - descuento + impuestos, '
          '       modificaciones = modificaciones + 1, impreso = 0, '
          '       reimpresiones = 0, ticket_escpos = NULL '
          ' WHERE id = ?2',
          [suma.centavos / 100, ventaId],
        );
      },
    );
    final total = _db.select('SELECT total FROM ventas WHERE id = ?', [ventaId]).single;
    return Dinero.deTexto(((total['total'] as num).toDouble()).toStringAsFixed(2));
  }
}
