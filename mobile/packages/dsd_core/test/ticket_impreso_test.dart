/// El ticket del corte, también en papel (ADR 0002 §133).
///
/// El mismo texto que va por WhatsApp sale en la impresora: sus negritas en
/// negritas, sin asteriscos ni viñetas que la tabla de la impresora no tiene,
/// nada más ancho que el papel, y papel al final para arrancarlo.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

void main() {
  final ticket = textoDelCorte(TicketDeCorte(
    id: '0b6f2c8e-1111-4222-8333-944455556666',
    vendedor: 'Juan Pérez',
    codigoVendedor: 'VEND01',
    fechaOperativa: '2026-10-19',
    momento: DateTime.utc(2026, 10, 20, 1, 30),
    ventas: 3,
    efectivoVendido: Dinero.deTexto('2250.00'),
    transferencias: Dinero.cero,
    efectivoEntregado: Dinero.deTexto('2250.00'),
    queda: [
      RenglonDeTicket(
        nombre: 'Sobre Minino salmón a la plancha 85gr con un nombre muy largo',
        cantidadBase: Cantidad.deTexto('60.000'),
        unidadBase: 'PZA',
      ),
    ],
  ));

  test('sale lo mismo que el mensaje, con sus negritas y a lo ancho del papel', () {
    final vista = decodificar(escPosDelTicket(ticket));
    final textos = vista.lineas.map((l) => l.texto).toList();
    expect(textos.first, 'DISTRIBUCIONES SE');
    // La tabla de la impresora no trae «Í»: sale «I», como en la remisión.
    final corte = vista.lineas.firstWhere((l) => l.texto == 'CORTE DEL DIA');
    expect(corte.negrita, isTrue);
    expect(vista.lineas.firstWhere((l) => l.texto.contains('Juan')).negrita, isFalse);
    expect(textos, contains(r'$2,250.00'));
    expect(textos.join('\n'), isNot(contains('*')));
    expect(textos.join('\n'), isNot(contains('•')));
    expect(textos.where((t) => t.startsWith('- Sobre Minino')), hasLength(1));
    for (final l in vista.lineas) {
      expect(l.columnasOcupadas, lessThanOrEqualTo(columnas58mm), reason: l.texto);
    }
    expect(vista.desconocidos, isEmpty);
  });
}
