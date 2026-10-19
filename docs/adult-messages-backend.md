# Mensajes adultos: consulta MVP

La integracion consulta el historial persistido en Analytics `04f1e3a`.
No ejecuta evaluaciones ni copia tarjetas en PostgreSQL. No agrega migraciones,
scheduler, avisos push, ACK de lectura ni cambios a Android.

## Endpoint para Android

```http
GET /api/v1/profiles/{profile_id}/adult-messages/history
Authorization: Bearer <access_token_del_adulto>
```

Requiere AdultBearer valido, vinculo activo con ese perfil, perfil activo y
verificacion del representante APPROVED. Se reutiliza
`require_verified_profile_access`; un token DeviceBearer no sirve. El backend
decide el identificador autorizado y comprueba ademas que todas las tarjetas
devueltas por Analytics pertenezcan a ese perfil. No acepta un profile_id del
cuerpo ni devuelve resultados parciales si falla esa comprobacion.

Respuesta 200: contrato original `{"versions":[{"version":1,"card":{...}}]}`.
Historial vacio: `{"versions":[]}`. Incluye `Cache-Control: no-store`.
La ausencia de tarjetas no es un error ni confirma que ya se evaluaron datos.

Android usa el token adulto existente y su URL de backend habitual; nunca llama
directamente a Analytics ni recibe su credencial de servicio. Para la bandeja,
agrupar por `card.card_id` y elegir el mayor `version`, no la ultima posicion ni
solo las tarjetas ACTIVE. Mostrar titulo, texto, recomendacion y fecha;
`updated_date` prevalece al presentar correcciones si esta presente. Mantener
estados RESOLVED/WITHDRAWN/CORRECTED_HISTORY e incertidumbre sin reinterpretarlos.
No marcar `CANDIDATE_NOT_DELIVERED` como leido: no existe ACK en esta integracion.
La respuesta conserva versiones anteriores y campos de medicion/configuracion.

Errores:

| HTTP backend | Situacion |
| --- | --- |
| 401 | AdultBearer ausente, invalido, vencido o token de dispositivo |
| 403 | Sin vinculo activo, perfil inactivo o verificacion no aprobada |
| 422 | Identificador de perfil con formato invalido |
| 503 | Servicio/token no configurado, conexion fallida, HTTP 5xx/429 de Analytics |
| 504 | Timeout de Analytics |
| 502 | Contrato/JSON invalido, perfil ajeno, redireccion o rechazo HTTP no transitorio |

Los errores de Analytics usan `detail={code,message,retryable}` con mensajes
locales; no se reenvian cuerpos, cabeceras o excepciones privadas del upstream.
No se confunde indisponibilidad con historial vacio. GET puede repetirse sin
crear evaluaciones ni duplicar tarjetas; no hay reintentos automaticos. La UI
puede ofrecer volver a intentar cuando `retryable=true`.

## Contratos y configuracion utilizados

Fuentes de la entrega `MODERA_ANALYTICS_INTEGRACION_04f1e3a`:

- `docs/integration/ANALYTICS_SERVICE_HANDOFF_2026-10-17.md`.
- `docs/integration/BACKEND_ADULT_MESSAGES_CONTRACT_2026-10-13.md`.
- `docs/adult_messages/CONTRACT_AND_HANDOFF.md`.
- Ejemplo oficial ficticio `docs/integration/examples/adult_messages_2026-10-13/history.fictitious.json`.

Solo se llama a GET `/v1/adult-messages/profiles/{profile_id}/history`.
`AdultMessageHistory`, `AdultMessageVersion`, `AdultMessageCard` y
`AdultMessageStatistics` tipan los campos del candidato v1, incluidos estadisticas
nulas, fechas de correccion opcionales y estados documentados. Campos adicionales,
versiones duplicadas, perfiles ajenos y JSON con claves duplicadas/no finitos
se rechazan como error de contrato. No se recalculan cifras ni textos.

Se reutilizan `ANALYTICS_SERVICE_URL`, `ANALYTICS_SERVICE_TOKEN` y
`ANALYTICS_TIMEOUT_SECONDS`. No hay variables nuevas. Persistencia en el volumen
SQLite adulto existente de Analytics; solo lectura desde esta API.

## Datos existentes y generacion pendiente

POST `/v1/adult-messages/evaluate` exige exactamente `request_id`, `profile_id`,
`series_id`, `time_zone`, `evaluation_date`, `assignment_verified`, `shared_phone`,
`context_declared`, `days` y `c1_evidence_ids`. Cada dia exige `date`, `revision`,
`minutes`, `observed` y `quality_verified`. Este POST no se implementa ni se llama.

| Evidencia | Situacion actual del backend |
| --- | --- |
| Identidad/autorizacion | UUID de perfil/dispositivo, vinculos y verificaciones existentes |
| Serie y calendario | HistoricalSubmission conserva binding, zona IANA, fecha local, version de medicion, series_key e intervalos; usa device_id como assignment_id |
| Minutos observados v2 | Payload historico preparado conserva value/status y provenance; los agregados DailyUsage no sustituyen esta representacion |
| Recibos C1 | analytics_evaluation_id y respuesta original si la submission fue procesada; validar pertenencia a la misma serie antes de referenciarlos |
| Contextos | ProfileContext registra declaraciones, pero su ausencia no acredita por si sola context_declared=false |
| Calidad diaria | No hay cierre respaldado que permita derivar quality_verified=true; coverage/OBSERVED/HTTP 200 no lo acreditan |
| Atribucion y telefono compartido | Binding tecnico no demuestra assignment_verified; no existe una declaracion shared_phone suficiente para el contrato |
| Revisiones y serie activa | No hay protocolo adulto de revisiones diarias crecientes ni seleccion persistida de una serie activa por perfil |
| Evaluacion durable | No hay trabajo semanal con payload congelado, request_id estable, orden por perfil y conciliacion de respuestas perdidas |

No se acredita que existan 28 dias reales completos, observados y verificados,
necesarios para una primera apertura. Los snapshots agregados o metricas manuales
no deben completar faltantes F12 ni convertir ausencia en cero. Los datos diarios
deben proceder de la serie v2 y su cierre verificable; las referencias C1 son
complementarias y pueden ser `[]`: CHANGE no dispara una tarjeta, PMUM tampoco.
El C1 SMOKE_ONLY actual no se presenta como configuracion calibrada.

Antes de generar automaticamente: cerrar esas garantias de evidencia/identidad,
revisiones y serie; conservar payload y estado de envio antes del HTTP; ejecutar
los lunes sin retroceder fechas; respetar 366 dias/16384 bytes; reutilizar el
mismo request y cuerpo en retries y conciliar 409 sin inventar IDs. Se conserva
la idempotencia y el estado en Analytics. No se necesita duplicar mensajes en DB.

## Verificacion

`tests/test_adult_messages.py` prueba ruta ASGI, autenticacion adulta y permisos
con SQLite temporal, cliente real y transporte Analytics simulado. La fixture
de tarjetas esta identificada como ficticia y procede del ejemplo de la entrega.
No se siembran evaluaciones en servicios reales.

```powershell
& '.venv/Scripts/python.exe' -m pytest -q -p no:cacheprovider tests/test_adult_messages.py
& '.venv/Scripts/python.exe' -m pytest -q -p no:cacheprovider
```

Tambien se comprobo el cliente desde un contenedor temporal con la imagen del
backend y el codigo nuevo montado de solo lectura contra `http://analytics:8765`:
historial vacio para UUID de ensayo y GET repetido identico, cero evaluaciones.
En ese ensayo previo no se reiniciaron backend, Analytics o PostgreSQL.
Posteriormente se completo el [despliegue local autorizado](backend-local-deployment.md),
incluida migracion 0015 con respaldo verificado: el GET ya esta disponible en el
backend Docker y paso las pruebas con AdultBearer real, historial vacio y rechazo
de accesos no autorizados. PostgreSQL y Analytics no se reiniciaron.

Resultado local del 18/10/2026: prueba focal inicial 36/36; suite completa final
285/285 en 167,14 s, incluidos los 37 casos finales de este modulo. La consulta
GET contra Analytics real tambien paso. Sin cambios a DB/migraciones o servicios
existentes y sin evaluaciones enviadas al servicio real.
