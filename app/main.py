from fastapi import FastAPI

from app.api.routes import adult_auth, adult_profile_links, adult_profiles, alerts, analysis, consents, device_links, devices, experimental_pmum, profile_activity_preferences, profile_activity_selections, profile_activity_suggestions, profile_contexts, profile_historical_submissions, profile_settings, profiles, usage
from app.api.routes import profile_adult_messages
from app.core.config import get_settings

settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    description="Privacy-conscious aggregate device usage API. Risk levels are behavioral indicators, not medical diagnoses.",
    version="0.1.0",
)
for router in (
    profiles.router,
    adult_auth.router,
    adult_profile_links.router,
    adult_profiles.router,
    profile_adult_messages.router,
    device_links.router,
    devices.router,
    consents.router,
    experimental_pmum.router,
    profile_settings.router,
    profile_activity_preferences.router,
    profile_activity_suggestions.router,
    profile_activity_selections.router,
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
