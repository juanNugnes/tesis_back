import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.dependencies import get_otp_delivery_service
from app.core.config import get_settings
from app.db.models import AdultOtpChallenge
from app.services.otp_delivery_service import (
    DevelopmentOtpDeliveryService,
    OtpDeliveryError,
    SesOtpDeliveryService,
)
from app.services import otp_delivery_service


class StubSesClient:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def send_email(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"MessageId": "test-message-id"}


@pytest.mark.asyncio
async def test_ses_delivers_otp_with_verified_sender_and_plain_text():
    client = StubSesClient()
    delivery = SesOtpDeliveryService("sender@example.com", "us-east-1", 5, client=client)

    await delivery.deliver("adult@example.com", "123456")

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["Source"] == "sender@example.com"
    assert call["Destination"] == {"ToAddresses": ["adult@example.com"]}
    assert call["Message"]["Body"]["Text"]["Charset"] == "UTF-8"
    assert "123456" in call["Message"]["Body"]["Text"]["Data"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("SES rejected 123456"), TimeoutError("123456")])
async def test_ses_failure_hides_sensitive_provider_details(error):
    delivery = SesOtpDeliveryService("sender@example.com", "us-east-1", 5,
                                     client=StubSesClient(error))
    with pytest.raises(OtpDeliveryError) as exc:
        await delivery.deliver("adult@example.com", "123456")
    assert str(exc.value) == "OTP delivery unavailable"
    assert exc.value.__cause__ is None


def test_provider_is_explicit_and_development_never_runs_in_production(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "otp_email_provider", None)
    with pytest.raises(HTTPException) as exc:
        get_otp_delivery_service()
    assert exc.value.status_code == 503

    monkeypatch.setattr(settings, "otp_email_provider", "development")
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(HTTPException) as exc:
        get_otp_delivery_service()
    assert exc.value.status_code == 503

    monkeypatch.setattr(settings, "environment", "development")
    assert isinstance(get_otp_delivery_service(), DevelopmentOtpDeliveryService)

    monkeypatch.setattr(settings, "otp_email_provider", "ses")
    with pytest.raises(HTTPException) as exc:
        get_otp_delivery_service()
    assert exc.value.status_code == 503

    monkeypatch.setattr(settings, "ses_sender_email", "sender@example.com")
    stub = StubSesClient()
    monkeypatch.setattr(otp_delivery_service, "_ses_client", lambda region, timeout: stub)
    assert isinstance(get_otp_delivery_service(), SesOtpDeliveryService)


@pytest.mark.asyncio
async def test_failed_delivery_returns_generic_503_without_storing_challenge(
    client, db_session, otp_delivery, monkeypatch
):
    async def fail(email, code):
        raise OtpDeliveryError("OTP delivery unavailable")

    monkeypatch.setattr(otp_delivery, "deliver", fail)
    response = await client.post(
        "/api/v1/auth/adult/otp/request", json={"email": "adult@example.com"}
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Verification email temporarily unavailable"}
    assert "adult@example.com" not in response.text
    assert (await db_session.scalars(select(AdultOtpChallenge))).all() == []


@pytest.mark.asyncio
async def test_production_never_exposes_code_even_with_dev_flag(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "expose_dev_otp", True)
    response = await client.post(
        "/api/v1/auth/adult/otp/request", json={"email": "adult@example.com"}
    )
    assert response.status_code == 202
    assert "dev_code" not in response.json()
