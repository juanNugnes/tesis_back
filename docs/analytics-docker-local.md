# Analytics 04f1e3a: entorno local

Imagen construida desde el ZIP extraido, sin editar `modera-modelo` ni las fuentes
analiticas. `04f1e3a` es la etiqueta de entrega; el ZIP no incluye historia Git.
PMUM permanece deshabilitado. C1 usa `smoke-only-not-calibrated`; los resultados
son ensayos tecnicos ficticios, sin calibracion de producto.

## Configuracion

Desde la raiz de `tesis_backend`, PowerShell. Compose lee el `.env` privado.
No ejecutar `docker compose config` sin `--quiet`, ni imprimir `docker inspect`
completo: pueden revelar el token.

- `ANALYTICS_SERVICE_TOKEN`: token privado existente, compartido con backend;
  minimo 16 caracteres ASCII sin espacios. No rotarlo para esta actualizacion.
- `ANALYTICS_SERVICE_URL=http://analytics:8765`: se conserva.
- `ANALYTICS_TIMEOUT_SECONDS=5`: se conserva.
- `ANALYTICS_IMAGE=modera-analytics:04f1e3a`: opcional, valor por defecto.
- `ANALYTICS_SOURCE_DIR`: opcional; por defecto
  `C:/Users/Juan/Projects/modera-analytics-zip/MODERA_ANALYTICS_INTEGRACION_04f1e3a`.

Compose fija Activities, historico, mensajes adultos y coordinador infantil en
`true`, PMUM en `false`. Las dos capacidades nuevas son exclusivamente internas:
esta entrega no agrega rutas al backend ni cambia Android.

| Estado | Volumen | Ruta del contenedor |
| --- | --- | --- |
| C1 activo, restaurado del respaldo | `tesis_backend_historical_state_smoke_04f1e3a` (externo) | `/var/lib/modera/historical/history.json` |
| C1 original, preservado | `tesis_backend_historical_state_smoke` | No montado en Analytics nuevo |
| Mensajes nuevo | `tesis_backend_analytics_messages_04f1e3a` | `/var/lib/modera/messages/messages.sqlite` |
| Coordinador nuevo | `tesis_backend_analytics_child_offers_04f1e3a` | `/var/lib/modera/child-offers/coordinator.sqlite` |

El nombre del volumen C1 es explicito para evitar una base vacia por cambiar el
nombre del proyecto Compose. El volumen activo fue restaurado y verificado desde
el respaldo del original, que se conserva intacto. Se mantiene `history.json`,
sus marcas y el resto del directorio. Los dos volumenes nuevos de mensajes y
coordinador reciben permisos UID/GID 10001 de la imagen.
El fixture C1 del ZIP coincide con el anterior. No hay puertos Analytics al host.

```powershell
docker compose -f docker-compose.yml -f docker-compose.analytics.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.analytics.yml build analytics
```

Comprobar `$LASTEXITCODE` tras cada comando; detenerse ante cualquier error.

## Ensayo aislado, sin reemplazar el servicio actual

`docker-compose.analytics.smoke.yml` crea un proyecto independiente, un volumen
nuevo `modera_analytics_smoke_04f1e3a_state` y un unico servicio en la red existente
`tesis_backend_default`, accesible como `analytics-smoke:8765`.
Usa el mismo token del `.env` pero no monta estados actuales.

La inicializacion siguiente solo corresponde al primer uso confirmado de un
volumen nuevo. En esta sesion ya se ejecuto: para repetir la verificacion usar
el recibo existente y la fase `replay`.

```powershell
docker compose -f docker-compose.analytics.smoke.yml run --rm --no-deps initialize-child-offers-smoke
docker compose -f docker-compose.analytics.smoke.yml up -d analytics-smoke
Get-Content -Raw scripts/verify_analytics_docker.py | docker exec -i tesis_backend-backend-1 python - --phase seed --url http://analytics-smoke:8765 --receipt /tmp/analytics-04f1e3a-smoke-receipt.json
docker compose -f docker-compose.analytics.smoke.yml restart analytics-smoke
Get-Content -Raw scripts/verify_analytics_docker.py | docker exec -i tesis_backend-backend-1 python - --phase replay --url http://analytics-smoke:8765 --receipt /tmp/analytics-04f1e3a-smoke-receipt.json
```

Esperar salud `healthy` antes de los HTTP. El verificador exige PMUM deshabilitado,
configuracion C1 de ensayo y autenticacion; usa los clientes Python reales del
backend para Activities e historico. No consulta PostgreSQL ni crea perfiles.
El recibo de `/tmp` pertenece al contenedor backend y se pierde si este se recrea:
guardarlo antes si se necesita continuar ese mismo ensayo. Un ensayo nuevo usa
otra ruta de recibo y genera una serie ficticia nueva.

## Reemplazo local autorizado y ejecutado el 18/10/2026

La autorizacion requirio conservar intacto el volumen C1 original. Por eso el
reemplazo utilizo una restauracion en un tercer volumen nuevo, ademas de los dos
destinados a mensajes/ofertas. No se reutilizo el original para escrituras.

- Respaldo verificado: `local-backups/analytics-cutover-20261018-200138/historical.tgz`.
- SHA-256: `edf292649c248be4a8af6b32693c3ec5fc63b85370931968b7482c3830599542`.
- Manifiesto: `historical-manifest.json` en la misma carpeta. Verifica contenido,
  permisos y propietarios de las tres entradas originales.
- Imagen anterior preservada como `modera-analytics:historical-candidate` y
  `modera-analytics:rollback-before-04f1e3a`.
- C1 activo: `tesis_backend_historical_state_smoke_04f1e3a`, creado vacio,
  restaurado y comparado con el manifiesto antes del reemplazo.
- Inicializador infantil ejecutado una sola vez sobre el volumen nuevo.
- Solo se recreo `tesis_backend-analytics-1`; despues se reinicio una vez para
  comprobar persistencia. Backend y db conservaron IDs, StartedAt y RestartCount.
- Verificacion HTTP desde el backend, sin copiar archivos a ese contenedor ni
  acceder a PostgreSQL. Se conservaron URL y token originales.
- Activities, autenticacion y C1 SMOKE_ONLY aprobados. Seed ficticio exclusivamente
  C1 y replay tras reiniciar: mismo recibo; hashes de C1 y ambos SQLite identicos.
  Los SQLite pasaron `integrity_check` y conservaron cero recibos adultos/infantiles.
- Evidencia privada: `cutover-verification.json` y `live-smoke-receipt.json` en
  la carpeta del respaldo. No se generaron ofertas ni mensajes ficticios.

No repetir el reemplazo o inicializacion en un entorno ya usado. Para comprobar
salud/estado, usar `docker compose ... ps` y health desde la red interna. Si se
necesita repetir el replay sin escribir un archivo en backend:

```powershell
$verifier = Get-Content -Raw scripts/verify_analytics_docker.py
Get-Content -Raw local-backups/analytics-cutover-20261018-200138/live-smoke-receipt.json | docker exec -i tesis_backend-backend-1 python -B -c $verifier --phase replay --receipt-stdio
```

Este comando devuelve un recibo exclusivamente ficticio por stdout; no muestra el
token. `scripts/verify_analytics_cutover.py` registra la verificacion inicial y
rechaza repetirla si existe el reporte. Sus IDs de control corresponden a este
reemplazo concreto, no a despliegues futuros.

### Procedimiento de reemplazo para referencia

Estado observado antes del cambio: `tesis_backend-analytics-1` detenido (255),
imagen anterior `modera-analytics:historical-candidate`, ID
`sha256:6f43af239dc2d85c80ba1a8c66583c90a0a1569fb4578dd098e79a6f245345e5`.
El backend y PostgreSQL estaban activos y se conservaron sin reemplazar/reiniciar.

Plan exacto: respaldar C1 con Analytics detenido; conservar la imagen anterior;
crear los dos volumenes de capacidades nuevas y restaurar C1 en un tercer volumen; inicializar el coordinador una
vez; recrear solo `analytics` en `tesis_backend_default` con el mismo token y
alias `analytics`. El volumen C1 original queda intacto y fuera del contenedor.
`up --no-deps` evita iniciar/recrear backend o db y ejecutar sus migraciones.

Ejecutar lo siguiente SOLO despues de aprobar el reemplazo:

```powershell
docker stop tesis_backend-analytics-1
$backupDir = Join-Path (Get-Location) ('local-backups/analytics-' + (Get-Date -Format yyyyMMdd-HHmmss))
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
Copy-Item docker-compose.analytics.legacy.yml (Join-Path $backupDir 'compose.legacy.yml')
docker image tag sha256:6f43af239dc2d85c80ba1a8c66583c90a0a1569fb4578dd098e79a6f245345e5 modera-analytics:rollback-before-04f1e3a
docker run --rm --read-only --network none --user 0:0 --mount type=volume,source=tesis_backend_historical_state_smoke,target=/state,readonly --mount "type=bind,source=$backupDir,target=/backup" --entrypoint tar modera-analytics:04f1e3a -czf /backup/historical.tgz -C /state .
Get-FileHash (Join-Path $backupDir 'historical.tgz') -Algorithm SHA256
docker run --rm --read-only --network none --mount "type=bind,source=$backupDir,target=/backup,readonly" --entrypoint tar modera-analytics:04f1e3a -tzf /backup/historical.tgz
```

Respaldar el directorio completo conserva JSON, marcas de inicializacion y
archivos asociados. Los respaldos son privados y estan excluidos de Git.
Antes de continuar comprobar que ambos volumenes nuevos NO existian previamente
(`docker volume ls`); si ya existian, inspeccionar/respaldar su estado primero.

Antes de iniciar la imagen nueva, crear el volumen C1 activo vacio y restaurar el
respaldo. Si ya existe, no repetir ni sobrescribirlo. El helper verifica SHA-256,
contenido, permisos y propietarios y se niega a restaurar sobre datos existentes:

```powershell
docker volume create tesis_backend_historical_state_smoke_04f1e3a
Get-Content -Raw scripts/analytics_volume_snapshot.py | docker run --rm -i --read-only --network none --user 0:0 --mount type=volume,source=tesis_backend_historical_state_smoke_04f1e3a,target=/state --mount "type=bind,source=$backupDir,target=/backup,readonly" --entrypoint python modera-analytics:04f1e3a - restore
```

Este helper requiere `historical-manifest.json`. Para crear ambos archivos de
respaldo verificados, reemplazar los dos comandos `tar` anteriores por el helper
en fases `backup` y `verify`, montando el original como `/state,readonly` y el
directorio privado como `/backup`. Fue el procedimiento usado en este reemplazo.

```powershell
docker compose -f docker-compose.yml -f docker-compose.analytics.yml run --rm --no-deps initialize-child-offers
docker compose -f docker-compose.yml -f docker-compose.analytics.yml up -d --no-deps --no-build analytics
docker compose -f docker-compose.yml -f docker-compose.analytics.yml ps
```

Este ultimo `up` reemplaza el contenedor Analytics anterior: por eso se requiere
aprobacion. No se reemplazan backend ni PostgreSQL. No borrar volumenes.
Si falta una base usada, restaurarla: no ejecutar inicializacion para ocultar
su perdida. La CLI valida bases existentes y rechaza bases vacias/incompatibles;
no se ejecuta automaticamente al iniciar ni al reiniciar el servicio.

Verificar primero salud y acceso desde backend. Despues ejecutar `seed` y
`replay` con la URL por defecto y una ruta de recibo nueva:

```powershell
Get-Content -Raw scripts/verify_analytics_docker.py | docker exec -i tesis_backend-backend-1 python - --phase seed --receipt /tmp/analytics-04f1e3a-live-smoke.json
docker compose -f docker-compose.yml -f docker-compose.analytics.yml restart analytics
Get-Content -Raw scripts/verify_analytics_docker.py | docker exec -i tesis_backend-backend-1 python - --phase replay --receipt /tmp/analytics-04f1e3a-live-smoke.json
```

Este ensayo agrega exclusivamente una serie ficticia al C1 SMOKE_ONLY compartido.
Antes de autorizar trafico normal, revisar salud, permisos y recibos previos.

## Recuperacion

Si falla la nueva version, detener solo Analytics. Antes de restaurar, respaldar
tambien los tres volumenes posteriores al cambio usando el mismo procedimiento
con nombres de archivo diferentes. Evita perder operaciones nuevas.

Para mantener el estado original intacto y no sobrescribir volumenes usados,
restaurar el respaldo C1 en un volumen NUEVO con nombre unico:

```powershell
docker compose -f docker-compose.yml -f docker-compose.analytics.yml stop analytics
docker volume create tesis_backend_historical_recovery_04f1e3a
docker run --rm --read-only --network none --user 0:0 --mount type=volume,source=tesis_backend_historical_recovery_04f1e3a,target=/state --mount "type=bind,source=$backupDir,target=/backup,readonly" --entrypoint tar modera-analytics:04f1e3a -xzpf /backup/historical.tgz -C /state
```

Si ese volumen ya existe, elegir otro nombre nuevo; nunca restaurar encima de
datos existentes. El override legacy admite seleccionar ese volumen restaurado
sin editar archivos:

```powershell
$env:ANALYTICS_LEGACY_VOLUME = 'tesis_backend_historical_recovery_04f1e3a'
$env:ANALYTICS_LEGACY_IMAGE = 'modera-analytics:rollback-before-04f1e3a'
docker compose -f docker-compose.yml -f docker-compose.analytics.legacy.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.analytics.legacy.yml up -d --no-deps --no-build analytics
```

La ruta del fixture legacy es absoluta. Recuperacion/reemplazo y cambio de volumen
requieren autorizacion; la recuperacion ante fallo critico fue autorizada para
este reemplazo. Como el C1 original quedo intacto, puede recuperarse directamente
con el override legacy (sin variables `ANALYTICS_LEGACY_*`) y `up -d --no-deps
--no-build analytics`, conservando tambien el volumen nuevo para conciliar recibos.
No eliminar los volumenes nuevos ni el C1 original: permiten conciliar recibos
posteriores al respaldo. Nunca ejecutar `down -v`.

## Evidencia del ensayo aislado previo a la autorizacion

Resultado del ensayo previo del 18/10/2026 (el reemplazo posterior se informa arriba):

- Imagen `modera-analytics:04f1e3a` construida; ID
  `sha256:b685a3feb189baec88b132256cc54b3aefbdacfe33ec6974d483fb18aef5d9fa`.
- Compose principal, ensayo y recuperacion validados con `config --quiet`.
  Token configurado y coincidente entre backend y Analytics, sin publicarlo.
- 61 pruebas focalizadas aprobadas en 22,36 s. El primer intento en sandbox
  encontro permisos denegados en Temp; el intento con acceso paso completo.
- Desde `tesis_backend-backend-1`, clientes reales Activities e historico por
  HTTP interno a `analytics-smoke:8765`; autenticacion y PMUM deshabilitado.
- Reinicio real del contenedor aislado, replay C1 con respuesta original
  (solo cambia `replayed`), sin modificar bytes del repositorio.
- SHA-256 antes/despues del reinicio y replay: C1
  `95f082fa2f1d0170ea9d714134e0d7b026dbc6dd3eb5eace14390bc1ee538af2`,
  mensajes `092c3a3c81f66a4430efe9ba0c90bb5714e8e4d8f2bdab169ab22808c2321352`,
  coordinador `e1f478b90456d4810b60dae3080c6d124946950152b36f68691b10340edc3c30`.
- Los SQLite nuevos tienen cero recibos: se comprobo persistencia de sus bases,
  no operaciones adultas/infantiles. Inicializacion repetida valida el coordinador
  y conserva su hash. El primer comando creo la base solo en el volumen nuevo.
- En ese momento: servicio aislado saludable; backend activo; db saludable;
  Analytics original detenido y reemplazo pendiente de aprobacion. El estado
  compartido C1 no se monto en ese ensayo. La autorizacion y el reemplazo posterior
  ya se completaron segun la seccion correspondiente de este documento.

Las pruebas focalizadas usan SQLite temporal, no PostgreSQL:

```powershell
& '.venv/Scripts/python.exe' -m pytest -q -p no:cacheprovider tests/test_activity_suggestions.py tests/test_historical_submissions.py
```

La verificacion de reinicio en el entorno aislado cubre recibos C1 y conserva
los archivos SQLite nuevos. Los recibos de mensajes/ofertas todavia no se generan
desde el backend. Quedan pendientes los clientes, autorizacion por perfil,
evaluacion/eventos/historial, entrega idempotente y pruebas integrales de esas
capacidades; no se implementan en este cambio.
