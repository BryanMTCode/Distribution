/// El ticket de la remisión: el papel que se queda el cliente.
///
/// ─────────────────────────────────────────────────────────────────────────
/// ESTE PAPEL ES LA ÚNICA PRUEBA QUE TIENE EL CLIENTE
/// ─────────────────────────────────────────────────────────────────────────
/// Cuando la venta se sincroniza, la oficina tiene el registro. Hasta entonces
/// —y pueden pasar horas— **lo único que existe es este papel**. Si un importe
/// sale desalineado o un nombre cortado a media palabra, la aclaración se hace
/// por teléfono contra un ticket que nadie puede leer.
///
/// Por eso el diseño sigue tres reglas:
///
/// 1. **El folio, grande y arriba.** Es lo que el cliente dicta por teléfono.
/// 2. **Cada renglón con su aritmética completa:** cantidad × precio = importe.
///    El cliente revisa la multiplicación, y si no está escrita no puede.
/// 3. **El total en tamaño doble.** Es el número que se mira primero.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LO QUE NO SE IMPRIME, Y POR QUÉ
/// ─────────────────────────────────────────────────────────────────────────
/// **El saldo del cliente no va en el ticket.** El saldo que trae el teléfono
/// puede tener horas (§0.3: "tiempo real es tiempo real de lo sincronizado").
/// Imprimir un saldo viejo en un papel que el cliente conserva es crear una
/// disputa: él va a sostener el número impreso y la oficina el suyo. Se imprime
/// lo que SÍ es un hecho de esta venta —su importe y que fue a crédito— y la
/// consulta del saldo queda donde se puede actualizar.
///
/// **No dice "factura" en ningún lado.** Es una remisión no fiscal (ADR 0002
/// §4), y el papel lo declara para que nadie la presente como comprobante
/// fiscal.
library;

import 'dinero.dart';
import 'escpos.dart';
import 'venta.dart';

/// Los datos del negocio que van en el encabezado.
///
/// Vienen de la configuración, no del código: el mismo binario sirve para otra
/// distribuidora cambiando estos campos.
class DatosDelNegocio {
  const DatosDelNegocio({
    required this.nombre,
    this.direccion,
    this.telefono,
    this.rfc,
    this.leyendaFinal,
  });

  final String nombre;
  final String? direccion;
  final String? telefono;
  final String? rfc;

  /// Lo que se imprime al pie, después del aviso no fiscal. Sirve para el
  /// horario de atención o el teléfono de aclaraciones.
  final String? leyendaFinal;
}

/// Quién atendió y a quién.
class DatosDeLaVisita {
  const DatosDeLaVisita({
    required this.nombreCliente,
    required this.nombreVendedor,
    this.codigoCliente,
    this.direccionCliente,
  });

  final String nombreCliente;
  final String nombreVendedor;
  final String? codigoCliente;
  final String? direccionCliente;
}

/// Arma el ticket de una venta.
///
/// [copia] es el número de reimpresión: 0 es el original, 1 en adelante son
/// copias. El contenido es **el mismo** —los mismos importes, el mismo folio—;
/// lo único que cambia es el aviso de copia, para que nadie presente una
/// reimpresión como si fuera el original.
///
/// El ticket original se guarda en `ventas.ticket_escpos` y la reimpresión
/// **reusa esos bytes** en vez de recalcularlos: si el catálogo cambió entre la
/// venta y la reimpresión, recalcular daría un papel distinto del que firmó el
/// cliente. Ver `CierreDeVenta.marcarImpresa`.
List<int> ticketDeVenta(
  VentaGuardada venta, {
  required DatosDelNegocio negocio,
  required DatosDeLaVisita visita,
  int columnas = columnas58mm,
  TablaDeCodigos tabla = TablaDeCodigos.pc437,
  int copia = 0,
}) {
  final t = ConstructorEscPos(columnas: columnas, tabla: tabla)..inicializar();

  _encabezado(t, negocio, copia: copia);
  _datosDeLaVenta(t, venta, visita);
  _partidas(t, venta);
  _totales(t, venta);
  _pie(t, venta, negocio);

  // Papel de sobra para arrancar sin romper el texto. Las impresoras móviles de
  // 58 mm no traen cortador: el vendedor jala el papel, y si el ticket termina
  // al ras se rompe encima de la última línea.
  t.avanzar(4);

  return t.bytes;
}

void _encabezado(ConstructorEscPos t, DatosDelNegocio negocio, {required int copia}) {
  t
    ..alinear(Alineacion.centro)
    ..negrita(true);

  // El nombre en tamaño doble **solo si cabe en una línea**. En 58 mm el tamaño
  // doble da 16 columnas, y "Distribuidora El Ñandú" son 22: partido en dos
  // renglones gigantes se lee peor que completo en tamaño normal. Se comprobó
  // mirando la vista previa, que es justo para lo que existe.
  if (ancho(negocio.nombre, tabla: t.tabla) <= columnas58mm ~/ 2) {
    t
      ..tamano(ancho: 2, alto: 2)
      ..linea(negocio.nombre)
      ..tamano();
  } else {
    t.parrafo(negocio.nombre);
  }

  t.negrita(false);

  if (negocio.direccion != null) t.parrafo(negocio.direccion!);
  if (negocio.telefono != null) t.linea('Tel. ${negocio.telefono}');
  if (negocio.rfc != null) t.linea('RFC ${negocio.rfc}');

  t.linea();

  if (copia > 0) {
    // Una reimpresión tiene que distinguirse a simple vista, o alguien va a
    // cobrar dos veces con el mismo papel.
    t
      ..negrita(true)
      ..tamano(ancho: 2, alto: 1)
      ..linea('* COPIA $copia *')
      ..tamano()
      ..negrita(false)
      ..linea();
  }
}

void _datosDeLaVenta(
  ConstructorEscPos t,
  VentaGuardada venta,
  DatosDeLaVisita visita,
) {
  // El folio en tamaño doble: es lo que el cliente dicta por teléfono cuando
  // algo se aclara, y lo que la oficina busca.
  t
    ..alinear(Alineacion.centro)
    ..negrita(true)
    ..tamano(ancho: 2, alto: 1)
    ..linea(venta.folioLocal)
    ..tamano()
    ..negrita(false)
    ..linea('REMISION DE VENTA')
    ..alinear(Alineacion.izquierda)
    ..separador();

  t.dosColumnas('Fecha', _fechaLegible(venta.fechaDispositivo));
  t.dosColumnas('Vendedor', visita.nombreVendedor);
  t.linea();

  t
    ..negrita(true)
    ..parrafo(visita.nombreCliente)
    ..negrita(false);
  if (visita.codigoCliente != null) t.linea('Cliente ${visita.codigoCliente}');
  if (visita.direccionCliente != null) t.parrafo(visita.direccionCliente!);

  t.separador();
}

void _partidas(ConstructorEscPos t, VentaGuardada venta) {
  t
    ..dosColumnas('CANT  DESCRIPCION', 'IMPORTE')
    ..separador();

  for (final l in venta.lineas) {
    // El nombre en su propia línea: en 32 columnas, meterlo junto con la
    // aritmética obliga a recortarlo a la mitad.
    t.parrafo(l.presentacion.nombre);

    // La multiplicación completa, sangrada, con el importe a la derecha. Es la
    // línea que el cliente revisa con el dedo.
    final operacion = '${l.cantidad.textoCorto} ${l.presentacion.unidadCodigo}'
        ' x ${l.presentacion.precio.textoCorto}';
    t.dosColumnas('  $operacion', l.importe.texto);
  }

  t.separador();
}

void _totales(ConstructorEscPos t, VentaGuardada venta) {
  // El subtotal solo se imprime cuando aporta algo. Con cero descuentos e IVA
  // incluido en el precio de lista, "Subtotal 592.00 / Total 592.00" es ruido
  // que hace dudar al cliente de si le cobraron algo extra.
  if (venta.subtotal != venta.total) {
    t.dosColumnas('Subtotal', venta.subtotal.texto);
  }

  t
    ..alinear(Alineacion.derecha)
    ..negrita(true)
    ..tamano(ancho: 2, alto: 2)
    ..linea('TOTAL ${venta.total.texto}')
    ..tamano()
    ..negrita(false)
    ..alinear(Alineacion.izquierda)
    ..linea();

  t.dosColumnas(
    'Forma de pago',
    venta.aCredito ? 'CREDITO' : 'CONTADO',
  );

  if (venta.aCredito) {
    // El saldo NO se imprime: el del teléfono puede tener horas, y un saldo
    // viejo en un papel que el cliente conserva es una disputa esperando. Lo que
    // sí es un hecho de esta venta es su importe y la firma de recibido.
    t
      ..linea()
      ..parrafo('Esta venta queda a crédito por ${venta.total.texto}.')
      // `parrafo` y no `linea`: son 34 columnas y el papel tiene 32. Con `linea`
      // la impresora continuaba el texto en el renglón siguiente y corría el
      // resto del ticket. Lo encontró la vista previa versionada, no una prueba:
      // la de desbordes solo cubría la venta de contado.
      ..parrafo('Consulta tu saldo con tu vendedor.')
      ..linea()
      ..linea()
      ..centrado('_______________________')
      ..centrado('Recibi conforme');
  }
}

void _pie(ConstructorEscPos t, VentaGuardada venta, DatosDelNegocio negocio) {
  t
    ..linea()
    ..alinear(Alineacion.centro)
    ..negrita(true)
    // Lo declara el papel para que nadie la presente como comprobante fiscal
    // (ADR 0002 §4).
    ..linea('DOCUMENTO NO FISCAL')
    ..negrita(false);

  if (negocio.leyendaFinal != null) t.parrafo(negocio.leyendaFinal!);

  // Marca de que la venta todavía no llegó a la oficina. Sirve en una
  // aclaración: explica por qué el sistema central no la tiene todavía.
  t
    ..linea('Folio del equipo: ${venta.folioConsecutivo}')
    ..alinear(Alineacion.izquierda);
}

/// '2026-09-29T17:42:03.250Z' → '29/09/2026 17:42'.
///
/// Se imprime el reloj del dispositivo, que es el que el vendedor tenía enfrente
/// cuando cobró. El del servidor también se guarda, pero poner en el papel una
/// hora que el cliente no vio en la pantalla no ayuda a nadie.
String _fechaLegible(String iso) {
  final momento = DateTime.tryParse(iso);
  if (momento == null) return iso;
  final l = momento.toLocal();
  String dos(int n) => n.toString().padLeft(2, '0');
  return '${dos(l.day)}/${dos(l.month)}/${l.year} ${dos(l.hour)}:${dos(l.minute)}';
}

/// El aviso de copia que se antepone a un ticket ya guardado.
///
/// Esta es la pieza que permite cumplir las dos cosas a la vez: la reimpresión
/// lleva **los bytes originales, sin recalcular** —así el papel dice exactamente
/// lo mismo que el que firmó el cliente— y además se distingue a simple vista.
List<int> avisoDeCopia(
  int numeroDeCopia, {
  int columnas = columnas58mm,
  TablaDeCodigos tabla = TablaDeCodigos.pc437,
}) =>
    (ConstructorEscPos(columnas: columnas, tabla: tabla)
          ..inicializar()
          ..alinear(Alineacion.centro)
          ..negrita(true)
          ..tamano(ancho: 2, alto: 2)
          ..linea('COPIA $numeroDeCopia')
          ..tamano()
          ..linea('No es el original')
          ..negrita(false)
          ..alinear(Alineacion.izquierda)
          ..linea())
        .bytes;

/// Lo que se manda a la impresora para una reimpresión.
List<int> reimpresion(
  List<int> ticketOriginal,
  int numeroDeCopia, {
  int columnas = columnas58mm,
  TablaDeCodigos tabla = TablaDeCodigos.pc437,
}) =>
    [
      ...avisoDeCopia(numeroDeCopia, columnas: columnas, tabla: tabla),
      ...ticketOriginal,
    ];

/// Un importe con su signo de pesos, para el papel.
String conPesos(Dinero d) => '\$${d.texto}';
