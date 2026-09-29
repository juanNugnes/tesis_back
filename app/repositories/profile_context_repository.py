from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProfileContext
from app.schemas.profile_context import ProfileContextCreate, ProfileContextUpdate


class ProfileContextRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, profile_id: uuid.UUID, data: ProfileContextCreate) -> ProfileContext:
        context = ProfileContext(profile_id=profile_id, **data.model_dump())
        self.db.add(context)
        await self.db.commit()
        await self.db.refresh(context)
        return context

    async def get(self, profile_id: uuid.UUID, context_id: uuid.UUID) -> ProfileContext | None:
        query = select(ProfileContext).where(
            ProfileContext.id == context_id,
            ProfileContext.profile_id == profile_id,
        )
        return (await self.db.execute(query)).scalar_one_or_none()

    async def list(self, profile_id: uuid.UUID) -> list[ProfileContext]:
        query = (
            select(ProfileContext)
            .where(ProfileContext.profile_id == profile_id)
            .order_by(ProfileContext.start_date.desc(), ProfileContext.created_at.desc())
        )
        return list((await self.db.execute(query)).scalars().all())

    async def list_active(self, profile_id: uuid.UUID, reference_date: date) -> list[ProfileContext]:
        query = (
            select(ProfileContext)
            .where(
                ProfileContext.profile_id == profile_id,
                ProfileContext.start_date <= reference_date,
                or_(ProfileContext.end_date.is_(None), ProfileContext.end_date >= reference_date),
            )
            .order_by(ProfileContext.start_date.desc(), ProfileContext.created_at.desc())
        )
        return list((await self.db.execute(query)).scalars().all())

    async def update(self, context: ProfileContext, data: ProfileContextUpdate) -> ProfileContext:
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(context, key, value)
        await self.db.commit()
        await self.db.refresh(context)
        return context

    async def delete(self, context: ProfileContext) -> None:
        await self.db.delete(context)
        await self.db.commit()
