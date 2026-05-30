from __future__ import annotations

import math
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from bookmakers import bookmaker_display
from config import settings
from database import (
    admin_deposits_count,
    admin_deposits_list,
    admin_deposits_stats,
    get_deposit,
    set_deposit_status,
)

router = Router(name="admin")

BTN_HISTORY = "📜 История пополнений"
PAGE_SIZE = 10

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
        keyboard=[[KeyboardButton(text=BTN_HISTORY)]],
        resize_keyboard=True,
    )


def _format_amount_kgs(amount: float) -> str:
    return f"{amount:,.0f}".replace(",", " ")


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
        amt = _format_amount_kgs(float(r["amount"]))
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
        f"✅ Подтверждено: <b>{confirmed['count']}</b> · {_format_amount_kgs(confirmed['total'])} KGS",
        f"🔍 На проверке: <b>{pending['count']}</b> · {_format_amount_kgs(pending['total'])} KGS",
        f"⏳ Ожидает чек: <b>{awaiting['count']}</b>",
        f"❌ Отклонено: <b>{rejected['count']}</b>",
        f"⌛ Просрочено: <b>{expired['count']}</b>",
        "",
        f"<i>Всего заявок: {total_ops}</i>",
    ]
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(CommandStart())
async def admin_start(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Доступ запрещён.")
        return
    await message.answer(
        "Админ-бот: здесь приходят чеки и заявки на пополнение.\n"
        "Используйте кнопки под сообщением с чеком.\n\n"
        "📜 <b>История пополнений</b> — кнопка ниже или /history\n"
        "📊 <b>Статистика</b> — /stats",
        parse_mode="HTML",
        reply_markup=_admin_menu_kb(),
    )


@router.message(Command("history"))
@router.message(F.text == BTN_HISTORY)
async def admin_history(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Доступ запрещён.")
        return
    await _send_admin_history(message)


@router.message(Command("stats"))
async def admin_stats(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Доступ запрещён.")
        return
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


@router.callback_query(F.data.startswith("dep_ok:"))
async def approve_deposit(query: CallbackQuery, dispatcher: Dispatcher) -> None:
    if not _is_admin(query.from_user.id):
        await query.answer("Нет доступа", show_alert=True)
        return

    deposit_id = int(query.data.split(":", 1)[1])
    row = await get_deposit(deposit_id)
    if not row:
        await query.answer("Заявка не найдена", show_alert=True)
        return
    if row["status"] != "pending_review":
        await query.answer("Уже обработано", show_alert=True)
        return

    await set_deposit_status(deposit_id, "confirmed")
    user_bot: Bot = dispatcher.workflow_data["user_bot"]
    uid = row["user_id"]
    try:
        await user_bot.send_message(
            chat_id=uid,
            text=(
                "✅ Счёт пополнен\n\n"
                f"Букмекер: {bookmaker_display(row['bookmaker'])}\n"
                f"ID счёта: {row['account_id']}\n"
                f"Сумма: {row['amount']:,.0f} KGS\n\n"
                "Запись добавлена в «Историю»."
            ),
        )
    except Exception:
        await query.message.answer(
            f"Статус обновлён, но не удалось написать пользователю {uid}. Проверьте, что он не блокировал бота."
        )

    await _hide_admin_request_message(query)
    await query.answer("Подтверждено")


@router.callback_query(F.data.startswith("dep_no:"))
async def reject_deposit(query: CallbackQuery, dispatcher: Dispatcher) -> None:
    if not _is_admin(query.from_user.id):
        await query.answer("Нет доступа", show_alert=True)
        return

    deposit_id = int(query.data.split(":", 1)[1])
    row = await get_deposit(deposit_id)
    if not row:
        await query.answer("Заявка не найдена", show_alert=True)
        return
    if row["status"] != "pending_review":
        await query.answer("Уже обработано", show_alert=True)
        return

    await set_deposit_status(deposit_id, "rejected")
    user_bot: Bot = dispatcher.workflow_data["user_bot"]
    uid = row["user_id"]
    try:
        await user_bot.send_message(
            chat_id=uid,
            text=(
                "❌ Заявка на пополнение отклонена.\n\n"
                "Если это ошибка, напишите в тех. поддержку и приложите чек."
            ),
        )
    except Exception:
        pass

    await _hide_admin_request_message(query, rejected=True)
    await query.answer("Отклонено")


async def _hide_admin_request_message(query: CallbackQuery, *, rejected: bool = False) -> None:
    """Убирает заявку из чата админа: удаление или компактная подпись без кнопок."""
    msg = query.message
    if not msg:
        return
    try:
        await msg.delete()
        return
    except TelegramBadRequest:
        pass
    suffix = "\n\n❌ Отклонено." if rejected else "\n\n✅ Подтверждено."
    cap = (msg.caption or "") + suffix
    if len(cap) > 1024:
        cap = cap[:1021] + "…"
    try:
        await msg.edit_caption(caption=cap, reply_markup=None)
    except TelegramBadRequest:
        pass
