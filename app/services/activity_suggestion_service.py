import uuid

from app.repositories.activity_offer_repository import ActivityOfferRepository
from app.repositories.profile_activity_preferences_repository import (
    ProfileActivityPreferencesRepository,
)
from app.schemas.activity_suggestion import (
    ActivitySuggestionContext,
    ActivitySuggestionRead,
    ActivitySuggestionsRead,
)
from app.schemas.profile_activity_preferences import ProfileActivityPreferencesRead
from app.services.activity_selector_gateway import ActivitySelectorGateway
from app.services.activity_selector_mapper import (
    ActivityEligibilityMapper,
    ActivityInterestMapper,
)


class ActivitySuggestionService:
    def __init__(
        self,
        preferences: ProfileActivityPreferencesRepository,
        offers: ActivityOfferRepository,
        selector: ActivitySelectorGateway,
    ):
        self.preferences = preferences
        self.offers = offers
        self.selector = selector

    async def suggest(
        self,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
        context: ActivitySuggestionContext,
    ) -> ActivitySuggestionsRead:
        preferences = await self.preferences.get(profile_id)
        if preferences is None:
            preferences = ProfileActivityPreferencesRead(profile_id=profile_id)

        mapped_interests = ActivityInterestMapper.map(preferences.interests)
        mapped_eligibility = ActivityEligibilityMapper.map(preferences)
        activities = await self.selector.recommend(
            context=context.value,
            interests=mapped_interests.selector_tags,
            eligibility_state=mapped_eligibility.eligibility_state,
            eligibility=mapped_eligibility.eligibility,
            n=3,
        )
        response_activities = [
            ActivitySuggestionRead.model_validate(activity)
            for activity in list(activities)[:3]
        ]
        offer = None
        if response_activities:
            offer = await self.offers.create_offer(
                profile_id=profile_id,
                device_id=device_id,
                context=context.value,
                activity_ids=[activity.activity_id for activity in response_activities],
            )
        return ActivitySuggestionsRead(
            offer_id=offer.id if offer is not None else None,
            context=context,
            activities=response_activities,
        )
