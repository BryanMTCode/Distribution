/// El tablero por periodo, para la oficina: el cliente de `/v1/tablero/periodo`.
///
/// Pedido en operación (octubre 2026): «que el gerente pueda ver en la app los
/// datos por días y periodos: lo mismo que en el dashboard». Las cifras salen de
/// la MISMA función que el tablero del panel.
library;

import 'dart:convert';

import 'dinero.dart';
import 'sync_cliente.dart' show SesionInvalida, ServidorConProblemas;
import 'tablero_cliente.dart' show SinPermisoDeTablero;
import 'transporte.dart';
import 'vendedores_cliente.dart' show PeriodoVisto, ServidorSinEstaFuncion;

class CifrasDelPeriodo {
  const CifrasDelPeriodo({
    required this.total,
    required this.contado,
    required this.credito,
    required this.canceladas,
    required this.cobrado,
    required this.mermas,
    required this.devoluciones,
    required this.noVentas,
    required this.clientesAtendidos,
    required this.clientesNuevos,
  });

  factory CifrasDelPeriodo.deJson(Map<String, Object?> j) => CifrasDelPeriodo(
        total: Dinero.deTexto(j['total']! as String),
        contado: Dinero.deTexto(j['contado']! as String),
        credito: Dinero.deTexto(j['credito']! as String),
        canceladas: (j['canceladas']! as num).toInt(),
        cobrado: Dinero.deTexto(j['cobrado']! as String),
        mermas: (j['mermas']! as num).toInt(),
        devoluciones: (j['devoluciones']! as num).toInt(),
        noVentas: (j['no_ventas']! as num).toInt(),
        clientesAtendidos: (j['clientes_atendidos']! as num).toInt(),
        clientesNuevos: (j['clientes_nuevos']! as num).toInt(),
      );

  final Dinero total;
  final Dinero contado;
  final Dinero credito;
  final int canceladas;
  final Dinero cobrado;
  final int mermas;
  final int devoluciones;
  final int noVentas;
  final int clientesAtendidos;
  final int clientesNuevos;
}

class VendedorDelPeriodo {
  const VendedorDelPeriodo({
    required this.id,
    required this.codigo,
    required this.nombre,
    required this.ventas,
    required this.importe,
    required this.cobrado,
    required this.mermas,
    required this.noVentas,
  });

  factory VendedorDelPeriodo.deJson(Map<String, Object?> j) => VendedorDelPeriodo(
        id: j['id']! as String,
        codigo: j['codigo']! as String,
        nombre: j['nombre']! as String,
        ventas: (j['ventas']! as num).toInt(),
        importe: Dinero.deTexto(j['importe']! as String),
        cobrado: Dinero.deTexto(j['cobrado']! as String),
        mermas: (j['mermas']! as num).toInt(),
        noVentas: (j['no_ventas']! as num).toInt(),
      );

  final String id;
  final String codigo;
  final String nombre;
  final int ventas;
  final Dinero importe;
  final Dinero cobrado;
  final int mermas;
  final int noVentas;
}

class DiaDelPeriodo {
  const DiaDelPeriodo({
    required this.fecha,
    required this.ventas,
    required this.importe,
    required this.cobrado,
  });

  factory DiaDelPeriodo.deJson(Map<String, Object?> j) => DiaDelPeriodo(
        fecha: j['fecha']! as String,
        ventas: (j['ventas']! as num).toInt(),
        importe: Dinero.deTexto(j['importe']! as String),
        cobrado: Dinero.deTexto(j['cobrado']! as String),
      );

  /// `YYYY-MM-DD`.
  final String fecha;
  final int ventas;
  final Dinero importe;
  final Dinero cobrado;
}

class TableroDelPeriodo {
  const TableroDelPeriodo({
    required this.periodo,
    required this.periodos,
    required this.cifras,
    required this.porVendedor,
    required this.porDia,
  });

  factory TableroDelPeriodo.deJson(Map<String, Object?> j) => TableroDelPeriodo(
        periodo: PeriodoVisto.deJson((j['periodo']! as Map).cast()),
        periodos: [
          for (final p in (j['periodos'] ?? const <Object?>[]) as List)
            ((p as List)[0] as String, p[1] as String),
        ],
        cifras: CifrasDelPeriodo.deJson((j['cifras']! as Map).cast()),
        porVendedor: [
          for (final v in j['por_vendedor']! as List)
            VendedorDelPeriodo.deJson((v as Map).cast()),
        ],
        porDia: [
          for (final d in j['por_dia']! as List) DiaDelPeriodo.deJson((d as Map).cast()),
        ],
      );

  final PeriodoVisto periodo;
  final List<(String, String)> periodos;
  final CifrasDelPeriodo cifras;
  final List<VendedorDelPeriodo> porVendedor;

  /// Vacío en un periodo de un solo día, o de más de dos meses.
  final List<DiaDelPeriodo> porDia;
}

/// El tamaño del negocio: cuántos clientes, vendedores, artículos… La misma
/// consulta que la pantalla Empresa del panel.
class ResumenDeLaEmpresa {
  const ResumenDeLaEmpresa(this._j);

  factory ResumenDeLaEmpresa.deJson(Map<String, Object?> j) => ResumenDeLaEmpresa(j);

  final Map<String, Object?> _j;

  int _n(String clave) => (_j[clave]! as num).toInt();
  Dinero _d(String clave) => Dinero.deTexto(_j[clave]! as String);

  int get clientesActivos => _n('clientes_activos');
  int get prospectos => _n('prospectos');
  int get clientesInactivos => _n('clientes_inactivos');
  int get clientesNuevosMes => _n('clientes_nuevos_mes');
  int get clientesConSaldo => _n('clientes_con_saldo');
  int get vendedores => _n('vendedores');
  int get vendedoresConCamion => _n('vendedores_con_camion');
  int get usuariosOficina => _n('usuarios_oficina');
  int get rutas => _n('rutas');
  int get telefonos => _n('telefonos');
  int get productos => _n('productos');
  int get productosSinPrecio => _n('productos_sin_precio');
  int get bodegas => _n('bodegas');
  int get camiones => _n('camiones');

  /// Texto del contrato («480.000»): la pantalla solo lo muestra.
  String get piezasEnBodegas => _j['piezas_en_bodegas']! as String;
  String get piezasEnCamiones => _j['piezas_en_camiones']! as String;
  int get existenciasNegativas => _n('existencias_negativas');
  Dinero get cartera => _d('cartera');
  Dinero get carteraVencida => _d('cartera_vencida');
  Dinero get vendidoMes => _d('vendido_mes');
  Dinero get vendidoAnio => _d('vendido_anio');
}

class ClientePeriodo {
  const ClientePeriodo(this._transporte);

  final Transporte _transporte;

  /// Un periodo con nombre («semana», «mes»…) o, con `desde` y `hasta`
  /// (`YYYY-MM-DD`), un rango a mano.
  Future<TableroDelPeriodo> ver({
    String periodo = 'hoy',
    String? desde,
    String? hasta,
  }) async {
    final r = await _transporte.obtener('/v1/tablero/periodo', parametros: {
      if (desde != null && hasta != null) ...{
        'periodo': 'rango',
        'desde': desde,
        'hasta': hasta,
      } else
        'periodo': periodo,
    });
    _revisar(r);
    return TableroDelPeriodo.deJson((jsonDecode(r.cuerpo) as Map).cast());
  }

  Future<ResumenDeLaEmpresa> empresa() async {
    final r = await _transporte.obtener('/v1/tablero/empresa');
    _revisar(r);
    return ResumenDeLaEmpresa.deJson((jsonDecode(r.cuerpo) as Map).cast());
  }

  void _revisar(RespuestaHttp r) {
    if (r.ok) return;
    if (r.codigo == 401) throw SesionInvalida(r.codigo);
    if (r.codigo == 403) throw SinPermisoDeTablero(r.cuerpo);
    if (r.codigo == 404 && r.cuerpo.contains('"Not Found"')) {
      throw const ServidorSinEstaFuncion();
    }
    throw ServidorConProblemas(r.codigo, r.cuerpo);
  }
}
