# Productor semanal real verificado

`AdultMessageWeeklyProducer` conecta el diagnóstico de
`AdultMessageWindowService` con el transporte durable existente de mensajes
adultos. No consulta `/usage`, no modifica F12/C1 y no programa ejecuciones.

## Condiciones de producción

El productor recibe un perfil y un lunes local. Si el diagnóstico tiene al
menos un bloqueo, devuelve el diagnóstico completo y no crea un trabajo. Una
ventana elegible debe tener una única serie y 28 días consecutivos con:

- evidencia F12 `AVAILABLE`, revisión 1 y preparación válida;
- minutos `OBSERVED` u `OBSERVED_ZERO` obtenidos de la representación F12;
- `quality_verified=true` bajo `f12-operational-quality-v1`, con auditoría
  vinculada a evidencia y preparación;
- declaraciones históricas completas de asignación, teléfono compartido y
  contexto;
- consentimiento histórico de monitoreo durante toda la ventana.

Los identificadores C1 procesados de la misma ventana se incluyen cuando
existen. Una lista vacía sigue siendo válida. `/usage` no participa.

## Persistencia e idempotencia

Los trabajos `REAL_VERIFIED` usan la tabla
`adult_message_evaluation_jobs`, al igual que el transporte ficticio, pero no
tienen `fixture_id`. Conservan:

- payload exacto y su SHA-256;
- referencias de las 28 evidencias y preparaciones;
- hashes de contenido original y de cada auditoría de calidad;
- revisiones y registros históricos de declaraciones/consentimiento;
- `window_fingerprint` y `request_id` derivados de esa trazabilidad.

Repetir la misma ventana devuelve el mismo trabajo. El envío y los reintentos
usan `AdultMessageEvaluationService`, por lo que un resultado incierto conserva
el mismo cuerpo y `request_id`. Una corrección F12 o una nueva declaración no se
promueve silenciosamente: la ventana queda bloqueada o la política de revisiones
rechaza otra evaluación para el mismo lunes.

## Ejecución manual local

El comando exige `ENVIRONMENT=development`, hosts locales para PostgreSQL y
Analytics, y confirmación explícita:

```powershell
python -m scripts.evaluate_real_adult_message_window `
  --profile-id UUID_DEL_PERFIL `
  --evaluation-date 2026-02-02 `
  --confirm-real-verified-window
```

Si la ventana no es elegible, imprime `BLOCKED` y los bloqueos sin crear un
trabajo ni llamar a Analytics. Para reconciliar explícitamente un timeout o un
trabajo `SENDING` incierto:

```powershell
python -m scripts.evaluate_real_adult_message_window `
  --retry-job UUID_DEL_TRABAJO `
  --confirm-real-verified-window
```

No existe endpoint público ni scheduler para este productor.
