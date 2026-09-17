# Entorno de desarrollo — Windows 11

Guía para dejar la máquina lista y para seguir el avance del proyecto a lo largo de las fases.

---

## 0. La decisión que ahorra semanas: WSL2

Todo este stack —Makefile, rutas de Unix, PostgreSQL, Docker— asume Linux. En PowerShell nativo vas a
pelear con detalles que no aportan nada al negocio: rutas con `\`, permisos, saltos de línea, `make`
que no existe.

**Trabaja dentro de WSL2 con Ubuntu.** Media hora de configuración. Windows sigue siendo tu escritorio;
Ubuntu es donde vive el código.

Abre **PowerShell como Administrador** y corre:

```powershell
wsl --install -d Ubuntu-24.04
```

Reinicia. Al volver, Ubuntu te pedirá un usuario y contraseña (son de Linux, no de Windows; anótalos).

Comprueba:

```powershell
wsl -l -v          # debe decir Ubuntu-24.04, VERSION 2
```

> **Regla que no se rompe:** el código vive en el sistema de archivos de **Linux**
> (`/home/tu-usuario/...`), nunca en `/mnt/c/...`. Trabajar desde `/mnt/c` hace que todo vaya entre 5 y
> 10 veces más lento y rompe permisos de Git.

---

## 1. Qué instalar

### En Windows

| Programa | Para qué | Cómo |
|---|---|---|
| **Docker Desktop** | Corre PostgreSQL sin instalarlo nativo | `winget install Docker.DockerDesktop` |
| **VS Code** | Editor | `winget install Microsoft.VisualStudioCode` |
| **Git para Windows** | Opcional; dentro de WSL usarás el de Ubuntu | `winget install Git.Git` |

Después de instalar Docker Desktop: ábrelo → **Settings → Resources → WSL Integration** → activa
**Ubuntu-24.04**. Sin ese paso, `docker` no existe dentro de Ubuntu.

**Extensiones de VS Code** (búscalas por nombre e instálalas):

- **WSL** — indispensable, es la que abre el editor dentro de Ubuntu
- **Python** y **Ruff**
- **Docker**
- **Flutter** (para la Fase 3)

### Dentro de Ubuntu (WSL)

Abre la terminal de Ubuntu desde el menú Inicio y corre:

```bash
sudo apt update && sudo apt install -y git make curl postgresql-client
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
```

**No instales Python ni PostgreSQL.** `uv` baja Python 3.12 solo; PostgreSQL corre en Docker.
`postgresql-client` es solo `psql`, para inspeccionar la base a mano.

---

## 2. Arranque

```bash
git clone https://github.com/BryanMTCode/Distribution.git
cd Distribution
git checkout claude/exciting-hamilton-asnv8o

make instalar     # uv baja Python 3.12 y las dependencias
make db           # PostgreSQL + PostGIS en Docker
make migrar       # aplica las 8 migraciones
make pruebas      # deben pasar 148
```

Si ves `148 passed`, tu entorno está bien. Si no, el error casi siempre es uno de estos tres:

| Síntoma | Causa | Arreglo |
|---|---|---|
| `docker: command not found` | Falta la integración WSL | Docker Desktop → Settings → Resources → WSL Integration |
| `connection refused` en 5432 | La base no está arriba | `make db` |
| `port is already allocated` | Otro PostgreSQL ocupa el 5432 | `make db-parar` y vuelve a `make db` |

Para abrir el proyecto en el editor, **desde la terminal de Ubuntu**:

```bash
code .
```

Eso abre VS Code conectado a WSL. Abajo a la izquierda debe decir `WSL: Ubuntu-24.04`.

### Ver la API funcionando

```bash
make api
```

Abre <http://localhost:8000/docs> en tu navegador de Windows: es la documentación interactiva de la API,
generada sola. Puedes probar el login desde ahí.

### Comandos del día a día

```bash
make ayuda        # lista todo lo disponible
make db           # levanta la base
make pruebas      # la suite completa
make lint         # revisa estilo
make api          # API con recarga automática
make contratos    # regenera vectores y OpenAPI — REVISA EL DIFF
make db-parar     # apaga y borra el contenedor de la base
```

---

## 3. Cómo monitorear el avance

### En GitHub (lo más importante)

| Qué | Dónde |
|---|---|
| **Qué se hizo y por qué** | [Commits de la rama](https://github.com/BryanMTCode/Distribution/commits/claude/exciting-hamilton-asnv8o) — cada mensaje explica la decisión, no solo el cambio |
| **Si todo sigue en verde** | [Pestaña Actions](https://github.com/BryanMTCode/Distribution/actions) — corre en cada `push` |
| **Ver un cambio a detalle** | Clic en cualquier commit: muestra el diff línea por línea |

**Lee la pestaña Actions como semáforo.** Verde = las 148 pruebas pasan, el estilo está limpio y los
contratos entre Dart y Python siguen coincidiendo. Rojo = algo se rompió, y el registro dice qué.

### En tu máquina

```bash
git pull                    # trae lo último
make pruebas                # confirma que corre en tu equipo, no solo en el mío
```

Que pase en CI y falle en tu máquina es información valiosa: casi siempre significa que algo depende
del entorno y no debería.

### En los documentos

| Archivo | Qué te dice |
|---|---|
| [`README.md`](../README.md) | Tabla de estado: qué pieza está lista y cuál no |
| [`docs/ARQUITECTURA.md`](ARQUITECTURA.md) | El plan de 11 fases con duraciones |
| [`docs/adr/`](adr/) | Cada decisión técnica grande, con las alternativas descartadas y por qué |

### Una señal de alarma que debes saber leer

Si en CI falla el paso **"Los vectores canónicos están al día"**, significa que alguien cambió la forma
en que se serializan los datos sin actualizar el contrato compartido. Eso, sin atrapar, se convierte
meses después en miles de tickets en cuarentena. Que falle el CI es exactamente lo que quieres.

---

## 4. Para la Fase 3 (todavía no)

Cuando llegue la app móvil:

```bash
sudo snap install flutter --classic    # dentro de WSL
```

Y en Windows: **Android Studio** (`winget install Google.AndroidStudio`), solo por el SDK de Android.

> **Aviso:** WSL2 no tiene acceso directo a USB ni a Bluetooth. Para probar en un teléfono real
> —que es lo único que vale para Bluetooth y GPS— vas a necesitar `usbipd-win`, o instalar Flutter
> directamente en Windows para la parte móvil. Se resuelve al llegar a la Fase 3; no lo montes ahora.

También necesitarás, físicamente:

- Un **teléfono Android de gama baja**, el mismo modelo que usarán tus vendedores.
- Una **impresora térmica Bluetooth de 58 mm**, la que vayas a comprar en volumen.

El emulador no sirve: no tiene Bluetooth ni GPS de verdad.

---

## 5. Para el servidor de la oficina (Fase 3, al salir el piloto)

- Mini PC (Intel N100 o similar, 16 GB RAM, SSD NVMe) con Ubuntu Server LTS
- **UPS / no-break** — no es opcional: un apagón con rutas sincronizando corrompe la base
- Cuenta gratuita de Cloudflare para el túnel
- Cuenta de Backblaze B2 o S3 para los respaldos

Ahí el despliegue es `cp .env.example .env`, rellenar, y `docker compose up -d`.
