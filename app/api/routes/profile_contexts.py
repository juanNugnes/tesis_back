import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_profile
from app.db.models import Profile
from app.db.session import get_db
from app.repositories.profile_context_repository import ProfileContextRepository
from app.schemas.profile_context import ProfileContextCreate, ProfileContextRead, ProfileContextUpdate
from app.services.profile_context_service import (
    InvalidContextDateRangeError,
    InvalidProfileContextError,
    ProfileContextNotFoundError,
    ProfileContextService,
)

router = APIRouter(prefix="/profiles/{profile_id}/contexts", tags=["profile contexts"])


def get_service(db: AsyncSession) -> ProfileContextService:
    return ProfileContextService(ProfileContextRepository(db))


@router.post("", response_model=ProfileContextRead, status_code=status.HTTP_201_CREATED)
async def create_profile_context(
    data: ProfileContextCreate,
    profile: Profile = Depends(get_profile),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_service(db).create(profile.id, data)
    except InvalidContextDateRangeError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_date must be greater than or equal to start_date",
        )


@router.get("", response_model=list[ProfileContextRead])
async def list_profile_contexts(
    profile: Profile = Depends(get_profile),
    db: AsyncSession = Depends(get_db),
):
    return await get_service(db).list(profile.id)


@router.get("/active", response_model=list[ProfileContextRead])
async def list_active_profile_contexts(
    profile: Profile = Depends(get_profile),
    db: AsyncSession = Depends(get_db),
):
    return await get_service(db).list_active(profile.id)


@router.put("/{context_id}", response_model=ProfileContextRead)
async def update_profile_context(
    context_id: uuid.UUID,
    data: ProfileContextUpdate,
    profile: Profile = Depends(get_profile),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_service(db).update(profile.id, context_id, data)
    except ProfileContextNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile context not found")
    except InvalidProfileContextError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except InvalidContextDateRangeError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_date must be greater than or equal to start_date",
        )


@router.delete("/{context_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile_context(
    context_id: uuid.UUID,
    profile: Profile = Depends(get_profile),
    db: AsyncSession = Depends(get_db),
):
    try:
        await get_service(db).delete(profile.id, context_id)
    except ProfileContextNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile context not found")
