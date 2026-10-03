# dsd_app — la app del vendedor

Flutter. Opera **100% offline** en ruta: vende, cobra, registra mermas y no-drops
sin señal, y sincroniza cuando la haya. La lógica de dominio que comparte con el
servidor vive en `mobile/packages/dsd_core`.

## Correrla

```bash
make app          # flutter run tal cual
make app-demo     # con el botón de modo demo: siembra datos y entra sin servidor
make movil        # pub get + analyze + test
```

Contra la PC en la red local hace falta pasar la dirección a mano —`make app` no
la inyecta—, y con `http://` solo funciona en depuración (el
`usesCleartextTraffic` vive únicamente en el manifiesto de debug):

```bash
cd mobile/app && flutter run --dart-define=DSD_BASE_URL=http://192.168.1.50:8000
```

El modo demo y qué siembra:
[ENTORNO-WINDOWS §4.1](../../docs/ENTORNO-WINDOWS.md#41-evaluar-la-app-en-el-teléfono-sin-levantar-el-servidor).

## El APK de producción

```bash
make apk DSD_BASE_URL=https://api.tudominio.com
```

Hace falta el keystore de producción en `android/key.properties` (no se versiona;
copia `android/key.properties.example`). **Sin él, el build de release se
detiene a propósito**: firmar con la llave de depuración produce un APK que se
instala hoy y no se puede actualizar mañana, y la única salida sería desinstalar
—lo que borra la base local del vendedor con lo que no haya subido—.

El procedimiento completo, incluido respaldar la llave antes de firmar el primer
APK:
[ENTORNO-WINDOWS §4.2](../../docs/ENTORNO-WINDOWS.md#42-el-apk-de-producción-y-la-llave-que-no-se-puede-perder).

## Tres cosas que se fijan al COMPILAR y no en una pantalla de ajustes

| Define | Qué hace | Por qué no es configurable en la app |
|---|---|---|
| `DSD_BASE_URL` | La dirección del servidor | Un campo editable es el camino para que un equipo robado mande la cartera a donde quiera quien lo tenga. Y nadie lo cambia en operación normal |
| `DSD_DEMO` | El atajo que siembra datos y entra sin login | Un atajo que salta el login no debe poder existir en el teléfono de un vendedor. Doble cerrojo: el define **y** `!kReleaseMode` |
| `version: x.y.z+N` (pubspec) | El `versionCode` de Android | Lo lee Gradle al construir; súbelo en cada reparto |

Sin `DSD_BASE_URL`, un APK de release apunta a un marcador que no resuelve a
ninguna parte. Para que eso no se confunda con falta de señal, la app **no
muestra el login**: muestra una pantalla que dice que se compiló sin servidor
(`lib/src/pantallas/sin_servidor.dart`).

## Dónde está qué

```
lib/main.dart                 llave de SQLCipher y arranque
lib/src/app.dart              portal por rol: vendedor y gerencia son dos shells
lib/src/pantallas/            una carpeta por rol, más las pantallas de bloqueo
lib/src/estado/               providers de Riverpod
lib/src/datos/                transporte HTTP, base local, almacén seguro
test/                         pruebas de widget (sin emulador, en segundos)
```
