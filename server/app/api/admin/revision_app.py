"""La revisión de la app y del dashboard en el panel (ADR 0002 §112).

«Agrega al dashboard un checklist de todo lo que debo checar en la app para ver
que las funcionalidades están bien.» Y después: dashboard y app juntos.

Una página por versión de la app, en dos partes —en el teléfono y en el
dashboard—: los puntos de `app/infra/revision_app.py`, cada uno con cómo
probarlo y qué debe pasar, y las marcas de esa versión —bien o falla con su
nota—. Sin JavaScript, como todo el panel: cada marca es un formulario que
vuelve al mismo punto.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette import status

from app.api.admin.comun import SesionDep, render, texto_o_nulo
from app.api.admin.sesion_web import ActorWeb, exigir_csrf
from app.infra import revision_app as revision
from app.infra.revision_app import MarcaInvalida

router = APIRouter(prefix="/panel/revision-app", tags=["panel"], include_in_schema=False)

PERMISO = "tablero.ver"
FILTROS = ("todos", "pendientes", "falla", "nuevos")


def _volver(
    version: str, filtro: str, punto: str = "", parte: str = "telefono", **que: str
) -> RedirectResponse:
    partes = [f"version={quote(version)}"]
    if parte in revision.PARTES and parte != "telefono":
        partes.append(f"parte={parte}")
    if filtro and filtro != "todos":
        partes.append(f"filtro={quote(filtro)}")
    partes += [f"{k}={quote(v)}" for k, v in que.items() if v]
    ancla = f"#punto_{punto}" if punto else ""
    return RedirectResponse(
        f"/panel/revision-app?{'&'.join(partes)}{ancla}", status_code=status.HTTP_303_SEE_OTHER
    )


def _build(version: str) -> str:
    """«0.1.0+35» → «+35», como se escribe `desde` en cada punto."""
    return "+" + version.rsplit("+", 1)[1] if "+" in version else ""


@router.get("", response_class=HTMLResponse)
async def revisar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    version: str = "",
    parte: str = "telefono",
    filtro: str = "todos",
    aviso: str = "",
    error: str = "",
) -> HTMLResponse:
    actor.exigir(PERMISO)
    conocidas = await revision.versiones(sesion)
    version = version.strip() or next(iter(conocidas), "")
    parte = parte if parte in revision.PARTES else "telefono"
    filtro = filtro if filtro in FILTROS else "todos"

    marcas = await revision.marcas_de(sesion, version) if version else {}
    # Junto a cada punto, cómo quedó en la última versión revisada antes que esta.
    anterior = (
        revision.anterior_a(version, await revision.versiones_con_marcas(sesion))
        if version
        else None
    )
    marcas_anteriores = await revision.marcas_de(sesion, anterior) if anterior else {}
    build = _build(version)

    def se_ve(p: revision.Punto) -> bool:
        marca = marcas.get(p.clave)
        return {
            "todos": True,
            "pendientes": marca is None,
            "falla": marca is not None and marca.estado == "falla",
            "nuevos": bool(build) and p.desde == build,
        }[filtro]

    secciones = [s for s in revision.SECCIONES if s.parte == parte]
    todos = [p for s in secciones for p in s.puntos]
    vigentes = {p.clave: marcas[p.clave] for p in todos if p.clave in marcas}

    def avance(cual: str) -> tuple[int, int]:
        puntos = [p for s in revision.SECCIONES if s.parte == cual for p in s.puntos]
        return sum(1 for p in puntos if p.clave in marcas), len(puntos)
    return render(
        peticion,
        "revision_app.html",
        {
            "version": version,
            "conocidas": conocidas,
            "telefonos": conocidas.get(version, []),
            "con_otra": [
                (quien, v) for v, quienes in conocidas.items() if v != version for quien in quienes
            ],
            "anterior": anterior,
            "marcas": marcas,
            "marcas_anteriores": marcas_anteriores,
            "build": build,
            "parte": parte,
            "partes": [
                (clave, titulo, *avance(clave)) for clave, titulo in revision.PARTES.items()
            ],
            "filtro": filtro,
            "secciones": [(s, [p for p in s.puntos if se_ve(p)]) for s in secciones],
            "total": len(todos),
            "bien": sum(1 for m in vigentes.values() if m.estado == "bien"),
            "fallas": sum(1 for m in vigentes.values() if m.estado == "falla"),
            "pendientes": len(todos) - len(vigentes),
            "nuevos": sum(1 for p in todos if build and p.desde == build),
            "puede_marcar": actor.puede(PERMISO),
            "aviso": aviso,
            "error": error,
        },
        actor=actor,
        seccion="Revisión",
    )


@router.post("/marcar")
async def marcar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    csrf: str = Form(""),
    version: str = Form(""),
    punto: str = Form(""),
    estado: str = Form(""),
    nota: str = Form(""),
    filtro: str = Form("todos"),
    parte: str = Form("telefono"),
) -> RedirectResponse:
    exigir_csrf(peticion, csrf)
    actor.exigir(PERMISO)
    try:
        await revision.marcar(
            sesion,
            version=version,
            punto=punto,
            estado=estado,
            nota=texto_o_nulo(nota, maximo=500),
            quien=actor.usuario_id,
        )
    except MarcaInvalida as e:
        await sesion.rollback()
        return _volver(version, filtro, punto, parte, error=str(e))
    await sesion.commit()
    return _volver(version, filtro, punto, parte)


@router.post("/desmarcar")
async def desmarcar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    csrf: str = Form(""),
    version: str = Form(""),
    punto: str = Form(""),
    filtro: str = Form("todos"),
    parte: str = Form("telefono"),
) -> RedirectResponse:
    exigir_csrf(peticion, csrf)
    actor.exigir(PERMISO)
    await revision.desmarcar(sesion, version=version, punto=punto)
    await sesion.commit()
    return _volver(version, filtro, punto, parte)


@router.post("/volver-a-empezar")
async def volver_a_empezar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    csrf: str = Form(""),
    version: str = Form(""),
    parte: str = Form("telefono"),
) -> RedirectResponse:
    """Borra las marcas de una parte en esa versión: cuando solo se actualizó el
    servidor, el dashboard se vuelve a revisar sin APK nuevo."""
    exigir_csrf(peticion, csrf)
    actor.exigir(PERMISO)
    if parte not in revision.PARTES:
        return _volver(version, "todos", error="Esa parte de la revisión no existe.")
    borradas = await revision.volver_a_empezar(sesion, version=version, parte=parte)
    await sesion.commit()
    return _volver(
        version, "todos", "", parte,
        aviso=f"{revision.PARTES[parte]}: {borradas} marca(s) borrada(s); todo queda pendiente.",
    )
