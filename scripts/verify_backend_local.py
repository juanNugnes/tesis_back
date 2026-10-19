"""Verificacion local de solo lectura desde el backend desplegado.

Ejecutar por stdin dentro del backend con --receipt-base64 para un recibo C1 de
ensayo YA existente. JWT de prueba breves, en memoria, de identidades locales
existentes. No OTP, alta de perfiles, metricas, ofertas ni evaluaciones adultas.
"""

import argparse
import asyncio
import base64
from datetime import timedelta
import json
import uuid

import httpx
from sqlalchemy import text

from app.core.config import get_settings
from app.core.security import AdultAccessTokenCodec, DeviceAccessTokenCodec
from app.db.session import engine
from app.services.activity_selector_gateway import ActivityEligibility
from app.services.activity_selector_http_gateway import HttpActivitySelectorGateway
from app.services.historical_analytics_client import HistoricalAnalyticsClient


async def main(args):
    settings = get_settings()
    assert settings.environment == "development"
    assert settings.analytics_service_url == "http://analytics:8765"
    async with engine.connect() as conn:
        tx = await conn.begin()
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        actor = (await conn.execute(text("SELECT a.id AS adult_id,p.id AS profile_id FROM adult_profile_links l JOIN adult_accounts a ON a.id=l.adult_id JOIN profiles p ON p.id=l.profile_id JOIN representative_verifications v ON v.adult_id=a.id AND v.profile_id=p.id WHERE l.active AND a.active AND p.active AND v.status='APPROVED' LIMIT 1"))).mappings().first()
        assert actor is not None, "No existe un vinculo aprobado local"
        adult_id, profile_id = actor["adult_id"], actor["profile_id"]
        other = (await conn.execute(text("SELECT p.id FROM profiles p WHERE p.id<>:profile AND NOT EXISTS (SELECT 1 FROM adult_profile_links l WHERE l.profile_id=p.id AND l.adult_id=:adult AND l.active) LIMIT 1"), {"profile": profile_id, "adult": adult_id})).scalar_one_or_none()
        denied_id = other or uuid.uuid4()
        device_id = (await conn.execute(text("SELECT id FROM devices WHERE profile_id=:profile AND active LIMIT 1"), {"profile": profile_id})).scalar_one_or_none()
        await tx.rollback()
    adult_token, _ = AdultAccessTokenCodec(settings.adult_auth_jwt_secret.get_secret_value(), 5).issue(adult_id, expiration=timedelta(minutes=5))
    auth = {"Authorization": f"Bearer {adult_token}"}
    results = {}
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=10) as client:
        async def check(label, method, path, expected, **kwargs):
            response = await client.request(method, path, **kwargs)
            assert response.status_code == expected, label + " HTTP " + str(response.status_code)
            assert settings.analytics_service_token.get_secret_value() not in response.text
            results[label] = response.status_code
            return response
        for attempt in range(15):
            try:
                await check("health", "GET", "/health", 200)
                break
            except httpx.ConnectError:
                if attempt == 14:
                    raise
                await asyncio.sleep(1)
        await check("openapi", "GET", "/openapi.json", 200)
        await check("docs", "GET", "/docs", 200)
        await check("adult_identity", "GET", "/api/v1/auth/adult/me", 200, headers=auth)
        await check("adult_profiles", "GET", "/api/v1/adult/profiles", 200, headers=auth)
        await check("profile", "GET", f"/api/v1/profiles/{profile_id}", 200, headers=auth)
        await check("devices", "GET", f"/api/v1/profiles/{profile_id}/devices", 200, headers=auth)
        usage = await check("usage", "GET", f"/api/v1/profiles/{profile_id}/usage", 200, headers=auth)
        for day in usage.json():
            assert day["last_night_activity_at"] is None
            assert day["metric_statuses"]["last_night_activity_at"] == "UNAVAILABLE"
        if usage.json():
            await check("usage_day", "GET", f"/api/v1/profiles/{profile_id}/usage/{usage.json()[0]['date']}", 200, headers=auth)
        await check("analysis_list", "GET", f"/api/v1/profiles/{profile_id}/analysis", 200, headers=auth)
        history_path = f"/api/v1/profiles/{profile_id}/adult-messages/history"
        first = await check("adult_history_authorized", "GET", history_path, 200, headers=auth)
        assert first.json() == {"versions": []}
        assert first.headers["cache-control"] == "no-store"
        again = await check("adult_history_repeat", "GET", history_path, 200, headers=auth)
        assert again.json() == first.json()
        await check("adult_history_anonymous", "GET", history_path, 401)
        await check("adult_history_invalid_token", "GET", history_path, 401, headers={"Authorization": "Bearer invalid-local-smoke"})
        await check("adult_history_other_profile", "GET", f"/api/v1/profiles/{denied_id}/adult-messages/history", 403, headers=auth)
        await check("usage_anonymous", "GET", f"/api/v1/profiles/{profile_id}/usage", 401)
        await check("activities_anonymous", "POST", f"/api/v1/profiles/{profile_id}/activity-suggestions", 401, json={"context": "voluntary"})
        await check("historical_anonymous", "POST", f"/api/v1/profiles/{profile_id}/historical-submissions", 401, json={})
        if device_id:
            device_token, _ = DeviceAccessTokenCodec(settings.device_auth_jwt_secret.get_secret_value(), 5).issue(device_id, expiration=timedelta(minutes=5))
            device_auth = {"Authorization": f"Bearer {device_token}"}
            await check("adult_history_device_denied", "GET", history_path, 401, headers=device_auth)
            await check("activities_request_validation", "POST", f"/api/v1/profiles/{profile_id}/activity-suggestions", 422, headers=device_auth, json={})
    service_token = settings.analytics_service_token.get_secret_value()
    async with httpx.AsyncClient(base_url=settings.analytics_service_url, timeout=10) as client:
        health = await client.get("/health", headers={"Authorization": f"Bearer {service_token}"})
        assert health.status_code == 200
        capabilities = health.json()["capabilities"]
        assert capabilities["adult_messages"]["state"] == "READY"
        assert capabilities["pmum"]["state"] == "DISABLED"
        assert capabilities["historical"]["configuration_id"] == "smoke-only-not-calibrated"
    selection = await HttpActivitySelectorGateway(settings.analytics_service_url, service_token, settings.analytics_timeout_seconds).recommend(
        context="voluntary", interests=("creative",), eligibility_state="known", eligibility=ActivityEligibility(),
    )
    assert selection.activities  # Solo selector sin persistencia, no una oferta real.
    receipt = json.loads(base64.b64decode(args.receipt_base64))
    replay = await HistoricalAnalyticsClient(settings.analytics_service_url, service_token, settings.analytics_timeout_seconds).evaluate(receipt["request"])
    assert replay.kind == "processed" and replay.replayed is True
    assert replay.response == dict(receipt["response"], replayed=True)
    await engine.dispose()
    print(json.dumps({"passed": True, "http_checks": results, "usage_rows": len(usage.json()),
                      "adult_history_empty": True, "adult_history_repeat_equal": True,
                      "analytics_connected": True, "activities_stateless_selection": True,
                      "existing_c1_receipt_replayed": True, "pmum_disabled": True,
                      "historical_configuration": "SMOKE_ONLY", "database_writes": 0,
                      "automatic_adult_evaluations": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt-base64", required=True)
    try:
        asyncio.run(main(parser.parse_args()))
    except Exception as error:
        print("CRITICAL: " + type(error).__name__ + "; no aplicar cambios adicionales")
        raise SystemExit(1)
