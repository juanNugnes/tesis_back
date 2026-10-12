from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class ActivityEligibility:
    excluded_activity_ids: tuple[str, ...] = ()
    excluded_categories: tuple[str, ...] = ()
    allowed_activity_levels: tuple[str, ...] = (
        "calm",
        "moderate",
        "active",
    )
    allow_requires_other_person: bool = True
    allow_may_require_device_interaction: bool = True


@dataclass(frozen=True)
class SelectorActivity:
    activity_id: str
    title: str
    description: str
    category: str
    activity_level: str
    bedtime_suitable: bool
    requires_other_person: bool
    may_require_device_interaction: bool


@dataclass(frozen=True)
class ActivitySelectorResult:
    activities: tuple[SelectorActivity, ...]
    reason: str | None = None


class ActivitySelectorTechnicalError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ActivitySelectorGateway(Protocol):
    async def recommend(
        self,
        *,
        context: str,
        interests: tuple[str, ...],
        eligibility_state: Literal["known", "unknown"],
        eligibility: ActivityEligibility | None,
        n: int = 3,
    ) -> ActivitySelectorResult: ...
