from __future__ import annotations

from config import settings


def welcome_banner(first_name: str | None) -> str:
    """Текст после отмены, /start и т.п. — настраивается в .env."""
    name = (first_name or "").strip() or "друг"
    brand = settings.welcome_brand

    dep = settings.welcome_deposit_pct
    wdr = settings.welcome_withdraw_pct
    lines = [
        f"Привет, {name} | {brand}",
        "",
        f"Пополнение {dep} | Вывод {wdr}",
        "",
        f"📥 Пополнение — {dep}",
        f"📤 Вывод — {wdr}",
        "🕒 Работаем 24/7",
        "",
    ]
    support_bot = settings.support_bot_username or settings.support_username
    if support_bot:
        u = support_bot.lstrip("@")
        lines.append(f"👨‍💻 Поддержка: @{u}")
    if settings.public_chat_username:
        c = settings.public_chat_username.lstrip("@")
        lines.append(f"💬 Чат для всех: @{c}")
    lines.append("")
    lines.append(settings.welcome_security_line)
    return "\n".join(lines)


RECEIPT_TIMEOUT_MESSAGE = """⏰ Пополнение отменено, время оплаты прошло

❌ Не переводите по старым реквизитам

Начните заново, нажав на Пополнить"""


INSTRUCTION = """📖 Инструкция

1. Нажмите «💳 Пополнить» и выберите букмекера.
2. Введите ID вашего игрового счёта.
3. Укажите сумму (от 35 до 500 000 KGS).
4. Оплатите по QR-коду и отправьте чек фото в этот чат.
5. После проверки администратором счёт будет пополнен — придёт уведомление.

По вопросам вывода и спорных ситуаций — раздел «🛟 Тех. поддержка»."""


def support_text(support_username: str | None) -> str:
    if support_username:
        return (
            "🛟 Тех. поддержка\n\n"
            f"Напишите нам: @{support_username}\n"
            "Опишите проблему и приложите скриншоты при необходимости."
        )
    return (
        "🛟 Тех. поддержка\n\n"
        "Укажите SUPPORT_USERNAME в настройках бота или свяжитесь с оператором по контактам из инструкции."
    )


WITHDRAW = """💸 Вывод средств

Вывод оформляется через оператора. Нажмите «🛟 Тех. поддержка» и напишите:
— букмекер;
— ID счёта;
— желаемую сумму."""
