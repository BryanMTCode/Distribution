/// Las pantallas del dashboard dentro de la app de la oficina (ADR 0002 §194).
///
/// «Solo quiero poder mover el almacén, agregar productos, ese tipo de cosas»
/// desde la app, «sin tener que actualizar la app a los vendedores».
///
///   · **Mover y capturar**, una parte de Almacén: los botones que el servidor
///     le da a esta persona —hoy, al administrador—.
///   · Cada botón abre **esa pantalla del dashboard** dentro de la app, con la
///     sesión que la app ya tiene: la app pide un boleto y el dashboard lo
///     canjea por la suya, sin teclear la contraseña otra vez.
///
/// Son las mismas pantallas de la computadora, con sus reglas: lo que se mejore
/// en el dashboard aparece aquí solo, sin otra APK. Necesitan señal, como el
/// dashboard. El Excel se baja en el navegador del teléfono, que sabe guardarlo.
library;

import 'dart:convert';
import 'dart:typed_data';

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:webview_flutter/webview_flutter.dart';

import '../../estado/dashboard.dart';
import '../../estado/sincronizacion.dart';

/// El ícono de cada botón, por el nombre que manda el servidor. Uno que no
/// conoce sale con el de abrir.
IconData iconoDelAtajo(String nombre) => switch (nombre) {
      'mover' => Icons.swap_horiz,
      'entrada' => Icons.move_to_inbox_outlined,
      'salida' => Icons.outbox_outlined,
      'ajustar' => Icons.fact_check_outlined,
      'articulos' => Icons.inventory_2_outlined,
      'movimientos' => Icons.history,
      _ => Icons.open_in_new,
    };

/// Los botones: uno por pantalla del dashboard.
class PantallaAtajosDelPanel extends StatelessWidget {
  const PantallaAtajosDelPanel({super.key, required this.atajos, this.conBarra = true});

  final List<AtajoDelPanel> atajos;
  final bool conBarra;

  @override
  Widget build(BuildContext context) {
    final cuerpo = ListView(
      key: const Key('atajos_del_panel'),
      padding: const EdgeInsets.fromLTRB(12, 12, 12, 24),
      children: [
        for (final a in atajos)
          Card(
            margin: const EdgeInsets.symmetric(vertical: 6),
            child: ListTile(
              key: Key('atajo_${a.ruta}'),
              contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              leading: Icon(iconoDelAtajo(a.icono), size: 32),
              title: Text(a.titulo, style: const TextStyle(fontWeight: FontWeight.w600)),
              subtitle: a.descripcion.isEmpty ? null : Text(a.descripcion),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => PantallaDelDashboard(titulo: a.titulo, ruta: a.ruta),
                ),
              ),
            ),
          ),
        const Padding(
          padding: EdgeInsets.fromLTRB(8, 12, 8, 0),
          child: Text(
            'Se abren las mismas pantallas del dashboard, con tu sesión. '
            'Necesitan señal.',
            style: TextStyle(color: Colors.black54),
          ),
        ),
      ],
    );
    if (!conBarra) return cuerpo;
    return Scaffold(appBar: AppBar(title: const Text('Mover y capturar')), body: cuerpo);
  }
}

/// Una pantalla del dashboard dentro de la app.
class PantallaDelDashboard extends ConsumerStatefulWidget {
  const PantallaDelDashboard({super.key, required this.titulo, required this.ruta});

  final String titulo;

  /// «/panel/inventario/mover».
  final String ruta;

  @override
  ConsumerState<PantallaDelDashboard> createState() => _EstadoDashboard();
}

class _EstadoDashboard extends ConsumerState<PantallaDelDashboard> {
  /// Se crea con el primer boleto: antes no hay nada que mostrar.
  WebViewController? _vista;
  String? _error;
  bool _cargando = true;

  /// Lo último que se vio, para volver ahí si la sesión del dashboard vence.
  late String _ultimaRuta = widget.ruta;

  /// La sesión del dashboard dura ocho horas; al vencer, el dashboard pide la
  /// contraseña y aquí se abre otra con un boleto nuevo. Una vez: si tampoco
  /// sirve, se queda la pantalla de entrar.
  bool _yaSeRenovo = false;

  /// Se tocó «Salir» en el dashboard: al llegar a la de entrar, a la app.
  bool _saliendo = false;
  bool _cerrando = false;

  Uri get _servidor => Uri.parse(ref.read(baseUrlProvider));

  @override
  void initState() {
    super.initState();
    _abrir(widget.ruta);
  }

  Future<void> _abrir(String ruta) async {
    _saliendo = false;
    setState(() {
      _error = null;
      _cargando = true;
    });
    final cliente = ref.read(clienteDelPanelProvider);
    if (cliente == null) {
      setState(() => _error = 'Tu sesión venció: sal y vuelve a entrar a la app.');
      return;
    }
    try {
      final boleto = await cliente.boleto();
      if (!mounted) return;
      final vista = _vista ??= _nuevaVista();
      setState(() {});
      // POST: el boleto no queda en la dirección ni en el historial.
      await vista.loadRequest(
        _servidor.replace(path: boleto.entrar),
        method: LoadRequestMethod.post,
        headers: const {'Content-Type': 'application/x-www-form-urlencoded'},
        body: Uint8List.fromList(utf8.encode(
          'boleto=${Uri.encodeQueryComponent(boleto.boleto)}'
          '&siguiente=${Uri.encodeQueryComponent(ruta)}',
        )),
      );
    } on Exception catch (e) {
      if (mounted) setState(() => _error = _explicar(e));
    }
  }

  String _explicar(Exception e) => switch (e) {
        ErrorDeRed() => 'Sin señal. Las pantallas del dashboard necesitan conexión.',
        SinPermisoDeLaOficina() => 'Tu usuario no abre el dashboard desde la app.',
        ServidorSinEstaFuncion() =>
          'El servidor todavía no tiene esto: falta actualizarlo.',
        SesionInvalida() => 'Tu sesión venció: sal y vuelve a entrar a la app.',
        _ => 'No se pudo abrir: $e',
      };

  WebViewController _nuevaVista() => WebViewController()
    ..setJavaScriptMode(JavaScriptMode.unrestricted)
    ..setNavigationDelegate(
      NavigationDelegate(
        onNavigationRequest: _alNavegar,
        onPageStarted: (_) {
          if (mounted) setState(() => _cargando = true);
        },
        onPageFinished: _alTerminar,
        onWebResourceError: (error) {
          if (error.isForMainFrame ?? true) {
            if (mounted) {
              setState(() => _error =
                  'No se pudo abrir la pantalla. Revisa la señal y vuelve a intentar.');
            }
          }
        },
      ),
    );

  NavigationDecision _alNavegar(NavigationRequest pedido) {
    if (!pedido.isMainFrame) return NavigationDecision.navigate;
    final url = Uri.parse(pedido.url);
    switch (adondeVa(url, _servidor)) {
      case Destino.dentro:
        return NavigationDecision.navigate;
      case Destino.salir:
        _saliendo = true;
        return NavigationDecision.navigate;
      case Destino.descarga:
        _bajarEnElNavegador(url);
        return NavigationDecision.prevent;
      case Destino.afuera:
        ref.read(abridorDeNavegadorProvider).abrir(url);
        return NavigationDecision.prevent;
    }
  }

  void _alTerminar(String direccion) {
    if (!mounted || _cerrando) return;
    setState(() => _cargando = false);
    final url = Uri.parse(direccion);
    if (url.path == '/panel/entrar') {
      // «Salir» en el dashboard: de regreso a la app. `pop` y no `maybePop`:
      // este lo detendría la flecha de atrás, que regresa por el historial.
      if (_saliendo) {
        _cerrando = true;
        Navigator.of(context).pop();
        return;
      }
      // Venció la sesión del dashboard: otra, con otro boleto, una vez.
      if (!_yaSeRenovo) {
        _yaSeRenovo = true;
        _abrir(_ultimaRuta);
      }
      return;
    }
    if (url.path.startsWith('/panel') && !url.path.startsWith('/panel/entrar')) {
      _yaSeRenovo = false;
      _ultimaRuta = url.hasQuery ? '${url.path}?${url.query}' : url.path;
    }
  }

  /// «Volver a cargar»: la misma pantalla, con la sesión que ya tiene —un GET,
  /// para no reenviar un formulario—. Sin vista o tras un error, desde el boleto.
  void _recargar() {
    final vista = _vista;
    if (_error != null || vista == null) {
      _abrir(_ultimaRuta);
    } else {
      vista.loadRequest(_servidor.resolve(_ultimaRuta));
    }
  }

  /// El Excel, en el navegador del teléfono, con su propio boleto.
  Future<void> _bajarEnElNavegador(Uri destino) async {
    final mensajero = ScaffoldMessenger.of(context);
    final cliente = ref.read(clienteDelPanelProvider);
    if (cliente == null) {
      mensajero.showSnackBar(
        const SnackBar(content: Text('Tu sesión venció: sal y vuelve a entrar a la app.')),
      );
      return;
    }
    try {
      final boleto = await cliente.boleto();
      final abrio = await ref
          .read(abridorDeNavegadorProvider)
          .abrir(enElNavegador(_servidor, boleto, destino));
      if (!abrio) {
        mensajero.showSnackBar(
          const SnackBar(content: Text('No hay un navegador para bajar el Excel.')),
        );
      }
    } on Exception catch (e) {
      mensajero.showSnackBar(SnackBar(content: Text(_explicar(e))));
    }
  }

  Future<void> _atras() async {
    final vista = _vista;
    if (vista != null && await vista.canGoBack()) {
      await vista.goBack();
    } else if (mounted) {
      Navigator.of(context).pop();
    }
  }

  @override
  Widget build(BuildContext context) {
    final vista = _vista;
    return PopScope(
      // La flecha de atrás regresa dentro del dashboard; en la primera
      // pantalla, a la app.
      canPop: false,
      onPopInvokedWithResult: (seFue, _) {
        if (!seFue) _atras();
      },
      child: Scaffold(
        key: const Key('pantalla_del_dashboard'),
        appBar: AppBar(
          title: Text(widget.titulo),
          actions: [
            IconButton(
              key: const Key('boton_recargar_dashboard'),
              tooltip: 'Volver a cargar',
              icon: const Icon(Icons.refresh),
              onPressed: _recargar,
            ),
          ],
          bottom: _cargando && _error == null
              ? const PreferredSize(
                  preferredSize: Size.fromHeight(3),
                  child: LinearProgressIndicator(minHeight: 3),
                )
              : null,
        ),
        // La vista web se queda montada aunque haya un error encima: quitarla y
        // volverla a poner con el mismo controlador es frágil en Android.
        body: Stack(
          children: [
            if (vista != null) WebViewWidget(controller: vista),
            if (vista == null && _error == null)
              const Center(child: CircularProgressIndicator()),
            if (_error != null)
              Positioned.fill(
                child: ColoredBox(
                  color: Theme.of(context).colorScheme.surface,
                  child: _SinAbrir(mensaje: _error!, alReintentar: () => _abrir(_ultimaRuta)),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _SinAbrir extends StatelessWidget {
  const _SinAbrir({required this.mensaje, required this.alReintentar});

  final String mensaje;
  final VoidCallback alReintentar;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.cloud_off_outlined, size: 48),
              const SizedBox(height: 12),
              Text(mensaje, key: const Key('error_del_dashboard'), textAlign: TextAlign.center),
              const SizedBox(height: 16),
              FilledButton.icon(
                key: const Key('reintentar_dashboard'),
                onPressed: alReintentar,
                icon: const Icon(Icons.refresh),
                label: const Text('Volver a intentar'),
              ),
            ],
          ),
        ),
      );
}
