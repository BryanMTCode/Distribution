/// La devolución de mercancía del camión a la bodega.
///
/// ───────────────────────────────────────────────────────────────────────────
/// POR QUÉ LO INICIA EL VENDEDOR
/// ───────────────────────────────────────────────────────────────────────────
/// La migración 0004 imaginó el traspaso al revés —la oficina propone, el vendedor
/// acepta— y para el sentido bodega → camión es correcto: nadie puede meterle
/// mercancía al camión de alguien sin su consentimiento.
///
/// Para camión → bodega el dueño del origen es el vendedor, así que él lo inicia y
/// **sin señal**: es el único que sabe que acaba de bajar 18 cajas, y exigirle
/// conexión para registrarlo haría que lo apuntara en papel — que es exactamente
/// cómo se pierde un sistema.
///
/// ───────────────────────────────────────────────────────────────────────────
/// LA MERCANCÍA SALE DEL CAMIÓN Y NO LLEGA A LA BODEGA TODAVÍA
/// ───────────────────────────────────────────────────────────────────────────
/// Lo que este documento mueve es camión → **tránsito**. La bodega sube cuando
/// alguien recibe y dice cuánto contó.
///
/// Si la declaración del vendedor moviera la mercancía directo a la bodega, un
/// faltante se podría cubrir escribiendo una devolución que nunca se entregó: su
/// camión baja, la bodega sube, y nadie contó nada. Sería la única operación del
/// sistema donde la palabra de una persona mueve dos almacenes.
///
/// ───────────────────────────────────────────────────────────────────────────
/// SIN GUARDA DE EXISTENCIA, COMO LA MERMA
/// ───────────────────────────────────────────────────────────────────────────
/// Si el vendedor dice que bajó 18 cajas, bajó 18 cajas. Que el conteo del teléfono
/// diga que traía 12 no cambia el hecho físico: se registra y el camión queda en
/// negativo, que es una señal honesta (§0.1). Rechazarlo no devolvería la mercancía
/// al camión y haría que el vendedor dejara de registrar devoluciones.
library;

import 'package:sqlite3/sqlite3.dart';

import 'dia_operativo.dart';
import 'outbox.dart';
import 'precio.dart';
import 'sobre.dart';

/// Un renglón de la devolución, en unidad base.
class RenglonDeTraspaso {
  const RenglonDeTraspaso({required this.productoId, required this.cantidadBase});

  final String productoId;
  final Cantidad cantidadBase;
}

/// Por qué no se pudo registrar. Son dos, y ninguna es una regla de negocio.
enum MotivoNoTraspaso {
  /// Ningún renglón, o todos en cero. No hay nada que devolver.
  sinRenglones('sin_renglones'),

  /// Falta el camión: sin él el servidor no sabe de dónde sale la mercancía.
  sinAlmacen('sin_almacen');

  const MotivoNoTraspaso(this.codigo);

  final String codigo;
}

class TraspasoRechazado implements Exception {
  const TraspasoRechazado(this.motivo);

  final MotivoNoTraspaso motivo;

  @override
  String toString() => 'traspaso rechazado (${motivo.codigo})';
}

/// Una devolución ya guardada, lista para mostrarse.
class TraspasoGuardado {
  const TraspasoGuardado({
    required this.id,
    required this.renglones,
    required this.fechaDispositivo,
    required this.fechaOperativa,
    this.observaciones,
  });

  final String id;
  final List<RenglonDeTraspaso> renglones;
  final String fechaDispositivo;
  final String fechaOperativa;
  final String? observaciones;

  Cantidad get totalBase => renglones.fold(
        Cantidad.cero,
        (acumulado, r) => acumulado + r.cantidadBase,
      );
}

class RegistroDeTraspaso {
  RegistroDeTraspaso({
    required Database db,
    required String dispositivoId,
    required Outbox outbox,
    required String Function() nuevoUuid,
    required DateTime Function() ahora,
    this.almacenId,
  })  : _db = db,
        _dispositivoId = dispositivoId,
        _outbox = outbox,
        _nuevoUuid = nuevoUuid,
        _ahora = ahora;

  final Database _db;
  final String _dispositivoId;

  /// El camión del que sale la mercancía. El servidor usa el del token, así que
  /// esto viaja por claridad del documento, no como autoridad.
  final String? almacenId;

  final Outbox _outbox;
  final String Function() _nuevoUuid;
  final DateTime Function() _ahora;

  /// Registra la devolución: baja el camión y encola el documento.
  ///
  /// Los renglones se agrupan por producto: dos capturas del mismo producto son un
  /// solo renglón, porque `traspaso_detalle` es único por producto y porque dos
  /// renglones del mismo artículo en un documento no significan nada distinto.
  TraspasoGuardado registrar({
    required List<RenglonDeTraspaso> renglones,
    String? observaciones,
  }) {
    if (almacenId == null) {
      throw const TraspasoRechazado(MotivoNoTraspaso.sinAlmacen);
    }

    final porProducto = <String, int>{};
    for (final r in renglones) {
      if (r.cantidadBase.milesimos <= 0) continue;
      porProducto.update(
        r.productoId,
        (previo) => previo + r.cantidadBase.milesimos,
        ifAbsent: () => r.cantidadBase.milesimos,
      );
    }
    if (porProducto.isEmpty) {
      throw const TraspasoRechazado(MotivoNoTraspaso.sinRenglones);
    }

    final agrupados = [
      for (final e in porProducto.entries)
        RenglonDeTraspaso(
          productoId: e.key,
          cantidadBase: Cantidad.deBase(e.value / 1000),
        ),
    ];

    // Una sola lectura del reloj: dos llamadas podrían caer en días distintos a
    // medianoche y estampar el documento con una fecha y el día operativo con otra.
    final instante = _ahora();
    final momento = instante.toUtc().toIso8601String();
    final fechaOperativa = diaOperativoDe(instante);
    final traspasoId = _nuevoUuid();
    final nota = (observaciones ?? '').trim();

    final sobre = SobreLocal(
      operacionId: _nuevoUuid(),
      secuencia: _outbox.siguienteSecuencia(),
      operaciones: [
        OperacionLocal(
          tipo: 'traspaso.crear',
          entidadId: traspasoId,
          datos: {
            'dispositivo_id': _dispositivoId,
            'almacen_origen_id': almacenId,
            'observaciones': nota.isEmpty ? null : nota,
            'fecha_dispositivo': momento,
            'fecha_operativa': fechaOperativa,
            'detalle': [
              for (final r in agrupados)
                {
                  'id': _nuevoUuid(),
                  'producto_id': r.productoId,
                  // Cantidad como string de tres decimales: contracts §1.4.
                  'cantidad': r.cantidadBase.texto,
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
        db.execute(
          '''
          INSERT INTO traspasos (id, estado, observaciones, fecha_dispositivo,
                                 fecha_operativa, sincronizado)
          VALUES (?, 'propuesto', ?, ?, ?, 0)
          ''',
          [traspasoId, nota.isEmpty ? null : nota, momento, fechaOperativa],
        );

        for (final r in agrupados) {
          db.execute(
            'INSERT INTO traspaso_detalle (id, traspaso_id, producto_id, '
            '                              cantidad_base) VALUES (?, ?, ?, ?)',
            [
              _nuevoUuid(),
              traspasoId,
              r.productoId,
              r.cantidadBase.milesimos / 1000,
            ],
          );

          // El camión baja. SIN guarda de existencia —ver el encabezado— y con
          // `INSERT ... ON CONFLICT` por si el producto no estaba registrado
          // arriba: el renglón en negativo es la señal correcta.
          db.execute(
            '''
            INSERT INTO existencias_camion (producto_id, cant_cargada, cant_actual)
            VALUES (?1, 0, ?2)
            ON CONFLICT(producto_id) DO UPDATE SET
              cant_actual = cant_actual + ?2
            ''',
            [r.productoId, -r.cantidadBase.milesimos / 1000],
          );
        }
      },
    );

    return TraspasoGuardado(
      id: traspasoId,
      renglones: agrupados,
      fechaDispositivo: momento,
      fechaOperativa: fechaOperativa,
      observaciones: nota.isEmpty ? null : nota,
    );
  }

  /// Las devoluciones de este teléfono, lo más reciente primero.
  ///
  /// Se leen con su detalle contado porque es lo que el vendedor quiere ver: si lo
  /// que entregó ya se recibió, y si lo que contaron coincide con lo que bajó.
  List<TraspasoEnLista> recientes({int limite = 20}) => _db
      .select(
        '''
        SELECT t.id, t.folio, t.estado, t.fecha_operativa, t.resuelto_en,
               COUNT(d.id)                       AS renglones,
               COALESCE(SUM(d.cantidad_base), 0) AS declarado,
               SUM(CASE WHEN d.cantidad_recibida IS NULL THEN 1 ELSE 0 END)
                                                 AS sin_contar,
               COALESCE(SUM(COALESCE(d.cantidad_recibida, 0)), 0) AS recibido
          FROM traspasos t
          LEFT JOIN traspaso_detalle d ON d.traspaso_id = t.id
         GROUP BY t.id
         ORDER BY t.fecha_dispositivo DESC
         LIMIT ?
        ''',
        [limite],
      )
      .map(
        (f) => TraspasoEnLista(
          id: f['id'] as String,
          folio: f['folio'] as String?,
          estado: f['estado'] as String,
          fechaOperativa: f['fecha_operativa'] as String,
          resueltoEn: f['resuelto_en'] as String?,
          renglones: f['renglones'] as int,
          declarado: Cantidad.deBase((f['declarado'] as num).toDouble()),
          recibido: (f['sin_contar'] as int) > 0
              ? null
              : Cantidad.deBase((f['recibido'] as num).toDouble()),
        ),
      )
      .toList();
}

/// Una devolución en la lista del vendedor.
class TraspasoEnLista {
  const TraspasoEnLista({
    required this.id,
    required this.estado,
    required this.fechaOperativa,
    required this.renglones,
    required this.declarado,
    this.folio,
    this.resueltoEn,
    this.recibido,
  });

  final String id;

  /// El que puso el servidor. `null` mientras el documento no ha subido.
  final String? folio;
  final String estado;
  final String fechaOperativa;

  /// Cuándo la recibieron en la bodega. `null` mientras nadie ha contado.
  final String? resueltoEn;

  final int renglones;

  /// Lo que el vendedor declaró, en unidad base.
  final Cantidad declarado;

  /// Lo que la bodega contó. `null` mientras algún renglón siga sin contar.
  final Cantidad? recibido;

  bool get recibida => estado == 'aceptado';

  /// `true` cuando la bodega contó algo distinto de lo declarado.
  bool get hayDiferencia => recibido != null && recibido != declarado;
}
