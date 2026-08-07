"""Ограничение частоты событий на пользователя (защита от флуда при большой аудитории)."""

from __future__ import annotations

import time
from collections import deque
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject


class MessageFloodMiddleware(BaseMiddleware):
    """Не более `max_events` событий за `window_sec` секунд на одного user_id.

    Работает и на сообщениях, и на callback-кнопках. Записи неактивных
    пользователей вычищаются: раньше словарь только рос — при десятках тысяч
    пользователей это была утечка памяти на всё время жизни процесса.
    """

    def __init__(
        self,
        *,
        max_events: int = 28,
        window_sec: float = 40.0,
        cleanup_every_sec: float = 300.0,
    ) -> None:
        self.max_events = max_events
        self.window_sec = window_sec
        self.cleanup_every_sec = cleanup_every_sec
        self._events: dict[int, deque[float]] = {}
        self._warned_at: dict[int, float] = {}
        self._next_cleanup = time.monotonic() + cleanup_every_sec

    def _cleanup(self, now: float) -> None:
        if now < self._next_cleanup:
            return
        self._next_cleanup = now + self.cleanup_every_sec
        stale = [uid for uid, dq in self._events.items() if not dq or now - dq[-1] > self.window_sec]
        for uid in stale:
            self._events.pop(uid, None)
            self._warned_at.pop(uid, None)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, (Message, CallbackQuery)):
            return await handler(event, data)
        user = event.from_user
        if not user:
            return await handler(event, data)

        uid = user.id
        now = time.monotonic()
        self._cleanup(now)

        dq = self._events.setdefault(uid, deque())
        while dq and now - dq[0] > self.window_sec:
            dq.popleft()

        if len(dq) >= self.max_events:
            if isinstance(event, CallbackQuery):
                await event.answer("Слишком часто. Подождите немного.", show_alert=False)
                return None
            # Одно предупреждение на окно: иначе на флуд отвечаем флудом и ловим 429.
            last_warn = self._warned_at.get(uid, 0.0)
            if now - last_warn > self.window_sec:
                self._warned_at[uid] = now
                await event.answer(
                    "Слишком много сообщений за короткое время. Подождите немного и повторите."
                )
            return None

        dq.append(now)
        return await handler(event, data)
