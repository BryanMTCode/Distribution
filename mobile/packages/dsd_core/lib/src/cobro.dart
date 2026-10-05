/// El cobro: dinero que el vendedor recibe en la calle.
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ UN COBRO ES MÁS DELICADO QUE UNA VENTA
/// ─────────────────────────────────────────────────────────────────────────
/// Una venta entrega mercancía que se puede contar al final del día. Un cobro
/// recibe **efectivo**, y el efectivo no se cuenta: se cuadra. Si un cobro no
/// queda registrado, el dinero existe en la bolsa del vendedor y no en el
/// sistema — y en la liquidación aparece como un sobrante que nadie puede
/// explicar, o peor, como un faltante del cliente que sí pagó.
///
/// El cliente se queda con un papel que dice que pagó. Ese papel es la única
/// prueba que tiene hasta que el cobro sincronice.
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL TELÉFONO NO DECIDE A QUÉ FACTURA SE APLICA
/// ─────────────────────────────────────────────────────────────────────────
/// El dispositivo registra **un abono por un importe**, y nada más. La
/// aplicación a facturas la resuelve el servidor en orden FIFO (ver
/// `migración 0005` y el manejador `cobro.crear`).
///
/// La razón es que el teléfono no conoce la cartera completa: trae un saldo en
/// caché que puede tener horas (§0.3). Si decidiera la aplicación, dos
/// dispositivos cobrando al mismo cliente el mismo día aplicarían los dos
/// abonos a la misma factura, y el servidor tendría que deshacer una decisión
/// que ya está impresa en un papel.
///
/// Lo que sí se guarda es `saldo_cache_disp`: lo que el teléfono **creía** que
/// debía el cliente al cobrar. Es forense, no autoridad — sirve para explicar
/// después por qué el vendedor cobró lo que cobró.
///
/// ─────────────────────────────────────────────────────────────────────────
/// COBRAR MÁS DE LO QUE DEBE NO ES UN ERROR
/// ─────────────────────────────────────────────────────────────────────────
/// El cliente puede pagar de más: liquida y deja un anticipo, o el saldo del
/// teléfono estaba viejo y ya había pagado parte. **El dinero ya cambió de
/// manos.** Rechazarlo haría que el vendedor se guardara efectivo sin
/// documento, que es exactamente el problema que este módulo existe para
/// evitar. El servidor lo registra como saldo a favor.
///
/// Es el §0.1 aplicado al dinero: el mundo físico ya ocurrió.
library;

import 'dart:typed_data';

import 'package:sqlite3/sqlite3.dart';

import 'dia_operativo.dart';
import 'dinero.dart';
import 'folios.dart';
import 'outbox.dart';
import 'sobre.dart';
import 'ubicacion.dart';

/// Cómo pagó el cliente.
///
/// Catálogo cerrado a propósito: el arqueo de la liquidación suma **solo el
/// efectivo**, porque una transferencia no viene en la bolsa. Con texto libre,
/// "efectivo " con un espacio quedaría fuera de la suma y el cuadre fallaría por
/// un dato que se ve bien.
enum FormaDePago {
  efectivo('efectivo', 'Efectivo'),
  transferencia('transferencia', 'Transferencia'),
  cheque('cheque', 'Cheque');

  const FormaDePago(this.codigo, this.etiqueta);

  final String codigo;
  final String etiqueta;

  /// Solo el efectivo entra al arqueo del cierre del día.
  bool get entraAlArqueo => this == FormaDePago.efectivo;

  static FormaDePago desdeCodigo(String codigo) => values.firstWhere(
        (f) => f.codigo == codigo,
        orElse: () => FormaDePago.efectivo,
      );
}

/// Por qué no se pudo registrar el cobro.
///
/// Fíjate en lo que **no** está: no hay `importeExcedeSaldo`. Cobrar de más es
/// válido y produce saldo a favor.
enum MotivoNoCobro {
  /// Importe en cero o negativo. No es un cobro.
  importeInvalido('importe_invalido'),

  /// El servidor no ha asignado folios de cobro a este equipo.
  sinRangoDeFolios('sin_rango_de_folios'),

  /// Se acabó el rango. Hay que sincronizar para pedir otro.
  sinFolios('sin_folios'),

  /// Una referencia es obligatoria cuando no es efectivo: sin ella nadie puede
  /// conciliar la transferencia o el cheque con el banco.
  faltaReferencia('falta_referencia');

  const MotivoNoCobro(this.codigo);

  final String codigo;
}

class CobroRechazado implements Exception {
  const CobroRechazado(this.motivo, [this.detalle]);

  final MotivoNoCobro motivo;
  final String? detalle;

  @override
  String toString() =>
      'cobro rechazado (${motivo.codigo})${detalle == null ? '' : ': $detalle'}';
}

/// Un cobro ya registrado. Es lo que se imprime y lo que se le muestra.
class CobroGuardado {
  const CobroGuardado({
    required this.id,
    required this.folioConsecutivo,
    required this.folioLocal,
    required this.visitaId,
    required this.clienteId,
    required this.importe,
    required this.formaDePago,
    required this.fechaDispositivo,
    required this.fechaOperativa,
    required this.foliosRestantes,
    this.referencia,
    this.saldoAntes,
    this.ubicacion,
  });

  final String id;
  final int folioConsecutivo;

  /// El que va en el papel: 'VEND01-000124'.
  final String folioLocal;
  final String visitaId;
  final String clienteId;
  final Dinero importe;
  final FormaDePago formaDePago;
  final String? referencia;

  /// Lo que el teléfono creía que debía el cliente. Forense, no autoridad: NO se
  /// imprime, porque puede tener horas (§0.3) y un saldo viejo en un papel que el
  /// cliente conserva es una disputa esperando.
  final Dinero? saldoAntes;

  final String fechaDispositivo;
  final String fechaOperativa;
  final Ubicacion? ubicacion;
  final int foliosRestantes;
}

/// Quién cobra y con qué equipo.
class IdentidadDeCobro {
  const IdentidadDeCobro({
    required this.vendedorId,
    required this.codigoVendedor,
    required this.dispositivoId,
    this.rutaId,
  });

  final String vendedorId;

  /// El prefijo del folio impreso.
  final String codigoVendedor;
  final String dispositivoId;
  final String? rutaId;
}

/// Registra el cobro: folio, fila, sobre y marca de folio, en una transacción.
///
/// Las cuatro cosas o ninguna, por lo mismo que la venta: si el teléfono se
/// apaga a la mitad, cada combinación parcial es un problema distinto que no se
/// detecta hasta el cierre del día.
///
///   · Cobro sin sobre        → el cliente tiene su papel y la oficina no se enteró.
///   · Sobre sin cobro        → el saldo baja en la oficina y el vendedor no sabe por qué.
///   · Marca de folio sin cobro → hueco en la numeración impresa.
class RegistroDeCobro {
  RegistroDeCobro({
    required Database db,
    required IdentidadDeCobro identidad,
    required Outbox outbox,
    required RepoFolios folios,
    required String Function() nuevoUuid,
    required DateTime Function() ahora,
  })  : _db = db,
        _identidad = identidad,
        _outbox = outbox,
        _folios = folios,
        _nuevoUuid = nuevoUuid,
        _ahora = ahora;

  final Database _db;
  final IdentidadDeCobro _identidad;
  final Outbox _outbox;
  final RepoFolios _folios;
  final String Function() _nuevoUuid;
  final DateTime Function() _ahora;

  CobroGuardado registrar({
    required String clienteId,
    required Dinero importe,
    FormaDePago formaDePago = FormaDePago.efectivo,
    String? referencia,
    Dinero? saldoAntes,
    String? visitaId,
    Ubicacion? ubicacion,
  }) {
    if (importe.centavos <= 0) {
      throw const CobroRechazado(MotivoNoCobro.importeInvalido);
    }
    // Sin referencia, una transferencia es imposible de conciliar con el banco: la
    // oficina tendría un abono registrado y ninguna forma de encontrarlo en el
    // estado de cuenta. El efectivo no la necesita porque el papel ES la prueba.
    if (!formaDePago.entraAlArqueo && (referencia ?? '').trim().isEmpty) {
      throw CobroRechazado(
        MotivoNoCobro.faltaReferencia,
        'un cobro por ${formaDePago.etiqueta.toLowerCase()} necesita referencia',
      );
    }

    // Se lee de la base en cada cobro, nunca de un campo en memoria: un objeto de
    // rango de larga vida se adelantaría en un rollback y dejaría huecos en la
    // numeración impresa que nadie podría explicar en una auditoría.
    final rango = _folios.leer('cobro');
    if (rango == null) {
      throw const CobroRechazado(MotivoNoCobro.sinRangoDeFolios);
    }
    if (rango.agotado) {
      throw CobroRechazado(
        MotivoNoCobro.sinFolios,
        'el rango terminaba en ${rango.hasta}',
      );
    }

    // Una sola lectura del reloj: dos llamadas podrían caer en segundos
    // distintos y estampar un documento con hora de un día y fecha de otro.
    final instante = _ahora();
    final momento = instante.toUtc().toIso8601String();
    final cobroId = _nuevoUuid();
    final visita = visitaId ?? _nuevoUuid();
    // La fecha operativa es la del día de ruta: un cobro a las 00:30 pertenece al
    // día que se abrió, no al que dice el calendario.
    // El día LOCAL, no el de `momento` —que está en UTC—: en UTC−6 ese
    // atajo mandaba las ventas de la tarde al día siguiente. Ver
    // `diaOperativoDe`.
    final fechaOperativa = diaOperativoDe(instante);

    // El folio se toma ANTES de armar el sobre, para que el payload viaje
    // completo. Si la transacción falla, la marca persistida no avanza y el
    // siguiente intento vuelve a este mismo número.
    final consecutivo = rango.tomar();
    final folioLocal =
        RangoFolios.formatear(_identidad.codigoVendedor, consecutivo);
    final limpia = (referencia ?? '').trim();

    final sobre = SobreLocal(
      operacionId: _nuevoUuid(),
      secuencia: _outbox.siguienteSecuencia(),
      visitaId: visita,
      operaciones: [
        OperacionLocal(
          tipo: 'cobro.crear',
          entidadId: cobroId,
          datos: {
            'folio_consecutivo': consecutivo,
            'folio_local': folioLocal,
            'cliente_id': clienteId,
            'vendedor_id': _identidad.vendedorId,
            'dispositivo_id': _identidad.dispositivoId,
            'visita_id': visita,
            // Dinero como string de dos decimales: contracts/README.md §1.4.
            'importe': importe.texto,
            'forma_pago': formaDePago.codigo,
            'referencia': limpia.isEmpty ? null : limpia,
            // Lo que el teléfono creía. El servidor NO lo usa para decidir nada:
            // le sirve para explicar después por qué se cobró lo que se cobró.
            'saldo_cache_disp': saldoAntes?.texto,
            'fecha_dispositivo': momento,
            'fecha_operativa': fechaOperativa,
            if (ubicacion != null) ...ubicacion.aPayload(),
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
          INSERT INTO cobros (id, folio_consecutivo, folio_local, visita_id,
                              cliente_id, importe, forma_pago, referencia,
                              saldo_cache_disp, lat, lng, estado,
                              fecha_dispositivo, fecha_operativa,
                              impreso, sincronizado, creado_en)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmado', ?, ?, 0, 0, ?)
          ''',
          [
            cobroId,
            consecutivo,
            folioLocal,
            visita,
            clienteId,
            _aReal(importe),
            formaDePago.codigo,
            limpia.isEmpty ? null : limpia,
            saldoAntes == null ? null : _aReal(saldoAntes),
            ubicacion?.lat,
            ubicacion?.lng,
            momento,
            fechaOperativa,
            momento,
          ],
        );

        // El `saldo_cache` del cliente NO se toca.
        //
        // Es zona ESPEJO: la escribe el delta de cartera. Si este cobro la
        // bajara, el siguiente pull la volvería a subir —porque el servidor
        // todavía no tiene el abono— y el vendedor vería la deuda reaparecer a
        // media ruta.
        //
        // El saldo que el vendedor ve se COMPONE al leer, restando los cobros
        // encolados (ver `repo_clientes.deLaRuta`). Así el número baja de
        // inmediato y ningún delta lo contradice.

        // La marca del folio, en la misma transacción. Es lo que hace que un
        // rollback no deje hueco en la numeración impresa.
        db.execute(
          'UPDATE folios_rangos SET consumido_hasta = ? WHERE tipo = ?',
          [consecutivo, 'cobro'],
        );
      },
    );

    return CobroGuardado(
      id: cobroId,
      folioConsecutivo: consecutivo,
      folioLocal: folioLocal,
      visitaId: visita,
      clienteId: clienteId,
      importe: importe,
      formaDePago: formaDePago,
      referencia: limpia.isEmpty ? null : limpia,
      saldoAntes: saldoAntes,
      fechaDispositivo: momento,
      fechaOperativa: fechaOperativa,
      ubicacion: ubicacion,
      foliosRestantes: rango.restantes,
    );
  }

  /// Marca el cobro como impreso y guarda sus bytes.
  ///
  /// La reimpresión reusa los bytes guardados en vez de recalcularlos, igual que
  /// la venta: el papel tiene que decir exactamente lo mismo que el que recibió
  /// el cliente.
  void marcarImpreso(String cobroId, {required List<int> ticket}) {
    _db.execute('BEGIN IMMEDIATE');
    try {
      final previa = _db.select(
        'SELECT impreso FROM cobros WHERE id = ?',
        [cobroId],
      );
      if (previa.isEmpty) {
        throw ArgumentError('no existe el cobro $cobroId');
      }
      if ((previa.single['impreso'] as int) == 0) {
        _db.execute(
          'UPDATE cobros SET impreso = 1, ticket_escpos = ?2 WHERE id = ?1',
          [cobroId, Uint8List.fromList(ticket)],
        );
      }
      _db.execute('COMMIT');
    } catch (_) {
      _db.execute('ROLLBACK');
      rethrow;
    }
  }

  /// Los bytes del ticket original, para reimprimir sin recalcular.
  List<int>? ticketGuardado(String cobroId) {
    final filas = _db.select(
      'SELECT ticket_escpos FROM cobros WHERE id = ?',
      [cobroId],
    );
    if (filas.isEmpty) return null;
    final bytes = filas.single['ticket_escpos'];
    return bytes is Uint8List ? bytes.toList() : null;
  }

  /// SQLite guarda dinero como REAL. La conversión cruza el límite del dominio
  /// una sola vez y aquí.
  static num _aReal(Dinero d) => d.centavos / 100;
}
