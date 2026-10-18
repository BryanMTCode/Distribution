/// El perfil del cliente en el teléfono del vendedor: sus datos y su ubicación.
///
/// La ubicación se fija con el GPS —parado en el negocio— o a mano (ADR 0002
/// §84). Se guarda en el teléfono al momento —la geocerca de la venta la usa
/// ya— y viaja por la cola al servidor, sin necesitar señal.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../datos/repo_clientes.dart';
import '../../estado/carrito.dart';
import '../../estado/mermas.dart';
import '../../estado/sesion.dart';
import '../editor_de_ubicacion.dart';
import 'comunes.dart';
import 'guia.dart';

final registroDeUbicacionProvider = Provider<RegistroDeUbicacion?>((ref) {
  final sesion = ref.watch(sesionProvider);
  if (sesion is! SesionAbierta) return null;
  final dispositivoId = ref.watch(dispositivoIdProvider);
  if (dispositivoId == null) return null;
  return RegistroDeUbicacion(
    outbox: ref.watch(outboxProvider),
    dispositivoId: dispositivoId,
    nuevoUuid: ref.watch(nuevoUuidProvider),
    ahora: ref.watch(relojProvider),
  );
});

class PantallaPerfilDelCliente extends ConsumerWidget {
  const PantallaPerfilDelCliente({super.key, required this.cliente});

  final ClienteEnRuta cliente;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final registro = ref.watch(registroDeUbicacionProvider);
    final estilo = Theme.of(context).textTheme;
    return Scaffold(
      key: const Key('pantalla_perfil_cliente'),
      appBar: AppBar(title: Text(cliente.nombreComercial)),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (cliente.codigo != null) Text(cliente.codigo!, style: estilo.titleSmall),
          if (cliente.telefono != null) Text('Tel. ${cliente.telefono}'),
          if (cliente.direccion != null) Text(cliente.direccion!),
          if (cliente.referencias != null)
            Text(cliente.referencias!, style: estilo.bodySmall),
          // Cómo llegar, sin mapa ni señal (ADR 0002 §129).
          if (cliente.conUbicacion) ...[
            const SizedBox(height: 20),
            Text('Cómo llegar', style: estilo.titleMedium),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                FilledButton.icon(
                  key: const Key('perfil_guiame'),
                  onPressed: () => Navigator.of(context).push(
                    MaterialPageRoute<void>(builder: (_) => PantallaGuia(cliente: cliente)),
                  ),
                  icon: const Icon(Icons.near_me_outlined),
                  label: const Text('Guíame (sin internet)'),
                ),
                OutlinedButton.icon(
                  key: const Key('perfil_abrir_en_mapas'),
                  onPressed: () => abrirEnMapas(context, ref, cliente),
                  icon: const Icon(Icons.map_outlined),
                  label: const Text('Abrir en Mapas'),
                ),
              ],
            ),
          ],
          const SizedBox(height: 20),
          Text('Ubicación del negocio', style: estilo.titleMedium),
          const Text('Es la geocerca de tus ventas: tómala con el GPS parado en la '
              'puerta, o escríbela si el GPS no lee.'),
          const SizedBox(height: 12),
          if (registro == null)
            const Aviso('Este equipo todavía no está registrado. Sincroniza primero.',
                grave: true)
          else
            EditorDeUbicacion(
              lat: cliente.lat,
              lng: cliente.lng,
              origen: cliente.ubicacionOrigen,
              alGuardar: (ubicacion) async {
                registro.fijar(cliente.id, ubicacion);
                // La lista de clientes y la geocerca la leen de la base.
                ref.invalidate(clientesProvider);
                ref.invalidate(resumenColaProvider);
                return 'Guardada en el teléfono. Se manda a la oficina al sincronizar.';
              },
            ),
        ],
      ),
    );
  }
}
