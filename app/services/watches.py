"""Monitor lifecycle: creation, editing, duplication and state transitions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models.conversations import BookingDraft
from app.db.models.users import TelegramUser
from app.db.models.watches import BookingWatch
from app.db.repositories.accounts import PatientRepository, PortalAccountRepository
from app.db.repositories.base import Page, utc_now
from app.db.repositories.watches import WatchRepository
from app.domain.enums import (
    LocationMode,
    SearchType,
    WatchCompletionPolicy,
    WatchMode,
    WatchStatus,
)
from app.domain.errors import (
    NotFoundError,
    QuotaExceededError,
    RateLimitedError,
    ValidationError,
)
from app.logging import get_logger
from app.services.mapping import copy_filters

logger = get_logger(__name__)

MAX_NAME_LENGTH = 80


@dataclass(frozen=True, slots=True)
class WatchSummary:
    """Compact, mask-safe description of a monitor for list screens."""

    watch: BookingWatch
    account_label: str
    patient_label: str
    location_summary: str


class WatchService:
    """Creates and manages availability monitors."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def clamp_interval(self, seconds: int) -> int:
        minimum = self._settings.monitor_min_interval_seconds
        return max(minimum, min(seconds, 86_400))

    async def create_from_draft(
        self,
        session: AsyncSession,
        *,
        user: TelegramUser,
        draft: BookingDraft,
        name: str,
        mode: WatchMode,
        notifications_enabled: bool,
        notify_only_changes: bool,
        interval_seconds: int,
        completion_policy: WatchCompletionPolicy,
    ) -> BookingWatch:
        """Materialise a monitor from a completed booking draft."""
        if draft.portal_account_id is None or draft.patient_id is None:
            raise ValidationError("account and patient are required")
        if draft.search_type is None or not draft.query_text:
            raise ValidationError("a search is required")
        if draft.search_type is SearchType.SERVICE and not draft.selected_service_external_id:
            raise ValidationError("an exact service must be selected")
        if draft.search_type is SearchType.DOCTOR and not draft.selected_doctor_external_id:
            raise ValidationError("an exact doctor must be selected")

        watches = WatchRepository(session)
        active = await watches.count_active_for_account(draft.portal_account_id)
        if active >= self._settings.max_active_monitors_per_account:
            raise QuotaExceededError(
                "the maximum number of monitors for this account has been reached",
                limit=str(self._settings.max_active_monitors_per_account),
            )

        watch = BookingWatch(
            telegram_user_id=user.id,
            portal_account_id=draft.portal_account_id,
            patient_id=draft.patient_id,
            name=self._clean_name(name),
            search_type=draft.search_type,
            query_text=draft.query_text[:160],
            selected_service_external_id=draft.selected_service_external_id,
            selected_service_name=draft.selected_service_name,
            selected_specification_id=draft.selected_specification_id,
            selected_doctor_external_id=draft.selected_doctor_external_id,
            selected_doctor_name=draft.selected_doctor_name,
            mode=mode,
            notifications_enabled=notifications_enabled,
            notify_only_changes=notify_only_changes,
            completion_policy=completion_policy,
            status=WatchStatus.ACTIVE,
            interval_seconds=self.clamp_interval(interval_seconds),
            max_results=self._settings.max_slots_shown,
            next_check_at=utc_now(),
        )
        copy_filters(draft, watch)
        watch.quiet_hours_start = user.quiet_hours_start
        watch.quiet_hours_end = user.quiet_hours_end
        await watches.add(watch)
        await self._copy_locations(session, watch, draft)
        logger.info(
            "monitor created",
            extra={"event_code": "WATCH_CREATED", "watch_id": watch.id.hex, "mode": mode.value},
        )
        return watch

    async def _copy_locations(
        self, session: AsyncSession, watch: BookingWatch, draft: BookingDraft
    ) -> None:
        watches = WatchRepository(session)
        if draft.location_mode is LocationMode.ALL_DYNAMIC:
            await watches.set_locations(watch, [])
            return
        entries = [
            (
                str(entry.get("id", "")),
                str(entry.get("name", entry.get("id", ""))),
                index,
            )
            for index, entry in enumerate(draft.selected_locations_json)
            if entry.get("id")
        ]
        await watches.set_locations(watch, entries)

    @staticmethod
    def _clean_name(name: str) -> str:
        cleaned = " ".join(name.strip().split())[:MAX_NAME_LENGTH]
        if not cleaned:
            raise ValidationError("a name is required")
        return cleaned

    async def require_watch(
        self, session: AsyncSession, user: TelegramUser, watch_id: uuid.UUID
    ) -> BookingWatch:
        watch = await WatchRepository(session).get_owned(watch_id, user.id)
        if watch is None:
            raise NotFoundError("monitor not found")
        return watch

    async def list_for_user(
        self,
        session: AsyncSession,
        user: TelegramUser,
        *,
        page: int,
        page_size: int,
        status: WatchStatus | None = None,
    ) -> Page[BookingWatch]:
        return await WatchRepository(session).list_for_user(
            user.id, page=page, page_size=page_size, status=status
        )

    async def summarise(self, session: AsyncSession, watch: BookingWatch) -> WatchSummary:
        """Build a summary with masked account and patient identifiers."""
        from app.security.redaction import mask_person_name

        accounts = PortalAccountRepository(session)
        patients = PatientRepository(session)
        account = await accounts.get(watch.portal_account_id)
        patient = await patients.get(watch.patient_id)
        locations = await WatchRepository(session).locations_for(watch.id)
        if watch.location_mode is LocationMode.ALL_DYNAMIC:
            summary = "tutte le sedi"
        elif locations:
            summary = ", ".join(item.location_name for item in locations)
        else:
            summary = "nessuna sede selezionata"
        patient_name = patient.display_name if patient is not None else None
        return WatchSummary(
            watch=watch,
            account_label=account.label if account is not None else "account rimosso",
            patient_label=mask_person_name(patient_name) if patient_name else "paziente",
            location_summary=summary,
        )

    async def duplicate(
        self, session: AsyncSession, watch: BookingWatch, *, name: str | None = None
    ) -> BookingWatch:
        """Clone a monitor, including its filters and location preferences."""
        watches = WatchRepository(session)
        active = await watches.count_active_for_account(watch.portal_account_id)
        if active >= self._settings.max_active_monitors_per_account:
            raise QuotaExceededError(
                "the maximum number of monitors for this account has been reached"
            )
        clone = BookingWatch(
            telegram_user_id=watch.telegram_user_id,
            portal_account_id=watch.portal_account_id,
            patient_id=watch.patient_id,
            name=self._clean_name(name or f"{watch.name} (copia)"),
            search_type=watch.search_type,
            query_text=watch.query_text,
            selected_service_external_id=watch.selected_service_external_id,
            selected_service_name=watch.selected_service_name,
            selected_specification_id=watch.selected_specification_id,
            selected_doctor_external_id=watch.selected_doctor_external_id,
            selected_doctor_name=watch.selected_doctor_name,
            location_mode=watch.location_mode,
            date_mode=watch.date_mode,
            date_from=watch.date_from,
            date_to=watch.date_to,
            rolling_days=watch.rolling_days,
            min_lead_hours=watch.min_lead_hours,
            weekdays_mask=watch.weekdays_mask,
            time_windows_json=list(watch.time_windows_json),
            excluded_dates_json=list(watch.excluded_dates_json),
            sort_strategy=watch.sort_strategy,
            max_results=watch.max_results,
            mode=watch.mode,
            notifications_enabled=watch.notifications_enabled,
            notify_only_changes=watch.notify_only_changes,
            quiet_hours_start=watch.quiet_hours_start,
            quiet_hours_end=watch.quiet_hours_end,
            completion_policy=watch.completion_policy,
            status=WatchStatus.PAUSED,
            interval_seconds=watch.interval_seconds,
        )
        await watches.add(clone)
        existing = await watches.locations_for(watch.id)
        await watches.set_locations(
            clone,
            [
                (item.portal_location_id, item.location_name, item.preference_order)
                for item in existing
            ],
        )
        return clone

    async def pause(self, session: AsyncSession, watch: BookingWatch) -> None:
        await WatchRepository(session).set_status(watch, WatchStatus.PAUSED, reason="USER_PAUSED")

    async def resume(self, session: AsyncSession, watch: BookingWatch) -> None:
        watches = WatchRepository(session)
        watch.consecutive_errors = 0
        watch.last_error_code = None
        watch.completed_reason = None
        watch.next_check_at = utc_now()
        await watches.set_status(watch, WatchStatus.ACTIVE)

    async def set_notifications(
        self, session: AsyncSession, watch: BookingWatch, *, enabled: bool
    ) -> None:
        """Notifications are independent from auto-booking."""
        watch.notifications_enabled = enabled
        await WatchRepository(session).flush()

    async def set_mode(
        self, session: AsyncSession, watch: BookingWatch, *, mode: WatchMode
    ) -> None:
        watch.mode = mode
        await WatchRepository(session).flush()

    async def set_interval(
        self, session: AsyncSession, watch: BookingWatch, *, seconds: int
    ) -> None:
        watch.interval_seconds = self.clamp_interval(seconds)
        await WatchRepository(session).flush()

    async def rename(self, session: AsyncSession, watch: BookingWatch, *, name: str) -> None:
        watch.name = self._clean_name(name)
        await WatchRepository(session).flush()

    async def delete(self, session: AsyncSession, watch: BookingWatch) -> None:
        await WatchRepository(session).soft_delete(watch)
        logger.info(
            "monitor deleted", extra={"event_code": "WATCH_DELETED", "watch_id": watch.id.hex}
        )

    async def request_run_now(
        self, session: AsyncSession, watch: BookingWatch, *, now: datetime | None = None
    ) -> datetime:
        """Schedule an immediate run, respecting the anti-spam cooldown."""
        moment = now or utc_now()
        cooldown = self._settings.monitor_run_now_cooldown_seconds
        if watch.last_run_now_at is not None:
            elapsed = (moment - watch.last_run_now_at).total_seconds()
            if elapsed < cooldown:
                raise RateLimitedError(
                    "the monitor was executed too recently",
                    retry_after_seconds=cooldown - elapsed,
                )
        if watch.status is not WatchStatus.ACTIVE:
            raise ValidationError("only an active monitor can be executed now")
        watch.last_run_now_at = moment
        watch.next_check_at = moment
        await WatchRepository(session).flush()
        return moment

    async def complete(self, session: AsyncSession, watch: BookingWatch, *, reason: str) -> None:
        if watch.completion_policy is WatchCompletionPolicy.CONTINUE_AFTER_SUCCESS:
            return
        await WatchRepository(session).set_status(watch, WatchStatus.COMPLETED, reason=reason)
