"""Controlled operator helper for the manual MVP verification workflow."""

import argparse
import asyncio
import uuid

from app.db.session import SessionLocal
from app.services.representative_verification_service import (
    RepresentativeVerificationService,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("adult_id", type=uuid.UUID)
    parser.add_argument("profile_id", type=uuid.UUID)
    parser.add_argument("status", choices=("PENDING", "APPROVED", "REJECTED"))
    parser.add_argument("--verified-by")
    parser.add_argument("--notes")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    if args.status != "PENDING" and not args.verified_by:
        raise SystemExit("--verified-by is required for APPROVED or REJECTED")

    async with SessionLocal() as db:
        service = RepresentativeVerificationService(db)
        if args.status == "PENDING":
            verification = await service.set_pending(args.adult_id, args.profile_id)
        elif args.status == "APPROVED":
            verification = await service.approve(
                args.adult_id,
                args.profile_id,
                verified_by=args.verified_by,
                notes=args.notes,
            )
        else:
            verification = await service.reject(
                args.adult_id,
                args.profile_id,
                verified_by=args.verified_by,
                notes=args.notes,
            )
    print(f"{verification.adult_id} {verification.profile_id} {verification.status.value}")


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
