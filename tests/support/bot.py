"""In-process Telegram harness.

Updates are fed through the real dispatcher, so the middlewares (context,
error boundary, authorization, idempotency) and the routers are exercised
exactly as in production; only the network session is replaced.
"""

from __future__ import annotations

import itertools
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import TelegramMethod
from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardMarkup,
    Message,
    Update,
    User,
)

from app.composition import Container
from app.main_bot import build_dispatcher

BOT_ID = 424242


@dataclass(slots=True)
class SentMessage:
    """One outbound Telegram API call recorded by the harness."""

    method: str
    text: str
    keyboard: InlineKeyboardMarkup | None
    payload: dict[str, Any]

    def buttons(self) -> list[str]:
        if self.keyboard is None:
            return []
        return [button.text for row in self.keyboard.inline_keyboard for button in row]

    def callbacks(self) -> list[str]:
        if self.keyboard is None:
            return []
        return [
            button.callback_data
            for row in self.keyboard.inline_keyboard
            for button in row
            if button.callback_data
        ]

    def callback_for(self, label_fragment: str) -> str:
        if self.keyboard is None:
            raise AssertionError("the message has no inline keyboard")
        for row in self.keyboard.inline_keyboard:
            for button in row:
                if label_fragment in (button.text or "") and button.callback_data:
                    return button.callback_data
        raise AssertionError(
            f"no button matching {label_fragment!r}; available: {self.buttons()}"
        )


class FakeSession(BaseSession):
    """A Telegram session that records calls instead of performing them."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[SentMessage] = []
        self._message_ids = itertools.count(1000)

    async def close(self) -> None:
        return None

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncIterator[bytes]:  # pragma: no cover - not used by the bot
        yield b""

    async def make_request(
        self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None
    ) -> Any:
        name = type(method).__name__
        payload = method.model_dump(exclude_none=True, warnings=False)
        keyboard = payload.get("reply_markup")
        markup = keyboard if isinstance(keyboard, InlineKeyboardMarkup) else None
        if markup is None and isinstance(keyboard, dict) and "inline_keyboard" in keyboard:
            markup = InlineKeyboardMarkup.model_validate(keyboard)
        text = str(payload.get("text") or payload.get("caption") or "")
        self.calls.append(
            SentMessage(method=name, text=text, keyboard=markup, payload=payload)
        )

        if name in {"SendMessage", "EditMessageText", "SendDocument"}:
            return Message(
                message_id=next(self._message_ids),
                date=datetime.now(UTC),
                chat=Chat(id=int(payload.get("chat_id", 1)), type="private"),
                text=text or None,
                reply_markup=markup,
            )
        return True


@dataclass
class BotHarness:
    """Feeds synthetic updates through the real dispatcher."""

    container: Container
    bot: Bot
    dispatcher: Dispatcher
    session: FakeSession
    _update_ids: Any = field(default_factory=lambda: itertools.count(1))

    @classmethod
    def build(cls, container: Container) -> BotHarness:
        session = FakeSession()
        bot = Bot(
            token=f"{BOT_ID}:AAH0000000000000000000000000000000",
            session=session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        return cls(
            container=container,
            bot=bot,
            dispatcher=build_dispatcher(container),
            session=session,
        )

    def reset(self) -> None:
        self.session.calls.clear()

    @property
    def sent(self) -> list[SentMessage]:
        return self.session.calls

    def last(self) -> SentMessage:
        assert self.session.calls, "no Telegram call was recorded"
        return self.session.calls[-1]

    def texts(self) -> list[str]:
        return [call.text for call in self.session.calls if call.text]

    def find(self, fragment: str) -> SentMessage:
        for call in reversed(self.session.calls):
            if fragment in call.text:
                return call
        raise AssertionError(f"no message containing {fragment!r}; got: {self.texts()}")

    def contains(self, fragment: str) -> bool:
        return any(fragment in call.text for call in self.session.calls)

    def _user(self, telegram_id: int) -> User:
        return User(id=telegram_id, is_bot=False, first_name="Utente")

    async def send(self, telegram_id: int, text: str) -> list[SentMessage]:
        before = len(self.session.calls)
        update = Update(
            update_id=next(self._update_ids),
            message=Message(
                message_id=next(self.session._message_ids),  # noqa: SLF001
                date=datetime.now(UTC),
                chat=Chat(id=telegram_id, type="private"),
                from_user=self._user(telegram_id),
                text=text,
            ),
        )
        await self.dispatcher.feed_update(self.bot, update)
        return self.session.calls[before:]

    async def press(
        self, telegram_id: int, callback_data: str, *, update_id: int | None = None
    ) -> list[SentMessage]:
        before = len(self.session.calls)
        update = Update(
            update_id=update_id if update_id is not None else next(self._update_ids),
            callback_query=CallbackQuery(
                id=f"cb-{callback_data}-{before}",
                from_user=self._user(telegram_id),
                chat_instance="synthetic",
                data=callback_data,
                message=Message(
                    message_id=next(self.session._message_ids),  # noqa: SLF001
                    date=datetime.now(UTC),
                    chat=Chat(id=telegram_id, type="private"),
                    from_user=User(id=BOT_ID, is_bot=True, first_name="bot"),
                    text="schermata precedente",
                ),
            ),
        )
        await self.dispatcher.feed_update(self.bot, update)
        return self.session.calls[before:]

    async def press_twice(self, telegram_id: int, callback_data: str) -> list[SentMessage]:
        """Simulate a duplicated Telegram update for the very same callback."""
        update_id = next(self._update_ids)
        first = await self.press(telegram_id, callback_data, update_id=update_id)
        second = await self.press(telegram_id, callback_data, update_id=update_id)
        return first + second

    async def close(self) -> None:
        await self.bot.session.close()
