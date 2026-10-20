/// La empresa en una pantalla: cuántos clientes, vendedores, artículos…
///
/// Pedido en operación (octubre 2026): «tanto en el dashboard como en la app de
/// gerente quiero un resumen de la empresa». Es la misma consulta que la
/// pantalla Empresa del panel: el tamaño del negocio, no lo que pasó hoy.
///
/// Es también la puerta a los clientes: la pestaña Clientes salió de la barra
/// (ADR 0002 §152). Si el resumen no llega —sin señal, o sin `tablero.ver`—,
/// las listas se abren igual con sus botones: no dependen de las cifras.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/almacen.dart';
import '../../estado/oficina_extra.dart';
import '../../estado/sesion.dart';
import '../../estado/sincronizacion.dart';
import '../../estado/vendedores.dart';
import 'almacen.dart';
import 'articulos.dart';
import 'clientes_oficina.dart';
import 'comunes.dart';
import 'periodo.dart';
import 'vendedores.dart';

class PantallaEmpresa extends ConsumerStatefulWidget {
  const PantallaEmpresa({super.key});

  @override
  ConsumerState<PantallaEmpresa> createState() => _EstadoEmpresa();
}

class _EstadoEmpresa extends ConsumerState<PantallaEmpresa> {
  ResumenDeLaEmpresa? _r;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _cargar());
  }

  Future<void> _cargar() async {
    final transporte = ref.read(transporteProvider);
    if (transporte == null) {
      setState(() => _error = 'No hay sesión en línea. Sal y vuelve a entrar con señal.');
      return;
    }
    try {
      final r = await ClientePeriodo(transporte).empresa();
      if (!mounted) return;
      setState(() {
        _r = r;
        _error = null;
      });
    } on SinPermisoDeTablero {
      if (!mounted) return;
      setState(() => _error = 'Tu usuario no puede ver el tablero (tablero.ver).');
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = explicarErrorDeOficina(e));
    }
  }

  /// Abre la pantalla de esa cifra (ADR 0002 §97). Nulo —la tarjeta no se
  /// toca— si el usuario no puede ver esa pantalla.
  VoidCallback? _ir(bool puede, Widget Function() pantalla) => !puede
      ? null
      : () => Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => pantalla()));

  @override
  Widget build(BuildContext context) {
    final r = _r;
    final estilo = Theme.of(context).textTheme;
    final veVendedores = ref.watch(puedeVerVendedoresProvider);
    // Los artículos los ve quien mueve o vende mercancía.
    final veAlmacen =
        ref.watch(muestraAlmacenProvider) || ref.watch(puedeVenderMayoreoProvider);
    final veInventario = ref.watch(puedeVerAlmacenProvider);
    final hoy = ref.watch(relojProvider)();
    String dia(DateTime d) =>
        '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';
    Widget titulo(String t) => Padding(
          padding: const EdgeInsets.only(top: 20, bottom: 10),
          child: Text(t, style: estilo.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
        );
    return Scaffold(
      key: const Key('pantalla_empresa'),
      appBar: AppBar(
        title: const Text('La empresa'),
        actions: [
          IconButton(
            tooltip: 'Volver a consultar',
            icon: const Icon(Icons.refresh),
            onPressed: _cargar,
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _cargar,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (_error != null) ...[
              Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
              const SizedBox(height: 12),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final (clave, texto, icono, puede, pantalla) in [
                    (
                      'empresa_ir_clientes', 'Clientes', Icons.storefront_outlined,
                      veVendedores, () => const PantallaClientesDeOficina(),
                    ),
                    (
                      'empresa_ir_vendedores', 'Vendedores', Icons.groups_outlined,
                      veVendedores, () => const PantallaVendedores(),
                    ),
                    (
                      'empresa_ir_inventario', 'Inventario', Icons.warehouse_outlined,
                      veInventario, () => const PantallaExistencias(),
                    ),
                    (
                      'empresa_ir_articulos', 'Artículos', Icons.inventory_2_outlined,
                      veAlmacen, () => const PantallaArticulos(),
                    ),
                  ])
                    if (puede)
                      OutlinedButton.icon(
                        key: Key(clave),
                        onPressed: _ir(true, pantalla),
                        icon: Icon(icono),
                        label: Text(texto),
                      ),
                ],
              ),
            ],
            if (r == null && _error == null)
              const Padding(
                padding: EdgeInsets.all(32),
                child: Center(child: CircularProgressIndicator()),
              ),
            if (r != null) ...[
              Text(
                'El tamaño del negocio hoy: lo que se tiene, no lo que pasó en el día. '
                'Toca una cifra para ver su lista.',
                style: estilo.bodySmall,
              ),
              titulo('Clientes'),
              _Rejilla(children: [
                Tarjeta(
                  key: const Key('empresa_clientes'),
                  cifra: '${r.clientesActivos}',
                  etiqueta: 'clientes activos',
                  onTap: _ir(veVendedores, () => const PantallaClientesDeOficina()),
                  detalle: '${r.clientesNuevosMes} nuevo(s) este mes\n'
                      '${r.prospectos} prospecto(s) · ${r.clientesInactivos} de baja',
                ),
              ]),
              titulo('Gente y rutas'),
              _Rejilla(children: [
                Tarjeta(
                  key: const Key('empresa_vendedores'),
                  cifra: '${r.vendedores}',
                  etiqueta: 'vendedores activos',
                  onTap: _ir(veVendedores, () => const PantallaVendedores()),
                  detalle: '${r.vendedoresConCamion} con camión asignado',
                ),
                Tarjeta(
                  key: const Key('empresa_rutas'),
                  cifra: '${r.rutas}',
                  etiqueta: 'rutas',
                  // Cada vendedor dice su ruta y su camión.
                  onTap: _ir(veVendedores, () => const PantallaVendedores()),
                  detalle: '${r.usuariosOficina} usuario(s) de oficina\n'
                      '${r.telefonos} teléfono(s) activo(s)',
                ),
              ]),
              titulo('Artículos e inventario'),
              _Rejilla(children: [
                Tarjeta(
                  key: const Key('empresa_articulos'),
                  cifra: '${r.productos}',
                  etiqueta: 'artículos activos',
                  onTap: _ir(veAlmacen, () => const PantallaArticulos()),
                  detalle: '${r.productosSinPrecio} sin precio',
                  alerta: r.productosSinPrecio > 0,
                ),
                Tarjeta(
                  key: const Key('empresa_bodegas'),
                  cifra: cantidadLegible(r.piezasEnBodegas),
                  etiqueta: 'piezas en bodega',
                  onTap: _ir(veInventario, () => const PantallaExistencias()),
                  detalle: '${r.bodegas} bodega(s)',
                ),
                Tarjeta(
                  key: const Key('empresa_camiones'),
                  cifra: cantidadLegible(r.piezasEnCamiones),
                  etiqueta: 'piezas en camiones',
                  onTap: _ir(veInventario, () => const PantallaExistencias()),
                  detalle: '${r.camiones} camión(es)'
                      '${r.existenciasNegativas > 0 ? '\n${r.existenciasNegativas} renglón(es) en negativo' : ''}',
                  alerta: r.existenciasNegativas > 0,
                ),
              ]),
              titulo('Ventas'),
              _Rejilla(children: [
                Tarjeta(
                  key: const Key('empresa_vendido_mes'),
                  cifra: pesos(r.vendidoMes, conCentavos: false),
                  etiqueta: 'vendido este mes',
                  onTap: _ir(
                    veVendedores,
                    () => const PantallaVendedores(periodoInicial: PeriodoElegido('mes')),
                  ),
                ),
                Tarjeta(
                  key: const Key('empresa_vendido_anio'),
                  cifra: pesos(r.vendidoAnio, conCentavos: false),
                  etiqueta: 'vendido este año',
                  onTap: _ir(
                    veVendedores,
                    () => PantallaVendedores(
                      periodoInicial: PeriodoElegido.rango('${hoy.year}-01-01', dia(hoy)),
                    ),
                  ),
                ),
              ]),
            ],
          ],
        ),
      ),
    );
  }
}

class _Rejilla extends StatelessWidget {
  const _Rejilla({required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) => LayoutBuilder(
        builder: (context, limites) {
          final ancho = (limites.maxWidth - 12) / 2;
          return Wrap(
            spacing: 12,
            runSpacing: 12,
            children: [for (final c in children) SizedBox(width: ancho, child: c)],
          );
        },
      );
}
