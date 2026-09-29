/// Estado de la visita: el cliente que se está atendiendo y su carrito.
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL CARRITO VIVE EN MEMORIA, Y ESO ES UNA DEUDA CONOCIDA
/// ─────────────────────────────────────────────────────────────────────────
/// Mientras el vendedor arma el pedido, el carrito no toca SQLite. Se escribe al
/// confirmar la venta, en una sola transacción con su sobre (Parte 2).
///
/// En un Android de gama baja, 20 minutos en un mercado con la app en segundo
/// plano es tiempo suficiente para que el sistema la mate y el carrito se pierda.
/// Persistir el borrador es la primera cosa de la Parte 2, junto con la escritura
/// de la venta — se hacen juntas porque comparten la misma transacción y
/// partirlas dejaría dos mecanismos a medias.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../datos/repo_catalogo.dart';
import '../datos/repo_clientes.dart';
import 'sesion.dart';

final repoCatalogoProvider = Provider<RepoCatalogo>(
  (ref) => RepoCatalogo(ref.watch(baseLocalProvider).db),
);

/// El cliente que se está atendiendo ahora. `null` = nadie.
///
/// Abrir el catálogo siempre es "para un cliente": el precio depende de su
/// lista, y el crédito de su saldo. Un catálogo sin cliente mostraría precios
/// que no son de nadie.
final clienteEnVisitaProvider = StateProvider<String?>((_) => null);

final busquedaCatalogoProvider = StateProvider<String>((_) => '');

/// El cliente de la visita, ya con su crédito compuesto desde la cola local.
final clienteDeLaVisitaProvider = Provider<ClienteEnRuta?>((ref) {
  final id = ref.watch(clienteEnVisitaProvider);
  if (id == null) return null;
  // Se reconstruye cuando cambia la cola: una venta a crédito encolada baja el
  // disponible, y el vendedor tiene que verlo en la misma visita.
  ref.watch(resumenColaProvider);
  return ref.watch(repoClientesProvider).porId(id);
});

/// Con qué lista se le cotiza al cliente de la visita.
final listaDeLaVisitaProvider = Provider<ListaResuelta?>((ref) {
  final id = ref.watch(clienteEnVisitaProvider);
  if (id == null) return null;
  return ref.watch(repoCatalogoProvider).listaParaCliente(id);
});

/// Lo que trae el camión hoy. Se lee entero: el carrito lo consulta en cada
/// toque de "+", y una consulta por toque se sentiría pegajosa.
final existenciasProvider = Provider<ExistenciasCamion>(
  (ref) => ref.watch(repoCatalogoProvider).existencias(),
);

/// `true` cuando el camión no trae carga. No es un error de la app, y se dice
/// con esas palabras.
final sinCargaActivaProvider = Provider<bool>(
  (ref) => ref.watch(repoCatalogoProvider).sinCargaActiva,
);

/// El catálogo cotizado para el cliente de la visita, filtrado por la búsqueda.
final catalogoProvider = Provider<List<ProductoEnCatalogo>>((ref) {
  final lista = ref.watch(listaDeLaVisitaProvider);
  if (lista == null) return const [];
  return ref.watch(repoCatalogoProvider).paraCliente(
        listaPreciosId: lista.id,
        busqueda: ref.watch(busquedaCatalogoProvider),
      );
});

/// Lo último que pasó al tocar el carrito. La pantalla lo escucha para avisar
/// "solo quedan 4 cajas" sin que el carrito sepa nada de interfaz.
final ultimoRechazoProvider = StateProvider<ResultadoCarrito?>((_) => null);

class ControladorCarrito extends Notifier<Carrito> {
  @override
  Carrito build() {
    // Cambiar de cliente empieza un carrito nuevo. Arrastrar líneas de una
    // tienda a la siguiente sería la forma más rápida de facturarle a quien no
    // pidió nada.
    ref.watch(clienteEnVisitaProvider);
    return const Carrito();
  }

  /// Agrega (o suma) una presentación.
  ///
  /// No recibe precio. No lo recibe porque no existe: el precio viene dentro de
  /// la presentación, resuelto del catálogo (ADR 0002 §7).
  ResultadoCarrito agregar(PresentacionVendible presentacion, Cantidad cantidad) =>
      _aplicar(
        state.agregar(
          presentacion,
          cantidad,
          existencias: ref.read(existenciasProvider),
        ),
      );

  ResultadoCarrito fijar(PresentacionVendible presentacion, Cantidad cantidad) =>
      _aplicar(
        state.fijarCantidad(
          presentacion,
          cantidad,
          existencias: ref.read(existenciasProvider),
        ),
      );

  void quitar(String llave) => state = state.quitar(llave);

  void vaciar() => state = state.vaciar();

  void cambiarFormaDePago({required bool aCredito}) =>
      state = state.conFormaDePago(aCredito: aCredito);

  /// Un rechazo **no** modifica el carrito: se publica para que la pantalla lo
  /// cuente y el estado se queda como estaba.
  ResultadoCarrito _aplicar(ResultadoCarrito resultado) {
    if (resultado.aceptado) {
      state = resultado.carrito;
      ref.read(ultimoRechazoProvider.notifier).state = null;
    } else {
      ref.read(ultimoRechazoProvider.notifier).state = resultado;
    }
    return resultado;
  }
}

final carritoProvider = NotifierProvider<ControladorCarrito, Carrito>(
  ControladorCarrito.new,
);

/// La evaluación de crédito del carrito actual contra el cliente de la visita.
final evaluacionProvider = Provider<ResultadoCredito?>((ref) {
  final cliente = ref.watch(clienteDeLaVisitaProvider);
  if (cliente == null) return null;
  return ref.watch(carritoProvider).evaluar(cliente.credito);
});

/// Cuánto queda **disponible de verdad** de un producto: lo que trae el camión
/// menos lo que ya está en el carrito.
///
/// Es el número que se muestra en el catálogo, y baja mientras el vendedor
/// agrega. Mostrar la existencia sin descontar el carrito lo dejaría prometiendo
/// mercancía que él mismo ya comprometió.
Cantidad disponibleReal(WidgetRef ref, String productoId) {
  final existencias = ref.watch(existenciasProvider);
  final enCarrito = ref.watch(carritoProvider).comprometidoDe(productoId);
  final libre = existencias.disponible(productoId) - enCarrito;
  return libre.milesimos < 0 ? Cantidad.cero : libre;
}
