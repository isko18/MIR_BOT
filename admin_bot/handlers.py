from __future__ import annotations

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from bookmakers import bookmaker_display
from config import settings
from database import get_deposit, set_deposit_status

router = Router(name="admin")


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


@router.message(CommandStart())
async def admin_start(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Доступ запрещён.")
        return
    await message.answer(
        "Админ-бот: здесь приходят чеки и заявки на пополнение.\n"
        "Используйте кнопки под сообщением с чеком."
    )


@router.message(Command("id"))
async def admin_id(message: Message) -> None:
    await message.answer(f"Ваш Telegram ID: `{message.from_user.id}`", parse_mode="Markdown")


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
