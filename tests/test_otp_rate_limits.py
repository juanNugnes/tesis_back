import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.client_ip import otp_client_ip
from app.db.models import OtpRateLimitEvent
from app.main import app
from app.services import otp_rate_limiter


REQUEST_PATH = "/api/v1/auth/adult/otp/request"
VERIFY_PATH = "/api/v1/auth/adult/otp/verify"


def as_ip(address: str):
    return AsyncClient(transport=ASGITransport(app=app, client=(address, 1234)),
                       base_url="http://test")


@pytest.mark.asyncio
async def test_email_cooldown_and_sliding_window(client, monkeypatch):
    start = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    current = [start]
    monkeypatch.setattr(otp_rate_limiter, "utc_now", lambda: current[0])
    payload = {"email": "limit@example.com"}

    first = await client.post(REQUEST_PATH, json=payload)
    assert first.status_code == 202
    blocked = await client.post(REQUEST_PATH, json=payload)
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "60"
    assert blocked.json() == {"detail": "Too many OTP attempts"}

    for seconds in (60, 120):
        current[0] = start + timedelta(seconds=seconds)
        assert (await client.post(REQUEST_PATH, json=payload)).status_code == 202

    current[0] = start + timedelta(seconds=180)
    full = await client.post(REQUEST_PATH, json=payload)
    assert full.status_code == 429
    assert full.headers["Retry-After"] == "720"

    current[0] = start + timedelta(minutes=15)
    assert (await client.post(REQUEST_PATH, json=payload)).status_code == 202


@pytest.mark.asyncio
async def test_ip_request_limit_and_untrusted_forwarded_header(client):
    async with as_ip("203.0.113.10") as sender:
        for index in range(10):
            response = await sender.post(
                REQUEST_PATH,
                json={"email": f"ip-{index}@example.com"},
                headers={"X-Forwarded-For": f"192.0.2.{index + 1}"},
            )
            assert response.status_code == 202
        limited = await sender.post(
            REQUEST_PATH,
            json={"email": "ip-11@example.com"},
            headers={"X-Forwarded-For": "198.51.100.25"},
        )
        assert limited.status_code == 429
        assert 1 <= int(limited.headers["Retry-After"]) <= 900


@pytest.mark.asyncio
async def test_verify_unknown_email_is_limited_without_account_disclosure(client):
    payload = {"email": "unknown@example.com", "code": "000000"}
    for _ in range(10):
        response = await client.post(VERIFY_PATH, json=payload)
        assert response.status_code == 401
    blocked = await client.post(VERIFY_PATH, json=payload)
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "Too many OTP attempts"}
    assert 1 <= int(blocked.headers["Retry-After"]) <= 900


@pytest.mark.asyncio
async def test_verify_ip_limit_across_distinct_emails(client):
    async with as_ip("198.51.100.44") as sender:
        for index in range(30):
            response = await sender.post(
                VERIFY_PATH,
                json={"email": f"verify-{index}@example.com", "code": "000000"},
            )
            assert response.status_code == 401
        blocked = await sender.post(
            VERIFY_PATH,
            json={"email": "verify-last@example.com", "code": "000000"},
        )
        assert blocked.status_code == 429


@pytest.mark.asyncio
async def test_concurrent_requests_cannot_bypass_cooldown(client, db_session):
    responses = await asyncio.gather(*(
        client.post(REQUEST_PATH, json={"email": "concurrent@example.com"})
        for _ in range(5)
    ))
    assert sorted(response.status_code for response in responses) == [202, 429, 429, 429, 429]
    events = (await db_session.scalars(select(OtpRateLimitEvent))).all()
    assert len(events) == 2
    assert all(len(event.key_hash) == 64 for event in events)


@pytest.mark.asyncio
async def test_trusted_proxy_uses_last_untrusted_hop(client):
    from starlette.requests import Request

    scope = {
        "type": "http", "method": "POST", "path": REQUEST_PATH,
        "headers": [(b"x-forwarded-for", b"192.0.2.77, 198.51.100.9")],
        "client": ("10.0.0.8", 1234),
    }
    assert otp_client_ip(Request(scope), "10.0.0.8/32") == "198.51.100.9"
    assert otp_client_ip(Request(scope), "") == "10.0.0.8"
