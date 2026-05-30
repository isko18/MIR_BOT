"""Живое обновление «Время на оплату» через edit_message_caption (подпись к QR)."""

from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from .receipt_timer import RECEIPT_DEADLINE_SEC

log = logging.getLogger(__name__)

_tasks: dict[int, asyncio.Task] = {}

QR_PAYMENT_INTRO = (
    "Отсканируйте QR для оплаты. После оплаты отправьте сюда фото чека."
)


def format_payment_block(amount: float, account_id: str, remaining_sec: int) -> str:
    remaining_sec = max(0, int(remaining_sec))
    m = remaining_sec // 60
    s = remaining_sec % 60
    time_str = f"{m}:{s:02d}"
    amt_str = f"{float(amount):,.2f}".replace(",", " ")
    return (
        f"💰 Сумма: {amt_str} сом\n"
        f"🆔 ID: {account_id}\n\n"
        f"⏳ Время на оплату: {time_str}\n"
        "‼️ Оплата строго до копеек\n"
        "📸 После оплаты отправьте фото чека"
    )


def format_qr_payment_caption(amount: float, account_id: str, remaining_sec: int) -> str:
    return f"{QR_PAYMENT_INTRO}\n\n{format_payment_block(amount, account_id, remaining_sec)}"


def cancel_payment_countdown(user_id: int) -> None:
    t = _tasks.pop(user_id, None)
    if t and not t.done():
        t.cancel()


def schedule_payment_countdown(
    bot: Bot,
    *,
    chat_id: int,
    user_id: int,
    message_id: int,
    amount: float,
    account_id: str,
    edit_caption: bool = True,
) -> None:
    cancel_payment_countdown(user_id)

    async def run() -> None:
        self_task = asyncio.current_task()
        deadline = time.monotonic() + RECEIPT_DEADLINE_SEC
        try:
            while True:
                # Целые секунды до дедлайна — синхронно с «реальным» временем
                remaining = max(0, int(deadline - time.monotonic()))
                caption = format_qr_payment_caption(amount, account_id, remaining)
                try:
                    if edit_caption:
                        await bot.edit_message_caption(
                            chat_id=chat_id,
                            message_id=message_id,
                            caption=caption,
                        )
                    else:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=format_payment_block(amount, account_id, remaining),
                        )
                except TelegramRetryAfter as e:
                    await asyncio.sleep(float(e.retry_after) + 0.15)
                    continue
                except TelegramBadRequest as e:
                    err = (e.message or "").lower()
                    if "message is not modified" in err or "not modified" in err:
                        pass
                    else:
                        log.debug("payment countdown edit: %s", e)
                if remaining <= 0:
                    break
                # Просыпаемся в момент, когда останется на 1 сек меньше (без дрейфа sleep(1)+сеть)
                next_tick = deadline - (remaining - 1)
                delay = next_tick - time.monotonic()
                await asyncio.sleep(max(0.05, min(delay, 3600.0)))
        except asyncio.CancelledError:
            if _tasks.get(user_id) is self_task:
                _tasks.pop(user_id, None)
            raise
        except Exception as e:
            log.warning("payment countdown: %s", e)
        finally:
            if _tasks.get(user_id) is self_task:
                _tasks.pop(user_id, None)

    _tasks[user_id] = asyncio.create_task(run())
