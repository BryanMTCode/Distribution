/// Los clientes para la oficina: el cliente de `/v1/oficina/clientes`.
///
/// No es la cartera de ruta que el vendedor baja para vender sin señal: es la
/// vista de la oficina sobre TODOS los clientes —quién debe, desde cuándo, qué
/// compra— y el bloqueo del crédito. Necesita red, como el tablero.
library;

import 'dart:convert';

import 'cargas_cliente.dart' show CargaRechazada;
import 'dinero.dart';
import 'sync_cliente.dart' show SesionInvalida, ServidorConProblemas;
import 'transporte.dart';
import 'vendedores_cliente.dart' show ServidorSinEstaFuncion, SinPermisoDeVendedores;

Dinero _d(Object? v) => Dinero.deTexto(v! as String);

class ClienteDeOficina {
  const ClienteDeOficina({
    required this.id,
    required this.codigo,
    required this.nombre,
    required this.ruta,
    required this.estatus,
    required this.bloqueado,
    required this.telefono,
    required this.saldo,
    required this.saldoVencido,
    required this.facturasVencidas,
    required this.ultimaCompra,
  });

  factory ClienteDeOficina.deJson(Map<String, Object?> j) => ClienteDeOficina(
        id: j['id']! as String,
        codigo: j['codigo'] as String?,
        nombre: j['nombre']! as String,
        ruta: j['ruta'] as String?,
        estatus: j['estatus']! as String,
        bloqueado: j['bloqueado']! as bool,
        telefono: j['telefono'] as String?,
        saldo: _d(j['saldo']),
        saldoVencido: _d(j['saldo_vencido']),
        facturasVencidas: (j['facturas_vencidas']! as num).toInt(),
        ultimaCompra: j['ultima_compra'] as String?,
      );

  final String id;
  final String? codigo;
  final String nombre;
  final String? ruta;
  final String estatus;
  final bool bloqueado;
  final String? telefono;
  final Dinero saldo;
  final Dinero saldoVencido;
  final int facturasVencidas;

  /// `YYYY-MM-DD`, o nulo si nunca ha comprado.
  final String? ultimaCompra;
}

class ListaDeClientesDeOficina {
  const ListaDeClientesDeOficina({
    required this.filtro,
    required this.clientes,
    required this.recortado,
    required this.conteos,
  });

  factory ListaDeClientesDeOficina.deJson(Map<String, Object?> j) =>
      ListaDeClientesDeOficina(
        filtro: j['filtro']! as String,
        clientes: [
          for (final c in j['clientes']! as List)
            ClienteDeOficina.deJson((c as Map).cast()),
        ],
        recortado: j['recortado']! as bool,
        conteos: {
          for (final e in (j['conteos']! as Map).entries)
            e.key as String: (e.value as num).toInt(),
        },
      );

  final String filtro;
  final List<ClienteDeOficina> clientes;
  final bool recortado;

  /// Cuántos hay en cada filtro: todos, con_saldo, vencidos, bloqueados, prospectos.
  final Map<String, int> conteos;
}

class CuentaAbierta {
  const CuentaAbierta({
    required this.ventaId,
    required this.folio,
    required this.emision,
    required this.vencimiento,
    required this.original,
    required this.pagado,
    required this.saldo,
    required this.vencida,
    required this.diasVencida,
  });

  factory CuentaAbierta.deJson(Map<String, Object?> j) => CuentaAbierta(
        ventaId: j['venta_id']! as String,
        folio: j['folio'] as String?,
        emision: j['fecha_emision']! as String,
        vencimiento: j['fecha_vencimiento']! as String,
        original: _d(j['importe_original']),
        pagado: _d(j['importe_pagado']),
        saldo: _d(j['saldo']),
        vencida: j['vencida']! as bool,
        diasVencida: (j['dias_vencida']! as num).toInt(),
      );

  final String ventaId;
  final String? folio;
  final String emision;
  final String vencimiento;
  final Dinero original;
  final Dinero pagado;
  final Dinero saldo;
  final bool vencida;
  final int diasVencida;
}

class VentaDelCliente {
  const VentaDelCliente({
    required this.id,
    required this.folio,
    required this.fecha,
    required this.tipo,
    required this.estado,
    required this.total,
    required this.vendedor,
  });

  factory VentaDelCliente.deJson(Map<String, Object?> j) => VentaDelCliente(
        id: j['id']! as String,
        folio: j['folio'] as String?,
        fecha: j['fecha']! as String,
        tipo: j['tipo']! as String,
        estado: j['estado']! as String,
        total: _d(j['total']),
        vendedor: j['vendedor']! as String,
      );

  final String id;
  final String? folio;
  final String fecha;
  final String tipo;
  final String estado;
  final Dinero total;
  final String vendedor;
}

class CobroDelCliente {
  const CobroDelCliente({
    required this.folio,
    required this.fecha,
    required this.importe,
    required this.formaPago,
    required this.estado,
    required this.vendedor,
  });

  factory CobroDelCliente.deJson(Map<String, Object?> j) => CobroDelCliente(
        folio: j['folio'] as String?,
        fecha: j['fecha']! as String,
        importe: _d(j['importe']),
        formaPago: j['forma_pago']! as String,
        estado: j['estado']! as String,
        vendedor: j['vendedor']! as String,
      );

  final String? folio;
  final String fecha;
  final Dinero importe;
  final String formaPago;
  final String estado;
  final String vendedor;
}

class FichaDelCliente {
  const FichaDelCliente(this._j);

  factory FichaDelCliente.deJson(Map<String, Object?> j) => FichaDelCliente(j);

  final Map<String, Object?> _j;

  String get id => _j['id']! as String;
  String? get codigo => _j['codigo'] as String?;
  String get nombre => _j['nombre']! as String;
  String? get razonSocial => _j['razon_social'] as String?;
  String? get contacto => _j['contacto'] as String?;
  String? get telefono => _j['telefono'] as String?;
  String? get direccion => _j['direccion'] as String?;
  String? get ruta => _j['ruta'] as String?;
  String get estatus => _j['estatus']! as String;
  bool get permiteCredito => _j['permite_credito']! as bool;
  Dinero get limiteCredito => _d(_j['limite_credito']);
  int? get diasCredito => (_j['dias_credito'] as num?)?.toInt();
  bool get bloqueado => _j['bloqueado']! as bool;
  String? get bloqueoMotivo => _j['bloqueo_motivo'] as String?;
  Dinero get saldo => _d(_j['saldo']);
  Dinero get disponible => _d(_j['disponible']);
  Dinero get saldoVencido => _d(_j['saldo_vencido']);
  Dinero get porConfirmar => _d(_j['por_confirmar']);
  Dinero get compradoMes => _d(_j['comprado_mes']);
  Dinero get compradoAnio => _d(_j['comprado_anio']);
  List<CuentaAbierta> get cuentas =>
      [for (final c in _j['cuentas']! as List) CuentaAbierta.deJson((c as Map).cast())];
  List<VentaDelCliente> get ventas =>
      [for (final v in _j['ventas']! as List) VentaDelCliente.deJson((v as Map).cast())];
  List<CobroDelCliente> get cobros =>
      [for (final k in _j['cobros']! as List) CobroDelCliente.deJson((k as Map).cast())];

  /// Si este usuario puede bloquear el crédito (`clientes.administrar`).
  bool get puedeBloquear => _j['puede_bloquear']! as bool;
  String? get mensaje => _j['mensaje'] as String?;
}

class ClienteClientesDeOficina {
  const ClienteClientesDeOficina(this._transporte);

  final Transporte _transporte;

  Future<ListaDeClientesDeOficina> lista({String filtro = 'todos', String q = ''}) async {
    final r = await _transporte.obtener('/v1/oficina/clientes', parametros: {
      'filtro': filtro,
      if (q.trim().isNotEmpty) 'q': q.trim(),
    });
    _revisar(r);
    return ListaDeClientesDeOficina.deJson((jsonDecode(r.cuerpo) as Map).cast());
  }

  Future<FichaDelCliente> ficha(String id) async {
    final r = await _transporte.obtener('/v1/oficina/clientes/$id');
    _revisar(r);
    return FichaDelCliente.deJson((jsonDecode(r.cuerpo) as Map).cast());
  }

  Future<FichaDelCliente> bloquear(String id, {required bool bloquear, String motivo = ''}) async {
    final r = await _transporte.post(
      '/v1/oficina/clientes/$id/bloqueo',
      {'bloquear': bloquear, 'motivo': motivo},
    );
    _revisar(r);
    return FichaDelCliente.deJson((jsonDecode(r.cuerpo) as Map).cast());
  }

  void _revisar(RespuestaHttp r) {
    if (r.ok) return;
    if (r.codigo == 401) throw SesionInvalida(r.codigo);
    if (r.codigo == 403) throw const SinPermisoDeVendedores();
    if (r.codigo == 404 && r.cuerpo.contains('"Not Found"')) {
      throw const ServidorSinEstaFuncion();
    }
    if (r.codigo >= 400 && r.codigo < 500) {
      String detalle = r.cuerpo;
      try {
        final j = jsonDecode(r.cuerpo);
        if (j is Map && j['detail'] is String) detalle = j['detail'] as String;
      } on FormatException {
        // Crudo.
      }
      throw CargaRechazada(r.codigo, detalle);
    }
    throw ServidorConProblemas(r.codigo, r.cuerpo);
  }
}
