"""Живое обновление «Время на оплату» через edit_message_caption (подпись к QR)."""

from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)

from money import format_kgs

from .receipt_timer import RECEIPT_DEADLINE_SEC

log = logging.getLogger(__name__)

_tasks: dict[int, asyncio.Task] = {}

QR_PAYMENT_INTRO = (
    "Отсканируйте QR для оплаты. После оплаты отправьте сюда фото чека."
)

# Счётчик тикает живо — раз в секунду, но не любой ценой: каждый тик это запрос
# edit_message_caption, а у Telegram общий потолок ~30 запросов/сек на бота. Фиксированная
# секунда означала бы 1 req/s на КАЖДОЕ активное пополнение: полсотни параллельных заявок
# — и бот в 429, обычные сообщения перестают доставляться.
#
# Поэтому шаг вычисляется от текущего числа активных отсчётов так, чтобы суммарно они
# не выходили за бюджет: пока пользователей мало (обычный режим) — честная секунда,
# на пике шаг сам растягивается до 2-5-15 секунд.
_MIN_TICK_SEC = 1.0
_MAX_TICK_SEC = 30.0
# Запросов/сек на все отсчёты вместе. Остальное от лимита Telegram оставляем
# обычным сообщениям, чекам и админ-боту.
_EDIT_BUDGET_PER_SEC = 8.0
# Потолок на один запрос правки: тик всё равно раз в секунду, ждать дольше смысла нет.
_EDIT_TIMEOUT_SEC = 5.0


def _tick_interval() -> float:
    """Шаг отсчёта при текущей нагрузке: 1 сек, пока активных ≤ бюджета."""
    active = max(1, len(_tasks))
    return min(_MAX_TICK_SEC, max(_MIN_TICK_SEC, active / _EDIT_BUDGET_PER_SEC))


def _next_delay(remaining: int) -> float:
    if remaining <= 0:
        return 0.0
    return min(_tick_interval(), float(remaining))


def format_payment_block(amount: float, account_id: str, remaining_sec: int) -> str:
    remaining_sec = max(0, int(remaining_sec))
    m = remaining_sec // 60
    s = remaining_sec % 60
    time_str = f"{m}:{s:02d}"
    return (
        f"💰 Сумма: {format_kgs(amount)}\n"
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
        ticks = 0
        log.debug("Счётчик оплаты запущен: user=%s msg=%s", user_id, message_id)
        try:
            while True:
                # Ждём ПЕРЕД правкой: подпись с «5:00» уже ушла вместе с QR, и первый
                # edit тем же текстом был бы лишним запросом («message is not modified»).
                remaining = max(0, int(round(deadline - time.monotonic())))
                if remaining > 0:
                    # Просыпаемся ровно к следующей смене цифры на табло — считаем от
                    # абсолютного дедлайна, чтобы не копить дрейф от сети и самих правок.
                    step = _next_delay(remaining)
                    next_tick = deadline - (remaining - step)
                    delay = max(0.05, min(next_tick - time.monotonic(), 3600.0))
                    await asyncio.sleep(delay)

                remaining = max(0, int(round(deadline - time.monotonic())))
                caption = format_qr_payment_caption(amount, account_id, remaining)
                try:
                    # Жёсткий потолок на запрос. Наблюдался залипший вызов, который не
                    # возвращался и не падал по таймауту сессии — счётчик замирал навсегда
                    # на «5:00». Лучше бросить такую правку и тикнуть в следующую секунду.
                    if edit_caption:
                        await asyncio.wait_for(
                            bot.edit_message_caption(
                                chat_id=chat_id,
                                message_id=message_id,
                                caption=caption,
                            ),
                            timeout=_EDIT_TIMEOUT_SEC,
                        )
                    else:
                        await asyncio.wait_for(
                            bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=format_payment_block(amount, account_id, remaining),
                            ),
                            timeout=_EDIT_TIMEOUT_SEC,
                        )
                except asyncio.TimeoutError:
                    log.warning(
                        "Счётчик оплаты: правка зависла дольше %s сек (user=%s), пропускаю тик",
                        _EDIT_TIMEOUT_SEC,
                        user_id,
                    )
                    continue
                except TelegramRetryAfter as e:
                    # Раньше эта ветка спала молча: при большом retry_after счётчик
                    # «замирал» без единой записи в логе.
                    log.warning(
                        "Счётчик оплаты: Telegram просит подождать %s сек (user=%s)",
                        e.retry_after,
                        user_id,
                    )
                    await asyncio.sleep(float(e.retry_after) + 0.15)
                    continue
                except TelegramNetworkError as e:
                    # Моргнула сеть — это не повод хоронить счётчик до конца оплаты.
                    # Раньше исключение улетало в общий except и цикл выходил навсегда.
                    log.warning("Счётчик оплаты: сеть (user=%s): %s", user_id, e)
                    await asyncio.sleep(1.0)
                    continue
                except TelegramForbiddenError as e:
                    # Пользователь заблокировал бота — крутить счётчик ещё 5 минут незачем.
                    log.warning("Счётчик оплаты остановлен: доступ запрещён (user=%s): %s", user_id, e)
                    return
                except TelegramBadRequest as e:
                    err = (e.message or "").lower()
                    if "not modified" in err:
                        pass
                    elif "message to edit not found" in err or "message can't be edited" in err:
                        # Раньше — молчаливый return: счётчик умирал, а в логе ни строчки.
                        log.warning(
                            "Счётчик оплаты остановлен: сообщение недоступно для правки "
                            "(chat=%s msg=%s): %s",
                            chat_id,
                            message_id,
                            e,
                        )
                        return
                    else:
                        # Раньше это глушилось в debug: счётчик молча замирал, а в логе
                        # не было ни строчки — причину приходилось искать вслепую.
                        log.warning(
                            "Счётчик оплаты: правка подписи не прошла (chat=%s msg=%s): %s",
                            chat_id,
                            message_id,
                            e,
                        )
                ticks += 1
                if remaining <= 0:
                    break
        except asyncio.CancelledError:
            if _tasks.get(user_id) is self_task:
                _tasks.pop(user_id, None)
            raise
        except Exception as e:
            log.warning("Счётчик оплаты упал (user=%s): %s", user_id, e, exc_info=True)
        finally:
            if _tasks.get(user_id) is self_task:
                _tasks.pop(user_id, None)

    _tasks[user_id] = asyncio.create_task(run())
