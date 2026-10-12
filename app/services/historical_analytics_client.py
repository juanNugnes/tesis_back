from dataclasses import dataclass
import json
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError


class _HistoricalEvaluationResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    evaluation_id: str
    outcome: Literal["ABSTAIN", "NO_CHANGE", "CHANGE"]


class _HistoricalResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: dict[str, Any]
    evaluation: _HistoricalEvaluationResponse
    emission: dict[str, Any]
    next_state: dict[str, Any]


class _HistoricalProcessedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["processed"]
    request_id: str
    replayed: bool
    historical_api_version: str
    configuration_id: str
    configuration_fingerprint: str
    scope: Literal["internal_technical_candidate"]
    result: _HistoricalResultResponse


@dataclass(frozen=True)
class HistoricalAnalyticsResult:
    kind: Literal["processed", "conflict", "technical_error"]
    response: dict[str, Any] | None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    replayed: bool | None = None
    evaluation_id: str | None = None

    @classmethod
    def technical_error(
        cls,
        code: str,
        message: str,
        *,
        response: dict[str, Any] | None = None,
        retryable: bool,
    ) -> "HistoricalAnalyticsResult":
        return cls(
            kind="technical_error",
            response=response,
            error_code=code,
            error_message=message,
            retryable=retryable,
        )


class HistoricalAnalyticsClient:
    def __init__(
        self,
        service_url: str,
        service_token: str,
        timeout_seconds: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.endpoint = f"{service_url.rstrip('/')}/v1/historical/evaluate"
        self.service_token = service_token
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def evaluate(self, payload: dict[str, Any]) -> HistoricalAnalyticsResult:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    self.endpoint,
                    headers={"Authorization": f"Bearer {self.service_token}"},
                    json=payload,
                )
        except httpx.TimeoutException:
            return HistoricalAnalyticsResult.technical_error(
                "HISTORICAL_TIMEOUT",
                "Analytics historical service timed out",
                retryable=True,
            )
        except httpx.ConnectError:
            return HistoricalAnalyticsResult.technical_error(
                "HISTORICAL_CONNECTION_ERROR",
                "Analytics historical service connection failed",
                retryable=True,
            )
        except httpx.RequestError:
            return HistoricalAnalyticsResult.technical_error(
                "HISTORICAL_REQUEST_ERROR",
                "Analytics historical service request failed",
                retryable=True,
            )

        response_payload = self._json_object(response)
        if response.status_code == 409:
            error_code = (
                response_payload.get("error")
                if response_payload is not None
                and isinstance(response_payload.get("error"), str)
                else "HISTORICAL_CONFLICT"
            )
            return HistoricalAnalyticsResult(
                kind="conflict",
                response=response_payload,
                error_code=error_code,
                error_message="Analytics rejected the historical submission as a conflict",
            )

        if response.status_code != 200:
            return HistoricalAnalyticsResult.technical_error(
                f"HISTORICAL_HTTP_{response.status_code}",
                f"Analytics historical service returned HTTP {response.status_code}",
                response=response_payload,
                retryable=response.status_code >= 500,
            )

        if response_payload is None:
            return HistoricalAnalyticsResult.technical_error(
                "HISTORICAL_INVALID_RESPONSE",
                "Analytics historical service returned invalid JSON",
                retryable=True,
            )
        try:
            parsed = _HistoricalProcessedResponse.model_validate(response_payload)
        except ValidationError:
            return HistoricalAnalyticsResult.technical_error(
                "HISTORICAL_INVALID_RESPONSE",
                "Analytics historical service returned an invalid response",
                response=response_payload,
                retryable=True,
            )
        if parsed.request_id != payload.get("request_id"):
            return HistoricalAnalyticsResult.technical_error(
                "HISTORICAL_INVALID_RESPONSE",
                "Analytics historical service returned a mismatched request id",
                response=response_payload,
                retryable=True,
            )
        return HistoricalAnalyticsResult(
            kind="processed",
            response=response_payload,
            replayed=parsed.replayed,
            evaluation_id=parsed.result.evaluation.evaluation_id,
        )

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any] | None:
        def reject_constant(_value: str):
            raise ValueError("non-finite JSON number")

        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value
            return result

        try:
            payload = json.loads(
                response.content.decode("utf-8"),
                parse_constant=reject_constant,
                object_pairs_hook=unique_object,
            )
        except (UnicodeDecodeError, ValueError, RecursionError):
            return None
        return payload if isinstance(payload, dict) else None
