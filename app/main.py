import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import adult_auth, adult_profile_links, adult_profiles, alerts, analysis, consents, device_links, devices, experimental_pmum, profile_activity_preferences, profile_activity_selections, profile_activity_suggestions, profile_contexts, profile_f12_evidence, profile_historical_submissions, profile_settings, profiles, usage
from app.api.routes import profile_adult_messages
from app.api.routes import profile_child_offers
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.f12_automatic_processing import F12AutomaticProcessingWorker


@asynccontextmanager
async def lifespan(_app: FastAPI):
    runtime_settings = get_settings()
    worker = None
    task = None
    if runtime_settings.f12_automatic_processing_enabled:
        token = (
            runtime_settings.analytics_service_token.get_secret_value()
            if runtime_settings.analytics_service_token is not None
            else ""
        )
        worker = F12AutomaticProcessingWorker(
            SessionLocal,
            analytics_service_url=runtime_settings.analytics_service_url,
            analytics_service_token=token,
            analytics_timeout_seconds=runtime_settings.analytics_timeout_seconds,
            poll_seconds=runtime_settings.f12_automatic_processing_poll_seconds,
            lease_seconds=runtime_settings.f12_automatic_processing_lease_seconds,
            retry_seconds=runtime_settings.f12_automatic_processing_retry_seconds,
            max_attempts=runtime_settings.f12_automatic_processing_max_attempts,
        )
        task = asyncio.create_task(worker.run_forever(), name="f12-automatic-processing")
    try:
        yield
    finally:
        if worker is not None and task is not None:
            worker.stop()
            await task

settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    description="Privacy-conscious aggregate device usage API. Risk levels are behavioral indicators, not medical diagnoses.",
    version="0.1.0",
    lifespan=lifespan,
)
for router in (
    profiles.router,
    adult_auth.router,
    adult_profile_links.router,
    adult_profiles.router,
    profile_adult_messages.router,
    profile_child_offers.router,
    device_links.router,
    devices.router,
    consents.router,
    experimental_pmum.router,
    profile_settings.router,
    profile_activity_preferences.router,
    profile_activity_suggestions.router,
    profile_activity_selections.router,
    profile_f12_evidence.router,
    profile_historical_submissions.router,
    profile_contexts.router,
    usage.router,
    analysis.router,
    alerts.router,
):
    app.include_router(router, prefix=settings.api_v1_prefix)


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}
