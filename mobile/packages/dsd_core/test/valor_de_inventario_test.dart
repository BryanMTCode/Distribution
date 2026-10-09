/// Lo que vale el inventario, como lo manda el servidor (`/v1/almacen`).
///
/// · Cada artículo trae su precio de venta por pieza y lo que vale su existencia;
///   sin precio, los dos son nulos —no cero—.
/// · El almacén trae lo que vale todo; un servidor anterior no lo manda y vale
///   cero, sin romper la lectura.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

Map<String, Object?> _almacen({String? valor}) => {
      'id': 'b1', 'codigo': 'BODEGA_PRINCIPAL', 'nombre': 'Bodega', 'tipo': 'bodega',
      'responsable': null, 'productos': 2, 'piezas': '765.000', 'negativos': 0,
      if (valor != null) 'valor': valor,
    };

void main() {
  test('cada artículo trae su precio y su valor; sin precio, nulos', () {
    final d = ExistenciasDelAlmacen.deJson({
      'almacen': _almacen(valor: '7600.00'),
      'existencias': [
        {'producto_id': 'p1', 'sku': 'S-100', 'nombre': 'Sobre Minino', 'unidad_base': 'PZA',
         'cantidad': '760.000', 'presentaciones': [], 'precio': '10.00', 'valor': '7600.00'},
        {'producto_id': 'p2', 'sku': 'X', 'nombre': 'Sin precio', 'unidad_base': 'PZA',
         'cantidad': '5.000', 'presentaciones': [], 'precio': null, 'valor': null},
      ],
    });
    expect(d.almacen.valor, Dinero.deTexto('7600.00'));
    expect(d.existencias.first.precio, Dinero.deTexto('10.00'));
    expect(d.existencias.first.valor!.enPesos, r'$7,600.00');
    expect(d.existencias.last.valor, isNull);
  });

  test('un servidor que no manda el valor no rompe la lectura', () {
    expect(AlmacenResumen.deJson(_almacen()).valor, Dinero.cero);
  });
}
