/// El borrado remoto desde el lado del teléfono (Fase 9).
///
/// ─────────────────────────────────────────────────────────────────────────
/// LA REGLA, Y LAS CUATRO FORMAS DE ROMPERLA
/// ─────────────────────────────────────────────────────────────────────────
///     NUNCA SE BORRA LO QUE NO SE HA ENTREGADO.
///
/// 1. **Borrar en cuanto llega la orden.** Es lo que haría un sistema que
///    piensa en el robo y no en lo que de verdad pasa: una renuncia, un cambio
///    de teléfono, un equipo extraviado. En los tres casos el aparato trae
///    dentro un día de ventas, y borrarlas es perder dinero cobrado.
/// 2. **Confirmar antes de borrar.** Dejaría al servidor creyendo que el
///    equipo está limpio cuando sigue teniendo todo dentro.
/// 3. **Dejar que la consulta de órdenes bloquee la sincronización.** Un
///    endpoint nuevo que falla no puede impedir que se entreguen ventas.
/// 4. **Intentar el pull con el equipo suspendido.** El servidor contesta 401,
///    el sincronizador lo traduce a «sesión vencida», y el vendedor va a
///    teclear su PIN para arreglar algo que no se arregla así.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

import 'ayudas_sync.dart';

Map<String, Object?> ordenDeBorrado({
  String estado = 'suspendido',
  String motivo = 'el vendedor dejó la empresa',
  int? diasSinSincronizar = 0,
  int diasMax = 7,
}) =>
    {
      'estado': estado,
      'borrar': true,
      'borrado_motivo': motivo,
      'dias_max_offline': diasMax,
      'dias_sin_sincronizar': diasSinSincronizar,
    };

void main() {
  late Database db;
  late Outbox outbox;

  setUp(() {
    db = baseLocal();
    outbox = Outbox(db);
  });

  tearDown(() => db.dispose());

  group('la orden de borrado', () {
    test('con la cola VACÍA el resultado es «listo para borrar»', () async {
      final transporte = TransporteFalso([])..ordenes = ordenDeBorrado();

      final resultado =
          await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

      expect(resultado.fin, FinDeSync.borradoListo);
      expect(resultado.ordenes!.borrar, isTrue);
      expect(resultado.detalle, 'el vendedor dejó la empresa');
    });

    test('con cola PENDIENTE no se borra nada', () async {
      // El caso que cuesta dinero si se hace mal. La red se cae a media
      // entrega: la orden sigue ahí, la cola también, y no se borra.
      encolarAlta(outbox, db, 'c1');
      final transporte = TransporteFalso([const SeCaeLaRed()])
        ..ordenes = ordenDeBorrado();

      final resultado =
          await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

      // El `fin` es el del fallo real —se cayó la red—, no «borradoPendiente»:
      // confundirlos haría que la pantalla dijera «entregando» cuando lo que
      // pasa es que no hay señal, y nadie sabría que hay que buscarla.
      expect(resultado.fin, FinDeSync.sinRed);

      // Lo que SÍ tiene que seguir visible es la orden, porque de ella depende
      // que el equipo quede bloqueado. Si viajara solo en el camino feliz, un
      // corte de red devolvería al vendedor a la pantalla normal con un equipo
      // que la oficina ya dio de baja.
      expect(resultado.ordenes!.borrar, isTrue);

      // Y lo más importante: la venta sigue en la cola.
      expect(outbox.resumen().pendientes, 1);
    });

    test('tras el corte, la siguiente corrida entrega y recién ahí borra',
        () async {
      // El equipo bloqueado no es un equipo perdido: en cuanto aparece la señal
      // termina lo que empezó.
      encolarAlta(outbox, db, 'c1');
      final sinRed = TransporteFalso([const SeCaeLaRed()])
        ..ordenes = ordenDeBorrado();
      await armarSincronizador(db, sinRed).sincronizar(cursorActual: 0);
      expect(outbox.resumen().pendientes, 1);

      final conRed = TransporteFalso([
        Responde.push(sobres: outbox.siguienteLote()),
      ])
        ..ordenes = ordenDeBorrado();
      final segunda =
          await armarSincronizador(db, conRed).sincronizar(cursorActual: 0);

      expect(segunda.fin, FinDeSync.borradoListo);
      expect(outbox.resumen().pendientes, 0);
    });

    test('se entrega primero y se borra en la MISMA corrida', () async {
      // Es el camino feliz completo: el teléfono tenía algo pendiente, lo
      // sube, y recién entonces queda listo para borrarse.
      encolarAlta(outbox, db, 'c1');
      final transporte = TransporteFalso([
        Responde.push(sobres: outbox.siguienteLote()),
      ])
        ..ordenes = ordenDeBorrado();

      final resultado =
          await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

      expect(resultado.sobresConfirmados, 1);
      expect(resultado.fin, FinDeSync.borradoListo);
      expect(outbox.resumen().pendientes, 0);
    });

    test('un sobre en cuarentena no impide el borrado', () async {
      // La cuarentena LOCAL es para lo que el servidor rechazó: ya lo vio y
      // dijo que no. Esperar a entregarlo dejaría al equipo bloqueado para
      // siempre por un sobre que nunca va a ser aceptado.
      encolarAlta(outbox, db, 'c1');
      final transporte = TransporteFalso([
        Responde.push(
          sobres: outbox.siguienteLote(),
          estado: 'rechazada',
          errorCodigo: 'conflicto_de_datos',
        ),
      ])
        ..ordenes = ordenDeBorrado();

      final resultado =
          await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

      expect(resultado.sobresEnCuarentena, 1);
      expect(resultado.fin, FinDeSync.borradoListo);
    });

    test('no se intenta el pull: el equipo se va a borrar', () async {
      final transporte = TransporteFalso([])..ordenes = ordenDeBorrado();
      await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);
      expect(transporte.llamadas.where((l) => l.contains('pull')), isEmpty);
    });
  });

  group('el equipo suspendido sin orden de borrado', () {
    test('entrega y no intenta recibir', () async {
      // El servidor le niega el pull con 401. Si el sincronizador lo intentara,
      // traduciría ese 401 a «sesión vencida» y mandaría al vendedor a teclear
      // su PIN para arreglar algo que no se arregla así.
      encolarAlta(outbox, db, 'c1');
      final transporte = TransporteFalso([
        Responde.push(sobres: outbox.siguienteLote()),
      ])
        ..ordenes = {
          'estado': 'suspendido',
          'borrar': false,
          'borrado_motivo': null,
          'dias_max_offline': 7,
          'dias_sin_sincronizar': 0,
        };

      final resultado =
          await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

      expect(resultado.sobresConfirmados, 1);
      expect(resultado.fin, FinDeSync.completa);
      expect(transporte.llamadas.where((l) => l.contains('pull')), isEmpty);
    });
  });

  group('las órdenes no son una dependencia dura', () {
    test('un servidor más viejo (404) no impide sincronizar', () async {
      // Convertir un endpoint nuevo en requisito para entregar ventas sería
      // cambiar una función de administración por la operación del día.
      encolarAlta(outbox, db, 'c1');
      final transporte = TransporteFalso([
        Responde.push(sobres: outbox.siguienteLote()),
        Responde.pull(cambios: const []),
      ])
        ..codigoDeOrdenes = 404;

      final resultado =
          await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

      expect(resultado.sobresConfirmados, 1);
      expect(resultado.fin, FinDeSync.completa);
      expect(resultado.ordenes, isNull);
    });

    test('una respuesta con OTRA FORMA tampoco rompe nada', () async {
      // El fallo que encontró una prueba de la app: un JSON con otra forma
      // —la página de error de un proxy, un servidor más viejo— produce un
      // `TypeError`, que es un `Error` y NO una `Exception`. Con `on Exception`
      // se escapaba y rompía la sincronización completa: el vendedor no podía
      // entregar sus ventas porque una consulta administrativa devolvió algo
      // raro.
      encolarAlta(outbox, db, 'c1');
      final transporte = TransporteFalso([
        Responde.push(sobres: outbox.siguienteLote()),
        Responde.pull(cambios: const []),
      ])
        // La forma del pull contestada a la consulta de órdenes.
        ..ordenes = const {'cursor': 7, 'hay_mas': false, 'cambios': []};

      final resultado =
          await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

      expect(resultado.sobresConfirmados, 1);
      expect(resultado.ordenes, isNull);
    });

    test('un 500 en las órdenes tampoco', () async {
      encolarAlta(outbox, db, 'c1');
      final transporte = TransporteFalso([
        Responde.push(sobres: outbox.siguienteLote()),
        Responde.pull(cambios: const []),
      ])
        ..codigoDeOrdenes = 500;

      final resultado =
          await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);
      expect(resultado.sobresConfirmados, 1);
    });

    test('un 401 SÍ detiene todo: el token no sirve', () async {
      encolarAlta(outbox, db, 'c1');
      final transporte = TransporteFalso([])..codigoDeOrdenes = 401;

      final resultado =
          await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

      expect(resultado.fin, FinDeSync.sesionInvalida);
      // Y la cola no se toca: no se perdió nada.
      expect(outbox.resumen().pendientes, 1);
    });

    test('sin red se corta antes de encadenar reintentos', () async {
      // Diez reintentos de red con el teléfono en la mano del vendedor son
      // medio minuto de nada.
      encolarAlta(outbox, db, 'c1');
      final transporte = TransporteFalso([])..codigoDeOrdenes = null;
      transporte.ordenes = ordenDeBorrado();
      // Se fuerza el corte sustituyendo el transporte por uno que siempre cae.
      final caido = _TransporteSiempreCaido();

      final resultado =
          await armarSincronizador(db, caido).sincronizar(cursorActual: 0);

      expect(resultado.fin, FinDeSync.sinRed);
      expect(caido.llamadas, 1, reason: 'no debe reintentar en cadena');
      expect(outbox.resumen().pendientes, 1);
    });
  });

  group('la vigencia del acceso', () {
    test('avisa dos días antes de que caduque', () {
      const vigencia = VigenciaDelAcceso(diasMaxOffline: 7, diasSinSincronizar: 5);
      expect(vigencia.diasRestantes, 2);
      expect(vigencia.conviendeAvisar, isTrue);
      expect(vigencia.caducado, isFalse);
    });

    test('con margen de sobra no molesta', () {
      const vigencia = VigenciaDelAcceso(diasMaxOffline: 7, diasSinSincronizar: 1);
      expect(vigencia.conviendeAvisar, isFalse);
    });

    test('caducado es cero o menos, no solo negativo', () {
      const justo = VigenciaDelAcceso(diasMaxOffline: 7, diasSinSincronizar: 7);
      expect(justo.caducado, isTrue);
      const pasado = VigenciaDelAcceso(diasMaxOffline: 7, diasSinSincronizar: 9);
      expect(pasado.caducado, isTrue);
    });

    test('nunca sincronizado no es cero días', () {
      // «Nunca» y «hoy» no son lo mismo: un equipo recién registrado que
      // todavía no sube nada no debe verse como al día ni como caducado.
      const nunca = VigenciaDelAcceso(diasMaxOffline: 7, diasSinSincronizar: null);
      expect(nunca.diasRestantes, isNull);
      expect(nunca.conviendeAvisar, isFalse);
      expect(nunca.caducado, isFalse);
    });
  });

  group('OrdenesDelServidor.deJson', () {
    test('lee lo que manda el servidor', () {
      final ordenes = OrdenesDelServidor.deJson(ordenDeBorrado());
      expect(ordenes.estado, 'suspendido');
      expect(ordenes.borrar, isTrue);
      expect(ordenes.soloEntrega, isTrue);
      expect(ordenes.borradoMotivo, 'el vendedor dejó la empresa');
    });

    test('un equipo activo sin orden no entra en ningún camino especial', () {
      final ordenes = OrdenesDelServidor.deJson(const {
        'estado': 'activo',
        'borrar': false,
        'borrado_motivo': null,
        'dias_max_offline': 7,
        'dias_sin_sincronizar': 0,
      });
      expect(ordenes.borrar, isFalse);
      expect(ordenes.soloEntrega, isFalse);
    });
  });
}

class _TransporteSiempreCaido implements Transporte {
  int llamadas = 0;

  @override
  Future<RespuestaHttp> obtener(
    String ruta, {
    Map<String, String>? parametros,
  }) async {
    llamadas++;
    throw const ErrorDeRed('sin conexión');
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    llamadas++;
    throw const ErrorDeRed('sin conexión');
  }
}
