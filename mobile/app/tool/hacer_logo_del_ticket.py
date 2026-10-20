"""Dibuja el logo del ticket (1 bit) con la letra del APK y lo deja como Dart."""
import sys
from PIL import Image, ImageDraw, ImageFont

fuente, salida_dart, salida_png = sys.argv[1:4]
ANCHO, ALTO = 288, 160
img = Image.new("L", (ANCHO, ALTO), 255)
d = ImageDraw.Draw(img)
borde = 6
d.ellipse([borde // 2, borde // 2, ANCHO - 1 - borde // 2, ALTO - 1 - borde // 2],
          outline=0, width=borde)

def texto(t, tam_max, ancho_max, y_centro):
    tam = tam_max
    while True:
        f = ImageFont.truetype(fuente, tam)
        x0, y0, x1, y1 = d.textbbox((0, 0), t, font=f)
        if x1 - x0 <= ancho_max or tam <= 10:
            break
        tam -= 1
    w, h = x1 - x0, y1 - y0
    d.text(((ANCHO - w) / 2 - x0, y_centro - h / 2 - y0), t, font=f, fill=0)

texto("Distribuciones", 44, int(ANCHO * 0.74), ALTO * 0.36)
texto("SE", 70, ANCHO, ALTO * 0.68)
bn = img.point(lambda p: 0 if p < 140 else 255, "1")
bn.save(salida_png)

bytes_por_renglon = (ANCHO + 7) // 8
datos = []
for y in range(ALTO):
    for bx in range(bytes_por_renglon):
        b = 0
        for bit in range(8):
            x = bx * 8 + bit
            if x < ANCHO and bn.getpixel((x, y)) == 0:
                b |= 0x80 >> bit
        datos.append(b)

renglones = []
for i in range(0, len(datos), 18):
    renglones.append("  " + ", ".join(f"0x{v:02x}" for v in datos[i:i + 18]) + ",")
with open(salida_dart, "w", encoding="utf-8") as f:
    f.write(
        "/// El logo de Distribuciones SE para la impresora térmica, en un bit por\n"
        "/// punto (ADR 0002 §155).\n"
        "///\n"
        "/// Generado, no a mano: el óvalo y las letras se dibujaron con Dancing Script\n"
        "/// —la misma letra del logo de la app (`assets/fuentes`)— y se pasaron a\n"
        "/// blanco y negro. Un 1 es un punto negro; cada renglón son\n"
        f"/// {bytes_por_renglon} bytes ({ANCHO} puntos), el bit más alto a la izquierda,\n"
        "/// como lo pide `GS v 0`.\n"
        "library;\n\n"
        "import 'escpos.dart';\n\n"
        "/// El parche del logo: 288 × 160 puntos, unos 36 × 20 mm en una térmica de\n"
        "/// 203 ppp.\n"
        "const logoDelTicket = ImagenMonocroma(\n"
        f"  ancho: {ANCHO},\n"
        f"  alto: {ALTO},\n"
        "  puntos: _puntos,\n"
        ");\n\n"
        "const _puntos = <int>[\n" + "\n".join(renglones) + "\n];\n"
    )
print(len(datos), "bytes")
