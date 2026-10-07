import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AdultAccount,
    ExperimentalPmumEstimate,
    ParentalScreenTimeResponse,
    Profile,
)
from app.schemas.experimental_pmum import ParentalScreenTimeResponseCreate
from app.services.experimental_pmum_client import ExperimentalPmumResult


class ExperimentalPmumRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_response(
        self,
        profile: Profile,
        adult: AdultAccount,
        data: ParentalScreenTimeResponseCreate,
    ) -> ParentalScreenTimeResponse:
        locked_profile = await self.db.scalar(
            select(Profile)
            .where(Profile.id == profile.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked_profile is None:
            raise RuntimeError("Profile disappeared while creating PMUM revision")
        latest_revision = await self.db.scalar(
            select(func.max(ParentalScreenTimeResponse.revision)).where(
                ParentalScreenTimeResponse.profile_id == profile.id
            )
        )
        response = ParentalScreenTimeResponse(
            profile_id=profile.id,
            adult_id=adult.id,
            revision=(latest_revision or 0) + 1,
            age=locked_profile.age,
            **data.model_dump(),
        )
        self.db.add(response)
        await self.db.commit()
        await self.db.refresh(response)
        return response

    async def create_estimate(
        self,
        parental_response_id: uuid.UUID,
        result: ExperimentalPmumResult,
    ) -> ExperimentalPmumEstimate:
        estimate = ExperimentalPmumEstimate(
            parental_response_id=parental_response_id,
            status=result.status,
            estimated_pmum_sf_mean=result.estimated_pmum_sf_mean,
            model_version=result.model_version,
            technical_error_code=result.technical_error_code,
            technical_error_message=result.technical_error_message,
        )
        self.db.add(estimate)
        await self.db.commit()
        await self.db.refresh(estimate)
        return estimate

    async def latest_response(
        self,
        profile_id: uuid.UUID,
    ) -> ParentalScreenTimeResponse | None:
        return await self.db.scalar(
            select(ParentalScreenTimeResponse)
            .where(ParentalScreenTimeResponse.profile_id == profile_id)
            .order_by(ParentalScreenTimeResponse.revision.desc())
            .limit(1)
        )

    async def latest_estimate(
        self,
        parental_response_id: uuid.UUID,
    ) -> ExperimentalPmumEstimate | None:
        return await self.db.scalar(
            select(ExperimentalPmumEstimate)
            .where(
                ExperimentalPmumEstimate.parental_response_id == parental_response_id
            )
            .order_by(ExperimentalPmumEstimate.estimated_at.desc())
            .limit(1)
        )
