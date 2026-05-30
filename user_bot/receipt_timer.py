from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from aiogram import Bot, Dispatcher
from aiogram.types import ReplyKeyboardMarkup
from aiogram.fsm.storage.base import StorageKey

from database import get_deposit, set_deposit_status
from texts import RECEIPT_TIMEOUT_MESSAGE

log = logging.getLogger(__name__)

RECEIPT_DEADLINE_SEC = 300

_tasks: dict[int, asyncio.Task] = {}


def cancel_receipt_deadline(user_id: int) -> None:
    t = _tasks.pop(user_id, None)
    if t and not t.done():
        t.cancel()


def schedule_receipt_deadline(
    dispatcher: Dispatcher,
    bot: Bot,
    *,
    chat_id: int,
    user_id: int,
    deposit_id: int,
    main_menu_kb: Callable[[], ReplyKeyboardMarkup],
) -> None:
    cancel_receipt_deadline(user_id)

    async def run() -> None:
        self_task = asyncio.current_task()
        try:
            await asyncio.sleep(RECEIPT_DEADLINE_SEC)
        except asyncio.CancelledError:
            if _tasks.get(user_id) is self_task:
                _tasks.pop(user_id, None)
            return
        try:
            from .payment_countdown import cancel_payment_countdown

            cancel_payment_countdown(user_id)
            row = await get_deposit(deposit_id)
            if not row or row["status"] != "awaiting_receipt":
                return
            await set_deposit_status(deposit_id, "expired_timeout")
            key = StorageKey(bot_id=bot.id, chat_id=chat_id, user_id=user_id)
            try:
                await dispatcher.storage.set_state(key=key, state=None)
                await dispatcher.storage.set_data(key=key, data={})
            except Exception as e:
                log.warning("FSM clear after receipt timeout: %s", e)
            try:
                await bot.send_message(
                    chat_id,
                    RECEIPT_TIMEOUT_MESSAGE,
                    reply_markup=main_menu_kb(),
                )
            except Exception as e:
                log.warning("Notify user after receipt timeout: %s", e)
        finally:
            if _tasks.get(user_id) is self_task:
                _tasks.pop(user_id, None)

    _tasks[user_id] = asyncio.create_task(run())
