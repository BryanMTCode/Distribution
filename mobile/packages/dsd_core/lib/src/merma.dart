/// Mermas y devoluciones: mercancía que se perdió o que regresó.
///
/// ─────────────────────────────────────────────────────────────────────────
/// SIN ESTO, AL VENDEDOR SE LE COBRA LO QUE SE ROMPIÓ
/// ─────────────────────────────────────────────────────────────────────────
/// La liquidación resta la merma del esperado:
///
///     esperado = cargado − vendido − merma + devuelto
///
/// Si una caja se rompe en el camión y nadie la registra, al cierre falta
/// mercancía que el sistema no puede explicar, y ese faltante **se le carga al
/// vendedor**. Es el caso que le duele a un vendedor honesto, y el único que no
/// puede corregir después: para el cierre, el cartón roto ya se tiró.
///
/// ─────────────────────────────────────────────────────────────────────────
/// DOS DOCUMENTOS CON SIGNOS OPUESTOS
/// ─────────────────────────────────────────────────────────────────────────
///   `merma`              la mercancía SALE del camión. Se perdió.
///   `devolucion_cliente` la mercancía ENTRA al camión. El cliente la devolvió.
///   `cambio`             el fresco SALE del camión a cambio del caducado del
///                        cliente, sin dinero. El malo cuenta como merma.
///
/// Es la misma tabla porque son el mismo hecho visto al revés —un producto que
/// cambia de manos sin dinero de por medio— y porque la liquidación los necesita
/// juntos. Pero el signo sobre el inventario del camión es contrario, y
/// equivocarlo produce un descuadre del doble del tamaño de la operación.
///
/// ─────────────────────────────────────────────────────────────────────────
/// UNA MERMA SE REGISTRA AUNQUE EL CAMIÓN DIGA QUE NO HABÍA
/// ─────────────────────────────────────────────────────────────────────────
/// Aquí la regla es la OPUESTA a la de la venta, y es deliberado.
///
/// La venta exige existencia (`cant_actual >= cantidad`) porque la mercancía
/// todavía no ha cambiado de manos: si el catálogo está desfasado, el vendedor
/// puede revisar y no vender.
///
/// La merma no la exige porque **el cartón ya está roto**. Si el camión marca 2 y
/// se rompieron 3, el que está mal es el conteo, no el mundo. Bloquearla haría que
/// la pérdida no se registrara, y entonces aparece en la liquidación como faltante
/// del vendedor — exactamente lo que este documento existe para evitar.
///
/// Es el §0.1 aplicado a la mercancía: el mundo físico ya ocurrió.
library;

import 'package:sqlite3/sqlite3.dart';

import 'dia_operativo.dart';
import 'folios.dart';
import 'outbox.dart';
import 'precio.dart';
import 'sobre.dart';
import 'ubicacion.dart';

/// Qué clase de documento es.
enum TipoDeMerma {
  /// Se perdió: caducó, se rompió, se cayó. Sale del camión.
  merma('merma'),

  /// El cliente la devolvió. Entra al camión y vuelve a la bodega al cierre.
  devolucion('devolucion_cliente'),

  /// Cambio físico: el cliente entrega un producto caducado o dañado y se lleva
  /// uno fresco del camión, sin dinero de por medio (octubre 2026, migración 0040
  /// del servidor). SALE del camión —lo que se va es el fresco— y el malo cuenta
  /// como merma. Nunca se le cobra al vendedor ni toca su arqueo.
  cambio('cambio');

  const TipoDeMerma(this.codigo);

  final String codigo;

  /// Cuánto cambia el inventario del camión: −1 si sale, +1 si entra.
  int get signo => this == TipoDeMerma.devolucion ? 1 : -1;

  /// Si el documento necesita al cliente. Una devolución sin él no se puede
  /// revisar contra su venta; un cambio sin él es mercancía que salió sin rastro.
  bool get exigeCliente => this != TipoDeMerma.merma;
}

/// Un renglón: qué producto y cuánto, en unidad base.
class RenglonDeMerma {
  const RenglonDeMerma({required this.productoId, required this.cantidadBase});

  final String productoId;

  /// SIEMPRE en unidad base. La conversión de cajas a piezas se resuelve al
  /// capturar, igual que en la carga: es la regla que evita que media empresa
  /// cuente cajas y la otra media piezas.
  final Cantidad cantidadBase;
}

enum MotivoNoMerma {
  /// Sin renglones no hay nada que registrar.
  sinRenglones('sin_renglones'),

  /// Una cantidad en cero o negativa no describe ninguna pérdida.
  cantidadInvalida('cantidad_invalida'),

  /// Una devolución **exige cliente**: sin él no se sabe a quién se le recibió, y
  /// la oficina no puede revisar si corresponde a una venta suya.
  faltaCliente('falta_cliente'),

  /// El motivo es de catálogo cerrado. Texto libre son datos que nunca se van a
  /// poder analizar, y el catálogo es lo que después permite preguntar "¿cuánto
  /// perdemos por transporte?".
  motivoDesconocido('motivo_desconocido'),

  // No hay `faltaNota`: a diferencia del no-drop, `motivos_merma` no tiene
  // `requiere_nota` en ninguna de las dos bases. Una pérdida se explica con su
  // motivo de catálogo y con la foto; declarar un rechazo que nada puede lanzar
  // solo le mentiría al que lea este enum.

  sinRangoDeFolios('sin_rango_de_folios'),
  sinFolios('sin_folios');

  const MotivoNoMerma(this.codigo);

  final String codigo;
}

class MermaRechazada implements Exception {
  const MermaRechazada(this.motivo, [this.detalle]);

  final MotivoNoMerma motivo;
  final String? detalle;

  @override
  String toString() =>
      'merma rechazada (${motivo.codigo})${detalle == null ? '' : ': $detalle'}';
}

class MermaGuardada {
  const MermaGuardada({
    required this.id,
    required this.folioConsecutivo,
    required this.tipo,
    required this.motivoCodigo,
    required this.renglones,
    required this.fechaDispositivo,
    required this.fechaOperativa,
    required this.foliosRestantes,
    this.clienteId,
    this.observaciones,
    this.ubicacion,
  });

  final String id;
  final int folioConsecutivo;
  final TipoDeMerma tipo;
  final String motivoCodigo;
  final List<RenglonDeMerma> renglones;
  final String? clienteId;
  final String? observaciones;
  final String fechaDispositivo;
  final String fechaOperativa;
  final Ubicacion? ubicacion;
  final int foliosRestantes;

  /// El total en unidad base. Es lo que la liquidación resta (o suma).
  Cantidad get total => renglones.fold(
        Cantidad.cero,
        (suma, r) => suma + r.cantidadBase,
      );
}

/// Un motivo del catálogo sincronizado.
class MotivoDeMerma {
  const MotivoDeMerma({
    required this.codigo,
    required this.nombre,
    required this.afectaVendedor,
  });

  final String codigo;
  final String nombre;

  /// Si la pérdida se le carga en la liquidación. Lo decide la OFICINA en el
  /// catálogo, nunca el vendedor al capturar: dejarlo en sus manos sería pedirle
  /// que elija si se le cobra.
  final bool afectaVendedor;
}

class RegistroDeMerma {
  RegistroDeMerma({
    required Database db,
    required String vendedorId,
    required String dispositivoId,
    required Outbox outbox,
    required RepoFolios folios,
    required String Function() nuevoUuid,
    required DateTime Function() ahora,
    this.almacenId,
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

  /// El camión. Viaja al servidor para que el movimiento de inventario sepa de
  /// dónde sale la mercancía.
  final String? almacenId;

  final Outbox _outbox;
  final RepoFolios _folios;
  final String Function() _nuevoUuid;
  final DateTime Function() _ahora;

  /// Los motivos que el servidor sincronizó. Catálogo cerrado.
  ///
  /// Los desactivados no se ofrecen: la oficina los retiró, y seguir
  /// mostrándolos haría que el vendedor escogiera uno que el servidor va a
  /// marcar para revisión sin que él hiciera nada mal.
  List<MotivoDeMerma> motivos() => _db
      .select(
        'SELECT codigo, nombre, afecta_vendedor FROM motivos_merma '
        'WHERE activo = 1 ORDER BY nombre',
      )
      .map(
        (f) => MotivoDeMerma(
          codigo: f['codigo'] as String,
          nombre: f['nombre'] as String,
          afectaVendedor: (f['afecta_vendedor'] as int) == 1,
        ),
      )
      .toList();

  MermaGuardada registrar({
    required TipoDeMerma tipo,
    required String motivoCodigo,
    required List<RenglonDeMerma> renglones,
    String? clienteId,
    String? ventaOrigenId,
    String? observaciones,
    String? visitaId,
    Ubicacion? ubicacion,
  }) {
    if (renglones.isEmpty) {
      throw const MermaRechazada(MotivoNoMerma.sinRenglones);
    }
    for (final r in renglones) {
      if (r.cantidadBase.milesimos <= 0) {
        throw MermaRechazada(
          MotivoNoMerma.cantidadInvalida,
          'el producto ${r.productoId} va en ${r.cantidadBase.textoCorto}',
        );
      }
    }
    if (tipo.exigeCliente && (clienteId ?? '').isEmpty) {
      throw MermaRechazada(
        MotivoNoMerma.faltaCliente,
        tipo == TipoDeMerma.cambio
            ? 'un cambio sin cliente es mercancía que salió sin rastro'
            : 'una devolución sin cliente no se puede revisar contra su venta',
      );
    }

    final catalogo = {for (final m in motivos()) m.codigo: m};
    if (!catalogo.containsKey(motivoCodigo)) {
      throw MermaRechazada(MotivoNoMerma.motivoDesconocido, motivoCodigo);
    }

    final rango = _folios.leer('merma');
    if (rango == null) {
      throw const MermaRechazada(MotivoNoMerma.sinRangoDeFolios);
    }
    if (rango.agotado) {
      throw MermaRechazada(
        MotivoNoMerma.sinFolios,
        'el rango terminaba en ${rango.hasta}',
      );
    }

    // Una sola lectura del reloj: dos llamadas podrían caer en segundos
    // distintos y estampar un documento con hora de un día y fecha de otro.
    final instante = _ahora();
    final momento = instante.toUtc().toIso8601String();
    final mermaId = _nuevoUuid();
    final visita = visitaId ?? _nuevoUuid();
    // El día LOCAL, no el de `momento` —que está en UTC—: en UTC−6 ese
    // atajo mandaba las ventas de la tarde al día siguiente. Ver
    // `diaOperativoDe`.
    final fechaOperativa = diaOperativoDe(instante);
    final consecutivo = rango.tomar();
    final nota = (observaciones ?? '').trim();

    // Los renglones se agrupan por producto ANTES de escribir: `merma_detalle`
    // tiene UNIQUE (merma_id, producto_id) del lado del servidor, y dos renglones
    // del mismo producto —tres piezas de una caja y dos de otra— son el caso
    // normal al capturar. Sumarlos aquí evita que el sobre caiga en cuarentena por
    // una llave duplicada.
    final porProducto = <String, Cantidad>{};
    for (final r in renglones) {
      porProducto.update(
        r.productoId,
        (v) => v + r.cantidadBase,
        ifAbsent: () => r.cantidadBase,
      );
    }
    final agrupados = [
      for (final e in porProducto.entries)
        RenglonDeMerma(productoId: e.key, cantidadBase: e.value),
    ];

    final sobre = SobreLocal(
      operacionId: _nuevoUuid(),
      secuencia: _outbox.siguienteSecuencia(),
      visitaId: visita,
      operaciones: [
        OperacionLocal(
          tipo: 'merma.crear',
          entidadId: mermaId,
          datos: {
            'folio_consecutivo': consecutivo,
            'folio_local': RangoFolios.formatear(_vendedorId, consecutivo),
            'tipo': tipo.codigo,
            'motivo_codigo': motivoCodigo,
            'dispositivo_id': _dispositivoId,
            'almacen_id': almacenId,
            'cliente_id': clienteId,
            'venta_origen_id': ventaOrigenId,
            'observaciones': nota.isEmpty ? null : nota,
            'fecha_dispositivo': momento,
            'fecha_operativa': fechaOperativa,
            if (ubicacion != null) ...ubicacion.aPayload(),
            'detalle': [
              for (final r in agrupados)
                {
                  'id': _nuevoUuid(),
                  'producto_id': r.productoId,
                  // Cantidad como string de tres decimales: contracts §1.4.
                  'cantidad_base': r.cantidadBase.texto,
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
          INSERT INTO mermas (id, folio_consecutivo, tipo, cliente_id,
                              venta_origen_id, motivo_codigo, observaciones,
                              lat, lng, fecha_dispositivo, fecha_operativa,
                              foto_subida, sincronizada)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
          ''',
          [
            mermaId,
            consecutivo,
            tipo.codigo,
            clienteId,
            ventaOrigenId,
            motivoCodigo,
            nota.isEmpty ? null : nota,
            ubicacion?.lat,
            ubicacion?.lng,
            momento,
            fechaOperativa,
          ],
        );

        for (final r in agrupados) {
          db.execute(
            'INSERT INTO merma_detalle (id, merma_id, producto_id, cantidad_base) '
            'VALUES (?, ?, ?, ?)',
            [_nuevoUuid(), mermaId, r.productoId, r.cantidadBase.milesimos / 1000],
          );

          // El inventario del camión, con el signo del tipo. SIN guarda de
          // existencia: ver la nota del encabezado. Un `UPDATE` que no afecta
          // filas tampoco es un error aquí —puede ser un producto que el camión
          // no traía registrado— y por eso se inserta la fila si falta.
          db.execute(
            '''
            INSERT INTO existencias_camion (producto_id, cant_cargada, cant_actual)
            VALUES (?1, 0, ?2)
            ON CONFLICT(producto_id) DO UPDATE SET
              cant_actual = cant_actual + ?2
            ''',
            [r.productoId, tipo.signo * r.cantidadBase.milesimos / 1000],
          );
        }

        db.execute(
          'UPDATE folios_rangos SET consumido_hasta = ? WHERE tipo = ?',
          [consecutivo, 'merma'],
        );
      },
    );

    return MermaGuardada(
      id: mermaId,
      folioConsecutivo: consecutivo,
      tipo: tipo,
      motivoCodigo: motivoCodigo,
      renglones: agrupados,
      clienteId: clienteId,
      observaciones: nota.isEmpty ? null : nota,
      fechaDispositivo: momento,
      fechaOperativa: fechaOperativa,
      ubicacion: ubicacion,
      foliosRestantes: rango.restantes,
    );
  }
}
