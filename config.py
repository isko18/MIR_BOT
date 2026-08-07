from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / ".env")

# Число с опциональным % — в баннер подставляется как «15%»; иначе строка как есть (напр. «до 5%»).
_WELCOME_PCT_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*%?\s*$")


def _format_welcome_pct(raw: str | None) -> str:
    if raw is None:
        return "0%"
    s = raw.strip()
    if not s:
        return "0%"
    m = _WELCOME_PCT_RE.match(s)
    if not m:
        return s
    v = float(m.group(1).replace(",", "."))
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v))}%"
    txt = f"{v:.4f}".rstrip("0").rstrip(".")
    return f"{txt}%"


@dataclass(frozen=True)
class Settings:
    user_bot_token: str
    admin_bot_token: str
    admin_ids: frozenset[int]
    payment_qr_payload: str
    payment_qr_path: Path | None
    support_username: str | None
    welcome_brand: str
    support_bot_username: str | None
    public_chat_username: str | None
    required_channel_username: str | None
    required_channel_id: str | None
    welcome_deposit_pct: str
    welcome_withdraw_pct: str
    welcome_security_line: str
    brand_logo_path: Path | None
    brand_logo_url: str | None
    # Масштаб: Redis для FSM (несколько процессов / переживает рестарт). Пусто — MemoryStorage.
    redis_url: str | None
    # TTL ключей FSM в Redis (секунды), ~14 суток
    fsm_redis_ttl_sec: int
    # Лимиты HTTP к api.telegram.org (aiohttp)
    telegram_http_limit_user: int
    telegram_http_limit_admin: int

    min_amount_kgs: float = 35.0
    max_amount_kgs: float = 500_000.0


def _parse_admin_ids(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            raise RuntimeError(
                f"ADMIN_IDS: «{part}» не является числовым Telegram ID"
            ) from None
    if not ids:
        raise RuntimeError("ADMIN_IDS не содержит ни одного корректного ID")
    return frozenset(ids)


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    """int из .env с понятной ошибкой вместо голого ValueError при опечатке."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(f"{name} должно быть целым числом, а не «{raw}»") from None
    return max(minimum, value)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip().replace(",", ".")
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise RuntimeError(f"{name} должно быть числом, а не «{raw}»") from None
    if value <= 0:
        raise RuntimeError(f"{name} должно быть больше нуля")
    return value


# Лого приветствия по умолчанию — первый существующий файл из списка.
# BRAND_LOGO_PATH в .env перебивает выбор.
_BRAND_LOGO_CANDIDATES = ("лого2.png", "лого.png")


def _resolve_brand_logo_path() -> Path | None:
    raw = os.getenv("BRAND_LOGO_PATH", "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = _ROOT / path
        return path if path.is_file() else None
    for name in _BRAND_LOGO_CANDIDATES:
        candidate = _ROOT / name
        if candidate.is_file():
            return candidate
    return None


def _resolve_payment_qr_path() -> Path | None:
    raw = os.getenv("PAYMENT_QR_IMAGE", "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = _ROOT / path
        return path if path.is_file() else None
    default = _ROOT / "Илиязбек Г. Ш..png"
    return default if default.is_file() else None


def load_settings() -> Settings:
    user = os.getenv("USER_BOT_TOKEN", "").strip()
    admin = os.getenv("ADMIN_BOT_TOKEN", "").strip()
    if not user or not admin:
        raise RuntimeError("Задайте USER_BOT_TOKEN и ADMIN_BOT_TOKEN в .env")

    admins_raw = os.getenv("ADMIN_IDS", "").strip()
    if not admins_raw:
        raise RuntimeError("Задайте ADMIN_IDS в .env (через запятую)")

    payload = os.getenv("PAYMENT_QR_PAYLOAD", "").strip() or "Оплата — уточните реквизиты у поддержки"
    support = os.getenv("SUPPORT_USERNAME", "").strip() or None

    brand = os.getenv("WELCOME_BRAND", "LUX ON!").strip() or "LUX ON!"
    support_bot = os.getenv("SUPPORT_BOT_USERNAME", "").strip() or None
    pub_chat = os.getenv("PUBLIC_CHAT_USERNAME", "").strip() or None
    channel_raw = os.getenv("REQUIRED_CHANNEL_USERNAME", "the100som").strip()
    required_channel = channel_raw.lstrip("@") if channel_raw else None
    required_channel_id = os.getenv("REQUIRED_CHANNEL_ID", "").strip() or None
    dep_pct = _format_welcome_pct(os.getenv("WELCOME_DEPOSIT_PCT"))
    wdr_pct = _format_welcome_pct(os.getenv("WELCOME_WITHDRAW_PCT"))
    security = os.getenv("WELCOME_SECURITY_LINE", "").strip() or (
        "🔒 Финансовый контроль обеспечен личным отделом безопасности"
    )

    # Локальный QR для оплаты (по умолчанию «Илиязбек Г. Ш..png» в корне проекта)
    payment_qr_path = _resolve_payment_qr_path()

    # Локальный файл лого в приветствии (по умолчанию «лого.png» в корне проекта)
    brand_logo_path = _resolve_brand_logo_path()

    # Запасной URL, если локального файла нет; пустая строка в .env отключает
    _logo_raw = os.environ.get("BRAND_LOGO_URL")
    if _logo_raw is not None:
        brand_logo = _logo_raw.strip() or None
    elif brand_logo_path is None:
        brand_logo = "https://magicclick.partners/assets/images/1680278989.jpg"
    else:
        brand_logo = None

    redis_url = os.getenv("REDIS_URL", "").strip() or None
    fsm_redis_ttl_sec = _env_int("FSM_REDIS_TTL_SEC", 1_209_600, minimum=60)  # 14 дней
    tg_lim_u = _env_int("TELEGRAM_HTTP_LIMIT_USER", 200, minimum=32)
    tg_lim_a = _env_int("TELEGRAM_HTTP_LIMIT_ADMIN", 80, minimum=16)

    min_amount = _env_float("MIN_AMOUNT_KGS", 35.0)
    max_amount = _env_float("MAX_AMOUNT_KGS", 500_000.0)
    if min_amount > max_amount:
        raise RuntimeError("MIN_AMOUNT_KGS больше MAX_AMOUNT_KGS")

    return Settings(
        user_bot_token=user,
        admin_bot_token=admin,
        admin_ids=_parse_admin_ids(admins_raw),
        payment_qr_payload=payload,
        payment_qr_path=payment_qr_path,
        support_username=support,
        welcome_brand=brand,
        support_bot_username=support_bot,
        public_chat_username=pub_chat,
        required_channel_username=required_channel,
        required_channel_id=required_channel_id,
        welcome_deposit_pct=dep_pct,
        welcome_withdraw_pct=wdr_pct,
        welcome_security_line=security,
        brand_logo_path=brand_logo_path,
        brand_logo_url=brand_logo,
        redis_url=redis_url,
        fsm_redis_ttl_sec=fsm_redis_ttl_sec,
        telegram_http_limit_user=tg_lim_u,
        telegram_http_limit_admin=tg_lim_a,
        min_amount_kgs=min_amount,
        max_amount_kgs=max_amount,
    )


settings = load_settings()
