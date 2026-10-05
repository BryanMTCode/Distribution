/// Lo que el vendedor vendió hoy y el dinero que debe traer.
///
/// ───────────────────────────────────────────────────────────────────────────
/// LA MISMA FÓRMULA QUE EL ARQUEO, Y POR ESO SE ESCRIBE AQUÍ
/// ───────────────────────────────────────────────────────────────────────────
/// `efectivo = ventas de CONTADO del día + cobros en EFECTIVO del día`
///
/// Es exactamente lo que calcula `_efectivo_esperado` en el servidor, que es la
/// cuenta con la que la oficina le cobra al vendedor en la liquidación. Si esta
/// pantalla usara otra —sumar el crédito, o incluir las transferencias— el
/// vendedor llegaría a la bodega con un número en la cabeza distinto del que le
/// van a pedir, y la discusión sería todas las tardes.
///
/// Las ventas a CRÉDITO no entran: no se cobró nada. Y de los cobros, solo los de
/// forma `efectivo`: una transferencia entró al sistema pero no a su bolsa.
///
/// Se lee de SQLite, así que la pantalla funciona sin señal — que es cuando el
/// vendedor la necesita: a media ruta, decidiendo si le alcanza el cambio.
library;

import 'package:sqlite3/sqlite3.dart';

import 'dinero.dart';

/// Una venta del día, para la lista.
class VentaDeMiDia {
  const VentaDeMiDia({
    required this.folio,
    required this.cliente,
    required this.total,
    required this.tipo,
    required this.sincronizada,
  });

  final String folio;
  final String cliente;
  final Dinero total;
  final String tipo;

  /// Si ya salió del teléfono. Se muestra porque una venta sin sincronizar es
  /// dinero que la oficina todavía no sabe que existe.
  final bool sincronizada;

  bool get esContado => tipo == 'contado';
}

/// Una venta del día que la OFICINA canceló o corrigió.
///
/// ───────────────────────────────────────────────────────────────────────────
/// POR QUÉ ESTO SE MUESTRA Y NO SE ESCONDE
/// ───────────────────────────────────────────────────────────────────────────
/// Una venta cancelada deja de sumar en el efectivo del día, y eso es correcto: el
/// arqueo del servidor tampoco la cuenta. Pero si solo cambiara el total, el
/// vendedor vería su número bajar sin explicación y pensaría que la app le perdió
/// una venta — y la próxima vez apuntaría en papel «por si acaso».
///
/// Así que aparece, con su motivo, que es el que gerencia escribió en el panel.
class VentaTocadaPorOficina {
  const VentaTocadaPorOficina({
    required this.folio,
    required this.cliente,
    required this.total,
    required this.cancelada,
    this.nota,
  });

  final String folio;
  final String cliente;

  /// Lo que la venta dice AHORA. En una cancelada ya no se le entrega nada.
  final Dinero total;
  final bool cancelada;

  /// Por qué. Lo escribió la oficina al hacer el cambio.
  final String? nota;
}

/// El corte del día, tal como lo va a ver el vendedor.
class MiDia {
  const MiDia({
    required this.ventas,
    required this.contado,
    required this.credito,
    required this.cobrosEfectivo,
    required this.cobrosOtros,
    required this.sinSincronizar,
    this.tocadasPorOficina = const [],
  });

  final List<VentaDeMiDia> ventas;

  /// Lo que la oficina canceló o corrigió hoy.
  final List<VentaTocadaPorOficina> tocadasPorOficina;

  /// Ventas de contado del día. Dinero que entró a la bolsa.
  final Dinero contado;

  /// Ventas a crédito del día. Mercancía que salió sin dinero.
  final Dinero credito;

  final Dinero cobrosEfectivo;

  /// Transferencias y cheques: entran al sistema, no a la bolsa.
  final Dinero cobrosOtros;

  /// Documentos del día que todavía no han salido del teléfono.
  final int sinSincronizar;

  /// Lo que el vendedor debe entregar. Es la cuenta del arqueo.
  Dinero get efectivo => contado + cobrosEfectivo;

  /// Lo vendido, con crédito incluido. No es dinero en la bolsa.
  Dinero get vendido => contado + credito;

  bool get vacio =>
      ventas.isEmpty &&
      cobrosEfectivo == Dinero.cero &&
      cobrosOtros == Dinero.cero;
}

class RepoMiDia {
  RepoMiDia(this._db);

  final Database _db;

  /// El corte de [fechaOperativa] (`AAAA-MM-DD`).
  ///
  /// Se pide la fecha en vez de calcularla aquí: el día operativo lo decide el
  /// reloj de la app en un solo lugar, y una función que llamara a `DateTime.now`
  /// por su cuenta haría que esta pantalla discrepara del resto por un minuto a
  /// medianoche.
  MiDia delDia(String fechaOperativa) {
    final ventas = _db
        .select(
          '''
          SELECT v.folio_local, v.total, v.tipo, v.sincronizada,
                 COALESCE(c.nombre_comercial, 'Cliente nuevo') AS cliente
            FROM ventas v
            LEFT JOIN clientes c ON c.id = v.cliente_id
           WHERE v.fecha_operativa = ? AND v.estado = 'confirmada'
           ORDER BY v.folio_consecutivo DESC
          ''',
          [fechaOperativa],
        )
        .map(
          (f) => VentaDeMiDia(
            folio: f['folio_local'] as String,
            cliente: f['cliente'] as String,
            total: _dinero(f['total']),
            tipo: f['tipo'] as String,
            sincronizada: (f['sincronizada'] as int? ?? 0) == 1,
          ),
        )
        .toList();

    // Lo que la oficina tocó hoy: las canceladas —que ya no están en la lista de
    // arriba, porque esa pide `confirmada`— y las corregidas, que sí están pero con
    // otro importe. Las dos llevan la nota que gerencia escribió.
    final tocadas = _db
        .select(
          '''
          SELECT v.folio_local, v.total, v.estado, v.nota_oficina,
                 COALESCE(c.nombre_comercial, 'Cliente nuevo') AS cliente
            FROM ventas v
            LEFT JOIN clientes c ON c.id = v.cliente_id
           WHERE v.fecha_operativa = ?
             AND (v.estado = 'cancelada' OR v.nota_oficina IS NOT NULL)
           ORDER BY v.folio_consecutivo DESC
          ''',
          [fechaOperativa],
        )
        .map(
          (f) => VentaTocadaPorOficina(
            folio: f['folio_local'] as String,
            cliente: f['cliente'] as String,
            total: _dinero(f['total']),
            cancelada: (f['estado'] as String?) == 'cancelada',
            nota: f['nota_oficina'] as String?,
          ),
        )
        .toList();

    Dinero sumaVentas(bool contado) => ventas
        .where((v) => v.esContado == contado)
        .fold(Dinero.cero, (acc, v) => acc + v.total);

    // Los cobros se suman en SQL y no en Dart porque no se listan: solo su total
    // entra en la cuenta del efectivo.
    Dinero sumaCobros(String operador) => _dinero(
          _db.select(
            "SELECT COALESCE(sum(importe), 0) AS t FROM cobros "
            "WHERE fecha_operativa = ? AND estado = 'confirmado' "
            'AND forma_pago $operador',
            [fechaOperativa],
          ).first['t'],
        );

    final pendientes = _db.select(
      '''
      SELECT (SELECT COUNT(*) FROM ventas
               WHERE fecha_operativa = ? AND estado = 'confirmada'
                 AND COALESCE(sincronizada, 0) = 0)
           + (SELECT COUNT(*) FROM cobros
               WHERE fecha_operativa = ? AND estado = 'confirmado'
                 AND COALESCE(sincronizado, 0) = 0) AS n
      ''',
      [fechaOperativa, fechaOperativa],
    ).first['n'] as int;

    return MiDia(
      ventas: ventas,
      contado: sumaVentas(true),
      credito: sumaVentas(false),
      cobrosEfectivo: sumaCobros("= 'efectivo'"),
      cobrosOtros: sumaCobros("<> 'efectivo'"),
      sinSincronizar: pendientes,
      tocadasPorOficina: tocadas,
    );
  }
}

/// Dinero desde una columna REAL de SQLite.
///
/// La base local guarda los importes como REAL —un double— y `Dinero` se niega a
/// construirse desde uno: perder medio centavo en una suma es exactamente lo que
/// ese tipo existe para impedir. Así que se redondea a dos decimales EN EL BORDE,
/// una sola vez al leer, y de ahí en adelante toda la aritmética es en centavos
/// enteros. Es el mismo puente que usa `repo_clientes.dart` para el saldo.
Dinero _dinero(Object? valor) =>
    Dinero.deTexto(((valor as num?) ?? 0).toDouble().toStringAsFixed(2));
