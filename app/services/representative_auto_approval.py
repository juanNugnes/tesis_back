"""Restrict the MVP test approval mechanism to two explicitly configured emails."""

from app.schemas.adult_auth import normalize_email


def auto_approval_allowed(enabled: bool, raw_allowlist: str, email: str) -> bool:
    if not enabled:
        return False
    entries = raw_allowlist.split(",")
    if len(entries) != 2:
        return False
    try:
        normalized = [normalize_email(entry) for entry in entries]
        if any(len(entry) > 320 or "*" in entry or "?" in entry for entry in normalized) or len(set(normalized)) != 2:
            return False
        return normalize_email(email) in normalized
    except ValueError:
        return False
