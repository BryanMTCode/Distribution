/// El registro del cobro.
///
/// Aquí se prueba lo que cuesta dinero cuando falla, y que no se puede provocar
/// a voluntad en la calle:
///
/// · Que las cuatro escrituras sean **atómicas**. Un cobro sin sobre deja al
///   cliente con un papel que la oficina nunca va a ver; un sobre sin cobro baja
///   el saldo en la oficina y el vendedor no sabe por qué.
/// · Que el folio **no se duplique ni deje hueco**, porque va impreso en un papel
///   que el cliente conserva.
/// · Que **cobrar de más no se rechace**: el dinero ya cambió de manos, y
///   rechazarlo haría que el vendedor se guardara efectivo sin documento.
/// · Que el `saldo_cache` del cliente **no se toque**: es zona espejo, y bajarla
///   aquí haría que el siguiente pull la volviera a subir.
library;

import 'dart:convert';

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

void main() {
  late Database db;
  late Outbox outbox;
  late RepoFolios folios;
  var contador = 0;

  String uuid() => 'id-${(++contador).toString().padLeft(4, '0')}';

  const identidad = IdentidadDeCobro(
    vendedorId: 'u-vendedor',
    codigoVendedor: 'VEND01',
    dispositivoId: 'd-poco',
    rutaId: 'r-04',
  );

  RegistroDeCobro registro() => RegistroDeCobro(
        db: db,
        outbox: outbox,
        folios: folios,
        nuevoUuid: uuid,
        ahora: () => '2026-09-29T17:42:03.250Z',
        identidad: identidad,
      );

  setUp(() {
    contador = 0;
    db = sqlite3.openInMemory();
    db.execute(esquemaLocal);
    outbox = Outbox(db);
    folios = RepoFolios(db);

    db.execute(
      "INSERT INTO clientes (id, nombre_comercial, permite_credito, "
      "limite_credito, saldo_cache, bloqueado, es_local, sincronizado) "
      "VALUES ('cli-1', 'Abarrotes Doña Mary', 1, 5000, 2000, 0, 0, 1)",
    );
    folios.guardar(
      RangoFolios(tipo: 'cobro', desde: 1, hasta: 1000, consumidoHasta: 0),
      asignadoEn: '2026-09-29T06:00:00.000Z',
    );
  });

  tearDown(() => db.dispose());

  /// El texto del ticket como una sola frase, para afirmar que algo está.
  ///
  /// Dos razones para no usar `render()` ni unir con saltos de línea:
  ///
  /// 1. `render()` representa el ancho doble espaciando los caracteres
  ///    ("P A G O  5 0 0 . 0 0"), que es correcto para mirar el papel en pantalla
  ///    e inútil para afirmar que un importe está.
  /// 2. `parrafo` envuelve a 32 columnas, así que "Consulta tu saldo con tu
  ///    vendedor." —34 columnas, la misma frase que desbordó la remisión— llega
  ///    partida en dos renglones. Uniendo con un espacio, el assert dice lo que
  ///    quiere decir.
  ///
  /// El desborde se mide aparte, sobre `vista.lineas`, donde sí importan los
  /// límites de cada renglón.
  String textoDe(List<int> bytes) =>
      decodificar(bytes).lineas.map((l) => l.texto).join(' ');

  Map<String, Object?> datosDelSobre() {
    final fila = db.select('SELECT payload FROM outbox').single;
    final sobre = jsonDecode(fila['payload'] as String) as Map<String, Object?>;
    final ops = sobre['operaciones'] as List;
    return (ops.single as Map<String, Object?>)['datos'] as Map<String, Object?>;
  }

  // -------------------------------------------------------------------------
  // Lo que escribe
  // -------------------------------------------------------------------------

  test('registrar escribe el cobro, el sobre y la marca del folio', () {
    final cobro = registro().registrar(
      clienteId: 'cli-1',
      importe: Dinero.deTexto('500.00'),
      saldoAntes: Dinero.deTexto('2000.00'),
    );

    expect(cobro.folioConsecutivo, equals(1));
    expect(cobro.folioLocal, equals('VEND01-000001'));

    final fila = db.select('SELECT * FROM cobros').single;
    expect(fila['cliente_id'], equals('cli-1'));
    expect(fila['importe'], equals(500.0));
    expect(fila['forma_pago'], equals('efectivo'));
    expect(fila['estado'], equals('confirmado'));
    expect(fila['sincronizado'], equals(0));
    expect(fila['impreso'], equals(0));
    // Forense: lo que el teléfono creía que debía.
    expect(fila['saldo_cache_disp'], equals(2000.0));

    // El sobre, con el importe como string de dos decimales (contrato §1.4).
    final datos = datosDelSobre();
    expect(datos['importe'], equals('500.00'));
    expect(datos['saldo_cache_disp'], equals('2000.00'));
    expect(datos['folio_local'], equals('VEND01-000001'));

    // Y la marca del folio avanzó dentro de la misma transacción.
    expect(folios.leer('cobro')!.consumidoHasta, equals(1));
  });

  test('el sobre va con tipo cobro.crear y su visita', () {
    final cobro = registro().registrar(
      clienteId: 'cli-1',
      importe: Dinero.deTexto('500.00'),
    );
    final fila = db.select('SELECT tipo, visita_id, entidad_id FROM outbox').single;
    expect(fila['tipo'], equals('cobro.crear'));
    expect(fila['visita_id'], equals(cobro.visitaId));
    expect(fila['entidad_id'], equals(cobro.id));
  });

  test('EL SALDO EN CACHÉ DEL CLIENTE NO SE TOCA', () {
    // Es zona espejo: la escribe el delta de cartera. Si este cobro la bajara, el
    // siguiente pull la volvería a subir —porque el servidor todavía no tiene el
    // abono— y el vendedor vería la deuda reaparecer a media ruta.
    //
    // El saldo que ve el vendedor se COMPONE al leer, restando los cobros
    // encolados (`repo_clientes.deLaRuta`).
    registro().registrar(
      clienteId: 'cli-1',
      importe: Dinero.deTexto('500.00'),
    );
    final saldo = db.select('SELECT saldo_cache FROM clientes').single;
    expect(saldo['saldo_cache'], equals(2000.0));
  });

  test('el cobro encolado se puede restar del saldo al leer', () {
    // Es la consulta que usa la app para mostrar el saldo efectivo. Aquí se
    // comprueba el dato que la hace posible: el cobro queda sin sincronizar y con
    // su importe.
    registro().registrar(clienteId: 'cli-1', importe: Dinero.deTexto('500.00'));

    final fila = db.select(
      '''
      SELECT c.saldo_cache - COALESCE((
               SELECT SUM(k.importe) FROM cobros k
                WHERE k.cliente_id = c.id
                  AND k.estado = 'confirmado'
                  AND k.sincronizado = 0
             ), 0) AS efectivo
        FROM clientes c WHERE c.id = 'cli-1'
      ''',
    ).single;
    expect(fila['efectivo'], equals(1500.0));
  });

  // -------------------------------------------------------------------------
  // Cobrar de más
  // -------------------------------------------------------------------------

  test('COBRAR MÁS DE LO QUE DEBE NO SE RECHAZA', () {
    // El cliente puede liquidar y dejar anticipo, o el saldo del teléfono estaba
    // viejo. El dinero ya cambió de manos: rechazarlo haría que el vendedor se
    // guardara efectivo sin documento, que es el problema que esto evita.
    final cobro = registro().registrar(
      clienteId: 'cli-1',
      importe: Dinero.deTexto('3000.00'),
      saldoAntes: Dinero.deTexto('2000.00'),
    );
    expect(cobro.importe, equals(Dinero.deTexto('3000.00')));
    expect(db.select('SELECT importe FROM cobros').single['importe'], equals(3000.0));
  });

  test('un importe en cero o negativo sí se rechaza', () {
    for (final malo in ['0.00', '-100.00']) {
      expect(
        () => registro().registrar(
          clienteId: 'cli-1',
          importe: Dinero.deTexto(malo),
        ),
        throwsA(
          isA<CobroRechazado>().having(
            (e) => e.motivo,
            'motivo',
            MotivoNoCobro.importeInvalido,
          ),
        ),
      );
    }
    expect(db.select('SELECT * FROM cobros'), isEmpty);
    expect(db.select('SELECT * FROM outbox'), isEmpty);
  });

  // -------------------------------------------------------------------------
  // La referencia de lo que no es efectivo
  // -------------------------------------------------------------------------

  test('una transferencia sin referencia se rechaza', () {
    // Sin referencia es imposible de conciliar con el banco: la oficina tendría un
    // abono registrado y ninguna forma de encontrarlo en el estado de cuenta.
    expect(
      () => registro().registrar(
        clienteId: 'cli-1',
        importe: Dinero.deTexto('500.00'),
        formaDePago: FormaDePago.transferencia,
      ),
      throwsA(
        isA<CobroRechazado>().having(
          (e) => e.motivo,
          'motivo',
          MotivoNoCobro.faltaReferencia,
        ),
      ),
    );
    // Y no quemó folio: el rollback dejó la marca donde estaba.
    expect(folios.leer('cobro')!.consumidoHasta, equals(0));
  });

  test('el efectivo no necesita referencia porque el papel ES la prueba', () {
    final cobro = registro().registrar(
      clienteId: 'cli-1',
      importe: Dinero.deTexto('500.00'),
    );
    expect(cobro.referencia, isNull);
  });

  test('una transferencia con referencia pasa y la guarda limpia', () {
    final cobro = registro().registrar(
      clienteId: 'cli-1',
      importe: Dinero.deTexto('500.00'),
      formaDePago: FormaDePago.transferencia,
      referencia: '  SPEI-77231  ',
    );
    expect(cobro.referencia, equals('SPEI-77231'));
    expect(
      db.select('SELECT referencia FROM cobros').single['referencia'],
      equals('SPEI-77231'),
    );
  });

  test('solo el efectivo entra al arqueo del cierre', () {
    // El arqueo de la liquidación suma solo el efectivo: una transferencia no
    // viene en la bolsa. Con texto libre, "efectivo " con un espacio quedaría
    // fuera de la suma y el cuadre fallaría por un dato que se ve bien.
    expect(FormaDePago.efectivo.entraAlArqueo, isTrue);
    expect(FormaDePago.transferencia.entraAlArqueo, isFalse);
    expect(FormaDePago.cheque.entraAlArqueo, isFalse);
  });

  // -------------------------------------------------------------------------
  // El folio
  // -------------------------------------------------------------------------

  test('sin rango de folios no se puede cobrar', () {
    db.execute("DELETE FROM folios_rangos WHERE tipo = 'cobro'");
    expect(
      () => registro().registrar(
        clienteId: 'cli-1',
        importe: Dinero.deTexto('500.00'),
      ),
      throwsA(
        isA<CobroRechazado>().having(
          (e) => e.motivo,
          'motivo',
          MotivoNoCobro.sinRangoDeFolios,
        ),
      ),
    );
  });

  test('con el rango agotado no se puede cobrar', () {
    folios.guardar(
      RangoFolios(tipo: 'cobro', desde: 1, hasta: 10, consumidoHasta: 10),
      asignadoEn: '2026-09-29T06:00:00.000Z',
    );
    expect(
      () => registro().registrar(
        clienteId: 'cli-1',
        importe: Dinero.deTexto('500.00'),
      ),
      throwsA(
        isA<CobroRechazado>().having(
          (e) => e.motivo,
          'motivo',
          MotivoNoCobro.sinFolios,
        ),
      ),
    );
  });

  test('los folios de cobro son su propia serie, no la de las ventas', () {
    // Comparten el formato del prefijo pero no el contador: si compartieran serie,
    // un recibo y una remisión podrían traer el mismo número impreso.
    folios.guardar(
      RangoFolios(tipo: 'venta', desde: 1, hasta: 1000, consumidoHasta: 500),
      asignadoEn: '2026-09-29T06:00:00.000Z',
    );
    final cobro = registro().registrar(
      clienteId: 'cli-1',
      importe: Dinero.deTexto('500.00'),
    );
    expect(cobro.folioConsecutivo, equals(1));
    expect(folios.leer('venta')!.consumidoHasta, equals(500));
  });

  test('dos cobros consecutivos no repiten folio', () {
    final r = registro();
    final primero = r.registrar(clienteId: 'cli-1', importe: Dinero.deTexto('100.00'));
    final segundo = r.registrar(clienteId: 'cli-1', importe: Dinero.deTexto('200.00'));

    expect(primero.folioConsecutivo, equals(1));
    expect(segundo.folioConsecutivo, equals(2));
    expect(db.select('SELECT * FROM cobros').length, equals(2));
    expect(db.select('SELECT * FROM outbox').length, equals(2));
  });

  test('un cobro fallido NO deja hueco en la numeración', () {
    final r = registro();
    r.registrar(clienteId: 'cli-1', importe: Dinero.deTexto('100.00'));

    // Falla por falta de referencia: la transacción se deshace completa.
    expect(
      () => r.registrar(
        clienteId: 'cli-1',
        importe: Dinero.deTexto('200.00'),
        formaDePago: FormaDePago.cheque,
      ),
      throwsA(isA<CobroRechazado>()),
    );

    // El siguiente reutiliza el número que no se llegó a usar.
    final tercero = r.registrar(clienteId: 'cli-1', importe: Dinero.deTexto('300.00'));
    expect(tercero.folioConsecutivo, equals(2));
  });

  test('un cliente que no existe tumba la transacción completa', () {
    // La llave foránea de `cobros.cliente_id` lo impide, y el rollback deja el
    // folio sin quemar: no puede quedar un sobre apuntando a nadie.
    expect(
      () => registro().registrar(
        clienteId: 'cli-que-no-existe',
        importe: Dinero.deTexto('500.00'),
      ),
      throwsA(anything),
    );
    expect(db.select('SELECT * FROM cobros'), isEmpty);
    expect(db.select('SELECT * FROM outbox'), isEmpty);
    expect(folios.leer('cobro')!.consumidoHasta, equals(0));
  });

  // -------------------------------------------------------------------------
  // La impresión
  // -------------------------------------------------------------------------

  test('marcarImpreso guarda los bytes y no los reemplaza al reimprimir', () {
    final r = registro();
    final cobro = r.registrar(clienteId: 'cli-1', importe: Dinero.deTexto('500.00'));

    r.marcarImpreso(cobro.id, ticket: const [1, 2, 3]);
    expect(db.select('SELECT impreso FROM cobros').single['impreso'], equals(1));
    expect(r.ticketGuardado(cobro.id), equals([1, 2, 3]));

    // La reimpresión reusa los bytes originales: el papel tiene que decir
    // exactamente lo mismo que el que recibió el cliente.
    r.marcarImpreso(cobro.id, ticket: const [9, 9, 9]);
    expect(r.ticketGuardado(cobro.id), equals([1, 2, 3]));
  });

  test('marcarImpreso de un cobro que no existe falla', () {
    expect(
      () => registro().marcarImpreso('no-existe', ticket: const [1]),
      throwsArgumentError,
    );
  });

  // -------------------------------------------------------------------------
  // El recibo
  // -------------------------------------------------------------------------

  test('EL RECIBO NO IMPRIME EL SALDO', () {
    // Por la misma razón que la remisión (ADR 0002 §11): el teléfono solo trae una
    // caché que puede tener horas y que no incluye los cobros de otros equipos.
    // Imprimir "le quedan $1,500" en un papel que el cliente conserva es crear una
    // disputa donde él sostiene el número impreso y la oficina el suyo.
    final cobro = registro().registrar(
      clienteId: 'cli-1',
      importe: Dinero.deTexto('500.00'),
      saldoAntes: Dinero.deTexto('2000.00'),
    );

    final bytes = ticketDeCobro(
      cobro,
      negocio: const DatosDelNegocio(nombre: 'Distribuidora El Sol'),
      visita: const DatosDeLaVisita(
        nombreCliente: 'Abarrotes Doña Mary',
        nombreVendedor: 'Juan Pérez',
        codigoCliente: 'C00001',
      ),
    );
    final texto = textoDe(bytes);

    expect(texto, contains('500.00'));
    expect(texto, contains('VEND01-000001'));
    expect(texto, contains('RECIBO DE PAGO'));
    expect(texto, contains('Consulta tu saldo con tu vendedor'));
    // El saldo anterior y el resultante NO aparecen.
    expect(texto, isNot(contains('2000.00')));
    expect(texto, isNot(contains('1500.00')));
  });

  test('el recibo dice que no es fiscal', () {
    final cobro = registro().registrar(
      clienteId: 'cli-1',
      importe: Dinero.deTexto('500.00'),
    );
    final texto = textoDe(
      ticketDeCobro(
        cobro,
        negocio: const DatosDelNegocio(nombre: 'Distribuidora El Sol'),
        visita: const DatosDeLaVisita(
          nombreCliente: 'Abarrotes Doña Mary',
          nombreVendedor: 'Juan Pérez',
        ),
      ),
    );
    expect(texto, contains('DOCUMENTO NO FISCAL'));
    expect(texto, contains('Recibi el pago'));
  });

  test('el recibo de una transferencia trae su referencia', () {
    final cobro = registro().registrar(
      clienteId: 'cli-1',
      importe: Dinero.deTexto('500.00'),
      formaDePago: FormaDePago.transferencia,
      referencia: 'SPEI-77231',
    );
    final texto = textoDe(
      ticketDeCobro(
        cobro,
        negocio: const DatosDelNegocio(nombre: 'Distribuidora El Sol'),
        visita: const DatosDeLaVisita(
          nombreCliente: 'Abarrotes Doña Mary',
          nombreVendedor: 'Juan Pérez',
        ),
      ),
    );
    expect(texto, contains('TRANSFERENCIA'));
    expect(texto, contains('SPEI-77231'));
  });

  test('NINGUNA LÍNEA DEL RECIBO SE DESBORDA DE LAS 32 COLUMNAS', () {
    // El desborde no falla: la impresora continúa el texto en el renglón
    // siguiente y corre el resto del ticket. Ya pasó una vez con la remisión.
    for (final forma in FormaDePago.values) {
      final cobro = registro().registrar(
        clienteId: 'cli-1',
        importe: Dinero.deTexto('123456.78'),
        formaDePago: forma,
        referencia: forma.entraAlArqueo
            ? null
            : 'REFERENCIA-BANCARIA-MUY-LARGA-DE-VERDAD-0001',
      );
      for (final copia in [0, 1]) {
        final vista = decodificar(
          ticketDeCobro(
            cobro,
            negocio: const DatosDelNegocio(
              nombre: 'Distribuidora El Ñandú del Bajío',
              direccion: 'Av. Siempre Viva 742, Col. Centro, Celaya',
              leyendaFinal: 'Aclaraciones al 461 123 4567 de 9 a 6',
            ),
            visita: const DatosDeLaVisita(
              nombreCliente: 'La Esquina de Ñoño y Asociados del Centro',
              nombreVendedor: 'Juan Carlos Pérez Hernández',
              codigoCliente: 'C00001',
            ),
            copia: copia,
          ),
        );
        expect(vista.desconocidos, isEmpty,
            reason: 'el generador emitió un comando que la vista previa no entiende');
        for (final l in vista.lineas) {
          expect(
            l.columnasOcupadas,
            lessThanOrEqualTo(columnas58mm),
            reason: 'se desborda con $forma, copia $copia: «${l.texto}»',
          );
        }
      }
    }
  });
}
