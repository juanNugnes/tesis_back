"""Private HTTP adapter for Analytics adult-messages v1."""

from dataclasses import dataclass
import json
from typing import Any, Literal
from uuid import UUID

import httpx
from pydantic import ValidationError

from app.schemas.adult_message import (
    AdultMessageEvaluationReceipt,
    AdultMessageHistory,
)


class AdultMessagesError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int, retryable: bool):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True)
class AdultMessageAnalyticsResult:
    kind: Literal["processed", "conflict", "technical_error"]
    response: dict[str, Any] | None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    replayed: bool | None = None
    evaluation_id: str | None = None
    card_id: str | None = None
    card_version: int | None = None

    @classmethod
    def technical_error(
        cls,
        code: str,
        message: str,
        *,
        response: dict[str, Any] | None = None,
        retryable: bool,
    ) -> "AdultMessageAnalyticsResult":
        return cls(
            kind="technical_error",
            response=response,
            error_code=code,
            error_message=message,
            retryable=retryable,
        )


class AdultMessagesClient:
    def __init__(
        self,
        service_url: str,
        service_token: str,
        timeout_seconds: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.service_url = service_url.rstrip("/")
        self.service_token = service_token
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def evaluate(self, payload: dict[str, Any]) -> AdultMessageAnalyticsResult:
        if not self.service_token:
            return AdultMessageAnalyticsResult.technical_error(
                "ADULT_MESSAGES_NOT_CONFIGURED",
                "Adult messages service is not configured",
                retryable=False,
            )
        endpoint = f"{self.service_url}/v1/adult-messages/evaluate"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {self.service_token}"},
                    json=payload,
                )
        except httpx.TimeoutException:
            return AdultMessageAnalyticsResult.technical_error(
                "ADULT_MESSAGES_TIMEOUT",
                "Analytics adult messages service timed out",
                retryable=True,
            )
        except httpx.RequestError:
            return AdultMessageAnalyticsResult.technical_error(
                "ADULT_MESSAGES_UNAVAILABLE",
                "Analytics adult messages service is unavailable",
                retryable=True,
            )

        response_payload = _decode_json_object(response)
        if response.status_code == 409:
            code = (
                response_payload.get("error")
                if response_payload is not None
                and isinstance(response_payload.get("error"), str)
                else "ADULT_MESSAGES_CONFLICT"
            )
            return AdultMessageAnalyticsResult(
                kind="conflict",
                response=response_payload,
                error_code=code,
                error_message="Analytics rejected the adult message evaluation as a conflict",
            )
        if response.status_code != 200:
            return AdultMessageAnalyticsResult.technical_error(
                f"ADULT_MESSAGES_HTTP_{response.status_code}",
                f"Analytics adult messages service returned HTTP {response.status_code}",
                response=response_payload,
                retryable=response.status_code >= 500 or response.status_code == 429,
            )
        if response_payload is None:
            return AdultMessageAnalyticsResult.technical_error(
                "ADULT_MESSAGES_INVALID_RESPONSE",
                "Analytics adult messages service returned invalid JSON",
                retryable=True,
            )
        try:
            parsed = AdultMessageEvaluationReceipt.model_validate(response_payload)
            if parsed.card is not None and (
                parsed.card.profile_id != payload.get("profile_id")
                or parsed.card.series_id != payload.get("series_id")
            ):
                raise ValueError("card identity mismatch")
            if parsed.c1_evidence_ids != payload.get("c1_evidence_ids"):
                raise ValueError("C1 evidence mismatch")
        except (ValueError, ValidationError):
            return AdultMessageAnalyticsResult.technical_error(
                "ADULT_MESSAGES_INVALID_RESPONSE",
                "Analytics adult messages service returned an invalid response",
                response=response_payload,
                retryable=True,
            )
        return AdultMessageAnalyticsResult(
            kind="processed",
            response=response_payload,
            replayed=parsed.replayed,
            evaluation_id=parsed.evaluation_id,
            card_id=parsed.card.card_id if parsed.card is not None else None,
            card_version=parsed.card_version,
        )

    async def history(self, profile_id: UUID) -> AdultMessageHistory:
        if not self.service_token:
            raise AdultMessagesError(
                "ADULT_MESSAGES_NOT_CONFIGURED", "Adult messages service is not configured",
                status_code=503, retryable=False,
            )
        endpoint = f"{self.service_url}/v1/adult-messages/profiles/{profile_id}/history"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, transport=self.transport, follow_redirects=False,
            ) as client:
                response = await client.get(
                    endpoint, headers={"Authorization": f"Bearer {self.service_token}"},
                )
        except httpx.TimeoutException:
            raise AdultMessagesError(
                "ADULT_MESSAGES_TIMEOUT", "Adult messages service timed out",
                status_code=504, retryable=True,
            ) from None
        except httpx.RequestError:
            raise AdultMessagesError(
                "ADULT_MESSAGES_UNAVAILABLE", "Adult messages service is unavailable",
                status_code=503, retryable=True,
            ) from None
        if response.status_code != 200:
            retryable = response.status_code >= 500 or response.status_code == 429
            raise AdultMessagesError(
                f"ADULT_MESSAGES_HTTP_{response.status_code}",
                "Adult messages service could not return history",
                status_code=503 if retryable else 502, retryable=retryable,
            )
        try:
            payload = _decode_json_object(response)
            if payload is None:
                raise ValueError("invalid JSON object")
            history = AdultMessageHistory.model_validate(payload)
            # Defensa adicional: nunca devolver tarjetas de otro perfil, aunque
            # provengan de una respuesta 200 del servicio interno.
            if any(item.card.profile_id != str(profile_id) for item in history.versions):
                raise ValueError("mismatched profile")
        except (UnicodeDecodeError, ValueError, ValidationError, RecursionError):
            raise AdultMessagesError(
                "ADULT_MESSAGES_INVALID_RESPONSE", "Adult messages service returned an invalid history",
                status_code=502, retryable=False,
            ) from None
        return history


def _reject_constant(_value):
    raise ValueError("non-finite JSON number")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _decode_json_object(response: httpx.Response) -> dict[str, Any] | None:
    try:
        payload = json.loads(
            response.content.decode("utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return payload if isinstance(payload, dict) else None
