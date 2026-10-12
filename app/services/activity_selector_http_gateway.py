import uuid
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.services.activity_selector_gateway import (
    ActivityEligibility,
    ActivitySelectorResult,
    ActivitySelectorTechnicalError,
    SelectorActivity,
)


class _ActivityEligibilityRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    excluded_activity_ids: tuple[str, ...] = ()
    excluded_categories: tuple[str, ...] = ()
    allowed_activity_levels: tuple[str, ...] = ("calm", "moderate", "active")
    allow_requires_other_person: bool = True
    allow_may_require_device_interaction: bool = True


class _ActivitySelectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    context: str
    eligibility_state: Literal["known", "unknown"]
    eligibility: _ActivityEligibilityRequest | None = None
    interests: tuple[str, ...]
    recently_shown_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_eligibility(self):
        if self.eligibility_state == "known" and self.eligibility is None:
            raise ValueError("known eligibility requires an eligibility object")
        if self.eligibility_state == "unknown" and self.eligibility is not None:
            raise ValueError("unknown eligibility must omit eligibility")
        return self


class _SelectorActivityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


class _ActivitySelectorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["selected", "no_eligible_activities", "unavailable"]
    reason: str | None = None
    request_id: str
    selection_fingerprint: str | None = None
    catalog_version: str
    catalog_status: Literal["PROVISIONAL"]
    selector_version: str
    activities: list[_SelectorActivityResponse] = Field(max_length=3)

    @model_validator(mode="after")
    def validate_status(self):
        if self.status == "selected":
            if not self.activities or self.selection_fingerprint is None:
                raise ValueError("selected responses require activities and a fingerprint")
        elif self.activities:
            raise ValueError("non-selected responses must not contain activities")
        elif self.status == "no_eligible_activities":
            if self.selection_fingerprint is None:
                raise ValueError("selection responses require a fingerprint")
        return self


class HttpActivitySelectorGateway:
    def __init__(
        self,
        service_url: str,
        service_token: str,
        timeout_seconds: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.endpoint = f"{service_url.rstrip('/')}/v1/activities/select"
        self.service_token = service_token
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def recommend(
        self,
        *,
        context: str,
        interests: tuple[str, ...],
        eligibility_state: Literal["known", "unknown"],
        eligibility: ActivityEligibility | None,
        n: int = 3,
    ) -> ActivitySelectorResult:
        if n != 3:
            raise ActivitySelectorTechnicalError(
                "SELECTOR_REQUEST_ERROR",
                "Analytics activity selector only supports up to three activities",
            )
        request = _ActivitySelectorRequest(
            request_id=f"modera-{uuid.uuid4()}",
            context=context,
            interests=interests,
            eligibility_state=eligibility_state,
            eligibility=(
                _ActivityEligibilityRequest.model_validate(eligibility)
                if eligibility is not None
                else None
            ),
        )
        request_payload = request.model_dump(mode="json", exclude_none=True)
        if request.eligibility is not None:
            request_payload["eligibility"] = request.eligibility.model_dump(
                mode="json",
                exclude_defaults=True,
            )

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    self.endpoint,
                    headers={"Authorization": f"Bearer {self.service_token}"},
                    json=request_payload,
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ActivitySelectorTechnicalError(
                "SELECTOR_TIMEOUT",
                "Analytics activity selector timed out",
            ) from exc
        except httpx.ConnectError as exc:
            raise ActivitySelectorTechnicalError(
                "SELECTOR_CONNECTION_ERROR",
                "Analytics activity selector connection failed",
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ActivitySelectorTechnicalError(
                "SELECTOR_HTTP_ERROR",
                f"Analytics activity selector returned HTTP {exc.response.status_code}",
            ) from exc
        except httpx.RequestError as exc:
            raise ActivitySelectorTechnicalError(
                "SELECTOR_REQUEST_ERROR",
                "Analytics activity selector request failed",
            ) from exc

        try:
            payload = _ActivitySelectorResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise ActivitySelectorTechnicalError(
                "SELECTOR_INVALID_RESPONSE",
                "Analytics activity selector returned an invalid response",
            ) from exc
        if payload.request_id != request.request_id:
            raise ActivitySelectorTechnicalError(
                "SELECTOR_INVALID_RESPONSE",
                "Analytics activity selector returned a mismatched request id",
            )

        return ActivitySelectorResult(
            activities=tuple(
                SelectorActivity(
                    activity_id=activity.activity_id,
                    title=activity.title,
                    description=activity.description,
                    category=activity.category,
                    activity_level=activity.activity_level,
                    bedtime_suitable=activity.bedtime_suitable,
                    requires_other_person=activity.requires_other_person,
                    may_require_device_interaction=(
                        activity.may_require_device_interaction
                    ),
                )
                for activity in payload.activities
            ),
            reason=payload.reason,
        )
