"""Private HTTP adapter for the real Analytics child-offer coordinator."""

import json

import httpx
from pydantic import ValidationError

from app.schemas.child_offer import (
    ChildOfferEvaluationReceipt,
    ChildOfferEventReceipt,
    ChildOfferHistory,
)


class ChildOffersError(Exception):
    def __init__(self, code: str, status_code: int):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("non-finite number")


class ChildOffersClient:
    def __init__(
        self, service_url: str, service_token: str, timeout_seconds: float,
        *, transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.root = f"{service_url.rstrip('/')}/v1/child-offers"
        self.token = service_token
        self.timeout = timeout_seconds
        self.transport = transport

    async def evaluate(self, payload: dict) -> dict:
        return await self._request(
            "POST", "/evaluate", payload, ChildOfferEvaluationReceipt,
            payload["evidence"]["profile_id"],
        )

    async def event(self, payload: dict) -> dict:
        return await self._request(
            "POST", "/events", payload, ChildOfferEventReceipt, payload["profile_id"],
        )

    async def history(self, profile_id: str) -> dict:
        return await self._request(
            "GET", f"/profiles/{profile_id}/history", None, ChildOfferHistory, profile_id,
        )

    async def _request(self, method, path, payload, schema, profile_id):
        if not self.token:
            raise ChildOffersError("CHILD_OFFERS_NOT_CONFIGURED", 503)
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self.transport, follow_redirects=False,
            ) as client:
                response = await client.request(
                    method, self.root + path,
                    headers={"Authorization": f"Bearer {self.token}"}, json=payload,
                )
        except httpx.TimeoutException:
            raise ChildOffersError("CHILD_OFFERS_TIMEOUT", 504) from None
        except httpx.RequestError:
            raise ChildOffersError("CHILD_OFFERS_UNAVAILABLE", 503) from None
        if response.status_code != 200:
            if response.status_code == 409:
                raise ChildOffersError("CHILD_OFFERS_CONFLICT", 409)
            if response.status_code == 400:
                raise ChildOffersError("CHILD_OFFERS_INVALID_REQUEST", 400)
            if response.status_code == 503:
                raise ChildOffersError("CHILD_OFFERS_UNAVAILABLE", 503)
            raise ChildOffersError("CHILD_OFFERS_UPSTREAM_ERROR", 502)
        try:
            raw = json.loads(
                response.content.decode("utf-8"), object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
            validated = schema.model_validate(raw)
            if schema is ChildOfferHistory:
                offers = validated.offers
            else:
                offers = [validated.offer] if validated.offer is not None else []
            if any(offer.profile_id != profile_id for offer in offers):
                raise ValueError("profile mismatch")
            if schema is ChildOfferHistory and any(
                event.get("profile_id", profile_id) != profile_id for event in validated.events
            ):
                raise ValueError("event profile mismatch")
            if payload is not None and raw.get("request_id") not in (None, payload["request_id"]):
                raise ValueError("request mismatch")
            return raw
        except (UnicodeDecodeError, ValueError, ValidationError, RecursionError, TypeError):
            raise ChildOffersError("CHILD_OFFERS_INVALID_RESPONSE", 502) from None
