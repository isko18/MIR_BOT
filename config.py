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
    support_username: str | None
    welcome_brand: str
    support_bot_username: str | None
    public_chat_username: str | None
    welcome_deposit_pct: str
    welcome_withdraw_pct: str
    welcome_security_line: str
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
        ids.add(int(part))
    return frozenset(ids)


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
    dep_pct = _format_welcome_pct(os.getenv("WELCOME_DEPOSIT_PCT"))
    wdr_pct = _format_welcome_pct(os.getenv("WELCOME_WITHDRAW_PCT"))
    security = os.getenv("WELCOME_SECURITY_LINE", "").strip() or (
        "🔒 Финансовый контроль обеспечен личным отделом безопасности"
    )

    # Картинка-«иконка» в приветствии; пустая строка в .env отключает
    _logo_raw = os.environ.get("BRAND_LOGO_URL")
    if _logo_raw is not None:
        brand_logo = _logo_raw.strip() or None
    else:
        brand_logo = "https://magicclick.partners/assets/images/1680278989.jpg"

    redis_url = os.getenv("REDIS_URL", "").strip() or None
    fsm_ttl_raw = os.getenv("FSM_REDIS_TTL_SEC", "").strip()
    fsm_redis_ttl_sec = int(fsm_ttl_raw) if fsm_ttl_raw else 1_209_600  # 14 дней
    tg_lim_u = int(os.getenv("TELEGRAM_HTTP_LIMIT_USER", "200").strip() or "200")
    tg_lim_a = int(os.getenv("TELEGRAM_HTTP_LIMIT_ADMIN", "80").strip() or "80")

    return Settings(
        user_bot_token=user,
        admin_bot_token=admin,
        admin_ids=_parse_admin_ids(admins_raw),
        payment_qr_payload=payload,
        support_username=support,
        welcome_brand=brand,
        support_bot_username=support_bot,
        public_chat_username=pub_chat,
        welcome_deposit_pct=dep_pct,
        welcome_withdraw_pct=wdr_pct,
        welcome_security_line=security,
        brand_logo_url=brand_logo,
        redis_url=redis_url,
        fsm_redis_ttl_sec=fsm_redis_ttl_sec,
        telegram_http_limit_user=max(32, tg_lim_u),
        telegram_http_limit_admin=max(16, tg_lim_a),
    )


settings = load_settings()
