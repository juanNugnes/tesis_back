import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdultAccount, AutomaticOfferSettings


class AutomaticOfferSettingsRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, profile_id: uuid.UUID) -> AutomaticOfferSettings | None:
        return await self.db.get(AutomaticOfferSettings, profile_id)

    async def upsert(
        self,
        *,
        profile_id: uuid.UUID,
        adult: AdultAccount,
        automatic_enabled: bool,
        shared_phone: bool,
    ) -> AutomaticOfferSettings:
        settings = await self.get(profile_id)
        if settings is None:
            settings = AutomaticOfferSettings(
                profile_id=profile_id,
                adult_id=adult.id,
                automatic_enabled=automatic_enabled,
                shared_phone=shared_phone,
            )
            self.db.add(settings)
        else:
            settings.adult_id = adult.id
            settings.automatic_enabled = automatic_enabled
            settings.shared_phone = shared_phone
        await self.db.commit()
        await self.db.refresh(settings)
        return settings
