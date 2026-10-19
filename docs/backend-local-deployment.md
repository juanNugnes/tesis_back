# Despliegue local: mensajes adultos y migracion 0015

Ejecutado con autorizacion el 18/10/2026, exclusivamente en desarrollo local:
Docker Desktop `desktop-linux`, pipe local, `ENVIRONMENT=development`, base
`tesis` en servicio `db`. Analytics interno `http://analytics:8765`.

## Respaldo y migracion

Respaldo privado conservado fuera de la imagen y excluido de Git:

`local-backups/backend-adult-messages-20261018-verified/`

- `tesis.dump`: PostgreSQL custom, esquema/datos/objetos de la base completa,
  112309 bytes, SHA-256
  `a1fd09bb2f8ffa326c9fcf6e95160af29f1fa3c7708bc4928c47c27c34b15d11`.
- `globals.sql`: roles y configuracion global, 671 bytes, SHA-256
  `097e19d2d1538384327d37daffb5f97d370bc3aee8e6b5b345cdce4ca846e504`.
  Contiene informacion privada; no mostrarlo, copiarlo a Git o incluirlo en imagenes.
- `backup-verification.json`, `restore-list.txt`: manifiesto y lista de restauracion.
- `migration-verification.json` y `deployment-final-verification.json`: controles posteriores.

Se restauro tanto el global como el dump en un PostgreSQL temporal con la misma
imagen, red `none` y almacenamiento tmpfs. Se compararon cantidad y hash del
contenido de las 29 tablas con el original. Solo ese contenedor temporal se
detuvo y elimino automaticamente; no tenia volumen persistente. El primer intento
del helper se detuvo antes del dump por interpretar etiquetas de salida psql;
se corrigio con modo quiet y el respaldo verificado se completo en la ruta indicada.

Se reviso la migracion existente `20261018_0015`, sin editarla. Es aditiva:
en `daily_usages` y `usage_snapshots` agrega `last_night_activity_at` timestamptz
nullable y `last_night_activity_status` usando el enum existente,
NOT NULL/default UNAVAILABLE. No borra columnas, transforma valores ni crea enums.
Alembic usa una transaccion DDL PostgreSQL.

Se aplico solo `alembic upgrade 20261018_0015` desde un proceso separado antes de
reemplazar backend. Revision final: **20261018_0015 (head)**. Los dos registros
anteriores de cada tabla quedaron con fecha NULL y estado UNAVAILABLE. Se comparo
el contenido de todas las columnas originales de todas las tablas; solo cambio
`alembic_version`. Se repitio la comparacion despues de las pruebas HTTP.

## Contexto e imagen

`.dockerignore` permite solo `app/`, `alembic/`, `requirements.txt`, `alembic.ini`
y archivos Docker. Excluye .env, credenciales, Git, respaldos, bases locales,
entornos virtuales, caches y reportes. Se exporto el contexto con un build scratch
antes de construir: 118 archivos, incluidos ruta/cliente/esquema nuevos y 0015.
Se verifico ausencia de archivos prohibidos y valores privados del .env en los
archivos exportados. Auditoria local: `local-backups/backend-context-audit-20261018`.

Imagen utilizada, dos etiquetas al mismo ID:

- `tesis_backend-backend:adult-messages-04f1e3a`.
- `tesis_backend-backend:latest` (nombre usado por Compose).
- ID: `sha256:27c33846aebbe3e7f207031bc16214e7f4a1643d1f88ae80c1b052614e3fb934`.

La imagen anterior permanece como `tesis_backend-backend:rollback-before-adult-messages`.
Se confirmaron importaciones y OpenAPI dentro de la imagen nueva sin red ni .env.
Las credenciales solo se inyectan al ejecutar, nunca en build; config de imagen
sin DATABASE_URL/tokens/JWT privados y logs del contenedor revisados sin secretos
ni errores. No se imprimieron valores privados. Configuracion y token existentes
se compararon en memoria antes de migrar y permanecen iguales.

## Comandos usados

Los scripts de backup y migracion son de una sola ejecucion; no repetirlos sobre
un despliegue ya completado. Se detienen ante revision, datos o respaldo inesperados.

```powershell
docker context show
docker compose -f docker-compose.yml -f docker-compose.analytics.yml config --quiet
& '.venv/Scripts/python.exe' scripts/backup_postgres_local.py local-backups/backend-adult-messages-20261018-verified
# Auditoria de contexto: Dockerfile por stdin, sin secretos.
$auditDockerfile = "FROM scratch`nCOPY . /`n"
$auditDockerfile | docker build --file - --output type=local,dest=local-backups/backend-context-audit-20261018 .
docker image tag sha256:87c338f7443587c00d02193d7aaec363d0c5a004955b299be5ff96fe211a73fd tesis_backend-backend:rollback-before-adult-messages
docker build --tag tesis_backend-backend:adult-messages-04f1e3a --tag tesis_backend-backend:latest .
& '.venv/Scripts/python.exe' scripts/migrate_backend_local.py local-backups/backend-adult-messages-20261018-verified
# El helper anterior ejecuta:
# docker compose -f docker-compose.yml -f docker-compose.analytics.yml run --rm --no-deps backend alembic upgrade 20261018_0015
docker compose -f docker-compose.yml -f docker-compose.analytics.yml up -d --no-deps --no-build backend
docker exec tesis_backend-backend-1 alembic current
docker ps -a
```

El ultimo up solo reemplazo backend, sin reiniciar dependencias. El arranque
normal volvio a comprobar Alembic: ya estaba en head y no aplico mas cambios.

## Validacion desplegada

`scripts/verify_backend_local.py` se ejecuto por stdin en el backend, sin copiar
archivos ni guardar credenciales. Usa un adulto local existente con vinculo
activo y verificacion aprobada; emite JWT de prueba de cinco minutos solo en
memoria, sin OTP, altas ni cambios en cuentas. No imprime tokens/datos personales.
El argumento de recibo contiene exclusivamente el fixture C1 ya procesado.

```powershell
$receipt = Get-Content -Raw local-backups/analytics-cutover-20261018-200138/live-smoke-receipt.json
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($receipt))
Get-Content -Raw scripts/verify_backend_local.py | docker exec -i tesis_backend-backend-1 python -B - --receipt-base64 $encoded
```

20 comprobaciones HTTP aprobadas:

- Health, OpenAPI/docs, identidad adulta, perfiles, perfil individual, dispositivos,
  Usage/listado/dia y listado de analisis: 200.
- Nuevo GET adulto: 200 con `versions=[]`, no-store y repeticion identica.
- Nuevo GET sin token, token invalido o DeviceBearer: 401; otro perfil: 403.
- Usage, Activities e historico sin autorizacion: 401.
- Activities con DeviceBearer y cuerpo incompleto: 422, sin crear ofertas.
- Selector Activities real por cliente HTTP, sin persistencia: seleccion correcta.
- Historico C1: replay del recibo ficticio YA existente, resultado original identico.
- Health Analytics: mensajes READY, PMUM DISABLED, C1 smoke-only-not-calibrated.

No se escribieron datos durante estas pruebas: no nuevas metricas, perfiles,
ofertas, tarjetas o evaluaciones. No se modificaron contratos ni reglas de
Android/Analytics/F12/C1/PMUM/Activities. La suite de aplicacion previa paso
285 pruebas; no se altero el codigo de aplicacion durante este despliegue.

Estado final: backend ejecutando y health 200; PostgreSQL y Analytics saludables,
IDs y StartedAt identicos a los registrados antes del despliegue. El Analytics
aislado de ensayos tambien se conservo. Todos los volumenes e imagenes anteriores
permanecen disponibles. No se uso down -v ni se eliminaron volumenes.

## Recuperacion segura, no ejecutada

Si aparece un fallo critico, detener el procedimiento antes de nuevos cambios.
La migracion aditiva permite usar el modelo anterior sin borrar las columnas.
El arranque anterior intenta Alembic y no conoce 0015; por eso el override
`docker-compose.backend.rollback.yml` inicia solo Uvicorn y conserva 0015:

```powershell
# Revisar y aprobar antes de ejecutar una recuperacion.
docker compose -f docker-compose.yml -f docker-compose.analytics.yml -f docker-compose.backend.rollback.yml up -d --no-deps --no-build backend
```

No ejecutar downgrade: eliminaria columnas y posibles valores nuevos. Si fuera
necesario recuperar datos, restaurar el respaldo verificado en una instancia y
volumen NUEVOS, conciliar cambios posteriores y decidir el cambio de conexion
antes de tocar la base original. Nunca sobrescribir automaticamente el original.

No se realizo commit ni push. Los cambios funcionales y de despliegue estan
listos para revisar/commitear; excluir .env, local-backups y coverage.xml.
