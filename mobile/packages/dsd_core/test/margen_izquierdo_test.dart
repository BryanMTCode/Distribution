/// El margen izquierdo de la impresora (ADR 0002 §141).
///
/// La EC-MP200 de la dirección imprime corrido a la izquierda: la primera letra
/// de cada renglón sale fuera del papel. El margen (`GS L`) se pone al mandar el
/// ticket, después de CADA `ESC @` —que lo reinicia—.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

const _gsL24 = [0x1D, 0x4C, 24, 0];

int _cuantas(List<int> bytes, List<int> buscado) {
  var n = 0;
  for (var i = 0; i + buscado.length <= bytes.length; i++) {
    var igual = true;
    for (var k = 0; k < buscado.length; k++) {
      if (bytes[i + k] != buscado[k]) {
        igual = false;
        break;
      }
    }
    if (igual) n++;
  }
  return n;
}

void main() {
  test('sin margen, los bytes no cambian', () {
    final t = (ConstructorEscPos()..inicializar()..linea('HOLA')).bytes;
    expect(conMargenIzquierdo(t, 0), same(t));
  });

  test('va justo después del ESC @, y el texto no cambia', () {
    final t = (ConstructorEscPos()..inicializar()..linea('HOLA')).bytes;
    final con = conMargenIzquierdo(t, 24);
    expect(con.sublist(0, 6), [0x1B, 0x40, ..._gsL24]);
    expect(decodificar(con).render(), decodificar(t).render());
    expect(decodificar(con).desconocidos, isEmpty);
  });

  test('la reimpresión trae dos ESC @ y lleva el margen después de los dos', () {
    final original = (ConstructorEscPos()..inicializar()..linea('VENTA')).bytes;
    final copia = reimpresion(original, 1);
    expect(_cuantas(conMargenIzquierdo(copia, 24), _gsL24), 2);
  });

  test('no confunde con un ESC @ el argumento de otro comando', () {
    // `ESC d 27` seguido de una «@»: el 27 es el argumento, no un ESC.
    final t = [0x1B, 0x40, 0x1B, 0x64, 0x1B, 0x40, 0x0A];
    expect(_cuantas(conMargenIzquierdo(t, 24), _gsL24), 1);
  });

  test('un flujo sin ESC @ lo lleva al principio', () {
    expect(conMargenIzquierdo([0x41, 0x0A], 12).sublist(0, 4), [0x1D, 0x4C, 12, 0]);
  });

  test('con menos columnas, ningún renglón del ticket de venta pasa del papel', () {
    // Lo que hace la app con el margen: 32 − 2 letras.
    final caja = PresentacionVendible(
      productoId: 'p-sopa',
      sku: 'SOPA-70G',
      nombre: 'Sopa de fideo instantánea sabor pollo 70 g',
      unidadCodigo: 'CAJA',
      factor: Factor.deEnteros(24),
      precio: Precio.deTexto('296.0000'),
      listaPreciosId: 'l',
      listaPreciosVersion: 7,
    );
    final lineas = [LineaCarrito(presentacion: caja, cantidad: Cantidad.deEnteros(12))];
    final total = lineas.first.importe;
    final venta = VentaGuardada(
      id: 'v-1',
      folioConsecutivo: 124,
      folioLocal: 'VEND01-000124',
      visitaId: 'vis-1',
      clienteId: 'cli-1',
      formaDePago: FormaDePago.efectivo,
      subtotal: total,
      total: total,
      lineas: lineas,
      fechaDispositivo: '2026-09-29T17:42:03.250Z',
      fechaOperativa: '2026-09-29',
      foliosRestantes: 400,
    );
    final t = ticketDeVenta(
      venta,
      negocio: const DatosDelNegocio(nombre: 'Distribuciones SE'),
      visita: const DatosDeLaVisita(
        nombreCliente: 'Abarrotes La Esperanza de San Juan',
        nombreVendedor: 'Juan Pérez',
      ),
      columnas: 30,
    );
    for (final l in decodificar(t, columnas: 30).lineas) {
      expect(l.columnasOcupadas, lessThanOrEqualTo(30), reason: l.texto);
    }
  });

  test('una imagen que trae un 1B 40 entre sus puntos no se toma por ESC @ (§155)', () {
    // Los puntos de la imagen son datos: un 1B 40 ahí es parte del dibujo. Si el
    // margen se metiera en medio, la imagen se corre y el resto del ticket sale
    // como basura.
    const imagen = ImagenMonocroma(ancho: 16, alto: 2, puntos: [0x1B, 0x40, 0x1B, 0x40]);
    final bytes = (ConstructorEscPos()
          ..inicializar()
          ..imagen(imagen)
          ..linea('fin'))
        .bytes;
    final conMargen = conMargenIzquierdo(bytes, 24);
    // Un solo margen, el del ESC @ de verdad.
    final margen = margenIzquierdo(24);
    var veces = 0;
    for (var i = 0; i + margen.length <= conMargen.length; i++) {
      if (List.generate(margen.length, (k) => conMargen[i + k]).join(',') == margen.join(',')) {
        veces++;
      }
    }
    expect(veces, 1);
    expect(conMargen.length, bytes.length + margen.length);
    expect(decodificar(conMargen).lineas.map((l) => l.texto), ['[logo]', 'fin']);
  });

  test('una imagen que no mide lo que dice no se arma', () {
    expect(
      () => ConstructorEscPos().imagen(const ImagenMonocroma(ancho: 16, alto: 2, puntos: [0])),
      throwsArgumentError,
    );
  });
}
