from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from app.db.models import ProfileContext
from app.repositories.profile_context_repository import ProfileContextRepository
from app.schemas.profile_context import ProfileContextCreate, ProfileContextUpdate


class InvalidContextDateRangeError(ValueError):
    pass


class ProfileContextNotFoundError(Exception):
    pass


class InvalidProfileContextError(ValueError):
    pass


class ProfileContextService:
    def __init__(self, repository: ProfileContextRepository):
        self.repository = repository

    async def create(self, profile_id: uuid.UUID, data: ProfileContextCreate) -> ProfileContext:
        self._validate_date_range(data.start_date, data.end_date)
        return await self.repository.create(profile_id, data)

    async def list(self, profile_id: uuid.UUID) -> list[ProfileContext]:
        return await self.repository.list(profile_id)

    async def list_active(
        self,
        profile_id: uuid.UUID,
        reference_date: date | None = None,
    ) -> list[ProfileContext]:
        current_date = reference_date or datetime.now(timezone.utc).date()
        return await self.repository.list_active(profile_id, current_date)

    async def update(
        self,
        profile_id: uuid.UUID,
        context_id: uuid.UUID,
        data: ProfileContextUpdate,
    ) -> ProfileContext:
        context = await self._get(profile_id, context_id)
        values = data.model_dump(exclude_unset=True)
        if values.get("type", context.type) is None:
            raise InvalidProfileContextError("type cannot be null")
        if values.get("start_date", context.start_date) is None:
            raise InvalidProfileContextError("start_date cannot be null")
        start_date = values.get("start_date", context.start_date)
        end_date = values.get("end_date", context.end_date)
        self._validate_date_range(start_date, end_date)
        return await self.repository.update(context, data)

    async def delete(self, profile_id: uuid.UUID, context_id: uuid.UUID) -> None:
        context = await self._get(profile_id, context_id)
        await self.repository.delete(context)

    async def _get(self, profile_id: uuid.UUID, context_id: uuid.UUID) -> ProfileContext:
        context = await self.repository.get(profile_id, context_id)
        if context is None:
            raise ProfileContextNotFoundError
        return context

    @staticmethod
    def _validate_date_range(start_date: date, end_date: date | None) -> None:
        if end_date is not None and end_date < start_date:
            raise InvalidContextDateRangeError
