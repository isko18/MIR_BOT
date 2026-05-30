from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from config import settings

log = logging.getLogger(__name__)

BTN_CHECK_SUBSCRIPTION = "✅ Проверить подписку"

_SUBSCRIBED_STATUSES = frozenset(
    {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
        ChatMemberStatus.RESTRICTED,
        "member",
        "administrator",
        "creator",
        "restricted",
    }
)

_channel_id: int | None = None
_check_ready: bool = True
_check_error: str | None = None


class SubscriptionCheckError(Exception):
    """Telegram API не позволяет проверить подписку (обычно бот не админ канала)."""


def is_subscription_exempt(user_id: int) -> bool:
    """Админы проходят /start без проверки подписки на канал."""
    return user_id in settings.admin_ids


def subscription_check_ready() -> bool:
    return _check_ready


def subscription_check_error() -> str | None:
    return _check_error


def channel_chat_id() -> int | str | None:
    if _channel_id is not None:
        return _channel_id
    ch = settings.required_channel_username
    return f"@{ch.lstrip('@')}" if ch else None


def is_subscribed_status(status: ChatMemberStatus | str) -> bool:
    return status in _SUBSCRIBED_STATUSES


def subscription_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CHECK_SUBSCRIPTION)]],
        resize_keyboard=True,
        input_field_placeholder="Подпишитесь на канал и вернитесь в бот…",
    )


def subscription_inline_kb(channel: str | None = None) -> InlineKeyboardMarkup:
    ch = (channel or settings.required_channel_username or "MIRKG_NEWS").lstrip("@")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться на канал", url=f"https://t.me/{ch}")],
            [InlineKeyboardButton(text=BTN_CHECK_SUBSCRIPTION, callback_data="sub_check")],
        ]
    )


def subscription_prompt_text(channel: str | None = None) -> str:
    ch = (channel or settings.required_channel_username or "MIRKG_NEWS").lstrip("@")
    return (
        "🔔 Подписка на канал\n\n"
        "Для продолжения работы с ботом необходимо подписаться на наш канал.\n\n"
        f"📢 Канал: @{ch}\n\n"
        "После подписки вернитесь в бот — доступ откроется автоматически."
    )


async def init_subscription_channel(bot: Bot) -> None:
    """При старте: получить ID канала и проверить, что бот может смотреть участников."""
    global _channel_id, _check_ready, _check_error

    ch = settings.required_channel_username
    if not ch:
        _check_ready = True
        _check_error = None
        return

    ch = ch.lstrip("@")
    cid_raw = (settings.required_channel_id or "").strip()
    try:
        if cid_raw:
            _channel_id = int(cid_raw)
            chat = await bot.get_chat(_channel_id)
        else:
            chat = await bot.get_chat(f"@{ch}")
            _channel_id = int(chat.id)

        probe_uid = next(iter(settings.admin_ids), None)
        if probe_uid is not None:
            await bot.get_chat_member(_channel_id, probe_uid)

        _check_ready = True
        _check_error = None
        log.info("Проверка подписки: @%s (id=%s)", ch, _channel_id)
    except TelegramBadRequest as exc:
        _check_ready = False
        _check_error = str(exc)
        log.error(
            "Проверка подписки НЕ РАБОТАЕТ: добавьте USER_BOT администратором в @%s "
            "(нужно право «добавление участников» / просмотр участников). Telegram: %s",
            ch,
            exc,
        )
    except Exception as exc:
        _check_ready = False
        _check_error = str(exc)
        log.error("Проверка подписки: ошибка инициализации @%s: %s", ch, exc)


async def is_user_subscribed(bot: Bot, user_id: int, channel: str | None = None) -> bool:
    if is_subscription_exempt(user_id):
        return True

    if not settings.required_channel_username and not channel:
        return True

    chat_id = channel_chat_id()
    if chat_id is None:
        if channel:
            chat_id = f"@{channel.lstrip('@')}"
        else:
            return True

    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return is_subscribed_status(member.status)
    except TelegramBadRequest as exc:
        err = str(exc).lower()
        if "user not found" in err or "participant_id_invalid" in err:
            return False
        log.warning("Subscription check failed for user %s: %s", user_id, exc)
        raise SubscriptionCheckError(str(exc)) from exc


async def verify_user_subscribed(
    bot: Bot,
    user_id: int,
    *,
    retries: int = 4,
    delay_sec: float = 1.0,
) -> tuple[bool, str | None]:
    """
    Проверка с повторами (Telegram иногда обновляет статус с задержкой).
    Возвращает (подписан, текст_ошибки_конфигурации).
    """
    if not subscription_check_ready():
        return False, _check_error or "Проверка подписки не настроена."

    for attempt in range(retries):
        try:
            if await is_user_subscribed(bot, user_id):
                return True, None
        except SubscriptionCheckError as exc:
            return False, str(exc)
        if attempt < retries - 1:
            await asyncio.sleep(delay_sec)
    return False, None


def is_required_channel(chat_id: int, username: str | None) -> bool:
    ch = (settings.required_channel_username or "").lstrip("@").lower()
    if not ch:
        return False
    if username and username.lstrip("@").lower() == ch:
        return True
    return _channel_id is not None and chat_id == _channel_id


async def send_subscription_prompt(message: Message, channel: str | None = None) -> None:
    ch = channel or settings.required_channel_username
    await message.answer(
        subscription_prompt_text(ch),
        reply_markup=subscription_inline_kb(ch),
    )
    await message.answer(
        "Нажмите кнопку ниже, если доступ не открылся автоматически:",
        reply_markup=subscription_reply_kb(),
    )
