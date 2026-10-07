from dataclasses import dataclass

from app.db.models import (
    AdultAccount,
    ExperimentalPmumEstimate,
    ParentalScreenTimeResponse,
    Profile,
)
from app.repositories.experimental_pmum_repository import ExperimentalPmumRepository
from app.schemas.experimental_pmum import ParentalScreenTimeResponseCreate
from app.services.experimental_pmum_client import (
    ExperimentalPmumClient,
    ExperimentalPmumRequest,
)


class ExperimentalPmumResponseNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class ExperimentalPmumReview:
    response: ParentalScreenTimeResponse
    estimate: ExperimentalPmumEstimate | None


class ExperimentalPmumService:
    def __init__(
        self,
        repository: ExperimentalPmumRepository,
        client: ExperimentalPmumClient,
    ):
        self.repository = repository
        self.client = client

    async def create_review(
        self,
        profile: Profile,
        adult: AdultAccount,
        data: ParentalScreenTimeResponseCreate,
    ) -> ExperimentalPmumReview:
        response = await self.repository.create_response(profile, adult, data)
        model_request = ExperimentalPmumRequest(
            age=response.age,
            sex_code=response.sex_code,
            weekday_category=response.weekday_category,
            weekend_category=response.weekend_category,
            respondent=response.respondent,
            reference_days=response.reference_days,
        )
        result = await self.client.estimate(model_request)
        estimate = await self.repository.create_estimate(response.id, result)
        return ExperimentalPmumReview(response=response, estimate=estimate)

    async def latest(self, profile: Profile) -> ExperimentalPmumReview:
        response = await self.repository.latest_response(profile.id)
        if response is None:
            raise ExperimentalPmumResponseNotFoundError
        estimate = await self.repository.latest_estimate(response.id)
        return ExperimentalPmumReview(response=response, estimate=estimate)
