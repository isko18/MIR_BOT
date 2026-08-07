"""Единая работа с суммами: разбор пользовательского ввода и форматирование.

Раньше сумма разбиралась через float(), из-за чего «nan» проходил проверку
диапазона (любое сравнение с NaN — False), а показывалась она то с копейками
(QR: «100.55 сом»), то округлённой (админ: «101 KGS»). Здесь один разбор и
одно форматирование для всех мест.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# Только цифры с необязательной дробной частью: «1000», «1 000,50», «1000.5».
# Отсекает nan / inf / 1e5 / -5 и прочий мусор.
_AMOUNT_RE = re.compile(r"^\d{1,9}(?:[.,]\d{1,2})?$")

_CENT = Decimal("0.01")


class AmountError(ValueError):
    """Ввод не является корректной суммой."""


def parse_amount(raw: str) -> Decimal:
    """'1 000,50' → Decimal('1000.50'). Бросает AmountError на любом мусоре."""
    s = (raw or "").strip().replace(" ", "").replace(" ", "")
    if not _AMOUNT_RE.match(s):
        raise AmountError("не похоже на сумму")
    try:
        value = Decimal(s.replace(",", "."))
    except InvalidOperation as exc:  # pragma: no cover — регексп уже отсёк
        raise AmountError("не похоже на сумму") from exc
    return value.quantize(_CENT)


def format_amount(amount: float | Decimal | str) -> str:
    """Сумма для показа: '1 000.50', '35' (целые — без лишних нулей)."""
    value = Decimal(str(amount)).quantize(_CENT)
    whole, _, frac = f"{value:f}".partition(".")
    grouped = f"{int(whole):,}".replace(",", " ")
    return grouped if frac == "00" else f"{grouped}.{frac}"


def format_kgs(amount: float | Decimal | str) -> str:
    return f"{format_amount(amount)} KGS"
