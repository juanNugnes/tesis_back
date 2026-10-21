"""Durable, profile-scoped transport for controlled coordinator tests."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChildOfferOperation, Profile
from app.services.child_offers_client import ChildOffersClient, ChildOffersError


class ChildOfferOperationConflict(Exception):
    pass


class ChildOfferOperationPending(Exception):
    pass


class ChildOfferService:
    def __init__(self, db: AsyncSession, analytics: ChildOffersClient):
        self.db = db
        self.analytics = analytics

    async def evaluate(self, profile_id: UUID, device_id: UUID, payload: dict) -> dict:
        return await self._post("evaluate", profile_id, device_id, payload)

    async def event(self, profile_id: UUID, device_id: UUID, payload: dict) -> dict:
        # An offer ID is not a bearer capability. Confirm its profile before a write.
        prior = await self.db.get(ChildOfferOperation, payload["request_id"])
        if prior is None:
            history = await self.analytics.history(str(profile_id))
            if not any(offer["offer_id"] == payload["offer_id"] for offer in history["offers"]):
                raise ChildOfferOperationConflict
        return await self._post("event", profile_id, device_id, payload)

    async def _post(self, kind: str, profile_id: UUID, device_id: UUID, payload: dict) -> dict:
        request_id = payload["request_id"]
        # PostgreSQL row lock serializes competing operations on the same profile,
        # including requests from different backend instances.
        profile = await self.db.scalar(
            select(Profile).where(Profile.id == profile_id).with_for_update()
        )
        if profile is None or not profile.active:
            raise ChildOfferOperationConflict
        existing = await self.db.get(ChildOfferOperation, request_id)
        if existing is not None:
            if (
                existing.profile_id != profile_id or existing.device_id != device_id
                or existing.kind != kind or existing.payload != payload
            ):
                await self.db.rollback()
                raise ChildOfferOperationConflict
            if existing.status == "DONE":
                receipt = {**existing.receipt, "replayed": True}
                await self.db.rollback()
                return receipt
            if existing.status == "CONFLICT":
                await self.db.rollback()
                raise ChildOfferOperationConflict
        else:
            unresolved = await self.db.scalar(
                select(ChildOfferOperation.request_id)
                .where(ChildOfferOperation.profile_id == profile_id)
                .where(ChildOfferOperation.status.in_(("PENDING", "CONFLICT")))
                .limit(1)
            )
            if unresolved is not None:
                await self.db.rollback()
                raise ChildOfferOperationPending
            existing = ChildOfferOperation(
                request_id=request_id, profile_id=profile_id, device_id=device_id,
                kind=kind, status="PENDING", payload=payload,
            )
            self.db.add(existing)
        # Commit before transport: lost responses leave a durable, exact retry payload.
        await self.db.commit()
        try:
            receipt = (
                await self.analytics.evaluate(payload) if kind == "evaluate"
                else await self.analytics.event(payload)
            )
        except ChildOffersError as error:
            if error.status_code in (400, 409):
                operation = await self.db.get(ChildOfferOperation, request_id)
                operation.status = "CONFLICT"
                await self.db.commit()
            # All other uncertain failures remain PENDING; never allocate a new ID.
            raise
        operation = await self.db.get(ChildOfferOperation, request_id)
        operation.status = "DONE"
        operation.receipt = receipt
        await self.db.commit()
        return receipt
