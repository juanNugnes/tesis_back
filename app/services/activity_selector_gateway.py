from dataclasses import dataclass
from typing import Protocol, Sequence


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
    interest_tags: tuple[str, ...]
    allowed_contexts: tuple[str, ...]
    activity_level: str
    bedtime_suitable: bool
    requires_other_person: bool
    may_require_device_interaction: bool


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
        eligibility: ActivityEligibility,
        n: int = 3,
    ) -> Sequence[SelectorActivity]: ...


class UnavailableActivitySelectorGateway:
    async def recommend(
        self,
        *,
        context: str,
        interests: tuple[str, ...],
        eligibility: ActivityEligibility,
        n: int = 3,
    ) -> Sequence[SelectorActivity]:
        raise ActivitySelectorTechnicalError(
            "SELECTOR_NOT_CONFIGURED",
            "Activity selector transport is not configured",
        )
