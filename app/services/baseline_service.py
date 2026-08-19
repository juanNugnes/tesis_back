import uuid
from typing import Any


class BaselineService:
    async def get_baseline(self, profile_id: uuid.UUID) -> dict[str, Any]:
        return {}

    async def calculate_baseline(self, profile_id: uuid.UUID) -> dict[str, Any]:
        # TODO: derive a profile baseline from sufficient historical observations.
        return {}

