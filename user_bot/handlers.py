from __future__ import annotations

import asyncio
import io
import logging
import re
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, KeyboardButton, Message, ReplyKeyboardMarkup

from bookmakers import bookmaker_display
from config import settings
from database import attach_receipt, create_deposit, upsert_user, user_history
from qr_util import make_qr_png_bytes
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
from logo_fetch import fetch_image_bytes, get_bookmaker_logo_bytes, guess_logo_filename
from texts import INSTRUCTION, WITHDRAW, support_text, welcome_banner

router = Router(name="user")
log = logging.getLogger(__name__)

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
    BTN_BM_1WIN: "1WIN",
    BTN_BM_MELBET: "MELBET",
    BTN_BM_MOSTBET: "MOSTBET",
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
            [KeyboardButton(text=BTN_BM_1XBET), KeyboardButton(text=BTN_BM_1WIN)],
            [KeyboardButton(text=BTN_BM_MELBET), KeyboardButton(text=BTN_BM_MOSTBET)],
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


def _format_amount_kgs(amount: float) -> str:
    s = f"{amount:,.0f}"
    return s.replace(",", " ")


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
        amt = _format_amount_kgs(float(r["amount"]))
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
    if total_all is not None:
        if is_last_chunk:
            out.append(f"<i>Всего подтверждённых: {total_all}</i>")
        else:
            out.append("<i>⏬ Продолжение ниже…</i>")
    else:
        out.append(f"<i>В списке: {len(rows)}</i>")
    return "\n".join(out)


def _history_html_chunks(all_rows: list[dict]) -> list[str]:
    """Дробит список на части ≤ ~3900 символов (лимит Telegram ~4096)."""
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
                total_all=total,
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
                    total_all=total,
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
                    total_all=total,
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


async def send_home_with_logo(message: Message, first_name: str | None) -> None:
    text = welcome_banner(first_name)
    kb = main_menu_kb()
    url = settings.brand_logo_url
    if url:
        data = await fetch_image_bytes(url)
        if data:
            await message.answer_photo(
                photo=BufferedInputFile(data, filename=guess_logo_filename(url)),
                caption=text,
                reply_markup=kb,
            )
            return
    await message.answer(text, reply_markup=kb)


@router.message(StateFilter(*DEPOSIT_STATES), CommandStart())
async def cmd_start_blocked(message: Message, state: FSMContext) -> None:
    await deposit_reply_blocked(message, state)


@router.message(StateFilter(*DEPOSIT_STATES), Command("menu"))
async def cmd_menu_blocked(message: Message, state: FSMContext) -> None:
    await deposit_reply_blocked(message, state)


@router.message(StateFilter(*DEPOSIT_STATES), F.text.in_(CANCEL_LABELS))
async def deposit_cancel(message: Message, state: FSMContext) -> None:
    cancel_receipt_deadline(message.from_user.id)
    cancel_payment_countdown(message.from_user.id)
    await state.clear()
    await send_home_with_logo(message, message.from_user.first_name)


@router.message(StateFilter(*DEPOSIT_STATES), F.text.in_(BLOCKED_IN_DEPOSIT))
async def deposit_block_parallel(message: Message, state: FSMContext) -> None:
    await deposit_reply_blocked(message, state)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    cancel_receipt_deadline(message.from_user.id)
    cancel_payment_countdown(message.from_user.id)
    await state.clear()
    await upsert_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )
    await send_home_with_logo(message, message.from_user.first_name)


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    cancel_receipt_deadline(message.from_user.id)
    cancel_payment_countdown(message.from_user.id)
    await state.clear()
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
        f"Минимум: {settings.min_amount_kgs:.0f} KGS\n"
        f"Максимум: {settings.max_amount_kgs:,.0f} KGS\n\n"
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
    raw = (message.text or "").strip().replace(" ", "").replace(",", ".")
    try:
        amount = float(raw)
    except ValueError:
        await message.answer("Введите число — сумму в KGS.")
        return
    if amount < settings.min_amount_kgs or amount > settings.max_amount_kgs:
        await message.answer(
            f"Сумма должна быть от {settings.min_amount_kgs:.0f} до {settings.max_amount_kgs:,.0f} KGS."
        )
        return

    data = await state.get_data()
    title = data["bookmaker_title"]
    account_id = data["account_id"]

    deposit_id = await create_deposit(
        user_id=message.from_user.id,
        bookmaker=title,
        account_id=account_id,
        amount=amount,
    )
    await state.update_data(deposit_id=deposit_id, amount=amount)
    await state.set_state(DepositFSM.receipt)

    png = make_qr_png_bytes(settings.payment_qr_payload)
    cap_details = (
        "💰 Пополнение счета\n\n"
        f"Счёт: {bookmaker_display(title)}\n"
        f"ID: {account_id}\n"
        f"Сумма: {amount:,.0f} KGS"
    )
    qr_caption = format_qr_payment_caption(amount, account_id, RECEIPT_DEADLINE_SEC)

    logo_data, logo_fname = await get_bookmaker_logo_bytes(title)
    if logo_data:
        await message.answer_photo(
            photo=BufferedInputFile(logo_data, filename=logo_fname),
            caption=cap_details,
        )
    qr_msg = await message.answer_photo(
        photo=BufferedInputFile(png, filename="payment.png"),
        caption=qr_caption,
        reply_markup=deposit_step_kb(),
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
        await message.answer(f"Сессия сброшена. Начните пополнение снова: {BTN_DEPOSIT}.")
        return

    cancel_receipt_deadline(message.from_user.id)
    cancel_payment_countdown(message.from_user.id)
    photo = message.photo[-1]
    await attach_receipt(
        deposit_id=deposit_id,
        receipt_file_id=photo.file_id,
        receipt_chat_id=message.chat.id,
        receipt_message_id=message.message_id,
    )

    admin_bot: Bot | None = dispatcher.workflow_data.get("admin_bot")
    if admin_bot is None:
        await message.answer("Ошибка: админ-бот не подключён. Обратитесь к администратору.")
        return

    title = data["bookmaker_title"]
    account_id = data["account_id"]
    amount = data["amount"]
    uid = message.from_user.id
    uname = f"@{message.from_user.username}" if message.from_user.username else "—"

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

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
        f"ID заявки: {deposit_id}\n"
        f"Пользователь: {escape(uname)} (<code>{uid}</code>)\n"
        f"Счёт: {escape(bookmaker_display(title))}\n"
        f"ID счёта: <code>{escape(account_id)}</code>\n"
        f"Сумма: {amount:,.0f} KGS"
    )

    # file_id от пользовательского бота недействителен для другого бота — качаем и шлём байты
    tg_file = await bot.get_file(photo.file_id)
    buf = io.BytesIO()
    await bot.download_file(tg_file.file_path, buf)
    receipt_png = buf.getvalue()

    admin_ids = list(settings.admin_ids)
    results = await asyncio.gather(
        *[
            admin_bot.send_photo(
                chat_id=aid,
                photo=BufferedInputFile(receipt_png, filename="receipt.jpg"),
                caption=cap,
                parse_mode="HTML",
                reply_markup=kb,
            )
            for aid in admin_ids
        ],
        return_exceptions=True,
    )
    for aid, res in zip(admin_ids, results):
        if isinstance(res, Exception):
            log.warning("Не удалось отправить чек админу %s: %s", aid, res)

    amt_str = f"{float(amount):,.2f}".replace(",", " ")
    bm_code = str(title).strip().upper()
    await state.clear()
    await message.answer(
        "✅ Заявка отправлена!\n\n"
        f"💰 Сумма: {amt_str} KGS\n"
        f"🆔 ID счета {bm_code}: {account_id}\n\n"
        "⏳ Ожидайте подтверждения от оператора.\n"
        "📞 Время обработки: до 30 минут",
        reply_markup=main_menu_kb(),
    )


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
    rows = await user_history(message.from_user.id)
    if not rows:
        await message.answer(
            "📜 <b>История пополнений</b>\n\n"
            "Пока нет подтверждённых пополнений. После проверки чека записи появятся здесь.",
            parse_mode="HTML",
            reply_markup=main_menu_kb(),
        )
        return
    parts = _history_html_chunks(rows)
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
