"""Development-only manual handoff of one fictitious F12 preparation to C1."""

import argparse
import asyncio
import json
import uuid
from urllib.parse import urlparse

from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.repositories.f12_preparation_repository import F12PreparationRepository
from app.repositories.historical_submission_repository import (
    HistoricalSubmissionRepository,
)
from app.services.f12_submission_orchestrator import F12SubmissionOrchestrator
from app.services.historical_analytics_client import HistoricalAnalyticsClient
from app.services.historical_submission_service import HistoricalSubmissionService
from app.services.monitoring_authorization_service import (
    MonitoringAuthorizationService,
)


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "db", "analytics"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preparation_id", type=uuid.UUID)
    parser.add_argument(
        "--retry",
        action="store_true",
        help="Reuse the existing retry operation for a retryable technical error",
    )
    parser.add_argument(
        "--confirm-fictitious-data",
        action="store_true",
        help="Required acknowledgement that no real profile is being evaluated",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    settings = get_settings()
    analytics_host = urlparse(settings.analytics_service_url).hostname
    database_host = make_url(settings.database_url).host
    if settings.environment != "development":
        raise SystemExit("This command is available only in development")
    if not args.confirm_fictitious_data:
        raise SystemExit("--confirm-fictitious-data is required")
    if analytics_host not in _LOCAL_HOSTS or database_host not in _LOCAL_HOSTS:
        raise SystemExit("Only local development database and Analytics hosts are allowed")

    token = (
        settings.analytics_service_token.get_secret_value()
        if settings.analytics_service_token is not None
        else ""
    )
    async with SessionLocal() as db:
        historical = HistoricalSubmissionService(
            HistoricalSubmissionRepository(db),
            HistoricalAnalyticsClient(
                settings.analytics_service_url,
                token,
                settings.analytics_timeout_seconds,
            ),
        )
        orchestrator = F12SubmissionOrchestrator(
            F12PreparationRepository(db),
            historical,
            MonitoringAuthorizationService(db),
        )
        submission = (
            await orchestrator.retry(preparation_id=args.preparation_id)
            if args.retry
            else await orchestrator.submit(preparation_id=args.preparation_id)
        )
        print(
            json.dumps(
                {
                    "preparation_id": str(args.preparation_id),
                    "historical_submission_id": str(submission.id),
                    "request_id": submission.request_id,
                    "status": submission.status.value,
                    "attempts": submission.attempts,
                    "analytics_evaluation_id": submission.analytics_evaluation_id,
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
