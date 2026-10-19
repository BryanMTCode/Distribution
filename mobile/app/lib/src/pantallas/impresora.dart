/// Elegir la impresora del camión y probarla (ADR 0002 §139).
///
/// Se llega desde el menú de «Mi ruta» → «Impresora». La impresora se empareja
/// una vez en los Ajustes de Android; aquí se elige de entre las emparejadas y se
/// imprime una prueba que dice, en el papel, si el ancho y los acentos salen bien.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../datos/impresora.dart';
import '../datos/impresora_bluetooth.dart';
import '../estado/carrito.dart';
import '../estado/impresora.dart';
import '../estado/sesion.dart';

/// Para las pruebas: de dónde salen las emparejadas.
final buscadorDeImpresorasProvider = Provider<Future<Emparejadas> Function()>(
  (_) => buscarEmparejadas,
);

/// El ticket de prueba: el ancho del papel, los acentos y un total, como los
/// del ticket de venta.
List<int> ticketDePrueba({
  required String negocio,
  required String impresora,
  required DateTime ahora,
  int columnas = columnas58mm,
}) {
  // La regla con un número por columna: si falta el «1» de la izquierda, la
  // impresora se come la primera letra y hay que correrla (ADR 0002 §141).
  final regla = List.generate(columnas, (i) => '${(i + 1) % 10}').join();
  final b = ConstructorEscPos(columnas: columnas)
    ..inicializar()
    ..alinear(Alineacion.centro)
    ..negrita(true)
    ..tamano(ancho: 2, alto: 2)
    ..linea('PRUEBA')
    ..tamano()
    ..negrita(false)
    ..linea(negocio)
    ..linea(impresora)
    ..alinear(Alineacion.izquierda)
    ..separador()
    ..parrafo('Si lees esto completo, la impresora quedó lista.')
    ..linea()
    ..parrafo('Ancho: $columnas números en una línea, empezando por el 1. Si no '
        'ves el 1, córrela a la derecha.')
    ..linea(regla)
    ..linea()
    ..linea('Acentos: á é í ó ú ñ Ñ ü ¿? ¡!')
    ..linea()
    ..dosColumnas('2 x Coca 600 ml', r'$36.00')
    ..dosColumnas('1 x Sabritas 45 g', r'$18.50')
    ..separador()
    ..negrita(true)
    ..dosColumnas('TOTAL', r'$54.50')
    ..negrita(false)
    ..separador()
    ..alinear(Alineacion.centro)
    ..linea(_fecha(ahora))
    ..avanzar(4);
  return b.bytes;
}

String _fecha(DateTime t) {
  String dos(int n) => n.toString().padLeft(2, '0');
  final l = t.toLocal();
  return '${dos(l.day)}/${dos(l.month)}/${l.year} ${dos(l.hour)}:${dos(l.minute)}';
}

class PantallaImpresora extends ConsumerStatefulWidget {
  const PantallaImpresora({super.key});

  @override
  ConsumerState<PantallaImpresora> createState() => _EstadoImpresora();
}

class _EstadoImpresora extends ConsumerState<PantallaImpresora> {
  Emparejadas? _emparejadas;
  bool _buscando = false;
  bool _imprimiendo = false;
  String? _resultado;
  bool _salioBien = false;

  void _margen(int letras) {
    ref.read(impresoraElegidaProvider.notifier).ajustarMargen(letras);
    setState(() => _resultado = null);
  }

  Future<void> _buscar() async {
    setState(() {
      _buscando = true;
      _resultado = null;
    });
    final r = await ref.read(buscadorDeImpresorasProvider)();
    if (!mounted) return;
    setState(() {
      _buscando = false;
      _emparejadas = r;
    });
  }

  Future<void> _probar() async {
    final impresora = ref.read(impresoraProvider);
    setState(() {
      _imprimiendo = true;
      _resultado = null;
    });
    final r = await impresora.imprimir(ticketDePrueba(
      negocio: ref.read(negocioProvider).nombre,
      impresora: impresora.nombre,
      ahora: ref.read(relojProvider)(),
      columnas: ref.read(columnasDelPapelProvider),
    ));
    if (!mounted) return;
    setState(() {
      _imprimiendo = false;
      _salioBien = r is ImpresionHecha;
      _resultado = switch (r) {
        ImpresionHecha() => 'Se mandó la prueba. Revisa el papel: los números completos en '
            'una línea, empezando por el 1, y los acentos legibles. Si se come la primera '
            'letra, toca «+» en «Mover a la derecha» y vuelve a imprimirla.',
        ImpresoraDesconectada() => 'La impresora no contestó. Revisa que esté encendida, con '
            'batería y cerca del teléfono, y vuelve a intentar.',
        ImpresoraSinPapel() => 'La impresora no tiene papel.',
        ImpresoraNoConfigurada() => 'Primero elige la impresora de la lista.',
        ImpresionFallida(:final detalle) => 'No se pudo imprimir: $detalle',
      };
    });
  }

  @override
  Widget build(BuildContext context) {
    final elegida = ref.watch(impresoraElegidaProvider);
    final estilo = Theme.of(context).textTheme;
    final colores = Theme.of(context).colorScheme;
    final emparejadas = _emparejadas;

    return Scaffold(
      key: const Key('pantalla_impresora'),
      appBar: AppBar(title: const Text('Impresora')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Card(
            child: ListTile(
              leading: const Icon(Icons.print_outlined),
              title: Text(
                elegida?.nombre ?? 'Ninguna elegida',
                key: const Key('impresora_elegida'),
              ),
              subtitle: Text(elegida == null
                  ? 'Los tickets no se imprimen hasta que elijas una.'
                  : 'Con esta se imprimen las ventas y el corte · ${elegida.direccion}'),
            ),
          ),
          const SizedBox(height: 8),
          FilledButton.icon(
            key: const Key('boton_imprimir_prueba'),
            onPressed: elegida == null || _imprimiendo ? null : _probar,
            icon: _imprimiendo
                ? const SizedBox.square(
                    dimension: 18, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.receipt_long_outlined),
            label: const Text('Imprimir una prueba'),
          ),
          if (_resultado != null) ...[
            const SizedBox(height: 8),
            Text(
              _resultado!,
              key: const Key('resultado_prueba'),
              style: TextStyle(color: _salioBien ? null : colores.error),
            ),
          ],
          if (elegida != null) ...[
            const SizedBox(height: 8),
            // La EC-MP200 se come la primera letra de cada renglón (§141).
            Row(
              children: [
                const Expanded(child: Text('Mover a la derecha')),
                IconButton(
                  key: const Key('boton_margen_menos'),
                  tooltip: 'Menos',
                  onPressed: elegida.margen == 0
                      ? null
                      : () => _margen(elegida.margen - 1),
                  icon: const Icon(Icons.remove_circle_outline),
                ),
                Text(
                  '${elegida.margen} letra(s)',
                  key: const Key('margen_impresora'),
                ),
                IconButton(
                  key: const Key('boton_margen_mas'),
                  tooltip: 'Más',
                  onPressed: elegida.margen >= ImpresoraEmparejada.margenMaximo
                      ? null
                      : () => _margen(elegida.margen + 1),
                  icon: const Icon(Icons.add_circle_outline),
                ),
              ],
            ),
            Text(
              'Si la impresora se come la primera letra de cada renglón, súbelo y vuelve '
              'a imprimir la prueba. Lo que se corre se le quita al ancho: quedan '
              '${ref.watch(columnasDelPapelProvider)} letras por renglón.',
              style: estilo.bodySmall,
            ),
          ],
          if (elegida != null)
            TextButton(
              key: const Key('boton_olvidar_impresora'),
              onPressed: () => ref.read(impresoraElegidaProvider.notifier).olvidar(),
              child: const Text('Olvidar esta impresora'),
            ),
          const Divider(height: 32),
          Text('Elegir la impresora', style: estilo.titleMedium),
          const SizedBox(height: 4),
          const Text(
            '1. Prende la impresora.\n'
            '2. En los Ajustes del teléfono → Bluetooth, emparéjala. Si pide clave, '
            'suele ser 0000 o 1234.\n'
            '3. Regresa aquí y toca «Buscar impresoras».',
          ),
          const SizedBox(height: 8),
          OutlinedButton.icon(
            key: const Key('boton_buscar_impresoras'),
            onPressed: _buscando ? null : _buscar,
            icon: const Icon(Icons.bluetooth_searching),
            label: Text(_buscando ? 'Buscando…' : 'Buscar impresoras'),
          ),
          if (emparejadas?.problema != null) ...[
            const SizedBox(height: 8),
            Text(
              emparejadas!.problema!,
              key: const Key('problema_impresoras'),
              style: TextStyle(color: colores.error),
            ),
          ],
          if (emparejadas != null &&
              emparejadas.problema == null &&
              emparejadas.impresoras.isEmpty) ...[
            const SizedBox(height: 8),
            const Text(
              'El teléfono no tiene nada emparejado. Emparéjala en los Ajustes del '
              'teléfono → Bluetooth y vuelve a buscar.',
              key: Key('sin_emparejadas'),
            ),
          ],
          for (final i in emparejadas?.impresoras ?? const <ImpresoraEmparejada>[])
            ListTile(
              key: Key('emparejada_${i.direccion}'),
              leading: Icon(i.pareceImpresora ? Icons.print : Icons.bluetooth),
              title: Text(i.nombre),
              subtitle: Text(i.direccion),
              trailing: elegida?.direccion == i.direccion
                  ? Icon(Icons.check_circle, color: colores.primary)
                  : null,
              onTap: () {
                // La misma otra vez se queda con su margen.
                ref.read(impresoraElegidaProvider.notifier).elegir(i.conMargen(
                    elegida?.direccion == i.direccion ? elegida!.margen : 0));
                setState(() => _resultado = null);
              },
            ),
        ],
      ),
    );
  }
}
