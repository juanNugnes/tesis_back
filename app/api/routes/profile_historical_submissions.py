import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_current_device,
    require_device_profile_access,
    require_verified_profile_access,
)
from app.core.config import get_settings
from app.db.models import Device, HistoricalSubmission, HistoricalSubmissionStatus, Profile
from app.db.session import get_db
from app.repositories.historical_submission_repository import (
    HistoricalSubmissionRepository,
)
from app.schemas.historical_submission import (
    HistoricalSubmissionCreate,
    HistoricalSubmissionRead,
    HistoricalSubmissionSummaryRead,
)
from app.services.historical_analytics_client import HistoricalAnalyticsClient
from app.services.historical_submission_service import (
    HistoricalSubmissionAccessDeniedError,
    HistoricalSubmissionContentConflictError,
    HistoricalSubmissionNotFoundError,
    HistoricalSubmissionOrderError,
    HistoricalSubmissionRetryError,
    HistoricalSubmissionService,
)

router = APIRouter(
    prefix="/profiles/{profile_id}/historical-submissions",
    tags=["profile historical submissions"],
)


def get_historical_analytics_client() -> HistoricalAnalyticsClient:
    settings = get_settings()
    token = (
        settings.analytics_service_token.get_secret_value()
        if settings.analytics_service_token is not None
        else ""
    )
    return HistoricalAnalyticsClient(
        settings.analytics_service_url,
        token,
        settings.analytics_timeout_seconds,
    )


def _set_transport_status(response: Response, submission: HistoricalSubmission) -> None:
    if submission.status == HistoricalSubmissionStatus.CONFLICT:
        response.status_code = status.HTTP_409_CONFLICT
    elif submission.status == HistoricalSubmissionStatus.TECHNICAL_ERROR:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE


def _service(
    db: AsyncSession,
    client: HistoricalAnalyticsClient,
) -> HistoricalSubmissionService:
    return HistoricalSubmissionService(
        HistoricalSubmissionRepository(db),
        client,
    )


@router.get("", response_model=list[HistoricalSubmissionSummaryRead])
async def list_historical_submissions(
    response: Response,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    submission_status: HistoricalSubmissionStatus | None = Query(
        default=None,
        alias="status",
    ),
    profile: Profile = Depends(require_verified_profile_access),
    db: AsyncSession = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="date_from must not be after date_to",
        )
    submissions = await HistoricalSubmissionRepository(db).list_for_profile(
        profile.id,
        date_from=date_from,
        date_to=date_to,
        submission_status=submission_status,
    )
    return [
        HistoricalSubmissionSummaryRead.from_submission(submission)
        for submission in submissions
    ]


@router.post("", response_model=HistoricalSubmissionRead)
async def create_historical_submission(
    data: HistoricalSubmissionCreate,
    response: Response,
    profile: Profile = Depends(require_device_profile_access),
    current_device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
    analytics_client: HistoricalAnalyticsClient = Depends(
        get_historical_analytics_client
    ),
):
    try:
        submission = await _service(db, analytics_client).create(
            profile_id=profile.id,
            device=current_device,
            data=data,
        )
    except HistoricalSubmissionContentConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "HISTORICAL_SUBMISSION_CONTENT_CONFLICT",
                "message": "Request id or historical day already has different content",
            },
        ) from None
    except HistoricalSubmissionOrderError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "HISTORICAL_SUBMISSION_ORDER_BLOCKED",
                "message": "An earlier submission in this series is unresolved",
            },
        ) from None
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    _set_transport_status(response, submission)
    return submission


@router.post("/{submission_id}/retry", response_model=HistoricalSubmissionRead)
async def retry_historical_submission(
    submission_id: uuid.UUID,
    response: Response,
    profile: Profile = Depends(require_device_profile_access),
    current_device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
    analytics_client: HistoricalAnalyticsClient = Depends(
        get_historical_analytics_client
    ),
):
    try:
        submission = await _service(db, analytics_client).retry(
            submission_id=submission_id,
            profile_id=profile.id,
            device_id=current_device.id,
        )
    except HistoricalSubmissionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Historical submission not found",
        ) from None
    except HistoricalSubmissionAccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Historical submission access denied",
        ) from None
    except HistoricalSubmissionOrderError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "HISTORICAL_SUBMISSION_ORDER_BLOCKED",
                "message": "An earlier submission in this series is unresolved",
            },
        ) from None
    except HistoricalSubmissionRetryError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "HISTORICAL_SUBMISSION_NOT_RETRYABLE",
                "message": "Only retryable technical errors can be retried",
            },
        ) from None
    _set_transport_status(response, submission)
    return submission
