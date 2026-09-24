/// Lista de clientes de la ruta.
///
/// Sale entera de SQLite: se ve igual con señal o sin ella. No hay estado
/// "cargando del servidor" que deje al vendedor esperando frente al cliente.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../datos/repo_clientes.dart';
import '../../estado/sesion.dart';

class PantallaClientes extends ConsumerWidget {
  const PantallaClientes({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final clientes = ref.watch(clientesProvider);
    final cola = ref.watch(resumenColaProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Mi ruta'),
        actions: [
          IconButton(
            key: const Key('boton_salir'),
            tooltip: 'Salir',
            icon: const Icon(Icons.logout),
            onPressed: () => ref.read(sesionProvider.notifier).salir(),
          ),
        ],
        bottom: cola.todoSincronizado ? null : _BarraPendientes(cola: cola),
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(12),
            child: TextField(
              key: const Key('campo_busqueda'),
              onChanged: (v) =>
                  ref.read(busquedaClientesProvider.notifier).state = v,
              decoration: const InputDecoration(
                hintText: 'Buscar por nombre, código o teléfono',
                prefixIcon: Icon(Icons.search),
                border: OutlineInputBorder(),
                isDense: true,
              ),
            ),
          ),
          Expanded(
            child: clientes.isEmpty
                ? const Center(
                    key: Key('lista_vacia'),
                    child: Text('No hay clientes que coincidan'),
                  )
                : ListView.separated(
                    key: const Key('lista_clientes'),
                    itemCount: clientes.length,
                    separatorBuilder: (_, __) => const Divider(height: 1),
                    itemBuilder: (_, i) => _Renglon(cliente: clientes[i]),
                  ),
          ),
        ],
      ),
    );
  }
}

/// Mientras haya cola sin sincronizar, se ve. Un número de pendientes que crece
/// es la primera señal de que algo va mal, y el vendedor es quien lo nota
/// primero.
class _BarraPendientes extends StatelessWidget implements PreferredSizeWidget {
  const _BarraPendientes({required this.cola});

  final ResumenCola cola;

  @override
  Size get preferredSize => const Size.fromHeight(32);

  @override
  Widget build(BuildContext context) {
    final colores = Theme.of(context).colorScheme;
    final cuarentena = cola.enCuarentena > 0 ? ' · ${cola.enCuarentena} con error' : '';
    return Container(
      key: const Key('barra_pendientes'),
      width: double.infinity,
      color: colores.tertiaryContainer,
      padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 12),
      child: Row(
        children: [
          Icon(Icons.cloud_upload_outlined, size: 16, color: colores.onTertiaryContainer),
          const SizedBox(width: 8),
          Text(
            'Por enviar: ${cola.pendientes}$cuarentena',
            style: TextStyle(color: colores.onTertiaryContainer, fontSize: 13),
          ),
        ],
      ),
    );
  }
}

class _Renglon extends StatelessWidget {
  const _Renglon({required this.cliente});

  final ClienteEnRuta cliente;

  @override
  Widget build(BuildContext context) {
    final subtitulo = [
      if (cliente.codigo != null) cliente.codigo,
      if (cliente.direccion != null) cliente.direccion,
    ].whereType<String>().join(' · ');

    return ListTile(
      key: Key('cliente_${cliente.id}'),
      leading: CircleAvatar(
        child: Text(
          cliente.secuencia?.toString() ?? '—',
          style: const TextStyle(fontSize: 13),
        ),
      ),
      title: Row(
        children: [
          Expanded(child: Text(cliente.nombreComercial)),
          if (cliente.esLocal)
            const Padding(
              padding: EdgeInsets.only(left: 6),
              child: Icon(Icons.cloud_upload_outlined, size: 16),
            ),
        ],
      ),
      subtitle: subtitulo.isEmpty ? null : Text(subtitulo),
      trailing: _Credito(cliente: cliente),
      onTap: () {},
    );
  }
}

/// El distintivo de crédito.
///
/// El número que se muestra ya incluye la cola local: si el vendedor acaba de
/// venderle a crédito y no ha sincronizado, el disponible que ve aquí ya bajó.
/// Mostrar el saldo del servidor a secas sería invitarlo a pasarse del límite.
class _Credito extends StatelessWidget {
  const _Credito({required this.cliente});

  final ClienteEnRuta cliente;

  @override
  Widget build(BuildContext context) {
    final colores = Theme.of(context).colorScheme;

    if (!cliente.credito.permiteCredito) {
      return Text(
        key: const Key('credito_solo_contado'),
        'Contado',
        style: TextStyle(color: colores.onSurfaceVariant, fontSize: 12),
      );
    }

    if (cliente.credito.bloqueado) {
      return _Etiqueta(
        clave: 'credito_bloqueado',
        texto: 'Bloqueado',
        fondo: colores.errorContainer,
        frente: colores.onErrorContainer,
      );
    }

    if (cliente.credito.disponible.esCero) {
      return _Etiqueta(
        clave: 'credito_agotado',
        texto: 'Sin crédito',
        fondo: colores.errorContainer,
        frente: colores.onErrorContainer,
      );
    }

    return Column(
      mainAxisAlignment: MainAxisAlignment.center,
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        Text(
          key: const Key('credito_disponible'),
          '\$${cliente.credito.disponible.texto}',
          style: const TextStyle(fontWeight: FontWeight.w600),
        ),
        Text('disponible',
            style: TextStyle(fontSize: 11, color: colores.onSurfaceVariant)),
      ],
    );
  }
}

class _Etiqueta extends StatelessWidget {
  const _Etiqueta({
    required this.clave,
    required this.texto,
    required this.fondo,
    required this.frente,
  });

  final String clave;
  final String texto;
  final Color fondo;
  final Color frente;

  @override
  Widget build(BuildContext context) => Container(
        key: Key(clave),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          color: fondo,
          borderRadius: BorderRadius.circular(999),
        ),
        child: Text(texto,
            style: TextStyle(color: frente, fontSize: 12, fontWeight: FontWeight.w600)),
      );
}
