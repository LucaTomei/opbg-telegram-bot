"""Rendering and delivery of queued notifications.

The worker owns the queue; this module turns a persisted event into an Italian
message plus the inline keyboard that lets the user act on it immediately.
"""

from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup

from app.bot import callbacks, texts
from app.bot.keyboards.inline import Entry, build_actions
from app.bot.presenters.formatting import escape
from app.db.models.notifications import NotificationEvent
from app.db.models.users import TelegramUser
from app.domain.enums import NotificationEventType, PortalAccountStatus
from app.logging import get_logger

logger = get_logger(__name__)

MAX_SLOTS_IN_MESSAGE = 8


class TelegramNotificationSender:
    """Send a queued notification through the bot API."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(self, *, chat_id: int, text: str, keyboard: Any | None = None) -> int | None:
        try:
            message = await self._bot.send_message(
                chat_id,
                text,
                reply_markup=keyboard if isinstance(keyboard, InlineKeyboardMarkup) else None,
                parse_mode="HTML",
                disable_notification=False,
            )
        except TelegramRetryAfter:
            # Propagated so the queue applies its own backoff.
            raise
        except TelegramForbiddenError:
            logger.warning("user blocked the bot", extra={"event_code": "NOTIFICATION_FORBIDDEN"})
            return None
        return message.message_id


def _payload(event: NotificationEvent) -> dict[str, Any]:
    payload = event.payload
    return payload if isinstance(payload, dict) else {}


def _watch_keyboard(event: NotificationEvent) -> InlineKeyboardMarkup:
    if event.watch_id is None:
        return build_actions([])
    return build_actions(
        [
            [
                Entry(
                    "🔍 Dettagli",
                    callbacks.build(callbacks.WATCH, "open", event.watch_id),
                ),
                Entry(
                    "⏸ Pausa",
                    callbacks.build(callbacks.WATCH, "pause", event.watch_id),
                ),
            ],
            [
                Entry(
                    "🔕 Silenzia",
                    callbacks.build(callbacks.WATCH, "tn", event.watch_id),
                ),
                Entry("🗓 Prenota ora", callbacks.build(callbacks.BOOKING, "start", 0)),
            ],
        ]
    )


def _appointments_keyboard() -> InlineKeyboardMarkup:
    return build_actions(
        [
            [
                Entry(
                    texts.BTN_MY_APPOINTMENTS,
                    callbacks.build(callbacks.APPOINTMENT, "list", 0),
                )
            ]
        ]
    )


def _accounts_keyboard() -> InlineKeyboardMarkup:
    return build_actions(
        [[Entry("👤 Vai agli account", callbacks.build(callbacks.ACCOUNT, "list", 0))]]
    )


def render_notification(
    event: NotificationEvent, user: TelegramUser
) -> tuple[str, InlineKeyboardMarkup]:
    """Build the Italian message and keyboard for one queued notification."""
    payload = _payload(event)
    watch_name = escape(str(payload.get("watch_name", "monitoraggio")))

    if event.event_type is NotificationEventType.NEW_SLOT:
        slots = payload.get("slots") or []
        lines = [
            f"🔔 <b>Nuove disponibilità</b> · {watch_name}",
            f"Trovate <b>{payload.get('count', len(slots))}</b> proposte compatibili.",
            "",
        ]
        for entry in list(slots)[:MAX_SLOTS_IN_MESSAGE]:
            if not isinstance(entry, dict):
                continue
            doctor = escape(str(entry.get("doctor", "")))
            suffix = f" · {doctor}" if doctor else ""
            lines.append(
                f"• {escape(str(entry.get('when', '')))} — "
                f"{escape(str(entry.get('location', '')))}{suffix}"
            )
        lines.append("")
        lines.append("Apri il monitoraggio per prenotare: gli slot vengono rivalidati.")
        return "\n".join(lines), _watch_keyboard(event)

    if event.event_type is NotificationEventType.SLOT_LOST:
        return (
            f"⚠️ <b>Slot non più disponibile</b> · {watch_name}\n"
            f"{escape(str(payload.get('slot', '')))} — "
            f"{escape(str(payload.get('location', '')))}\n"
            "Il monitoraggio continua a cercare.",
            _watch_keyboard(event),
        )

    if event.event_type is NotificationEventType.AUTOBOOK_SUCCESS:
        return (
            f"✅ <b>Prenotazione automatica riuscita</b> · {watch_name}\n"
            f"{escape(str(payload.get('slot', '')))} — "
            f"{escape(str(payload.get('location', '')))}\n\n"
            "Il pagamento avviene allo sportello. Scarica il promemoria dagli appuntamenti.",
            _appointments_keyboard(),
        )

    if event.event_type is NotificationEventType.AUTOBOOK_FAILED:
        return (
            f"⚠️ <b>Prenotazione automatica non riuscita</b> · {watch_name}\n"
            f"{escape(str(payload.get('detail', '')))}",
            _watch_keyboard(event),
        )

    if event.event_type is NotificationEventType.AUTOBOOK_AMBIGUOUS:
        return (
            f"⚠️ <b>Esito incerto</b> · {watch_name}\n"
            "La conferma è stata inviata ma non è stato possibile verificarla. "
            "<b>Non ho ritentato.</b> Controlla i tuoi appuntamenti.",
            _appointments_keyboard(),
        )

    if event.event_type is NotificationEventType.REAUTH_REQUIRED:
        if str(payload.get("error_code", "")) == "CONNECT_LOCKED":
            # Reached while adding an account and while updating an existing
            # one, so it must not claim anything about a stored login: in the
            # first case there is none, and in the second the old one is still
            # there untouched. What both need to know is that the attempts
            # have been paused.
            return (
                "🔐 <b>Troppi tentativi di collegamento</b>\n"
                "Per sicurezza ho sospeso i tentativi per un'ora. "
                "Controlla le credenziali sul portale e riprova più tardi.",
                _accounts_keyboard(),
            )
        label = escape(str(payload.get("account_label", "")))
        target = f" · {label}" if label else ""
        locked = str(payload.get("status", "")) == PortalAccountStatus.LOCKED.value
        reason = (
            "Il portale ha rifiutato l'accesso troppe volte e l'account è stato "
            "<b>bloccato</b> per sicurezza."
            if locked
            else "L'accesso salvato non è più valido e il nuovo tentativo è stato rifiutato."
        )
        return (
            f"🔐 <b>Accesso al portale perso</b>{target}\n"
            f"{reason}\n"
            "I monitoraggi collegati sono in pausa: aggiorna le credenziali dalla "
            "sezione Account per farli ripartire.",
            _accounts_keyboard(),
        )

    if event.event_type is NotificationEventType.ACCOUNT_RECOVERED:
        label = escape(str(payload.get("account_label", "")))
        target = f" · {label}" if label else ""
        resumed = int(payload.get("resumed", 0) or 0)
        if resumed == 0:
            tail = "Nessun monitoraggio era in pausa."
        elif resumed == 1:
            tail = "Ho ripreso il monitoraggio che era in pausa."
        else:
            tail = f"Ho ripreso i {resumed} monitoraggi che erano in pausa."
        return (
            f"✅ <b>Accesso al portale ripristinato</b>{target}\n"
            f"Le credenziali salvate funzionano di nuovo: non devi fare nulla. {tail}",
            _accounts_keyboard(),
        )

    if event.event_type is NotificationEventType.WATCH_SUSPENDED:
        return (
            f"⏸ <b>Monitoraggio sospeso</b> · {watch_name}\n"
            f"Motivo: <code>{escape(str(payload.get('reason', '-')))}</code>",
            _watch_keyboard(event),
        )

    if event.event_type is NotificationEventType.WATCH_AMBIGUOUS_SEARCH:
        return (
            f"⚠️ <b>Ricerca non più univoca</b> · {watch_name}\n"
            "Il portale restituisce risultati diversi per la ricerca salvata. "
            "Il monitoraggio è in pausa: rifai la selezione.",
            _watch_keyboard(event),
        )

    if event.event_type is NotificationEventType.LOCATION_DISAPPEARED:
        missing = payload.get("missing") or []
        names = ", ".join(escape(str(item)) for item in missing)
        return (
            f"ℹ️ <b>Sedi non più disponibili</b> · {watch_name}\n"
            f"{names}\nIl monitoraggio continua sulle altre sedi.",
            _watch_keyboard(event),
        )

    if event.event_type is NotificationEventType.APPOINTMENT_DETECTED:
        return (
            "📅 <b>Nuovo appuntamento rilevato</b>\n"
            f"{escape(str(payload.get('when', '')))} — "
            f"{escape(str(payload.get('location', '')))}",
            _appointments_keyboard(),
        )

    if event.event_type is NotificationEventType.CANCELLATION_SUCCESS:
        return ("✅ <b>Disdetta confermata</b>", _appointments_keyboard())

    if event.event_type is NotificationEventType.CANCELLATION_FAILED:
        return (
            f"⚠️ <b>Disdetta non riuscita</b>\n{escape(str(payload.get('detail', '')))}",
            _appointments_keyboard(),
        )

    if event.event_type is NotificationEventType.PDF_UNAVAILABLE:
        return (texts.APPOINTMENT_PDF_UNAVAILABLE, _appointments_keyboard())

    if event.event_type is NotificationEventType.ADMIN_ACTION:
        return (
            f"🛡 <b>Operazione amministrativa</b>\n{escape(str(payload.get('detail', '')))}",
            build_actions([]),
        )

    return (
        f"ℹ️ {escape(event.event_type.value)}",
        build_actions([]),
    )


async def render_for_queue(
    event: NotificationEvent, user: TelegramUser
) -> tuple[str, InlineKeyboardMarkup]:
    """Adapter matching the renderer signature expected by the queue."""
    return render_notification(event, user)
