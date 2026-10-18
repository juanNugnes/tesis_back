"""Smoke HTTP desde el backend, sin acceder a PostgreSQL. Solo C1 SMOKE_ONLY.

Ejecutar por stdin dentro del backend; seed guarda datos ficticios en Analytics
y un recibo local. replay exige el mismo recibo despues de reiniciar Analytics.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import httpx

from app.core.config import get_settings
from app.services.activity_selector_gateway import ActivityEligibility
from app.services.activity_selector_http_gateway import HttpActivitySelectorGateway
from app.services.historical_analytics_client import HistoricalAnalyticsClient


async def run(args):
    settings = get_settings()
    token = settings.analytics_service_token.get_secret_value()
    url = args.url or settings.analytics_service_url
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=url, timeout=10) as client:
        health = await client.get("/health", headers=headers)
        health.raise_for_status()
        capabilities = health.json()["capabilities"]
        assert capabilities["pmum"]["state"] == "DISABLED", "PMUM debe estar deshabilitado"
        assert "smoke" in capabilities["historical"]["configuration_id"].lower(), "Solo ensayo SMOKE_ONLY"
        denied = await client.post("/v1/activities/select", json={})
        assert denied.status_code == 401, "Se requiere autenticacion interna"
    activities = await HttpActivitySelectorGateway(url, token, 10).recommend(
        context="voluntary", interests=("creative",),
        eligibility_state="known", eligibility=ActivityEligibility(),
    )
    assert activities.activities, "El selector debe devolver actividades ficticias"
    if args.phase == "seed":
        if args.receipt is not None:
            assert not args.receipt.exists(), "El recibo ya existe: usar replay o una ruta nueva"
        identifier = "docker-smoke-" + uuid4().hex
        payload = {
            "request_id": identifier,
            "representation": {
                "representation_record_id": identifier + "-rep",
                "representation_spec_id": "daily_use_duration_minutes_v2",
                "subject_id": identifier + "-series",
                "phenomenon": "DAILY_USE_DURATION",
                "interval_start": "2026-09-01T03:00:00+00:00",
                "interval_end": "2026-09-02T03:00:00+00:00",
                "value": 60.0, "unit": "minutes", "status": "OBSERVED",
                "coverage": {"value": None, "basis": "fictitious_smoke_only"},
                "quality_flags": [],
                "provenance": {
                    "source_id": "docker-integration-smoke",
                    "source_version": "04f1e3a", "source_semantics": "Fictitious technical evidence",
                    "adapter_version": "v2", "input_fingerprint": identifier,
                },
                "computed_at": "2026-09-02T03:02:00+00:00",
            },
        }
    else:
        saved = json.loads(sys.stdin.read() if args.receipt_stdio else args.receipt.read_text(encoding="utf-8"))
        payload = saved["request"]
    result = await HistoricalAnalyticsClient(url, token, 10).evaluate(payload)
    assert result.kind == "processed", "Historico no aceptado: " + str(result.error_code)
    if args.phase == "seed":
        assert result.replayed is False
        saved = {"request": payload, "response": result.response}
        if args.receipt is not None:
            with args.receipt.open("x", encoding="utf-8") as stream:
                json.dump(saved, stream)
    else:
        assert result.replayed is True, "No se recupero el recibo"
        original = dict(saved["response"], replayed=True)
        assert result.response == original, "Cambio el recibo original"
    if args.receipt_stdio:
        print(json.dumps(saved))
    else:
        print(f"PASS {args.phase}: autenticacion, Activities y C1 SMOKE_ONLY; PostgreSQL no utilizado")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("seed", "replay"), required=True)
    parser.add_argument("--url", help="URL interna; por defecto usa la configuracion del backend")
    receipt = parser.add_mutually_exclusive_group(required=True)
    receipt.add_argument("--receipt", type=Path)
    receipt.add_argument("--receipt-stdio", action="store_true", help="Recibo por stdin/stdout, sin escribir en el backend")
    arguments = parser.parse_args()
    try:
        asyncio.run(run(arguments))
    except Exception as error:
        # No imprimir excepciones de transporte/configuracion que puedan incluir secretos.
        print("FAIL: " + type(error).__name__)
        raise SystemExit(1)
