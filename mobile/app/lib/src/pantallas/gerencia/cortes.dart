/// El corte del día desde el teléfono de la oficina.
///
/// El corte se hace con el camión enfrente: quien cuenta está en el patio con el
/// teléfono en la mano. Las reglas las pone el servidor y son las del panel:
///
///   · Se cuenta lo que se QUEDA arriba del camión. El campo vacío vale cero: lo
///     que no se anotó es lo que no está.
///   · El arqueo compara lo que entrega contra lo que el sistema espera.
///   · No se cierra si el teléfono del vendedor no ha subido todo; sin un dato de
///     que terminó, hay que confirmarlo a mano.
///   · Al cerrar, el camión queda en lo contado, el faltante se carga a la cuenta
///     del vendedor y su teléfono recibe el ajuste.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/sesion.dart';
import '../../estado/sincronizacion.dart';
import '../../estado/vendedores.dart';
import 'comunes.dart';

ClienteCortes? _cliente(WidgetRef ref) {
  final t = ref.read(transporteProvider);
  return t == null ? null : ClienteCortes(t);
}

class _Aviso extends StatelessWidget {
  const _Aviso(this.texto, {this.esError = false, this.clave});

  final String texto;
  final bool esError;
  final String? clave;

  @override
  Widget build(BuildContext context) {
    final c = Theme.of(context).colorScheme;
    return Container(
      key: clave == null ? null : Key(clave!),
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: esError ? c.errorContainer : c.secondaryContainer,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(
        texto,
        style: TextStyle(color: esError ? c.onErrorContainer : c.onSecondaryContainer),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// La lista
// ---------------------------------------------------------------------------
class PantallaCortes extends ConsumerStatefulWidget {
  const PantallaCortes({super.key, this.conBarra = true});

  /// Sin barra cuando va dentro de la pestaña Camiones, que ya pone la suya.
  final bool conBarra;

  @override
  ConsumerState<PantallaCortes> createState() => _EstadoCortes();
}

class _EstadoCortes extends ConsumerState<PantallaCortes> {
  ListaDeCortes? _lista;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _cargar());
  }

  Future<void> _cargar() async {
    final cliente = _cliente(ref);
    if (cliente == null) {
      setState(() => _error = 'No hay sesión en línea. Sal y vuelve a entrar con señal.');
      return;
    }
    try {
      final l = await cliente.lista();
      if (!mounted) return;
      setState(() {
        _lista = l;
        _error = null;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = explicarErrorDeOficina(e));
    }
  }

  Future<void> _abrirDetalle(Future<CorteDelDia> Function(ClienteCortes) cual) async {
    final cliente = _cliente(ref);
    if (cliente == null) return;
    try {
      final corte = await cual(cliente);
      if (!mounted) return;
      await Navigator.of(context).push(
        MaterialPageRoute(builder: (_) => PantallaCorte(inicial: corte)),
      );
      await _cargar();
    } on Object catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(explicarErrorDeOficina(e))),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = _lista;
    final estilo = Theme.of(context).textTheme;
    final cuerpo = RefreshIndicator(
      onRefresh: _cargar,
      child: ListView(
        key: const Key('lista_cortes'),
        padding: const EdgeInsets.all(16),
        children: [
          if (_error != null) _Aviso(_error!, esError: true),
          if (l == null && _error == null)
            const Padding(
              padding: EdgeInsets.all(32),
              child: Center(child: CircularProgressIndicator()),
            ),
          if (l != null) ...[
            Text('Por cortar', style: estilo.titleMedium),
            if (l.porCortar.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 8),
                child: Text('Todos los camiones están cortados.'),
              ),
            for (final c in l.porCortar)
              Card(
                child: ListTile(
                  key: Key('por_cortar_${c.folio}'),
                  leading: Icon(
                    Icons.fact_check_outlined,
                    color: c.dias > 0 ? Theme.of(context).colorScheme.error : null,
                  ),
                  title: Text('${c.vendedor} · ${c.camion}'),
                  subtitle: Text(
                    '${diaEnPalabras(c.fecha)} · carga ${c.folio}\n'
                    '${c.ventas} venta(s) · ${pesos(c.importe)}'
                    '${c.dias > 0 ? '\nHace ${c.dias} día(s) sin cortar' : ''}',
                  ),
                  isThreeLine: true,
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => _abrirDetalle((cl) => cl.abrir(c.cargaId)),
                ),
              ),
            const SizedBox(height: 16),
            Text('Cortes recientes', style: estilo.titleMedium),
            if (l.cortes.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 8),
                child: Text('Todavía no hay cortes.'),
              ),
            for (final c in l.cortes)
              ListTile(
                key: Key('corte_${c.folio}'),
                contentPadding: EdgeInsets.zero,
                leading: Icon(
                  c.estado == 'cerrada' ? Icons.lock_outline : Icons.edit_note,
                ),
                title: Text('${c.vendedor} · ${diaEnPalabras(c.fecha)}'),
                subtitle: Text(
                  '${c.folio} · ${c.estado == 'cerrada' ? 'cerrado' : 'abierto'}'
                  '${c.faltantes > 0 ? ' · ${c.faltantes} producto(s) con diferencia' : ''}'
                  '${c.diferenciaEfectivo.centavos != 0 ? ' · efectivo ${pesos(c.diferenciaEfectivo)}' : ''}',
                ),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => _abrirDetalle((cl) => cl.ver(c.id)),
              ),
          ],
        ],
      ),
    );
    if (!widget.conBarra) return cuerpo;
    return Scaffold(
      key: const Key('pantalla_cortes'),
      appBar: AppBar(title: const Text('Corte del día')),
      body: cuerpo,
    );
  }
}

// ---------------------------------------------------------------------------
// Un corte: contar, arqueo, cerrar
// ---------------------------------------------------------------------------
class PantallaCorte extends ConsumerStatefulWidget {
  const PantallaCorte({super.key, required this.inicial});

  final CorteDelDia inicial;

  @override
  ConsumerState<PantallaCorte> createState() => _EstadoCorte();
}

class _EstadoCorte extends ConsumerState<PantallaCorte> {
  late CorteDelDia _corte = widget.inicial;
  String? _error;
  bool _ocupado = false;
  final Map<String, TextEditingController> _contados = {};
  final _efectivo = TextEditingController();
  final _observaciones = TextEditingController();

  @override
  void initState() {
    super.initState();
    _llenarCampos();
  }

  @override
  void dispose() {
    for (final c in _contados.values) {
      c.dispose();
    }
    _efectivo.dispose();
    _observaciones.dispose();
    super.dispose();
  }

  /// Lo ya contado se muestra; lo que vale cero, vacío —el conteo nace en cero y
  /// un «0» escrito en cada campo invitaría a dejarlo así sin contar—.
  void _llenarCampos() {
    for (final r in _corte.renglones) {
      final campo = _contados.putIfAbsent(r.id, TextEditingController.new);
      final contada = cantidadLegible(r.contada);
      campo.text = contada == '0' ? '' : contada;
    }
    if (_corte.arqueoHecho) {
      _efectivo.text = _corte.efectivoEntregado.texto;
    }
  }

  Future<void> _hacer(Future<CorteDelDia> Function(ClienteCortes) accion) async {
    final cliente = _cliente(ref);
    if (cliente == null) return;
    setState(() => _ocupado = true);
    try {
      final c = await accion(cliente);
      if (!mounted) return;
      setState(() {
        _corte = c;
        _error = null;
        _ocupado = false;
        _llenarCampos();
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() {
        _error = explicarErrorDeOficina(e);
        _ocupado = false;
      });
    }
  }

  Future<void> _guardarConteo() => _hacer(
        (c) => c.contar(_corte.id, {
          for (final e in _contados.entries) e.key: e.value.text.trim(),
        }),
      );

  Future<void> _guardarArqueo() => _hacer(
        (c) => c.arqueo(
          _corte.id,
          efectivo: _efectivo.text.trim(),
          observaciones: _observaciones.text.trim(),
        ),
      );

  Future<void> _cerrar() async {
    final confirmado = await showDialog<bool>(
      context: context,
      builder: (_) => _DialogoCerrar(corte: _corte),
    );
    if (confirmado == null) return;
    await _hacer((c) => c.cerrar(_corte.id, confirmoSincronizado: confirmado));
  }

  @override
  Widget build(BuildContext context) {
    final c = _corte;
    final estilo = Theme.of(context).textTheme;
    final colores = Theme.of(context).colorScheme;
    return Scaffold(
      key: const Key('pantalla_corte'),
      appBar: AppBar(title: Text('Corte ${c.folio}')),
      bottomNavigationBar: !c.abierto
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
                child: FilledButton.icon(
                  key: const Key('boton_cerrar_corte'),
                  onPressed: _ocupado ? null : _cerrar,
                  icon: const Icon(Icons.lock_outline),
                  label: const Padding(
                    padding: EdgeInsets.symmetric(vertical: 12),
                    child: Text('Cerrar el día'),
                  ),
                ),
              ),
            ),
      body: ListView(
        key: const Key('lista_corte'),
        padding: const EdgeInsets.all(16),
        children: [
          Text('${c.vendedor} · ${c.camion}', style: estilo.titleMedium),
          Text(
            '${encabezadoDelDia(c.fecha, hoy: diaOperativoDe(ref.read(relojProvider)()))}'
            ' · carga ${c.carga} · ${c.abierto ? 'abierto' : 'cerrado'}',
          ),
          const SizedBox(height: 12),
          if (_error != null) _Aviso(_error!, esError: true, clave: 'aviso_corte_error'),
          if (c.mensaje != null && _error == null) _Aviso(c.mensaje!, clave: 'aviso_corte'),
          if (c.abierto) ...[
            for (final b in c.bloqueos) _Aviso(b, esError: true),
            if (c.bloqueos.isEmpty && !c.respaldado && c.motivoSinRespaldo != null)
              _Aviso(
                'Sin dato de que el teléfono terminó de subir (${c.motivoSinRespaldo}). '
                'Al cerrar te pedirá confirmarlo.',
              ),
          ],
          if (!c.abierto && c.totalCargado.centavos > 0)
            _Aviso(
              'Se cargaron ${pesos(c.totalCargado)} a la cuenta del vendedor.',
              esError: true,
            ),

          // ---- El conteo ----
          const SizedBox(height: 8),
          Text('Lo que se queda arriba del camión', style: estilo.titleMedium),
          if (c.abierto)
            Text(
              'Cuenta lo que hay arriba, en piezas. Lo que dejes vacío cuenta como cero.',
              style: estilo.bodySmall,
            ),
          for (final r in c.renglones) _renglon(r, c.abierto, colores),
          if (c.abierto)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: OutlinedButton(
                key: const Key('boton_guardar_conteo'),
                onPressed: _ocupado ? null : _guardarConteo,
                child: const Text('Guardar el conteo'),
              ),
            ),

          // ---- El arqueo ----
          const Divider(height: 32),
          Text('El efectivo', style: estilo.titleMedium),
          Text('El sistema espera ${pesos(c.efectivoEsperado)}.',
              key: const Key('efectivo_esperado_corte')),
          if (c.arqueoHecho)
            Text(
              'Entregó ${pesos(c.efectivoEntregado)}'
              '${c.diferenciaEfectivo.centavos == 0 ? ' · cuadra' : ' · diferencia ${pesos(c.diferenciaEfectivo)}'}',
              style: TextStyle(
                color: c.diferenciaEfectivo.centavos < 0 ? colores.error : null,
                fontWeight: FontWeight.w600,
              ),
            ),
          if (c.abierto) ...[
            const SizedBox(height: 8),
            TextField(
              key: const Key('campo_efectivo_entregado'),
              controller: _efectivo,
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              decoration: const InputDecoration(
                labelText: 'Efectivo que entrega',
                prefixText: r'$ ',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: _observaciones,
              maxLength: 600,
              decoration: const InputDecoration(
                labelText: 'Observaciones (opcional)',
                border: OutlineInputBorder(),
              ),
            ),
            OutlinedButton(
              key: const Key('boton_guardar_arqueo'),
              onPressed: _ocupado ? null : _guardarArqueo,
              child: const Text('Guardar el arqueo'),
            ),
          ],
          const SizedBox(height: 24),
        ],
      ),
    );
  }

  Widget _renglon(RenglonDelCorte r, bool abierto, ColorScheme colores) {
    final diferencia = cantidadLegible(r.diferencia);
    return Padding(
      key: Key('renglon_corte_${r.nombre}'),
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(r.nombre),
                Text(
                  'Esperado ${cantidadLegible(r.esperado)} ${r.unidadBase} · '
                  'cargó ${cantidadLegible(r.cargada)} · vendió ${cantidadLegible(r.vendida)}'
                  '${cantidadLegible(r.merma) != '0' ? ' · merma ${cantidadLegible(r.merma)}' : ''}',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                if (!r.cuadra)
                  Text(
                    r.diferencia.startsWith('-')
                        ? 'Faltan ${diferencia.substring(1)}'
                        : 'Sobran $diferencia',
                    style: TextStyle(color: colores.error, fontWeight: FontWeight.w600),
                  ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          SizedBox(
            width: 80,
            child: abierto
                ? TextField(
                    key: Key('contado_${r.nombre}'),
                    controller: _contados[r.id],
                    keyboardType: const TextInputType.numberWithOptions(decimal: true),
                    textAlign: TextAlign.center,
                    decoration: const InputDecoration(isDense: true, border: OutlineInputBorder()),
                  )
                : Text(
                    cantidadLegible(r.contada),
                    textAlign: TextAlign.center,
                    style: const TextStyle(fontWeight: FontWeight.w600),
                  ),
          ),
        ],
      ),
    );
  }
}

/// Confirmar el cierre. Devuelve si se confirmó que el teléfono terminó de
/// sincronizar (solo se pregunta cuando no hay un dato que lo diga), o nulo si se
/// canceló.
class _DialogoCerrar extends StatefulWidget {
  const _DialogoCerrar({required this.corte});

  final CorteDelDia corte;

  @override
  State<_DialogoCerrar> createState() => _EstadoDialogoCerrar();
}

class _EstadoDialogoCerrar extends State<_DialogoCerrar> {
  bool _confirmo = false;

  @override
  Widget build(BuildContext context) {
    final c = widget.corte;
    final pideConfirmar = !c.respaldado;
    return AlertDialog(
      title: const Text('¿Cerrar el día?'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'El camión se queda con lo que contaste. Lo que falte se carga a la '
              'cuenta del vendedor, y su teléfono recibe el ajuste. Ya no se edita.',
            ),
            if (!c.arqueoHecho) ...[
              const SizedBox(height: 8),
              Text(
                'Ojo: no has guardado el arqueo. Sin él, el efectivo no se le cobra: '
                'nadie lo contó.',
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ],
            if (pideConfirmar)
              CheckboxListTile(
                key: const Key('casilla_confirmo_sincronizado'),
                contentPadding: EdgeInsets.zero,
                value: _confirmo,
                onChanged: (v) => setState(() => _confirmo = v ?? false),
                title: const Text('Confirmo que el teléfono del vendedor terminó de sincronizar'),
              ),
          ],
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Todavía no')),
        FilledButton(
          key: const Key('boton_cerrar_de_verdad'),
          onPressed: pideConfirmar && !_confirmo
              ? null
              : () => Navigator.of(context).pop(_confirmo),
          child: const Text('Cerrar'),
        ),
      ],
    );
  }
}
