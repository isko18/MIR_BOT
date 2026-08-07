from __future__ import annotations

import io
import logging
import re
from html import escape

from decimal import Decimal

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import (
    JOIN_TRANSITION,
    LEAVE_TRANSITION,
    ChatMemberUpdatedFilter,
    Command,
    CommandStart,
    StateFilter,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from bookmakers import bookmaker_display
from config import settings
from database import (
    add_admin_message,
    attach_receipt,
    create_deposit,
    upsert_user,
    user_confirmed_count,
    user_history,
)
from money import AmountError, format_amount, format_kgs, parse_amount
from qr_util import get_payment_qr_bytes
from .payment_countdown import (
    cancel_payment_countdown,
    format_qr_payment_caption,
    schedule_payment_countdown,
)
from .receipt_timer import (
    RECEIPT_DEADLINE_SEC,
    cancel_receipt_deadline,
    schedule_receipt_deadline,
)
from logo_fetch import get_bookmaker_logo_bytes, get_welcome_logo_bytes
from texts import INSTRUCTION, WITHDRAW, support_text, welcome_banner
from user_bot.subscription import (
    BTN_CHECK_SUBSCRIPTION,
    drop_subscription_cache,
    is_required_channel,
    is_subscription_exempt,
    send_subscription_prompt,
    subscription_check_error,
    subscription_check_ready,
    subscription_inline_kb,
    verify_user_subscribed,
)

router = Router(name="user")
log = logging.getLogger(__name__)

_MIN_AMOUNT = Decimal(str(settings.min_amount_kgs))
_MAX_AMOUNT = Decimal(str(settings.max_amount_kgs))

HISTORY_LIMIT = 20

# Тексты кнопок — только эти константы (букмекеры без эмодзи: в кнопку картинку Telegram не вставить)
BTN_DEPOSIT = "💳 Пополнить"
BTN_WITHDRAW = "📤 Вывести"
BTN_HISTORY = "📜 История"
BTN_INSTRUCTION = "📖 Инструкция"
BTN_SUPPORT = "🛟 Тех. поддержка"

# У reply-кнопок только текст (без эмодзи); картинки — в сообщении над клавиатурой / после выбора
BTN_BM_1XBET = "1xBet"
BTN_BM_1WIN = "1Win"
BTN_BM_MELBET = "Melbet"
BTN_BM_MOSTBET = "Mostbet"

BTN_CANCEL = "↩️ Отмена"
BTN_TO_MENU = "🏠 В меню"

BOOKMAKER_BY_BUTTON = {
    BTN_BM_1XBET: "1XBET",
    BTN_BM_MELBET: "MELBET",
    # 1Win и Mostbet временно отключены — чтобы вернуть, раскомментируйте здесь
    # и в bookmaker_kb() ниже.
    # BTN_BM_1WIN: "1WIN",
    # BTN_BM_MOSTBET: "MOSTBET",
}

CANCEL_LABELS = frozenset({BTN_CANCEL, BTN_TO_MENU})

MAIN_MENU_KEYS = frozenset(
    {BTN_DEPOSIT, BTN_WITHDRAW, BTN_HISTORY, BTN_INSTRUCTION, BTN_SUPPORT}
)

_FORBIDDEN_ACCOUNT_ID = CANCEL_LABELS | MAIN_MENU_KEYS | frozenset(BOOKMAKER_BY_BUTTON)


class DepositFSM(StatesGroup):
    choose_bookmaker = State()
    account_id = State()
    amount = State()
    receipt = State()


DEPOSIT_STATES = (
    DepositFSM.choose_bookmaker,
    DepositFSM.account_id,
    DepositFSM.amount,
    DepositFSM.receipt,
)
BLOCKED_IN_DEPOSIT = MAIN_MENU_KEYS | {BTN_DEPOSIT}


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_DEPOSIT), KeyboardButton(text=BTN_WITHDRAW)],
            [KeyboardButton(text=BTN_HISTORY), KeyboardButton(text=BTN_INSTRUCTION)],
            [KeyboardButton(text=BTN_SUPPORT)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие…",
    )


def bookmaker_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BM_1XBET), KeyboardButton(text=BTN_BM_MELBET)],
            # [KeyboardButton(text=BTN_BM_1WIN), KeyboardButton(text=BTN_BM_MOSTBET)],
            [KeyboardButton(text=BTN_CANCEL), KeyboardButton(text=BTN_TO_MENU)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Букмекер или отмена…",
    )


def deposit_step_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CANCEL), KeyboardButton(text=BTN_TO_MENU)]],
        resize_keyboard=True,
        input_field_placeholder="Ввод или «Отмена»…",
    )


def _history_html(
    rows: list[dict],
    *,
    continuation: bool = False,
    is_last_chunk: bool = True,
    total_all: int | None = None,
) -> str:
    """HTML-сообщение со списком подтверждённых пополнений."""
    sep = "──────────────"
    if continuation:
        head: list[str] = ["📜 <b>История — продолжение</b>", ""]
    else:
        head = [
            "📜 <b>История пополнений</b>",
            "<i>Подтверждённые операции — новые сверху.</i>",
            "",
        ]
    out = head
    for r in rows:
        bm = escape(bookmaker_display(r["bookmaker"]))
        amt = format_amount(r["amount"])
        acc = escape(str(r["account_id"]))
        when = escape(str(r["confirmed_at"] or r["created_at"]))
        rid = int(r["id"])
        out.append(sep)
        out.append(f"🆔 <b>#{rid}</b> · {bm}")
        out.append(f"💰 <b>{amt}</b> KGS")
        out.append(f"👤 <code>{acc}</code>")
        out.append(f"🕐 <i>{when}</i>")
        out.append("")
    if out[-1] == "":
        out.pop()
    out.append("")
    if not is_last_chunk:
        out.append("<i>⏬ Продолжение ниже…</i>")
    elif total_all is not None:
        out.append(f"<i>Всего подтверждённых: {total_all}</i>")
    else:
        out.append(f"<i>В списке: {len(rows)}</i>")
    return "\n".join(out)


def _history_html_chunks(all_rows: list[dict], grand_total: int) -> list[str]:
    """Дробит список на части ≤ ~3900 символов (лимит Telegram ~4096).

    grand_total — сколько подтверждённых заявок у пользователя всего; в списке
    показываются только последние HISTORY_LIMIT, и раньше подпись выдавала их
    число за «всего».
    """
    if not all_rows:
        return []
    max_len = 3900
    total = len(all_rows)
    chunks: list[str] = []
    start = 0
    while start < total:
        lo, hi = start, total - 1
        best_end = start - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            part = all_rows[start : mid + 1]
            is_last = mid == total - 1
            text = _history_html(
                part,
                continuation=bool(chunks),
                is_last_chunk=is_last,
                total_all=grand_total,
            )
            if len(text) <= max_len:
                best_end = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if best_end < start:
            part = all_rows[start : start + 1]
            chunks.append(
                _history_html(
                    part,
                    continuation=bool(chunks),
                    is_last_chunk=start + 1 >= total,
                    total_all=grand_total,
                )
            )
            start += 1
        else:
            part = all_rows[start : best_end + 1]
            chunks.append(
                _history_html(
                    part,
                    continuation=bool(chunks),
                    is_last_chunk=best_end == total - 1,
                    total_all=grand_total,
                )
            )
            start = best_end + 1
    return chunks


async def deposit_reply_blocked(message: Message, state: FSMContext) -> None:
    st = await state.get_state()
    if st == DepositFSM.choose_bookmaker.state:
        kb = bookmaker_kb()
    else:
        kb = deposit_step_kb()
    await message.answer(
        "Сейчас оформляется пополнение. Другие действия недоступны. "
        "Нажмите «Отмена» или «В меню», чтобы выйти.",
        reply_markup=kb,
    )


async def goto_account_id_step(message: Message, state: FSMContext, title: str) -> None:
    await state.update_data(bookmaker_title=title)
    await state.set_state(DepositFSM.account_id)
    await answer_caption_with_bookmaker_logo(
        message,
        title,
        f"💰 Пополнение счета\n\nСчёт: {bookmaker_display(title)}\n\nВведите ID счета:",
        reply_markup=deposit_step_kb(),
    )


async def send_bookmaker_keyboard_prompt(message: Message) -> None:
    """Только текст и reply-клавиатура с букмекерами."""
    await message.answer("Выберите букмекера:", reply_markup=bookmaker_kb())


async def answer_caption_with_bookmaker_logo(
    message: Message,
    title: str,
    caption: str,
    reply_markup: ReplyKeyboardMarkup | None = None,
) -> None:
    """Текст или фото с подписью: локальный .ico/URL из bookmakers.py."""
    data, fname = await get_bookmaker_logo_bytes(title)
    if data:
        await message.answer_photo(
            photo=BufferedInputFile(data, filename=fname),
            caption=caption,
            reply_markup=reply_markup,
        )
        return
    await message.answer(caption, reply_markup=reply_markup)


async def send_home_to_user(bot: Bot, user_id: int, first_name: str | None) -> None:
    text = welcome_banner(first_name)
    kb = main_menu_kb()
    data, fname = await get_welcome_logo_bytes()
    if data:
        await bot.send_photo(
            chat_id=user_id,
            photo=BufferedInputFile(data, filename=fname),
            caption=text,
            parse_mode="HTML",
            reply_markup=kb,
        )
        return
    await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML", reply_markup=kb)


async def send_home_with_logo(message: Message, first_name: str | None) -> None:
    await send_home_to_user(message.bot, message.from_user.id, first_name)


async def _clear_user_flow(state: FSMContext, user_id: int) -> None:
    cancel_receipt_deadline(user_id)
    cancel_payment_countdown(user_id)
    await state.clear()


async def _subscription_fail_message(message: Message, config_error: str | None) -> None:
    ch = (settings.required_channel_username or "the100som").lstrip("@")
    hint = ""
    if config_error and "member list is inaccessible" in config_error.lower():
        hint = (
            "\n\nПричина: бот не может видеть участников канала.\n"
            "Добавьте пользовательский бот в @"
            f"{ch} как <b>администратора</b> (достаточно права «добавление участников»)."
        )
    await message.answer(
        "⚠️ Не удалось проверить подписку автоматически."
        f"{hint}\n\n"
        "После настройки перезапустите бота.",
        parse_mode="HTML",
        reply_markup=subscription_inline_kb(ch),
    )


async def _enter_home_if_subscribed(message: Message, state: FSMContext) -> bool:
    if is_subscription_exempt(message.from_user.id):
        await _clear_user_flow(state, message.from_user.id)
        await send_home_with_logo(message, message.from_user.first_name)
        return True

    channel = settings.required_channel_username
    if not channel:
        await _clear_user_flow(state, message.from_user.id)
        await send_home_with_logo(message, message.from_user.first_name)
        return True

    if not subscription_check_ready():
        await send_subscription_prompt(message, channel)
        await _subscription_fail_message(message, subscription_check_error())
        return False

    subscribed, config_error = await verify_user_subscribed(
        message.bot,
        message.from_user.id,
        retries=4,
        delay_sec=1.0,
    )
    if subscribed:
        await _clear_user_flow(state, message.from_user.id)
        await send_home_with_logo(message, message.from_user.first_name)
        return True

    await send_subscription_prompt(message, channel)
    if config_error:
        await _subscription_fail_message(message, config_error)
    return False


async def _handle_subscription_check(
    message: Message,
    user_id: int,
    first_name: str | None,
    state: FSMContext,
    *,
    from_callback: bool = False,
) -> None:
    channel = settings.required_channel_username
    if not channel:
        await _clear_user_flow(state, user_id)
        await send_home_to_user(message.bot, user_id, first_name)
        return

    if not subscription_check_ready():
        await _subscription_fail_message(message, subscription_check_error())
        return

    subscribed, config_error = await verify_user_subscribed(
        message.bot,
        user_id,
        retries=4,
        delay_sec=1.0,
    )
    if subscribed:
        await _clear_user_flow(state, user_id)
        await send_home_to_user(message.bot, user_id, first_name)
        return

    if config_error:
        await _subscription_fail_message(message, config_error)
        return

    ch = channel.lstrip("@")
    text = (
        "❌ Подписка пока не видна.\n\n"
        f"1. Подпишитесь на @{ch}\n"
        "2. Вернитесь в бот — проверка произойдёт автоматически\n\n"
        "Если только что подписались, подождите пару секунд и нажмите /start."
    )
    if from_callback:
        await message.answer(text, reply_markup=subscription_inline_kb(ch))
    else:
        await message.answer(text, reply_markup=subscription_inline_kb(ch))


@router.message(StateFilter(*DEPOSIT_STATES), CommandStart())
async def cmd_start_blocked(message: Message, state: FSMContext) -> None:
    await deposit_reply_blocked(message, state)


@router.message(StateFilter(*DEPOSIT_STATES), Command("menu"))
async def cmd_menu_blocked(message: Message, state: FSMContext) -> None:
    await deposit_reply_blocked(message, state)


@router.message(StateFilter(*DEPOSIT_STATES), Command("cancel"))
@router.message(StateFilter(*DEPOSIT_STATES), F.text.in_(CANCEL_LABELS))
async def deposit_cancel(message: Message, state: FSMContext) -> None:
    await _clear_user_flow(state, message.from_user.id)
    await send_home_with_logo(message, message.from_user.first_name)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await _clear_user_flow(state, message.from_user.id)
    await send_home_with_logo(message, message.from_user.first_name)


@router.message(StateFilter(*DEPOSIT_STATES), F.text.in_(BLOCKED_IN_DEPOSIT))
async def deposit_block_parallel(message: Message, state: FSMContext) -> None:
    await deposit_reply_blocked(message, state)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await upsert_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )
    await _enter_home_if_subscribed(message, state)


@router.message(F.text == BTN_CHECK_SUBSCRIPTION)
async def check_subscription_message(message: Message, state: FSMContext) -> None:
    drop_subscription_cache(message.from_user.id)
    await _handle_subscription_check(
        message,
        message.from_user.id,
        message.from_user.first_name,
        state,
    )


@router.callback_query(F.data == "sub_check")
async def check_subscription_callback(query: CallbackQuery, state: FSMContext) -> None:
    if not query.from_user or not query.message:
        await query.answer()
        return
    await query.answer("Проверяем подписку…")
    # Пользователь только что подписался — кэш заведомо устарел.
    drop_subscription_cache(query.from_user.id)
    await _handle_subscription_check(
        query.message,
        query.from_user.id,
        query.from_user.first_name,
        state,
        from_callback=True,
    )


@router.chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def on_channel_subscribe(event: ChatMemberUpdated, bot: Bot) -> None:
    if not is_required_channel(event.chat.id, event.chat.username):
        return
    user = event.from_user
    if not user:
        return

    await upsert_user(user.id, user.username, user.first_name)
    subscribed, config_error = await verify_user_subscribed(
        bot, user.id, retries=3, delay_sec=0.5, use_cache=False
    )
    if not subscribed:
        if config_error:
            log.warning("Auto subscribe notify skipped for %s: %s", user.id, config_error)
        return

    cancel_receipt_deadline(user.id)
    cancel_payment_countdown(user.id)
    try:
        await bot.send_message(
            user.id,
            "✅ Подписка подтверждена! Добро пожаловать.",
        )
        await send_home_to_user(bot, user.id, user.first_name)
    except Exception as exc:
        log.warning("Could not notify user %s after channel join: %s", user.id, exc)


@router.chat_member(ChatMemberUpdatedFilter(LEAVE_TRANSITION))
async def on_channel_unsubscribe(event: ChatMemberUpdated) -> None:
    """Отписался — сразу снимаем доступ, не дожидаясь истечения TTL кэша."""
    if not is_required_channel(event.chat.id, event.chat.username):
        return
    if event.from_user:
        drop_subscription_cache(event.from_user.id)


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await _clear_user_flow(state, message.from_user.id)
    await send_home_with_logo(message, message.from_user.first_name)


@router.message(F.text == BTN_DEPOSIT)
async def deposit_start(message: Message, state: FSMContext) -> None:
    cancel_receipt_deadline(message.from_user.id)
    cancel_payment_countdown(message.from_user.id)
    await state.clear()
    await state.set_state(DepositFSM.choose_bookmaker)
    await send_bookmaker_keyboard_prompt(message)


@router.message(DepositFSM.choose_bookmaker, F.text.in_(tuple(BOOKMAKER_BY_BUTTON)))
async def deposit_pick_bookmaker(message: Message, state: FSMContext) -> None:
    label = (message.text or "").strip()
    title = BOOKMAKER_BY_BUTTON[label]
    await goto_account_id_step(message, state, title)


@router.message(DepositFSM.choose_bookmaker, F.text)
async def deposit_choose_bookmaker_hint(message: Message) -> None:
    await message.answer(
        "Выберите букмекера кнопкой ниже или нажмите «Отмена» / «В меню».",
        reply_markup=bookmaker_kb(),
    )


@router.message(DepositFSM.account_id, F.text)
async def deposit_account_id(message: Message, state: FSMContext) -> None:
    aid = (message.text or "").strip()
    if aid in _FORBIDDEN_ACCOUNT_ID:
        await message.answer(
            "Введите ID игрового счёта или нажмите «Отмена» / «В меню».",
            reply_markup=deposit_step_kb(),
        )
        return
    if len(aid) < 3 or len(aid) > 64:
        await message.answer("ID слишком короткий или длинный. Введите корректный ID счёта.")
        return
    if not re.match(r"^[\w\-@.+]+$", aid):
        await message.answer("Допустимы буквы, цифры и символы _ - @ . + Попробуйте снова.")
        return
    await state.update_data(account_id=aid)
    await state.set_state(DepositFSM.amount)
    await message.answer(
        "💰 Пополнение счета\n\n"
        f"Минимум: {format_kgs(_MIN_AMOUNT)}\n"
        f"Максимум: {format_kgs(_MAX_AMOUNT)}\n\n"
        "Введите сумму пополнения:",
        reply_markup=deposit_step_kb(),
    )


@router.message(DepositFSM.amount, F.text)
async def deposit_amount(
    message: Message,
    state: FSMContext,
    dispatcher: Dispatcher,
    bot: Bot,
) -> None:
    try:
        amount_dec = parse_amount(message.text or "")
    except AmountError:
        await message.answer(
            "Введите сумму числом, например: 500 или 1500.50",
            reply_markup=deposit_step_kb(),
        )
        return
    if amount_dec < _MIN_AMOUNT or amount_dec > _MAX_AMOUNT:
        await message.answer(
            f"Сумма должна быть от {format_amount(_MIN_AMOUNT)} "
            f"до {format_amount(_MAX_AMOUNT)} KGS.",
            reply_markup=deposit_step_kb(),
        )
        return

    data = await state.get_data()
    title = data.get("bookmaker_title")
    account_id = data.get("account_id")
    if not title or not account_id:
        # FSM переживает рестарт (Redis), а данные шага могли не сохраниться.
        await state.clear()
        await message.answer(
            f"Сессия сброшена. Начните пополнение снова: {BTN_DEPOSIT}",
            reply_markup=main_menu_kb(),
        )
        return

    amount = float(amount_dec)
    deposit_id = await create_deposit(
        user_id=message.from_user.id,
        bookmaker=title,
        account_id=account_id,
        amount=amount,
    )
    await state.update_data(deposit_id=deposit_id, amount=amount)
    await state.set_state(DepositFSM.receipt)

    png, png_name = await get_payment_qr_bytes()
    cap_details = (
        "💰 Пополнение счета\n\n"
        f"Счёт: {bookmaker_display(title)}\n"
        f"ID: {account_id}\n"
        f"Сумма: {format_kgs(amount)}"
    )
    qr_caption = format_qr_payment_caption(amount, account_id, RECEIPT_DEADLINE_SEC)

    logo_data, logo_fname = await get_bookmaker_logo_bytes(title)
    if logo_data:
        await message.answer_photo(
            photo=BufferedInputFile(logo_data, filename=logo_fname),
            caption=cap_details,
            reply_markup=deposit_step_kb(),
        )
    else:
        await message.answer(cap_details, reply_markup=deposit_step_kb())

    # QR отправляем БЕЗ reply_markup: Telegram запрещает править сообщение, к которому
    # прикреплена reply-клавиатура («message can't be edited»), а подпись здесь каждую
    # секунду обновляет счётчик оплаты. Клавиатуру несёт сообщение выше — в чате она
    # остаётся до следующей замены.
    qr_msg = await message.answer_photo(
        photo=BufferedInputFile(png, filename=png_name),
        caption=qr_caption,
    )
    schedule_payment_countdown(
        bot,
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        message_id=qr_msg.message_id,
        amount=amount,
        account_id=account_id,
        edit_caption=True,
    )
    schedule_receipt_deadline(
        dispatcher,
        bot,
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        deposit_id=deposit_id,
        main_menu_kb=main_menu_kb,
    )


@router.message(DepositFSM.receipt, F.photo)
async def deposit_receipt_photo(
    message: Message,
    state: FSMContext,
    dispatcher: Dispatcher,
    bot: Bot,
) -> None:
    data = await state.get_data()
    deposit_id = data.get("deposit_id")
    if not deposit_id:
        await state.clear()
        await message.answer(
            f"Сессия сброшена. Начните пополнение снова: {BTN_DEPOSIT}",
            reply_markup=main_menu_kb(),
        )
        return

    cancel_receipt_deadline(message.from_user.id)
    cancel_payment_countdown(message.from_user.id)
    photo = message.photo[-1]

    accepted = await attach_receipt(
        deposit_id=deposit_id,
        user_id=message.from_user.id,
        receipt_file_id=photo.file_id,
        receipt_chat_id=message.chat.id,
        receipt_message_id=message.message_id,
    )
    if not accepted:
        # Заявка уже просрочена / обработана — второй чек к ней не принимаем.
        await state.clear()
        await message.answer(
            "⏰ Эта заявка больше не активна — время оплаты истекло или её уже обработали.\n\n"
            f"Начните заново: {BTN_DEPOSIT}",
            reply_markup=main_menu_kb(),
        )
        return

    admin_bot: Bot | None = dispatcher.workflow_data.get("admin_bot")
    if admin_bot is None:
        log.error("admin_bot не подключён — заявка %s без уведомления", deposit_id)
        await message.answer("Ошибка: админ-бот не подключён. Обратитесь к администратору.")
        return

    title = data["bookmaker_title"]
    account_id = data["account_id"]
    amount = data["amount"]
    uid = message.from_user.id
    uname = f"@{message.from_user.username}" if message.from_user.username else "—"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"dep_ok:{deposit_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"dep_no:{deposit_id}"),
            ],
        ]
    )
    cap = (
        "🔔 Новая заявка на пополнение\n\n"
        f"ID заявки: <b>{deposit_id}</b>\n"
        f"Пользователь: {escape(uname)} (<code>{uid}</code>)\n"
        f"Счёт: {escape(bookmaker_display(title))}\n"
        f"ID счёта: <code>{escape(account_id)}</code>\n"
        f"Сумма: <b>{escape(format_kgs(amount))}</b>"
    )

    # Отвечаем пользователю сразу: заливка чека админам не должна держать его в ожидании.
    await state.clear()
    await message.answer(
        "✅ Заявка отправлена!\n\n"
        f"💰 Сумма: {format_kgs(amount)}\n"
        f"🆔 ID счета {str(title).strip().upper()}: {account_id}\n\n"
        "⏳ Ожидайте подтверждения от оператора.\n"
        "📞 Время обработки: до 30 минут",
        reply_markup=main_menu_kb(),
    )

    await _notify_admins(bot, admin_bot, deposit_id, photo.file_id, cap, kb)


async def _notify_admins(
    user_bot: Bot,
    admin_bot: Bot,
    deposit_id: int,
    photo_file_id: str,
    caption: str,
    kb: InlineKeyboardMarkup,
) -> None:
    """Разослать чек админам и запомнить message_id каждой копии.

    file_id пользовательского бота недействителен для админ-бота, поэтому первый
    админ получает загруженные байты, а остальные — file_id из ответа Telegram:
    так чек уходит на серверы Telegram один раз, а не N раз.
    """
    tg_file = await user_bot.get_file(photo_file_id)
    buf = io.BytesIO()
    await user_bot.download_file(tg_file.file_path, buf)
    receipt: BufferedInputFile | str = BufferedInputFile(buf.getvalue(), filename="receipt.jpg")

    for admin_id in sorted(settings.admin_ids):
        try:
            sent = await admin_bot.send_photo(
                chat_id=admin_id,
                photo=receipt,
                caption=caption,
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception as exc:
            log.warning("Не удалось отправить чек админу %s: %s", admin_id, exc)
            continue
        if isinstance(receipt, BufferedInputFile) and sent.photo:
            receipt = sent.photo[-1].file_id
        await add_admin_message(deposit_id, sent.chat.id, sent.message_id)


@router.message(DepositFSM.receipt)
async def deposit_receipt_wrong(message: Message) -> None:
    await message.answer("Пришлите чек одним фото (как картинку в чат).")


@router.message(F.text == BTN_WITHDRAW)
async def withdraw(message: Message, state: FSMContext) -> None:
    cancel_receipt_deadline(message.from_user.id)
    cancel_payment_countdown(message.from_user.id)
    await state.clear()
    await message.answer(WITHDRAW, reply_markup=main_menu_kb())


@router.message(F.text == BTN_HISTORY)
async def history(message: Message, state: FSMContext) -> None:
    cancel_receipt_deadline(message.from_user.id)
    cancel_payment_countdown(message.from_user.id)
    await state.clear()
    rows = await user_history(message.from_user.id, limit=HISTORY_LIMIT)
    if not rows:
        await message.answer(
            "📜 <b>История пополнений</b>\n\n"
            "Пока нет подтверждённых пополнений. После проверки чека записи появятся здесь.",
            parse_mode="HTML",
            reply_markup=main_menu_kb(),
        )
        return
    grand_total = await user_confirmed_count(message.from_user.id)
    parts = _history_html_chunks(rows, grand_total)
    for i, text in enumerate(parts):
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=main_menu_kb() if i == len(parts) - 1 else None,
        )


@router.message(F.text == BTN_INSTRUCTION)
async def instruction(message: Message, state: FSMContext) -> None:
    cancel_receipt_deadline(message.from_user.id)
    cancel_payment_countdown(message.from_user.id)
    await state.clear()
    await message.answer(INSTRUCTION, reply_markup=main_menu_kb())


@router.message(F.text == BTN_SUPPORT)
async def support(message: Message, state: FSMContext) -> None:
    cancel_receipt_deadline(message.from_user.id)
    cancel_payment_countdown(message.from_user.id)
    await state.clear()
    await message.answer(support_text(settings.support_username), reply_markup=main_menu_kb())
