import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.consent_repository import ConsentRepository


class MonitoringAuthorizationService:
    def __init__(self, db: AsyncSession):
        self.consents = ConsentRepository(db)

    async def is_usage_monitoring_allowed(self, profile_id: uuid.UUID) -> bool:
        return await self.consents.has_active_usage_monitoring_authorization(profile_id)
