from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from app.api.dependencies import require_usage_monitoring_authorization
from app.core.config import get_settings
from app.db.models import Device
from app.db.session import get_db
from app.repositories.f12_evidence_repository import F12EvidenceRepository
from app.schemas.f12_evidence import (
    F12EvidenceReceipt,
    F12_MAX_HTTP_BODY_BYTES,
    F12_MAX_MANIFEST_BYTES,
    F12_MAX_PAIRS,
    F12_MAX_SOURCE_BYTES,
)
from app.services.f12_evidence_service import (
    F12EvidenceConflictError,
    F12EvidencePersistenceError,
    F12EvidenceService,
)
from app.services.f12_evidence_validator import (
    F12EvidenceValidationError,
    parse_manifest,
    validate_evidence,
)


router = APIRouter(
    prefix="/profiles/{profile_id}/f12-evidence/v1",
    tags=["profile F12 evidence"],
)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


async def _read_file(upload: UploadFile, remaining: int) -> bytes:
    chunks = []
    size = 0
    while chunk := await upload.read(min(1024 * 1024, remaining - size + 1)):
        size += len(chunk)
        if size > remaining:
            raise F12EvidenceValidationError(
                "evidence_too_large",
                "Evidence exceeds 32 MiB",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "",
    response_model=F12EvidenceReceipt,
    status_code=status.HTTP_201_CREATED,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["manifest"],
                        "properties": {
                            "manifest": {
                                "type": "string",
                                "description": "Strict f12-raw-evidence-v1 manifest JSON",
                            },
                            "summary": {
                                "type": "array",
                                "items": {"type": "string", "format": "binary"},
                            },
                            "events": {
                                "type": "array",
                                "items": {"type": "string", "format": "binary"},
                            },
                        },
                    }
                }
            },
        }
    },
    responses={
        status.HTTP_200_OK: {
            "model": F12EvidenceReceipt,
            "description": "The exact evidence and stable receipt were already stored.",
        },
        status.HTTP_409_CONFLICT: {
            "description": "The capture id or revision conflicts with stored evidence.",
        },
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: {
            "description": "The multipart body or decoded source evidence exceeds its limit.",
        },
    },
)
async def receive_f12_evidence(
    request: Request,
    response: Response,
    device: Device = Depends(require_usage_monitoring_authorization),
    db: AsyncSession = Depends(get_db),
):
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data;"):
        raise _error(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "unsupported_media_type",
            "Content-Type must be multipart/form-data",
        )
    content_length_value = request.headers.get("content-length")
    try:
        content_length = int(content_length_value) if content_length_value else None
    except ValueError:
        content_length = None
    if content_length is None or content_length < 0:
        raise _error(
            status.HTTP_411_LENGTH_REQUIRED,
            "content_length_required",
            "A valid Content-Length is required",
        )
    if content_length > F12_MAX_HTTP_BODY_BYTES:
        raise _error(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "request_too_large",
            "Multipart request exceeds 48 MiB",
        )

    try:
        async with request.form(
            max_files=2 * F12_MAX_PAIRS,
            max_fields=1,
            max_part_size=F12_MAX_MANIFEST_BYTES,
        ) as form:
            if not set(form.keys()).issubset({"manifest", "summary", "events"}):
                raise F12EvidenceValidationError(
                    "unexpected_multipart_part",
                    "Multipart body contains an unsupported part",
                )
            manifests = form.getlist("manifest")
            if len(manifests) != 1 or not isinstance(manifests[0], str):
                raise F12EvidenceValidationError(
                    "invalid_manifest_part",
                    "Multipart body requires exactly one text manifest part",
                )
            manifest_bytes = manifests[0].encode("utf-8")
            if len(manifest_bytes) > F12_MAX_MANIFEST_BYTES:
                raise F12EvidenceValidationError(
                    "manifest_too_large",
                    "Evidence manifest exceeds 64 KiB",
                )
            manifest = parse_manifest(manifest_bytes)

            summary_files = form.getlist("summary")
            event_files = form.getlist("events")
            if any(not isinstance(item, UploadFile) for item in summary_files + event_files):
                raise F12EvidenceValidationError(
                    "invalid_file_part",
                    "summary and events parts must be files",
                )
            if len(summary_files) != len(event_files):
                raise F12EvidenceValidationError(
                    "file_count_mismatch",
                    "Each summary.json requires one events.jsonl",
                )
            if len(summary_files) > F12_MAX_PAIRS:
                raise F12EvidenceValidationError(
                    "too_many_pairs",
                    "Evidence contains too many export pairs",
                )

            uploaded_pairs = []
            remaining = F12_MAX_SOURCE_BYTES
            for summary_file, events_file in zip(
                summary_files,
                event_files,
                strict=True,
            ):
                summary_bytes = await _read_file(summary_file, remaining)
                remaining -= len(summary_bytes)
                events_bytes = await _read_file(events_file, remaining)
                remaining -= len(events_bytes)
                uploaded_pairs.append((summary_bytes, events_bytes))
    except (MultiPartException, StarletteHTTPException) as exc:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_multipart",
            "Multipart body is invalid",
        ) from exc
    except F12EvidenceValidationError as exc:
        error_status = (
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            if exc.code in {"evidence_too_large", "manifest_too_large"}
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        raise _error(error_status, exc.code, str(exc)) from None

    try:
        evidence = validate_evidence(
            manifest,
            uploaded_pairs,
            profile_id=device.profile_id,
            device_id=device.id,
        )
        result = await F12EvidenceService(
            F12EvidenceRepository(db),
            retention_days=get_settings().f12_evidence_retention_days,
        ).ingest(
            evidence,
            profile_id=device.profile_id,
            device_id=device.id,
        )
    except F12EvidenceValidationError as exc:
        error_status = (
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            if exc.code == "evidence_too_large"
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        raise _error(error_status, exc.code, str(exc)) from None
    except F12EvidenceConflictError as exc:
        raise _error(status.HTTP_409_CONFLICT, exc.code, str(exc)) from None
    except F12EvidencePersistenceError:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "evidence_storage_unavailable",
            "F12 evidence could not be stored",
        ) from None

    response.status_code = (
        status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    )
    response.headers["X-F12-Evidence-Write-Result"] = (
        "created" if result.created else "unchanged"
    )
    response.headers["Cache-Control"] = "no-store"
    return result.receipt
