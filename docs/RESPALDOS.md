# Respaldos, restauración y el simulacro

> **La única frase que importa de este documento:**
> un respaldo que nunca restauraste no es un respaldo, es un archivo del que no
> sabes nada.

Todo lo demás de aquí existe para que esa frase deje de ser verdad en esta
instalación.

---

## 0. Qué hay que proteger, y de qué

La base de datos del servidor de oficina tiene cosas que **no se pueden volver a
capturar**:

| Qué | Si se pierde |
|---|---|
| Ventas y sus partidas | No hay forma de reconstruirlas: el ticket impreso está en el cliente |
| Cobros y su aplicación FIFO | Se pierde el rastro de quién pagó qué |
| `cuentas_por_cobrar` | **No se sabe a quién cobrarle**, ni cuánto |
| `movimientos_inventario` | El libro mayor, que es append-only precisamente para esto |
| Mermas y no-drops | La explicación de cada diferencia de la liquidación |

Y cosas que sí se podrían volver a capturar, con trabajo: catálogo, precios,
clientes, usuarios.

Los riesgos reales, en orden de probabilidad:

1. **El disco de la mini PC muere.** Es lo que pasa. Un respaldo en el mismo
   disco no protege de esto.
2. **Un `DELETE` o un `UPDATE` sin `WHERE`** hecho a mano en `psql` a las once
   de la noche. Protege un respaldo de ayer, no la replicación.
3. **Ransomware o robo del equipo.** Protege un respaldo *fuera del edificio*.
4. **Un apagón a media escritura.** Protege el UPS; el respaldo es el plan B.

---

## 1. El comando del día

```bash
make respaldo
```

Escribe en `~/respaldos-dsd` un `dsd-AAAAMMDD-HHMMSS.dump` con su `.sha256` al
lado, y conserva los últimos 14.

Para elegir otra carpeta —un disco externo montado, por ejemplo:

```bash
DSD_RESPALDOS=/mnt/d/respaldos-dsd make respaldo
```

### Las cuatro decisiones del script, y por qué

**Formato `custom` (`-Fc`) y no SQL plano.** Comprime, y sobre todo permite
restaurar **una sola tabla**. El día que haya que recuperar
`cuentas_por_cobrar` sin pisar el resto, un `.sql` de 400 MB no sirve para eso.

**Checksum al lado.** Un respaldo corrupto es indistinguible de uno bueno hasta
que se intenta restaurar, y eso pasa el peor día. El `.sha256` se verifica en el
simulacro y tarda un segundo.

**Se escribe a un temporal y se renombra al final.** Si el disco se llena o se
corta la luz a media escritura, el archivo incompleto **no reemplaza al de
ayer**. `mv` dentro del mismo sistema de archivos es atómico; copiar encima no.

**Retención por cantidad, no por fecha.** `find -mtime` borra por fecha de
modificación: si el respaldo falla una semana, se queda sin nada que borrar y
luego borra todo de golpe. Contar archivos y quedarse con los N más recientes es
la regla que no se puede volver en contra.

---

## 2. El simulacro: la parte que de verdad importa

```bash
make simulacro
```

Toma el respaldo más reciente (o el que se le pase como argumento), lo restaura
en una base **desechable** llamada `dsd_simulacro`, verifica, y la borra.

**No toca la base de producción.** Es la única propiedad no negociable del
script: el nombre de la base destino se construye dentro y se comprueba que
termine en `_simulacro` antes de cualquier `DROP`. Un simulacro que pueda pisar
lo que protege es peor que no tenerlo.

### Qué comprueba, y qué fallo busca cada cosa

| Comprobación | El fallo que encuentra |
|---|---|
| El `.sha256` cuadra | El archivo está truncado — el disco se llenó y `pg_dump` terminó con 0 |
| Edad del respaldo | El cron dejó de correr y nadie se enteró |
| PostGIS antes de restaurar | Las extensiones **no van en el dump**: sin crearla, el restore falla con un error sobre un tipo desconocido que no menciona PostGIS |
| `alembic_version` restaurada | El dump no trajo el esquema |
| La versión coincide con el repositorio | El respaldo es bueno y el esquema es **viejo**: se restaura y la API no arranca |
| Conteo por tabla | El dump es de una base vacía y «restaurar» no probó nada |
| `smoke_invariantes.sql` | El esquema llegó sin sus constraints ni sus disparadores — los datos están y las reglas que los sostienen no |

La última es la que separa este simulacro de un `pg_restore` a mano. Que las
tablas existan no prueba nada: lo que sostiene el sistema offline son los
`CHECK`, los índices únicos de folio y los disparadores del libro mayor.
`smoke_invariantes.sql` **intenta violar cinco reglas** y espera que PostgreSQL
lo impida.

### Si el simulacro falla

El script dice qué comprobación falló y con qué comando reproducirlo. Lo que
**no** hay que hacer es dejarlo para después: un simulacro rojo significa que
ahora mismo no existe forma de recuperarse.

---

## 3. Sacar el respaldo del edificio

**Esto es lo que ningún script de aquí hace, y es la mitad del valor.**

Un respaldo en el mismo disco que la base no protege del disco. En el mismo
cuarto, no protege del cuarto: un incendio, un robo o un ransomware que cifre
todo lo montado se lleva las dos copias.

La regla práctica, que es vieja y sigue siendo la correcta — **3-2-1**:

- **3** copias de los datos,
- en **2** medios distintos,
- **1** de ellas fuera del sitio.

Para esta operación eso se traduce en:

| Copia | Dónde | Cómo |
|---|---|---|
| 1 | La base viva en la mini PC | — |
| 2 | `~/respaldos-dsd` en la misma máquina | `make respaldo` por cron |
| 3 | **Fuera** | ver abajo |

Tres caminos razonables para la tercera, de menos a más trabajo:

**Un disco externo que se rota.** Dos discos USB, uno en la oficina y otro en
casa, y se cambian el lunes. Es lo más barato y lo que de verdad se sostiene en
un negocio pequeño, porque no depende de ninguna cuenta ni de ninguna conexión.

```bash
# con el disco montado
DSD_RESPALDOS=/mnt/d/respaldos-dsd make respaldo
DSD_RESPALDOS=/mnt/d/respaldos-dsd make simulacro
```

**`rclone` a un almacenamiento remoto.** Ya está instalado en muchos entornos.
Lo importante es **cifrar antes de subir**: un dump en la nube de un tercero es
la cartera completa del negocio en un disco ajeno.

```bash
# Cifrado simétrico con GPG, y la frase NO en el script.
gpg --symmetric --cipher-algo AES256 \
    --passphrase-file ~/.dsd-respaldo-frase \
    ~/respaldos-dsd/dsd-20261001-030000.dump
rclone copy ~/respaldos-dsd/dsd-20261001-030000.dump.gpg remoto:dsd/
```

> La frase de cifrado **no puede vivir solo en la mini PC**. Si el disco muere,
> la frase muere con él y los respaldos remotos quedan ilegibles. Escríbela en
> papel y guárdala donde guardas los documentos del negocio.

**Replicación en caliente a una segunda máquina.** Protege del disco y no
protege del `DELETE` sin `WHERE`: el borrado se replica. Es un complemento del
respaldo, nunca un reemplazo.

---

## 4. La bitácora de simulacros

Un simulacro que no se apunta es un simulacro que nadie sabe si se hizo. La
tabla se llena a mano, con una línea por corrida:

| Fecha | Respaldo probado | Resultado | Quién | Notas |
|---|---|---|---|---|
| | | | | |

Cadencia recomendada: **una vez al mes**, y **siempre** después de:

- una migración que cambie el esquema,
- cambiar de disco o de máquina,
- cambiar la carpeta o el medio de respaldo.

---

## 5. El cron

```bash
crontab -e
```

```cron
# Respaldo diario a las 3 de la mañana. A esa hora no hay nadie
# sincronizando: un pg_dump concurrente con ocho camiones subiendo no corrompe
# nada —PostgreSQL es MVCC— pero compite por disco justo cuando no hace falta.
0 3 * * * cd /home/TU_USUARIO/Distribution && make respaldo >> ~/respaldos-dsd/cron.log 2>&1

# Simulacro semanal, los domingos a las 4. Si falla, el log lo dice.
0 4 * * 0 cd /home/TU_USUARIO/Distribution && make simulacro >> ~/respaldos-dsd/simulacro.log 2>&1
```

> **El log no se lee solo.** `dsd_jobs_fallidos` y los demás números de salud
> están en `/metrics`; el respaldo no. Lo más simple que funciona es mirar
> `simulacro.log` el lunes por la mañana, y es justo lo que la bitácora de §4
> obliga a hacer.

---

## 6. Restaurar de verdad

El día que haga falta. **Leer completo antes de teclear nada.**

```bash
# 1. Parar lo que escribe. La API y el worker, en ese orden: el worker puede
#    estar a media transacción y la API seguiría aceptando ventas.
docker compose stop api worker     # producción
# (en desarrollo: Ctrl+C en las terminales de `make api` y `make worker`)

# 2. Comprobar el archivo ANTES de tocar la base.
cd ~/respaldos-dsd && sha256sum -c dsd-AAAAMMDD-HHMMSS.dump.sha256

# 3. Renombrar la base actual en vez de borrarla. Si la restauración sale mal,
#    lo que había sigue ahí. Borrarla es el paso que no se puede deshacer, y no
#    hace falta darlo hoy.
psql "$ADMIN_URL" -c 'ALTER DATABASE dsd RENAME TO dsd_antes_de_restaurar'
psql "$ADMIN_URL" -c 'CREATE DATABASE dsd'
psql "$DB_URL" -c 'CREATE EXTENSION IF NOT EXISTS postgis'

# 4. Restaurar.
pg_restore --dbname="$DB_URL" --no-owner --no-privileges --jobs=2 \
    dsd-AAAAMMDD-HHMMSS.dump

# 5. Los roles NO vienen en el dump (van con --no-owner --no-privileges).
#    Hay que volver a crearlos, o la API arrancará sin poder leer nada.
psql "$DB_URL" -v clave_api="$DSD_CLAVE_API" -f server/db/ops/rol_api.sql
psql "$DB_URL" -v clave_analitica="$DSD_CLAVE_ANALITICA" \
    -f server/db/ops/rol_analitico.sql

# 6. Migraciones pendientes: el respaldo puede ser de antes del último despliegue.
make migrar

# 7. Y recalcular lo derivado, que no se respalda por ser derivado.
make recalcular-tablero
make refrescar-analitica

# 8. Arrancar y comprobar.
docker compose start api worker
curl -s http://127.0.0.1:8000/salud | jq
```

### Lo que hay que mirar después de restaurar

1. **`/salud`** — `ok: true`, y **`rls: true`** si es producción. Un `rls:
   false` ahí significa que falta `DSD_DATABASE_URL_API` o que el rol no se
   recreó en el paso 5.
2. **Los folios de los dispositivos.** Es el punto delicado de cualquier
   restauración en este sistema: si el respaldo es de antes de que un teléfono
   consumiera parte de su rango, ese rango se volverá a asignar y **dos
   documentos distintos podrían llevar el mismo folio impreso**. Antes de dejar
   salir a los camiones, en `Panel → Teléfonos`, revisa qué equipos traen cola
   pendiente y pídeles que sincronicen **antes** de vender; si hay duda,
   asígnales un rango nuevo.
3. **La cuarentena.** `Panel → Cuarentena`: lo que llegó entre el respaldo y el
   corte puede volver a llegar —los teléfonos reintentan— y la idempotencia del
   servidor hace que eso sea inofensivo. Lo que **no** vuelve es lo que un
   teléfono ya había dado por entregado y confirmado. Ahí la única recuperación
   es el papel del vendedor.

---

## 7. Lo que este sistema tiene a su favor

Vale decirlo, porque cambia el tamaño del problema: en un DSD, **los teléfonos
son una tercera copia parcial**.

La cola de salida de cada equipo conserva lo que no ha entregado, y el servidor
es idempotente: recibir dos veces el mismo sobre no duplica nada. Así que una
restauración a un punto de ayer no pierde necesariamente lo de hoy — los
camiones vuelven a subir lo que tengan pendiente, y lo que ya estaba entregado y
confirmado es lo único irrecuperable.

Eso **no** es un respaldo y no sustituye a nada de lo de arriba. Pero explica
por qué el borrado remoto de un equipo (Fase 9) entrega antes de borrar: esa
copia parcial es real y vale dinero.
