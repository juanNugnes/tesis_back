import hashlib
import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.db.models import ChildOfferOperation, Device, ParentalRespondent, Profile
from app.repositories.activity_offer_repository import ActivityOfferRepository
from app.repositories.automatic_offer_settings_repository import (
    AutomaticOfferSettingsRepository,
)
from app.repositories.experimental_pmum_repository import ExperimentalPmumRepository
from app.repositories.profile_activity_preferences_repository import (
    ProfileActivityPreferencesRepository,
)
from app.repositories.profile_settings_repository import ProfileSettingsRepository
from app.schemas.child_offer import (
    AutomaticOfferRecord,
    AutomaticOpportunityCreate,
    AutomaticOpportunityRead,
)
from app.schemas.profile_activity_preferences import ProfileActivityPreferencesRead
from app.services.activity_selector_mapper import (
    ActivityEligibilityMapper,
    ActivityInterestMapper,
)
from app.services.child_offer_service import ChildOfferOperationConflict, ChildOfferService


def _inside_window(value: time, start: time, end: time) -> bool:
    if start < end:
        return start <= value < end
    return value >= start or value < end


def _next_local_time(now: datetime, value: time, zone: ZoneInfo) -> datetime:
    candidate = datetime.combine(now.date(), value, zone)
    if candidate < now:
        candidate += timedelta(days=1)
    return candidate


class AutomaticOpportunityService:
    def __init__(
        self,
        *,
        profile_settings: ProfileSettingsRepository,
        offer_settings: AutomaticOfferSettingsRepository,
        preferences: ProfileActivityPreferencesRepository,
        parental_responses: ExperimentalPmumRepository,
        activity_offers: ActivityOfferRepository,
        child_offers: ChildOfferService,
    ):
        self.profile_settings = profile_settings
        self.offer_settings = offer_settings
        self.preferences = preferences
        self.parental_responses = parental_responses
        self.activity_offers = activity_offers
        self.child_offers = child_offers

    @staticmethod
    def _discard(data: AutomaticOpportunityCreate, reason: str) -> AutomaticOpportunityRead:
        return AutomaticOpportunityRead(
            outcome="discarded",
            request_id=data.request_id,
            reason=reason,
        )

    @staticmethod
    def _same_capture(
        payload: dict,
        data: AutomaticOpportunityCreate,
        profile_id: uuid.UUID,
    ) -> bool:
        evidence = payload.get("evidence", {})
        return (
            payload.get("request_id") == data.request_id
            and evidence.get("profile_id") == str(profile_id)
            and evidence.get("series_id") == data.series_id
            and evidence.get("interval_id") == data.interval_id
            and evidence.get("now") == data.observed_at.isoformat()
            and evidence.get("interval_started_at") == data.interval_started_at.isoformat()
            and evidence.get("capture_verified") is data.capture_verified
            and evidence.get("screen_interactive") is data.screen_interactive
            and evidence.get("game_active") is data.game_active
        )

    async def _result_from_receipt(
        self,
        *,
        data: AutomaticOpportunityCreate,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
        receipt: dict,
    ) -> AutomaticOpportunityRead:
        if receipt["status"] == "suppressed":
            return AutomaticOpportunityRead(
                outcome="discarded",
                request_id=data.request_id,
                reason=receipt.get("reason") or "coordinator_suppressed",
                replayed=receipt["replayed"],
                policy_version=receipt["policy_version"],
                coordinator_offer_id=receipt.get("offer_id"),
            )

        offer = receipt["offer"]
        try:
            validated_offer = AutomaticOfferRecord.model_validate(offer)
        except ValidationError:
            coordinator_offer_id = (
                offer.get("offer_id") if isinstance(offer, dict) else None
            )
            if isinstance(coordinator_offer_id, str):
                failure_id = "content-invalid-" + hashlib.sha256(
                    (
                        f"{profile_id}:{device_id}:{data.request_id}:"
                        f"{coordinator_offer_id}"
                    ).encode("utf-8")
                ).hexdigest()
                await self.child_offers.event(
                    profile_id,
                    device_id,
                    {
                        "request_id": failure_id,
                        "profile_id": str(profile_id),
                        "offer_id": coordinator_offer_id,
                        "event": "delivery_failed",
                        "occurred_at": data.observed_at.isoformat(),
                        "now": data.observed_at.isoformat(),
                    },
                )
            return AutomaticOpportunityRead(
                outcome="discarded",
                request_id=data.request_id,
                reason="activity_content_incomplete",
                replayed=receipt["replayed"],
                policy_version=receipt["policy_version"],
                coordinator_offer_id=coordinator_offer_id,
            )
        try:
            offer_id = uuid.UUID(validated_offer.offer_id)
        except (KeyError, TypeError, ValueError):
            raise ValueError("Coordinator offer cannot be linked to activity selections") from None
        activities = validated_offer.activities_offered
        local_offer = await self.activity_offers.ensure_coordinator_offer(
            offer_id=offer_id,
            profile_id=profile_id,
            device_id=device_id,
            activity_ids=activities,
        )
        return AutomaticOpportunityRead(
            outcome="offer_available",
            request_id=data.request_id,
            replayed=receipt["replayed"],
            policy_version=receipt["policy_version"],
            coordinator_offer_id=validated_offer.offer_id,
            activity_offer_id=local_offer.id,
            channel=receipt["channel"],
            offer=validated_offer,
        )

    async def evaluate(
        self,
        profile: Profile,
        device: Device,
        data: AutomaticOpportunityCreate,
    ) -> AutomaticOpportunityRead:
        profile_id = profile.id
        device_id = device.id
        existing = await self.child_offers.db.get(ChildOfferOperation, data.request_id)
        if existing is not None:
            if (
                existing.profile_id != profile_id
                or existing.device_id != device_id
                or existing.kind != "adaptive"
                or not self._same_capture(existing.payload, data, profile_id)
            ):
                raise ChildOfferOperationConflict
            receipt = await self.child_offers.evaluate_adaptive(
                profile_id,
                device_id,
                existing.payload,
            )
            return await self._result_from_receipt(
                data=data,
                profile_id=profile_id,
                device_id=device_id,
                receipt=receipt,
            )

        controls = await self.offer_settings.get(profile_id)
        if controls is None:
            return self._discard(data, "adult_offer_settings_missing")
        if not controls.automatic_enabled:
            return self._discard(data, "automatic_offers_disabled")
        if controls.shared_phone:
            return self._discard(data, "shared_phone")
        if not data.capture_verified:
            return self._discard(data, "capture_unverified")
        if not data.screen_interactive:
            return self._discard(data, "screen_inactive")
        if data.game_active is None:
            return self._discard(data, "game_state_missing")

        schedules = await self.profile_settings.get(profile_id)
        if schedules is None or schedules.school_start is None or schedules.school_end is None:
            return self._discard(data, "school_schedule_missing")
        if schedules.sleep_start is None or schedules.sleep_end is None:
            return self._discard(data, "sleep_schedule_missing")

        preferences = await self.preferences.get(profile_id)
        if preferences is None:
            preferences = ProfileActivityPreferencesRead(profile_id=profile_id)
        eligibility = ActivityEligibilityMapper.map(preferences)
        if eligibility.eligibility_state != "known" or eligibility.eligibility is None:
            return self._discard(data, "eligibility_unknown")
        interests = ActivityInterestMapper.map(preferences.interests)

        parental = await self.parental_responses.latest_response(profile_id)
        if parental is None:
            return self._discard(data, "pmum_answers_missing")
        if (
            parental.sex_code is None
            or parental.weekday_category is None
            or parental.weekend_category is None
            or parental.respondent != ParentalRespondent.PARENT_OR_CAREGIVER
            or parental.reference_days != 30
        ):
            return self._discard(data, "pmum_answers_ineligible")

        zone = ZoneInfo(data.timezone)
        local_now = data.observed_at.astimezone(zone)
        in_school_period = _inside_window(
            local_now.timetz().replace(tzinfo=None),
            schedules.school_start,
            schedules.school_end,
        )
        if in_school_period:
            return self._discard(data, "school_period")
        bedtime_at = _next_local_time(local_now, schedules.sleep_start, zone)

        payload = {
            "request_id": data.request_id,
            "evidence": {
                "profile_id": str(profile_id),
                "series_id": data.series_id,
                "interval_id": data.interval_id,
                "now": data.observed_at.isoformat(),
                "interval_started_at": data.interval_started_at.isoformat(),
                "bedtime_at": bedtime_at.isoformat(),
                "automatic_enabled": True,
                # Device authentication verified this active device/profile assignment.
                "assignment_verified": True,
                "shared_phone": False,
                "capture_verified": data.capture_verified,
                "screen_interactive": data.screen_interactive,
                "game_active": data.game_active,
                "in_school_period": False,
                "eligibility_state": eligibility.eligibility_state,
                "eligibility": {
                    "excluded_activity_ids": list(
                        eligibility.eligibility.excluded_activity_ids
                    ),
                    "excluded_categories": list(
                        eligibility.eligibility.excluded_categories
                    ),
                    "allowed_activity_levels": list(
                        eligibility.eligibility.allowed_activity_levels
                    ),
                    "allow_requires_other_person": (
                        eligibility.eligibility.allow_requires_other_person
                    ),
                    "allow_may_require_device_interaction": (
                        eligibility.eligibility.allow_may_require_device_interaction
                    ),
                },
                "interests": list(interests.selector_tags),
            },
            "pmum_parent_answers": {
                "age": parental.age,
                "sex_code": parental.sex_code,
                "weekday_category": parental.weekday_category,
                "weekend_category": parental.weekend_category,
                "respondent": parental.respondent.value,
                "reference_days": parental.reference_days,
            },
        }
        receipt = await self.child_offers.evaluate_adaptive(
            profile_id,
            device_id,
            payload,
        )
        return await self._result_from_receipt(
            data=data,
            profile_id=profile_id,
            device_id=device_id,
            receipt=receipt,
        )
