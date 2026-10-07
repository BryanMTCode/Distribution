/// La marca: Distribuciones SE.
///
/// El logo de la empresa es un parche bordado: un óvalo blanco con orilla gris
/// claro y, en rojo y letra manuscrita, «Distribuciones» arriba y «SE» grande
/// abajo. Aquí se dibuja con widgets y no con una foto del bordado, para que se
/// vea nítido en cualquier tamaño de pantalla.
///
/// La letra manuscrita es Dancing Script, que va dentro del APK
/// (`assets/fuentes`, licencia OFL): así el logo se ve igual en cualquier
/// teléfono. Es la misma con la que se dibujó el ícono de la app.
library;

import 'package:flutter/material.dart';

/// El rojo del bordado.
const rojoDistribucionesSE = Color(0xFFE0282E);

const nombreDeLaEmpresa = 'Distribuciones SE';

/// El tema de la app, con el rojo de la marca.
ThemeData temaDeLaMarca() {
  final esquema = ColorScheme.fromSeed(
    seedColor: rojoDistribucionesSE,
    // `fidelity` conserva el rojo tal cual en vez de suavizarlo: es el color de
    // la marca, no una sugerencia.
    dynamicSchemeVariant: DynamicSchemeVariant.fidelity,
  ).copyWith(primary: rojoDistribucionesSE, onPrimary: Colors.white);
  return ThemeData(
    colorScheme: esquema,
    useMaterial3: true,
    // La app se usa a pleno sol en la calle: texto grande y contraste alto.
    visualDensity: VisualDensity.comfortable,
    appBarTheme: AppBarTheme(
      backgroundColor: esquema.primary,
      foregroundColor: esquema.onPrimary,
    ),
  );
}

/// El parche: óvalo blanco, letras rojas.
class LogoDistribucionesSE extends StatelessWidget {
  const LogoDistribucionesSE({super.key, this.ancho = 260});

  final double ancho;

  @override
  Widget build(BuildContext context) {
    final alto = ancho * 0.56;
    const letra = TextStyle(
      color: rojoDistribucionesSE,
      fontFamily: 'DancingScript',
      fontWeight: FontWeight.w700,
      height: 1.0,
    );
    return Semantics(
      label: nombreDeLaEmpresa,
      child: Container(
        key: const Key('logo_distribuciones_se'),
        width: ancho,
        height: alto,
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.all(Radius.elliptical(ancho / 2, alto / 2)),
          border: Border.all(color: const Color(0xFFD9D9D9), width: ancho * 0.018),
          boxShadow: const [
            BoxShadow(color: Color(0x33000000), blurRadius: 6, offset: Offset(0, 2)),
          ],
        ),
        child: ExcludeSemantics(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              FittedBox(
                child: Padding(
                  padding: EdgeInsets.symmetric(horizontal: ancho * 0.12),
                  child: Text('Distribuciones', style: letra.copyWith(fontSize: ancho * 0.13)),
                ),
              ),
              SizedBox(height: alto * 0.04),
              Text('SE', style: letra.copyWith(fontSize: ancho * 0.2)),
            ],
          ),
        ),
      ),
    );
  }
}
