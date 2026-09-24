/// Andamiaje para las pruebas de widget.
///
/// Monta la app con una base en memoria y un almacén seguro falso, así que la
/// suite corre sin emulador, sin Keystore y en segundos.
library;

import 'dart:convert';
import 'dart:io';

import 'package:dsd_app/src/app.dart';
import 'package:dsd_app/src/datos/almacen_seguro.dart';
import 'package:dsd_app/src/datos/base_local.dart';
import 'package:dsd_app/src/estado/sesion.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// Hash Argon2id real, generado por el servidor. Se lee de los vectores
/// compartidos para que la prueba ejerza el contrato de verdad y no un hash
/// inventado.
final vectorArgon2 = () {
  final documento = jsonDecode(
    File('../../contracts/argon2_vectors.json').readAsStringSync(),
  ) as Map<String, dynamic>;
  final vectores = (documento['vectores'] as List).cast<Map<String, dynamic>>();
  return vectores.firstWhere((v) => v['nombre'] == 'pin_numerico');
}();

String get pinCorrecto => vectorArgon2['password'] as String;
String get pinIncorrecto => vectorArgon2['password_incorrecta'] as String;

Map<String, Object?> credencialDelServidor({
  String rol = 'vendedor',
  String validaHasta = '2026-12-31T00:00:00.000Z',
  List<String> permisos = const ['ventas.crear', 'clientes.crear', 'cobranza.ver'],
}) =>
    {
      'usuario_id': '019283a0-0001-7000-8000-000000000001',
      'codigo': 'VEND01',
      'nombre': 'Juan Pérez',
      'rol': rol,
      'password_hash': vectorArgon2['hash_phc'],
      'permisos': permisos,
      'almacen_id': '019283a0-0002-7000-8000-000000000002',
      'valida_hasta': validaHasta,
    };

/// Inserta un cliente en la base local, como lo dejaría un pull.
void sembrarCliente(
  BaseLocal base, {
  required String id,
  required String nombre,
  int? secuencia,
  String? codigo,
  bool permiteCredito = true,
  double limite = 5000,
  double saldoCache = 0,
  bool bloqueado = false,
  bool esLocal = false,
}) {
  base.db.execute(
    '''
    INSERT INTO clientes (id, codigo, nombre_comercial, secuencia, permite_credito,
                          limite_credito, saldo_cache, saldo_cache_en, bloqueado,
                          es_local, sincronizado)
    VALUES (?, ?, ?, ?, ?, ?, ?, '2026-09-24T07:00:00.000Z', ?, ?, 1)
    ''',
    [id, codigo, nombre, secuencia, permiteCredito ? 1 : 0, limite, saldoCache,
     bloqueado ? 1 : 0, esLocal ? 1 : 0],
  );
}

/// Venta a crédito encolada y sin sincronizar. Es la que tiene que bajar el
/// disponible que ve el vendedor.
void sembrarVentaACreditoPendiente(
  BaseLocal base, {
  required String clienteId,
  required double total,
  int consecutivo = 1,
}) {
  base.db.execute(
    '''
    INSERT INTO ventas (id, folio_consecutivo, folio_local, cliente_id, tipo,
                        estado, total, fecha_dispositivo, fecha_operativa,
                        sincronizada, creado_en)
    VALUES (?, ?, ?, ?, 'credito', 'confirmada', ?,
            '2026-09-24T10:00:00.000Z', '2026-09-24', 0, '2026-09-24T10:00:00.000Z')
    ''',
    [
      'venta-$consecutivo',
      consecutivo,
      'VEND01-${consecutivo.toString().padLeft(6, '0')}',
      clienteId,
      total,
    ],
  );
}

void sembrarCobroPendiente(
  BaseLocal base, {
  required String clienteId,
  required double importe,
  int consecutivo = 1,
}) {
  base.db.execute(
    '''
    INSERT INTO cobros (id, folio_consecutivo, folio_local, cliente_id, importe,
                        forma_pago, estado, fecha_dispositivo, fecha_operativa,
                        sincronizado, creado_en)
    VALUES (?, ?, ?, ?, ?, 'efectivo', 'confirmado',
            '2026-09-24T11:00:00.000Z', '2026-09-24', 0, '2026-09-24T11:00:00.000Z')
    ''',
    [
      'cobro-$consecutivo',
      consecutivo,
      'VEND01-C${consecutivo.toString().padLeft(5, '0')}',
      clienteId,
      importe,
    ],
  );
}

/// Encola un sobre cualquiera, para probar el indicador de pendientes.
void sembrarPendienteEnCola(BaseLocal base, {int cuantos = 1}) {
  for (var i = 0; i < cuantos; i++) {
    base.db.execute(
      '''
      INSERT INTO outbox (operacion_id, tipo, entidad_id, payload, hash_payload,
                          secuencia, estado, intentos, creado_en)
      VALUES (?, 'cliente.crear', ?, '{}', ?, ?, 'pendiente', 0,
              '2026-09-24T09:00:00.000Z')
      ''',
      ['op-$i', 'ent-$i', '0' * 64, i],
    );
  }
}

/// Monta la app completa con dependencias de prueba.
///
/// Devuelve la base para poder sembrar datos y verificar efectos.
Future<BaseLocal> montarApp(
  WidgetTester tester, {
  Map<String, Object?>? credencial,
  DateTime? ahora,
  void Function(BaseLocal base)? sembrar,
}) async {
  final base = BaseLocal.enMemoria();
  final almacen = AlmacenSeguroEnMemoria();
  if (credencial != null) {
    await almacen.escribir('credencial_local_v1', jsonEncode(credencial));
  }
  sembrar?.call(base);

  addTearDown(base.cerrar);

  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        almacenSeguroProvider.overrideWithValue(almacen),
        baseLocalProvider.overrideWithValue(base),
        relojProvider.overrideWithValue(
          () => ahora ?? DateTime.utc(2026, 9, 24, 7),
        ),
      ],
      child: const AppDsd(),
    ),
  );
  await tester.pumpAndSettle();
  return base;
}

/// Teclea el PIN y espera a que Argon2 termine.
///
/// La verificación tarda cientos de milisegundos a propósito: es lo que hace
/// caro probar PINs en un teléfono robado.
Future<void> entrarCon(WidgetTester tester, String pin) async {
  await tester.enterText(find.byKey(const Key('campo_pin')), pin);
  await tester.tap(find.byKey(const Key('boton_entrar')));
  await tester.pumpAndSettle(const Duration(seconds: 5));
}

Finder textoQueContiene(String fragmento) => find.byWidgetPredicate(
      (w) => w is Text && (w.data ?? '').contains(fragmento),
    );
