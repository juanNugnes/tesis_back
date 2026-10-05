from typing import Protocol


class OtpDeliveryService(Protocol):
    async def deliver(self, email: str, code: str) -> None: ...


class DevelopmentOtpDeliveryService:
    """Development placeholder. It intentionally does not log or send the OTP."""

    async def deliver(self, email: str, code: str) -> None:
        return None


class FakeOtpDeliveryService:
    def __init__(self):
        self.codes_by_email: dict[str, str] = {}

    async def deliver(self, email: str, code: str) -> None:
        self.codes_by_email[email] = code
