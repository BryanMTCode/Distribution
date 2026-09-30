/// Lienzo espacial: dónde estoy respecto a los clientes que ya conozco.
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ UN RADAR Y NO UN MAPA
/// ─────────────────────────────────────────────────────────────────────────
/// Un mapa con calles necesita descargar mosaicos, y **la corrección de
/// coordenadas se hace justo donde no hay red** —dentro de un mercado techado,
/// en una colonia sin cobertura—. Un mapa que se queda en cuadros grises es
/// peor que no tener mapa: ocupa la pantalla y no dice nada.
///
/// Este lienzo no descarga nada. Dibuja lo que el teléfono ya sabe: el punto
/// donde está parado el vendedor, y los clientes de su ruta alrededor, cada uno
/// a su distancia y en su rumbo real. Es referencia **relativa**, que es
/// precisamente la que sirve para decidir: "la tienda que ya tengo registrada
/// está a 30 metros al norte, entonces esta de enfrente es otra".
///
/// Y cuando el vendedor mueve el punto con los botones cardinales, los vecinos
/// **se desplazan en el lienzo**. Ese movimiento es la confirmación visual de
/// que el ajuste va para el lado correcto, que era lo que faltaba en la prueba
/// de campo.
library;

import 'dart:math' as math;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';

/// Radio que abarca el lienzo cuando no hay vecinos que obliguen a más.
const radioLienzoMinimoMetros = 60.0;

/// Radio máximo. Más allá, un vecino deja de ser referencia útil y solo
/// comprime el dibujo hasta volverlo ilegible.
const radioLienzoMaximoMetros = 400.0;

class LienzoEspacial extends StatelessWidget {
  const LienzoEspacial({
    super.key,
    required this.centro,
    required this.vecinos,
    this.alto = 220,
  });

  /// Dónde está el punto ahora, ya con los ajustes cardinales aplicados.
  final Ubicacion centro;

  /// Clientes conocidos alrededor, con su distancia y rumbo.
  final List<PosibleDuplicado> vecinos;

  final double alto;

  /// El radio que se dibuja: alcanza para el vecino más lejano, con holgura, y
  /// acotado a los extremos útiles.
  double get radioMetros {
    final masLejano = vecinos.isEmpty
        ? 0.0
        : vecinos.map((v) => v.distanciaMetros).reduce(math.max);
    return (masLejano * 1.25)
        .clamp(radioLienzoMinimoMetros, radioLienzoMaximoMetros);
  }

  @override
  Widget build(BuildContext context) {
    final colores = Theme.of(context).colorScheme;

    return Semantics(
      // Sin esto, un lector de pantalla solo anuncia "imagen". La descripción
      // dice lo mismo que el dibujo, en palabras.
      label: vecinos.isEmpty
          ? 'Lienzo de ubicación. No hay clientes conocidos cerca.'
          : 'Lienzo de ubicación con ${vecinos.length} '
              '${vecinos.length == 1 ? "cliente" : "clientes"} alrededor. '
              'El más cercano a ${vecinos.first.distanciaMetros.round()} metros.',
      child: SizedBox(
        key: const Key('lienzo_espacial'),
        height: alto,
        width: double.infinity,
        child: CustomPaint(
          painter: _PintorDelLienzo(
            centro: centro,
            vecinos: vecinos,
            radioMetros: radioMetros,
            colorFondo: colores.surfaceContainerHighest,
            colorAnillo: colores.outlineVariant,
            colorTexto: colores.onSurfaceVariant,
            colorYo: colores.primary,
            colorVecino: colores.tertiary,
            colorVecinoCerca: colores.error,
            esManual: centro.origen == OrigenUbicacion.manual,
          ),
        ),
      ),
    );
  }
}

class _PintorDelLienzo extends CustomPainter {
  const _PintorDelLienzo({
    required this.centro,
    required this.vecinos,
    required this.radioMetros,
    required this.colorFondo,
    required this.colorAnillo,
    required this.colorTexto,
    required this.colorYo,
    required this.colorVecino,
    required this.colorVecinoCerca,
    required this.esManual,
  });

  final Ubicacion centro;
  final List<PosibleDuplicado> vecinos;
  final double radioMetros;
  final Color colorFondo;
  final Color colorAnillo;
  final Color colorTexto;
  final Color colorYo;
  final Color colorVecino;
  final Color colorVecinoCerca;
  final bool esManual;

  @override
  void paint(Canvas lienzo, Size tamano) {
    final centroPx = Offset(tamano.width / 2, tamano.height / 2);
    // Se deja margen para las etiquetas de los anillos y los nombres.
    final radioPx = math.min(tamano.width, tamano.height) / 2 - 18;

    lienzo.drawRRect(
      RRect.fromRectAndRadius(
        Offset.zero & tamano,
        const Radius.circular(12),
      ),
      Paint()..color = colorFondo,
    );

    _anillos(lienzo, centroPx, radioPx);
    _norte(lienzo, centroPx, radioPx);
    _ejes(lienzo, centroPx, radioPx);

    for (final v in vecinos) {
      _vecino(lienzo, centroPx, radioPx, v);
    }

    _yo(lienzo, centroPx);
  }

  /// Anillos de distancia, con su etiqueta en metros.
  ///
  /// Sin la etiqueta el dibujo es decorativo: el vendedor necesita saber si el
  /// punto de al lado está a 20 metros o a 200.
  void _anillos(Canvas lienzo, Offset centroPx, double radioPx) {
    final trazo = Paint()
      ..color = colorAnillo
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1;

    for (final fraccion in const [0.33, 0.66, 1.0]) {
      lienzo.drawCircle(centroPx, radioPx * fraccion, trazo);
      _texto(
        lienzo,
        '${(radioMetros * fraccion).round()} m',
        Offset(centroPx.dx + 4, centroPx.dy - radioPx * fraccion - 6),
        tamano: 9,
      );
    }
  }

  void _ejes(Canvas lienzo, Offset centroPx, double radioPx) {
    final trazo = Paint()
      ..color = colorAnillo.withValues(alpha: 0.5)
      ..strokeWidth = 1;
    lienzo.drawLine(
      Offset(centroPx.dx - radioPx, centroPx.dy),
      Offset(centroPx.dx + radioPx, centroPx.dy),
      trazo,
    );
    lienzo.drawLine(
      Offset(centroPx.dx, centroPx.dy - radioPx),
      Offset(centroPx.dx, centroPx.dy + radioPx),
      trazo,
    );
  }

  void _norte(Canvas lienzo, Offset centroPx, double radioPx) {
    _texto(
      lienzo,
      'N',
      Offset(centroPx.dx - 4, centroPx.dy - radioPx - 16),
      tamano: 11,
      negrita: true,
    );
  }

  /// Un cliente conocido.
  ///
  /// Los que caen dentro del radio de duplicado se pintan distinto: son los que
  /// van a disparar el aviso al guardar, y verlos antes evita la sorpresa.
  void _vecino(
    Canvas lienzo,
    Offset centroPx,
    double radioPx,
    PosibleDuplicado v,
  ) {
    final proporcion = (v.distanciaMetros / radioMetros).clamp(0.0, 1.0);
    // El rumbo es desde el norte en sentido del reloj; en el lienzo el norte es
    // arriba (−Y) y el este la derecha (+X).
    final anguloRad = v.rumboGrados * math.pi / 180;
    final punto = Offset(
      centroPx.dx + radioPx * proporcion * math.sin(anguloRad),
      centroPx.dy - radioPx * proporcion * math.cos(anguloRad),
    );

    final sospechoso = v.distanciaMetros <= radioDuplicadoMetros;
    lienzo.drawCircle(
      punto,
      sospechoso ? 7 : 5,
      Paint()..color = sospechoso ? colorVecinoCerca : colorVecino,
    );

    // El nombre, recortado: en el lienzo cabe una referencia, no una etiqueta
    // completa.
    final etiqueta = v.nombreComercial.length > 14
        ? '${v.nombreComercial.substring(0, 13)}…'
        : v.nombreComercial;
    _texto(lienzo, etiqueta, punto + const Offset(9, -6), tamano: 9);
  }

  /// El vendedor. Se dibuja al final para que quede encima de todo.
  void _yo(Canvas lienzo, Offset centroPx) {
    lienzo.drawCircle(centroPx, 11, Paint()..color = colorYo.withValues(alpha: 0.25));
    lienzo.drawCircle(centroPx, 5, Paint()..color = colorYo);

    if (esManual) {
      // Un punto corregido a mano no es una lectura del satélite, y el lienzo lo
      // dice: el anillo punteado distingue "aquí estoy" de "aquí lo puse".
      final trazo = Paint()
        ..color = colorYo
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2;
      for (var i = 0; i < 8; i++) {
        final desde = i * math.pi / 4;
        lienzo.drawArc(
          Rect.fromCircle(center: centroPx, radius: 15),
          desde,
          math.pi / 8,
          false,
          trazo,
        );
      }
    }
  }

  void _texto(
    Canvas lienzo,
    String contenido,
    Offset donde, {
    double tamano = 10,
    bool negrita = false,
  }) {
    final pintor = TextPainter(
      text: TextSpan(
        text: contenido,
        style: TextStyle(
          color: colorTexto,
          fontSize: tamano,
          fontWeight: negrita ? FontWeight.w700 : FontWeight.w400,
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    pintor.paint(lienzo, donde);
  }

  @override
  bool shouldRepaint(_PintorDelLienzo anterior) =>
      anterior.centro != centro ||
      anterior.vecinos.length != vecinos.length ||
      anterior.radioMetros != radioMetros ||
      anterior.esManual != esManual ||
      // Los vecinos se mueven cuando el vendedor ajusta el punto: si no se
      // compara su posición, el lienzo se queda congelado y el ajuste parece no
      // hacer nada.
      !_mismasPosiciones(anterior.vecinos, vecinos);

  bool _mismasPosiciones(
    List<PosibleDuplicado> a,
    List<PosibleDuplicado> b,
  ) {
    if (a.length != b.length) return false;
    for (var i = 0; i < a.length; i++) {
      if (a[i].distanciaMetros != b[i].distanciaMetros ||
          a[i].rumboGrados != b[i].rumboGrados) {
        return false;
      }
    }
    return true;
  }
}
