# Atajos de desarrollo. Producción va por docker-compose.
.PHONY: ayuda instalar db db-parar migrar usuario pruebas lint contratos movil movil-demo movil-contratos movil-esquema movil-ticket app app-demo panel apk-demo api worker limpiar

DB ?= postgresql+psycopg://postgres:dsd@127.0.0.1:5432/dsd

ayuda:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

instalar:  ## Crea el venv e instala dependencias
	cd server && uv venv --python 3.12 && uv pip install -e '.[dev]'

migrar:  ## Aplica las migraciones (DB=... para apuntar a otra base)
	cd server && DSD_DATABASE_URL="$(DB)" .venv/bin/alembic upgrade head

pruebas:  ## Corre la suite completa (necesita PostgreSQL+PostGIS arriba)
	cd server && .venv/bin/python -m pytest -q

db:  ## Levanta PostgreSQL+PostGIS para desarrollo (contenedor desechable)
	docker run -d --name dsd-postgres -p 5432:5432 		-e POSTGRES_PASSWORD=dsd -e POSTGRES_DB=dsd 		postgis/postgis:17-3.5
	@echo "Esperando a que acepte conexiones..."
	@until docker exec dsd-postgres pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
	@docker exec dsd-postgres psql -U postgres -d dsd -c 'CREATE EXTENSION IF NOT EXISTS postgis' >/dev/null
	@echo "Listo en 127.0.0.1:5432 (usuario postgres, clave dsd)"

db-parar:  ## Detiene y borra el contenedor de desarrollo
	-docker rm -f dsd-postgres

lint:  ## Ruff
	cd server && .venv/bin/ruff check app tests

contratos:  ## Regenera vectores y OpenAPI — REVISA EL DIFF antes de commitear
	cd server && .venv/bin/python ../contracts/generar_vectores.py
	cd server && .venv/bin/python ../contracts/generar_importes.py
	cd server && .venv/bin/python ../contracts/exportar_openapi.py
	@echo
	@echo "Los vectores de Argon2 NO se regeneran solos (la sal es aleatoria)."
	@echo "Si cambiaste PARAMETROS_ARGON2: make contratos-argon2"

contratos-argon2:  ## Regenera los vectores de Argon2id
	cd server && .venv/bin/python ../contracts/generar_vectores_argon2.py

usuario:  ## Crea el primer usuario de oficina del panel (pregunta la contraseña)
	cd server && DSD_DATABASE_URL="$(DB)" .venv/bin/python -m app.cli crear-usuario

api:  ## Levanta la API y el panel en modo desarrollo
	@echo "API:   http://127.0.0.1:8000/docs"
	@echo "Panel: http://127.0.0.1:8000/panel"
	cd server && DSD_DATABASE_URL="$(DB)" DSD_DEBUG=1 .venv/bin/uvicorn app.main:app --reload

panel:  ## Recuerda cómo entrar al panel
	@echo "Base recién migrada y sin usuarios:"
	@echo "  1. make usuario   <- crea el primero; pregunta la contraseña"
	@echo "  2. make api"
	@echo "  3. Abre http://127.0.0.1:8000/panel"
	@echo
	@echo "NO hay usuario por defecto. Sembrar admin/admin123 en una migración"
	@echo "dejaría en producción un usuario con todos los permisos y la"
	@echo "contraseña publicada en el repositorio."
	@echo
	@echo "DSD_DEBUG=1 apaga el atributo Secure de la cookie, que es lo que"
	@echo "permite usar el panel sobre http en desarrollo. En producción va"
	@echo "detrás de Caddy con TLS y el atributo se enciende solo."

worker:  ## Levanta el worker de la cola
	cd server && DSD_DATABASE_URL="$(DB)" .venv/bin/python -m app.workers.principal

movil:  ## Analiza y prueba el núcleo Dart y la app Flutter
	cd mobile/packages/dsd_core && dart pub get && dart analyze && dart test
	cd mobile/app && flutter pub get && flutter analyze && flutter test

movil-demo:  ## Prueba la app compilada en modo demo (el camino que se instala en campo)
	cd mobile/app && flutter test --dart-define=DSD_DEMO=true

app:  ## Corre la app en el dispositivo o emulador conectado
	cd mobile/app && flutter run

app-demo:  ## Corre la app con el botón de modo demo (siembra datos, entra sin servidor)
	cd mobile/app && flutter run --dart-define=DSD_DEMO=true

apk-demo:  ## Genera el APK de depuración con modo demo para instalar a mano
	cd mobile/app && flutter build apk --debug --dart-define=DSD_DEMO=true
	@echo
	@echo "APK en mobile/app/build/app/outputs/flutter-apk/app-debug.apk"
	@echo "Instalar:  adb install -r mobile/app/build/app/outputs/flutter-apk/app-debug.apk"

movil-ticket:  ## Regenera la vista previa del ticket — REVÍSALA, se ve el papel
	cd mobile/packages/dsd_core && dart run tool/generar_ticket_ejemplo.dart
	@echo
	@echo "Míralo:  cat contracts/ticket_58mm_ejemplo.txt"

movil-esquema:  ## Regenera la constante del esquema local desde el .sql
	cd mobile/packages/dsd_core && dart run tool/generar_esquema.dart

movil-contratos:  ## Regenera los sobres de ejemplo que consume el servidor
	cd mobile/packages/dsd_core && dart run tool/generar_sobres_ejemplo.dart
	@echo "Ahora corre 'make pruebas': test_contrato_dart.py los empuja al servidor real."

limpiar:
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf server/.pytest_cache server/.ruff_cache
