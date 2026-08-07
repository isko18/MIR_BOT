import asyncio
import logging
import logging.handlers
from contextlib import suppress
from datetime import timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage

from admin_bot.handlers import router as admin_router
from bot_commands import setup_bot_commands
from config import settings
from database import close_db, expire_stale_deposits, init_db
from logo_fetch import close_http
from user_bot.flood_middleware import MessageFloodMiddleware
from user_bot.receipt_timer import RECEIPT_DEADLINE_SEC
from user_bot.subscription import init_subscription_channel
from user_bot.subscription_middleware import SubscriptionMiddleware
from user_bot.handlers import router as user_router

LOG_PATH = Path(__file__).resolve().parent / "bot.log"

# chat_member — авто-подтверждение при подписке на канал
_POLL_UPDATES = ["message", "callback_query", "my_chat_member", "chat_member"]

log = logging.getLogger(__name__)


def _setup_logging() -> None:
    """Консоль + файл с ротацией: bot.log рос без ограничений и однажды съел бы диск."""
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler()
    console.setFormatter(fmt)

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_PATH,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(file_handler)
    # aiogram сыплет INFO на каждый апдейт — на проде это шум
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


def _build_fsm_storage():
    if settings.redis_url:
        from aiogram.fsm.storage.redis import RedisStorage

        ttl = timedelta(seconds=settings.fsm_redis_ttl_sec)
        log.info("FSM: Redis (%s…)", settings.redis_url[:24])
        return RedisStorage.from_url(
            settings.redis_url,
            connection_kwargs={
                "decode_responses": False,
                "health_check_interval": 30,
            },
            state_ttl=ttl,
            data_ttl=ttl,
        )
    log.info("FSM: MemoryStorage (для горизонтального масштаба задайте REDIS_URL)")
    return MemoryStorage()


async def _sweep_stale_deposits() -> None:
    """Таймеры чеков живут в памяти: после рестарта заявки без чека «зависали»
    в awaiting_receipt навсегда. Просрочиваем их при старте."""
    stale = await expire_stale_deposits(RECEIPT_DEADLINE_SEC)
    if stale:
        log.info("Просрочено зависших заявок при старте: %d", len(stale))


async def _shutdown(
    storage,
    user_session: AiohttpSession,
    admin_session: AiohttpSession,
) -> None:
    await close_db()
    await close_http()
    with suppress(Exception):
        await user_session.close()
    with suppress(Exception):
        await admin_session.close()
    close_fn = getattr(storage, "close", None)
    if close_fn is not None:
        with suppress(Exception):
            await close_fn()
    log.info("Shutdown complete")


async def run() -> None:
    _setup_logging()
    await init_db()
    await _sweep_stale_deposits()
    storage = _build_fsm_storage()

    user_session = AiohttpSession(limit=settings.telegram_http_limit_user)
    admin_session = AiohttpSession(limit=settings.telegram_http_limit_admin)

    # parse_mode по умолчанию: раньше каждый вызов должен был помнить про HTML,
    # и часть сообщений уезжала с сырыми тегами.
    default = DefaultBotProperties(parse_mode="HTML")
    user_bot = Bot(settings.user_bot_token, session=user_session, default=default)
    admin_bot = Bot(settings.admin_bot_token, session=admin_session, default=default)

    await init_subscription_channel(user_bot)
    await setup_bot_commands(user_bot, admin_bot)

    user_dp = Dispatcher(storage=storage)
    admin_dp = Dispatcher(storage=storage)

    user_dp.message.middleware(MessageFloodMiddleware())
    user_dp.callback_query.middleware(MessageFloodMiddleware())
    user_dp.message.middleware(SubscriptionMiddleware())

    user_dp.workflow_data["admin_bot"] = admin_bot
    admin_dp.workflow_data["user_bot"] = user_bot

    user_dp.include_router(user_router)
    admin_dp.include_router(admin_router)

    tasks = [
        asyncio.create_task(
            user_dp.start_polling(user_bot, allowed_updates=_POLL_UPDATES),
            name="user-polling",
        ),
        asyncio.create_task(
            admin_dp.start_polling(admin_bot, allowed_updates=_POLL_UPDATES),
            name="admin-polling",
        ),
    ]
    try:
        # Если один поллинг упал — гасим второй, иначе бот остаётся «наполовину живым»:
        # пользователи шлют чеки, а админ-бот их не принимает.
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        for task in done:
            exc = task.exception()
            if exc is not None:
                log.error("Поллинг %s остановлен с ошибкой: %s", task.get_name(), exc)
    except (KeyboardInterrupt, SystemExit):
        log.info("Остановка по сигналу")
    finally:
        await _shutdown(storage, user_session, admin_session)


def main() -> None:
    with suppress(KeyboardInterrupt, SystemExit):
        asyncio.run(run())


if __name__ == "__main__":
    main()
