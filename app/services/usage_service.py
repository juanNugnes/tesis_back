import uuid

from sqlalchemy.exc import IntegrityError

from app.repositories.usage_repository import UsageRepository
from app.schemas.usage import DailyUsageCreate


class DuplicateUsageError(Exception): pass


class UsageService:
    def __init__(self, repository: UsageRepository): self.repository = repository

    async def create(self, profile_id: uuid.UUID, data: DailyUsageCreate):
        try:
            return await self.repository.create(profile_id, data)
        except IntegrityError as exc:
            await self.repository.db.rollback()
            raise DuplicateUsageError from exc
