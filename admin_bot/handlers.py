from __future__ import annotations

import asyncio
import io
import logging
import math
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from PIL import Image

from bookmakers import bookmaker_display
from config import settings
from database import (
    admin_deposits_count,
    admin_deposits_list,
    admin_deposits_stats,
    clear_payment_qr,
    get_deposit,
    get_payment_qr,
    pop_admin_messages,
    set_payment_qr,
    transition_deposit,
)
from money import format_amount, format_kgs
from qr_util import get_payment_qr_bytes, invalidate_payment_qr_cache

router = Router(name="admin")
log = logging.getLogger(__name__)

BTN_HISTORY = "📜 История пополнений"
BTN_QR = "🧾 QR оплаты"
PAGE_SIZE = 10

# Телеграм отдаёт документы до 20 МБ, но QR — это маленькая картинка;
# лимит отсекает случайно присланный «не тот» файл до скачивания.
QR_MAX_BYTES = 5 * 1024 * 1024

_FILTER_KEYS = {
    "all": None,
    "confirmed": "confirmed",
    "pending": "pending_review",
    "rejected": "rejected",
    "awaiting": "awaiting_receipt",
    "expired": "expired_timeout",
}

_FILTER_LABELS = {
    "all": "Все",
    "confirmed": "✅",
    "pending": "🔍",
    "rejected": "❌",
    "awaiting": "⏳",
    "expired": "⌛",
}

_STATUS_LABELS = {
    "awaiting_receipt": "⏳ Ожидает чек",
    "pending_review": "🔍 На проверке",
    "confirmed": "✅ Подтверждено",
    "rejected": "❌ Отклонено",
    "expired_timeout": "⌛ Просрочено",
}


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


def _admin_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_HISTORY)], [KeyboardButton(text=BTN_QR)]],
        resize_keyboard=True,
    )


def _user_label(row: dict) -> str:
    uid = int(row["user_id"])
    username = row.get("username")
    first_name = row.get("first_name")
    if username:
        return f"@{escape(username)}"
    if first_name:
        return escape(str(first_name))
    return f"<code>{uid}</code>"


def _history_filter_kb(page: int, filter_key: str, total: int) -> InlineKeyboardMarkup:
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◀️",
                callback_data=f"adm_hist:{page - 1}:{filter_key}",
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data=f"adm_hist:{page}:{filter_key}",
        )
    )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                text="▶️",
                callback_data=f"adm_hist:{page + 1}:{filter_key}",
            )
        )
    filter_row = [
        InlineKeyboardButton(
            text=f"{'• ' if filter_key == key else ''}{label}",
            callback_data=f"adm_hist:0:{key}",
        )
        for key, label in _FILTER_LABELS.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=[filter_row, nav])


def _admin_history_html(rows: list[dict], *, filter_key: str, page: int, total: int) -> str:
    filter_label = _FILTER_LABELS.get(filter_key, filter_key)
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    lines = [
        "📜 <b>История пополнений</b>",
        f"<i>Фильтр: {escape(filter_label)} · стр. {page + 1}/{total_pages} · всего {total}</i>",
        "",
    ]
    if not rows:
        lines.append("Записей нет.")
        return "\n".join(lines)

    sep = "──────────────"
    for r in rows:
        rid = int(r["id"])
        bm = escape(bookmaker_display(r["bookmaker"]))
        amt = format_amount(float(r["amount"]))
        acc = escape(str(r["account_id"]))
        status = _STATUS_LABELS.get(r["status"], escape(str(r["status"])))
        when = escape(str(r["confirmed_at"] or r["created_at"]))
        user = _user_label(r)
        uid = int(r["user_id"])
        lines.extend(
            [
                sep,
                f"🆔 <b>#{rid}</b> · {status}",
                f"👤 {user} · <code>{uid}</code>",
                f"🎰 {bm} · <code>{acc}</code>",
                f"💰 <b>{amt}</b> KGS",
                f"🕐 <i>{when}</i>",
                "",
            ]
        )
    if lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


async def _send_admin_history(
    message: Message,
    *,
    page: int = 0,
    filter_key: str = "all",
    edit: bool = False,
) -> None:
    status = _FILTER_KEYS.get(filter_key)
    total = await admin_deposits_count(status)
    if total == 0:
        text = _admin_history_html([], filter_key=filter_key, page=0, total=0)
        kb = _history_filter_kb(0, filter_key, 0)
        if edit:
            try:
                await message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            except TelegramBadRequest:
                await message.answer(text, parse_mode="HTML", reply_markup=kb)
        else:
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
        return

    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    rows = await admin_deposits_list(status, limit=PAGE_SIZE, offset=page * PAGE_SIZE)
    text = _admin_history_html(rows, filter_key=filter_key, page=page, total=total)
    kb = _history_filter_kb(page, filter_key, total)
    if edit:
        try:
            await message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except TelegramBadRequest:
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)


async def _send_admin_stats(message: Message) -> None:
    stats = await admin_deposits_stats()
    confirmed = stats.get("confirmed", {"count": 0, "total": 0.0})
    pending = stats.get("pending_review", {"count": 0, "total": 0.0})
    rejected = stats.get("rejected", {"count": 0, "total": 0.0})
    awaiting = stats.get("awaiting_receipt", {"count": 0, "total": 0.0})
    expired = stats.get("expired_timeout", {"count": 0, "total": 0.0})
    total_ops = sum(v["count"] for v in stats.values())
    lines = [
        "📊 <b>Статистика операций</b>",
        "",
        f"✅ Подтверждено: <b>{confirmed['count']}</b> · {format_amount(confirmed['total'])} KGS",
        f"🔍 На проверке: <b>{pending['count']}</b> · {format_amount(pending['total'])} KGS",
        f"⏳ Ожидает чек: <b>{awaiting['count']}</b>",
        f"❌ Отклонено: <b>{rejected['count']}</b>",
        f"⌛ Просрочено: <b>{expired['count']}</b>",
        "",
        f"<i>Всего заявок: {total_ops}</i>",
    ]
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(CommandStart())
async def admin_start(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Доступ запрещён.")
        return
    await state.clear()
    await message.answer(
        "Админ-бот: здесь приходят чеки и заявки на пополнение.\n"
        "Используйте кнопки под сообщением с чеком.\n\n"
        "📜 <b>История пополнений</b> — кнопка ниже или /history\n"
        "📊 <b>Статистика</b> — /stats\n"
        "🧾 <b>QR оплаты</b> — кнопка ниже или /qr",
        parse_mode="HTML",
        reply_markup=_admin_menu_kb(),
    )


@router.message(Command("history"))
@router.message(F.text == BTN_HISTORY)
async def admin_history(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Доступ запрещён.")
        return
    await state.clear()
    await _send_admin_history(message)


@router.message(Command("stats"))
async def admin_stats(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Доступ запрещён.")
        return
    await state.clear()
    await _send_admin_stats(message)


@router.callback_query(F.data.startswith("adm_hist:"))
async def admin_history_page(query: CallbackQuery) -> None:
    if not _is_admin(query.from_user.id):
        await query.answer("Нет доступа", show_alert=True)
        return
    parts = query.data.split(":", 2)
    if len(parts) != 3:
        await query.answer()
        return
    page = int(parts[1])
    filter_key = parts[2]
    if filter_key not in _FILTER_KEYS:
        filter_key = "all"
    await _send_admin_history(query.message, page=page, filter_key=filter_key, edit=True)
    await query.answer()


class QrFSM(StatesGroup):
    waiting_image = State()


def _qr_card_kb(*, has_custom: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="♻️ Заменить QR", callback_data="adm_qr:set")]]
    if has_custom:
        rows.append(
            [InlineKeyboardButton(text="🗑 Вернуть стандартный", callback_data="adm_qr:reset")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _is_image(data: bytes) -> bool:
    """Битый или «не тот» файл дошёл бы до клиентов вместо QR — проверяем до записи."""
    try:
        Image.open(io.BytesIO(data)).verify()
    except Exception:
        return False
    return True


async def _send_qr_card(message: Message) -> None:
    custom = await get_payment_qr()
    png, name = await get_payment_qr_bytes()
    if custom is not None:
        source = (
            "Источник: <b>загружен из админ-бота</b>\n"
            f"Обновлён: <i>{escape(str(custom['updated_at']))}</i>"
        )
    elif settings.payment_qr_path is not None:
        source = (
            "Источник: <b>файл в проекте</b>\n"
            f"<code>{escape(settings.payment_qr_path.name)}</code>"
        )
    else:
        source = "Источник: <b>сгенерирован</b> из PAYMENT_QR_PAYLOAD"
    caption = (
        "🧾 <b>QR оплаты</b>\n"
        f"{source}\n\n"
        "Именно этот QR получают клиенты при пополнении."
    )
    await message.answer_photo(
        photo=BufferedInputFile(png, filename=name),
        caption=caption,
        parse_mode="HTML",
        reply_markup=_qr_card_kb(has_custom=custom is not None),
    )


@router.message(Command("qr"))
@router.message(F.text == BTN_QR)
async def admin_qr(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Доступ запрещён.")
        return
    await state.clear()
    await _send_qr_card(message)


@router.callback_query(F.data == "adm_qr:set")
async def admin_qr_set(query: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(query.from_user.id):
        await query.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(QrFSM.waiting_image)
    await query.message.answer(
        "Пришлите новый QR картинкой (фото или файлом .png/.jpg).\n"
        "Он сразу заменит текущий для всех клиентов.\n\n"
        "Отмена — /cancel",
    )
    await query.answer()


@router.callback_query(F.data == "adm_qr:reset")
async def admin_qr_reset(query: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(query.from_user.id):
        await query.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    removed = await clear_payment_qr()
    invalidate_payment_qr_cache()
    await query.answer("Возвращён стандартный QR" if removed else "Уже стандартный")
    if removed:
        log.info("Админ %s сбросил QR оплаты на стандартный", query.from_user.id)
        await _send_qr_card(query.message)


@router.message(QrFSM.waiting_image, Command("cancel"))
async def admin_qr_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Замена QR отменена — текущий QR не тронут.")


@router.message(QrFSM.waiting_image, F.photo | F.document)
async def admin_qr_receive(message: Message, state: FSMContext, bot: Bot) -> None:
    if not _is_admin(message.from_user.id):
        return

    if message.photo:
        photo = message.photo[-1]
        file_id, filename, size = photo.file_id, "payment_qr.jpg", photo.file_size or 0
    else:
        doc = message.document
        if doc.mime_type and not doc.mime_type.startswith("image/"):
            await message.answer("Нужна картинка: пришлите фото или файл .png/.jpg.")
            return
        file_id = doc.file_id
        filename = doc.file_name or "payment_qr.png"
        size = doc.file_size or 0

    if size > QR_MAX_BYTES:
        await message.answer(
            f"Файл слишком большой ({size // 1024} КБ). Максимум — {QR_MAX_BYTES // 1024 // 1024} МБ."
        )
        return

    try:
        buf = await bot.download(file_id)
        data = buf.read()
    except Exception as exc:
        log.warning("Не удалось скачать QR от админа %s: %s", message.from_user.id, exc)
        await message.answer("Не удалось скачать файл. Попробуйте ещё раз.")
        return

    if not data:
        await message.answer("Файл пустой. Пришлите картинку ещё раз.")
        return
    if not await asyncio.to_thread(_is_image, data):
        await message.answer("Это не похоже на картинку. Пришлите QR как фото или .png/.jpg.")
        return

    await set_payment_qr(data, filename)
    invalidate_payment_qr_cache()
    await state.clear()
    log.info("Админ %s обновил QR оплаты (%s, %d байт)", message.from_user.id, filename, len(data))
    await message.answer("✅ QR обновлён — клиенты получают его уже со следующей заявки.")
    await _send_qr_card(message)


@router.message(QrFSM.waiting_image, F.text)
async def admin_qr_wait_hint(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    await message.answer("Жду картинку с QR. Отмена — /cancel")


@router.callback_query(F.data.startswith("dep_ok:"))
async def approve_deposit(query: CallbackQuery, bot: Bot, dispatcher: Dispatcher) -> None:
    await _decide_deposit(query, bot, dispatcher, approve=True)


@router.callback_query(F.data.startswith("dep_no:"))
async def reject_deposit(query: CallbackQuery, bot: Bot, dispatcher: Dispatcher) -> None:
    await _decide_deposit(query, bot, dispatcher, approve=False)


async def _decide_deposit(
    query: CallbackQuery,
    bot: Bot,
    dispatcher: Dispatcher,
    *,
    approve: bool,
) -> None:
    if not _is_admin(query.from_user.id):
        await query.answer("Нет доступа", show_alert=True)
        return

    try:
        deposit_id = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer("Некорректная заявка", show_alert=True)
        return

    # Смена статуса и проверка «а не обработали ли уже» — одним UPDATE ... WHERE status=?.
    # Иначе два админа, нажавшие одновременно, оба проходили проверку и пользователь
    # получал два уведомления (а заявка засчитывалась дважды).
    row = await transition_deposit(
        deposit_id,
        expected="pending_review",
        new="confirmed" if approve else "rejected",
    )
    if row is None:
        existing = await get_deposit(deposit_id)
        if existing is None:
            await query.answer("Заявка не найдена", show_alert=True)
        else:
            await query.answer(
                f"Уже обработано: {_STATUS_LABELS.get(existing['status'], existing['status'])}",
                show_alert=True,
            )
            await _close_admin_copies(bot, deposit_id, approved=existing["status"] == "confirmed")
        return

    if approve:
        text = (
            "✅ Счёт пополнен\n\n"
            f"Букмекер: {bookmaker_display(row['bookmaker'])}\n"
            f"ID счёта: {row['account_id']}\n"
            f"Сумма: {format_kgs(row['amount'])}\n\n"
            "Запись добавлена в «Историю»."
        )
    else:
        text = (
            "❌ Заявка на пополнение отклонена.\n\n"
            "Если это ошибка, напишите в тех. поддержку и приложите чек."
        )

    user_bot: Bot = dispatcher.workflow_data["user_bot"]
    try:
        await user_bot.send_message(chat_id=row["user_id"], text=text)
    except TelegramForbiddenError:
        await query.message.answer(
            f"⚠️ Статус обновлён, но пользователь <code>{row['user_id']}</code> "
            "заблокировал бота — уведомление не доставлено.",
            parse_mode="HTML",
        )
    except Exception as exc:
        log.warning("Не удалось уведомить пользователя %s: %s", row["user_id"], exc)
        await query.message.answer(
            f"⚠️ Статус обновлён, но уведомить пользователя {row['user_id']} не удалось."
        )

    await _close_admin_copies(bot, deposit_id, approved=approve)
    await query.answer("Подтверждено" if approve else "Отклонено")


async def _close_admin_copies(bot: Bot, deposit_id: int, *, approved: bool) -> None:
    """Убрать заявку у ВСЕХ админов, а не только у нажавшего.

    Раньше правилось лишь сообщение автора клика — у остальных админов кнопки
    оставались живыми, и клик по ним упирался в «Уже обработано».
    """
    verdict = "✅ Подтверждено" if approved else "❌ Отклонено"
    for msg in await pop_admin_messages(deposit_id):
        chat_id, message_id = msg["chat_id"], msg["message_id"]
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
            continue
        except TelegramForbiddenError:
            continue
        except TelegramBadRequest:
            pass  # сообщение старше 48 часов — удалить нельзя, снимаем хотя бы кнопки
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=message_id, reply_markup=None
            )
            await bot.send_message(
                chat_id, f"Заявка #{deposit_id}: {verdict}", disable_notification=True
            )
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            log.debug("Не удалось закрыть копию заявки %s: %s", deposit_id, exc)
