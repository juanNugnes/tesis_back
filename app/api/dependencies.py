import uuid

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Profile
from app.db.session import get_db
from app.repositories.profile_repository import ProfileRepository


async def get_profile(profile_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Profile:
    profile = await ProfileRepository(db).get(profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return profile

