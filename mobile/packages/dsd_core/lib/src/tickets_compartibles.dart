/// Los tickets del cierre del día como TEXTO para compartir (ADR 0002 §82).
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ TEXTO Y NO IMAGEN
/// ─────────────────────────────────────────────────────────────────────────
/// El ticket del corte y el de la carga se mandan por WhatsApp al gerente, al
/// grupo de la ruta o al vendedor. Como texto se lee en cualquier teléfono, se
/// puede buscar, copiar y reenviar, y no depende de la impresora térmica —que
/// sigue sirviendo para la remisión del cliente—.
///
/// Los encabezados van entre asteriscos porque WhatsApp los pinta en negritas;
/// en cualquier otro lado se leen igual de bien.
///
/// Dart puro: lo usan el teléfono del vendedor (su corte y su solicitud) y la
/// app del gerente (la carga que acaba de aceptar), y los dos tienen que decir
/// lo mismo con las mismas palabras.
library;

import 'dia_operativo.dart';
import 'dinero.dart';
import 'precio.dart';

/// Un producto del ticket: cuánto, en qué presentación.
class RenglonDeTicket {
  const RenglonDeTicket({
    required this.nombre,
    required this.cantidadBase,
    this.unidadBase = 'PZA',
    this.unidad,
    this.factor,
    this.aceptadaBase,
  });

  final String nombre;

  /// Lo contado (corte) o lo pedido (carga), en unidad base.
  final Cantidad cantidadBase;
  final String unidadBase;

  /// La presentación en que se pidió (CAJA) y su factor, si no es la base.
  final String? unidad;
  final Factor? factor;

  /// Lo que la oficina dejó al aceptar, en unidad base. Nulo mientras no se
  /// resuelve.
  final Cantidad? aceptadaBase;

  String get cantidadEnPalabras => _enBultos(cantidadBase);

  String? get aceptadaEnPalabras =>
      aceptadaBase == null ? null : _enBultos(aceptadaBase!);

  /// «10 CAJA» si cabe en bultos enteros; si no, en la unidad base.
  String _enBultos(Cantidad base) {
    final f = factor;
    final u = unidad;
    if (f != null && u != null && !f.esUno && f.esValido) {
      final numerador = base.milesimos * 10;
      if (numerador % f.diezmilesimos == 0) {
        return '${numerador ~/ f.diezmilesimos} $u';
      }
    }
    return '${base.textoCorto} $unidadBase';
  }
}

/// Lo que dice el ticket del corte del vendedor.
class TicketDeCorte {
  const TicketDeCorte({
    required this.id,
    required this.vendedor,
    required this.fechaOperativa,
    required this.momento,
    required this.ventas,
    required this.efectivoVendido,
    required this.transferencias,
    required this.efectivoEntregado,
    required this.sobrante,
    this.codigoVendedor,
    this.cargaFolio,
    this.observaciones,
  });

  final String id;
  final String vendedor;
  final String? codigoVendedor;

  /// `YYYY-MM-DD`, el día operativo del corte.
  final String fechaOperativa;

  /// Cuándo se hizo, para la hora del ticket.
  final DateTime momento;
  final String? cargaFolio;
  final int ventas;
  final Dinero efectivoVendido;
  final Dinero transferencias;
  final Dinero efectivoEntregado;
  final List<RenglonDeTicket> sobrante;
  final String? observaciones;

  Dinero get vendido => efectivoVendido + transferencias;

  /// Positivo si sobra, negativo si falta.
  Dinero get diferencia => efectivoEntregado - efectivoVendido;
}

/// Lo que dice el ticket de la solicitud de carga.
class TicketDeCarga {
  const TicketDeCarga({
    required this.id,
    required this.vendedor,
    required this.paraElDia,
    required this.estado,
    required this.renglones,
    this.codigoVendedor,
    this.cargaFolio,
    this.motivo,
    this.observaciones,
    this.bodega,
  });

  final String id;
  final String vendedor;
  final String? codigoVendedor;

  /// `YYYY-MM-DD`: el día en que sale esa carga.
  final String paraElDia;

  /// 'pendiente' | 'aceptada' | 'rechazada' | 'reemplazada'.
  final String estado;
  final List<RenglonDeTicket> renglones;
  final String? cargaFolio;
  final String? motivo;
  final String? observaciones;
  final String? bodega;
}

const _empresa = '*DISTRIBUCIONES SE*';

String _quien(String vendedor, String? codigo) =>
    codigo == null || codigo.isEmpty ? vendedor : '$vendedor ($codigo)';

String _conMayuscula(String texto) =>
    texto.isEmpty ? texto : texto[0].toUpperCase() + texto.substring(1);

String _hora(DateTime momento) {
  final l = momento.toLocal();
  return '${l.hour.toString().padLeft(2, '0')}:${l.minute.toString().padLeft(2, '0')}';
}

/// Los primeros ocho caracteres del id: basta para encontrarlo y cabe en un renglón.
String _folioCorto(String id) => id.replaceAll('-', '').substring(0, 8).toUpperCase();

/// El ticket del corte: lo vendido, el efectivo que entrega y lo que le sobró.
String textoDelCorte(TicketDeCorte t) {
  final b = StringBuffer()
    ..writeln(_empresa)
    ..writeln('*CORTE DEL DÍA*')
    ..writeln(_quien(t.vendedor, t.codigoVendedor))
    ..writeln('${_conMayuscula(diaEnPalabras(t.fechaOperativa))} · ${_hora(t.momento)}');
  if (t.cargaFolio != null) b.writeln('Carga ${t.cargaFolio}');

  b
    ..writeln()
    ..writeln('*VENTAS*')
    ..writeln('${t.ventas} venta${t.ventas == 1 ? '' : 's'}: ${t.vendido.enPesos}')
    ..writeln('  Efectivo: ${t.efectivoVendido.enPesos}')
    ..writeln('  Transferencia: ${t.transferencias.enPesos}')
    ..writeln()
    ..writeln('*EFECTIVO QUE ENTREGA*')
    ..writeln(t.efectivoEntregado.enPesos);
  final d = t.diferencia;
  if (d.esCero) {
    b.writeln('Cuadra con lo vendido en efectivo.');
  } else if (d.esNegativo) {
    b.writeln('Faltan ${(-d).enPesos} contra lo vendido en efectivo.');
  } else {
    b.writeln('Sobran ${d.enPesos} contra lo vendido en efectivo.');
  }

  b
    ..writeln()
    ..writeln('*SOBRANTE EN EL CAMIÓN*');
  final conAlgo = t.sobrante.where((r) => !r.cantidadBase.esCero).toList();
  if (conAlgo.isEmpty) {
    b.writeln('Nada: el camión regresó vacío.');
  } else {
    for (final r in conAlgo) {
      b.writeln('• ${r.nombre}: ${r.cantidadEnPalabras}');
    }
  }

  if ((t.observaciones ?? '').trim().isNotEmpty) {
    b
      ..writeln()
      ..writeln('Notas: ${t.observaciones!.trim()}');
  }
  b
    ..writeln()
    ..write('Corte ${_folioCorto(t.id)}');
  return b.toString();
}

/// El ticket de la carga pedida; cuando la oficina la acepta, el de la carga.
String textoDeLaCarga(TicketDeCarga t) {
  final aceptada = t.estado == 'aceptada';
  final b = StringBuffer()
    ..writeln(_empresa)
    ..writeln(aceptada ? '*CARGA ACEPTADA*' : '*SOLICITUD DE CARGA*')
    ..writeln(_quien(t.vendedor, t.codigoVendedor))
    ..writeln('Para el ${diaEnPalabras(t.paraElDia)}');
  switch (t.estado) {
    case 'aceptada':
      b.writeln(
        'Carga ${t.cargaFolio ?? ''}'.trim() +
            (t.bodega == null ? '' : ' · sale de ${t.bodega}'),
      );
    case 'rechazada':
      b.writeln('RECHAZADA${t.motivo == null ? '' : ': ${t.motivo}'}');
    case 'reemplazada':
      b.writeln('Reemplazada por una solicitud más reciente.');
    default:
      b.writeln('Pendiente: la revisa la oficina.');
  }

  b
    ..writeln()
    ..writeln('*PRODUCTOS*');
  var cuantos = 0;
  for (final r in t.renglones) {
    final aceptado = r.aceptadaEnPalabras;
    if (aceptada && aceptado != null && r.aceptadaBase != r.cantidadBase) {
      if (r.aceptadaBase!.esCero) {
        b.writeln('• ${r.nombre}: pidió ${r.cantidadEnPalabras}, no se carga');
        continue;
      }
      b.writeln('• ${r.nombre}: $aceptado (pidió ${r.cantidadEnPalabras})');
    } else {
      b.writeln('• ${r.nombre}: ${r.cantidadEnPalabras}');
    }
    cuantos++;
  }
  b.writeln('$cuantos producto${cuantos == 1 ? '' : 's'}');

  if ((t.observaciones ?? '').trim().isNotEmpty) {
    b
      ..writeln()
      ..writeln('Notas: ${t.observaciones!.trim()}');
  }
  b
    ..writeln()
    ..write('Solicitud ${_folioCorto(t.id)}');
  return b.toString();
}
