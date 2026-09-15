# Atajos de desarrollo. Producción va por docker-compose.
.PHONY: ayuda instalar migrar pruebas lint contratos api worker limpiar

DB ?= postgresql+psycopg://postgres@127.0.0.1:5432/dsd

ayuda:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

instalar:  ## Crea el venv e instala dependencias
	cd server && uv venv --python 3.12 && uv pip install -e '.[dev]'

migrar:  ## Aplica las migraciones (DB=... para apuntar a otra base)
	cd server && DSD_DATABASE_URL="$(DB)" .venv/bin/alembic upgrade head

pruebas:  ## Corre la suite completa
	cd server && .venv/bin/python -m pytest -q

lint:  ## Ruff
	cd server && .venv/bin/ruff check app tests

contratos:  ## Regenera vectores y OpenAPI — REVISA EL DIFF antes de commitear
	cd server && .venv/bin/python ../contracts/generar_vectores.py
	cd server && .venv/bin/python ../contracts/exportar_openapi.py
	@echo
	@echo "Los vectores de Argon2 NO se regeneran solos (la sal es aleatoria)."
	@echo "Si cambiaste PARAMETROS_ARGON2: make contratos-argon2"

contratos-argon2:  ## Regenera los vectores de Argon2id
	cd server && .venv/bin/python ../contracts/generar_vectores_argon2.py

api:  ## Levanta la API en modo desarrollo
	cd server && DSD_DATABASE_URL="$(DB)" .venv/bin/uvicorn app.main:app --reload

worker:  ## Levanta el worker de la cola
	cd server && DSD_DATABASE_URL="$(DB)" .venv/bin/python -m app.workers.principal

limpiar:
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf server/.pytest_cache server/.ruff_cache
