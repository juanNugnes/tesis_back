from dataclasses import dataclass
from datetime import date
from typing import Iterable, Literal, Protocol

from app.db.models import ActivityInterest
from app.services.activity_selector_gateway import ActivityEligibility


class ActivityPreferencesSource(Protocol):
    interests: Iterable[ActivityInterest | str]
    considerations_answered: bool
    considerations: Iterable[object]


@dataclass(frozen=True)
class MappedActivityInterests:
    selector_tags: tuple[str, ...]
    unmapped: tuple[ActivityInterest, ...]


class ActivityInterestMapper:
    MAPPINGS: dict[ActivityInterest, tuple[str, ...]] = {
        ActivityInterest.DRAWING_CREATING: (),
        ActivityInterest.READING_STORIES: (),
        ActivityInterest.MUSIC: ("music",),
        ActivityInterest.GAMES_CHALLENGES: (),
        ActivityInterest.OUTDOORS: (),
        ActivityInterest.MOVEMENT: ("movement",),
        ActivityInterest.BUILDING: ("building",),
        ActivityInterest.WRITING: ("writing",),
        ActivityInterest.IMAGINATIVE_PLAY: (),
    }

    @classmethod
    def map(cls, interests: Iterable[ActivityInterest | str]) -> MappedActivityInterests:
        selector_tags: list[str] = []
        unmapped: list[ActivityInterest] = []
        for raw_interest in interests:
            interest = ActivityInterest(raw_interest)
            tags = cls.MAPPINGS[interest]
            if not tags:
                unmapped.append(interest)
                continue
            for tag in tags:
                if tag not in selector_tags:
                    selector_tags.append(tag)
        return MappedActivityInterests(tuple(selector_tags), tuple(unmapped))


@dataclass(frozen=True)
class MappedActivityEligibility:
    eligibility_state: Literal["known", "unknown"]
    eligibility: ActivityEligibility | None


class ActivityEligibilityMapper:
    @classmethod
    def map(
        cls,
        preferences: ActivityPreferencesSource,
        *,
        today: date | None = None,
    ) -> MappedActivityEligibility:
        if preferences.considerations_answered and not list(
            preferences.considerations
        ):
            return MappedActivityEligibility(
                eligibility_state="known",
                eligibility=ActivityEligibility(),
            )
        return MappedActivityEligibility(
            eligibility_state="unknown",
            eligibility=None,
        )
