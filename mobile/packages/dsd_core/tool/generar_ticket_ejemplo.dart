/// Genera `contracts/ticket_58mm_ejemplo.txt`.
///
/// La impresora está en otro Estado. Sin este archivo, el diseño del ticket se
/// quedaría sin revisar hasta que llegue: nadie puede opinar de un arreglo de
/// 800 bytes.
///
/// El archivo se versiona, así que **cualquier cambio de diseño aparece en el
/// diff del repositorio** y se puede revisar como se revisa un documento. El CI
/// lo regenera y compara, igual que con los otros contratos.
///
/// Uso:  dart run tool/generar_ticket_ejemplo.dart
library;

import 'dart:io';

import 'package:dsd_core/dsd_core.dart';

const _negocio = DatosDelNegocio(
  nombre: 'Distribuidora El Ñandú',
  direccion: 'Av. Hidalgo 145, Col. Centro',
  telefono: '55 1234 5678',
  rfc: 'XAXX010101000',
  leyendaFinal: 'Aclaraciones al 55 1234 5678',
);

PresentacionVendible _presentacion({
  required String nombre,
  required String unidad,
  required int factor,
  required String precio,
}) =>
    PresentacionVendible(
      productoId: 'p-$nombre',
      sku: nombre.toUpperCase().replaceAll(' ', '-'),
      nombre: nombre,
      unidadCodigo: unidad,
      factor: Factor.deEnteros(factor),
      precio: Precio.deTexto(precio),
      listaPreciosId: 'lista-general',
      listaPreciosVersion: 7,
    );

VentaGuardada _venta({
  required bool aCredito,
  required List<LineaCarrito> lineas,
  int foliosRestantes = 376,
}) {
  final suma =
      lineas.fold(Dinero.cero, (acumulado, l) => acumulado + l.importe);
  return VentaGuardada(
    id: '019283e0-0001-7000-8000-000000000001',
    folioConsecutivo: 124,
    folioLocal: 'VEND01-000124',
    visitaId: 'vis-1',
    clienteId: 'cli-1',
    aCredito: aCredito,
    subtotal: suma,
    total: suma,
    lineas: lineas,
    fechaDispositivo: '2026-09-29T17:42:03.250Z',
    fechaOperativa: '2026-09-29',
    foliosRestantes: foliosRestantes,
  );
}

/// Los tres casos que hay que poder revisar a ojo.
List<(String, List<int>)> _casos() {
  // Una venta de contado, mezclando caja y pieza, con el precio de 4 decimales
  // que hace que 24 piezas valgan 296.00 y no 295.92.
  final contado = _venta(
    aCredito: false,
    lineas: [
      LineaCarrito(
        presentacion: _presentacion(
          nombre: 'Sopa de fideo 70 g',
          unidad: 'CAJA',
          factor: 24,
          precio: '296.0000',
        ),
        cantidad: Cantidad.deEnteros(2),
      ),
      LineaCarrito(
        presentacion: _presentacion(
          nombre: 'Sopa de fideo 70 g',
          unidad: 'PZA',
          factor: 1,
          precio: '12.3333',
        ),
        cantidad: Cantidad.deEnteros(3),
      ),
      LineaCarrito(
        presentacion: _presentacion(
          nombre: 'Frijol bayo 1 kg',
          unidad: 'PZA',
          factor: 1,
          precio: '32.5000',
        ),
        cantidad: Cantidad.deEnteros(6),
      ),
      // El caso que pone a prueba el ancho: un nombre que no cabe en una línea.
      LineaCarrito(
        presentacion: _presentacion(
          nombre: 'Refresco de cola 600 ml no retornable presentación familiar',
          unidad: 'CAJA',
          factor: 12,
          precio: '198.5000',
        ),
        cantidad: Cantidad.deEnteros(1),
      ),
    ],
  );

  final credito = _venta(
    aCredito: true,
    lineas: [
      LineaCarrito(
        presentacion: _presentacion(
          nombre: 'Aceite vegetal 900 ml',
          unidad: 'CAJA',
          factor: 12,
          precio: '445.5000',
        ),
        cantidad: Cantidad.deEnteros(3),
      ),
    ],
  );

  const visita = DatosDeLaVisita(
    // Con emoji a propósito: es lo que hay en los datos reales, y el papel tiene
    // que salir limpio.
    nombreCliente: 'La Esquina de Ñoño 🏪',
    nombreVendedor: 'Juan Pérez',
    codigoCliente: 'C-001',
    direccionCliente: 'Morelos 22, Col. Centro',
  );

  final original =
      ticketDeVenta(contado, negocio: _negocio, visita: visita);

  return [
    ('CONTADO — cuatro renglones, uno con nombre largo', original),
    (
      'CREDITO — con firma de recibido y sin saldo impreso',
      ticketDeVenta(credito, negocio: _negocio, visita: visita)
    ),
    (
      'REIMPRESION — los bytes del original, con aviso de copia',
      reimpresion(original, 1)
    ),
  ];
}

void main() {
  final salida = StringBuffer()
    ..writeln('TICKET DE REMISIÓN — 58 mm, 32 columnas, PC437')
    ..writeln('=' * 60)
    ..writeln()
    ..writeln('GENERADO — no editar a mano.')
    ..writeln('Fuente:     mobile/packages/dsd_core/lib/src/ticket.dart')
    ..writeln('Regenerar:  dart run tool/generar_ticket_ejemplo.dart')
    ..writeln()
    ..writeln('Esto es lo que saldría del papel, decodificando los bytes')
    ..writeln('ESC/POS con las mismas reglas que aplica la impresora.')
    ..writeln('El marco marca el ancho real: una línea que lo rebase')
    ..writeln('lleva ">" en vez de "|" y es un defecto de diseño.')
    ..writeln();

  for (final (titulo, bytes) in _casos()) {
    final vista = decodificar(bytes);
    salida
      ..writeln('-' * 60)
      ..writeln(titulo)
      ..writeln('${bytes.length} bytes'
          '${vista.desconocidos.isEmpty ? '' : ' · COMANDOS DESCONOCIDOS: '
              '${vista.desconocidos.join(", ")}'}')
      ..writeln('-' * 60)
      ..writeln(vista.renderConMarco());
  }

  final archivo = File('../../../contracts/ticket_58mm_ejemplo.txt');
  archivo.writeAsStringSync(salida.toString());
  stdout.writeln('ticket de ejemplo → ${archivo.path}');
}
