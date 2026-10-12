"""Typed application configuration.

Configuration is loaded once per process from the environment.  Start-up fails
loudly when a mandatory secret is missing, uses an example placeholder or is
too weak; there is deliberately no production-safe default for any secret.
"""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache
from typing import Annotated, Final, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "test", "staging", "production"]

MIN_KEY_BYTES: Final[int] = 32

_PLACEHOLDER_MARKERS: Final[tuple[str, ...]] = (
    "replace-me",
    "replace-with",
    "changeme",
    "change-me",
    "example",
    "placeholder",
    "insecure",
    "test-secret",
)


class ConfigurationError(ValueError):
    """Raised when the process cannot start with the supplied configuration.

    It derives from :class:`ValueError` so pydantic reports it as a normal
    validation failure when raised inside a validator.
    """


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def decode_key_material(value: str, *, field_name: str) -> bytes:
    """Decode a base64 (standard or url-safe) key and validate its strength."""
    candidate = value.strip()
    if not candidate:
        raise ConfigurationError(f"{field_name} is empty")
    if _looks_like_placeholder(candidate):
        raise ConfigurationError(f"{field_name} still contains an example placeholder value")
    padded = candidate + "=" * (-len(candidate) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            raw = decoder(padded)
        except (binascii.Error, ValueError):
            continue
        if len(raw) >= MIN_KEY_BYTES:
            return raw
    raw_utf8 = candidate.encode("utf-8")
    if len(raw_utf8) >= MIN_KEY_BYTES and len(set(raw_utf8)) >= 8:
        return raw_utf8
    raise ConfigurationError(
        f"{field_name} must decode to at least {MIN_KEY_BYTES} bytes of high-entropy material"
    )


class Settings(BaseSettings):
    """Immutable process configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        case_sensitive=False,
    )

    # --- Application -----------------------------------------------------
    app_env: AppEnv = "development"
    app_log_level: str = "INFO"
    app_timezone: str = "Europe/Rome"
    app_secret_key: SecretStr
    app_encryption_key: SecretStr
    app_encryption_key_version: Annotated[int, Field(ge=1, le=1_000)] = 1
    app_previous_encryption_keys: str = ""
    app_blind_index_key: SecretStr

    # --- Telegram --------------------------------------------------------
    telegram_bot_token: SecretStr
    telegram_bot_username: str
    telegram_admin_ids: str = ""

    # --- Database --------------------------------------------------------
    mysql_database: str = "opbg_bot"
    mysql_user: str = "opbg_bot"
    mysql_password: SecretStr
    database_host: str = "mysql"
    database_port: Annotated[int, Field(ge=1, le=65535)] = 3306
    database_pool_size: Annotated[int, Field(ge=1, le=64)] = 5
    database_max_overflow: Annotated[int, Field(ge=0, le=64)] = 5
    database_pool_recycle_seconds: Annotated[int, Field(ge=60, le=86_400)] = 1_800

    # --- Portal ----------------------------------------------------------
    portal_base_url: str = "https://prenotazioni.ospedalebambinogesu.it"
    # Reading a JSON endpoint is quick; waiting a full minute on one that is
    # not answering only multiplies the wait by the number of retries.
    portal_timeout_seconds: Annotated[float, Field(gt=0, le=180)] = 30.0
    # Submitting a wizard form is slow: measured against the real portal,
    # choosing a doctor regularly takes more than thirty seconds.
    portal_slow_timeout_seconds: Annotated[float, Field(gt=0, le=180)] = 90.0
    # A ceiling on one operation, retries included. Without it a flaky portal
    # keeps a person waiting minutes before being told it did not work, which
    # reads as a broken bot: better to give up early and let them try again.
    portal_operation_budget_seconds: Annotated[float, Field(gt=0, le=600)] = 90.0
    portal_max_concurrency: Annotated[int, Field(ge=1, le=16)] = 2
    portal_user_agent: str = "OPBG-Booking-Assistant/1.0"
    portal_min_interval_ms: Annotated[int, Field(ge=0, le=60_000)] = 800
    portal_max_retries: Annotated[int, Field(ge=0, le=8)] = 3
    portal_backoff_base_seconds: Annotated[float, Field(gt=0, le=60)] = 1.5
    portal_backoff_max_seconds: Annotated[float, Field(gt=0, le=600)] = 60.0
    portal_session_cookie_name: str = "opbg-prenotazione"

    # --- Monitoring ------------------------------------------------------
    monitor_default_interval_seconds: Annotated[int, Field(ge=60, le=86_400)] = 300
    monitor_min_interval_seconds: Annotated[int, Field(ge=120, le=86_400)] = 120
    monitor_jitter_seconds: Annotated[int, Field(ge=0, le=3_600)] = 30
    monitor_error_backoff_max_seconds: Annotated[int, Field(ge=60, le=86_400)] = 3_600
    monitor_lease_seconds: Annotated[int, Field(ge=30, le=3_600)] = 180
    monitor_batch_size: Annotated[int, Field(ge=1, le=100)] = 10
    monitor_max_consecutive_errors: Annotated[int, Field(ge=1, le=100)] = 8
    monitor_run_now_cooldown_seconds: Annotated[int, Field(ge=0, le=3_600)] = 60
    max_portal_accounts_per_user: Annotated[int, Field(ge=1, le=100)] = 10
    max_active_monitors_per_account: Annotated[int, Field(ge=1, le=200)] = 20
    appointment_sync_interval_seconds: Annotated[int, Field(ge=300, le=86_400)] = 3_600

    # --- Account recovery ------------------------------------------------
    #: An account the portal refused retries its stored credentials on its own.
    #: It is not a password loop: the attempts are spaced out, and a refusal
    #: still counts towards the lockout that hands the account to a human.
    account_recovery_enabled: bool = True
    account_recovery_delay_seconds: Annotated[int, Field(ge=60, le=86_400)] = 900
    account_recovery_batch_size: Annotated[int, Field(ge=1, le=50)] = 5

    # --- Circuit breaker -------------------------------------------------
    breaker_account_failure_threshold: Annotated[int, Field(ge=1, le=50)] = 5
    breaker_account_cooldown_seconds: Annotated[int, Field(ge=30, le=86_400)] = 900
    breaker_global_failure_threshold: Annotated[int, Field(ge=1, le=500)] = 20
    breaker_global_cooldown_seconds: Annotated[int, Field(ge=30, le=86_400)] = 600

    # --- Security and lifecycle -----------------------------------------
    invite_ttl_hours: Annotated[int, Field(ge=1, le=8_760)] = 72
    destructive_confirmation_ttl_seconds: Annotated[int, Field(ge=15, le=3_600)] = 120
    conversation_state_ttl_seconds: Annotated[int, Field(ge=60, le=86_400)] = 3_600
    booking_draft_ttl_seconds: Annotated[int, Field(ge=60, le=86_400)] = 3_600
    pdf_max_bytes: Annotated[int, Field(ge=1_024, le=104_857_600)] = 10_485_760
    audit_retention_days: Annotated[int, Field(ge=30, le=3_650)] = 365
    event_retention_days: Annotated[int, Field(ge=7, le=3_650)] = 90
    notification_retention_days: Annotated[int, Field(ge=7, le=3_650)] = 90
    seen_slot_retention_days: Annotated[int, Field(ge=1, le=365)] = 30
    appointment_retention_days: Annotated[int, Field(ge=30, le=3_650)] = 365
    connect_rate_limit_per_hour: Annotated[int, Field(ge=1, le=1_000)] = 10
    connect_max_failures: Annotated[int, Field(ge=1, le=50)] = 5
    consent_version: str = "1.0"

    # --- Presentation ----------------------------------------------------
    page_size: Annotated[int, Field(ge=3, le=20)] = 6
    max_slots_shown: Annotated[int, Field(ge=1, le=200)] = 40

    # --- Worker ----------------------------------------------------------
    worker_id: str = ""
    worker_poll_interval_seconds: Annotated[float, Field(gt=0, le=60)] = 5.0
    worker_heartbeat_seconds: Annotated[int, Field(ge=5, le=600)] = 30
    worker_heartbeat_stale_seconds: Annotated[int, Field(ge=15, le=3_600)] = 120

    # --- Validators ------------------------------------------------------
    @field_validator("app_log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError(f"unsupported log level: {value}")
        return normalized

    @field_validator("app_timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:  # pragma: no cover - depends on tzdata
            raise ValueError(f"unknown timezone: {value}") from exc
        return value

    @field_validator("portal_base_url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return cleaned

    @field_validator("telegram_bot_username")
    @classmethod
    def _validate_bot_username(cls, value: str) -> str:
        cleaned = value.strip().lstrip("@")
        if not cleaned:
            raise ValueError("telegram bot username must not be empty")
        return cleaned

    @field_validator("app_secret_key", "app_encryption_key", "app_blind_index_key")
    @classmethod
    def _validate_secret(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if _looks_like_placeholder(raw):
            raise ValueError("secret still contains an example placeholder value")
        if len(raw) < 32:
            raise ValueError("secret must be at least 32 characters long")
        return value

    @field_validator("telegram_bot_token", "mysql_password")
    @classmethod
    def _validate_required_secret(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw.strip():
            raise ValueError("secret must not be empty")
        if _looks_like_placeholder(raw):
            raise ValueError("secret still contains an example placeholder value")
        return value

    @model_validator(mode="after")
    def _validate_consistency(self) -> Settings:
        if self.monitor_default_interval_seconds < self.monitor_min_interval_seconds:
            raise ValueError(
                "monitor_default_interval_seconds must be >= monitor_min_interval_seconds"
            )
        decode_key_material(
            self.app_encryption_key.get_secret_value(), field_name="APP_ENCRYPTION_KEY"
        )
        decode_key_material(
            self.app_blind_index_key.get_secret_value(), field_name="APP_BLIND_INDEX_KEY"
        )
        for version, material in self.previous_encryption_keys.items():
            if version == self.app_encryption_key_version:
                raise ValueError(
                    "APP_PREVIOUS_ENCRYPTION_KEYS must not redeclare the active key version"
                )
            if len(material) < MIN_KEY_BYTES:
                raise ValueError(f"previous encryption key v{version} is too weak")
        return self

    # --- Derived helpers -------------------------------------------------
    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.app_timezone)

    @property
    def admin_telegram_ids(self) -> tuple[int, ...]:
        ids: list[int] = []
        for chunk in self.telegram_admin_ids.replace(";", ",").split(","):
            cleaned = chunk.strip()
            if not cleaned:
                continue
            try:
                ids.append(int(cleaned))
            except ValueError as exc:
                raise ConfigurationError(
                    f"TELEGRAM_ADMIN_IDS contains a non numeric entry: {cleaned!r}"
                ) from exc
        return tuple(dict.fromkeys(ids))

    @property
    def active_encryption_key(self) -> bytes:
        return decode_key_material(
            self.app_encryption_key.get_secret_value(), field_name="APP_ENCRYPTION_KEY"
        )

    @property
    def token_pepper(self) -> bytes:
        """Key material used to pepper the hashes of one-time tokens."""
        return decode_key_material(
            self.app_secret_key.get_secret_value(), field_name="APP_SECRET_KEY"
        )

    @property
    def blind_index_key(self) -> bytes:
        return decode_key_material(
            self.app_blind_index_key.get_secret_value(), field_name="APP_BLIND_INDEX_KEY"
        )

    @property
    def previous_encryption_keys(self) -> dict[int, bytes]:
        """Parse ``APP_PREVIOUS_ENCRYPTION_KEYS`` as ``version:base64`` pairs."""
        keys: dict[int, bytes] = {}
        for chunk in self.app_previous_encryption_keys.split(","):
            cleaned = chunk.strip()
            if not cleaned:
                continue
            version_text, separator, material = cleaned.partition(":")
            if not separator:
                raise ConfigurationError(
                    "APP_PREVIOUS_ENCRYPTION_KEYS entries must use the 'version:key' format"
                )
            try:
                version = int(version_text)
            except ValueError as exc:
                raise ConfigurationError(
                    "APP_PREVIOUS_ENCRYPTION_KEYS version must be an integer"
                ) from exc
            keys[version] = decode_key_material(
                material, field_name=f"APP_PREVIOUS_ENCRYPTION_KEYS[{version}]"
            )
        return keys

    @property
    def database_url(self) -> str:
        password = self.mysql_password.get_secret_value()
        return (
            "mysql+aiomysql://"
            f"{self.mysql_user}:{_quote(password)}@{self.database_host}:{self.database_port}"
            f"/{self.mysql_database}?charset=utf8mb4"
        )

    @property
    def sync_database_url(self) -> str:
        password = self.mysql_password.get_secret_value()
        return (
            "mysql+pymysql://"
            f"{self.mysql_user}:{_quote(password)}@{self.database_host}:{self.database_port}"
            f"/{self.mysql_database}?charset=utf8mb4"
        )

    def invite_link(self, code: str) -> str:
        """Deep link that opens the bot and redeems the invite in one tap."""
        return f"https://t.me/{self.telegram_bot_username}?start={code}"


def _quote(value: str) -> str:
    from urllib.parse import quote_plus

    return quote_plus(value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, validating them on first access."""
    try:
        return Settings()
    except Exception as exc:  # pragma: no cover - exercised through tests with bad env
        raise ConfigurationError(f"invalid application configuration: {exc}") from exc
