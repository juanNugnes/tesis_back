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
- `GET /api/v1/profiles/{profile_id}/representative-verification`
- `POST /api/v1/profiles/{profile_id}/consents`
- `GET /api/v1/profiles/{profile_id}/consents/current`
- `POST /api/v1/profiles/{profile_id}/consents/{consent_id}/revoke`
- `POST /api/v1/profiles/{profile_id}/experimental-pmum/responses`
- `GET /api/v1/profiles/{profile_id}/experimental-pmum/latest`
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

## Identidad de dispositivo y bootstrap

Un Profile nuevo se crea junto con una credencial bootstrap opaca de alta entropia:

```text
POST /api/v1/profiles
-> campos de ProfileDetailRead + bootstrap_token + bootstrap_expires_at

POST /api/v1/profiles/{profile_id}/devices
Authorization: Bearer <bootstrap_token>
-> campos de DeviceRead + access_token + token_type + expires_in
```

Los campos existentes de la respuesta de Profile permanecen en el nivel superior para reducir la ruptura del consumidor Android. El bootstrap dura 10 minutos, esta asociado a un solo Profile, se almacena como HMAC-SHA256 y se consume una sola vez dentro de la misma transaccion que crea el Device. `installation_id` sigue siendo solamente un identificador: nunca autoriza una operacion.

El Device JWT HS256 usa un secreto separado y contiene solamente `sub=Device.id`, `type=device_access`, `iat` y `exp`. La autorizacion obtiene `profile_id` desde el Device persistido. Desactivar el Device o el Profile invalida inmediatamente el acceso aunque el JWT no haya expirado.

La recuperacion o alta de otro Device conserva el flujo humano de seis digitos:

```text
Adult autorizado -> POST /profiles/{profile_id}/device-link-codes
Nuevo dispositivo -> POST /device-links/claim
-> Device + ProfileDetailRead + Device access token
```

El codigo `DEVICE_LINK` dura 10 minutos, se almacena como HMAC-SHA256 y se consume una sola vez. El claim no exige Device token porque el codigo temporal es su credencial de recuperacion. Si la instalacion ya corresponde al Profile, devuelve el Device existente y emite un token nuevo sin duplicarlo.

`last_seen_at` se inicializa al registrar o vincular el Device, pero esta version no lo actualiza automaticamente en cada request. La concurrencia se protege con locking y consumo condicional en PostgreSQL; falta una prueba concurrente de integracion contra PostgreSQL porque la suite usa SQLite. Los Profiles cuyo bootstrap nunca se completa requieren una futura politica operativa de limpieza.

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
| Adult-only | `GET /usage`, `GET /usage/{day}`, todos los `/analysis`, generar `/device-link-codes`, Context CRUD, `/auth/adult/me`, `/adult/profiles` | `AdultBearer` + link activo cuando hay `profile_id` |
| Device-only | `POST /usage`, generar `/adult-link-codes` | `DeviceBearer` + `Device.profile_id` coincidente; Usage ademas requiere autorizacion de monitoreo vigente |
| Adult-or-Device | Profile GET/PUT, Settings GET/PUT y ActivityPreferences GET/PUT | JWT con type explicito y autorizacion consultada en DB |
| Bootstrap | `POST /profiles`, registrar primer Device y `POST /device-links/claim` | Profile creation publica; registro con `DeviceBootstrapBearer`; claim con codigo temporal |
| Public | OTP request/verify y `/health` | Sin principal previo |

No se usa la ausencia de token como identidad infantil. Un principal autenticado sin acceso recibe `403`; un Device o Adult inactivo recibe `401`. `AdultProfileLink` se crea mediante el claim autenticado descripto a continuacion; ningun endpoint acepta `adult_id` o `profile_id` enviados por el adulto para crear esa relacion.

## Vinculacion AdultAccount - Profile

El dispositivo infantil autenticado puede generar un codigo temporal para vincular su Profile con una cuenta adulta:

```text
POST /api/v1/profiles/{profile_id}/adult-link-codes
-> codigo ADULT_PROFILE_LINK de seis digitos
```

El endpoint exige `DeviceBearer` y comprueba en DB que el Device pertenece al `profile_id` del path. Un Adult JWT, un Device de otro Profile o un `installation_id` sin credencial no autorizan la generacion.

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

Este flujo demuestra control del email y posesion del codigo temporal, y crea una relacion tecnica `AdultProfileLink`. No demuestra parentesco, tutela ni representacion legal. Con la configuracion segura por defecto, cada vinculacion nueva crea una `RepresentativeVerification` en estado `PENDING`, que puede actualizarse mediante el proceso manual interno descripto debajo.

## Verificacion manual y consentimiento de monitoreo

El flujo tecnico del MVP es:

```text
Adult autenticado
-> AdultProfileLink activo
-> revision manual de RepresentativeVerification
-> APPROVED
-> aceptacion de una version explicita del aviso de privacidad
-> Consent ACTIVE con purpose USAGE_MONITORING
-> Device habilitado para enviar nuevas metricas Usage
```

El adulto puede consultar su verificacion, otorgar consentimiento, consultar el consentimiento actual y revocarlo mediante los cuatro endpoints de Profile documentados arriba. Todos requieren `AdultBearer` y un `AdultProfileLink` activo. `adult_id` se deriva del JWT y nunca se acepta en path o body. Un grant activo identico es idempotente; una revocacion repetida devuelve el mismo recurso sin agregar otro evento. Volver a consentir despues de una revocacion crea una fila nueva y conserva la historia.

Las finalidades se almacenan normalizadas en `consent_purposes`. Como el MVP admite unicamente `USAGE_MONITORING`, el indice parcial unico sobre Adult/Profile equivale hoy a la regla Adult/Profile/Purpose. Incorporar una segunda finalidad requerira ampliar ese constraint sin cambiar el historial existente.

No existe un endpoint HTTP para aprobar o rechazar la verificacion. En desarrollo u operacion controlada se usa el helper interno:

```bash
python -m scripts.set_representative_verification <adult_id> <profile_id> APPROVED --verified-by manual-mvp
python -m scripts.set_representative_verification <adult_id> <profile_id> REJECTED --verified-by manual-mvp --notes "revision manual"
```

`verified_by` es un identificador operativo provisional hasta que exista identidad administrativa. La revision manual registra resultado y observacion minima; esta etapa no almacena copias de DNI, pasaporte, partidas, fotografias ni otros documentos.

Para simplificar exclusivamente pruebas y demostraciones del MVP puede habilitarse:

```text
AUTO_APPROVE_REPRESENTATIVE_VERIFICATION=true
```

El valor por defecto es `false`. Cuando esta habilitado, solamente un claim exitoso de `POST /adult/profile-links/claim` promueve una verificacion inexistente o `PENDING` a `APPROVED`, con `verified_by=automatic-mvp` y su AuditEvent. Una verificacion `APPROVED` no se duplica y una `REJECTED` nunca se sobrescribe automaticamente. OTP, login, generacion de codigos y `DEVICE_LINK` no disparan esta transicion.

La autoaprobacion no crea Consent ni modifica sus reglas: el adulto debe aceptar explicitamente el aviso de privacidad despues de vincularse. Esta opcion no constituye una verificacion real de representacion legal. Antes de produccion debe configurarse `AUTO_APPROVE_REPRESENTATIVE_VERIFICATION=false` y utilizarse un mecanismo real o manual de verificacion.

Para aceptar `POST /usage` debe existir al menos un adulto del Profile con vinculo activo, verificacion `APPROVED` y consentimiento `ACTIVE` para `USAGE_MONITORING`. No se exige que todos los adultos vinculados consientan. Un vinculo revocado o una verificacion que luego pasa a `REJECTED` deja de contar aunque el Consent permanezca `ACTIVE`.

La revocacion se aplica al momento de recepcion: el mismo Device JWT recibe `403 Monitoring authorization is not active` en todo POST posterior, incluso si el payload corresponde a una fecha anterior. No hay una excepcion para cargas offline historicas. La revocacion no borra Usage ni Analysis previos; los endpoints adultos de consulta y Analysis conservan su autorizacion historica actual. Supresion y retencion son flujos futuros separados.

Los cambios `APPROVED`, `REJECTED`, `CONSENT_GRANTED` y `CONSENT_REVOKED` generan `AuditEvent` con actor, Profile, resultado y metadata minima. El audit no guarda OTP, JWT, codigos temporales, documentos ni payloads de Usage, y no se usa como fuente de verdad para autorizar.

El email/OTP, `ADULT_PROFILE_LINK` y estos controles no prueban por si solos representacion legal. `RepresentativeVerification` representa un proceso manual MVP. Estos controles implementan requisitos tecnicos del MVP y no constituyen por si solos una evaluacion de cumplimiento legal integral.

## PMUM-SF experimental

La integracion PMUM-SF experimental es un flujo Adult-only separado de Usage, del historico y del Analysis existente. No usa metricas Android ni las once variables del modelo anterior. Un adulto con `AdultProfileLink` activo crea una revision parental inmutable mediante:

```http
POST /api/v1/profiles/{profile_id}/experimental-pmum/responses
Authorization: Bearer <adult JWT>
Content-Type: application/json

{
  "sex_code": 1,
  "weekday_category": 2,
  "weekend_category": 3,
  "respondent": "parent_or_caregiver",
  "reference_days": 30
}
```

`adult_id` se obtiene exclusivamente del JWT. `age` se copia del Profile bajo el lock que asigna la revision y ambos valores quedan persistidos como snapshot. `sex_code`, `weekday_category` y `weekend_category` admiten `null`; no se infieren desde Profile ni desde Usage. `reference_days` debe enviarse explicitamente.

Cada envio crea una fila nueva con `revision` incremental por Profile. La revision se confirma antes de invocar Python, por lo que una caida externa no elimina la respuesta parental. El backend envia al modelo unicamente `age`, `sex_code`, `weekday_category`, `weekend_category`, `respondent` y `reference_days`; no envia UUID, email, alias, Device, JWT ni otra identidad.

El cliente llama `POST /experimental/pmum-estimate` usando:

```text
EXPERIMENTAL_PMUM_SERVICE_URL=http://host.docker.internal:8765
EXPERIMENTAL_PMUM_SERVICE_TIMEOUT_SECONDS=5
```

En Docker, una instancia que corre en el host necesita una URL alcanzable desde el contenedor, por ejemplo `http://host.docker.internal:8765`. No hay retries agresivos ni autenticacion del prototipo local.

Los resultados persistidos distinguen `estimated`, `unavailable` y `technical_error`. `unavailable` es una abstencion valida y responde normalmente con score nulo. Timeout, conexion, HTTP no exitoso o payload invalido producen un resultado tecnico acotado, tambien con score nulo. `GET /experimental-pmum/latest` devuelve la ultima revision y su estimacion asociada.

`estimated_pmum_sf_mean` es un valor experimental crudo en escala 1-5. No se convierte en riesgo, alerta, diagnostico, severidad, recomendacion o bloqueo, y no esta aprobado para mostrarse a familias.

Permanecen pendientes el texto definitivo de las preguntas, los huecos de categorias horarias originales, el mapping de sexo de Modera a codigos 1/2, la validacion con poblacion argentina, la aprobacion para mostrar resultados a familias, el deploy final del servicio y la distribucion segura del `.joblib`. No se inventan reglas para estos contratos todavia abiertos.

## Cambios requeridos en Android

Android debe conservar `bootstrap_token` al crear el Profile, enviarlo como Bearer al registrar el primer Device y reemplazarlo por el `access_token` Device devuelto. Tambien debe guardar el token nuevo recibido por `/device-links/claim`, enviar `DeviceBearer` en Usage, Profile, Settings, ActivityPreferences y generacion de `adult-link-codes`, y borrar o renovar la credencial cuando el backend responda `401`.

No hay refresh token Device en esta etapa. La renovacion requiere un nuevo `DEVICE_LINK`; siguen pendientes una estrategia de refresh/rotacion, limpieza de Profiles huerfanos, UI de revocacion, rate limiting, almacenamiento seguro en Android y hardening productivo.

Siguen pendientes una estrategia de refresh/rotacion, acceso/rectificacion/supresion, retencion automatica, exportacion, identidad administrativa y una auditoria legal completa.

## Estado del análisis

No hay un algoritmo de ML real ni umbrales clínicos. `BaselineService` y `RulesService` son puntos de extensión deliberadamente mínimos. El futuro adaptador de ML probablemente cargará un artefacto de scikit-learn y deberá implementar el protocolo definido en `app/services/ml_service.py`.
