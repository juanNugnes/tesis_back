import os
import uuid

os.environ["ADULT_AUTH_JWT_SECRET"] = "test-only-adult-auth-secret-at-least-32-characters"
os.environ["ENVIRONMENT"] = "development"
os.environ["EXPOSE_DEV_OTP"] = "true"

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.api.dependencies import get_otp_delivery_service
from app.main import app
from app.repositories.adult_account_repository import AdultAccountRepository
from app.repositories.profile_repository import ProfileRepository
from app.services.adult_profile_service import AdultProfileService
from app.services.otp_delivery_service import FakeOtpDeliveryService


@pytest.fixture
def otp_delivery():
    return FakeOtpDeliveryService()


@pytest_asyncio.fixture
async def db_session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    yield sessions

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_session_factory):
    async with db_session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_session_factory, otp_delivery):
    sessions = db_session_factory

    async def override_db():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_otp_delivery_service] = lambda: otp_delivery
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def adult_session(client, db_session):
    async def create(profile_id, *, email=None):
        adult_email = email or f"adult-{uuid.uuid4()}@example.com"
        requested = await client.post("/api/v1/auth/adult/otp/request", json={"email": adult_email})
        assert requested.status_code == 202
        verified = await client.post(
            "/api/v1/auth/adult/otp/verify",
            json={"email": adult_email, "code": requested.json()["dev_code"]},
        )
        assert verified.status_code == 200

        adult = await AdultAccountRepository(db_session).get_by_email(adult_email.lower())
        profile = await ProfileRepository(db_session).get(uuid.UUID(str(profile_id)))
        assert adult is not None
        assert profile is not None
        await AdultProfileService(db_session).create_link(adult, profile)
        return {
            "Authorization": f"Bearer {verified.json()['access_token']}"
        }, adult

    return create

