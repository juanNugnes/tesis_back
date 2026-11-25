from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.db.models import AdultAccount, AdultWeeklyDeclaration
from app.repositories.adult_weekly_declaration_repository import (
    AdultWeeklyDeclarationRepository,
)
from app.schemas.adult_weekly_declaration import AdultWeeklyDeclarationCreate


class AdultWeeklyDeclarationNotFoundError(Exception):
    pass


class AdultWeeklyDeclarationDeviceError(Exception):
    pass


class AdultWeeklyDeclarationDateError(Exception):
    pass


class AdultWeeklyDeclarationPersistenceError(Exception):
    pass


class AdultWeeklyDeclarationService:
    def __init__(self, repository: AdultWeeklyDeclarationRepository):
        self.repository = repository

    async def create(
        self,
        *,
        profile_id: uuid.UUID,
        adult: AdultAccount,
        data: AdultWeeklyDeclarationCreate,
        now: datetime | None = None,
    ) -> AdultWeeklyDeclaration:
        now = self._utc(now or datetime.now(UTC))
        if data.effective_from < now.date():
            raise AdultWeeklyDeclarationDateError(
                "effective_from cannot precede the declaration date"
            )
        try:
            profile = await self.repository.lock_profile(profile_id)
            if profile is None or not profile.active:
                raise AdultWeeklyDeclarationNotFoundError
            if await self.repository.active_device(profile_id, data.device_id) is None:
                raise AdultWeeklyDeclarationDeviceError
            declaration = AdultWeeklyDeclaration(
                profile_id=profile_id,
                device_id=data.device_id,
                created_by_adult_id=adult.id,
                revision=await self.repository.next_revision(profile_id),
                effective_from=data.effective_from,
                effective_to=data.effective_to,
                assignment_verified=data.assignment_verified,
                shared_phone=data.shared_phone,
                context_declared=data.context_declared,
                created_at=now,
            )
            await self.repository.add(declaration)
            await self.repository.commit()
            await self.repository.refresh(declaration)
            return declaration
        except (
            AdultWeeklyDeclarationNotFoundError,
            AdultWeeklyDeclarationDeviceError,
            AdultWeeklyDeclarationDateError,
        ):
            await self.repository.rollback()
            raise
        except IntegrityError as exc:
            await self.repository.rollback()
            raise AdultWeeklyDeclarationPersistenceError from exc
        except SQLAlchemyError as exc:
            await self.repository.rollback()
            raise AdultWeeklyDeclarationPersistenceError from exc

    async def list(
        self,
        *,
        profile_id: uuid.UUID,
    ) -> list[AdultWeeklyDeclaration]:
        return await self.repository.list_for_profile(profile_id)

    async def revoke(
        self,
        *,
        profile_id: uuid.UUID,
        declaration_id: uuid.UUID,
        adult: AdultAccount,
        now: datetime | None = None,
    ) -> AdultWeeklyDeclaration:
        now = self._utc(now or datetime.now(UTC))
        try:
            declaration = await self.repository.get_for_update(
                profile_id, declaration_id
            )
            if declaration is None:
                raise AdultWeeklyDeclarationNotFoundError
            if declaration.revoked_at is None:
                declaration.revoked_at = now
                declaration.revoked_effective_date = now.date()
                declaration.revoked_by_adult_id = adult.id
                await self.repository.commit()
                await self.repository.refresh(declaration)
            return declaration
        except AdultWeeklyDeclarationNotFoundError:
            await self.repository.rollback()
            raise
        except SQLAlchemyError as exc:
            await self.repository.rollback()
            raise AdultWeeklyDeclarationPersistenceError from exc

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
