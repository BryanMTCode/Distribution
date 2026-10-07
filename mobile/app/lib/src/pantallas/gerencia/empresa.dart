/// La empresa en una pantalla: cuántos clientes, vendedores, artículos…
///
/// Pedido en operación (octubre 2026): «tanto en el dashboard como en la app de
/// gerente quiero un resumen de la empresa». Es la misma consulta que la
/// pantalla Empresa del panel: el tamaño del negocio, no lo que pasó hoy.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/sincronizacion.dart';
import '../../estado/vendedores.dart';
import 'comunes.dart';

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

  @override
  Widget build(BuildContext context) {
    final r = _r;
    final estilo = Theme.of(context).textTheme;
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
            if (_error != null)
              Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
            if (r == null && _error == null)
              const Padding(
                padding: EdgeInsets.all(32),
                child: Center(child: CircularProgressIndicator()),
              ),
            if (r != null) ...[
              Text('El tamaño del negocio hoy: lo que se tiene, no lo que pasó en el día.',
                  style: estilo.bodySmall),
              titulo('Clientes'),
              _Rejilla(children: [
                Tarjeta(
                  key: const Key('empresa_clientes'),
                  cifra: '${r.clientesActivos}',
                  etiqueta: 'clientes activos',
                  detalle: '${r.clientesNuevosMes} nuevo(s) este mes\n'
                      '${r.prospectos} prospecto(s) · ${r.clientesInactivos} de baja',
                ),
                Tarjeta(
                  cifra: pesos(r.cartera, conCentavos: false),
                  etiqueta: 'por cobrar',
                  detalle: '${r.clientesConSaldo} cliente(s) con saldo\n'
                      'Vencido ${pesos(r.carteraVencida)}',
                  alerta: r.carteraVencida.centavos > 0,
                ),
              ]),
              titulo('Gente y rutas'),
              _Rejilla(children: [
                Tarjeta(
                  key: const Key('empresa_vendedores'),
                  cifra: '${r.vendedores}',
                  etiqueta: 'vendedores activos',
                  detalle: '${r.vendedoresConCamion} con camión asignado',
                ),
                Tarjeta(
                  cifra: '${r.rutas}',
                  etiqueta: 'rutas',
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
                  detalle: '${r.productosSinPrecio} sin precio',
                  alerta: r.productosSinPrecio > 0,
                ),
                Tarjeta(
                  cifra: cantidadLegible(r.piezasEnBodegas),
                  etiqueta: 'piezas en bodega',
                  detalle: '${r.bodegas} bodega(s)',
                ),
                Tarjeta(
                  cifra: cantidadLegible(r.piezasEnCamiones),
                  etiqueta: 'piezas en camiones',
                  detalle: '${r.camiones} camión(es)'
                      '${r.existenciasNegativas > 0 ? '\n${r.existenciasNegativas} renglón(es) en negativo' : ''}',
                  alerta: r.existenciasNegativas > 0,
                ),
              ]),
              titulo('Ventas'),
              _Rejilla(children: [
                Tarjeta(
                  cifra: pesos(r.vendidoMes, conCentavos: false),
                  etiqueta: 'vendido este mes',
                ),
                Tarjeta(
                  cifra: pesos(r.vendidoAnio, conCentavos: false),
                  etiqueta: 'vendido este año',
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
