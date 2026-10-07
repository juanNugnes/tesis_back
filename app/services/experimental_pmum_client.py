from dataclasses import dataclass
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.db.models import ExperimentalPmumEstimateStatus, ParentalRespondent


class ExperimentalPmumRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    age: int
    sex_code: int | None
    weekday_category: int | None
    weekend_category: int | None
    respondent: ParentalRespondent
    reference_days: int


class _ExperimentalPmumServiceResponse(BaseModel):
    status: Literal["estimated", "unavailable", "technical_error"]
    estimated_pmum_sf_mean: float | None = Field(default=None, ge=1, le=5)
    model_version: str | None = Field(default=None, max_length=100)
    technical_error_code: str | None = Field(default=None, max_length=50)
    technical_error_message: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_status_fields(self):
        if self.status == "estimated" and (
            self.estimated_pmum_sf_mean is None or not self.model_version
        ):
            raise ValueError("estimated responses require a score and model version")
        return self


@dataclass(frozen=True)
class ExperimentalPmumResult:
    status: ExperimentalPmumEstimateStatus
    estimated_pmum_sf_mean: float | None = None
    model_version: str | None = None
    technical_error_code: str | None = None
    technical_error_message: str | None = None

    @classmethod
    def technical_error(cls, code: str, message: str) -> "ExperimentalPmumResult":
        return cls(
            status=ExperimentalPmumEstimateStatus.TECHNICAL_ERROR,
            technical_error_code=code,
            technical_error_message=message,
        )


class ExperimentalPmumClient:
    def __init__(
        self,
        service_url: str,
        timeout_seconds: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.endpoint = f"{service_url.rstrip('/')}/experimental/pmum-estimate"
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def estimate(self, data: ExperimentalPmumRequest) -> ExperimentalPmumResult:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    self.endpoint,
                    json=data.model_dump(mode="json"),
                )
                response.raise_for_status()
        except httpx.TimeoutException:
            return ExperimentalPmumResult.technical_error(
                "TIMEOUT",
                "Experimental PMUM service timed out",
            )
        except httpx.ConnectError:
            return ExperimentalPmumResult.technical_error(
                "CONNECTION_ERROR",
                "Experimental PMUM service connection failed",
            )
        except httpx.HTTPStatusError as exc:
            return ExperimentalPmumResult.technical_error(
                "HTTP_ERROR",
                f"Experimental PMUM service returned HTTP {exc.response.status_code}",
            )
        except httpx.RequestError:
            return ExperimentalPmumResult.technical_error(
                "REQUEST_ERROR",
                "Experimental PMUM service request failed",
            )

        try:
            payload = _ExperimentalPmumServiceResponse.model_validate(response.json())
        except (ValueError, ValidationError):
            return ExperimentalPmumResult.technical_error(
                "INVALID_RESPONSE",
                "Experimental PMUM service returned an invalid response",
            )

        if payload.status == "estimated":
            return ExperimentalPmumResult(
                status=ExperimentalPmumEstimateStatus.ESTIMATED,
                estimated_pmum_sf_mean=payload.estimated_pmum_sf_mean,
                model_version=payload.model_version,
            )
        if payload.status == "unavailable":
            return ExperimentalPmumResult(
                status=ExperimentalPmumEstimateStatus.UNAVAILABLE,
                model_version=payload.model_version,
            )
        return ExperimentalPmumResult.technical_error(
            payload.technical_error_code or "MODEL_ERROR",
            "Experimental PMUM model reported a technical error",
        )
