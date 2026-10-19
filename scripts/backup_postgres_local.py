"""Respaldo completo local y ensayo de restauracion en PostgreSQL temporal aislado.

No restaura, detiene ni modifica la base original. Los archivos privados quedan
en local-backups, fuera del contexto Docker. No imprimir SQL, credenciales o datos.
"""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time


DB = "tesis_backend-db-1"


def docker(*args, stdin=None, stdout=None):
    result = subprocess.run(["docker", *args], stdin=stdin, stdout=stdout or subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError("Docker fallo en " + args[0] + "; procedimiento detenido")
    return result.stdout.decode("utf-8").strip() if stdout is None else ""


def query(container, sql, *, user="postgres", database="tesis"):
    return json.loads(docker("exec", container, "psql", "-X", "-U", user, "-d", database,
                             "-qAt", "-v", "ON_ERROR_STOP=1", "-c", sql))


def columns(container):
    return query(container, "SELECT json_object_agg(table_name,cols) FROM (SELECT table_name,json_agg(column_name ORDER BY ordinal_position) AS cols FROM information_schema.columns WHERE table_schema='public' GROUP BY table_name) t")


def audit(container, mapping, *, user="postgres", database="tesis"):
    parts = []
    for table, names in sorted(mapping.items()):
        if not table.replace("_", "").isalnum() or any(not col.replace("_", "").isalnum() for col in names):
            raise RuntimeError("Identificador de tabla no admitido")
        projection = ",".join('"' + name + '"' for name in names)
        parts.append("SELECT jsonb_build_object('table','" + table + "','rows',count(*),'digest',md5(coalesce(string_agg(to_jsonb(t)::text,'' ORDER BY to_jsonb(t)::text),''))) AS body FROM (SELECT " + projection + ' FROM public."' + table + '") t')
    return query(container, "BEGIN TRANSACTION READ ONLY; SELECT json_agg(body ORDER BY body->>'table') FROM (" + " UNION ALL ".join(parts) + ") s; ROLLBACK;", user=user, database=database)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main(destination):
    destination.mkdir(parents=True, exist_ok=False)
    mapping = columns(DB)
    before = audit(DB, mapping)
    for filename, command in (
        ("tesis.dump", ("pg_dump", "-U", "postgres", "-d", "tesis", "--format=custom", "--no-password")),
        ("globals.sql", ("pg_dumpall", "-U", "postgres", "--globals-only", "--no-password")),
    ):
        with (destination / filename).open("xb") as output:
            docker("exec", DB, *command, stdout=output)
    with (destination / "tesis.dump").open("rb") as archive:
        toc = docker("exec", "-i", DB, "pg_restore", "--list", stdin=archive)
    (destination / "restore-list.txt").write_text(toc, encoding="utf-8")
    image = docker("inspect", DB, "--format", "{{.Image}}")
    temporary = "modera-pg-restore-" + str(int(time.time()))
    docker("run", "--rm", "-d", "--name", temporary, "--network", "none",
           "--tmpfs", "/var/lib/postgresql/data:rw", "--env", "POSTGRES_USER=restore_verifier",
           "--env", "POSTGRES_HOST_AUTH_METHOD=trust", image)
    ready = False
    for _ in range(30):
        probe = subprocess.run(["docker", "exec", temporary, "pg_isready", "-U", "restore_verifier", "-d", "postgres"], capture_output=True)
        if probe.returncode == 0:
            ready = True
            break
        time.sleep(1)
    if not ready:
        raise RuntimeError("PostgreSQL temporal no disponible; no migrar")
    with (destination / "globals.sql").open("rb") as roles:
        docker("exec", "-i", temporary, "psql", "-X", "-U", "restore_verifier", "-d", "postgres", "-v", "ON_ERROR_STOP=1", stdin=roles)
    with (destination / "tesis.dump").open("rb") as archive:
        docker("exec", "-i", temporary, "pg_restore", "-U", "restore_verifier", "-d", "postgres",
               "--clean", "--if-exists", "--exit-on-error", stdin=archive)
    restored = audit(temporary, mapping, user="restore_verifier", database="postgres")
    if before != restored or before != audit(DB, mapping):
        raise RuntimeError("Original/restauracion no coinciden; no migrar")
    report = {"database": "tesis", "environment": "local-development", "restore_verified": True,
              "columns_before": mapping, "data_before": before,
              "files": {name: {"sha256": sha256(destination / name), "bytes": (destination / name).stat().st_size}
                        for name in ("tesis.dump", "globals.sql")}}
    (destination / "backup-verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    # Solo este contenedor temporal, creado arriba con tmpfs y --rm. Ningun volumen se elimina.
    docker("stop", "--time", "15", temporary)
    print(json.dumps({"backup": str(destination.resolve()), "restore_verified": True,
                      "tables_verified": len(before), "files": report["files"]}))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
