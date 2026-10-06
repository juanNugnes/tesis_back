from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import DeviceAccessTokenCodec, InvalidAccessTokenError
from app.db.models import Device
from app.repositories.device_repository import DeviceRepository


@dataclass(frozen=True)
class DeviceTokenResult:
    access_token: str
    expires_in: int


class DeviceAuthService:
    def __init__(
        self,
        db: AsyncSession,
        jwt_secret: str,
        jwt_expiration_minutes: int,
        repository: DeviceRepository | None = None,
    ):
        self.devices = repository or DeviceRepository(db)
        self.token_codec = DeviceAccessTokenCodec(jwt_secret, jwt_expiration_minutes)

    def issue(self, device: Device) -> DeviceTokenResult:
        access_token, expires_in = self.token_codec.issue(device.id)
        return DeviceTokenResult(access_token=access_token, expires_in=expires_in)

    async def authenticate(self, token: str) -> Device:
        device_id = self.token_codec.decode_subject(token)
        device = await self.devices.get(device_id)
        if device is None or not device.active or not device.profile.active:
            raise InvalidAccessTokenError
        return device
