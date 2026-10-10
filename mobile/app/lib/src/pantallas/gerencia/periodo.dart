/// El tablero por periodo, en el teléfono de la oficina.
///
/// Pedido en operación (octubre 2026): «que el gerente pueda ver los datos por
/// días y periodos, y los de un vendedor en particular: lo mismo que en el
/// dashboard». Las cifras salen de la misma función que el tablero del panel.
///
///   · Hoy, ayer, esta semana, la pasada, este mes, el pasado, o un rango de
///     fechas a mano.
///   · Lo vendido (efectivo y transferencia), visitas sin venta, mermas,
///     devoluciones y clientes.
///   · Por vendedor —tocar uno abre todo lo que hizo en ese periodo— y por día
///     —tocar uno abre el tablero de ese día—.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/sesion.dart';
import '../../estado/sincronizacion.dart';
import '../../estado/vendedores.dart';
import 'comunes.dart';
import 'vendedores.dart';

/// Qué periodo se está viendo: uno con nombre, o un rango a mano.
class PeriodoElegido {
  const PeriodoElegido(this.clave) : desde = null, hasta = null;
  const PeriodoElegido.rango(String this.desde, String this.hasta) : clave = 'rango';

  final String clave;

  /// `YYYY-MM-DD`, solo en un rango a mano.
  final String? desde;
  final String? hasta;
}

/// Los periodos que se ofrecen como botones. «Personalizado» abre el calendario.
const _periodosRapidos = {
  'hoy', 'ayer', 'semana', 'semana_pasada', 'mes', 'mes_pasado',
  // Todo, desde el primer día con movimientos (ADR 0002 §96).
  'todo',
};

/// Los mismos que `PERIODOS` del servidor (`app/api/admin/periodo.py`), para
/// dibujar el selector antes de la primera respuesta.
const periodosDeSiempre = [
  ('hoy', 'Hoy'),
  ('ayer', 'Ayer'),
  ('semana', 'Esta semana'),
  ('semana_pasada', 'Semana pasada'),
  ('mes', 'Este mes'),
  ('mes_pasado', 'Mes pasado'),
  ('todo', 'Todo'),
];

/// Botones de periodo en una fila que se desliza, y «Fechas…» al final.
class SelectorDePeriodo extends StatelessWidget {
  const SelectorDePeriodo({
    super.key,
    required this.periodos,
    required this.elegido,
    required this.onElegir,
    required this.hoy,
    this.conUnDia = false,
  });

  final List<(String, String)> periodos;
  final PeriodoElegido elegido;
  final ValueChanged<PeriodoElegido> onElegir;
  final DateTime hoy;

  /// Ofrece también «Un día…»: el calendario para un solo día.
  final bool conUnDia;

  bool get _esUnDia => elegido.clave == 'rango' && elegido.desde == elegido.hasta;

  Future<void> _elegirUnDia(BuildContext context) async {
    final dia = DateTime(hoy.year, hoy.month, hoy.day);
    final elegidoDia = await showDatePicker(
      context: context,
      initialDate: _esUnDia ? DateTime.parse(elegido.desde!) : dia,
      firstDate: dia.subtract(const Duration(days: 366)),
      // Un día futuro no tiene cifras: no se ofrece.
      lastDate: dia,
      helpText: '¿Qué día quieres ver?',
    );
    if (elegidoDia == null) return;
    final d = diaOperativoDe(elegidoDia);
    onElegir(PeriodoElegido.rango(d, d));
  }

  Future<void> _elegirFechas(BuildContext context) async {
    final dia = DateTime(hoy.year, hoy.month, hoy.day);
    final rango = await showDateRangePicker(
      context: context,
      firstDate: dia.subtract(const Duration(days: 366)),
      lastDate: dia,
      initialDateRange: elegido.desde == null
          ? DateTimeRange(start: dia.subtract(const Duration(days: 6)), end: dia)
          : DateTimeRange(
              start: DateTime.parse(elegido.desde!),
              end: DateTime.parse(elegido.hasta!),
            ),
      helpText: 'Elige los días',
      saveText: 'Ver',
    );
    if (rango == null) return;
    onElegir(PeriodoElegido.rango(diaOperativoDe(rango.start), diaOperativoDe(rango.end)));
  }

  @override
  Widget build(BuildContext context) => SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(
          children: [
            for (final (clave, etiqueta) in periodos)
              if (_periodosRapidos.contains(clave))
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: ChoiceChip(
                    key: Key('periodo_$clave'),
                    label: Text(etiqueta),
                    selected: clave == elegido.clave,
                    onSelected: (_) => onElegir(PeriodoElegido(clave)),
                  ),
                ),
            if (conUnDia)
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: ChoiceChip(
                  key: const Key('periodo_un_dia'),
                  avatar: const Icon(Icons.event, size: 18),
                  label: Text(_esUnDia ? diaEnPalabras(elegido.desde!) : 'Un día…'),
                  selected: _esUnDia,
                  onSelected: (_) => _elegirUnDia(context),
                ),
              ),
            ChoiceChip(
              key: const Key('periodo_rango'),
              avatar: const Icon(Icons.date_range, size: 18),
              label: const Text('Fechas…'),
              selected: elegido.clave == 'rango' && !(conUnDia && _esUnDia),
              onSelected: (_) => _elegirFechas(context),
            ),
          ],
        ),
      );
}

/// Qué pestaña del portal de la oficina está a la vista. El desglose por día la
/// cambia a «Día» para abrir el tablero de la fecha que se tocó.
final pestanaDeOficinaProvider = StateProvider<int>((_) => 0);

/// El resumen de un periodo de varios días. Vive dentro del Tablero, que pone el
/// selector arriba: un solo día se ve con el tablero completo, no aquí.
class VistaDelPeriodo extends ConsumerStatefulWidget {
  const VistaDelPeriodo({super.key, required this.periodo, required this.onVerDia});

  final PeriodoElegido periodo;

  /// Tocar un día del desglose: el Tablero lo abre completo.
  final ValueChanged<String> onVerDia;

  @override
  ConsumerState<VistaDelPeriodo> createState() => _EstadoPeriodo();
}

class _EstadoPeriodo extends ConsumerState<VistaDelPeriodo> {
  PeriodoElegido get _periodo => widget.periodo;
  TableroDelPeriodo? _datos;
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
      final datos = await ClientePeriodo(transporte).ver(
        periodo: _periodo.clave,
        desde: _periodo.desde,
        hasta: _periodo.hasta,
      );
      if (!mounted) return;
      setState(() {
        _datos = datos;
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

  void _verDia(String fecha) => widget.onVerDia(fecha);

  @override
  Widget build(BuildContext context) {
    final d = _datos;
    final hoy = ref.watch(relojProvider)();
    final estilo = Theme.of(context).textTheme;
    final puedeVerVendedores = ref.watch(puedeVerVendedoresProvider);
    return RefreshIndicator(
        key: const Key('pantalla_periodo'),
        onRefresh: _cargar,
        child: ListView(
          key: const Key('lista_periodo'),
          padding: const EdgeInsets.all(16),
          children: [
            if (_error != null)
              Text(_error!, key: const Key('aviso_periodo_error'),
                  style: TextStyle(color: Theme.of(context).colorScheme.error)),
            if (d == null && _error == null)
              const Padding(
                padding: EdgeInsets.all(32),
                child: Center(child: CircularProgressIndicator()),
              ),
            if (d != null) ...[
              Text(
                d.periodo.enPalabras(hoy: diaOperativoDe(hoy)),
                key: const Key('dias_del_periodo'),
                style: estilo.titleMedium,
              ),
              Text(
                'Lo que no han subido los teléfonos todavía no está aquí.',
                style: estilo.bodySmall,
              ),
              const SizedBox(height: 12),
              _Rejilla(children: [
                Tarjeta(
                  key: const Key('periodo_vendido'),
                  cifra: pesos(d.cifras.total, conCentavos: false),
                  etiqueta: 'vendido',
                  detalle: 'Efectivo ${pesos(d.cifras.efectivo)}\n'
                      'Transferencia ${pesos(d.cifras.transferencias)}'
                      '${d.cifras.canceladas > 0 ? '\n${d.cifras.canceladas} cancelada(s)' : ''}',
                ),
                Tarjeta(
                  cifra: '${d.cifras.clientesAtendidos}',
                  etiqueta: 'clientes con venta',
                  detalle: '${d.cifras.clientesNuevos} cliente(s) nuevo(s)',
                ),
                Tarjeta(
                  cifra: '${d.cifras.noVentas}',
                  etiqueta: 'visitas sin venta',
                  detalle: '${d.cifras.mermas} merma(s) · '
                      '${d.cifras.devoluciones} devolución(es)',
                ),
              ]),
              const SizedBox(height: 16),
              Text('Por vendedor', style: estilo.titleMedium),
              for (final v in d.porVendedor)
                ListTile(
                  key: Key('periodo_vendedor_${v.codigo}'),
                  contentPadding: EdgeInsets.zero,
                  title: Text(v.nombre),
                  subtitle: Text(
                    '${v.ventas} venta(s) · ${pesos(v.efectivo)} en efectivo'
                    '${v.noVentas > 0 ? ' · ${v.noVentas} sin venta' : ''}'
                    '${v.mermas > 0 ? ' · ${v.mermas} merma(s)' : ''}',
                  ),
                  trailing: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(pesos(v.importe)),
                      if (puedeVerVendedores) const Icon(Icons.chevron_right),
                    ],
                  ),
                  onTap: puedeVerVendedores
                      ? () => Navigator.of(context).push(
                            MaterialPageRoute(
                              builder: (_) => PantallaVendedor(
                                vendedorId: v.id,
                                nombre: v.nombre,
                                periodo: _periodo,
                              ),
                            ),
                          )
                      : null,
                ),
              if (d.porDia.isNotEmpty) ...[
                const SizedBox(height: 16),
                Text('Por día', style: estilo.titleMedium),
                Text('Toca un día para abrir su tablero.', style: estilo.bodySmall),
                ..._porDia(d.porDia),
              ],
            ],
          ],
        ),
    );
  }

  Iterable<Widget> _porDia(List<DiaDelPeriodo> dias) {
    // La barra es relativa al mejor día del periodo: se ve de un vistazo qué día
    // flojeó. No es una gráfica con escala.
    final tope = dias.fold<int>(0, (m, d) => d.importe.centavos > m ? d.importe.centavos : m);
    return [
      for (final dia in dias)
        InkWell(
          key: Key('periodo_dia_${dia.fecha}'),
          onTap: () => _verDia(dia.fecha),
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 6),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(child: Text(diaEnPalabras(dia.fecha))),
                    Text('${pesos(dia.importe)} · ${dia.ventas} venta(s)'),
                  ],
                ),
                const SizedBox(height: 4),
                LinearProgressIndicator(
                  value: tope == 0 ? 0 : dia.importe.centavos / tope,
                  minHeight: 6,
                ),
              ],
            ),
          ),
        ),
    ];
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
