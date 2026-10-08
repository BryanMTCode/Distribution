/// Cómo paga el cliente. Siempre en el acto: la operación es de contado
/// (ADR 0002 §81).
///
/// El efectivo está en la mano y lo cuenta el arqueo del corte. La transferencia
/// llega al banco: no viene en la bolsa del vendedor, y la oficina la confirma
/// contra el estado de cuenta solo para cuadrar el dinero.
library;

enum FormaDePago {
  efectivo('efectivo', 'Efectivo'),
  transferencia('transferencia', 'Transferencia');

  const FormaDePago(this.codigo, this.etiqueta);

  /// Como viaja al servidor y como se guarda en la base local.
  final String codigo;
  final String etiqueta;

  /// Solo el efectivo entra al arqueo del corte del día.
  bool get entraAlArqueo => this == FormaDePago.efectivo;

  /// Lo que no se reconoce es efectivo: es lo que hacía la app antes de que la
  /// venta guardara su forma de pago.
  static FormaDePago deCodigo(String? codigo) =>
      codigo == FormaDePago.transferencia.codigo ? transferencia : efectivo;
}
