/// «Mi día» usa el MISMO día que estampa la venta.
///
/// Bug de campo, octubre 2026: la pantalla calculaba el día en UTC y los
/// documentos en hora local. De las 18:00 en adelante (centro de México) el día
/// UTC ya es mañana: la venta llegaba al servidor y «Mi día» no la mostraba.
library;

import 'package:dsd_app/src/estado/mi_dia.dart';
import 'package:dsd_app/src/estado/sesion.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('el día de «Mi día» es el de los documentos, también de noche', () {
    // 01:30 UTC del 7 de octubre: en México todavía son las 19:30 del 6.
    final instante = DateTime.utc(2026, 10, 7, 1, 30);
    final contenedor = ProviderContainer(
      overrides: [relojProvider.overrideWithValue(() => instante)],
    );
    addTearDown(contenedor.dispose);

    expect(contenedor.read(diaOperativoProvider), equals(diaOperativoDe(instante)));
  });
}
