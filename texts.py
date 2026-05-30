from __future__ import annotations

import re

from config import settings

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
    name = (first_name or "").strip() or "друг"
    brand = settings.welcome_brand
    dep = _condition_label(settings.welcome_deposit_pct)
    wdr = _condition_label(settings.welcome_withdraw_pct)

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
