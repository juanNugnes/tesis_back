"""Verificacion del reemplazo local autorizado; no escribe en backend/PostgreSQL.

Ejecutar desde la raiz: python scripts/verify_analytics_cutover.py BACKUP_DIR.
Ante fallo critico restaura solo Analytics con el override legacy, que usa el
volumen original intacto. No elimina imagenes, respaldos ni volumenes.
"""

import json
from pathlib import Path
import subprocess
import sys
import time


BACKEND = "tesis_backend-backend-1"
ANALYTICS = "tesis_backend-analytics-1"
DB = "tesis_backend-db-1"
EXPECTED_IMAGE = "sha256:b685a3feb189baec88b132256cc54b3aefbdacfe33ec6974d483fb18aef5d9fa"
BASELINES = {
    BACKEND: ("d60a677806a66f7ba60d84598554d22ebb278618a042b0763b039ddbe0d50ad6", "2026-10-18T22:27:57.535751254Z"),
    DB: ("f35a27ef3ae28ba840037478be88e635102ec387c60f375560abe0a7152a71f0", "2026-10-18T22:27:51.349359731Z"),
}


def docker(*args, data=None):
    result = subprocess.run(["docker", *args], input=data, text=True, capture_output=True)
    if result.returncode:
        # No reproducir configuraciones/errores que pudieran contener secretos.
        raise RuntimeError("Docker fallo: " + args[0])
    return result.stdout.strip()


def state():
    code = """
import hashlib,json,sqlite3
from pathlib import Path
root=Path('/var/lib/modera')
hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and p.suffix in ('.json','.sqlite')}
receipts={}
for p in root.rglob('*.sqlite'):
    with sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True) as db:
        assert db.execute('pragma integrity_check').fetchone()[0]=='ok'
        receipts[str(p.relative_to(root))]=db.execute('select count(*) from receipts').fetchone()[0]
print(json.dumps({'hashes':hashes,'sqlite_receipts':receipts}))
"""
    return json.loads(docker("exec", ANALYTICS, "python", "-B", "-c", code))


def wait_healthy():
    deadline = time.monotonic() + 55
    while time.monotonic() < deadline:
        if docker("inspect", ANALYTICS, "--format", "{{.State.Health.Status}}") == "healthy":
            return
        time.sleep(1)
    raise RuntimeError("Analytics no alcanzo healthy")


def main(backup):
    report_path = backup / "cutover-verification.json"
    if report_path.exists():
        raise RuntimeError("El reporte ya existe; no repetir seed automaticamente")
    if docker("inspect", ANALYTICS, "--format", "{{.Image}}") != EXPECTED_IMAGE:
        raise RuntimeError("Imagen efectiva incorrecta")
    initial = state()
    manifest = json.loads((backup / "historical-manifest.json").read_text(encoding="utf-8"))
    if initial["hashes"]["historical/history.json"] != manifest["entries"]["history.json"]["sha256"]:
        raise RuntimeError("No se conservaron los bytes C1 previos al reemplazo")
    verifier = Path("scripts/verify_analytics_docker.py").read_text(encoding="utf-8")
    # El codigo y el recibo viajan por stdin/argv. No se copian archivos al backend.
    receipt = json.loads(docker("exec", "-i", BACKEND, "python", "-B", "-c", verifier,
                                "--phase", "seed", "--receipt-stdio"))
    with (backup / "live-smoke-receipt.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2)
    before_restart = state()
    docker("compose", "-f", "docker-compose.yml", "-f", "docker-compose.analytics.yml", "restart", "--timeout", "15", "analytics")
    wait_healthy()
    replay = json.loads(docker("exec", "-i", BACKEND, "python", "-B", "-c", verifier,
                               "--phase", "replay", "--receipt-stdio", data=json.dumps(receipt)))
    if replay != receipt:
        raise RuntimeError("No coincide la verificacion del recibo original")
    after_restart = state()
    if before_restart != after_restart:
        raise RuntimeError("Estado modificado durante reinicio/replay")
    if any(after_restart["sqlite_receipts"].values()):
        raise RuntimeError("Se generaron recibos adultos/infantiles inesperados")
    for name, (identifier, started) in BASELINES.items():
        actual = docker("inspect", name, "--format", "{{.Id}} {{.State.StartedAt}} {{.RestartCount}}")
        if actual != f"{identifier} {started} 0":
            raise RuntimeError("Backend/db no conserva estado de ejecucion original")
    health = docker("exec", BACKEND, "python", "-B", "-c",
                    "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5).status)")
    if health != "200":
        raise RuntimeError("Backend no saludable")
    report = {"passed": True, "image_id": EXPECTED_IMAGE, "url": "http://analytics:8765",
              "prior_c1_bytes_preserved": True, "historical_seed_replay": True,
              "before_restart": before_restart, "after_restart": after_restart,
              "backend_db_unchanged": True, "backend_health": 200,
              "scope": "local-development-smoke-only-not-calibrated"}
    with report_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report))


if __name__ == "__main__":
    # Herramienta de un solo reemplazo: repetirla no debe volver a sembrar datos
    # ni disparar una recuperacion por encontrar su reporte previo.
    if len(sys.argv) != 2:
        raise SystemExit("Uso: verify_analytics_cutover.py BACKUP_DIR")
    if (Path(sys.argv[1]) / "cutover-verification.json").exists():
        raise SystemExit("Verificacion ya registrada; no se realiza ninguna operacion")
    try:
        main(Path(sys.argv[1]))
    except Exception as error:
        print("CRITICAL: " + type(error).__name__ + "; ejecutando recuperacion de Analytics")
        docker("compose", "-f", "docker-compose.yml", "-f", "docker-compose.analytics.yml", "stop", "analytics")
        docker("compose", "-f", "docker-compose.yml", "-f", "docker-compose.analytics.legacy.yml",
               "up", "-d", "--no-deps", "--no-build", "--wait", "--wait-timeout", "60", "analytics")
        print("RECOVERY: Analytics legacy restaurado; todos los volumenes conservados")
        raise SystemExit(1)
