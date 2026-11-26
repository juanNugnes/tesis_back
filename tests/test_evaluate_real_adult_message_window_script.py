from types import SimpleNamespace

import pytest

from scripts import evaluate_real_adult_message_window as command


def settings(
    *,
    environment="development",
    database="postgresql+asyncpg://u:p@db/x",
    analytics="http://analytics:8765",
):
    return SimpleNamespace(
        environment=environment,
        database_url=database,
        analytics_service_url=analytics,
    )


def test_manual_real_weekly_command_requires_explicit_confirmation(monkeypatch):
    monkeypatch.setattr(command, "get_settings", lambda: settings())

    with pytest.raises(SystemExit, match="confirm-real-verified-window"):
        command.validate_local_development(confirmed=False)


def test_manual_real_weekly_command_rejects_nonlocal_or_nondevelopment(monkeypatch):
    monkeypatch.setattr(
        command,
        "get_settings",
        lambda: settings(environment="production"),
    )
    with pytest.raises(SystemExit, match="only in development"):
        command.validate_local_development(confirmed=True)

    monkeypatch.setattr(
        command,
        "get_settings",
        lambda: settings(
            database="postgresql+asyncpg://u:p@remote.example/x",
            analytics="https://analytics.example",
        ),
    )
    with pytest.raises(SystemExit, match="Only local development"):
        command.validate_local_development(confirmed=True)


def test_manual_real_weekly_command_accepts_internal_local_hosts(monkeypatch):
    monkeypatch.setattr(command, "get_settings", lambda: settings())

    command.validate_local_development(confirmed=True)
