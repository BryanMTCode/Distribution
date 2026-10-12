/// Los objetivos, desde la app de la oficina (ADR 0002 §104).
///
/// «Quiero poder agregar los objetivos en la app; los objetivos pueden ser por
/// el periodo que yo decida, incluso por día.»
///
///   · La lista: los que corren, los que vienen y los que terminaron hace poco,
///     cada uno con lo vendido contra el objetivo y la marca de lo esperado a la
///     fecha —la misma barra del tablero—.
///   · «Nuevo objetivo»: de una ruta o de toda la empresa; hoy, mañana, esta
///     semana, este mes, un día o unas fechas. Un mes completo de una ruta es
///     su objetivo del mes, el de la barra del tablero.
///   · «Cada día»: con varios días escogidos, el mismo objetivo para cada uno,
///     uno por día —«esta semana, todos los días 3,500»— (§109).
///
/// En línea: el avance lo calcula el servidor con las ventas del rango.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/oficina_extra.dart';
import '../../estado/sesion.dart';
import '../../estado/vendedores.dart';
import 'comunes.dart';

class PantallaObjetivos extends ConsumerStatefulWidget {
  const PantallaObjetivos({super.key});

  @override
  ConsumerState<PantallaObjetivos> createState() => _EstadoObjetivos();
}

class _EstadoObjetivos extends ConsumerState<PantallaObjetivos> {
  Objetivos? _datos;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _cargar());
  }

  Future<void> _cargar([Future<Objetivos> Function(ClienteObjetivos c)? accion]) async {
    final cliente = ref.read(clienteObjetivosProvider);
    if (cliente == null) {
      setState(() => _error = 'No hay sesión en línea. Sal y vuelve a entrar con señal.');
      return;
    }
    try {
      final datos = await (accion ?? (c) => c.listar())(cliente);
      if (!mounted) return;
      setState(() {
        _datos = datos;
        _error = null;
      });
    } on Object catch (e) {
      if (!mounted) return;
      if (accion != null) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(explicarErrorDeOficina(e))),
        );
      } else {
        setState(() => _error = explicarErrorDeOficina(e));
      }
    }
  }

  Future<void> _nuevo() async {
    final datos = _datos;
    if (datos == null) return;
    final pedido = await showModalBottomSheet<_Pedido>(
      context: context,
      isScrollControlled: true,
      builder: (_) => _NuevoObjetivo(rutas: datos.rutas, hoy: ref.read(relojProvider)()),
    );
    if (pedido == null || !mounted) return;
    await _cargar(
      (c) => c.fijar(
        rutaId: pedido.rutaId,
        desde: pedido.desde,
        hasta: pedido.hasta,
        objetivoVenta: pedido.venta,
        objetivoVisitas: pedido.visitas,
        nota: pedido.nota,
        porDia: pedido.porDia,
      ),
    );
  }

  Future<void> _quitar(ObjetivoConAvance o) async {
    final si = await showDialog<bool>(
      context: context,
      builder: (contexto) => AlertDialog(
        title: const Text('¿Quitar este objetivo?'),
        content: Text('${o.ruta ?? 'Toda la empresa'}, ${_periodoEnPalabras(o)}.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(contexto, false), child: const Text('No')),
          FilledButton(
            key: const Key('confirmar_quitar_objetivo'),
            onPressed: () => Navigator.pop(contexto, true),
            child: const Text('Quitar'),
          ),
        ],
      ),
    );
    if (si != true || !mounted) return;
    await _cargar((c) => c.borrar(o.id));
  }

  @override
  Widget build(BuildContext context) {
    final datos = _datos;
    final grupos = [
      ('en_curso', 'En curso'),
      ('por_empezar', 'Por empezar'),
      ('terminado', 'Terminados'),
    ];
    return Scaffold(
      key: const Key('pantalla_objetivos'),
      appBar: AppBar(
        title: const Text('Objetivos'),
        actions: [
          IconButton(
            tooltip: 'Volver a consultar',
            icon: const Icon(Icons.refresh),
            onPressed: _cargar,
          ),
        ],
      ),
      floatingActionButton: datos != null && datos.puedeFijar
          ? FloatingActionButton.extended(
              key: const Key('boton_nuevo_objetivo'),
              onPressed: _nuevo,
              icon: const Icon(Icons.flag_outlined),
              label: const Text('Nuevo objetivo'),
            )
          : null,
      body: RefreshIndicator(
        onRefresh: _cargar,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 96),
          children: [
            if (_error != null) AvisoDeOficina(_error!, esError: true),
            if (datos == null && _error == null)
              const Padding(
                padding: EdgeInsets.all(32),
                child: Center(child: CircularProgressIndicator()),
              ),
            if (datos != null && datos.objetivos.isEmpty)
              const Padding(
                padding: EdgeInsets.all(24),
                child: Text(
                  'Todavía no hay objetivos. Toca «Nuevo objetivo»: puede ser de un '
                  'día, de una semana o de las fechas que quieras.',
                  key: Key('objetivos_vacio'),
                  textAlign: TextAlign.center,
                ),
              ),
            for (final (estado, titulo) in grupos)
              if (datos != null && datos.objetivos.any((o) => o.estado == estado)) ...[
                Padding(
                  padding: const EdgeInsets.only(top: 8, bottom: 4),
                  child: Text(titulo, style: Theme.of(context).textTheme.titleMedium),
                ),
                for (final o in datos.objetivos.where((o) => o.estado == estado))
                  _Tarjeta(
                    objetivo: o,
                    alQuitar: datos.puedeFijar ? () => _quitar(o) : null,
                  ),
              ],
          ],
        ),
      ),
    );
  }
}

String _periodoEnPalabras(ObjetivoConAvance o) => o.desde == o.hasta
    ? diaEnPalabras(o.desde)
    : 'del ${diaEnPalabras(o.desde)} al ${diaEnPalabras(o.hasta)}';

class _Tarjeta extends StatelessWidget {
  const _Tarjeta({required this.objetivo, this.alQuitar});

  final ObjetivoConAvance objetivo;
  final VoidCallback? alQuitar;

  @override
  Widget build(BuildContext context) {
    final o = objetivo;
    final estilo = Theme.of(context).textTheme;
    final logrado = double.tryParse(o.logrado ?? '') ?? 0;
    return Card(
      key: Key('objetivo_${o.id}'),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 8, 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    o.ruta ?? 'Toda la empresa',
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                ),
                if (o.clase == 'mes')
                  const Padding(
                    padding: EdgeInsets.only(right: 4),
                    child: Chip(label: Text('del mes'), visualDensity: VisualDensity.compact),
                  ),
                if (alQuitar != null)
                  IconButton(
                    key: Key('quitar_objetivo_${o.id}'),
                    tooltip: 'Quitar',
                    icon: const Icon(Icons.delete_outline),
                    onPressed: alQuitar,
                  ),
              ],
            ),
            Text(
              '${_periodoEnPalabras(o)}${o.dias > 1 ? ' · ${o.dias} días' : ''}',
              style: estilo.bodySmall,
            ),
            const SizedBox(height: 8),
            Text(
              '${pesos(Dinero.deTexto(o.venta))} de ${pesos(Dinero.deTexto(o.objetivoVenta))}'
              '${o.logrado == null ? '' : ' · ${o.logrado}%'}',
              key: Key('avance_${o.id}'),
              style: const TextStyle(fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 6),
            BarraDeAvance(
              logrado: logrado,
              esperado: double.tryParse(o.esperado) ?? 0,
              semaforo: o.semaforo,
            ),
            const SizedBox(height: 4),
            Text(
              [
                if (o.estado == 'en_curso') 'Esperado a hoy: ${o.esperado}%',
                if (o.objetivoVisitas != null)
                  '${o.visitas} de ${o.objetivoVisitas} visitas'
                else
                  '${o.visitas} visitas',
                if (o.nota != null) o.nota!,
              ].join(' · '),
              style: estilo.bodySmall,
            ),
          ],
        ),
      ),
    );
  }
}

class _Pedido {
  const _Pedido(this.rutaId, this.desde, this.hasta, this.venta, this.visitas, this.nota,
      {this.porDia = false});

  final String? rutaId;
  final String desde;
  final String hasta;
  final String venta;
  final int? visitas;
  final String? nota;
  final bool porDia;
}

/// Uno por día se pone para un mes a lo más: lo mismo que acepta el servidor.
const diasMaximosPorDia = 31;

/// El formulario del objetivo nuevo, en una hoja que sube desde abajo.
class _NuevoObjetivo extends StatefulWidget {
  const _NuevoObjetivo({required this.rutas, required this.hoy});

  final List<RutaDeObjetivo> rutas;
  final DateTime hoy;

  @override
  State<_NuevoObjetivo> createState() => _EstadoNuevoObjetivo();
}

class _EstadoNuevoObjetivo extends State<_NuevoObjetivo> {
  String? _ruta;
  late DateTime _desde;
  late DateTime _hasta;
  String _rapido = 'hoy';
  bool _porDia = false;
  final _venta = TextEditingController();
  final _visitas = TextEditingController();
  final _nota = TextEditingController();
  String? _error;

  DateTime get _dia {
    final h = widget.hoy.toLocal();
    return DateTime(h.year, h.month, h.day);
  }

  /// Los días del periodo escogido, contando los dos extremos. Se cuenta en
  /// UTC para que un cambio de horario no se coma ni agregue un día.
  int get _dias =>
      DateTime.utc(_hasta.year, _hasta.month, _hasta.day)
          .difference(DateTime.utc(_desde.year, _desde.month, _desde.day))
          .inDays +
      1;

  /// «Cada día» tiene sentido con dos días o más, y hasta un mes.
  bool get _puedeSerPorDia => _dias > 1 && _dias <= diasMaximosPorDia;

  @override
  void initState() {
    super.initState();
    _desde = _hasta = _dia;
  }

  @override
  void dispose() {
    _venta.dispose();
    _visitas.dispose();
    _nota.dispose();
    super.dispose();
  }

  void _rapidoA(String clave) {
    final hoy = _dia;
    final (desde, hasta) = switch (clave) {
      'manana' => (hoy.add(const Duration(days: 1)), hoy.add(const Duration(days: 1))),
      'semana' => (
          hoy.subtract(Duration(days: hoy.weekday - 1)),
          hoy.add(Duration(days: 7 - hoy.weekday)),
        ),
      'mes' => (DateTime(hoy.year, hoy.month), DateTime(hoy.year, hoy.month + 1, 0)),
      _ => (hoy, hoy),
    };
    setState(() {
      _rapido = clave;
      _desde = desde;
      _hasta = hasta;
    });
  }

  Future<void> _unDia() async {
    final dia = await showDatePicker(
      context: context,
      initialDate: _desde,
      firstDate: _dia.subtract(const Duration(days: 366)),
      lastDate: _dia.add(const Duration(days: 366)),
      helpText: '¿Qué día?',
    );
    if (dia == null) return;
    setState(() {
      _rapido = 'un_dia';
      _desde = _hasta = dia;
    });
  }

  Future<void> _fechas() async {
    final rango = await showDateRangePicker(
      context: context,
      firstDate: _dia.subtract(const Duration(days: 366)),
      lastDate: _dia.add(const Duration(days: 366)),
      initialDateRange: DateTimeRange(start: _desde, end: _hasta),
      helpText: 'Elige los días',
      saveText: 'Listo',
    );
    if (rango == null) return;
    setState(() {
      _rapido = 'fechas';
      _desde = rango.start;
      _hasta = rango.end;
    });
  }

  void _guardar() {
    final venta = leerPesosDeObjetivo(_venta.text);
    if (venta == null) {
      setState(() => _error = 'Escribe cuánto se tiene que vender.');
      return;
    }
    final visitas = int.tryParse(_visitas.text.trim());
    Navigator.of(context).pop(
      _Pedido(
        _ruta,
        diaOperativoDe(_desde),
        diaOperativoDe(_hasta),
        venta,
        visitas != null && visitas > 0 ? visitas : null,
        _nota.text.trim().isEmpty ? null : _nota.text.trim(),
        porDia: _porDia && _puedeSerPorDia,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final desde = diaOperativoDe(_desde);
    final hasta = diaOperativoDe(_hasta);
    return Padding(
      padding: EdgeInsets.fromLTRB(16, 16, 16, 16 + MediaQuery.of(context).viewInsets.bottom),
      child: SingleChildScrollView(
        child: Column(
          key: const Key('forma_objetivo'),
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('Nuevo objetivo', style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 12),
            DropdownButtonFormField<String?>(
              key: const Key('campo_ruta_objetivo'),
              initialValue: _ruta,
              isExpanded: true,
              decoration: const InputDecoration(
                labelText: 'De quién',
                border: OutlineInputBorder(),
              ),
              items: [
                const DropdownMenuItem(value: null, child: Text('Toda la empresa')),
                for (final r in widget.rutas)
                  DropdownMenuItem(value: r.id, child: Text('${r.codigo} · ${r.nombre}')),
              ],
              onChanged: (v) => setState(() => _ruta = v),
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 4,
              children: [
                for (final (clave, etiqueta) in const [
                  ('hoy', 'Hoy'),
                  ('manana', 'Mañana'),
                  ('semana', 'Esta semana'),
                  ('mes', 'Este mes'),
                ])
                  ChoiceChip(
                    key: Key('objetivo_$clave'),
                    label: Text(etiqueta),
                    selected: _rapido == clave,
                    onSelected: (_) => _rapidoA(clave),
                  ),
                ChoiceChip(
                  key: const Key('objetivo_un_dia'),
                  avatar: const Icon(Icons.event, size: 18),
                  label: const Text('Un día…'),
                  selected: _rapido == 'un_dia',
                  onSelected: (_) => _unDia(),
                ),
                ChoiceChip(
                  key: const Key('objetivo_fechas'),
                  avatar: const Icon(Icons.date_range, size: 18),
                  label: const Text('Fechas…'),
                  selected: _rapido == 'fechas',
                  onSelected: (_) => _fechas(),
                ),
              ],
            ),
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 8),
              child: Text(
                desde == hasta
                    ? 'Para el ${diaEnPalabras(desde)}'
                    : 'Del ${diaEnPalabras(desde)} al ${diaEnPalabras(hasta)}',
                key: const Key('periodo_del_objetivo'),
                style: const TextStyle(fontWeight: FontWeight.w600),
              ),
            ),
            if (_puedeSerPorDia)
              SwitchListTile(
                key: const Key('objetivo_cada_dia'),
                contentPadding: EdgeInsets.zero,
                value: _porDia,
                onChanged: (v) => setState(() => _porDia = v),
                title: const Text('Cada día'),
                subtitle: Text(
                  _porDia
                      ? '$_dias objetivos, uno por día, cada uno de lo que escribas abajo'
                      : 'Un solo objetivo para los $_dias días juntos',
                  key: const Key('explicacion_cada_dia'),
                ),
              ),
            TextField(
              key: const Key('campo_venta_objetivo'),
              controller: _venta,
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              inputFormatters: [FilteringTextInputFormatter.allow(RegExp(r'[0-9.,$]'))],
              onChanged: (_) => setState(() {}),
              decoration: InputDecoration(
                labelText: _porDia && _puedeSerPorDia ? 'Vender cada día' : 'Vender',
                prefixText: r'$ ',
                border: const OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('campo_visitas_objetivo'),
              controller: _visitas,
              keyboardType: TextInputType.number,
              inputFormatters: [FilteringTextInputFormatter.digitsOnly],
              decoration: const InputDecoration(
                labelText: 'Visitas (opcional)',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('campo_nota_objetivo'),
              controller: _nota,
              maxLength: 300,
              decoration: const InputDecoration(
                labelText: 'Nota (opcional)',
                border: OutlineInputBorder(),
              ),
            ),
            if (_error != null) AvisoDeOficina(_error!, esError: true),
            FilledButton.icon(
              key: const Key('boton_guardar_objetivo'),
              onPressed: _guardar,
              icon: const Icon(Icons.check),
              label: const Text('Guardar objetivo'),
            ),
          ],
        ),
      ),
    );
  }
}

/// «3,000» o «$3,000.50» → «3000.00». Nulo si no se entiende o es cero.
String? leerPesosDeObjetivo(String texto) {
  final limpio = texto.trim().replaceAll(',', '').replaceAll(r'$', '').replaceAll(' ', '');
  final m = RegExp(r'^(\d{1,9})(?:\.(\d{1,2}))?$').firstMatch(limpio);
  if (m == null) return null;
  final valor = '${int.parse(m.group(1)!)}.${(m.group(2) ?? '').padRight(2, '0')}';
  return valor == '0.00' ? null : valor;
}
