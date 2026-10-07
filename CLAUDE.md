# Instrucciones para trabajar en este repositorio

## Al terminar cualquier cambio: los comandos de actualización

Siempre que se suba un cambio a `main`, la respuesta termina con los comandos exactos
para aplicarlo, aunque el cambio parezca no necesitarlos:

1. **Servidor**: `git pull origin main`, el `git log --oneline -1` esperado, y
   `docker compose build migraciones api worker && docker compose up -d`. Si hubo
   migración, decir qué revisión debe mostrar `/salud` en `esquema`.
2. **App (en la PC, WSL)**: `git pull origin main`, la versión esperada de
   `grep '^version' mobile/app/pubspec.yaml`, y
   `make apk DSD_BASE_URL=https://api.distribucionesse.com`. Recordar que se instala
   ENCIMA de la que ya está, sin desinstalar. Si el cambio no tocó `mobile/`, decirlo:
   «no hace falta APK nuevo», y dar igual el comando por si no se ha instalado el
   último.

Si `mobile/` cambió, subir el `versionCode` (el número después del `+`) antes de
subir el cambio.

## Idioma

Todo lo que se escribe —commits, documentación, ADR, textos de pantalla, comentarios
del código y respuestas— va en español.
