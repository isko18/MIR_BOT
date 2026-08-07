from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import BotCommand

log = logging.getLogger(__name__)

USER_BOT_COMMANDS = (
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="menu", description="Вернуться в меню"),
    BotCommand(command="cancel", description="Отменить текущее пополнение"),
)

ADMIN_BOT_COMMANDS = (
    BotCommand(command="start", description="Начало работы"),
    BotCommand(command="history", description="История пополнений"),
    BotCommand(command="stats", description="Статистика операций"),
    BotCommand(command="qr", description="QR оплаты: посмотреть и заменить"),
    BotCommand(command="cancel", description="Отменить текущее действие"),
)


async def setup_bot_commands(user_bot: Bot, admin_bot: Bot) -> None:
    await user_bot.set_my_commands(list(USER_BOT_COMMANDS))
    await admin_bot.set_my_commands(list(ADMIN_BOT_COMMANDS))
    log.info("Меню команд Telegram установлено (user + admin)")
