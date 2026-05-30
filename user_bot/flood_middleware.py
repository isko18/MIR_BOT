"""Ограничение частоты сообщений на пользователя (защита от флуда при большой аудитории)."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject


class MessageFloodMiddleware(BaseMiddleware):
    """Не более `max_events` текстовых сообщений за `window_sec` секунд на одного user_id."""

    def __init__(self, *, max_events: int = 28, window_sec: float = 40.0) -> None:
        self.max_events = max_events
        self.window_sec = window_sec
        self._msg_times: dict[int, deque[float]] = defaultdict(deque)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)
        user = event.from_user
        if not user:
            return await handler(event, data)
        uid = user.id
        now = time.monotonic()
        dq = self._msg_times[uid]
        while dq and now - dq[0] > self.window_sec:
            dq.popleft()
        if len(dq) >= self.max_events:
            await event.answer(
                "Слишком много сообщений за короткое время. Подождите немного и повторите."
            )
            return None
        dq.append(now)
        return await handler(event, data)
