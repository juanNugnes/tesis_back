import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AuditActorType,
    AuditEvent,
    AuditEventType,
    AuditResult,
)


class AuditEventRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def append(
        self,
        *,
        actor_type: AuditActorType,
        actor_id: uuid.UUID | None,
        profile_id: uuid.UUID | None,
        event_type: AuditEventType,
        metadata: dict | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            actor_type=actor_type,
            actor_id=actor_id,
            profile_id=profile_id,
            event_type=event_type,
            result=AuditResult.SUCCESS,
            event_metadata=metadata,
        )
        self.db.add(event)
        await self.db.flush()
        return event
