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
- `POST /api/v1/profiles/{profile_id}/usage`
- `GET /api/v1/profiles/{profile_id}/usage`
- `GET /api/v1/profiles/{profile_id}/usage/{date}`
- `POST /api/v1/profiles/{profile_id}/analysis`
- `GET /api/v1/profiles/{profile_id}/analysis`
- `GET /api/v1/profiles/{profile_id}/analysis/latest`
- `GET /health`

## Estado del análisis

No hay un algoritmo de ML real ni umbrales clínicos. `BaselineService` y `RulesService` son puntos de extensión deliberadamente mínimos. El futuro adaptador de ML probablemente cargará un artefacto de scikit-learn y deberá implementar el protocolo definido en `app/services/ml_service.py`.
