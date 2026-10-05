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
/// perdidas son culpa nuestra (no traía lo que pidió, se le acabó el crédito) y
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
/// EXIGE UBICACIÓN, Y ESO ES DELIBERADO
/// ─────────────────────────────────────────────────────────────────────────
/// `no_drops.lat` y `lng` son NOT NULL en el esquema local. La razón está escrita
/// ahí: **un no-drop sin GPS es indistinguible de una visita que nunca se hizo.**
/// Es el único documento del sistema que exige ubicación, porque es el único cuyo
/// valor entero depende de haber estado ahí.
///
/// Cuando el satélite no aparece, el camino no es registrarlo sin ubicación: es
/// ajustar el punto a mano (`ubicacion_origen = 'manual'`), igual que en el alta
/// de cliente. Queda marcado como ajustado y la oficina puede juzgarlo.
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

  /// Sin ubicación no se distingue de una visita que nunca se hizo.
  faltaUbicacion('falta_ubicacion'),

  sinRangoDeFolios('sin_rango_de_folios'),
  sinFolios('sin_folios');

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
  });

  final String id;
  final int folioConsecutivo;
  final String visitaId;
  final String clienteId;
  final String motivoCodigo;
  final String? nota;
  final Ubicacion ubicacion;
  final String fechaDispositivo;
  final String fechaOperativa;
  final int foliosRestantes;
}

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

    // La ubicación es obligatoria y el esquema lo impone. Se valida aquí para dar
    // un mensaje que el vendedor pueda accionar en vez de un error de NOT NULL.
    if (ubicacion == null) {
      throw const NoDropRechazado(
        MotivoNoNoDrop.faltaUbicacion,
        'sin ubicación no se distingue de una visita que nunca se hizo',
      );
    }

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
            ...ubicacion.aPayload(),
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
                                fecha_dispositivo, fecha_operativa, sincronizado)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
          ''',
          [
            noDropId,
            consecutivo,
            visita,
            clienteId,
            motivoCodigo,
            limpia.isEmpty ? null : limpia,
            ubicacion.lat,
            ubicacion.lng,
            ubicacion.precisionMetros,
            ubicacion.origen.codigo,
            momento,
            fechaOperativa,
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
    );
  }
}
