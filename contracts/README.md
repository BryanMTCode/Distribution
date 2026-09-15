# Contratos entre Dart y Python

Tres serializaciones se calculan en dos lenguajes y **deben coincidir byte a byte**. Divergen en
silencio, y el síntoma aparece meses después con miles de documentos en cuarentena. Por eso cada una
tiene vectores de prueba que **ambas suites ejecutan en CI**.

| Archivo | Contrato |
|---|---|
| `canonical_vectors.json` | Forma canónica del payload y su SHA-256 |
| `argon2_vectors.json` | Parámetros de Argon2id para el login offline |
| `openapi.json` | Contrato HTTP (generado; de aquí sale el cliente Dart) |

Regenerar: `python3 contracts/generar_vectores.py`

---

## 1. Formato canónico

`hash_payload` detecta que un mismo `operacion_id` llegó con un payload distinto — bug del cliente o
manipulación. Para que esa alarma sirva, el hash tiene que ser reproducible en los dos lenguajes.

### Reglas

1. **Objetos**: claves ordenadas por **punto de código Unicode** (no por orden de inserción, no por
   configuración regional). Sin espacios: `{"a":1,"b":2}`.
2. **Claves nulas: se omiten.** `{"a":1,"b":null}` y `{"a":1}` producen el **mismo hash**. Esto elimina
   de raíz la divergencia `null` vs clave ausente.
3. **Arreglos**: el orden **sí** es significativo. Sin espacios: `[1,2,3]`.
4. **Flotantes: prohibidos.** Un `float`/`double` en el payload es un error, no una advertencia.
   - **Dinero** → string con **exactamente 2 decimales**: `"250.00"`, `"-125.50"`, `"0.00"`.
   - **Cantidad** → string con **exactamente 3 decimales**: `"12.000"`, `"1.375"`.
   - Sin separador de miles, sin `+`, sin notación exponencial.
   - Los **enteros** (folios, versiones, líneas) sí van sin comillas: `123`, `-47`, `0`.
5. **Fechas**: RFC 3339 en **UTC**, con **exactamente 3 decimales** y sufijo `Z`:
   `"2026-09-15T03:14:07.123Z"`. Un instante con offset se normaliza a UTC antes de canonizar. Una
   fecha sin zona horaria es un error.
6. **Strings**: se escapan solo `"` `\` y los controles `<0x20` (`\b \f \n \r \t`, el resto como
   `\u00xx` en minúsculas). Todo lo demás se emite **literal en UTF-8**: acentos y emoji **no** se
   escapan como `\uXXXX`. La diagonal `/` **no** se escapa.
7. **Hash**: `sha256(utf8(texto_canónico))` en **hexadecimal minúsculas**.

### Cómo usar los vectores

Cada vector trae `payload`, `canonico` y `sha256`. Verifica **los dos**: cuando el hash falla, la
diferencia se ve en el texto canónico y no en 64 caracteres hexadecimales.

```
payload  → forma canónica  → assert == vector.canonico
                           → sha256 → assert == vector.sha256
```

Implementación de referencia: `server/app/domain/canonico.py` (Python) y `mobile/lib/dsd/canonico.dart`
(Dart).

### Al agregar un caso

Agrégalo a `CASOS` en `generar_vectores.py`, regenera, y **revisa el diff**. Si cambia el hash de un
vector que no tocaste, algo se rompió en el serializador: eso es exactamente lo que este archivo está
para atrapar.

---

## 2. Argon2id

El servidor calcula el hash de la contraseña y **lo replica al dispositivo** para permitir el login sin
señal. Los bindings de Argon2 en Dart son FFI y no comparten valores por defecto con `argon2-cffi`: si
los parámetros no coinciden, el vendedor no puede entrar al empezar el día — el peor momento posible
para descubrirlo.

Parámetros fijados (ver `server/app/core/seguridad.py`, constante `PARAMETROS_ARGON2`):

| Parámetro | Valor |
|---|---|
| Variante | Argon2**id** |
| Memoria | 65536 KiB (64 MiB) |
| Iteraciones | 3 |
| Paralelismo | 4 |
| Longitud de sal | 16 bytes |
| Longitud de hash | 32 bytes |
| Codificación | cadena PHC estándar (`$argon2id$v=19$m=65536,t=3,p=4$...`) |

`argon2_vectors.json` trae contraseñas conocidas con su hash PHC. La prueba de Dart **verifica**
(no genera: la sal es aleatoria) que cada hash valida contra su contraseña, y que una contraseña
equivocada falla.

> **Nota de rendimiento:** 64 MiB por verificación es deliberado en el servidor, pero en un teléfono de
> gama baja tarda. Mídelo en la Fase 3 con el equipo real; si el login offline se siente lento, baja la
> memoria **en ambos lados a la vez** y regenera los vectores. Nunca en uno solo.

---

## 3. OpenAPI

FastAPI emite **OpenAPI 3.1**, y buena parte de los generadores de Dart todavía solo digieren 3.0. El
paso de CI `contracts/exportar_openapi.py` produce una versión degradada a 3.0.x que es la que consume
el generador del cliente Dart.

Descubrir esto en la Fase 3, con 40 endpoints, significa escribir los modelos a mano.
