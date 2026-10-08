/// Lo que el vendedor vendió hoy y el dinero que debe traer.
///
/// ───────────────────────────────────────────────────────────────────────────
/// LA MISMA FÓRMULA QUE EL ARQUEO, Y POR ESO SE ESCRIBE AQUÍ
/// ───────────────────────────────────────────────────────────────────────────
/// `efectivo = ventas del día pagadas en EFECTIVO`
///
/// Es exactamente lo que calcula `_efectivo_esperado` en el servidor, que es la
/// cuenta con la que la oficina le cobra al vendedor en el corte. Si esta
/// pantalla usara otra —incluir las transferencias— el vendedor llegaría a la
/// bodega con un número en la cabeza distinto del que le van a pedir, y la
/// discusión sería todas las tardes.
///
/// Todo es de contado (ADR 0002 §81). La transferencia entró al sistema pero no
/// a su bolsa: se muestra aparte.
///
/// Se lee de SQLite, así que la pantalla funciona sin señal — que es cuando el
/// vendedor la necesita: a media ruta, decidiendo si le alcanza el cambio.
library;

import 'package:sqlite3/sqlite3.dart';

import 'dinero.dart';
import 'forma_de_pago.dart';

/// Un renglón de lo que se le vendió al cliente.
class PartidaDeMiDia {
  const PartidaDeMiDia({
    required this.producto,
    required this.cantidad,
    required this.unidad,
    required this.precio,
    required this.importe,
  });

  final String producto;

  /// En la presentación en que se vendió: «2 CAJA», no «48 PZA».
  final num cantidad;
  final String unidad;
  final Dinero precio;
  final Dinero importe;

  /// `2` y no `2.0`; `1.5` se queda.
  String get cantidadTexto =>
      cantidad == cantidad.roundToDouble() ? cantidad.round().toString() : '$cantidad';
}

/// Una venta del día, para la lista.
class VentaDeMiDia {
  const VentaDeMiDia({
    required this.folio,
    required this.cliente,
    required this.total,
    required this.formaDePago,
    required this.sincronizada,
    this.partidas = const [],
  });

  /// Lo que se vendió, en el orden del ticket. Se despliega al tocar la venta:
  /// es lo que el vendedor necesita para contestar «¿qué me dejaste el martes?».
  final List<PartidaDeMiDia> partidas;

  final String folio;
  final String cliente;
  final Dinero total;
  final FormaDePago formaDePago;

  /// Si ya salió del teléfono. Se muestra porque una venta sin sincronizar es
  /// dinero que la oficina todavía no sabe que existe.
  final bool sincronizada;
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
    required this.efectivo,
    required this.transferencias,
    required this.sinSincronizar,
    this.tocadasPorOficina = const [],
  });

  final List<VentaDeMiDia> ventas;

  /// Lo que la oficina canceló o corrigió hoy.
  final List<VentaTocadaPorOficina> tocadasPorOficina;

  /// Lo que el vendedor debe entregar: las ventas en efectivo. Es la cuenta del
  /// arqueo.
  final Dinero efectivo;

  /// Las ventas por transferencia: entran al banco, no a la bolsa.
  final Dinero transferencias;

  /// Documentos del día que todavía no han salido del teléfono.
  final int sinSincronizar;

  /// Todo lo vendido: efectivo y transferencias.
  Dinero get vendido => efectivo + transferencias;

  bool get vacio => ventas.isEmpty;
}

class RepoMiDia {
  RepoMiDia(this._db);

  final Database _db;

  List<PartidaDeMiDia> _partidasDe(String ventaId) => _db
      .select(
        '''
        SELECT COALESCE(p.nombre, vp.producto_id) AS producto, vp.cantidad,
               vp.unidad_codigo, vp.precio_unitario, vp.importe
          FROM venta_partidas vp
          LEFT JOIN productos p ON p.id = vp.producto_id
         WHERE vp.venta_id = ?
         ORDER BY vp.linea
        ''',
        [ventaId],
      )
      .map(
        (f) => PartidaDeMiDia(
          producto: f['producto'] as String,
          cantidad: f['cantidad'] as num,
          unidad: f['unidad_codigo'] as String,
          precio: _dinero(f['precio_unitario']),
          importe: _dinero(f['importe']),
        ),
      )
      .toList();

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
          SELECT v.id, v.folio_local, v.total, v.forma_pago, v.sincronizada,
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
            formaDePago: FormaDePago.deCodigo(f['forma_pago'] as String?),
            sincronizada: (f['sincronizada'] as int? ?? 0) == 1,
            partidas: _partidasDe(f['id'] as String),
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

    Dinero suma(FormaDePago forma) => ventas
        .where((v) => v.formaDePago == forma)
        .fold(Dinero.cero, (acc, v) => acc + v.total);

    final pendientes = _db.select(
      '''
      SELECT COUNT(*) AS n FROM ventas
       WHERE fecha_operativa = ? AND estado = 'confirmada'
         AND COALESCE(sincronizada, 0) = 0
      ''',
      [fechaOperativa],
    ).first['n'] as int;

    return MiDia(
      ventas: ventas,
      efectivo: suma(FormaDePago.efectivo),
      transferencias: suma(FormaDePago.transferencia),
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
