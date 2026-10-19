import asyncio
from functools import lru_cache
from typing import Protocol


class OtpDeliveryService(Protocol):
    async def deliver(self, email: str, code: str) -> None: ...


class DevelopmentOtpDeliveryService:
    """Development placeholder. It intentionally does not log or send the OTP."""

    async def deliver(self, email: str, code: str) -> None:
        return None


class OtpDeliveryError(Exception):
    """Delivery failed; never include the recipient, code, or provider response."""


@lru_cache(maxsize=8)
def _ses_client(region: str, timeout_seconds: float):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "ses",
        region_name=region,
        config=Config(
            connect_timeout=timeout_seconds,
            read_timeout=timeout_seconds,
            retries={"total_max_attempts": 1},
        ),
    )


class SesOtpDeliveryService:
    def __init__(self, sender: str, region: str, timeout_seconds: float, *, client=None):
        self.sender = sender
        self.client = client if client is not None else _ses_client(region, timeout_seconds)

    async def deliver(self, email: str, code: str) -> None:
        try:
            await asyncio.to_thread(
                self.client.send_email,
                Source=self.sender,
                Destination={"ToAddresses": [email]},
                Message={
                    "Subject": {"Data": "Tu código de acceso a Moderá", "Charset": "UTF-8"},
                    "Body": {"Text": {
                        "Data": f"Tu código de verificación es {code}. No lo compartas.",
                        "Charset": "UTF-8",
                    }},
                },
            )
        except Exception:
            raise OtpDeliveryError("OTP delivery unavailable") from None


class FakeOtpDeliveryService:
    def __init__(self):
        self.codes_by_email: dict[str, str] = {}

    async def deliver(self, email: str, code: str) -> None:
        self.codes_by_email[email] = code
