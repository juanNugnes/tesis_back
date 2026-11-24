# Evaluación controlada de mensajes adultos

Esta etapa permite probar manualmente `POST /v1/adult-messages/evaluate` desde el
backend. No es un productor semanal, no lee `/usage`, no programa tareas y no
habilita datos reales.

## Fixture admitida

El comando acepta exclusivamente un wrapper JSON con estos campos:

```json
{
  "fixture_type": "modera-adult-messages-controlled-v1",
  "fictional": true,
  "fixture_id": "fictitious-identificador-estable",
  "payload": { "request_id": "...", "profile_id": "..." }
}
```

`payload` usa exactamente el contrato de Analytics: `request_id`, `profile_id`,
`series_id`, `time_zone`, `evaluation_date`, `assignment_verified`,
`shared_phone`, `context_declared`, `days` y `c1_evidence_ids`. No se envían los
campos externos del wrapper. La integración controlada exige los 28 días
consecutivos anteriores a un lunes, en orden: 14 de referencia y 14 recientes.
`c1_evidence_ids=[]` es válido.

Los booleanos de asignación, calidad, teléfono compartido y contexto son
afirmaciones explícitas de la fixture ficticia. Este flujo no los deriva de
snapshots, F12/C1 ni configuraciones actuales.

## Persistencia y reintento

`adult_message_evaluation_jobs` conserva el cuerpo validado antes del HTTP, su
SHA-256, fixture, perfil, serie, lunes, estado, intentos y respuesta. Los estados
son `PENDING`, `SENDING`, `PROCESSED`, `CONFLICT` y `TECHNICAL_ERROR`.

Un timeout o una respuesta perdida queda reintentable y conserva el mismo
`request_id` y el mismo payload. Un trabajo `SENDING` también se considera
incierto: sólo se reenvía con recuperación explícita. Un 409 queda como
`CONFLICT`; no se genera otra identidad para evadirlo.

Las correcciones usan otra fixture y otro `request_id`. Para el mismo lunes y
serie deben incrementar la revisión de al menos un día; cambiar contenido con
la misma revisión o disminuirla se rechaza antes del transporte.

## Comando local

Requiere `ENVIRONMENT=development`, base y Analytics locales, y confirmación
explícita. El perfil UUID de la fixture debe existir en esa base ficticia.

```powershell
python -m scripts.evaluate_adult_message_fixture `
  --fixture tests/fixtures/adult_message_evaluation.fictitious.json `
  --confirm-fictitious-data
```

Recuperación de una operación incierta:

```powershell
python -m scripts.evaluate_adult_message_fixture `
  --retry-job UUID_DEL_TRABAJO `
  --confirm-fictitious-data
```

No existe una ruta pública para estas operaciones.

## Historial adulto

Las tarjetas siguen consultándose mediante
`GET /api/v1/profiles/{profile_id}/adult-messages/history`. Analytics conserva
versiones por `card_id`; el consumidor debe elegir la versión mayor. El replay
de un recibo antiguo puede devolver una tarjeta `ACTIVE` histórica, por lo que
no debe reemplazar una versión posterior `WITHDRAWN`.

`INSUFFICIENT_DATA` y `NO_CARD` con `card=null` quedan auditados en el trabajo y
en Analytics, pero no agregan una tarjeta al historial adulto. Una lista vacía
no demuestra que nunca se haya ejecutado una evaluación.

## Pendiente para datos reales

Faltan una política y evidencia durable para cierre/calidad diaria, asignación
durante el período, uso compartido y contexto. También faltan la selección de
serie real, construcción semanal, scheduler y operación de recuperación. Nada
de esta etapa autoriza a promover `MEASURED`, `OBSERVED`, HTTP 200 o fecha pasada
a evidencia verificada.
