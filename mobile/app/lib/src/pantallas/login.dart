/// Login del vendedor. Funciona sin señal.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../datos/servicio_ubicacion.dart';
import '../demo.dart';
import '../estado/alta.dart';
import '../estado/sesion.dart';

class PantallaLogin extends ConsumerStatefulWidget {
  const PantallaLogin({super.key});

  @override
  ConsumerState<PantallaLogin> createState() => _EstadoLogin();
}

class _EstadoLogin extends ConsumerState<PantallaLogin> {
  final _pin = TextEditingController();
  bool _verificando = false;
  ResultadoLogin? _error;

  @override
  void dispose() {
    _pin.dispose();
    super.dispose();
  }

  Future<void> _entrar() async {
    // Verificar cuesta cientos de milisegundos a propósito (Argon2id con 64 MiB):
    // es lo que hace caro probar PINs en un teléfono robado. Se bloquea el botón
    // para que no se disparen varias verificaciones a la vez.
    setState(() {
      _verificando = true;
      _error = null;
    });
    final resultado = await ref.read(sesionProvider.notifier).entrarOffline(_pin.text);
    if (!mounted) return;
    setState(() {
      _verificando = false;
      _error = resultado == ResultadoLogin.ok ? null : resultado;
    });
  }

  /// Siembra la sesión y los datos de demostración, y entra.
  ///
  /// No es un atajo que salte la verificación: guarda la credencial y después
  /// llama al **mismo** login offline con el PIN de demo, así que el camino de
  /// Argon2 se recorre igual que en producción.
  Future<void> _entrarEnModoDemo() async {
    setState(() {
      _verificando = true;
      _error = null;
    });

    final ahora = ref.read(relojProvider)();
    await ref
        .read(repoCredencialProvider)
        .guardar(credencialDemo(ahora: ahora));

    // Se intenta leer el GPS para colocar los clientes de demostración
    // alrededor del punto donde estás: es lo que permite probar el aviso de
    // posible duplicado en campo. Si no hay lectura, se usan coordenadas fijas.
    final lectura = await ref.read(servicioUbicacionProvider).leer();
    sembrarDemo(
      ref.read(baseLocalProvider).db,
      referencia: lectura is GpsObtenido ? lectura.ubicacion : null,
      ahora: ahora.toUtc().toIso8601String(),
    );

    ref.invalidate(clientesProvider);
    ref.invalidate(resumenColaProvider);

    final resultado =
        await ref.read(sesionProvider.notifier).entrarOffline(pinDemo);
    if (!mounted) return;
    setState(() {
      _verificando = false;
      _error = resultado == ResultadoLogin.ok ? null : resultado;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const Icon(Icons.local_shipping_outlined, size: 64),
                  const SizedBox(height: 12),
                  Text('Ruta', style: Theme.of(context).textTheme.headlineMedium),
                  const SizedBox(height: 32),
                  TextField(
                    key: const Key('campo_pin'),
                    controller: _pin,
                    obscureText: true,
                    keyboardType: TextInputType.number,
                    autofocus: true,
                    textInputAction: TextInputAction.go,
                    onSubmitted: (_) => _verificando ? null : _entrar(),
                    decoration: const InputDecoration(
                      labelText: 'PIN',
                      border: OutlineInputBorder(),
                      prefixIcon: Icon(Icons.lock_outline),
                    ),
                  ),
                  const SizedBox(height: 16),
                  SizedBox(
                    width: double.infinity,
                    child: FilledButton(
                      key: const Key('boton_entrar'),
                      onPressed: _verificando ? null : _entrar,
                      child: Padding(
                        padding: const EdgeInsets.symmetric(vertical: 12),
                        child: _verificando
                            ? const SizedBox(
                                height: 20,
                                width: 20,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : const Text('Entrar'),
                      ),
                    ),
                  ),
                  if (_error != null) ...[
                    const SizedBox(height: 20),
                    _Aviso(motivo: _error!),
                  ],
                  // Este bloque NO EXISTE en un build de release: las dos
                  // constantes que lo gobiernan son de compilación, así que el
                  // compilador lo elimina del árbol. Ver lib/src/demo.dart.
                  if (modoDemoDisponible) ...[
                    const SizedBox(height: 32),
                    const Divider(),
                    const SizedBox(height: 8),
                    Text(
                      'Compilado en modo demo',
                      style: Theme.of(context).textTheme.labelSmall,
                    ),
                    const SizedBox(height: 8),
                    SizedBox(
                      width: double.infinity,
                      child: OutlinedButton.icon(
                        key: const Key('boton_modo_demo'),
                        onPressed: _verificando ? null : _entrarEnModoDemo,
                        icon: const Icon(Icons.science_outlined),
                        label: const Text('Sembrar datos y entrar'),
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      'PIN de demo: $pinDemo',
                      style: Theme.of(context).textTheme.bodySmall,
                      textAlign: TextAlign.center,
                    ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// El mensaje importa: "PIN incorrecto" y "conéctate para renovar" piden cosas
/// distintas del vendedor, y confundirlos lo deja parado a media ruta sin saber
/// qué hacer.
class _Aviso extends StatelessWidget {
  const _Aviso({required this.motivo});

  final ResultadoLogin motivo;

  (IconData, String, String?) get _contenido => switch (motivo) {
        ResultadoLogin.passwordIncorrecta => (
            Icons.error_outline,
            'PIN incorrecto',
            null,
          ),
        ResultadoLogin.credencialVencida => (
            Icons.wifi_off_outlined,
            'Tu acceso venció',
            'Conéctate a internet una vez para renovarlo.',
          ),
        ResultadoLogin.sinCredencial => (
            Icons.cloud_off_outlined,
            'Este equipo no tiene sesión',
            'Necesitas conectarte a internet la primera vez.',
          ),
        ResultadoLogin.credencialCorrupta => (
            Icons.warning_amber_outlined,
            'La sesión guardada no sirve',
            'Conéctate a internet para volver a entrar.',
          ),
        ResultadoLogin.ok => (Icons.check, '', null),
      };

  @override
  Widget build(BuildContext context) {
    final (icono, titulo, detalle) = _contenido;
    final colores = Theme.of(context).colorScheme;
    return Container(
      key: const Key('aviso_login'),
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: colores.errorContainer,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        children: [
          Icon(icono, color: colores.onErrorContainer),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(titulo,
                    style: TextStyle(
                      color: colores.onErrorContainer,
                      fontWeight: FontWeight.w600,
                    )),
                if (detalle != null)
                  Text(detalle, style: TextStyle(color: colores.onErrorContainer)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
