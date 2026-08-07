from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.types import Message, TelegramObject

from config import settings
from user_bot.subscription import (
    is_subscribed_cached,
    is_subscription_exempt,
    send_subscription_prompt,
    subscription_check_ready,
    verify_user_subscribed,
)


class SubscriptionMiddleware(BaseMiddleware):
    """Блокирует бота для пользователей без подписки на обязательный канал."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        channel = settings.required_channel_username
        if not channel:
            return await handler(event, data)
        if not isinstance(event, Message):
            return await handler(event, data)
        user = event.from_user
        if not user:
            return await handler(event, data)

        if is_subscription_exempt(user.id) or is_subscribed_cached(user.id):
            return await handler(event, data)

        text = (event.text or "").strip()
        if text.startswith("/start"):
            return await handler(event, data)

        bot: Bot = data["bot"]
        # Один запрос без повторов: это горячий путь на каждое сообщение, а повторы
        # с задержкой нужны только сразу после подписки — там свои retries.
        subscribed, _err = await verify_user_subscribed(bot, user.id, retries=1)
        if subscribed:
            return await handler(event, data)

        if not subscription_check_ready():
            await event.answer(
                "⚠️ Проверка подписки временно недоступна. "
                "Администратор должен добавить бота администратором канала @"
                f"{channel.lstrip('@')}.",
            )
            return None

        await send_subscription_prompt(event, channel)
        return None
