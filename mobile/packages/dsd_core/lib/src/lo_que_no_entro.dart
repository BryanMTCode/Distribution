/// Lo que el servidor no aceptó, dicho para el vendedor (ADR 0002 §165).
///
/// Hasta ahora el teléfono solo contaba: «2 con error». El motivo venía en la
/// respuesta del servidor y se guardaba en la cola (`outbox.ultimo_error`), pero
/// nadie lo enseñaba, así que ni el vendedor ni quien lo atendía por teléfono
/// sabían qué había pasado sin entrar al panel. Esto dice QUÉ documento es —la
/// venta, con su folio y su tienda— y lo que contestó el servidor.
library;

import 'dart:convert';

import 'package:sqlite3/sqlite3.dart';

class DocumentoConError {
  const DocumentoConError({
    required this.operacionId,
    required this.que,
    required this.motivo,
  });

  final String operacionId;

  /// «Venta VEND01-000124 · Abarrotes Doña Mary».
  final String que;

  /// Lo que contestó el servidor, sin el código técnico de adelante.
  final String motivo;
}

/// Lo que quedó con error, en el orden de la cola.
List<DocumentoConError> documentosConError(Database db) => [
      for (final f in db.select(
        "SELECT operacion_id, tipo, payload, ultimo_error FROM outbox "
        "WHERE estado = 'cuarentena' ORDER BY secuencia",
      ))
        DocumentoConError(
          operacionId: f['operacion_id'] as String,
          que: _que(db, f['tipo'] as String, f['payload'] as String),
          motivo: _motivo(f['ultimo_error'] as String?),
        ),
    ];

String _que(Database db, String tipo, String payload) {
  List<Object?> operaciones;
  try {
    operaciones = (jsonDecode(payload) as Map)['operaciones'] as List? ?? const [];
  } catch (_) {
    return tipo;
  }
  final partes = [
    for (final o in operaciones.whereType<Map<Object?, Object?>>()) _unaOperacion(db, o),
  ];
  return partes.isEmpty ? tipo : partes.join(' y ');
}

String _unaOperacion(Database db, Map<Object?, Object?> o) {
  final datos = (o['datos'] as Map?) ?? const {};
  final entidad = o['entidad_id'] as String? ?? '';
  return switch (o['tipo']) {
    'venta.crear' => 'Venta ${datos['folio_local'] ?? ''}${_tienda(db, datos['cliente_id'])}',
    'venta.cancelar' => 'Cancelación de la venta ${_deLaVenta(db, entidad)}',
    'venta.modificar' => 'Cambio a la venta ${_deLaVenta(db, datos['venta_id'])}',
    'no_drop.crear' => 'No compró${_tienda(db, datos['cliente_id'])}',
    'cliente.crear' => 'Tienda nueva · ${datos['nombre_comercial'] ?? ''}',
    'cliente.ubicar' => 'Ubicación${_tienda(db, entidad)}',
    'merma.crear' => 'Merma',
    'corte.crear' => 'Corte del día',
    'solicitud_carga.crear' => 'Pedido de carga',
    'traspaso.crear' => 'Traspaso',
    final otro => '$otro',
  };
}

String _tienda(Database db, Object? clienteId) {
  if (clienteId is! String) return '';
  final f = db.select('SELECT nombre_comercial FROM clientes WHERE id = ?', [clienteId]);
  return f.isEmpty ? '' : ' · ${f.single['nombre_comercial']}';
}

String _deLaVenta(Database db, Object? ventaId) {
  if (ventaId is! String) return '';
  final f = db.select('SELECT folio_local, cliente_id FROM ventas WHERE id = ?', [ventaId]);
  if (f.isEmpty) return '';
  return '${f.single['folio_local']}${_tienda(db, f.single['cliente_id'])}';
}

/// «conflicto_de_datos: la venta referencia…» → «la venta referencia…». Un lote
/// que el servidor no aceptó trae el cuerpo de la respuesta: se toma su `detail`.
String _motivo(String? error) {
  if (error == null || error.trim().isEmpty) return 'El servidor no dijo por qué.';
  var texto = error.replaceFirst(RegExp(r'^[a-z_]+: '), '');
  if (texto.startsWith('{')) {
    try {
      final detalle = (jsonDecode(texto) as Map)['detail'];
      if (detalle is String) texto = detalle;
    } catch (_) {
      // Se queda como vino: mejor el texto crudo que nada.
    }
  }
  return texto;
}
