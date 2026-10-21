# Coordinador infantil: integración de prueba aislada

Fuente de verdad: `CHILD_OFFERS_HTTP_V1_2026-10-17.md` y
`CHILD_COORDINATOR_CONTRACT_2026-10-14.md` del paquete Analytics `04f1e3a`.
No se modifica la política ni el catálogo. Las ofertas automáticas no se guardan
como `ActivityOffer` voluntarios ni incrementan `ActivitySelection`.

## Activación controlada

La API de prueba responde 404 salvo que `ENVIRONMENT=development`,
`CHILD_OFFERS_TEST_ENABLED=true` y `CHILD_OFFERS_TEST_PROFILE_IDS` contenga el UUID
exacto del perfil. El alias del perfil debe empezar por `TEST_CHILD_OFFERS_`.
Crear un perfil **ficticio** nuevo y vincularlo mediante el flujo real DeviceBearer
y AdultBearer; no agregar a la lista un perfil infantil existente. No usar esta
API para oportunidades reales. No hay generación automática ni scheduler.
El volumen SQLite de Analytics debe ser independiente y haber sido inicializado
explícitamente según su handoff; el backend nunca lo inicializa ni sustituye.
El UUID de perfil, no un ID numérico legado, se usa como identificador global
en esta prueba. Antes de uso familiar debe acordarse con Matías si se requiere
además un prefijo estable de cuenta en todos los IDs.
El propio Analytics conserva ofertas, eventos y recibos; PostgreSQL conserva
solamente sobres, estado de envío y recibo de transporte en
`child_offer_operations` (migración `20261020_0017`).

## Rutas de prueba

- `POST /api/v1/profiles/{profile_id}/child-offers/evaluate`: DeviceBearer del
  dispositivo vinculado. El body es el sobre exacto `request_id` + `evidence`
  del contrato. `profile_id` debe ser el UUID del perfil; `series_id` e
  `interval_id` deben empezar por `{profile_id}.`. Todas las evidencias deben
  declararse explícitamente, sin valores favorables implícitos.
- `POST /api/v1/profiles/{profile_id}/child-offers/events`: DeviceBearer del mismo
  perfil; el body es el sobre exacto de evento de Analytics. Enviar `delivered`
  únicamente después de presentación real. No inventar acuses ni respuestas.
- `GET /api/v1/profiles/{profile_id}/child-offers/history`: AdultBearer de adulto
  vinculado y con verificación aprobada. Una lectura no autoriza presentación.

Reintentar el mismo POST con **idéntico** `request_id` y body (incluido `now`).
Si se pierde la respuesta, la operación queda `PENDING` en PostgreSQL y se
recupera repitiendo esa petición tras reiniciar. No crear otra operación del
mismo perfil mientras exista una pendiente o en conflicto. Un `409` de Analytics
queda `CONFLICT` y requiere conciliación manual con su historial y recibos; no
se genera otro ID para eludirlo. Una respuesta `503` no autoriza reinicializar
SQLite. Una oferta generada no equivale a entregada; antes de mostrarla se
requiere evaluación fresca, vigencia y deduplicación por oferta/canal.

## Pendiente para Android y captura real

La demo Android en debug no consume estas rutas. Para una integración futura,
Android necesitará identidad estable de intervalo, evidencia de cobertura
continua, asignación inequívoca, estado de pantalla/juego/escuela, zona horaria
y descanso resueltos, preferencias/elegibilidad explícitas, presentación
deduplicada por `offer_id` + canal y acuses durables con hora real. Ni métricas
diarias ni la cola FIFO actual prueban esos hechos. Hasta contar con ellos y
revisar permisos, estas rutas deben seguir limitadas a pruebas ficticias.

## Validación

`tests/test_child_offers.py` usa transporte simulado y SQLite temporal del
backend. `tests/test_child_offers_real_analytics.py` es opt-in: requiere
`ANALYTICS_SOURCE_DIR` apuntando al paquete extraído y usa el coordinador HTTP
real en proceso con un archivo SQLite **temporal nuevo**, inicializado solo por
la prueba. No toca el volumen ni el contenedor Analytics desplegado. Incluye
un recorrido backend HTTP → Analytics HTTP y reapertura del almacén temporal.
La concurrencia entre instancias FastAPI y PostgreSQL aún requiere una prueba
específica en una base desechable antes de habilitar operaciones reales.
