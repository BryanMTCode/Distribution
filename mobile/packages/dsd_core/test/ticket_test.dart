/// El ticket de la remisión.
///
/// La impresora está en otro Estado, así que estas pruebas son la única
/// verificación del diseño hasta que llegue. Se afirman sobre la **vista
/// previa** —el texto tal como saldría en el papel— porque cuando algo se
/// desalinea, el diff de un texto se lee y el de 800 bytes no.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

const _negocio = DatosDelNegocio(
  nombre: 'Distribuidora El Ñandú',
  direccion: 'Av. Hidalgo 145, Centro',
  telefono: '55 1234 5678',
  leyendaFinal: 'Aclaraciones al 55 1234 5678',
);

const _visita = DatosDeLaVisita(
  nombreCliente: 'La Esquina de Ñoño 🏪',
  nombreVendedor: 'Juan Pérez',
  codigoCliente: 'C-001',
  direccionCliente: 'Morelos 22, Centro',
);

PresentacionVendible _caja({String precio = '296.0000', String? nombre}) =>
    PresentacionVendible(
      productoId: 'p-sopa',
      sku: 'SOPA-70G',
      nombre: nombre ?? 'Sopa de fideo 70 g',
      unidadCodigo: 'CAJA',
      factor: Factor.deEnteros(24),
      precio: Precio.deTexto(precio),
      listaPreciosId: 'l',
      listaPreciosVersion: 7,
    );

PresentacionVendible _pieza() => PresentacionVendible(
      productoId: 'p-sopa',
      sku: 'SOPA-70G',
      nombre: 'Sopa de fideo 70 g',
      unidadCodigo: 'PZA',
      factor: Factor.uno,
      precio: Precio.deTexto('12.3333'),
      listaPreciosId: 'l',
      listaPreciosVersion: 7,
    );

VentaGuardada _venta({
  FormaDePago forma = FormaDePago.efectivo,
  String? referencia,
  List<LineaCarrito>? lineas,
  Dinero? total,
  int foliosRestantes = 400,
}) {
  final renglones = lineas ??
      [
        LineaCarrito(presentacion: _caja(), cantidad: Cantidad.deEnteros(2)),
        LineaCarrito(presentacion: _pieza(), cantidad: Cantidad.deEnteros(3)),
      ];
  final suma = renglones.fold(
    Dinero.cero,
    (acumulado, l) => acumulado + l.importe,
  );
  return VentaGuardada(
    id: 'v-1',
    folioConsecutivo: 124,
    folioLocal: 'VEND01-000124',
    visitaId: 'vis-1',
    clienteId: 'cli-1',
    formaDePago: forma,
    referenciaPago: referencia,
    subtotal: suma,
    total: total ?? suma,
    lineas: renglones,
    fechaDispositivo: '2026-09-29T17:42:03.250Z',
    fechaOperativa: '2026-09-29',
    foliosRestantes: foliosRestantes,
  );
}

VistaPrevia _vista(VentaGuardada venta, {int copia = 0}) => decodificar(
      ticketDeVenta(venta, negocio: _negocio, visita: _visita, copia: copia),
    );

void main() {
  group('la forma del papel', () {
    test('ninguna línea se desborda, EN NINGUNA VARIANTE del ticket', () {
      // Una línea que se desborda no se corta: la impresora la continúa en la
      // siguiente y todo el ticket se corre. Es el defecto más fácil de
      // introducir y el más difícil de ver sin la impresora.
      //
      // Recorre todas las variantes: efectivo, transferencia con una clave de
      // rastreo larga, y sus copias.
      final transferencia = _venta(
        forma: FormaDePago.transferencia,
        referencia: 'BNET01002610080000123456789012',
      );
      final variantes = {
        'efectivo': _vista(_venta()),
        'transferencia': _vista(transferencia),
        'copia de efectivo': _vista(_venta(), copia: 1),
        'copia de transferencia': _vista(transferencia, copia: 3),
      };

      for (final entrada in variantes.entries) {
        for (final l in entrada.value.lineas) {
          expect(
            l.columnasOcupadas,
            lessThanOrEqualTo(32),
            reason: 'en ${entrada.key} se desborda: "${l.texto}"',
          );
        }
      }
    });

    test('empieza reiniciando la impresora y fijando la tabla', () {
      // Sin ESC @ el ticket hereda el estado del anterior; sin ESC t los acentos
      // salen como le toque a la impresora.
      final bytes = ticketDeVenta(_venta(), negocio: _negocio, visita: _visita);
      expect(bytes.sublist(0, 2), equals([0x1B, 0x40]));
      expect(_vista(_venta()).tabla?.nombre, equals('PC437'));
    });

    test('no emite ningún comando que la vista previa no entienda', () {
      // Si el generador emitiera algo que el decodificador no conoce, la vista
      // previa dejaría de corresponder al papel y nadie se enteraría.
      expect(_vista(_venta()).desconocidos, isEmpty);
    });

    test('termina con papel de sobra para arrancarlo', () {
      // Las móviles de 58 mm no traen cortador: si el ticket termina al ras, el
      // vendedor rompe el papel encima de la última línea.
      final vista = _vista(_venta());
      final ultimas = vista.lineas.reversed.take(4);
      expect(ultimas.every((l) => l.texto.isEmpty), isTrue);
    });
  });

  group('lo que el cliente necesita leer', () {
    test('el folio está, y en tamaño doble', () {
      // Es lo que dicta por teléfono cuando algo se aclara.
      final vista = _vista(_venta());
      final folio =
          vista.lineas.firstWhere((l) => l.texto.contains('VEND01-000124'));
      expect(folio.anchoDoble, isTrue);
      expect(folio.alineacion, equals(Alineacion.centro));
    });

    test('cada renglón trae su aritmética completa', () {
      // El cliente revisa la multiplicación con el dedo. Si no está escrita, no
      // puede.
      final texto = _vista(_venta()).texto;
      expect(texto, contains('2 CAJA x 296.00'));
      expect(texto, contains('592.00'));
      expect(texto, contains('3 PZA x 12.3333'));
      expect(texto, contains('37.00'));
    });

    test('el precio de 4 decimales se imprime con sus 4 decimales', () {
      // Imprimir 12.33 donde el precio es 12.3333 haría que el cliente calcule
      // 3 × 12.33 = 36.99 y reclame un centavo que sí le corresponde... al
      // revés. El papel tiene que permitir reproducir el importe.
      expect(_vista(_venta()).texto, contains('12.3333'));
    });

    test('el total va en tamaño doble y a la derecha', () {
      final vista = _vista(_venta());
      final total = vista.lineas.firstWhere((l) => l.texto.startsWith('TOTAL'));
      expect(total.anchoDoble, isTrue);
      expect(total.altoDoble, isTrue);
      expect(total.texto, contains('629.00'));
    });

    test('dice que NO es un documento fiscal', () {
      // Lo declara el papel para que nadie la presente como comprobante fiscal
      // (ADR 0002 §4).
      expect(_vista(_venta()).texto, contains('DOCUMENTO NO FISCAL'));
      expect(_vista(_venta()).texto.toLowerCase(), isNot(contains('factura')));
    });

    test('el emoji del nombre del cliente no ensucia el papel', () {
      // "La Esquina de Ñoño 🏪" está en los datos reales. El emoji se descarta;
      // la ñ se imprime.
      final texto = _vista(_venta()).texto;
      expect(texto, contains('La Esquina de Ñoño'));
      expect(texto, isNot(contains('🏪')));
      // Y no quedó ningún carácter de relleno del decodificador, que es como se
      // vería un byte sin traducción.
      expect(texto, isNot(contains('·')));
    });

    test('la fecha se lee como la lee una persona', () {
      expect(_vista(_venta()).texto, contains('29/09/2026'));
    });
  });

  group('la forma de pago', () {
    test('en efectivo lo dice y no pide firma', () {
      final texto = _vista(_venta()).texto;
      expect(texto, contains('EFECTIVO'));
      expect(texto, isNot(contains('Recibi conforme')));
    });

    test('por transferencia lo dice, con su referencia completa', () {
      // La oficina la busca en el estado de cuenta: cortada no sirve.
      final texto = _vista(
        _venta(forma: FormaDePago.transferencia, referencia: 'SPEI 4471'),
      ).texto;
      expect(texto, contains('TRANSFERENCIA'));
      expect(texto, contains('Ref. SPEI 4471'));
    });

    test('no hay saldo ni crédito en el papel: todo es de contado', () {
      final texto = _vista(_venta()).texto.toLowerCase();
      expect(texto, isNot(contains('saldo')));
      expect(texto, isNot(contains('credito')));
    });

    test('el subtotal solo aparece si difiere del total', () {
      // Con cero descuentos e IVA incluido, "Subtotal 629.00 / Total 629.00" es
      // ruido que hace dudar al cliente de si le cobraron algo extra.
      expect(_vista(_venta()).texto, isNot(contains('Subtotal')));

      final conDescuento = _venta(total: Dinero.deTexto('600.00'));
      expect(_vista(conDescuento).texto, contains('Subtotal'));
    });
  });

  group('las reimpresiones', () {
    test('la copia se distingue a simple vista', () {
      // Sin esto, alguien cobra dos veces con el mismo papel.
      final vista = _vista(_venta(), copia: 1);
      expect(vista.texto, contains('COPIA 1'));
    });

    test('el original no dice nada de copia', () {
      expect(_vista(_venta()).texto, isNot(contains('COPIA')));
    });

    test('la reimpresión reusa los bytes originales y les pone el aviso', () {
      // Las dos propiedades a la vez: el papel dice EXACTAMENTE lo mismo que el
      // que firmó el cliente —no se recalcula nada— y además se distingue.
      final original = ticketDeVenta(
        _venta(),
        negocio: _negocio,
        visita: _visita,
      );
      final copia = reimpresion(original, 2);

      // Los bytes del original están contenidos, intactos, al final.
      expect(
        copia.sublist(copia.length - original.length),
        equals(original),
      );
      final vista = decodificar(copia);
      expect(vista.texto, contains('COPIA 2'));
      expect(vista.texto, contains('No es el original'));
      expect(vista.texto, contains('VEND01-000124'));
    });
  });

  group('los casos que rompen el diseño', () {
    test('un nombre de producto larguísimo se parte, no se desborda', () {
      final venta = _venta(
        lineas: [
          LineaCarrito(
            presentacion: _caja(
              nombre: 'Refresco de cola sabor original 600 ml '
                  'no retornable presentación familiar',
            ),
            cantidad: Cantidad.deEnteros(1),
          ),
        ],
      );
      final vista = _vista(venta);
      for (final l in vista.lineas) {
        expect(l.columnasOcupadas, lessThanOrEqualTo(32),
            reason: 'se desborda: "${l.texto}"');
      }
      // Y el nombre completo sigue ahí, repartido en líneas.
      expect(
        vista.texto.replaceAll('\n', ' '),
        contains('Refresco de cola sabor original'),
      );
    });

    test('un importe de seis cifras cabe con su etiqueta', () {
      final venta = _venta(
        lineas: [
          LineaCarrito(
            presentacion: _caja(precio: '99999.0000'),
            cantidad: Cantidad.deEnteros(9),
          ),
        ],
      );
      final vista = _vista(venta);
      expect(vista.texto, contains('899991.00'));
      for (final l in vista.lineas) {
        expect(l.columnasOcupadas, lessThanOrEqualTo(32));
      }
    });

    test('una venta de un solo renglón no deja el papel deforme', () {
      final venta = _venta(
        lineas: [
          LineaCarrito(presentacion: _pieza(), cantidad: Cantidad.deEnteros(1)),
        ],
      );
      final vista = _vista(venta);
      expect(vista.texto, contains('12.33'));
      expect(vista.desconocidos, isEmpty);
    });

    test('quince renglones siguen dentro del ancho', () {
      // Un pedido grande de mercado. Lo que se vigila no es el largo —el papel
      // es continuo— sino que ningún renglón se desalinee.
      final venta = _venta(
        lineas: [
          for (var i = 0; i < 15; i++)
            LineaCarrito(
              presentacion: _caja(nombre: 'Producto número ${i + 1}'),
              cantidad: Cantidad.deEnteros(i + 1),
            ),
        ],
      );
      for (final l in _vista(venta).lineas) {
        expect(l.columnasOcupadas, lessThanOrEqualTo(32),
            reason: 'se desborda: "${l.texto}"');
      }
    });

    test('en 80 mm el ticket usa el ancho completo', () {
      // El día que la oficina imprima una copia en una de escritorio.
      final bytes = ticketDeVenta(
        _venta(),
        negocio: _negocio,
        visita: _visita,
        columnas: columnas80mm,
      );
      final vista = decodificar(bytes, columnas: columnas80mm);
      final separador =
          vista.lineas.firstWhere((l) => l.texto.startsWith('---'));
      expect(separador.texto.length, equals(48));
    });

    test('con PC850 los acentos mayúsculos se conservan', () {
      const negocioConMayusculas = DatosDelNegocio(nombre: 'DISTRIBUIDORA ÑANDÚ');
      final bytes = ticketDeVenta(
        _venta(),
        negocio: negocioConMayusculas,
        visita: _visita,
        tabla: TablaDeCodigos.pc850,
      );
      expect(decodificar(bytes).texto, contains('ÑANDÚ'));

      // Con PC437, la Ú se degrada a U — legible en cualquier impresora.
      final bytes437 = ticketDeVenta(
        _venta(),
        negocio: negocioConMayusculas,
        visita: _visita,
      );
      expect(decodificar(bytes437).texto, contains('ÑANDU'));
    });
  });
}
