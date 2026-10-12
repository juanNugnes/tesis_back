import uuid

from app.db.models import ActivityOffer, ActivitySelection
from app.repositories.activity_offer_repository import ActivityOfferRepository
from app.schemas.activity_selection import ActivitySelectionCreate


class ActivityOfferNotFoundError(Exception):
    pass


class ActivityOfferAccessDeniedError(Exception):
    pass


class ActivityNotOfferedError(Exception):
    pass


class ActivitySelectionConflictError(Exception):
    pass


class ActivitySelectionService:
    def __init__(self, repository: ActivityOfferRepository):
        self.repository = repository

    async def create(
        self,
        *,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
        data: ActivitySelectionCreate,
    ) -> ActivitySelection:
        existing = await self.repository.get_selection(data.selection_id)
        if existing is not None:
            return self._idempotent_result(
                existing,
                profile_id=profile_id,
                device_id=device_id,
                data=data,
            )

        offer = await self.repository.get_offer(data.offer_id)
        self._validate_offer(offer, profile_id=profile_id, device_id=device_id)
        if data.activity_id not in {item.activity_id for item in offer.items}:
            raise ActivityNotOfferedError

        selection, created = await self.repository.create_selection(
            profile_id=profile_id,
            device_id=device_id,
            data=data,
        )
        if created:
            return selection
        return self._idempotent_result(
            selection,
            profile_id=profile_id,
            device_id=device_id,
            data=data,
        )

    @staticmethod
    def _validate_offer(
        offer: ActivityOffer | None,
        *,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
    ) -> None:
        if offer is None:
            raise ActivityOfferNotFoundError
        if offer.profile_id != profile_id or offer.device_id != device_id:
            raise ActivityOfferAccessDeniedError

    @staticmethod
    def _idempotent_result(
        existing: ActivitySelection,
        *,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
        data: ActivitySelectionCreate,
    ) -> ActivitySelection:
        if (
            existing.offer_id != data.offer_id
            or existing.profile_id != profile_id
            or existing.device_id != device_id
            or existing.activity_id != data.activity_id
            or existing.selected_at_epoch_millis != data.selected_at_epoch_millis
        ):
            raise ActivitySelectionConflictError
        return existing
