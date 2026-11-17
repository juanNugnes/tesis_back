import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import ActivityOffer, ActivityOfferItem, ActivitySelection
from app.schemas.activity_selection import ActivitySelectionCreate


class ActivityOfferRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_offer(
        self,
        *,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
        context: str,
        activity_ids: list[str],
    ) -> ActivityOffer:
        offer = ActivityOffer(
            profile_id=profile_id,
            device_id=device_id,
            context=context,
            items=[
                ActivityOfferItem(activity_id=activity_id, ordinal=ordinal)
                for ordinal, activity_id in enumerate(activity_ids, start=1)
            ],
        )
        self.db.add(offer)
        await self.db.commit()
        await self.db.refresh(offer)
        return offer

    async def ensure_coordinator_offer(
        self,
        *,
        offer_id: uuid.UUID,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
        activity_ids: list[str],
    ) -> ActivityOffer:
        """Materialize a coordinator offer in the existing selection repository."""
        existing = await self.get_offer(offer_id)
        if existing is not None:
            if (
                existing.profile_id != profile_id
                or existing.device_id != device_id
                or existing.context != "automatic"
                or [item.activity_id for item in existing.items] != activity_ids
            ):
                raise ValueError("Coordinator offer conflicts with persisted activity offer")
            return existing

        offer = ActivityOffer(
            id=offer_id,
            profile_id=profile_id,
            device_id=device_id,
            context="automatic",
            items=[
                ActivityOfferItem(activity_id=activity_id, ordinal=ordinal)
                for ordinal, activity_id in enumerate(activity_ids, start=1)
            ],
        )
        self.db.add(offer)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            existing = await self.get_offer(offer_id)
            if existing is None:
                raise
            if (
                existing.profile_id != profile_id
                or existing.device_id != device_id
                or existing.context != "automatic"
                or [item.activity_id for item in existing.items] != activity_ids
            ):
                raise ValueError("Coordinator offer conflicts with persisted activity offer")
            return existing
        await self.db.refresh(offer)
        return offer

    async def get_offer(self, offer_id: uuid.UUID) -> ActivityOffer | None:
        return await self.db.scalar(
            select(ActivityOffer)
            .options(selectinload(ActivityOffer.items))
            .where(ActivityOffer.id == offer_id)
        )

    async def get_selection(
        self,
        selection_id: uuid.UUID,
    ) -> ActivitySelection | None:
        return await self.db.get(ActivitySelection, selection_id)

    async def has_recorded_selection(
        self,
        *,
        offer_id: uuid.UUID,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
        activity_id: str,
    ) -> bool:
        return bool(
            await self.db.scalar(
                select(ActivitySelection.selection_id)
                .where(
                    ActivitySelection.offer_id == offer_id,
                    ActivitySelection.profile_id == profile_id,
                    ActivitySelection.device_id == device_id,
                    ActivitySelection.activity_id == activity_id,
                )
                .limit(1)
            )
        )

    async def count_selections(
        self,
        *,
        profile_id: uuid.UUID,
        start_epoch_millis: int,
        end_epoch_millis: int,
    ) -> int:
        count = await self.db.scalar(
            select(func.count())
            .select_from(ActivitySelection)
            .where(
                ActivitySelection.profile_id == profile_id,
                ActivitySelection.selected_at_epoch_millis >= start_epoch_millis,
                ActivitySelection.selected_at_epoch_millis < end_epoch_millis,
            )
        )
        return count or 0

    async def create_selection(
        self,
        *,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
        data: ActivitySelectionCreate,
    ) -> tuple[ActivitySelection, bool]:
        selection = ActivitySelection(
            selection_id=data.selection_id,
            offer_id=data.offer_id,
            profile_id=profile_id,
            device_id=device_id,
            activity_id=data.activity_id,
            selected_at_epoch_millis=data.selected_at_epoch_millis,
        )
        self.db.add(selection)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            existing = await self.get_selection(data.selection_id)
            if existing is None:
                raise
            return existing, False
        await self.db.refresh(selection)
        return selection, True
