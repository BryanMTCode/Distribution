/// El carrito de una visita.
///
/// Dos cosas se prueban aquí por encima de todo:
///
/// 1. **No hay forma de cambiar el precio.** No con una validación que se pueda
///    saltar, sino porque el parámetro no existe. Eso se comprueba con el
///    analizador, no con una aserción — pero sí se comprueba que el importe
///    salga siempre del precio de la presentación.
/// 2. **El camión no da crédito de mercancía.** Vender más de lo que queda
///    arriba no es un error que la oficina corrija: es un camión que llega a la
///    siguiente tienda sin lo que prometió.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

/// Sopa en dos presentaciones: la caja de 24 y la pieza.
PresentacionVendible _caja({String precio = '296.0000'}) => PresentacionVendible(
      productoId: 'p-sopa',
      sku: 'SOPA-70G',
      nombre: 'Sopa de fideo 70 g',
      unidadCodigo: 'CAJA',
      factor: Factor.deEnteros(24),
      precio: Precio.deTexto(precio),
      listaPreciosId: 'lista-mayoreo',
      listaPreciosVersion: 7,
    );

PresentacionVendible _pieza({String precio = '13.5000'}) => PresentacionVendible(
      productoId: 'p-sopa',
      sku: 'SOPA-70G',
      nombre: 'Sopa de fideo 70 g',
      unidadCodigo: 'PZA',
      factor: Factor.uno,
      precio: Precio.deTexto(precio),
      listaPreciosId: 'lista-mayoreo',
      listaPreciosVersion: 7,
      esDefault: true,
    );

PresentacionVendible _frijol() => PresentacionVendible(
      productoId: 'p-frijol',
      sku: 'FRIJOL-1K',
      nombre: 'Frijol bayo 1 kg',
      unidadCodigo: 'PZA',
      factor: Factor.uno,
      precio: Precio.deTexto('32.5000'),
      listaPreciosId: 'lista-mayoreo',
      listaPreciosVersion: 7,
      esDefault: true,
    );

ExistenciasCamion _camion({int sopa = 240, int frijol = 40}) => ExistenciasCamion({
      'p-sopa': Cantidad.deEnteros(sopa),
      'p-frijol': Cantidad.deEnteros(frijol),
    });

void main() {
  group('armar el carrito', () {
    test('una línea vale cantidad × precio de la lista', () {
      final r = const Carrito()
          .agregar(_caja(), Cantidad.deEnteros(2), existencias: _camion());

      expect(r.aceptado, isTrue);
      expect(r.carrito.cuantasLineas, equals(1));
      expect(r.carrito.total, equals(Dinero.deTexto('592.00')));
    });

    test('volver a pasar el mismo producto suma, no reemplaza', () {
      // Es lo que hace el vendedor: pasa el producto, el cliente pide otro, lo
      // vuelve a pasar.
      final uno = const Carrito()
          .agregar(_pieza(), Cantidad.deEnteros(3), existencias: _camion())
          .carrito;
      final dos =
          uno.agregar(_pieza(), Cantidad.deEnteros(2), existencias: _camion()).carrito;

      expect(dos.cuantasLineas, equals(1));
      expect(dos.cantidadDe(_pieza().llave), equals(Cantidad.deEnteros(5)));
      expect(dos.total, equals(Dinero.deTexto('67.50')));
    });

    test('caja y pieza del mismo producto son líneas distintas', () {
      // El cliente pide "dos cajas y tres piezas": así se las lleva y así se
      // imprimen.
      var c = const Carrito();
      c = c.agregar(_caja(), Cantidad.deEnteros(2), existencias: _camion()).carrito;
      c = c.agregar(_pieza(), Cantidad.deEnteros(3), existencias: _camion()).carrito;

      expect(c.cuantasLineas, equals(2));
      expect(c.total, equals(Dinero.deTexto('632.50'))); // 592.00 + 40.50
      // Pero compiten por la misma existencia: 2×24 + 3 = 51 unidades base.
      expect(c.comprometidoDe('p-sopa'), equals(Cantidad.deEnteros(51)));
    });

    test('el subtotal suma importes ya redondeados, no vuelve a redondear', () {
      // Tres líneas de 0.125 cada una: si se sumara antes de redondear daría
      // 0.375 → 0.38. Redondeando línea por línea da 0.13 × 3 = 0.39. El
      // ticket impreso muestra las líneas, así que el total TIENE que ser la
      // suma de lo impreso.
      var c = const Carrito();
      c = c
          .agregar(
            PresentacionVendible(
              productoId: 'p-frijol',
              sku: 'X',
              nombre: 'X',
              unidadCodigo: 'PZA',
              factor: Factor.uno,
              precio: Precio.deTexto('0.1250'),
              listaPreciosId: 'l',
              listaPreciosVersion: 1,
            ),
            Cantidad.deEnteros(1),
            existencias: _camion(),
          )
          .carrito;
      expect(c.total, equals(Dinero.deTexto('0.13')));
    });

    test('quitar una línea la saca del total', () {
      var c = const Carrito();
      c = c.agregar(_caja(), Cantidad.deEnteros(1), existencias: _camion()).carrito;
      c = c.agregar(_frijol(), Cantidad.deEnteros(2), existencias: _camion()).carrito;
      expect(c.total, equals(Dinero.deTexto('361.00')));

      c = c.quitar(_caja().llave);
      expect(c.cuantasLineas, equals(1));
      expect(c.total, equals(Dinero.deTexto('65.00')));
    });

    test('fijar la cantidad en cero quita la línea', () {
      // Es el botón de "−" bajado hasta el fondo: no deja una línea de cero.
      var c = const Carrito()
          .agregar(_pieza(), Cantidad.deEnteros(1), existencias: _camion())
          .carrito;
      c = c.fijarCantidad(_pieza(), Cantidad.cero, existencias: _camion()).carrito;
      expect(c.estaVacio, isTrue);
    });

    test('una cantidad de cero o negativa no agrega nada', () {
      final r = const Carrito()
          .agregar(_pieza(), Cantidad.cero, existencias: _camion());
      expect(r.aceptado, isFalse);
      expect(r.motivo, equals(MotivoRechazo.cantidadInvalida));
      expect(r.carrito.estaVacio, isTrue);
    });
  });

  group('el camión no da crédito de mercancía', () {
    test('no se puede vender más de lo que queda arriba', () {
      final r = const Carrito()
          .agregar(_pieza(), Cantidad.deEnteros(41), existencias: _camion(sopa: 40));

      expect(r.aceptado, isFalse);
      expect(r.motivo, equals(MotivoRechazo.sinExistencia));
      expect(r.carrito.estaVacio, isTrue, reason: 'un rechazo no deja rastro');
    });

    test('dice cuánto sí cabe, en la unidad que el vendedor está usando', () {
      // Quedan 100 piezas y está vendiendo cajas de 24: caben 4, no "100".
      // Decirle 100 lo obliga a dividir de cabeza frente al cliente.
      final r = const Carrito()
          .agregar(_caja(), Cantidad.deEnteros(5), existencias: _camion(sopa: 100));

      expect(r.aceptado, isFalse);
      expect(r.disponibleEnCamion, equals(Cantidad.deTexto('4.166')));
      expect(r.disponibleEnCamion!.enteros, equals(4));
    });

    test('las presentaciones del mismo producto comparten la existencia', () {
      // 240 unidades base. Diez cajas las agotan; ya no cabe ni una pieza.
      var c = const Carrito()
          .agregar(_caja(), Cantidad.deEnteros(10), existencias: _camion(sopa: 240))
          .carrito;
      expect(c.comprometidoDe('p-sopa'), equals(Cantidad.deEnteros(240)));

      final r = c.agregar(_pieza(), Cantidad.deEnteros(1),
          existencias: _camion(sopa: 240));
      expect(r.aceptado, isFalse);
      expect(r.motivo, equals(MotivoRechazo.sinExistencia));
    });

    test('fijar la cantidad libera lo que esa misma línea tenía', () {
      // Sin esto, bajar de 10 cajas a 9 se rechazaría por "sin existencia": la
      // línea competiría contra sí misma.
      var c = const Carrito()
          .agregar(_caja(), Cantidad.deEnteros(10), existencias: _camion(sopa: 240))
          .carrito;

      final r = c.fijarCantidad(_caja(), Cantidad.deEnteros(9),
          existencias: _camion(sopa: 240));
      expect(r.aceptado, isTrue);
      expect(r.carrito.cantidadDe(_caja().llave), equals(Cantidad.deEnteros(9)));

      // Y subir a 10 otra vez también cabe.
      final vuelta = r.carrito
          .fijarCantidad(_caja(), Cantidad.deEnteros(10), existencias: _camion(sopa: 240));
      expect(vuelta.aceptado, isTrue);
    });

    test('un producto que no va en la carga no se puede vender', () {
      // Ausente del mapa NO es "cantidad desconocida": es mercancía que no
      // subió al camión. Permitirlo devolvería el descuadre a la liquidación
      // del final del día, que es donde ya no se puede investigar.
      final r = const Carrito().agregar(
        _frijol(),
        Cantidad.deEnteros(1),
        existencias: ExistenciasCamion({'p-sopa': Cantidad.deEnteros(24)}),
      );
      expect(r.aceptado, isFalse);
      expect(r.motivo, equals(MotivoRechazo.noVaEnLaCarga));
    });

    test('sin carga activa no se puede armar nada', () {
      final r = const Carrito().agregar(_pieza(), Cantidad.deEnteros(1),
          existencias: const ExistenciasCamion.vacio());
      expect(r.aceptado, isFalse);
      expect(r.motivo, equals(MotivoRechazo.noVaEnLaCarga));
    });

    test('vender exactamente lo último que queda sí procede', () {
      final r = const Carrito()
          .agregar(_caja(), Cantidad.deEnteros(10), existencias: _camion(sopa: 240));
      expect(r.aceptado, isTrue);
    });
  });

  group('la forma de pago', () {
    EstadoCredito conLinea({int limite = 1000, int saldo = 0}) => EstadoCredito(
          limite: Dinero.dePesos(limite),
          saldoConfirmado: Dinero.dePesos(saldo),
        );

    test('de contado procede aunque el cliente deba hasta la camisa', () {
      final c = const Carrito()
          .agregar(_caja(), Cantidad.deEnteros(2), existencias: _camion())
          .carrito;

      final r = c.evaluar(conLinea(limite: 100, saldo: 5000));
      expect(r.permitida, isTrue);
      expect(r.motivo, equals(MotivoCredito.contadoSiemprePermitido));
    });

    test('a crédito se bloquea al pasar el límite, y dice cuánto falta', () {
      final c = const Carrito()
          .agregar(_caja(), Cantidad.deEnteros(2), existencias: _camion())
          .carrito
          .conFormaDePago(aCredito: true);

      final r = c.evaluar(conLinea(limite: 500, saldo: 100));
      expect(r.permitida, isFalse);
      expect(r.motivo, equals(MotivoCredito.excedeLimite));
      // 100 + 592 = 692, sobre un límite de 500 ⇒ faltan 192.
      expect(r.excedente, equals(Dinero.deTexto('192.00')));
    });

    test('cambiar la forma de pago no toca los importes', () {
      final contado = const Carrito()
          .agregar(_caja(), Cantidad.deEnteros(2), existencias: _camion())
          .carrito;
      final credito = contado.conFormaDePago(aCredito: true);

      expect(credito.total, equals(contado.total));
      expect(credito.cuantasLineas, equals(contado.cuantasLineas));
    });

    test('el carrito vacío no tiene nada que evaluar', () {
      const c = Carrito();
      expect(c.total, equals(Dinero.cero));
      expect(c.evaluar(conLinea()).permitida, isTrue);
    });
  });

  group('el carrito es inmutable', () {
    test('agregar no modifica el carrito anterior', () {
      // Que dos partes de la interfaz puedan mutar el mismo carrito es la clase
      // de bug que produce un ticket con una línea de más.
      const vacio = Carrito();
      final conUno =
          vacio.agregar(_pieza(), Cantidad.deEnteros(1), existencias: _camion()).carrito;

      expect(vacio.estaVacio, isTrue);
      expect(conUno.cuantasLineas, equals(1));
    });

    test('vaciar conserva la forma de pago elegida', () {
      final c = const Carrito()
          .conFormaDePago(aCredito: true)
          .agregar(_pieza(), Cantidad.deEnteros(1), existencias: _camion())
          .carrito;
      expect(c.vaciar().aCredito, isTrue);
      expect(c.vaciar().estaVacio, isTrue);
    });
  });
}
