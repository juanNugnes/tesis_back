from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_adult, require_profile_access
from app.core.config import get_settings
from app.db.models import AdultAccount, Profile
from app.db.session import get_db
from app.repositories.experimental_pmum_repository import ExperimentalPmumRepository
from app.schemas.experimental_pmum import (
    ExperimentalPmumEstimateRead,
    ExperimentalPmumReviewRead,
    ParentalScreenTimeResponseCreate,
    ParentalScreenTimeResponseRead,
)
from app.services.experimental_pmum_client import ExperimentalPmumClient
from app.services.experimental_pmum_service import (
    ExperimentalPmumResponseNotFoundError,
    ExperimentalPmumReview,
    ExperimentalPmumService,
)

router = APIRouter(
    prefix="/profiles/{profile_id}/experimental-pmum",
    tags=["experimental PMUM"],
)


def get_experimental_pmum_client() -> ExperimentalPmumClient:
    settings = get_settings()
    return ExperimentalPmumClient(
        settings.experimental_pmum_service_url,
        settings.experimental_pmum_service_timeout_seconds,
    )


def review_read(review: ExperimentalPmumReview) -> ExperimentalPmumReviewRead:
    return ExperimentalPmumReviewRead(
        response=ParentalScreenTimeResponseRead.model_validate(review.response),
        estimate=(
            ExperimentalPmumEstimateRead.model_validate(review.estimate)
            if review.estimate is not None
            else None
        ),
    )


@router.post(
    "/responses",
    response_model=ExperimentalPmumReviewRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_experimental_pmum_response(
    data: ParentalScreenTimeResponseCreate,
    profile: Profile = Depends(require_profile_access),
    current_adult: AdultAccount = Depends(get_current_adult),
    db: AsyncSession = Depends(get_db),
    model_client: ExperimentalPmumClient = Depends(get_experimental_pmum_client),
):
    review = await ExperimentalPmumService(
        ExperimentalPmumRepository(db),
        model_client,
    ).create_review(profile, current_adult, data)
    return review_read(review)


@router.get("/latest", response_model=ExperimentalPmumReviewRead)
async def read_latest_experimental_pmum_response(
    profile: Profile = Depends(require_profile_access),
    db: AsyncSession = Depends(get_db),
    model_client: ExperimentalPmumClient = Depends(get_experimental_pmum_client),
):
    try:
        review = await ExperimentalPmumService(
            ExperimentalPmumRepository(db),
            model_client,
        ).latest(profile)
    except ExperimentalPmumResponseNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No experimental PMUM response found",
        )
    return review_read(review)
