"""Run one explicitly fictitious adult-message evaluation in local development."""

import argparse
import asyncio
import json
import uuid
from pathlib import Path
from urllib.parse import urlparse

from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.repositories.adult_message_evaluation_repository import (
    AdultMessageEvaluationRepository,
)
from app.schemas.adult_message import ControlledAdultMessageFixture
from app.services.adult_message_evaluation_service import (
    AdultMessageEvaluationService,
)
from app.services.adult_messages_client import AdultMessagesClient


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "db", "postgres", "analytics"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument(
        "--fixture",
        type=Path,
        help="JSON wrapper marked fictional=true with an exact Analytics payload",
    )
    operation.add_argument(
        "--retry-job",
        type=uuid.UUID,
        help="Retry/reconcile an uncertain persisted job with its frozen request",
    )
    parser.add_argument(
        "--confirm-fictitious-data",
        action="store_true",
        help="Required acknowledgement that no real metrics are being evaluated",
    )
    return parser.parse_args()


def load_fixture(path: Path) -> ControlledAdultMessageFixture:
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
            path.read_text(encoding="utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
        return ControlledAdultMessageFixture.model_validate(payload)
    except (OSError, UnicodeDecodeError, ValueError, ValidationError) as exc:
        raise SystemExit(f"Invalid controlled fixture: {exc}") from None


def validate_local_development() -> None:
    settings = get_settings()
    analytics_host = urlparse(settings.analytics_service_url).hostname
    database_host = make_url(settings.database_url).host
    if settings.environment != "development":
        raise SystemExit("This command is available only in development")
    if analytics_host not in _LOCAL_HOSTS or database_host not in _LOCAL_HOSTS:
        raise SystemExit("Only local development database and Analytics hosts are allowed")


async def run(args: argparse.Namespace) -> None:
    if not args.confirm_fictitious_data:
        raise SystemExit("--confirm-fictitious-data is required")
    validate_local_development()
    settings = get_settings()
    token = (
        settings.analytics_service_token.get_secret_value()
        if settings.analytics_service_token is not None
        else ""
    )
    async with SessionLocal() as db:
        service = AdultMessageEvaluationService(
            AdultMessageEvaluationRepository(db),
            AdultMessagesClient(
                settings.analytics_service_url,
                token,
                settings.analytics_timeout_seconds,
            ),
        )
        if args.retry_job is not None:
            job = await service.execute(args.retry_job, retry=True)
        else:
            fixture = load_fixture(args.fixture)
            job = await service.create_from_fixture(fixture)
            job = await service.execute(job.id)
        response = job.analytics_response or {}
        print(
            json.dumps(
                {
                    "job_id": str(job.id),
                    "fixture_id": job.fixture_id,
                    "request_id": job.request_id,
                    "profile_id": str(job.profile_id),
                    "series_id": job.series_id,
                    "evaluation_date": job.evaluation_date.isoformat(),
                    "payload_sha256": job.payload_sha256,
                    "status": job.status.value,
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
