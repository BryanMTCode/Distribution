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
import 'ordenes.dart';
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

  /// Hay orden de borrado y la cola ya quedó VACÍA: toca borrar.
  ///
  /// El sincronizador no borra nada por su cuenta — no conoce el archivo de la
  /// base ni el Keystore— así que devuelve esto y la app ejecuta el borrado y
  /// después confirma. Separarlo así tiene una ventaja que importa: el borrado
  /// se puede probar sin red y la decisión de borrar se puede probar sin tocar
  /// el disco.
  borradoListo,

  /// Hay orden de borrado y TODAVÍA queda cola por entregar.
  ///
  /// No se borra nada. El equipo queda bloqueado mostrando cuántas operaciones
  /// le faltan por subir: un equipo bloqueado con datos dentro es recuperable,
  /// uno borrado no.
  borradoPendiente,
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
    this.ordenes,
  });

  final FinDeSync fin;
  final int sobresConfirmados;
  final int sobresEnCuarentena;
  final int deltasAplicados;
  final int deltasDesconocidos;
  final int cursor;
  final String? detalle;

  /// Lo que el servidor ordenó al empezar la corrida. `null` si no se pudo
  /// preguntar — sin red, o contra un servidor más viejo que no conoce el
  /// endpoint. Que sea nulo NO detiene la sincronización: ver `sincronizar`.
  final OrdenesDelServidor? ordenes;

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

    // ---- ÓRDENES: qué dice el servidor del equipo -----------------------
    //
    // Va primero porque de aquí sale si hay que borrarse, y porque un equipo
    // suspendido no debe intentar el pull (el servidor se lo niega con 401, y
    // ese 401 se vería como sesión vencida).
    //
    // Y NO es una dependencia dura de la sincronización: si la consulta falla
    // por cualquier cosa que no sea un 401 —un 404 contra un servidor más
    // viejo, un 500— se sigue adelante sin órdenes. Convertir un endpoint
    // nuevo en requisito para entregar ventas sería cambiar una función de
    // administración por la operación del día.
    OrdenesDelServidor? ordenes;
    try {
      ordenes = await _cliente.ordenes();
    } on SesionInvalida catch (e) {
      return ResultadoSincronizacion(
        fin: FinDeSync.sesionInvalida,
        cursor: cursorActual,
        detalle: e.toString(),
        // Las órdenes viajan aunque la corrida termine mal: el equipo con
        // orden de borrado tiene que quedar bloqueado también cuando se
        // cayó la red a media entrega, no solo cuando logró vaciarse.
        ordenes: ordenes,
      );
    } on ErrorDeRed catch (e) {
      // Sin red tampoco se va a poder pushear ni pullear. Se corta aquí para
      // no dejar diez reintentos de red encadenados con el teléfono en la mano.
      return ResultadoSincronizacion(
        fin: FinDeSync.sinRed,
        cursor: cursorActual,
        detalle: e.mensaje,
        // Las órdenes viajan aunque la corrida termine mal: el equipo con
        // orden de borrado tiene que quedar bloqueado también cuando se
        // cayó la red a media entrega, no solo cuando logró vaciarse.
        ordenes: ordenes,
      );
    } catch (_) {
      // Cualquier otra cosa: se sigue sin órdenes.
      //
      // `catch (_)` y no `on Exception`, y es deliberado — es uno de los dos o
      // tres lugares del sistema donde tragarse TODO es lo correcto. La versión
      // con `on Exception` dejaba pasar los `Error`, y un JSON con otra forma
      // —la página de error de un proxy, el HTML de un portal cautivo, un
      // servidor más viejo que contesta otra cosa— produce un `TypeError`, que
      // es un `Error` y no una `Exception`.
      //
      // El resultado era que una respuesta inesperada en esta consulta rompía
      // la sincronización COMPLETA: el vendedor no podía entregar sus ventas
      // porque una consulta administrativa devolvió algo raro. Lo encontró una
      // prueba de la app que tenía un transporte falso contestando la forma del
      // pull a cualquier GET.
      ordenes = null;
    }

    // ---- PUSH: primero sale lo del vendedor -----------------------------
    // El orden importa. Si se trajeran los deltas primero, una actualización de
    // precios podría pisar el espejo mientras la venta que se hizo con el
    // precio viejo todavía está en la cola. Primero se entrega lo ocurrido.
    while (tandas < maxTandas) {
      final lote = _outbox.siguienteLote(limite: maxSobresPorLote);
      if (lote.isEmpty) break;
      tandas++;

      // Cuántos sobres quedarán después de esta tanda.
      //
      // Se calcula ANTES de mandarla y se resta el tamaño de la tanda, en vez de
      // medirlo después: el servidor necesita el número que corresponde al estado
      // en que lo deja ESTE push, y medirlo después obligaría a un segundo viaje
      // solo para decirlo. Si la tanda acaba en cuarentena el número sigue
      // valiendo, porque esos sobres también salen de 'pendiente'.
      //
      // Es un dato con la honestidad de §0.3: dice lo que el teléfono sabía en ese
      // momento. Una venta levantada un segundo después ya no está contada, y por
      // eso el servidor guarda la HORA junto al número.
      final restantes = _outbox.resumen().pendientes - lote.length;

      final RespuestaPush respuesta;
      try {
        respuesta = await _cliente.push(
          loteId: nuevoLoteId?.call() ?? _loteIdPorDefecto(lote),
          sobres: lote.map((s) => s.payload).toList(),
          appVersion: appVersion,
          colaPendiente: restantes < 0 ? 0 : restantes,
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
          // Las órdenes viajan aunque la corrida termine mal: el equipo con
          // orden de borrado tiene que quedar bloqueado también cuando se
          // cayó la red a media entrega, no solo cuando logró vaciarse.
          ordenes: ordenes,
        );
      } on SesionInvalida catch (e) {
        return ResultadoSincronizacion(
          fin: FinDeSync.sesionInvalida,
          sobresConfirmados: confirmados,
          sobresEnCuarentena: enCuarentena,
          cursor: cursorActual,
          detalle: e.toString(),
          // Las órdenes viajan aunque la corrida termine mal: el equipo con
          // orden de borrado tiene que quedar bloqueado también cuando se
          // cayó la red a media entrega, no solo cuando logró vaciarse.
          ordenes: ordenes,
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
          // Las órdenes viajan aunque la corrida termine mal: el equipo con
          // orden de borrado tiene que quedar bloqueado también cuando se
          // cayó la red a media entrega, no solo cuando logró vaciarse.
          ordenes: ordenes,
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

    // ---- BORRADO: solo cuando ya no queda nada que entregar -------------
    //
    // Es la regla que gobierna el borrado remoto: NUNCA SE BORRA LO QUE NO SE
    // HA ENTREGADO. Se comprueba DESPUÉS del push —para darle la oportunidad de
    // vaciarse en esta misma corrida— y ANTES del pull, porque un equipo que se
    // va a borrar no tiene por qué bajar nada más.
    if (ordenes != null && ordenes.borrar) {
      final pendientes = _outbox.resumen().pendientes;
      return ResultadoSincronizacion(
        fin: pendientes == 0
            ? FinDeSync.borradoListo
            : FinDeSync.borradoPendiente,
        sobresConfirmados: confirmados,
        sobresEnCuarentena: enCuarentena,
        cursor: cursorActual,
        ordenes: ordenes,
        detalle: ordenes.borradoMotivo,
      );
    }

    // Un equipo suspendido entrega y no recibe. Intentar el pull le daría un
    // 401 que el sincronizador traduciría a «sesión vencida», y mandaría al
    // vendedor a teclear su PIN para arreglar algo que no se arregla así.
    if (ordenes != null && ordenes.soloEntrega) {
      return ResultadoSincronizacion(
        fin: _outbox.resumen().pendientes > 0
            ? FinDeSync.parcial
            : FinDeSync.completa,
        sobresConfirmados: confirmados,
        sobresEnCuarentena: enCuarentena,
        cursor: cursorActual,
        ordenes: ordenes,
      );
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
          // Las órdenes viajan aunque la corrida termine mal: el equipo con
          // orden de borrado tiene que quedar bloqueado también cuando se
          // cayó la red a media entrega, no solo cuando logró vaciarse.
          ordenes: ordenes,
        );
      } on SesionInvalida catch (e) {
        return ResultadoSincronizacion(
          fin: FinDeSync.sesionInvalida,
          sobresConfirmados: confirmados,
          deltasAplicados: aplicados,
          cursor: cursor,
          detalle: e.toString(),
          // Las órdenes viajan aunque la corrida termine mal: el equipo con
          // orden de borrado tiene que quedar bloqueado también cuando se
          // cayó la red a media entrega, no solo cuando logró vaciarse.
          ordenes: ordenes,
        );
      } on ServidorConProblemas catch (e) {
        return ResultadoSincronizacion(
          fin: FinDeSync.servidorCaido,
          sobresConfirmados: confirmados,
          deltasAplicados: aplicados,
          cursor: cursor,
          detalle: e.toString(),
          // Las órdenes viajan aunque la corrida termine mal: el equipo con
          // orden de borrado tiene que quedar bloqueado también cuando se
          // cayó la red a media entrega, no solo cuando logró vaciarse.
          ordenes: ordenes,
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
      // Las órdenes viajan incluso cuando no hay nada que hacer con ellas: de
      // ahí sale el aviso de «tu acceso vence en 2 días», que no es una orden
      // pero sí algo que el vendedor tiene que ver.
      ordenes: ordenes,
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
