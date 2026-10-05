"""El despliegue, comprobado desde las pruebas.

`docs/ENTORNO-WINDOWS.md` §5 decía «`cp .env.example .env`, rellenar, y
`docker compose up -d`», y eso NO LEVANTABA en una máquina limpia por dos razones
que solo se descubrían en la oficina el día del despliegue:

  1. `docker-compose.yml` exige `DSD_CLAVE_API` con `:?` y `.env.example` no la
     mencionaba. Compose falla antes de arrancar nada, nombrando una variable que
     el archivo que te dicen copiar no tiene.
  2. Nada creaba los roles `dsd_api` ni `dsd_analitica`. `api` y `analitica` se
     conectan con ellos, así que los dos se quedaban reiniciándose con
     «role does not exist».

Las dos cosas están arregladas. Estas pruebas existen para que no vuelvan: una
variable obligatoria nueva, o un servicio nuevo que use un rol restringido, rompe
la prueba aquí y no en el despliegue.

Se lee el YAML como texto a propósito: `pyyaml` no es una dependencia declarada
de este proyecto —entra de rebote con `uvicorn[standard]`— y una prueba del
despliegue que dependa de un paquete que nadie pidió es otra cosa que se puede
romper sola.
"""

from __future__ import annotations

import pathlib
import re

_RAIZ = pathlib.Path(__file__).resolve().parents[2]
_COMPOSE = (_RAIZ / "docker-compose.yml").read_text(encoding="utf-8")
_EJEMPLO = (_RAIZ / ".env.example").read_text(encoding="utf-8")


def _claves_del_ejemplo() -> set[str]:
    claves = set()
    for linea in _EJEMPLO.splitlines():
        limpia = linea.strip()
        if not limpia or limpia.startswith("#") or "=" not in limpia:
            continue
        claves.add(limpia.split("=", 1)[0].strip())
    return claves


def _servicios() -> dict[str, str]:
    """El bloque de texto de cada servicio, partido por la sangría de dos espacios."""
    cuerpo = _COMPOSE.split("\nservices:\n", 1)[1]
    cuerpo = cuerpo.split("\nvolumes:\n", 1)[0]
    servicios: dict[str, list[str]] = {}
    actual: str | None = None
    for linea in cuerpo.splitlines():
        cabecera = re.match(r"^  ([a-z][a-z0-9_-]*):\s*$", linea)
        if cabecera:
            actual = cabecera.group(1)
            servicios[actual] = []
        elif actual:
            servicios[actual].append(linea)
    return {nombre: "\n".join(lineas) for nombre, lineas in servicios.items()}


def test_todo_lo_obligatorio_esta_en_env_example():
    """Si compose lo marca con `:?`, el archivo que se copia tiene que nombrarlo.

    Este es el defecto exacto que se encontró: `DSD_CLAVE_API` era obligatoria y
    `.env.example` no la tenía, así que seguir el procedimiento al pie de la letra
    fallaba en el primer comando.
    """
    obligatorias = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*):\?", _COMPOSE))
    assert obligatorias, "no se encontró ninguna variable obligatoria: ¿cambió la sintaxis?"
    faltan = obligatorias - _claves_del_ejemplo()
    assert not faltan, (
        f"docker-compose.yml las exige y .env.example no las menciona: {sorted(faltan)}"
    )


# Las que están en `.env.example` y compose NO interpola. Cada una con su razón,
# porque una variable que se rellena y no hace nada es una mentira al que despliega.
_NO_LAS_LEE_COMPOSE = {
    "DSD_DOMINIO_API": "las lee el Caddyfile",
    "DSD_DOMINIO_ANALITICA": "las lee el Caddyfile",
    # Compose lo fija en `produccion` a mano, sin `${}`, y es deliberado: es el
    # valor que hace que la API NO ARRANQUE sin el rol restringido. Si se
    # interpolara, un `desarrollo` en el `.env` del servidor de la oficina
    # apagaría esa exigencia en silencio. Vale para `make api` fuera de compose.
    "DSD_ENTORNO": "compose lo fija en 'produccion' a propósito",
}


def test_el_ejemplo_no_trae_variables_que_nadie_lee():
    """Al revés: una variable en el ejemplo que compose ya no usa es una mentira.

    Quien la rellena cree que hace algo. Las excepciones están arriba, cada una
    con el motivo escrito.
    """
    usadas = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)[:}]", _COMPOSE))
    sobran = _claves_del_ejemplo() - usadas - set(_NO_LAS_LEE_COMPOSE)
    assert not sobran, f".env.example las pide y nadie las lee: {sorted(sobran)}"


def test_los_dominios_del_ejemplo_los_usa_el_caddyfile():
    """La excepción de arriba, comprobada en vez de supuesta."""
    caddy = (_RAIZ / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    for clave in ("DSD_DOMINIO_API", "DSD_DOMINIO_ANALITICA"):
        assert clave in caddy, f"{clave} ya no aparece en el Caddyfile"


def test_quien_usa_un_rol_restringido_espera_a_que_exista():
    """El segundo defecto: `api` y `analitica` arrancaban antes de que el rol existiera.

    No se comprueba por nombre de servicio sino por el hecho: cualquier servicio
    que se conecte como `dsd_api` o `dsd_analitica` tiene que depender de `roles`.
    Así, un servicio nuevo que use uno de esos roles rompe esta prueba.
    """
    servicios = _servicios()
    assert "roles" in servicios, "desapareció el servicio que crea los roles"

    for nombre, cuerpo in servicios.items():
        if nombre == "roles":
            continue
        usa_rol = "dsd_api:" in cuerpo or "dsd_analitica:" in cuerpo
        if not usa_rol:
            continue
        assert "roles:" in cuerpo, (
            f"«{nombre}» se conecta con un rol restringido y no espera al servicio "
            "«roles»: va a arrancar contra un rol que todavía no existe"
        )


def test_los_roles_se_crean_despues_de_las_migraciones():
    """Sus GRANT son sobre tablas que las migraciones crean. Antes, no habría qué dar."""
    roles = _servicios()["roles"]
    assert "migraciones:" in roles
    assert "service_completed_successfully" in roles


def test_el_servicio_de_roles_aplica_los_archivos_de_ops_tal_cual():
    """Y no una copia suya.

    Las pruebas de RLS aplican estos MISMOS archivos (ADR §35). Si el despliegue
    usara una copia, lo que se prueba y lo que se despliega podrían separarse sin
    que nada avisara.
    """
    roles = _servicios()["roles"]
    assert "./server/db/ops:/ops:ro" in roles
    for archivo in ("rol_api.sql", "rol_analitico.sql"):
        assert f"/ops/{archivo}" in roles
        assert (_RAIZ / "server" / "db" / "ops" / archivo).is_file()


def test_el_script_de_roles_llega_como_un_solo_argumento():
    """Compose parte una cadena por espacios.

    Con `entrypoint: ["/bin/bash", "-c"]` y `command` como cadena, el contenedor
    recibiría `bash -c set -e` y SALDRÍA CON CERO sin ejecutar un solo `psql`.
    `api` arrancaría contra un rol inexistente y el servicio que debía evitarlo
    habría reportado éxito. Pasó al escribirlo.
    """
    roles = _servicios()["roles"]
    assert re.search(r"command:\n\s+- \|", roles), (
        "`command` del servicio «roles» tiene que ser una lista de un elemento "
        "(`- |`), no una cadena"
    )


def test_el_servicio_de_roles_aborta_al_primer_error():
    """Sin `ON_ERROR_STOP` psql sigue y sale con cero: los roles quedarían a medias
    y `api` arrancaría igual, que es justo lo que este servicio viene a evitar."""
    roles = _servicios()["roles"]
    assert roles.count("ON_ERROR_STOP=1") == 2
    assert "set -e" in roles


def test_el_rol_de_la_api_no_salta_las_politicas():
    """La propiedad de la que depende todo lo demás, leída del archivo que se aplica.

    Se miran solo las sentencias, no los comentarios: el archivo explica BYPASSRLS
    en prosa, y una prueba que leyera la prosa diría lo contrario de la verdad.
    """
    sql = (_RAIZ / "server" / "db" / "ops" / "rol_api.sql").read_text(encoding="utf-8")
    sentencias = "\n".join(
        linea for linea in sql.splitlines() if not linea.lstrip().startswith("--")
    )
    assert "NOBYPASSRLS" in sentencias
    assert "BYPASSRLS" not in sentencias.replace("NOBYPASSRLS", "")


# Las claves que acaban DENTRO de una URL de conexión en docker-compose.yml.
_EN_UNA_URL = ("DSD_DB_PASSWORD", "DSD_CLAVE_API", "DSD_CLAVE_ANALITICA")


def test_las_claves_que_van_en_una_url_se_generan_en_hexadecimal():
    """`openssl rand -base64` produce `/` y `+`, y una de las dos rutas se rompe.

    libpq —el que usa el laboratorio analítico— parte mal una URI cuya contraseña
    trae `/`: intenta resolver el NOMBRE DEL ROL como si fuera el servidor y falla
    con «failed to resolve host 'dsd_analitica'», que no apunta a la causa.
    SQLAlchemy sí la tolera, así que la API arranca bien y solo el laboratorio
    queda roto — lo peor para diagnosticar. En hexadecimal no pasa.

    `.env.example` decía `-base64 24` para las tres. Esta prueba existe para que
    no vuelva: si alguien sugiere `-base64` para una clave que va en una URL, aquí
    se rompe.
    """
    for linea in _EJEMPLO.splitlines():
        clave = linea.split("=", 1)[0].strip()
        if clave not in _EN_UNA_URL or "openssl" not in linea:
            continue
        assert "-hex" in linea, (
            f"{clave} va dentro de una URL de conexión: su clave se genera con "
            f"`openssl rand -hex`, no con base64 — {linea.strip()}"
        )


def test_las_tres_de_postgresql_siguen_yendo_en_una_url():
    """La razón de la prueba de arriba, comprobada en vez de supuesta.

    Si alguna deja de incrustarse en una URL, la exigencia del hexadecimal pierde
    su motivo y esta prueba obliga a releerla.
    """
    for clave in _EN_UNA_URL:
        assert re.search(rf"://[^\s]*\$\{{{clave}[:}}]", _COMPOSE), (
            f"{clave} ya no aparece dentro de una URL en docker-compose.yml: "
            "revisa si sigue teniendo sentido exigirle hexadecimal"
        )


# ---------------------------------------------------------------------------
# El otro despliegue: el APK que se instala en el teléfono del vendedor
# ---------------------------------------------------------------------------
# `mobile/app/android/app/build.gradle.kts` traía la plantilla de Flutter, que
# firma el build de release con la LLAVE DE DEPURACIÓN. Eso no falla: produce un
# APK instalable. El daño aparece meses después, porque Android solo acepta
# actualizar una app instalada si la firma coincide, y la llave de depuración es
# distinta en cada máquina. Cuando no coincida habrá que desinstalar — y
# desinstalar borra la base local del vendedor con lo que no haya subido.
#
# Se lee como texto, igual que el compose: aquí no hay SDK de Android, y un
# `flutter build apk` no se puede correr en CI por el peso del SDK. Lo que estas
# pruebas pueden afirmar es la FORMA del camino de build, y eso es justo lo que
# se rompería sin darse cuenta.

_MOVIL = _RAIZ / "mobile" / "app"
_GRADLE = (_MOVIL / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
_MAKEFILE = (_RAIZ / "Makefile").read_text(encoding="utf-8")
_IGNORADOS = (_RAIZ / ".gitignore").read_text(encoding="utf-8")


def test_el_release_no_se_firma_con_la_llave_de_depuracion():
    """La línea que traía la plantilla, y que no debe volver.

    `signingConfig = signingConfigs.getByName("debug")` dentro de `release` es
    exactamente el defecto: compila, instala, y rompe la actualización el día que
    la llave no coincida.
    """
    sentencias = "\n".join(
        linea for linea in _GRADLE.splitlines() if not linea.lstrip().startswith("//")
    )
    assert 'getByName("debug")' not in sentencias, (
        "el build de release volvió a firmarse con la llave de depuración"
    )
    assert 'create("release")' in sentencias, "desapareció la firma de producción"


def test_el_build_de_release_se_detiene_sin_keystore():
    """Y se detiene, no avisa: un APK de producción mal firmado no debe existir.

    La comprobación va en `taskGraph.whenReady` a propósito — al configurar
    rompería `flutter run` en cualquier máquina sin keystore, que es la mayoría.
    """
    assert "gradle.taskGraph.whenReady" in _GRADLE
    assert "GradleException" in _GRADLE
    assert 'it.name.contains("Release")' in _GRADLE


def test_la_llave_no_se_puede_versionar():
    """Lo único de este repositorio que no se puede regenerar no vive en él."""
    for patron in ("mobile/app/android/key.properties", "*.jks", "*.keystore"):
        assert patron in _IGNORADOS, f".gitignore ya no cubre {patron}"

    # Y el ejemplo sí se versiona: es el que se copia.
    assert (_MOVIL / "android" / "key.properties.example").is_file()


def test_el_ejemplo_trae_los_cuatro_campos_que_gradle_lee():
    """Si Gradle pide un campo que el ejemplo no menciona, quien lo copie falla
    sin saber qué le falta — y al revés, un campo de más es ruido que alguien va
    a rellenar creyendo que sirve."""
    ejemplo = (_MOVIL / "android" / "key.properties.example").read_text(
        encoding="utf-8"
    )
    en_el_ejemplo = {
        linea.split("=", 1)[0].strip()
        for linea in ejemplo.splitlines()
        if "=" in linea and not linea.strip().startswith("#")
    }
    pedidos = set(re.findall(r'clave\("(\w+)"\)', _GRADLE)) | set(
        re.findall(r'"(storeFile|storePassword|keyAlias|keyPassword)"', _GRADLE)
    )
    assert pedidos == en_el_ejemplo, (
        f"Gradle lee {sorted(pedidos)} y el ejemplo trae {sorted(en_el_ejemplo)}"
    )


def test_make_apk_exige_el_servidor_y_construye_en_release():
    """`DSD_BASE_URL` se fija al compilar, no en una pantalla de ajustes.

    Sin ella el APK apunta a un marcador que no resuelve: se instala, abre, y el
    login falla con un error de red — «no hay internet», y nadie mira el binario.
    """
    receta = _MAKEFILE.split("\napk:", 1)[1].split("\n\n", 1)[0]
    assert "--release" in receta
    assert "--dart-define=DSD_BASE_URL" in receta
    assert "$(DSD_BASE_URL)" in receta
    # Las tres negativas, cada una por LA COMPROBACIÓN y no por el texto que la
    # explica. La primera versión de esto buscaba `"https://"` a secas y pasaba
    # con el guardia roto, porque la cadena también está en el mensaje de error:
    # una prueba que lee la prosa en vez del código no prueba nada.
    assert 'test -n "$(DSD_BASE_URL)"' in receta, "dejó de exigir la dirección"
    assert "in https://*)" in receta, "dejó de exigir TLS"
    assert "test -f mobile/app/android/key.properties" in receta, (
        "dejó de exigir el keystore"
    )


def test_el_apk_terminado_se_revisa():
    """El build no puede comprobar con qué llave quedó firmado: eso se lee del
    APK ya hecho, y es la última oportunidad de verlo antes de repartirlo."""
    receta = _MAKEFILE.split("\napk:", 1)[1].split("\n\n", 1)[0]
    assert "scripts/revisar_apk.sh" in receta
    revisor = (_RAIZ / "scripts" / "revisar_apk.sh").read_text(encoding="utf-8")
    assert "CN=Android Debug" in revisor, (
        "el revisor dejó de detectar la llave de depuración"
    )
    assert "apksigner" in revisor


def test_el_apk_lleva_un_versioncode_explicito():
    """Sin el `+N`, el número no sube solo.

    Android rechaza instalar encima un versionCode MENOR que el instalado; con el
    mismo sí reinstala, y entonces dos APK distintos son indistinguibles con el
    teléfono en la mano.
    """
    pubspec = (_MOVIL / "pubspec.yaml").read_text(encoding="utf-8")
    version = re.search(r"^version:\s*(\S+)", pubspec, re.M)
    assert version, "pubspec.yaml sin `version:`"
    assert "+" in version.group(1), (
        f"pubspec.yaml dice «version: {version.group(1)}», sin «+N»: "
        "el versionCode no sube solo"
    )


# ---------------------------------------------------------------------------
# Los candados de dependencias
# ---------------------------------------------------------------------------
# `docs/ARQUITECTURA.md` decía «uv con lockfile versionado» y el Dockerfile
# decía «instalación determinista a partir del lockfile». No había lockfile:
# `make instalar`, el CI y el Dockerfile resolvían contra PyPI en cada corrida.
#
# El quinto caso de la misma huella, y el más barato de arreglar — un comando—,
# lo que lo hace peor: estuvo escrito como hecho durante nueve fases.

def _sin_comentarios(texto: str, marca: str = "#") -> str:
    """Solo las líneas que el shell, Docker o make ejecutan de verdad.

    Las afirmaciones NEGATIVAS —«esto ya no aparece»— tienen que leerse contra
    esto y no contra el archivo entero: los comentarios de este repositorio citan
    el comando viejo para explicar por qué se cambió, y una prueba que lea la
    prosa pasa con el código roto. Ya pasó una vez con la guarda de `https://`
    en `make apk`.
    """
    return "\n".join(
        linea
        for linea in texto.splitlines()
        if not linea.lstrip().startswith(marca)
    )


def _comandos(texto: str, marca: str = "#") -> str:
    """Como [_sin_comentarios], pero además une las continuaciones de línea.

    Hace falta porque un `RUN` de Docker o una receta de make se parten con `\\`,
    y una bandera puede caer en la segunda línea. Mirar solo la línea que trae
    `uv sync` deja pasar un `--extra dev` escrito debajo: pasó al verificar esto
    por mutación, y es por lo que el helper existe.
    """
    return _sin_comentarios(texto.replace("\\\n", " "), marca)


_LOCK = _RAIZ / "server" / "uv.lock"
_REQ_LAB = _RAIZ / "analytics" / "requirements.txt"
_DOCKER_API = (_RAIZ / "server" / "Dockerfile").read_text(encoding="utf-8")
_DOCKER_LAB = (_RAIZ / "analytics" / "Dockerfile").read_text(encoding="utf-8")
_CI = (_RAIZ / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def test_el_candado_del_servidor_existe_y_esta_versionado():
    """Lo que tres documentos daban por hecho."""
    assert _LOCK.is_file(), "server/uv.lock no existe"
    contenido = _LOCK.read_text(encoding="utf-8")
    assert "[[package]]" in contenido
    # Con hashes: sin ellos el candado fija la versión pero no el contenido, y
    # un paquete re-subido con el mismo número pasaría igual.
    assert "hash = " in contenido, "el candado no trae hashes"


def test_el_candado_no_esta_ignorado_por_git():
    """Un candado que no se versiona no es un candado: cada máquina tendría el
    suyo, que es exactamente el estado del que se viene."""
    for linea in _IGNORADOS.splitlines():
        limpia = linea.strip()
        assert limpia not in ("uv.lock", "server/uv.lock", "*.lock"), (
            f".gitignore excluye el candado: «{limpia}»"
        )


def test_las_tres_vias_de_instalacion_usan_el_candado():
    """`make instalar`, el CI y el Dockerfile, o no sirve de nada.

    Si una sola de las tres resuelve por su cuenta, es la que va a diferir — y
    será la de producción o la del CI, nunca la que alguien mira.
    """
    receta = _sin_comentarios(_MAKEFILE.split("\ninstalar:", 1)[1].split("\n\n", 1)[0])
    assert "uv sync --locked" in receta, "make instalar dejó de usar el candado"
    assert "uv pip install" not in receta

    instalacion_ci = _sin_comentarios(
        _CI.split("name: Instalar dependencias", 1)[1].split("- name:", 1)[0]
    )
    assert "uv sync --locked" in instalacion_ci, "el CI dejó de usar el candado"
    assert "uv pip install" not in instalacion_ci

    docker = _comandos(_DOCKER_API)
    assert "uv sync --locked" in docker, "el Dockerfile dejó de usar el candado"
    assert "uv pip install" not in docker


def test_la_imagen_de_la_api_no_lleva_las_dependencias_de_desarrollo():
    """Sin `--extra`, `uv sync` instala solo lo base.

    Un `--extra dev` aquí metería pytest y ruff en la imagen de producción, y un
    `--extra analitica` le sumaría streamlit y pandas a un contenedor que no
    los usa.
    """
    sincronizacion = [
        linea for linea in _comandos(_DOCKER_API).splitlines() if "uv sync" in linea
    ]
    assert sincronizacion, "desapareció el `uv sync` del Dockerfile"
    assert not any("--extra" in linea for linea in sincronizacion), (
        "la imagen de la API está instalando un extra"
    )


def test_la_imagen_de_la_api_encuentra_sus_binarios():
    """`uv sync` deja el venv en /srv/.venv, y el CMD llama a `uvicorn` a secas.

    Sin el PATH, `uvicorn` y el `alembic` del servicio de migraciones se
    buscarían en el Python del sistema, donde ya no están. Falla al arrancar el
    contenedor, no al construirlo.
    """
    assert "/srv/.venv/bin:$PATH" in _DOCKER_API


def test_uv_esta_pinneado_en_el_dockerfile():
    """El instalador también es una dependencia.

    Con `uv:latest`, dos builds del mismo commit pueden usar resolvedores
    distintos — y el que decide qué se instala es el resolvedor.
    """
    docker = _sin_comentarios(_DOCKER_API)
    assert "astral-sh/uv:latest" not in docker, "uv volvió a `:latest`"
    assert re.search(r"astral-sh/uv:\d+\.\d+\.\d+", docker), (
        "uv no está pinneado a una versión exacta"
    )


def test_el_laboratorio_instala_versiones_exactas_con_hash():
    """El otro contenedor tenía tres rangos `>=`, y es el mismo defecto.

    `requirements.txt` se compila desde `requirements.in` (`make candado`), y el
    Dockerfile lo instala con `--require-hashes`: una línea sin hash detiene el
    build en vez de instalarse sin verificar.
    """
    assert (_RAIZ / "analytics" / "requirements.in").is_file()
    compilado = _REQ_LAB.read_text(encoding="utf-8")
    assert "--hash=sha256:" in compilado
    # Sin comentarios: la cabecera que genera uv cita el comando, que lleva el
    # nombre del `.in` pero no rangos — aun así se lee solo lo instalable.
    assert ">=" not in _sin_comentarios(compilado), (
        "analytics/requirements.txt trae un rango: ¿se editó a mano en vez de compilarse?"
    )
    assert "--require-hashes" in _sin_comentarios(_DOCKER_LAB)


def test_lo_declarado_en_el_laboratorio_esta_fijado_en_su_compilado():
    """La consistencia entre `requirements.in` y `requirements.txt`, SIN RED.

    El fallo real que esto atrapa: agregar una dependencia al `.in` y olvidar
    `make candado`. El contenedor del laboratorio se construiría sin ella y el
    síntoma aparecería al desplegar, no aquí.

    Se comprueba por estructura y no recompilando. Recompilar y comparar fue el
    primer diseño y estaba mal: `uv pip compile` hacia un archivo nuevo no ve los
    pines viejos, así que resuelve a lo más reciente — la prueba se habría puesto
    roja el día que streamlit publicara una versión, en un commit que no tocó
    nada. Que es exactamente el problema que el candado viene a quitar.
    """
    declarado = (_RAIZ / "analytics" / "requirements.in").read_text(encoding="utf-8")
    compilado = _REQ_LAB.read_text(encoding="utf-8")

    # La cabecera que genera uv dice de dónde salió. Si apunta a otra entrada,
    # este archivo no es el compilado de ESTE `.in`.
    assert "requirements.in" in compilado.split("\n", 3)[1], (
        "la cabecera del compilado no nombra requirements.in"
    )

    for linea in _sin_comentarios(declarado).splitlines():
        linea = linea.strip()
        if not linea:
            continue
        # `psycopg[binary]>=3.2` → nombre `psycopg`, mínimo `3.2`
        coincide = re.match(r"^([A-Za-z0-9._-]+)(?:\[[^\]]+\])?>=(\S+)$", linea)
        assert coincide, f"no supe leer «{linea}» de requirements.in"
        nombre, minimo = coincide.group(1), coincide.group(2)

        fijado = re.search(rf"^{re.escape(nombre)}==(\S+?) ", compilado, re.M)
        assert fijado, (
            f"«{nombre}» está en requirements.in y no está fijado en "
            "requirements.txt: falta correr `make candado`"
        )
        # Y el pin satisface el mínimo declarado. Comparación por tuplas de
        # enteros, que alcanza para estos tres y no inventa un parser de PEP 440.
        def partes(v: str) -> tuple[int, ...]:
            return tuple(int(x) for x in re.findall(r"\d+", v))

        assert partes(fijado.group(1)) >= partes(minimo), (
            f"{nombre}: requirements.in pide >={minimo} y el compilado fija "
            f"{fijado.group(1)}"
        )


def test_el_laboratorio_corre_lo_mismo_en_local_que_en_su_contenedor():
    """Los dos candados comparten cuatro paquetes, y tienen que coincidir.

    En local el laboratorio corre desde el venv del servidor (extra `analitica`);
    en producción, desde su propia imagen. Si las versiones se separan, aparece
    el «en mi máquina funciona» más caro de diagnosticar: la misma consulta
    devolviendo algo distinto según dónde corra, con pandas de por medio.
    """
    compilado = _REQ_LAB.read_text(encoding="utf-8")
    candado = _LOCK.read_text(encoding="utf-8")

    for paquete in ("streamlit", "pandas", "psycopg", "numpy"):
        en_el_lab = re.search(rf"^{paquete}==(\S+?) ", compilado, re.M)
        assert en_el_lab, f"{paquete} ya no está en el candado del laboratorio"
        en_el_servidor = re.search(
            rf'name = "{paquete}"\nversion = "([^"]+)"', candado
        )
        assert en_el_servidor, f"{paquete} ya no está en server/uv.lock"
        assert en_el_lab.group(1) == en_el_servidor.group(1), (
            f"{paquete}: el laboratorio usa {en_el_lab.group(1)} en su contenedor "
            f"y {en_el_servidor.group(1)} en el venv del servidor"
        )


def test_el_revisor_del_servidor_esta_cableado():
    """El guion existe, el `make` lo llama, y revisa lo que el documento promete.

    Lo que el servidor tiene configurado no se puede comprobar desde el código
    —vive en la máquina—, así que esto cubre lo que sí puede romperse sin que
    nadie lo note: que el objetivo del Makefile siga apuntando al guion, y que el
    guion siga buscando cada cosa que `DESPLIEGUE.md` dice que busca.
    """
    guion = _RAIZ / "scripts" / "revisar_servidor.sh"
    assert guion.is_file()

    receta = _sin_comentarios(
        _MAKEFILE.split("\nservidor-revisar:", 1)[1].split("\n\n", 1)[0]
    )
    assert "scripts/revisar_servidor.sh" in receta

    # Contra las líneas que SE EJECUTAN, no contra el archivo: los comentarios y
    # los mensajes de ayuda del guion nombran `ufw`, `sshd_config` y lo demás,
    # así que leer el archivo entero deja pasar un guardia desconectado. Pasó al
    # verificar esto por mutación: cambiar `command -v ufw` por otra cosa no
    # ponía roja ninguna prueba.
    ejecutable = _sin_comentarios(guion.read_text(encoding="utf-8"))
    comprobaciones = {
        'TYPE="crypt"': "el volumen cifrado",
        "swapon --noheadings": "el swap",
        # La RESTA, no el nombre de la variable: `RECUPERABLES=1` dejaría la
        # cuenta siempre en verde y el nombre seguiría ahí. Es el número de
        # frases que una persona puede teclear, y cero significa que el disco se
        # vuelve ilegible el día que se resetee el TPM.
        "RANURAS - AUTO": "que quede una frase de recuperación del disco",
        "command -v ufw": "el cortafuegos",
        "command -v timedatectl": "la zona horaria, que decide qué día es «hoy»",
        "valor_ssh PasswordAuthentication": "que SSH no acepte contraseñas",
        "valor_ssh PermitRootLogin": "que root no entre por SSH",
        "stat -c": "los permisos del .env",
    }
    for aguja, que_es in comprobaciones.items():
        assert aguja in ejecutable, f"el revisor dejó de revisar {que_es}"


def test_el_revisor_vigila_los_puertos_publicados():
    """La trampa de Docker + ufw, que es la que deja bases de datos públicas.

    Docker escribe sus propias reglas de iptables por delante de las de ufw, así
    que un `ports:` en compose queda abierto a internet aunque `ufw status` diga
    que ese puerto está cerrado. El guion lee el compose —la fuente de la
    verdad— y no solo lo que está corriendo, para dar la misma respuesta con el
    stack arriba o abajo.
    """
    ejecutable = _sin_comentarios(
        (_RAIZ / "scripts" / "revisar_servidor.sh").read_text(encoding="utf-8")
    )
    assert "docker-compose.yml" in ejecutable, (
        "el revisor dejó de leer qué publica el compose"
    )
    assert "80|443" in ejecutable, (
        "desapareció la lista de los únicos puertos permitidos"
    )

    # Y que el compose siga publicando solo esos dos: si alguien agrega un
    # `ports:` para depurar y lo deja, esto se rompe aquí y no en producción.
    publicados = re.findall(r'^\s+-\s+"(\d+):\d+"', _COMPOSE, re.M)
    assert set(publicados) <= {"80", "443"}, (
        f"docker-compose.yml publica {sorted(set(publicados))}: solo 80 y 443 "
        "deben salir a internet, el resto entra por Caddy"
    )


def test_el_tunel_de_cloudflare_es_opcional():
    """En un VPS no hace falta, y antes impedía desplegar sin él.

    `TUNNEL_TOKEN` estaba marcado `:?`, así que compose fallaba al INTERPOLAR
    —antes de arrancar nada— pidiendo una variable que en un VPS no tiene
    sentido. Ahora el servicio va detrás de un perfil y el token es opcional.
    """
    tunel = _servicios()["tunel"]
    assert 'profiles: ["tunel"]' in tunel, (
        "el túnel volvió a ser un servicio que se levanta solo"
    )
    assert "DSD_TUNNEL_TOKEN:?" not in _COMPOSE, (
        "el token del túnel volvió a ser obligatorio: eso rompe el despliegue en un VPS"
    )


def test_el_laboratorio_tiene_techo_de_memoria():
    """En un VPS chico, sin techo el OOM killer se lleva a PostgreSQL.

    Streamlit con pandas, pyarrow, numpy y altair residentes es el proceso más
    grande del stack. Cuando la memoria se agota, el kernel mata al más grande
    por RSS —que puede ser PostgreSQL— así que una consulta del laboratorio
    tiraría la base a media venta. Con el techo, lo que muere es el laboratorio.
    """
    analitica = _servicios()["analitica"]
    assert re.search(r"^\s+mem_limit:\s*\S+", analitica, re.M), (
        "el laboratorio analítico se quedó sin techo de memoria: en un VPS de "
        "2 GB eso pone a PostgreSQL a tiro del OOM killer"
    )


def test_el_envoltorio_del_servidor_no_apunta_a_localhost():
    """En el servidor la base NO escucha en 127.0.0.1, y ese es el punto.

    `respaldar.sh`, `simulacro.sh` y `piloto_listo.sh` traen por omisión una URL
    a `127.0.0.1:5432`, que es la de desarrollo. En el VPS PostgreSQL vive en un
    contenedor sin puerto publicado, así que esa URL falla con «connection
    refused» —que suena a base caída y no lo es—. El envoltorio tiene que armar
    las URLs contra el NOMBRE DEL SERVICIO de compose.
    """
    guion = (_RAIZ / "scripts" / "en_el_servidor.sh").read_text(encoding="utf-8")
    comandos = _sin_comentarios(guion)

    assert "@postgres:5432" in comandos, (
        "el envoltorio dejó de apuntar al servicio `postgres` de compose"
    )
    assert "127.0.0.1" not in comandos and "localhost" not in comandos, (
        "el envoltorio apunta a localhost: en el servidor ahí no hay nada "
        "escuchando, porque el compose no publica el puerto de PostgreSQL"
    )
    # El respaldo tiene que salir del contenedor al host, o desaparece con él.
    assert ":/respaldos" in comandos, (
        "el envoltorio ya no monta la carpeta de respaldos del host: un respaldo "
        "escrito dentro del contenedor se va con el contenedor"
    )


# ---------------------------------------------------------------------------
# El simulacro de respaldo
# ---------------------------------------------------------------------------
# Nada ejercitaba estos dos archivos: ni una prueba ni CI. El fallo de abajo
# llegó hasta un servidor de producción y ahí se vio.


def test_la_semilla_de_humo_no_usa_codigos_de_produccion():
    """`simulacro.sh` corre esta prueba sobre un respaldo REAL restaurado.

    `sucursales.codigo`, `usuarios.codigo`, `almacenes.codigo` y `productos.sku`
    son únicos. Una semilla con códigos verosímiles choca con los de verdad, y
    con ON_ERROR_STOP psql sale distinto de cero: el simulacro concluye «el
    esquema restaurado está incompleto» y declara inservible un respaldo que
    sirve. 'MATRIZ' en particular la crea `crear-usuario`, así que el choque
    ocurre en toda instalación en cuanto alguien puede entrar al panel.
    """
    smoke = (_RAIZ / "server" / "db" / "tests" / "smoke_invariantes.sql").read_text(
        encoding="utf-8"
    )
    # `marca="--"`: en SQL el comentario es `--`, no `#`. Con la marca por
    # omisión esta prueba leía los comentarios del propio archivo —que CITAN
    # 'MATRIZ' para explicar por qué ya no se usa— y fallaba sobre la prosa.
    inserciones = _sin_comentarios(smoke, marca="--")

    for codigo in ("'MATRIZ'", "'VEND01'", "'BODEGA_PRINCIPAL'", "'CAMION_01'", "'SKU-001'"):
        assert codigo not in inserciones, (
            f"la semilla de smoke_invariantes.sql volvió a usar {codigo}: eso choca "
            "con el dato real y hace que el simulacro declare malo un respaldo bueno"
        )
    assert "ZZ-HUMO-" in inserciones, (
        "la semilla perdió el prefijo que la mantiene fuera del camino de los "
        "datos reales"
    )


def test_el_simulacro_no_se_conforma_con_el_codigo_de_salida():
    """Tres invariantes reportan con texto y dos con WARNING: psql sale CERO.

    Si un disparador dejara de bloquear el UPDATE al libro mayor, el archivo
    imprimiría 'FALLA · se permitió editar el libro mayor' y psql saldría con
    cero. Sin el grep, el simulacro diría «las invariantes se cumplen» sobre una
    base en la que el libro mayor es editable — el fallo exacto que busca.
    """
    guion = _sin_comentarios((_RAIZ / "scripts" / "simulacro.sh").read_text(encoding="utf-8"))
    assert "grep -q 'FALLA'" in guion, (
        "simulacro.sh volvió a confiar solo en el código de salida de psql: un "
        "FALLA impreso por una invariante pasaría por verde"
    )
