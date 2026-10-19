"""Aplica solo 0015 tras validar respaldo y compara todos los datos preexistentes.

Uso de una sola vez para el despliegue local autorizado. No reemplaza servicios.
"""

import json
from pathlib import Path
import sys

from backup_postgres_local import DB, audit, docker, query, sha256


def main(backup):
    record = json.loads((backup / "backup-verification.json").read_text(encoding="utf-8"))
    if not record["restore_verified"]:
        raise RuntimeError("El respaldo no esta verificado")
    for name, details in record["files"].items():
        if sha256(backup / name) != details["sha256"]:
            raise RuntimeError("El respaldo cambio; no migrar")
    current = query(DB, "SELECT json_agg(version_num) FROM alembic_version")
    if current != ["20261017_0014"]:
        raise RuntimeError("Revision inicial inesperada; no migrar")
    if audit(DB, record["columns_before"]) != record["data_before"]:
        raise RuntimeError("Los datos cambiaron desde el respaldo; detener")
    configured = json.loads(docker("compose", "-f", "docker-compose.yml", "-f", "docker-compose.analytics.yml", "config", "--format", "json"))["services"]["backend"]["environment"]
    old = dict(item.split("=", 1) for item in json.loads(docker("inspect", "tesis_backend-backend-1", "--format", "{{json .Config.Env}}")))
    keys = ("ENVIRONMENT", "DATABASE_URL", "ANALYTICS_SERVICE_URL", "ANALYTICS_SERVICE_TOKEN",
            "ANALYTICS_TIMEOUT_SECONDS", "ADULT_AUTH_JWT_SECRET", "DEVICE_AUTH_JWT_SECRET")
    if any(str(configured.get(key, "")) != old.get(key, "") for key in keys):
        raise RuntimeError("La configuracion actual y propuesta difieren; detener")
    if configured["ENVIRONMENT"] != "development":
        raise RuntimeError("Solo desarrollo local")
    image = docker("image", "inspect", "tesis_backend-backend:adult-messages-04f1e3a", "--format", "{{.Id}}")
    if docker("image", "inspect", "tesis_backend-backend:latest", "--format", "{{.Id}}") != image:
        raise RuntimeError("Compose no usaria la imagen verificada")
    # La migracion se ejecuta en un proceso separado, sin recrear backend/db/analytics.
    docker("compose", "-f", "docker-compose.yml", "-f", "docker-compose.analytics.yml",
           "run", "--rm", "--no-deps", "backend", "alembic", "upgrade", "20261018_0015")
    revision = query(DB, "SELECT json_agg(version_num) FROM alembic_version")
    if revision != ["20261018_0015"]:
        raise RuntimeError("Revision final incorrecta; no reemplazar backend")
    after = audit(DB, record["columns_before"])
    unchanged = [row for row in after if row["table"] != "alembic_version"]
    expected = [row for row in record["data_before"] if row["table"] != "alembic_version"]
    if unchanged != expected:
        raise RuntimeError("Datos preexistentes modificados; detener y planear recuperacion")
    defaults = query(DB, "SELECT json_agg(json_build_object('table',table_name,'column',column_name,'nullable',is_nullable,'default',column_default,'type',udt_name) ORDER BY table_name,column_name) FROM information_schema.columns WHERE table_schema='public' AND table_name IN ('daily_usages','usage_snapshots') AND column_name IN ('last_night_activity_at','last_night_activity_status')")
    if len(defaults) != 4:
        raise RuntimeError("Faltan columnas; no reemplazar")
    for column in defaults:
        if column["column"].endswith("_at"):
            assert column["nullable"] == "YES" and column["default"] is None and column["type"] == "timestamptz"
        else:
            assert column["nullable"] == "NO" and "UNAVAILABLE" in column["default"] and column["type"] == "usage_metric_status"
    values = query(DB, "SELECT json_agg(t) FROM (SELECT 'daily_usages' AS table_name,count(*) AS rows,count(*) FILTER (WHERE last_night_activity_at IS NOT NULL OR last_night_activity_status <> 'UNAVAILABLE') AS unexpected FROM daily_usages UNION ALL SELECT 'usage_snapshots',count(*),count(*) FILTER (WHERE last_night_activity_at IS NOT NULL OR last_night_activity_status <> 'UNAVAILABLE') FROM usage_snapshots) t")
    if any(row["unexpected"] for row in values):
        raise RuntimeError("Valores iniciales incorrectos; no reemplazar")
    report = {"revision": revision, "existing_data_preserved": True, "columns": defaults,
              "existing_rows": values, "backend_image": image, "configuration_preserved": True}
    (backup / "migration-verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
