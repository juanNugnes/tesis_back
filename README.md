# Backend de análisis de hábitos de uso

API modular para recibir métricas diarias agregadas de uso de dispositivos de perfiles infantiles de 6 a 12 años. El sistema evita almacenar nombres completos, documentos, mensajes, fotografías, contactos, ubicación precisa o contenido interno de aplicaciones. Sus niveles de riesgo son indicadores de comportamiento y **no son diagnósticos médicos ni psicológicos**.

## Arquitectura

Es una sola aplicación FastAPI organizada en capas: rutas HTTP → servicios → repositorios → modelos SQLAlchemy. `AnalysisService` orquesta `BaselineService`, `RulesService` y la interfaz `MLService`. Actualmente se inyecta `MockMLService`, que siempre produce un resultado neutral; esto permite reemplazarlo por un modelo real sin cambiar la API.

## Inicio con Docker

```bash
cp .env.example .env
docker compose up --build
```

La API queda disponible en `http://localhost:8000`, Swagger en `http://localhost:8000/docs` y OpenAPI en `http://localhost:8000/openapi.json`. El contenedor ejecuta `alembic upgrade head` antes de iniciar.

## Desarrollo local

Requiere Python 3.11 o superior y una instancia de PostgreSQL.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
copy .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

Si PostgreSQL corre en el host, cambie `db` por `localhost` en `DATABASE_URL`.

Para crear una migración futura:

```bash
alembic revision --autogenerate -m "descripcion"
alembic upgrade head
```

## Pruebas

```bash
pytest
```

Las pruebas usan una base SQLite temporal y cubren creación/validación de perfiles, registro y duplicación de métricas, historial y análisis mock.

## Endpoints

- `POST /api/v1/profiles`
- `GET /api/v1/profiles/{profile_id}`
- `PUT /api/v1/profiles/{profile_id}`
- `POST /api/v1/profiles/{profile_id}/devices`
- `POST /api/v1/profiles/{profile_id}/device-link-codes`
- `POST /api/v1/device-links/claim`
- `POST /api/v1/profiles/{profile_id}/adult-link-codes`
- `POST /api/v1/adult/profile-links/claim`
- `POST /api/v1/auth/adult/otp/request`
- `POST /api/v1/auth/adult/otp/verify`
- `GET /api/v1/auth/adult/me`
- `GET /api/v1/adult/profiles`
- `GET /api/v1/profiles/{profile_id}/settings`
- `PUT /api/v1/profiles/{profile_id}/settings`
- `GET /api/v1/profiles/{profile_id}/activity-preferences`
- `PUT /api/v1/profiles/{profile_id}/activity-preferences`
- `POST /api/v1/profiles/{profile_id}/contexts`
- `GET /api/v1/profiles/{profile_id}/contexts`
- `GET /api/v1/profiles/{profile_id}/contexts/active`
- `PUT /api/v1/profiles/{profile_id}/contexts/{context_id}`
- `DELETE /api/v1/profiles/{profile_id}/contexts/{context_id}`
- `POST /api/v1/profiles/{profile_id}/usage`
- `GET /api/v1/profiles/{profile_id}/usage`
- `GET /api/v1/profiles/{profile_id}/usage/{date}`
- `POST /api/v1/profiles/{profile_id}/analysis`
- `GET /api/v1/profiles/{profile_id}/analysis`
- `GET /api/v1/profiles/{profile_id}/analysis/latest`
- `GET /health`

## Contexto informado por el adulto

Los contextos son datos temporales del producto asociados a un perfil. No modifican el analisis ni generan recomendaciones automaticamente. Los tipos admitidos son `VACATION`, `ILLNESS`, `SCHOOL_ACTIVITY`, `ROUTINE_CHANGE` y `OTHER`.

Ejemplo para crear un contexto:

```http
POST /api/v1/profiles/{profile_id}/contexts
Content-Type: application/json

{
  "type": "VACATION",
  "start_date": "2026-10-01",
  "end_date": "2026-10-07",
  "description": null
}
```

La respuesta contiene el recurso persistido:

```json
{
  "id": "<uuid>",
  "profile_id": "<uuid>",
  "type": "VACATION",
  "start_date": "2026-10-01",
  "end_date": "2026-10-07",
  "description": null,
  "created_at": "<datetime>",
  "updated_at": "<datetime>"
}
```

`GET /api/v1/profiles/{profile_id}/contexts/active` devuelve los contextos cuya fecha de inicio ya llego y que no tienen fecha de fin o todavia no finalizaron.

## Preferencias y necesidades para actividades

Cada perfil puede tener un recurso actual de preferencias. `PUT /api/v1/profiles/{profile_id}/activity-preferences` funciona como upsert y como actualizacion parcial. `GET` devuelve un estado neutral sin crear una fila cuando todavia no existe configuracion.

Intereses admitidos: `DRAWING_CREATING`, `READING_STORIES`, `MUSIC`, `GAMES_CHALLENGES`, `OUTDOORS`, `MOVEMENT`, `BUILDING`, `WRITING`, `IMAGINATIVE_PLAY`.

Tipos de adaptacion: `NONE`, `TEMPORARY`, `PERMANENT`.

Consideraciones admitidas: `MOBILITY`, `VISION`, `HEARING`, `ATTENTION_CONCENTRATION`, `ARMS_HANDS`, `LEGS`, `COMMUNICATION`, `SENSORY_SENSITIVITY`, `TEMPORARY_PHYSICAL_DISCOMFORT`, `OTHER`.

Caracteristicas a evitar: `RUNNING`, `JUMPING`, `PROLONGED_STANDING`, `ARMS_HANDS_USE`, `VISUAL_PRECISION`, `LOUD_SOUNDS`, `HIGH_STIMULATION`, `PROLONGED_CONCENTRATION`.

Ejemplo con intereses solamente:

```json
{
  "interests": ["DRAWING_CREATING", "MUSIC"]
}
```

Ejemplo con una necesidad temporal:

```json
{
  "interests": ["BUILDING"],
  "adaptation_type": "TEMPORARY",
  "considerations": ["LEGS"],
  "activities_to_avoid": ["RUNNING", "JUMPING"],
  "additional_notes": "Por unos dias conviene evitar actividades de impacto.",
  "valid_until": "2026-10-15"
}
```

Ejemplo sin restricciones:

```json
{
  "interests": ["BUILDING", "GAMES_CHALLENGES"],
  "adaptation_type": "NONE",
  "considerations": [],
  "activities_to_avoid": [],
  "additional_notes": null,
  "valid_until": null
}
```

Las colecciones no aceptan duplicados. `additional_notes` admite hasta 500 caracteres. Al cambiar `adaptation_type` a `NONE`, el backend limpia `valid_until` y conserva las demas preferencias declaradas.

## Identidad de dispositivo y vinculacion temporal (MVP)

Para un perfil nuevo, Android conserva su identificador de instalacion y registra el dispositivo en dos pasos:

```text
POST /api/v1/profiles
-> profile_id
POST /api/v1/profiles/{profile_id}/devices
```

Si una instalacion pierde el `profile_id`, el flujo provisional de recuperacion es:

```text
POST /api/v1/profiles/{profile_id}/device-link-codes
-> codigo temporal
POST /api/v1/device-links/claim
-> Device + ProfileDetailRead
```

El codigo `DEVICE_LINK` tiene seis digitos, dura 10 minutos, se almacena como HMAC-SHA256 y se consume una sola vez. No es una identidad permanente. Su generacion requiere un JWT adulto y un `AdultProfileLink` activo; el claim sigue siendo publico porque el dispositivo aun esta recuperando su identidad.

`last_seen_at` se inicializa al registrar o vincular el Device, pero esta version no lo actualiza automaticamente en cada request. La concurrencia del claim se protege con locking y consumo condicional en PostgreSQL; falta una prueba de integracion concurrente contra PostgreSQL porque la suite usa SQLite.

## Autenticacion y autorizacion adulta

El flujo adulto MVP usa un OTP numerico de seis digitos y un JWT HS256. El OTP dura 10 minutos, tiene un maximo de 5 intentos y se almacena como HMAC, nunca en texto plano. El JWT contiene solamente `sub`, `type=adult_access`, `iat` y `exp`; los perfiles autorizados se consultan en base de datos en cada operacion.

```text
POST /api/v1/auth/adult/otp/request
POST /api/v1/auth/adult/otp/verify -> Bearer token
GET  /api/v1/auth/adult/me
GET  /api/v1/adult/profiles
```

`ADULT_AUTH_JWT_SECRET` es obligatorio y debe tener al menos 32 caracteres. `EXPOSE_DEV_OTP=true` incluye `dev_code` unicamente cuando `ENVIRONMENT=development`; debe permanecer desactivado fuera del desarrollo. No existe envio real de email todavia: `OtpDeliveryService` es el punto de integracion futuro.

Clasificacion actual de acceso:

| Clase | Endpoints | Estado |
|---|---|---|
| Adult-only | `GET /usage`, `GET /usage/{day}`, todos los `/analysis`, generar `/device-link-codes`, `/auth/adult/me`, `/adult/profiles` | Bearer adulto + link activo cuando hay `profile_id` |
| Device-only | `POST /usage` | Abierto temporalmente; **DEVICE AUTH REQUIRED** |
| Shared | Profile GET/PUT, Settings, ActivityPreferences y Context CRUD | Abiertos para no romper Android; requieren principal Adult o Device en una etapa futura |
| Bootstrap | `POST /profiles`, registrar Device y `POST /device-links/claim` | Abiertos por el flujo actual; ownership/bootstrap definitivo pendiente |

No se usa la ausencia de token como identidad infantil. Los endpoints compartidos siguen abiertos de forma explicita hasta implementar credenciales Device. Un adulto autenticado sin link recibe `403` tanto para un Profile ajeno como para un UUID inexistente. `AdultProfileLink` se crea mediante el claim autenticado descripto a continuacion; ningun endpoint acepta `adult_id` o `profile_id` enviados por el adulto para crear esa relacion.

## Vinculacion AdultAccount - Profile

El dispositivo infantil puede generar un codigo temporal para vincular el Profile con una cuenta adulta:

```text
POST /api/v1/profiles/{profile_id}/adult-link-codes
-> codigo ADULT_PROFILE_LINK de seis digitos
```

Este endpoint es un bootstrap provisional sin autenticacion porque todavia no existe una credencial Device verificable. Conocer o enviar un `device_id` o `installation_id` no prueba control del dispositivo. **DEVICE AUTH REQUIRED BEFORE PRODUCTION.**

Despues de validar su email mediante OTP, el adulto reclama el codigo con su JWT:

```http
POST /api/v1/adult/profile-links/claim
Authorization: Bearer <adult JWT>
Content-Type: application/json

{
  "code": "728341"
}
```

El DTO admite solamente `code`: `adult_id` se obtiene del JWT validado y `profile_id` se resuelve desde el codigo almacenado. El codigo dura 10 minutos, se guarda como HMAC-SHA256, tiene un solo uso y no puede utilizarse para el purpose `DEVICE_LINK`. El claim bloquea la fila del codigo y confirma en una unica transaccion tanto el `AdultProfileLink` como `used_at`.

Si el vinculo activo ya existe, el claim es idempotente, consume el nuevo codigo y devuelve el mismo vinculo. Si el vinculo fue revocado, responde `409` y no consume el codigo; la reautorizacion se definira junto con consentimiento y verificacion de representante. Un claim exitoso aparece inmediatamente en `GET /api/v1/adult/profiles` y habilita los endpoints adult-only con el mismo JWT, porque la autorizacion consulta la base de datos en cada request.

Este flujo demuestra control del email y posesion del codigo temporal, y crea una relacion tecnica `AdultProfileLink`. **Representative verification is not implemented in this stage.** No demuestra parentesco, tutela ni representacion legal.

Esta etapa implementa autenticacion y autorizacion adulta basica, no cumplimiento legal integral. Siguen pendientes consentimiento y su revocacion, verificacion de representante, bloqueo de ingestion, Device authentication, acceso/rectificacion/supresion, retencion, auditoria completa, rate limiting y hardening productivo.

## Estado del análisis

No hay un algoritmo de ML real ni umbrales clínicos. `BaselineService` y `RulesService` son puntos de extensión deliberadamente mínimos. El futuro adaptador de ML probablemente cargará un artefacto de scikit-learn y deberá implementar el protocolo definido en `app/services/ml_service.py`.
