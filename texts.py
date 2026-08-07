from __future__ import annotations

import re
from html import escape

from config import settings
from money import format_amount

_PCT_ONLY = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*%?\s*$")


def _condition_label(raw: str) -> str:
    """Человекочитаемое условие: 0 → «без комиссии», 15 → «комиссия 15%», текст — как есть."""
    s = (raw or "").strip()
    if not s or s in {"0", "0%"}:
        return "без комиссии"
    m = _PCT_ONLY.match(s)
    if not m:
        return s
    v = float(m.group(1).replace(",", "."))
    if abs(v) < 1e-9:
        return "без комиссии"
    if abs(v - round(v)) < 1e-9:
        return f"комиссия {int(round(v))}%"
    txt = f"{v:.4f}".rstrip("0").rstrip(".")
    return f"комиссия {txt}%"


def welcome_banner(first_name: str | None) -> str:
    """Текст после /start, «Отмена» и т.п. — условия из .env (WELCOME_*_PCT)."""
    # Баннер уходит с parse_mode=HTML: имя из Telegram может содержать «<» или «&»,
    # и без экранирования Telegram отклонял всё сообщение — /start молча не работал.
    name = escape((first_name or "").strip()) or "друг"
    brand = escape(settings.welcome_brand)
    dep = escape(_condition_label(settings.welcome_deposit_pct))
    wdr = escape(_condition_label(settings.welcome_withdraw_pct))

    lines = [
        f"Привет, {name}! 👋",
        f"Добро пожаловать в <b>{brand}</b>",
        "",
        "Здесь можно пополнить счёт букмекера или оформить вывод средств.",
        "",
        f"📥 <b>Пополнение</b> — {dep}",
        "   1. Нажмите «💳 Пополнить»",
        "   2. Оплатите по QR-коду",
        "   3. Отправьте фото чека — зачислим после проверки",
        "",
        f"📤 <b>Вывод</b> — {wdr}",
        "   Напишите в «🛟 Тех. поддержка»: букмекер, ID счёта и сумма",
        "",
        "🕒 Принимаем заявки <b>24/7</b>",
        "",
    ]
    support_bot = settings.support_bot_username or settings.support_username
    if support_bot:
        u = support_bot.lstrip("@")
        lines.append(f"👨‍💻 Поддержка: @{u}")
    if settings.public_chat_username:
        c = settings.public_chat_username.lstrip("@")
        lines.append(f"💬 Чат: @{c}")
    lines.append("")
    lines.append(settings.welcome_security_line)
    return "\n".join(lines)


RECEIPT_TIMEOUT_MESSAGE = """⏰ Пополнение отменено, время оплаты прошло

❌ Не переводите по старым реквизитам

Начните заново, нажав на Пополнить"""


# Лимиты берём из настроек, иначе текст расходится с реальной проверкой в хендлере.
INSTRUCTION = f"""📖 Инструкция

1. Нажмите «💳 Пополнить» и выберите букмекера.
2. Введите ID вашего игрового счёта.
3. Укажите сумму (от {format_amount(settings.min_amount_kgs)} до \
{format_amount(settings.max_amount_kgs)} KGS).
4. Оплатите по QR-коду — сумму нужно перевести точь-в-точь, до копеек.
5. Отправьте фото чека в этот чат — на оплату и чек даётся 5 минут.
6. После проверки администратором счёт будет пополнен — придёт уведомление.

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
