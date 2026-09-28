/// Orquesta la sincronización: empuja la cola y trae los deltas.
///
/// ─────────────────────────────────────────────────────────────────────────
/// QUÉ SE HACE CON CADA FALLO
/// ─────────────────────────────────────────────────────────────────────────
/// Esta tabla ES el diseño. Equivocarse en un renglón se paga con una cola
/// atorada o con datos perdidos.
///
/// | Situación                       | Decisión                               |
/// |---------------------------------|----------------------------------------|
/// | 200 · aceptada                  | confirmar y sacar de la cola            |
/// | 200 · duplicada                 | igual: ya está del otro lado            |
/// | 200 · rechazada                 | cuarentena local; no reintentar         |
/// | Error de red (no llegó)         | sigue pendiente, intentos++, backoff    |
/// | 401 / 403                       | detener; NO tocar la cola; pedir login  |
/// | 422 lote inválido               | cuarentena del lote completo            |
/// | 429 / 5xx                       | sigue pendiente, backoff                |
///
/// Las dos filas que más importan:
///
/// **Error de red** no es lo mismo que rechazo. Cuando la petición no vuelve,
/// no se sabe si el servidor la aplicó. Por eso el sobre se conserva y se
/// reintenta — y por eso el servidor tiene que ser idempotente. Si se
/// descartara "por si acaso", se perderían ventas ya cobradas.
///
/// **422** es el único caso donde se descarta sin haberlo aplicado: el lote está
/// mal formado, reenviar lo mismo va a fallar igual, y reintentar para siempre
/// dejaría al vendedor sin poder vender. Se guarda en cuarentena local y se
/// sigue.
library;

import 'dart:math' as math;

import 'aplicador_deltas.dart';
import 'outbox.dart';
import 'sync_cliente.dart';
import 'transporte.dart';

/// Por qué terminó la sincronización.
enum FinDeSync {
  /// No quedó nada pendiente y los deltas están al día.
  completa,

  /// Se empujó lo que se pudo; falta más cola o más deltas.
  parcial,

  /// Sin señal. Nada se perdió: la cola sigue intacta.
  sinRed,

  /// El token no sirve. Hay que volver a entrar.
  sesionInvalida,

  /// El servidor está con problemas. Se reintenta más tarde.
  servidorCaido,
}

class ResultadoSincronizacion {
  const ResultadoSincronizacion({
    required this.fin,
    this.sobresConfirmados = 0,
    this.sobresEnCuarentena = 0,
    this.deltasAplicados = 0,
    this.deltasDesconocidos = 0,
    this.cursor = 0,
    this.detalle,
  });

  final FinDeSync fin;
  final int sobresConfirmados;
  final int sobresEnCuarentena;
  final int deltasAplicados;
  final int deltasDesconocidos;
  final int cursor;
  final String? detalle;

  bool get huboActividad =>
      sobresConfirmados > 0 || sobresEnCuarentena > 0 || deltasAplicados > 0;
}

/// Espera antes del siguiente intento, creciente y con techo.
///
/// Sin techo, tras una noche sin señal el siguiente intento caería a horas de
/// distancia y el vendedor saldría a ruta con la cola del día anterior.
Duration esperaPorIntentos(int intentos) {
  const base = Duration(seconds: 15);
  const techo = Duration(minutes: 10);
  final factor = math.min(1 << math.min(intentos, 10), 64);
  final espera = base * factor;
  return espera > techo ? techo : espera;
}

class Sincronizador {
  Sincronizador({
    required Outbox outbox,
    required ClienteSync cliente,
    required AplicadorDeltas aplicador,
    required String Function() ahora,
    this.maxSobresPorLote = 50,
    this.maxTandas = 10,
  })  : _outbox = outbox,
        _cliente = cliente,
        _aplicador = aplicador,
        _ahora = ahora;

  final Outbox _outbox;
  final ClienteSync _cliente;
  final AplicadorDeltas _aplicador;
  final String Function() _ahora;

  final int maxSobresPorLote;

  /// Tope de tandas por corrida. Una cola de miles de sobres se manda en varias
  /// pasadas: dejar corriendo la sincronización indefinidamente con el teléfono
  /// en la mano del vendedor es peor que terminar y volver a intentar.
  final int maxTandas;

  Future<ResultadoSincronizacion> sincronizar({
    required int cursorActual,
    String Function()? nuevoLoteId,
    String? appVersion,
  }) async {
    var confirmados = 0;
    var enCuarentena = 0;
    var tandas = 0;

    // ---- PUSH: primero sale lo del vendedor -----------------------------
    // El orden importa. Si se trajeran los deltas primero, una actualización de
    // precios podría pisar el espejo mientras la venta que se hizo con el
    // precio viejo todavía está en la cola. Primero se entrega lo ocurrido.
    while (tandas < maxTandas) {
      final lote = _outbox.siguienteLote(limite: maxSobresPorLote);
      if (lote.isEmpty) break;
      tandas++;

      final RespuestaPush respuesta;
      try {
        respuesta = await _cliente.push(
          loteId: nuevoLoteId?.call() ?? _loteIdPorDefecto(lote),
          sobres: lote.map((s) => s.payload).toList(),
          appVersion: appVersion,
        );
      } on ErrorDeRed catch (e) {
        // No se sabe si llegó. Se conserva todo y se reintenta.
        _marcarParaReintento(lote, e.mensaje);
        return ResultadoSincronizacion(
          fin: FinDeSync.sinRed,
          sobresConfirmados: confirmados,
          sobresEnCuarentena: enCuarentena,
          cursor: cursorActual,
          detalle: e.mensaje,
        );
      } on SesionInvalida catch (e) {
        return ResultadoSincronizacion(
          fin: FinDeSync.sesionInvalida,
          sobresConfirmados: confirmados,
          sobresEnCuarentena: enCuarentena,
          cursor: cursorActual,
          detalle: e.toString(),
        );
      } on LoteRechazado catch (e) {
        // Reenviar lo mismo fallaría igual: a cuarentena y seguimos.
        for (final sobre in lote) {
          _outbox.aCuarentena(sobre.operacionId, 'lote_invalido: ${e.detalle}');
          enCuarentena++;
        }
        continue;
      } on ServidorConProblemas catch (e) {
        _marcarParaReintento(lote, e.toString());
        return ResultadoSincronizacion(
          fin: FinDeSync.servidorCaido,
          sobresConfirmados: confirmados,
          sobresEnCuarentena: enCuarentena,
          cursor: cursorActual,
          detalle: e.toString(),
        );
      }

      final confirmables = <String>[];
      for (final resultado in respuesta.resultados) {
        if (resultado.quedoDelOtroLado) {
          confirmables.add(resultado.operacionId);
        } else {
          _outbox.aCuarentena(
            resultado.operacionId,
            '${resultado.errorCodigo}: ${resultado.errorMensaje}',
          );
          enCuarentena++;
        }
      }
      if (confirmables.isNotEmpty) {
        _outbox.confirmar(confirmables, confirmadoEn: _ahora());
        confirmados += confirmables.length;
      }
    }

    // ---- PULL: después entra lo del servidor ----------------------------
    var cursor = cursorActual;
    var aplicados = 0;
    var desconocidos = 0;
    var vueltas = 0;

    while (vueltas < maxTandas) {
      final RespuestaPull delta;
      try {
        delta = await _cliente.pull(cursor: cursor);
      } on ErrorDeRed catch (e) {
        return ResultadoSincronizacion(
          fin: FinDeSync.sinRed,
          sobresConfirmados: confirmados,
          sobresEnCuarentena: enCuarentena,
          deltasAplicados: aplicados,
          deltasDesconocidos: desconocidos,
          cursor: cursor,
          detalle: e.mensaje,
        );
      } on SesionInvalida catch (e) {
        return ResultadoSincronizacion(
          fin: FinDeSync.sesionInvalida,
          sobresConfirmados: confirmados,
          deltasAplicados: aplicados,
          cursor: cursor,
          detalle: e.toString(),
        );
      } on ServidorConProblemas catch (e) {
        return ResultadoSincronizacion(
          fin: FinDeSync.servidorCaido,
          sobresConfirmados: confirmados,
          deltasAplicados: aplicados,
          cursor: cursor,
          detalle: e.toString(),
        );
      }

      vueltas++;
      if (delta.cambios.isNotEmpty) {
        final r = _aplicador.aplicar(delta.cambios, recibidoEn: _ahora());
        aplicados += r.aplicados;
        desconocidos += r.desconocidos;
        // El cursor avanza SOLO después de aplicar. Si la app muere entre el
        // pull y la escritura, la próxima corrida vuelve a traer el mismo
        // tramo: aplicar dos veces es inofensivo (todo es upsert), perderse un
        // tramo no lo es.
        cursor = delta.cursor;
      }
      if (!delta.hayMas) break;
    }

    final quedaCola = _outbox.resumen().pendientes > 0;
    return ResultadoSincronizacion(
      fin: quedaCola ? FinDeSync.parcial : FinDeSync.completa,
      sobresConfirmados: confirmados,
      sobresEnCuarentena: enCuarentena,
      deltasAplicados: aplicados,
      deltasDesconocidos: desconocidos,
      cursor: cursor,
    );
  }

  void _marcarParaReintento(List<SobreEnCola> lote, String error) {
    for (final sobre in lote) {
      _outbox.reintentarDespues(
        sobre.operacionId,
        error,
        proximoIntento: _ahora(),
      );
    }
  }

  /// Un id de lote derivado de su contenido: si el teléfono reintenta el mismo
  /// tramo tras un corte, el servidor lo reconoce como el mismo lote.
  String _loteIdPorDefecto(List<SobreEnCola> lote) =>
      'lote-${lote.first.operacionId}';
}
