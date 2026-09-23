/// Reglas de crédito en el dispositivo.
///
/// Espejo exacto de `server/app/domain/credito.py`. Las dos implementaciones
/// existen a propósito: **aquí es donde bloquear sirve**, porque la mercancía
/// todavía no sale del camión. El servidor, cuando recibe la venta, solo puede
/// marcarla para revisión — el cliente ya tiene su remisión impresa.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LA TRAMPA DEL OFFLINE
/// ─────────────────────────────────────────────────────────────────────────
/// El saldo que trae el teléfono puede tener horas. Si el cálculo usara solo
/// ese número, el vendedor podría hacer cinco ventas a crédito en la misma
/// mañana —cada una por debajo del límite— y dejar al cliente al triple de su
/// línea, porque ninguna alcanzó a sincronizar.
///
///     saldoEfectivo = saldoConfirmado + cargosPendientes - abonosPendientes
///
/// Los abonos cuentan igual de rápido que los cargos: si el vendedor acaba de
/// cobrarle en efectivo, la línea se libera en ese momento.
library;

import 'dinero.dart';

enum MotivoCredito {
  ok('ok'),
  contadoSiemprePermitido('contado_siempre_permitido'),
  sinLineaDeCredito('sin_linea_de_credito'),
  clienteBloqueado('cliente_bloqueado'),
  excedeLimite('excede_limite');

  const MotivoCredito(this.codigo);

  /// El mismo código que usa el servidor: viaja en la respuesta del endpoint
  /// de evaluación y se compara en las pruebas de paridad.
  final String codigo;
}

class EstadoCredito {
  const EstadoCredito({
    required this.limite,
    required this.saldoConfirmado,
    this.permiteCredito = true,
    this.bloqueado = false,
    this.cargosPendientes = Dinero.cero,
    this.abonosPendientes = Dinero.cero,
  });

  final Dinero limite;

  /// Lo último que dijo el servidor.
  final Dinero saldoConfirmado;
  final bool permiteCredito;
  final bool bloqueado;

  /// Ventas a crédito de este equipo que aún no sincronizan.
  final Dinero cargosPendientes;

  /// Cobros de este equipo que aún no sincronizan. Liberan línea de inmediato.
  final Dinero abonosPendientes;

  /// Puede quedar negativo: es saldo a favor, y suma línea disponible.
  Dinero get saldoEfectivo => saldoConfirmado + cargosPendientes - abonosPendientes;

  /// Nunca negativo: un cliente pasado de su límite tiene cero disponible, no
  /// una deuda de línea que confundiría al vendedor en pantalla.
  Dinero get disponible => (limite - saldoEfectivo).maximoConCero;
}

class ResultadoCredito {
  const ResultadoCredito({
    required this.permitida,
    required this.motivo,
    required this.disponible,
    this.excedente = Dinero.cero,
  });

  final bool permitida;
  final MotivoCredito motivo;
  final Dinero disponible;

  /// Cuánto le falta abonar. Es lo que se le dice al vendedor en pantalla.
  final Dinero excedente;
}

/// Decide si la venta procede.
///
/// El contado nunca se bloquea: da igual cuánto deba el cliente, si paga en
/// efectivo la venta entra. Negarla no cobra la deuda vieja y sí pierde la
/// venta nueva.
ResultadoCredito evaluarVenta(
  EstadoCredito estado,
  Dinero total, {
  required bool aCredito,
}) {
  if (total.esNegativo) {
    throw ArgumentError('el total no puede ser negativo: $total');
  }

  if (!aCredito) {
    return ResultadoCredito(
      permitida: true,
      motivo: MotivoCredito.contadoSiemprePermitido,
      disponible: estado.disponible,
    );
  }

  if (estado.bloqueado) {
    return ResultadoCredito(
      permitida: false,
      motivo: MotivoCredito.clienteBloqueado,
      disponible: estado.disponible,
    );
  }

  if (!estado.permiteCredito) {
    return ResultadoCredito(
      permitida: false,
      motivo: MotivoCredito.sinLineaDeCredito,
      disponible: estado.disponible,
    );
  }

  final nuevoSaldo = estado.saldoEfectivo + total;
  if (nuevoSaldo > estado.limite) {
    return ResultadoCredito(
      permitida: false,
      motivo: MotivoCredito.excedeLimite,
      disponible: estado.disponible,
      excedente: nuevoSaldo - estado.limite,
    );
  }

  return ResultadoCredito(
    permitida: true,
    motivo: MotivoCredito.ok,
    disponible: estado.disponible - total,
  );
}
