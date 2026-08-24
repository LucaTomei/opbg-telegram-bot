"""Configuration validation unit tests."""

from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from app.config import ConfigurationError, Settings, decode_key_material
from tests.conftest import _test_settings_env


def build(**overrides: str) -> Settings:
    values = {key.lower(): value for key, value in _test_settings_env().items()}
    values.update({key.lower(): value for key, value in overrides.items()})
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_valid_environment_produces_settings() -> None:
    settings = build()
    assert settings.app_env == "test"
    assert settings.admin_telegram_ids == (1001,)
    assert settings.timezone.key == "Europe/Rome"


@pytest.mark.parametrize(
    "placeholder",
    ["replace-me", "replace-with-a-valid-fernet-key", "changeme-changeme-changeme-1234"],
)
def test_example_placeholders_are_refused(placeholder: str) -> None:
    with pytest.raises(ValidationError):
        build(APP_ENCRYPTION_KEY=placeholder)


def test_short_secrets_are_refused() -> None:
    with pytest.raises(ValidationError):
        build(APP_SECRET_KEY="troppo-corta")


def test_low_entropy_key_material_is_refused() -> None:
    with pytest.raises(ConfigurationError):
        decode_key_material("a" * 40, field_name="APP_ENCRYPTION_KEY")


def test_key_material_accepts_url_safe_base64() -> None:
    material = base64.urlsafe_b64encode(bytes(range(32))).decode()
    assert decode_key_material(material, field_name="APP_ENCRYPTION_KEY") == bytes(range(32))


def test_empty_key_material_is_refused() -> None:
    with pytest.raises(ConfigurationError):
        decode_key_material("  ", field_name="APP_ENCRYPTION_KEY")


def test_production_requires_https() -> None:
    with pytest.raises(ValidationError):
        build(APP_ENV="production", PUBLIC_BASE_URL="http://insecure.example")


def test_unknown_timezone_is_refused() -> None:
    with pytest.raises(ValidationError):
        build(APP_TIMEZONE="Mars/Olympus")


def test_unknown_log_level_is_refused() -> None:
    with pytest.raises(ValidationError):
        build(APP_LOG_LEVEL="CHATTY")


def test_interval_consistency_is_enforced() -> None:
    with pytest.raises(ValidationError):
        build(MONITOR_DEFAULT_INTERVAL_SECONDS="120", MONITOR_MIN_INTERVAL_SECONDS="300")


def test_monitor_minimum_cannot_go_below_two_minutes() -> None:
    with pytest.raises(ValidationError):
        build(MONITOR_MIN_INTERVAL_SECONDS="30")


def test_admin_ids_are_parsed_and_deduplicated() -> None:
    settings = build(TELEGRAM_ADMIN_IDS="10, 20;10 , ")
    assert settings.admin_telegram_ids == (10, 20)


def test_non_numeric_admin_ids_fail_loudly() -> None:
    settings = build(TELEGRAM_ADMIN_IDS="mario")
    with pytest.raises(ConfigurationError):
        _ = settings.admin_telegram_ids


def test_bot_username_is_normalised() -> None:
    assert build(TELEGRAM_BOT_USERNAME="@bot_name").telegram_bot_username == "bot_name"


def test_urls_are_trimmed_and_validated() -> None:
    assert build(PUBLIC_BASE_URL="https://example.test/").public_base_url == (
        "https://example.test"
    )
    with pytest.raises(ValidationError):
        build(PUBLIC_BASE_URL="ftp://example.test")


def test_database_urls_quote_the_password() -> None:
    password = base64.urlsafe_b64encode(bytes(range(64, 96))).decode()
    settings = build(MYSQL_PASSWORD=password)
    assert "mysql+aiomysql://" in settings.database_url
    assert "mysql+pymysql://" in settings.sync_database_url
    assert "charset=utf8mb4" in settings.database_url


def test_previous_encryption_keys_are_parsed() -> None:
    previous = base64.urlsafe_b64encode(bytes(range(128, 160))).decode()
    settings = build(APP_ENCRYPTION_KEY_VERSION="2", APP_PREVIOUS_ENCRYPTION_KEYS=f"1:{previous}")
    assert set(settings.previous_encryption_keys) == {1}


def test_previous_keys_cannot_redeclare_the_active_version() -> None:
    previous = base64.urlsafe_b64encode(bytes(range(128, 160))).decode()
    with pytest.raises(ValidationError):
        build(APP_ENCRYPTION_KEY_VERSION="1", APP_PREVIOUS_ENCRYPTION_KEYS=f"1:{previous}")


def test_previous_keys_require_the_version_prefix() -> None:
    previous = base64.urlsafe_b64encode(bytes(range(128, 160))).decode()
    with pytest.raises(ValidationError):
        build(APP_PREVIOUS_ENCRYPTION_KEYS=previous)


def test_connect_and_pdf_urls_are_built_from_the_public_base() -> None:
    settings = build()
    assert settings.connect_url("abc") == "https://bot.test.invalid/connect/abc"
    assert settings.pdf_url("abc") == "https://bot.test.invalid/download/abc"


def test_secrets_are_not_exposed_by_repr() -> None:
    settings = build()
    rendered = repr(settings)
    assert settings.telegram_bot_token.get_secret_value() not in rendered
    assert settings.mysql_password.get_secret_value() not in rendered
