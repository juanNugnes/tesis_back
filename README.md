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

## Estado del análisis

No hay un algoritmo de ML real ni umbrales clínicos. `BaselineService` y `RulesService` son puntos de extensión deliberadamente mínimos. El futuro adaptador de ML probablemente cargará un artefacto de scikit-learn y deberá implementar el protocolo definido en `app/services/ml_service.py`.
