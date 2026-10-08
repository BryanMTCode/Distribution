/// Trae el tablero del servidor y guarda lo último que se pudo bajar.
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ HAY COPIA LOCAL DE ALGO QUE NECESITA RED
/// ─────────────────────────────────────────────────────────────────────────
/// El tablero no puede funcionar sin red: su razón de existir es ver lo que
/// están haciendo los otros. Pero hay dos formas de fallar cuando no hay señal,
/// y una es mucho peor que la otra:
///
///   · pantalla vacía con "sin conexión"  → no sirve para nada;
///   · las cifras de hace una hora, CON SU ETIQUETA → sirven para decidir casi
///     todo lo que se decide con un tablero.
///
/// La segunda exige disciplina: la antigüedad tiene que estar a la vista y ser
/// imposible de confundir con una cifra fresca. Por eso `TableroLocal` lleva
/// siempre `recibidoEn` y la pantalla lo pinta, y por eso la copia se guarda
/// *con* la hora del teléfono además de la hora de cálculo del servidor.
///
/// Las dos horas son distintas y las dos importan: el servidor calculó a las
/// 10:05, el teléfono lo bajó a las 10:40, así que la cifra arrastra 35 minutos
/// de camino **más** los que tuviera al calcularse.
library;

import 'dart:convert';

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';

/// Un tablero con la hora en que este teléfono lo recibió.
class TableroLocal {
  const TableroLocal({
    required this.tablero,
    required this.recibidoEn,
    required this.deLaCopia,
  });

  final Tablero tablero;

  /// Hora del TELÉFONO al recibirlo. No es `tablero.frescura.calculadoEn`.
  final DateTime recibidoEn;

  /// Si salió de la copia local porque no se pudo preguntar al servidor.
  final bool deLaCopia;
}

class MapaLocal {
  const MapaLocal({
    required this.mapa,
    required this.recibidoEn,
    required this.deLaCopia,
  });

  final MapaDelDia mapa;
  final DateTime recibidoEn;
  final bool deLaCopia;
}

/// No hubo red y tampoco hay copia guardada.
///
/// Es el único estado en que la pantalla no puede mostrar cifras, y se
/// distingue del resto a propósito: "nunca has podido bajar el tablero" pide
/// buscar señal, y "estas cifras son de hace una hora" no pide nada.
class SinTableroNiCopia implements Exception {
  const SinTableroNiCopia(this.motivo);

  final String motivo;

  @override
  String toString() => 'SinTableroNiCopia: $motivo';
}

class RepoTablero {
  RepoTablero(this._db, this._cliente, {DateTime Function()? reloj})
      : _reloj = reloj ?? DateTime.now;

  final Database _db;
  final ClienteTablero _cliente;
  final DateTime Function() _reloj;

  /// Pide el tablero. Si no hay red, devuelve la copia marcada como tal.
  ///
  /// `ErrorDeRed` es lo único que cae a la copia. Un 403 (sin permiso) o un 401
  /// (sesión vencida) se propagan: mostrar cifras viejas a alguien a quien le
  /// quitaron el permiso sería exactamente lo contrario de lo que el permiso
  /// significa.
  Future<TableroLocal> ver({DateTime? fecha}) async {
    final clave = _clave('tablero', fecha);
    try {
      final tablero = await _cliente.ver(fecha: fecha);
      final ahora = _reloj();
      _guardar(clave, tablero, ahora);
      return TableroLocal(
        tablero: tablero,
        recibidoEn: ahora,
        deLaCopia: false,
      );
    } on ErrorDeRed catch (e) {
      final copia = _leer(clave);
      if (copia == null) throw SinTableroNiCopia(e.mensaje);
      return TableroLocal(
        tablero: Tablero.deJson(copia.$1),
        recibidoEn: copia.$2,
        deLaCopia: true,
      );
    }
  }

  Future<MapaLocal> mapa({DateTime? fecha}) async {
    final clave = _clave('mapa', fecha);
    try {
      final mapa = await _cliente.mapa(fecha: fecha);
      final ahora = _reloj();
      _db.execute(
        'INSERT INTO tablero_cache (clave, cuerpo, recibido_en) '
        'VALUES (?, ?, ?) '
        'ON CONFLICT(clave) DO UPDATE SET cuerpo = excluded.cuerpo, '
        '                                recibido_en = excluded.recibido_en',
        [clave, jsonEncode(_aJsonMapa(mapa)), ahora.toUtc().toIso8601String()],
      );
      return MapaLocal(mapa: mapa, recibidoEn: ahora, deLaCopia: false);
    } on ErrorDeRed catch (e) {
      final copia = _leer(clave);
      if (copia == null) throw SinTableroNiCopia(e.mensaje);
      return MapaLocal(
        mapa: MapaDelDia.deJson(copia.$1),
        recibidoEn: copia.$2,
        deLaCopia: true,
      );
    }
  }

  /// `tablero:2026-10-01`. Sin fecha, `tablero:hoy`.
  ///
  /// La clave de hoy es literal y no la fecha de hoy: así la copia de "hoy" se
  /// reescribe sola al día siguiente en vez de acumular un renglón por día que
  /// nadie va a volver a mirar.
  String _clave(String que, DateTime? fecha) {
    if (fecha == null) return '$que:hoy';
    final local = fecha.toLocal();
    final mes = local.month.toString().padLeft(2, '0');
    final dia = local.day.toString().padLeft(2, '0');
    return '$que:${local.year}-$mes-$dia';
  }

  void _guardar(String clave, Tablero tablero, DateTime ahora) {
    _db.execute(
      'INSERT INTO tablero_cache (clave, cuerpo, recibido_en) '
      'VALUES (?, ?, ?) '
      'ON CONFLICT(clave) DO UPDATE SET cuerpo = excluded.cuerpo, '
      '                                recibido_en = excluded.recibido_en',
      [clave, jsonEncode(_aJson(tablero)), ahora.toUtc().toIso8601String()],
    );
  }

  (Map<String, Object?>, DateTime)? _leer(String clave) {
    final filas = _db.select(
      'SELECT cuerpo, recibido_en FROM tablero_cache WHERE clave = ?',
      [clave],
    );
    if (filas.isEmpty) return null;
    try {
      return (
        jsonDecode(filas.first['cuerpo'] as String) as Map<String, Object?>,
        DateTime.parse(filas.first['recibido_en'] as String).toLocal(),
      );
    } on FormatException {
      // Una copia que no se puede leer es como no tenerla. Se borra para que no
      // vuelva a fallar en cada apertura: una app que revienta por un renglón
      // corrupto de caché es una app rota por un dato que no importaba.
      _db.execute('DELETE FROM tablero_cache WHERE clave = ?', [clave]);
      return null;
    }
  }

  // ---------------------------------------------------------------------
  // Serialización de vuelta a la forma del contrato
  // ---------------------------------------------------------------------
  // Se guarda con la MISMA forma que mandó el servidor —importes como string de
  // dos decimales— y no la representación interna. Así `Tablero.deJson` es el
  // único lector, y una copia guardada por una versión anterior de la app se
  // sigue leyendo igual.
  //
  // Lo alternativo sería guardar el cuerpo HTTP crudo. Se descartó porque
  // obligaría a que el repo recibiera el string además del objeto, y entonces
  // el cliente tendría que devolver las dos cosas solo para esto.
  Map<String, Object?> _aJson(Tablero t) => {
        'frescura': {
          'calculado_en': t.frescura.calculadoEn?.toUtc().toIso8601String(),
          'minutos': t.frescura.minutos,
          'confiable': t.frescura.confiable,
          'equipos_sin_sincronizar': t.frescura.equiposSinSincronizar,
          'cola_reportada': t.frescura.colaReportada,
          'ops_en_cuarentena': t.frescura.opsEnCuarentena,
          'advertencia': t.frescura.advertencia,
        },
        'venta': {
          'fecha': _soloFecha(t.venta.fecha),
          'total': t.venta.total.texto,
          'efectivo': t.venta.efectivo.texto,
          'transferencia': t.venta.transferencia.texto,
          'documentos': t.venta.documentos,
          'ticket_promedio': t.venta.ticketPromedio.texto,
          'calculado_en': t.venta.calculadoEn?.toUtc().toIso8601String(),
        },
        'visitas': {
          'visitas': t.visitas.visitas,
          'con_venta': t.visitas.conVenta,
          'no_drops': t.visitas.noDrops,
          'no_drops_nuestros': t.visitas.noDropsNuestros,
          'efectividad': t.visitas.efectividad.toStringAsFixed(1),
          'drop_size': t.visitas.dropSize.texto,
          'calculado_en': t.visitas.calculadoEn?.toUtc().toIso8601String(),
        },
        'por_confirmar': {
          'cuantas': t.porConfirmar.cuantas,
          'importe': t.porConfirmar.importe.texto,
        },
        'mermas': {
          'documentos': t.mermas.documentos,
          'unidades': t.mermas.unidades.texto,
          'calculado_en': t.mermas.calculadoEn?.toUtc().toIso8601String(),
        },
        'vendedores': [
          for (final v in t.vendedores)
            {
              'vendedor_id': v.vendedorId,
              'codigo': v.codigo,
              'nombre': v.nombre,
              'venta': v.venta.texto,
              'documentos': v.documentos,
              'visitas': v.visitas,
              'con_venta': v.conVenta,
              'no_drops': v.noDrops,
              'efectivo': v.efectivo.texto,
              'efectividad': v.efectividad.toStringAsFixed(1),
            },
        ],
        'avance': {
          'periodo': _soloFecha(t.avance.periodo),
          'dia_del_mes': t.avance.diaDelMes,
          'dias_del_mes': t.avance.diasDelMes,
          'venta_sin_ruta': t.avance.ventaSinRuta.texto,
          'documentos_sin_ruta': t.avance.documentosSinRuta,
          'rutas': [
            for (final r in t.avance.rutas)
              {
                'ruta_id': r.rutaId,
                'codigo': r.codigo,
                'nombre': r.nombre,
                'venta_mes': r.ventaMes.texto,
                'objetivo': r.objetivo?.texto,
                'logrado': r.logrado?.toStringAsFixed(1),
                'esperado': r.esperado.toStringAsFixed(1),
                'diferencia': r.diferencia?.toStringAsFixed(1),
                'semaforo': r.semaforo,
                'visitas_mes': r.visitasMes,
                'dias_con_venta': r.diasConVenta,
                'clientes_distintos': r.clientesDistintos,
              },
          ],
        },
      };

  Map<String, Object?> _aJsonMapa(MapaDelDia m) => {
        'fecha': _soloFecha(m.fecha),
        'recortados': m.recortados,
        'calculado_en': m.calculadoEn?.toUtc().toIso8601String(),
        'puntos': [
          for (final p in m.puntos)
            {
              'clase': p.clase,
              'lat': p.lat.toStringAsFixed(7),
              'lng': p.lng.toStringAsFixed(7),
              'cliente': p.cliente,
              'vendedor': p.vendedor,
              'importe': p.importe?.texto,
              'motivo': p.motivo,
              'momento': p.momento.toUtc().toIso8601String(),
            },
        ],
      };

  String _soloFecha(DateTime fecha) {
    final mes = fecha.month.toString().padLeft(2, '0');
    final dia = fecha.day.toString().padLeft(2, '0');
    return '${fecha.year}-$mes-$dia';
  }
}
