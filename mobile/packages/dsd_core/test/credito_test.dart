/// Reglas de crédito en el dispositivo.
///
/// Los casos son deliberadamente los MISMOS que `server/tests/test_credito.py`.
/// Dos implementaciones de una regla de negocio solo sirven si se puede
/// demostrar que coinciden.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

EstadoCredito estado({
  String limite = '5000.00',
  String saldo = '0.00',
  bool permiteCredito = true,
  bool bloqueado = false,
  String cargos = '0.00',
  String abonos = '0.00',
}) =>
    EstadoCredito(
      limite: Dinero.deTexto(limite),
      saldoConfirmado: Dinero.deTexto(saldo),
      permiteCredito: permiteCredito,
      bloqueado: bloqueado,
      cargosPendientes: Dinero.deTexto(cargos),
      abonosPendientes: Dinero.deTexto(abonos),
    );

void main() {
  group('Dinero exacto', () {
    test('la suma de centavos no pierde nada', () {
      // 0.1 + 0.2 en double da 0.30000000000000004.
      final suma = Dinero.deTexto('0.10') + Dinero.deTexto('0.20');
      expect(suma.texto, equals('0.30'));
    });

    test('cien sumas de un centavo dan exactamente un peso', () {
      var total = Dinero.cero;
      for (var i = 0; i < 100; i++) {
        total = total + Dinero.deTexto('0.01');
      }
      expect(total.texto, equals('1.00'));
    });

    test('ida y vuelta por texto', () {
      for (final t in ['0.00', '250.00', '-125.50', '1234567.89']) {
        expect(Dinero.deTexto(t).texto, equals(t));
      }
    });

    test('rechaza formatos que el contrato no permite', () {
      for (final malo in ['250', '250.5', '250.000', '1,250.00', '+250.00', '250.00 ']) {
        expect(() => Dinero.deTexto(malo), throwsFormatException, reason: malo);
      }
    });
  });

  group('regla de crédito', () {
    test('dentro del límite pasa', () {
      final r = evaluarVenta(estado(saldo: '1000.00'), Dinero.deTexto('500.00'),
          aCredito: true);
      expect(r.permitida, isTrue);
      expect(r.motivo, equals(MotivoCredito.ok));
      expect(r.disponible.texto, equals('3500.00'));
    });

    test('excederlo lo bloquea y reporta cuánto falta abonar', () {
      final r = evaluarVenta(estado(saldo: '4800.00'), Dinero.deTexto('500.00'),
          aCredito: true);
      expect(r.permitida, isFalse);
      expect(r.motivo, equals(MotivoCredito.excedeLimite));
      expect(r.excedente.texto, equals('300.00'));
    });

    test('justo en el límite pasa', () {
      final r = evaluarVenta(estado(saldo: '4500.00'), Dinero.deTexto('500.00'),
          aCredito: true);
      expect(r.permitida, isTrue);
      expect(r.disponible, equals(Dinero.cero));
    });

    test('un centavo arriba no pasa', () {
      expect(
        evaluarVenta(estado(saldo: '4500.00'), Dinero.deTexto('500.01'), aCredito: true)
            .permitida,
        isFalse,
      );
    });

    test('el contado nunca se bloquea', () {
      final sinLinea = estado(
        limite: '100.00',
        saldo: '99999.00',
        permiteCredito: false,
        bloqueado: true,
      );
      final r = evaluarVenta(sinLinea, Dinero.deTexto('500.00'), aCredito: false);
      expect(r.permitida, isTrue);
      expect(r.motivo, equals(MotivoCredito.contadoSiemprePermitido));
    });

    test('cliente bloqueado no compra a crédito', () {
      expect(
        evaluarVenta(estado(bloqueado: true), Dinero.deTexto('10.00'), aCredito: true).motivo,
        equals(MotivoCredito.clienteBloqueado),
      );
    });

    test('cliente sin línea no compra a crédito', () {
      expect(
        evaluarVenta(estado(permiteCredito: false), Dinero.deTexto('10.00'),
                aCredito: true)
            .motivo,
        equals(MotivoCredito.sinLineaDeCredito),
      );
    });

    test('el bloqueo manual gana sobre el límite', () {
      expect(
        evaluarVenta(estado(saldo: '9999.00', bloqueado: true), Dinero.deTexto('10.00'),
                aCredito: true)
            .motivo,
        equals(MotivoCredito.clienteBloqueado),
      );
    });
  });

  group('la trampa del offline', () {
    test('ventas encadenadas sin sincronizar', () {
      // Cinco ventas de la mañana, cada una por debajo del límite.
      final primera = evaluarVenta(
        estado(saldo: '4000.00'),
        Dinero.deTexto('800.00'),
        aCredito: true,
      );
      expect(primera.permitida, isTrue);

      final segunda = evaluarVenta(
        estado(saldo: '4000.00', cargos: '800.00'),
        Dinero.deTexto('800.00'),
        aCredito: true,
      );
      expect(segunda.permitida, isFalse);
      expect(segunda.excedente.texto, equals('600.00'));
    });

    test('un abono sin sincronizar libera línea de inmediato', () {
      expect(
        evaluarVenta(estado(saldo: '4800.00'), Dinero.deTexto('500.00'), aCredito: true)
            .permitida,
        isFalse,
      );
      expect(
        evaluarVenta(estado(saldo: '4800.00', abonos: '1000.00'),
                Dinero.deTexto('500.00'), aCredito: true)
            .permitida,
        isTrue,
      );
    });

    test('saldo a favor suma línea', () {
      final e = estado(abonos: '500.00');
      expect(e.saldoEfectivo.texto, equals('-500.00'));
      expect(e.disponible.texto, equals('5500.00'));
    });

    test('disponible nunca es negativo', () {
      expect(estado(saldo: '9000.00').disponible, equals(Dinero.cero));
    });
  });
}
