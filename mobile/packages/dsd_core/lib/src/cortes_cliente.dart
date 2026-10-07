/// El corte del día desde la app: el cliente de `/v1/cortes`.
///
/// Solo para quien tiene `inventario.liquidar` (admin, supervisor, gerente). Las
/// reglas son las del panel —el servidor usa las mismas funciones—: el conteo
/// vacío vale cero, no se cierra con el teléfono del vendedor atrasado, y sin dato
/// de que terminó de subir hay que confirmarlo a mano.
library;

import 'dart:convert';

import 'cargas_cliente.dart' show CargaRechazada;
import 'dinero.dart';
import 'sync_cliente.dart' show SesionInvalida, ServidorConProblemas;
import 'transporte.dart';
import 'vendedores_cliente.dart' show ServidorSinEstaFuncion;

/// Sin `inventario.liquidar`.
class SinPermisoDeCortar implements Exception {
  const SinPermisoDeCortar();
}

class CargaPorCortar {
  const CargaPorCortar({
    required this.cargaId,
    required this.folio,
    required this.fecha,
    required this.vendedor,
    required this.camion,
    required this.dias,
    required this.ventas,
    required this.importe,
  });

  factory CargaPorCortar.deJson(Map<String, Object?> j) => CargaPorCortar(
        cargaId: j['carga_id']! as String,
        folio: j['folio']! as String,
        fecha: j['fecha_operativa']! as String,
        vendedor: j['vendedor']! as String,
        camion: j['camion']! as String,
        dias: (j['dias']! as num).toInt(),
        ventas: (j['ventas']! as num).toInt(),
        importe: Dinero.deTexto(j['importe']! as String),
      );

  final String cargaId;
  final String folio;
  final String fecha;
  final String vendedor;
  final String camion;

  /// Días desde la carga. Uno de hace tres días es un camión que nadie contó.
  final int dias;
  final int ventas;
  final Dinero importe;
}

class CorteEnLista {
  const CorteEnLista({
    required this.id,
    required this.folio,
    required this.estado,
    required this.fecha,
    required this.vendedor,
    required this.diferenciaEfectivo,
    required this.faltantes,
  });

  factory CorteEnLista.deJson(Map<String, Object?> j) => CorteEnLista(
        id: j['id']! as String,
        folio: j['folio']! as String,
        estado: j['estado']! as String,
        fecha: j['fecha_operativa']! as String,
        vendedor: j['vendedor']! as String,
        diferenciaEfectivo: Dinero.deTexto(j['diferencia_efectivo']! as String),
        faltantes: (j['faltantes']! as num).toInt(),
      );

  final String id;
  final String folio;
  final String estado;
  final String fecha;
  final String vendedor;
  final Dinero diferenciaEfectivo;

  /// Cuántos productos con diferencia.
  final int faltantes;
}

class ListaDeCortes {
  const ListaDeCortes({required this.porCortar, required this.cortes});

  factory ListaDeCortes.deJson(Map<String, Object?> j) => ListaDeCortes(
        porCortar: [
          for (final c in j['por_cortar']! as List)
            CargaPorCortar.deJson((c as Map).cast()),
        ],
        cortes: [
          for (final c in j['cortes']! as List) CorteEnLista.deJson((c as Map).cast()),
        ],
      );

  final List<CargaPorCortar> porCortar;
  final List<CorteEnLista> cortes;
}

class RenglonDelCorte {
  const RenglonDelCorte({
    required this.id,
    required this.nombre,
    required this.unidadBase,
    required this.inicial,
    required this.cargada,
    required this.vendida,
    required this.merma,
    required this.devuelta,
    required this.esperado,
    required this.contada,
    required this.diferencia,
  });

  factory RenglonDelCorte.deJson(Map<String, Object?> j) => RenglonDelCorte(
        id: j['id']! as String,
        nombre: j['nombre']! as String,
        unidadBase: j['unidad_base']! as String,
        inicial: j['inicial']! as String,
        cargada: j['cargada']! as String,
        vendida: j['vendida']! as String,
        merma: j['merma']! as String,
        devuelta: j['devuelta']! as String,
        esperado: j['esperado']! as String,
        contada: j['contada']! as String,
        diferencia: j['diferencia']! as String,
      );

  final String id;
  final String nombre;
  final String unidadBase;

  /// Cantidades en texto del contrato («60.000»): la pantalla solo las muestra.
  final String inicial;
  final String cargada;
  final String vendida;
  final String merma;
  final String devuelta;
  final String esperado;
  final String contada;

  /// Contado menos esperado: negativo es faltante, positivo sobrante.
  final String diferencia;

  bool get cuadra => double.parse(diferencia) == 0;
}

class CorteDelDia {
  const CorteDelDia({
    required this.id,
    required this.folio,
    required this.estado,
    required this.abierto,
    required this.fecha,
    required this.vendedor,
    required this.camion,
    required this.carga,
    required this.efectivoEsperado,
    required this.efectivoEntregado,
    required this.diferenciaEfectivo,
    required this.arqueoHecho,
    required this.renglones,
    required this.bloqueos,
    required this.respaldado,
    required this.motivoSinRespaldo,
    required this.totalCargado,
    this.mensaje,
  });

  factory CorteDelDia.deJson(Map<String, Object?> j) {
    final respaldo = (j['respaldo']! as Map).cast<String, Object?>();
    return CorteDelDia(
      id: j['id']! as String,
      folio: j['folio']! as String,
      estado: j['estado']! as String,
      abierto: j['abierto']! as bool,
      fecha: j['fecha_operativa']! as String,
      vendedor: j['vendedor']! as String,
      camion: j['camion']! as String,
      carga: j['carga']! as String,
      efectivoEsperado: Dinero.deTexto(j['efectivo_esperado']! as String),
      efectivoEntregado: Dinero.deTexto(j['efectivo_entregado']! as String),
      diferenciaEfectivo: Dinero.deTexto(j['diferencia_efectivo']! as String),
      arqueoHecho: j['arqueo_hecho']! as bool,
      renglones: [
        for (final r in j['renglones']! as List) RenglonDelCorte.deJson((r as Map).cast()),
      ],
      bloqueos: ((j['bloqueos'] ?? const <Object?>[]) as List).cast<String>(),
      respaldado: respaldo['respaldado']! as bool,
      motivoSinRespaldo: respaldo['motivo'] as String?,
      totalCargado: Dinero.deTexto(j['total_cargado']! as String),
      mensaje: j['mensaje'] as String?,
    );
  }

  final String id;
  final String folio;
  final String estado;
  final bool abierto;
  final String fecha;
  final String vendedor;
  final String camion;
  final String carga;
  final Dinero efectivoEsperado;
  final Dinero efectivoEntregado;
  final Dinero diferenciaEfectivo;
  final bool arqueoHecho;
  final List<RenglonDelCorte> renglones;

  /// Lo que impide cerrar ahora mismo.
  final List<String> bloqueos;

  /// Si hay un dato de que el teléfono del vendedor terminó de subir. Si no,
  /// cerrar pide confirmarlo a mano.
  final bool respaldado;
  final String? motivoSinRespaldo;

  /// Lo que el cierre le cargó a la cuenta del vendedor.
  final Dinero totalCargado;
  final String? mensaje;
}

class ClienteCortes {
  const ClienteCortes(this._transporte);

  final Transporte _transporte;

  Future<ListaDeCortes> lista() async {
    final r = await _transporte.obtener('/v1/cortes');
    _revisar(r);
    return ListaDeCortes.deJson((jsonDecode(r.cuerpo) as Map).cast());
  }

  Future<CorteDelDia> abrir(String cargaId) =>
      _post('/v1/cortes', {'carga_id': cargaId});

  Future<CorteDelDia> ver(String id) async {
    final r = await _transporte.obtener('/v1/cortes/$id');
    _revisar(r);
    return CorteDelDia.deJson((jsonDecode(r.cuerpo) as Map).cast());
  }

  /// De id de renglón a lo contado, como se tecleó. El que no va, vale cero.
  Future<CorteDelDia> contar(String id, Map<String, String> contados) =>
      _post('/v1/cortes/$id/conteo', {'contados': contados});

  Future<CorteDelDia> arqueo(String id, {required String efectivo, String observaciones = ''}) =>
      _post('/v1/cortes/$id/arqueo', {'efectivo': efectivo, 'observaciones': observaciones});

  Future<CorteDelDia> cerrar(String id, {bool confirmoSincronizado = false}) =>
      _post('/v1/cortes/$id/cerrar', {'confirmo_sincronizado': confirmoSincronizado});

  Future<CorteDelDia> _post(String ruta, Map<String, Object?> cuerpo) async {
    final r = await _transporte.post(ruta, cuerpo);
    _revisar(r);
    return CorteDelDia.deJson((jsonDecode(r.cuerpo) as Map).cast());
  }

  void _revisar(RespuestaHttp r) {
    if (r.ok) return;
    if (r.codigo == 401) throw SesionInvalida(r.codigo);
    if (r.codigo == 403) throw const SinPermisoDeCortar();
    if (r.codigo == 404 && r.cuerpo.contains('"Not Found"')) {
      throw const ServidorSinEstaFuncion();
    }
    // El mismo tipo que las cargas: «el servidor dijo que no, y este es su
    // texto». La pantalla lo muestra tal cual.
    if (r.codigo >= 400 && r.codigo < 500) throw CargaRechazada(r.codigo, _detalle(r.cuerpo));
    throw ServidorConProblemas(r.codigo, r.cuerpo);
  }

  String _detalle(String cuerpo) {
    try {
      final json = jsonDecode(cuerpo);
      if (json is Map && json['detail'] is String) return json['detail'] as String;
    } on FormatException {
      // Cae al crudo.
    }
    return cuerpo;
  }
}
