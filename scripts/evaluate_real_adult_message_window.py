"""Produce or retry one real verified weekly evaluation in local development."""

import argparse
import asyncio
import json
import uuid
from datetime import date
from urllib.parse import urlparse

from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.repositories.adult_message_evaluation_repository import (
    AdultMessageEvaluationRepository,
)
from app.repositories.adult_message_window_repository import (
    AdultMessageWindowRepository,
)
from app.services.adult_message_evaluation_service import (
    AdultMessageEvaluationService,
)
from app.services.adult_message_weekly_producer import AdultMessageWeeklyProducer
from app.services.adult_message_window_service import AdultMessageWindowService
from app.services.adult_messages_client import AdultMessagesClient


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "db", "postgres", "analytics"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--profile-id", type=uuid.UUID)
    operation.add_argument("--retry-job", type=uuid.UUID)
    parser.add_argument("--evaluation-date", type=date.fromisoformat)
    parser.add_argument("--confirm-real-verified-window", action="store_true")
    args = parser.parse_args()
    if args.profile_id is not None and args.evaluation_date is None:
        parser.error("--evaluation-date is required with --profile-id")
    if args.retry_job is not None and args.evaluation_date is not None:
        parser.error("--evaluation-date is only valid with --profile-id")
    return args


def validate_local_development(*, confirmed: bool) -> None:
    settings = get_settings()
    analytics_host = urlparse(settings.analytics_service_url).hostname
    database_host = make_url(settings.database_url).host
    if not confirmed:
        raise SystemExit("--confirm-real-verified-window is required")
    if settings.environment != "development":
        raise SystemExit("This command is available only in development")
    if analytics_host not in _LOCAL_HOSTS or database_host not in _LOCAL_HOSTS:
        raise SystemExit("Only local development database and Analytics hosts are allowed")


async def run(args: argparse.Namespace) -> None:
    validate_local_development(confirmed=args.confirm_real_verified_window)
    settings = get_settings()
    token = (
        settings.analytics_service_token.get_secret_value()
        if settings.analytics_service_token is not None
        else ""
    )
    async with SessionLocal() as db:
        evaluations = AdultMessageEvaluationService(
            AdultMessageEvaluationRepository(db),
            AdultMessagesClient(
                settings.analytics_service_url,
                token,
                settings.analytics_timeout_seconds,
            ),
        )
        if args.retry_job is not None:
            job = await evaluations.execute(args.retry_job, retry=True)
            diagnostic = None
        else:
            producer = AdultMessageWeeklyProducer(
                AdultMessageWindowService(AdultMessageWindowRepository(db)),
                evaluations,
            )
            result = await producer.produce(
                profile_id=args.profile_id,
                evaluation_date=args.evaluation_date,
            )
            diagnostic = result.diagnostic
            if result.blocked:
                print(
                    json.dumps(
                        {
                            "status": "BLOCKED",
                            "profile_id": str(args.profile_id),
                            "evaluation_date": args.evaluation_date.isoformat(),
                            "blockers": [
                                blocker.model_dump(mode="json")
                                for blocker in diagnostic.blockers
                            ],
                        },
                        sort_keys=True,
                    )
                )
                return
            job = await evaluations.execute(result.job.id)

        response = job.analytics_response or {}
        print(
            json.dumps(
                {
                    "status": job.status.value,
                    "job_id": str(job.id),
                    "source": job.source,
                    "request_id": job.request_id,
                    "profile_id": str(job.profile_id),
                    "series_id": job.series_id,
                    "evaluation_date": job.evaluation_date.isoformat(),
                    "window_fingerprint": job.window_fingerprint,
                    "payload_sha256": job.payload_sha256,
                    "attempts": job.attempts,
                    "analytics_status": response.get("status"),
                    "analytics_action": response.get("action"),
                    "analytics_evaluation_id": job.analytics_evaluation_id,
                    "analytics_card_id": job.analytics_card_id,
                    "analytics_card_version": job.analytics_card_version,
                    "analytics_replayed": job.analytics_replayed,
                    "retryable": job.retryable,
                    "error_code": job.last_error_code,
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
