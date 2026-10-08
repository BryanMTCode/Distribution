/// El cierre del día del vendedor: su corte y la carga que pide (ADR 0002 §82).
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL FLUJO
/// ─────────────────────────────────────────────────────────────────────────
/// 1. **Corte del día.** El vendedor cuenta lo que le sobró arriba del camión y
///    el efectivo que entrega. Sale un ticket con lo vendido, el efectivo y el
///    sobrante, para compartirlo por WhatsApp.
/// 2. **Solicitar carga.** Inmediatamente después pide la carga del día
///    siguiente. Solo hay UNA carga al día, y es para mañana.
/// 3. La oficina revisa el corte junto con la solicitud y la acepta: cierra el
///    corte y confirma la carga. Al teléfono le llega por delta
///    ('solicitud_carga') con el folio, y la mercancía como cualquier carga.
///
/// Todo se hace SIN señal: el corte y la solicitud se escriben aquí y viajan
/// por la cola en el orden en que se hicieron —el corte detrás de todas las
/// ventas del día—, con el id que les da el teléfono para que el reenvío no
/// los duplique.
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL CONTEO ES A CIEGAS
/// ─────────────────────────────────────────────────────────────────────────
/// `ResumenDelDia.productos` NO trae cuánto cree el sistema que hay de cada
/// cosa. Un conteo con la cifra a la vista se vuelve un copiado, y entonces el
/// corte ya no dice nada: el faltante se descubre el día que alguien cuenta
/// de verdad. La comparación la ve la oficina antes de aceptar.
library;

import 'package:sqlite3/sqlite3.dart';

import 'dia_operativo.dart';
import 'dinero.dart';
import 'outbox.dart';
import 'precio.dart';
import 'sobre.dart';
import 'tickets_compartibles.dart';

/// Una presentación en la que se puede pedir un producto: «CAJA de 24».
class Presentacion {
  const Presentacion({required this.unidad, required this.factor});

  final String unidad;
  final Factor factor;
}

/// Un producto que el vendedor tiene que contar al cerrar.
class ProductoPorContar {
  const ProductoPorContar({
    required this.productoId,
    required this.nombre,
    required this.sku,
    required this.unidadBase,
    this.presentaciones = const [],
  });

  final String productoId;
  final String nombre;
  final String sku;
  final String unidadBase;
  final List<Presentacion> presentaciones;

  /// La presentación más grande que no es la base: para contar «2 cajas y 3».
  Presentacion? get mayor {
    Presentacion? mejor;
    for (final p in presentaciones) {
      if (p.factor.esUno) continue;
      if (mejor == null || p.factor.compareTo(mejor.factor) > 0) mejor = p;
    }
    return mejor;
  }
}

/// Lo que el vendedor ve antes de hacer su corte.
class ResumenDelDia {
  const ResumenDelDia({
    required this.fechaOperativa,
    required this.ventas,
    required this.efectivo,
    required this.transferencias,
    required this.sinSincronizar,
    required this.productos,
    this.cargaId,
  });

  final String fechaOperativa;
  final int ventas;

  /// Lo vendido en efectivo: lo que tendría que traer en la bolsa.
  final Dinero efectivo;
  final Dinero transferencias;

  /// Documentos del día que no han subido. El corte viaja detrás de ellos en
  /// la cola, pero el vendedor tiene que saber que la oficina no los ve aún.
  final int sinSincronizar;

  /// Lo que hay que contar, sin la cifra del sistema (ver la nota de arriba).
  final List<ProductoPorContar> productos;
  final String? cargaId;

  Dinero get vendido => efectivo + transferencias;
}

/// Un producto del catálogo, para pedirlo en la carga.
class ProductoParaPedir {
  const ProductoParaPedir({
    required this.productoId,
    required this.nombre,
    required this.sku,
    required this.unidadBase,
    required this.presentaciones,
  });

  final String productoId;
  final String nombre;
  final String sku;
  final String unidadBase;
  final List<Presentacion> presentaciones;

  /// Se pide por la presentación más grande: en la bodega se carga por caja.
  Presentacion get porOmision {
    var mejor = presentaciones.first;
    for (final p in presentaciones) {
      if (p.factor.compareTo(mejor.factor) > 0) mejor = p;
    }
    return mejor;
  }
}

/// Un renglón de la solicitud: cuántos bultos de qué presentación.
class RenglonPedido {
  const RenglonPedido({
    required this.productoId,
    required this.nombre,
    required this.unidad,
    required this.factor,
    required this.bultos,
    this.unidadBase = 'PZA',
  });

  final String productoId;
  final String nombre;
  final String unidad;
  final Factor factor;
  final int bultos;
  final String unidadBase;

  Cantidad get enUnidadBase => cantidadBase(Cantidad.deEnteros(bultos), factor);
}

enum MotivoNoCierre {
  /// Un producto contado en negativo: es un dedazo.
  conteoNegativo('conteo_negativo'),

  /// Efectivo negativo: también.
  efectivoNegativo('efectivo_negativo'),

  /// Una solicitud sin un solo renglón no pide nada.
  sinRenglones('sin_renglones'),

  /// Bultos en cero o negativos.
  bultosInvalidos('bultos_invalidos');

  const MotivoNoCierre(this.codigo);

  final String codigo;
}

class CierreNoValido implements Exception {
  const CierreNoValido(this.motivo, [this.detalle]);

  final MotivoNoCierre motivo;
  final String? detalle;

  @override
  String toString() =>
      'cierre no válido (${motivo.codigo})${detalle == null ? '' : ': $detalle'}';
}

/// El corte, tal como quedó guardado en el teléfono.
class CorteDelVendedor {
  const CorteDelVendedor({
    required this.id,
    required this.fechaOperativa,
    required this.efectivoDeclarado,
    required this.efectivoEsperado,
    required this.textoTicket,
    required this.fechaDispositivo,
    required this.sincronizado,
    this.cargaId,
    this.observaciones,
  });

  final String id;
  final String fechaOperativa;
  final String? cargaId;
  final Dinero efectivoDeclarado;
  final Dinero efectivoEsperado;
  final String? observaciones;
  final String textoTicket;
  final String fechaDispositivo;
  final bool sincronizado;
}

/// La solicitud de carga, con lo que la oficina contestó si ya contestó.
class SolicitudDeCarga {
  const SolicitudDeCarga({
    required this.id,
    required this.fechaOperativa,
    required this.estado,
    required this.renglones,
    required this.fechaDispositivo,
    required this.sincronizado,
    this.corteId,
    this.cargaId,
    this.cargaFolio,
    this.motivo,
    this.observaciones,
    this.resueltaEn,
  });

  final String id;
  final String? corteId;

  /// PARA cuándo es la carga.
  final String fechaOperativa;

  /// 'pendiente' | 'aceptada' | 'rechazada' | 'reemplazada'.
  final String estado;
  final String? cargaId;
  final String? cargaFolio;
  final String? motivo;
  final String? observaciones;
  final String? resueltaEn;
  final String fechaDispositivo;
  final bool sincronizado;
  final List<RenglonDeTicket> renglones;

  bool get pendiente => estado == 'pendiente';
  bool get aceptada => estado == 'aceptada';
  bool get rechazada => estado == 'rechazada';
}

/// Escribe y lee el cierre del día en la base del teléfono.
class RegistroDeCierre {
  RegistroDeCierre({
    required Database db,
    required Outbox outbox,
    required String dispositivoId,
    required String vendedor,
    required String Function() nuevoUuid,
    required DateTime Function() ahora,
    this.codigoVendedor,
  })  : _db = db,
        _outbox = outbox,
        _dispositivoId = dispositivoId,
        _vendedor = vendedor,
        _nuevoUuid = nuevoUuid,
        _ahora = ahora;

  final Database _db;
  final Outbox _outbox;
  final String _dispositivoId;
  final String _vendedor;
  final String? codigoVendedor;
  final String Function() _nuevoUuid;
  final DateTime Function() _ahora;

  String get hoy => diaOperativoDe(_ahora());

  // -------------------------------------------------------------------------
  // Lectura
  // -------------------------------------------------------------------------

  ResumenDelDia resumen() {
    final dia = hoy;
    final ventas = _db.select(
      '''
      SELECT COUNT(*) AS n,
             COALESCE(SUM(CASE WHEN forma_pago = 'transferencia' THEN 0 ELSE total END), 0)
               AS efectivo,
             COALESCE(SUM(CASE WHEN forma_pago = 'transferencia' THEN total ELSE 0 END), 0)
               AS transferencias,
             COALESCE(SUM(CASE WHEN COALESCE(sincronizada, 0) = 0 THEN 1 ELSE 0 END), 0)
               AS sin_subir
        FROM ventas
       WHERE fecha_operativa = ? AND estado = 'confirmada'
      ''',
      [dia],
    ).single;

    final productos = _db
        .select(
          '''
          SELECT e.producto_id, COALESCE(p.nombre, e.producto_id) AS nombre,
                 COALESCE(p.sku, '') AS sku, COALESCE(p.unidad_base, 'PZA') AS unidad_base
            FROM existencias_camion e
            LEFT JOIN productos p ON p.id = e.producto_id
           WHERE e.cant_actual <> 0 OR e.cant_cargada <> 0
           ORDER BY nombre
          ''',
        )
        .map(
          (f) => ProductoPorContar(
            productoId: f['producto_id'] as String,
            nombre: f['nombre'] as String,
            sku: f['sku'] as String,
            unidadBase: f['unidad_base'] as String,
            presentaciones: _presentacionesDe(f['producto_id'] as String),
          ),
        )
        .toList();

    final carga = _db.select(
      "SELECT valor FROM sync_estado WHERE clave = 'carga_id_activa'",
    );

    return ResumenDelDia(
      fechaOperativa: dia,
      ventas: ventas['n'] as int,
      efectivo: _dinero(ventas['efectivo']),
      transferencias: _dinero(ventas['transferencias']),
      sinSincronizar: ventas['sin_subir'] as int,
      productos: productos,
      cargaId: carga.isEmpty ? null : carga.single['valor'] as String?,
    );
  }

  /// El catálogo para pedir la carga: lo activo, con sus presentaciones.
  List<ProductoParaPedir> catalogoParaPedir({String busqueda = ''}) {
    final filtro = busqueda.trim();
    return _db
        .select(
          '''
          SELECT id, nombre, sku, unidad_base FROM productos
           WHERE activo = 1
             AND (?1 = '' OR nombre LIKE ?2 OR sku LIKE ?2 OR codigo_barras = ?1)
           ORDER BY nombre
           LIMIT 300
          ''',
          [filtro, '%$filtro%'],
        )
        .map((f) {
          final id = f['id'] as String;
          final presentaciones = _presentacionesDe(id);
          return ProductoParaPedir(
            productoId: id,
            nombre: f['nombre'] as String,
            sku: f['sku'] as String,
            unidadBase: f['unidad_base'] as String,
            presentaciones: presentaciones.isEmpty
                ? [Presentacion(unidad: f['unidad_base'] as String, factor: Factor.uno)]
                : presentaciones,
          );
        })
        .toList();
  }

  List<Presentacion> _presentacionesDe(String productoId) => _db
      .select(
        'SELECT unidad_codigo, factor FROM producto_unidades '
        ' WHERE producto_id = ? ORDER BY factor',
        [productoId],
      )
      .map(
        (f) => Presentacion(
          unidad: f['unidad_codigo'] as String,
          factor: Factor.deBase(f['factor'] as num),
        ),
      )
      .toList();

  /// El último corte de ese día (por omisión, hoy).
  CorteDelVendedor? corteDelDia([String? fechaOperativa]) {
    final filas = _db.select(
      'SELECT * FROM cortes_vendedor WHERE fecha_operativa = ? '
      ' ORDER BY fecha_dispositivo DESC, rowid DESC LIMIT 1',
      [fechaOperativa ?? hoy],
    );
    if (filas.isEmpty) return null;
    final f = filas.single;
    return CorteDelVendedor(
      id: f['id'] as String,
      fechaOperativa: f['fecha_operativa'] as String,
      cargaId: f['carga_id'] as String?,
      efectivoDeclarado: _dinero(f['efectivo_declarado']),
      efectivoEsperado: _dinero(f['efectivo_esperado']),
      observaciones: f['observaciones'] as String?,
      textoTicket: f['texto_ticket'] as String? ?? '',
      fechaDispositivo: f['fecha_dispositivo'] as String,
      sincronizado: (f['sincronizado'] as int? ?? 0) == 1,
    );
  }

  /// La última solicitud para ese día (por omisión, mañana).
  SolicitudDeCarga? solicitudPara([String? fechaOperativa]) {
    final filas = _db.select(
      // La reemplazada nunca es «la» solicitud del día, aunque tenga la misma
      // hora que la que la reemplazó.
      'SELECT * FROM solicitudes_carga WHERE fecha_operativa = ? '
      " ORDER BY estado = 'reemplazada', fecha_dispositivo DESC, rowid DESC LIMIT 1",
      [fechaOperativa ?? diaSiguiente(hoy)],
    );
    return filas.isEmpty ? null : _solicitud(filas.single);
  }

  SolicitudDeCarga? solicitud(String id) {
    final filas = _db.select('SELECT * FROM solicitudes_carga WHERE id = ?', [id]);
    return filas.isEmpty ? null : _solicitud(filas.single);
  }

  SolicitudDeCarga _solicitud(Row f) {
    final id = f['id'] as String;
    final renglones = _db
        .select(
          '''
          SELECT d.*, COALESCE(d.nombre, p.nombre, d.producto_id) AS el_nombre,
                 COALESCE(p.unidad_base, 'PZA') AS unidad_base
            FROM solicitud_carga_detalle d
            LEFT JOIN productos p ON p.id = d.producto_id
           WHERE d.solicitud_id = ?
           ORDER BY el_nombre
          ''',
          [id],
        )
        .map(
          (r) => RenglonDeTicket(
            nombre: r['el_nombre'] as String,
            cantidadBase: Cantidad.deBase(r['cantidad'] as num),
            unidadBase: r['unidad_base'] as String,
            unidad: r['unidad_codigo'] as String,
            factor: Factor.deBase(r['factor'] as num),
            aceptadaBase: r['cantidad_aceptada'] == null
                ? null
                : Cantidad.deBase(r['cantidad_aceptada'] as num),
          ),
        )
        .toList();
    return SolicitudDeCarga(
      id: id,
      corteId: f['corte_id'] as String?,
      fechaOperativa: f['fecha_operativa'] as String,
      estado: f['estado'] as String,
      cargaId: f['carga_id'] as String?,
      cargaFolio: f['carga_folio'] as String?,
      motivo: f['motivo'] as String?,
      observaciones: f['observaciones'] as String?,
      resueltaEn: f['resuelta_en'] as String?,
      fechaDispositivo: f['fecha_dispositivo'] as String,
      sincronizado: (f['sincronizado'] as int? ?? 0) == 1,
      renglones: renglones,
    );
  }

  /// El ticket de una solicitud, con lo que haya contestado la oficina.
  String ticketDe(SolicitudDeCarga s) => textoDeLaCarga(
        TicketDeCarga(
          id: s.id,
          vendedor: _vendedor,
          codigoVendedor: codigoVendedor,
          paraElDia: s.fechaOperativa,
          estado: s.estado,
          renglones: s.renglones,
          cargaFolio: s.cargaFolio,
          motivo: s.motivo,
          observaciones: s.observaciones,
        ),
      );

  // -------------------------------------------------------------------------
  // Escritura
  // -------------------------------------------------------------------------

  /// Guarda el corte, lo encola y devuelve su ticket.
  ///
  /// `conteo` va de id de producto a lo contado, en unidad base. Lo que no
  /// viene vale cero, como en el corte de la oficina.
  CorteDelVendedor hacerCorte({
    required Map<String, Cantidad> conteo,
    required Dinero efectivo,
    String? observaciones,
  }) {
    if (efectivo.esNegativo) {
      throw const CierreNoValido(MotivoNoCierre.efectivoNegativo);
    }
    for (final e in conteo.entries) {
      if (e.value.esNegativa) {
        throw CierreNoValido(MotivoNoCierre.conteoNegativo, e.key);
      }
    }

    final resumen = this.resumen();
    final nombres = {for (final p in resumen.productos) p.productoId: p};
    final instante = _ahora();
    final momento = instante.toUtc().toIso8601String();
    final corteId = _nuevoUuid();
    final nota = (observaciones ?? '').trim();

    final ticket = textoDelCorte(
      TicketDeCorte(
        id: corteId,
        vendedor: _vendedor,
        codigoVendedor: codigoVendedor,
        fechaOperativa: resumen.fechaOperativa,
        momento: instante,
        ventas: resumen.ventas,
        efectivoVendido: resumen.efectivo,
        transferencias: resumen.transferencias,
        efectivoEntregado: efectivo,
        observaciones: nota.isEmpty ? null : nota,
        sobrante: [
          for (final e in conteo.entries)
            RenglonDeTicket(
              nombre: nombres[e.key]?.nombre ?? _nombreDe(e.key),
              cantidadBase: e.value,
              unidadBase: nombres[e.key]?.unidadBase ?? 'PZA',
            ),
        ]..sort((a, b) => a.nombre.compareTo(b.nombre)),
      ),
    );

    final sobre = SobreLocal(
      operacionId: _nuevoUuid(),
      secuencia: _outbox.siguienteSecuencia(),
      operaciones: [
        OperacionLocal(
          tipo: 'corte.crear',
          entidadId: corteId,
          datos: {
            'dispositivo_id': _dispositivoId,
            'fecha_operativa': resumen.fechaOperativa,
            'carga_id': resumen.cargaId,
            // Dinero como string de dos decimales: contracts §1.4.
            'efectivo_declarado': efectivo.texto,
            'observaciones': nota.isEmpty ? null : nota,
            'fecha_dispositivo': momento,
            'conteo': [
              for (final e in conteo.entries)
                {'producto_id': e.key, 'cantidad': e.value.texto},
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
          INSERT INTO cortes_vendedor (id, fecha_operativa, carga_id,
                                       efectivo_declarado, efectivo_esperado,
                                       observaciones, texto_ticket,
                                       fecha_dispositivo, sincronizado)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
          ''',
          [
            corteId,
            resumen.fechaOperativa,
            resumen.cargaId,
            efectivo.centavos / 100,
            resumen.efectivo.centavos / 100,
            nota.isEmpty ? null : nota,
            ticket,
            momento,
          ],
        );
        for (final e in conteo.entries) {
          db.execute(
            'INSERT INTO corte_vendedor_conteo (corte_id, producto_id, nombre, cantidad) '
            'VALUES (?, ?, ?, ?)',
            [corteId, e.key, nombres[e.key]?.nombre, e.value.milesimos / 1000],
          );
        }
      },
    );

    return corteDelDia(resumen.fechaOperativa)!;
  }

  /// Guarda la solicitud de carga para MAÑANA, la encola y la devuelve.
  SolicitudDeCarga pedirCarga({
    required List<RenglonPedido> renglones,
    String? corteId,
    String? observaciones,
  }) {
    final porProducto = <String, RenglonPedido>{};
    for (final r in renglones) {
      if (r.bultos < 0 || !r.factor.esValido) {
        throw CierreNoValido(MotivoNoCierre.bultosInvalidos, r.nombre);
      }
      if (r.bultos == 0) continue;
      // El mismo producto dos veces: vale el último que se escribió.
      porProducto[r.productoId] = r;
    }
    if (porProducto.isEmpty) {
      throw const CierreNoValido(MotivoNoCierre.sinRenglones);
    }

    final instante = _ahora();
    final momento = instante.toUtc().toIso8601String();
    final para = diaSiguiente(diaOperativoDe(instante));
    final solicitudId = _nuevoUuid();
    final nota = (observaciones ?? '').trim();
    final corte = corteId ?? corteDelDia(diaOperativoDe(instante))?.id;

    final sobre = SobreLocal(
      operacionId: _nuevoUuid(),
      secuencia: _outbox.siguienteSecuencia(),
      operaciones: [
        OperacionLocal(
          tipo: 'solicitud_carga.crear',
          entidadId: solicitudId,
          datos: {
            'dispositivo_id': _dispositivoId,
            'fecha_operativa': para,
            'corte_id': corte,
            'observaciones': nota.isEmpty ? null : nota,
            'fecha_dispositivo': momento,
            'detalle': [
              for (final r in porProducto.values)
                {
                  'producto_id': r.productoId,
                  'unidad_codigo': r.unidad,
                  'bultos': Cantidad.deEnteros(r.bultos).texto,
                  'cantidad': r.enUnidadBase.texto,
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
        // La anterior que nadie ha visto queda reemplazada, como en el servidor.
        db.execute(
          "UPDATE solicitudes_carga SET estado = 'reemplazada' "
          " WHERE fecha_operativa = ? AND estado = 'pendiente'",
          [para],
        );
        db.execute(
          '''
          INSERT INTO solicitudes_carga (id, corte_id, fecha_operativa, estado,
                                         observaciones, fecha_dispositivo, sincronizado)
          VALUES (?, ?, ?, 'pendiente', ?, ?, 0)
          ''',
          [solicitudId, corte, para, nota.isEmpty ? null : nota, momento],
        );
        for (final r in porProducto.values) {
          db.execute(
            '''
            INSERT INTO solicitud_carga_detalle (solicitud_id, producto_id, nombre,
                                                 unidad_codigo, factor, bultos, cantidad)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            [
              solicitudId,
              r.productoId,
              r.nombre,
              r.unidad,
              r.factor.diezmilesimos / 10000,
              r.bultos,
              r.enUnidadBase.milesimos / 1000,
            ],
          );
        }
      },
    );

    return solicitud(solicitudId)!;
  }

  String _nombreDe(String productoId) {
    final filas = _db.select('SELECT nombre FROM productos WHERE id = ?', [productoId]);
    return filas.isEmpty ? productoId : filas.single['nombre'] as String;
  }

  static Dinero _dinero(Object? valor) =>
      Dinero.deTexto((valor as num? ?? 0).toStringAsFixed(2));
}
