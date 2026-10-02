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
