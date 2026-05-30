import asyncio
import logging
from contextlib import suppress
from datetime import timedelta

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage

from admin_bot.handlers import router as admin_router
from bot_commands import setup_bot_commands
from config import settings
from database import close_db, init_db
from logo_fetch import close_http
from user_bot.flood_middleware import MessageFloodMiddleware
from user_bot.subscription_middleware import SubscriptionMiddleware
from user_bot.subscription import init_subscription_channel
from user_bot.handlers import router as user_router

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# chat_member — авто-подтверждение при подписке на канал
_POLL_UPDATES = ["message", "callback_query", "my_chat_member", "chat_member"]


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
    await init_db()
    storage = _build_fsm_storage()

    user_session = AiohttpSession(limit=settings.telegram_http_limit_user)
    admin_session = AiohttpSession(limit=settings.telegram_http_limit_admin)

    user_bot = Bot(settings.user_bot_token, session=user_session)
    admin_bot = Bot(settings.admin_bot_token, session=admin_session)

    await init_subscription_channel(user_bot)
    await setup_bot_commands(user_bot, admin_bot)

    user_dp = Dispatcher(storage=storage)
    admin_dp = Dispatcher(storage=storage)

    user_dp.message.middleware(MessageFloodMiddleware())
    user_dp.message.middleware(SubscriptionMiddleware())

    user_dp.workflow_data["admin_bot"] = admin_bot
    admin_dp.workflow_data["user_bot"] = user_bot

    user_dp.include_router(user_router)
    admin_dp.include_router(admin_router)

    try:
        await asyncio.gather(
            user_dp.start_polling(user_bot, allowed_updates=_POLL_UPDATES),
            admin_dp.start_polling(admin_bot, allowed_updates=_POLL_UPDATES),
        )
    finally:
        await _shutdown(storage, user_session, admin_session)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
