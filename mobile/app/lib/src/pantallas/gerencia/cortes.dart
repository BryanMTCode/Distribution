/// El corte del día desde el teléfono de la oficina.
///
/// Arriba, los cortes que mandan los vendedores desde su teléfono: el camión no
/// se cuenta, se queda con lo que calcula el sistema, y el gerente solo los
/// cierra (`SeccionCortesDeVendedores`, ADR 0002 §82).
///
/// Abajo, los cortes **de un día**, y se pasa de día en día, como las cargas
/// (ADR 0002 §149, §151): «que tengan una vista parecida a la de las cargas».
/// Cada día trae sus camiones: los que se cortaron ese día —el corte es del día
/// en que se hizo: «si hago el corte a las 11 de la noche, es de ese día»— y los
/// que faltan por cortar, con la etiqueta «Sin cortar». El que falta va en el
/// día en que se vende su carga, y si ese día no ha llegado —la carga de la
/// noche es para mañana—, en hoy: es trabajo pendiente y no se esconde. Los que
/// quedaron sin cortar de otros días salen como «También sin cortar».
///
/// Tocar uno sin cortar es el corte contando el camión, para cuando la oficina
/// quiere contar la mercancía ella misma. Se hace con el camión enfrente: quien
/// cuenta está en el patio con el teléfono en la mano. Las reglas las pone el
/// servidor y son las del panel:
///
///   · Se cuenta lo que se QUEDA arriba del camión. El campo vacío vale cero: lo
///     que no se anotó es lo que no está.
///   · El arqueo compara lo que entrega contra lo que el sistema espera.
///   · No se cierra si el teléfono del vendedor no ha subido todo; sin un dato de
///     que terminó, hay que confirmarlo a mano.
///   · Al cerrar, el camión queda en lo contado, el faltante se carga a la cuenta
///     del vendedor y su teléfono recibe el ajuste.
///
/// Cada corte sale una sola vez (§124): el que cerró el gerente desde el corte
/// del vendedor dice en su renglón lo que éste entregó, y cada uno dice su
/// estatus.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/sesion.dart';
import '../../estado/sincronizacion.dart';
import '../../estado/vendedores.dart';
import 'cierres.dart';
import 'comunes.dart';
import 'selector_de_dia.dart';

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

  /// Sin barra cuando va dentro de la pestaña Almacén, que ya pone la suya.
  final bool conBarra;

  @override
  ConsumerState<PantallaCortes> createState() => _EstadoCortes();
}

class _EstadoCortes extends ConsumerState<PantallaCortes> {
  ListaDeCortes? _lista;
  String? _error;

  /// El día cuyos cortes se ven, por el día en que se hicieron; nulo es hoy.
  String? _dia;

  /// Para tirar la respuesta de un día que ya no es el que se ve.
  int _consulta = 0;

  /// Al deslizar para recargar, la sección de los vendedores se vuelve a leer.
  int _vuelta = 0;

  /// Los cortes del vendedor cerrados esta semana, por el folio del corte que
  /// salió de cada uno: su renglón dice lo que entregó.
  Map<String, CorteRecibido> _delVendedor = const {};

  void _leerDelVendedor(Cierres c) {
    if (!mounted) return;
    setState(() => _delVendedor = {
          for (final r in c.recientes)
            if (r.corte!.liquidacionFolio != null) r.corte!.liquidacionFolio!: r.corte!,
        });
  }

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
    final consulta = ++_consulta;
    final dia = _dia ?? diaOperativoDe(ref.read(relojProvider)());
    try {
      final l = await cliente.lista(dia: dia);
      if (!mounted || consulta != _consulta) return;
      setState(() {
        _lista = l;
        _error = null;
      });
    } on Object catch (e) {
      if (!mounted || consulta != _consulta) return;
      setState(() => _error = explicarErrorDeOficina(e));
    }
  }

  void _irA(String dia) {
    setState(() {
      _dia = dia;
      _lista = null;
      _error = null;
    });
    _cargar();
  }

  Future<void> _elegirEnCalendario(String dia, String hoy) async {
    final elegido = await elegirDiaEnCalendario(
      context,
      dia: dia,
      hoy: hoy,
      ayuda: '¿De qué día son los cortes?',
    );
    if (elegido == null || !mounted) return;
    _irA(elegido);
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
    final hoy = diaOperativoDe(ref.watch(relojProvider)());
    final dia = _dia ?? hoy;
    // Del servidor ya vienen los del día; por si acaso, solo esos.
    final delDia = [
      for (final c in l?.cortes ?? const <CorteEnLista>[])
        if (c.hechoEl == dia) c,
    ];
    final porCortar = l?.porCortar ?? const <CargaPorCortar>[];
    final sinCortar = [
      for (final c in porCortar)
        if (diaParaCortar(c.fecha, hoy: hoy) == dia) c,
    ];
    final cuerpo = RefreshIndicator(
      onRefresh: () async {
        setState(() => _vuelta++);
        await _cargar();
      },
      child: ListView(
        key: const Key('lista_cortes'),
        padding: const EdgeInsets.all(16),
        children: [
          SeccionCortesDeVendedores(
            key: ValueKey(_vuelta),
            alCambiar: _cargar,
            alLeer: _leerDelVendedor,
            // Los que ya están abajo, en su día, no se repiten arriba.
            yaListados: {for (final c in l?.cortes ?? const <CorteEnLista>[]) c.folio},
          ),
          const Divider(height: 32),
          // El día, aunque esté cargando: es con lo que uno se mueve.
          SelectorDeDia(
            dia: dia,
            hoy: hoy,
            sufijo: 'cortes',
            textoDeHoy: 'Ver los de hoy',
            // Un día que no ha llegado no tiene cortes.
            ultimoDia: hoy,
            alIr: _irA,
            alCalendario: () => _elegirEnCalendario(dia, hoy),
          ),
          // Los que faltan de otros días: tocar uno lleva a su día.
          _OtrosDiasSinCortar(dia: dia, hoy: hoy, porCortar: porCortar, alIr: _irA),
          if (_error != null) _Aviso(_error!, esError: true),
          if (l == null && _error == null)
            const Padding(
              padding: EdgeInsets.all(32),
              child: Center(child: CircularProgressIndicator()),
            ),
          if (l != null) ...[
            if (delDia.isEmpty && sinCortar.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 8),
                child: Text(
                  'No hay cortes de este día. Muévete con las flechas.',
                  key: Key('cortes_vacio'),
                ),
              )
            else
              Padding(
                padding: const EdgeInsets.only(top: 4, bottom: 4),
                child: Text(
                  _resumen(delDia, sinCortar: sinCortar.length),
                  key: Key('dia_de_cortes_$dia'),
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
              ),
            // Lo que falta va primero: es lo que se atiende.
            for (final c in sinCortar) _renglonSinCortar(c, dia),
            if (sinCortar.isNotEmpty)
              const Padding(
                padding: EdgeInsets.only(bottom: 8),
                child: Text(
                  'Toca uno sin cortar para cortarlo contando el camión tú mismo. Si el '
                  'vendedor ya mandó su corte, ciérralo arriba.',
                ),
              ),
            for (final c in delDia) _renglon(c),
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

  Widget _renglonSinCortar(CargaPorCortar c, String dia) => Card(
        child: ListTile(
          key: Key('por_cortar_${c.folio}'),
          leading: Icon(
            Icons.fact_check_outlined,
            color: c.dias > 0 ? Theme.of(context).colorScheme.error : null,
          ),
          title: Text('${c.vendedor} · ${c.camion}'),
          subtitle: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Carga ${c.folio}${paraVender(c.fecha, dia: dia)}\n'
                '${c.ventas} venta(s) · ${pesos(c.importe)}'
                '${c.dias > 0 ? '\nHace ${c.dias} día(s) sin cortar' : ''}',
              ),
              const SizedBox(height: 6),
              EtiquetaDeEstatus(
                key: Key('estado_por_cortar_${c.folio}'),
                texto: 'Sin cortar',
                fondo: Colors.orange.shade100,
                letra: Colors.orange.shade900,
              ),
            ],
          ),
          isThreeLine: true,
          trailing: const Icon(Icons.chevron_right),
          onTap: () => _abrirDetalle((cl) => cl.abrir(c.cargaId)),
        ),
      );

  Widget _renglon(CorteEnLista c) {
    final delVendedor = _delVendedor[c.folio];
    return ListTile(
      key: Key('corte_${c.folio}'),
      contentPadding: EdgeInsets.zero,
      leading: Icon(
        c.estado == 'cerrada' ? Icons.lock_outline : Icons.edit_note,
      ),
      title: Text(c.vendedor),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '${c.folio}'
            '${c.faltantes > 0 ? ' · ${c.faltantes} producto(s) con diferencia' : ''}'
            '${c.diferenciaEfectivo.centavos != 0 ? ' · efectivo ${pesos(c.diferenciaEfectivo)}' : ''}'
            // El que mandó el vendedor desde su teléfono: lo que entregó.
            '${delVendedor == null ? '' : '\nLo mandó el vendedor · ${efectivoEnPalabras(delVendedor)}'}',
          ),
          const SizedBox(height: 6),
          _EtiquetaDeCorte(estado: c.estado, key: Key('estado_corte_${c.folio}')),
        ],
      ),
      isThreeLine: true,
      trailing: const Icon(Icons.chevron_right),
      onTap: () => _abrirDetalle((cl) => cl.ver(c.id)),
    );
  }
}

/// Cómo va un corte (ADR 0002 §149): abierto mientras se cuenta; cuadrado o con
/// diferencia después del arqueo, esperando que lo cierren; cerrado.
String etiquetaDeEstadoDeCorte(String estado) => switch (estado) {
      'abierta' => 'Abierto',
      'cuadrada' => 'Cuadrado, falta cerrar',
      'con_diferencia' => 'Con diferencia, falta cerrar',
      'cerrada' => 'Cerrado',
      _ => estado,
    };

class _EtiquetaDeCorte extends StatelessWidget {
  const _EtiquetaDeCorte({super.key, required this.estado});

  final String estado;

  @override
  Widget build(BuildContext context) {
    final colores = Theme.of(context).colorScheme;
    final (fondo, letra) = switch (estado) {
      'abierta' => (Colors.orange.shade100, Colors.orange.shade900),
      'cuadrada' => (colores.primaryContainer, colores.onPrimaryContainer),
      'con_diferencia' => (colores.errorContainer, colores.onErrorContainer),
      'cerrada' => (Colors.green.shade100, Colors.green.shade900),
      _ => (colores.surfaceContainerHighest, colores.onSurfaceVariant),
    };
    return EtiquetaDeEstatus(texto: etiquetaDeEstadoDeCorte(estado), fondo: fondo, letra: letra);
  }
}

/// «3 corte(s) · 2 cerrados · 1 sin cerrar · 1 sin cortar»: el día de reojo.
String _resumen(List<CorteEnLista> cortes, {required int sinCortar}) {
  final cerrados = cortes.where((c) => c.estado == 'cerrada').length;
  final porCerrar = cortes.length - cerrados;
  return [
    if (cortes.isNotEmpty) '${cortes.length} corte(s)',
    if (cerrados > 0) '$cerrados ${cerrados == 1 ? 'cerrado' : 'cerrados'}',
    if (porCerrar > 0) '$porCerrar sin cerrar',
    if (sinCortar > 0) '$sinCortar sin cortar',
  ].join(' · ');
}

/// El día en que se ve una carga que falta cortar: el día en que se vende, y si
/// no ha llegado —la de la noche es para mañana—, hoy (ADR 0002 §151).
String diaParaCortar(String fecha, {required String hoy}) =>
    fecha.compareTo(hoy) > 0 ? hoy : fecha;

/// «También sin cortar: martes 20 (1)»: los que faltan de otros días.
class _OtrosDiasSinCortar extends StatelessWidget {
  const _OtrosDiasSinCortar({
    required this.dia,
    required this.hoy,
    required this.porCortar,
    required this.alIr,
  });

  final String dia;
  final String hoy;
  final List<CargaPorCortar> porCortar;
  final void Function(String) alIr;

  @override
  Widget build(BuildContext context) {
    final porDia = <String, int>{};
    for (final c in porCortar) {
      final d = diaParaCortar(c.fecha, hoy: hoy);
      if (d != dia) porDia[d] = (porDia[d] ?? 0) + 1;
    }
    if (porDia.isEmpty) return const SizedBox.shrink();
    final dias = porDia.keys.toList()..sort();
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Wrap(
        spacing: 6,
        runSpacing: 4,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          const Text('También sin cortar:'),
          for (final d in dias)
            ActionChip(
              key: Key('sin_cortar_$d'),
              label: Text('${diaEnPalabras(d)} (${porDia[d]})'),
              onPressed: () => alIr(d),
            ),
        ],
      ),
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
