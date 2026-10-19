import asyncio
import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.models import OtpRateLimitEvent
from app.services.otp_rate_limiter import OtpRateLimitExceeded, OtpRateLimiter


@pytest.mark.asyncio
async def test_postgres_concurrent_otp_requests_share_transactional_limit():
    url = os.getenv("OTP_RATE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set OTP_RATE_TEST_DATABASE_URL to a disposable PostgreSQL database")

    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(OtpRateLimitEvent.__table__.create)

        async def attempt():
            async with sessions() as session:
                limiter = OtpRateLimiter(
                    session, "test-only-rate-limit-secret", Settings()
                )
                try:
                    await limiter.enforce("request", "same@example.com", "203.0.113.40")
                    return "accepted"
                except OtpRateLimitExceeded:
                    return "limited"

        outcomes = await asyncio.gather(*(attempt() for _ in range(5)))
        assert outcomes.count("accepted") == 1
        assert outcomes.count("limited") == 4
        async with sessions() as session:
            assert len((await session.scalars(select(OtpRateLimitEvent))).all()) == 2
    finally:
        await engine.dispose()
