# El laboratorio analítico.
#
# ---------------------------------------------------------------------------
# EL CONTEXTO DE BUILD ES LA RAÍZ DEL REPOSITORIO, NO `analytics/`
# ---------------------------------------------------------------------------
# Porque las definiciones de las métricas NO viven aquí: viven en
# `server/app/domain/analitica.py`, con el resto del dominio. El riesgo del
# laboratorio no es que una consulta sea lenta, es que la misma pregunta se
# conteste distinto cada vez que alguien la hace — y dos copias del mismo SQL se
# separan en la primera corrección.
#
# Ese módulo solo importa biblioteca estándar (`dataclasses`, `decimal`), así que
# se copia suelto: no arrastra FastAPI, SQLAlchemy ni el resto del servidor a esta
# imagen. Si algún día importara algo del servidor, esto se rompe al construir —
# que es mucho mejor que romperse en silencio al arrancar.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

COPY analytics/requirements.txt /srv/analytics/
RUN pip install --no-cache-dir -r /srv/analytics/requirements.txt

# La estructura del repositorio se replica TAL CUAL bajo /srv, y eso no es
# cosmético: `app.py` encuentra el dominio con
# `Path(__file__).parent.parent / "server"`. Si aquí se aplanara —app.py en /srv
# y el dominio en /srv/server— ese cálculo daría /server y el import fallaría
# solo dentro del contenedor, que es el peor lugar para descubrirlo.
COPY analytics/ /srv/analytics/
COPY server/app/domain/analitica.py /srv/server/app/domain/analitica.py
RUN touch /srv/server/app/__init__.py /srv/server/app/domain/__init__.py

WORKDIR /srv/analytics

RUN useradd --create-home --uid 10002 analitica && chown -R analitica /srv
USER analitica

EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
