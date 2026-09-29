from fastapi import FastAPI

from app.api.routes import alerts, analysis, profile_activity_preferences, profile_contexts, profile_settings, profiles, usage
from app.core.config import get_settings

settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    description="Privacy-conscious aggregate device usage API. Risk levels are behavioral indicators, not medical diagnoses.",
    version="0.1.0",
)
for router in (
    profiles.router,
    profile_settings.router,
    profile_activity_preferences.router,
    profile_contexts.router,
    usage.router,
    analysis.router,
    alerts.router,
):
    app.include_router(router, prefix=settings.api_v1_prefix)


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}
