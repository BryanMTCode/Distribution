/// Dónde vive la credencial y la llave de la base cifrada.
///
/// Se abstrae por dos razones. La primera es poder probar sin Keystore. La
/// segunda, más importante: deja explícito que **la credencial no se guarda en
/// la base de datos**. Guardarla ahí significaría que la llave de SQLCipher
/// protege también el hash que abre la app — un círculo donde comprometer una
/// cosa compromete la otra.
library;

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

abstract interface class AlmacenSeguro {
  Future<String?> leer(String clave);
  Future<void> escribir(String clave, String valor);
  Future<void> borrar(String clave);
}

/// Keystore de Android. Es lo que hace que un teléfono robado y rooteado no
/// entregue la credencial con solo copiar archivos.
class AlmacenSeguroDelSistema implements AlmacenSeguro {
  const AlmacenSeguroDelSistema([
    this._almacen = const FlutterSecureStorage(
      aOptions: AndroidOptions(encryptedSharedPreferences: true),
    ),
  ]);

  final FlutterSecureStorage _almacen;

  @override
  Future<String?> leer(String clave) => _almacen.read(key: clave);

  @override
  Future<void> escribir(String clave, String valor) =>
      _almacen.write(key: clave, value: valor);

  @override
  Future<void> borrar(String clave) => _almacen.delete(key: clave);
}

/// Para pruebas. No toca el sistema.
class AlmacenSeguroEnMemoria implements AlmacenSeguro {
  final Map<String, String> _datos = {};

  @override
  Future<String?> leer(String clave) async => _datos[clave];

  @override
  Future<void> escribir(String clave, String valor) async => _datos[clave] = valor;

  @override
  Future<void> borrar(String clave) async => _datos.remove(clave);
}
