from datetime import timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client_ip import otp_client_ip
from app.core.config import get_settings
from app.services.otp_rate_limiter import OtpRateLimitExceeded, PersistentRateLimiter


async def enforce_link_rate(
    db: AsyncSession,
    request: Request,
    ip_scope: str,
    ip_limit: int,
    dimensions: tuple[tuple[str, str, int], ...] = (),
) -> None:
    settings = get_settings()
    client_ip = otp_client_ip(request, settings.otp_trusted_proxy_cidrs)
    # Authentication dependencies may have opened a read transaction already.
    # No route writes before this call; release it before the SQLite write lock.
    await db.commit()
    try:
        await PersistentRateLimiter(
            db, settings.device_link_code_hash_secret.get_secret_value(), settings
        ).enforce_scopes(
            dimensions + ((ip_scope, client_ip, ip_limit),),
            timedelta(minutes=settings.link_rate_window_minutes),
        )
    except OtpRateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
