from __future__ import annotations

import io
from pathlib import Path

"""
Картинка «рядом» с кнопками букмекеров
======================================
В Telegram у reply-кнопок (KeyboardButton) может быть только ТЕКСТ — картинку внутрь кнопки вставить нельзя (ограничение Bot API).

Папка `photo/`: положите файлы `1xbet.jpg`, `1win.jpg`, `melbet.jpg`, `mostbet.jpg` (или .png)
— они показываются после выбора букмекера при пополнении (шаг ввода ID и далее).

Файл 1680278989.ico в папке проекта — запасной логотип для 1WIN в BOOKMAKER_LOCAL_LOGOS.

Если .ico нет на диске — баннер подгружается по BOOKMAKER_MENU_IMAGE_URL (https).
"""

_ROOT = Path(__file__).resolve().parent
# Локальные картинки букмекеров при пополнении: `photo/1xbet.jpg`, `photo/mostbet.png` и т.д.
# Имя файла = ключ букмекера в нижнем регистре (1xbet, 1win, melbet, mostbet).
_PHOTO_DIR = _ROOT / "photo"

# Общая иконка бренда (лежит рядом с этим файлом)
BRAND_ICO_PATH = _ROOT / "1680278989.ico"

_DEFAULT_LOGO = "https://magicclick.partners/assets/images/1680278989.jpg"

# Запасной URL баннера, если локального .ico нет. "" — не качать с интернета
BOOKMAKER_MENU_IMAGE_URL = _DEFAULT_LOGO

# Логотип в шагах для счетов без локального файла (1xBet, Melbet, Mostbet)
_SHARED_LOGO = _DEFAULT_LOGO

# Локальные файлы по счёту (1WIN → тот же 1680278989.ico)
BOOKMAKER_LOCAL_LOGOS: dict[str, Path] = {
    "1WIN": BRAND_ICO_PATH,
}

_LOCAL_FILE_LOGO = "__LOCAL_FILE__"

_BOOKMAKER_ICONS: dict[str, str] = {
    "1XBET": _SHARED_LOGO,
    "1WIN": _LOCAL_FILE_LOGO,
    "MELBET": _SHARED_LOGO,
    "MOSTBET": _SHARED_LOGO,
    "WINWIN": _SHARED_LOGO,
    "888STARZ": _SHARED_LOGO,
}

_BOOKMAKER_TEXT_EMOJI: dict[str, str] = {
    "1XBET": "🎰",
    "1WIN": "🎯",
    "MELBET": "⚽",
    "MOSTBET": "🏀",
    "WINWIN": "🎲",
    "888STARZ": "⭐",
}


def _is_logo_url(value: str) -> bool:
    v = value.strip().lower()
    return v.startswith("http://") or v.startswith("https://")


def _is_local_file_logo(value: str) -> bool:
    return value == _LOCAL_FILE_LOGO


def _image_file_to_png_bytes(path: Path, filename_for_tg: str) -> tuple[bytes | None, str]:
    if not path.is_file():
        return None, filename_for_tg
    try:
        from PIL import Image

        with Image.open(path) as im:
            im.load()
            rgba = im.convert("RGBA")
            buf = io.BytesIO()
            rgba.save(buf, format="PNG")
            return buf.getvalue(), filename_for_tg if filename_for_tg.endswith(".png") else f"{filename_for_tg}.png"
    except Exception:
        return None, filename_for_tg


def bookmaker_menu_banner_png_bytes() -> tuple[bytes | None, str]:
    """Баннер над клавиатурой выбора букмекера — из 1680278989.ico."""
    return _image_file_to_png_bytes(BRAND_ICO_PATH, "brand_menu.png")


def _resolve_bookmaker_photo_file(canonical_key: str) -> Path | None:
    """Файл в папке `photo/`: `{ключ}.jpg|.jpeg|.png|.webp|.ico` (ключ в нижнем регистре)."""
    key = (canonical_key or "").strip().upper()
    if not key:
        return None
    slug = key.lower().replace(" ", "_")
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".ico"):
        p = _PHOTO_DIR / f"{slug}{ext}"
        if p.is_file():
            return p
    return None


def bookmaker_local_logo_png_bytes(canonical_key: str) -> tuple[bytes | None, str]:
    """Логотип с диска: сначала папка `photo/`, затем BOOKMAKER_LOCAL_LOGOS (.ico и т.д.)."""
    key = canonical_key.strip().upper()
    photo_file = _resolve_bookmaker_photo_file(key)
    if photo_file is not None:
        data, fname = _image_file_to_png_bytes(photo_file, f"{key.lower()}_logo.png")
        if data:
            return data, fname
    p = BOOKMAKER_LOCAL_LOGOS.get(key)
    if p is None:
        return None, "logo.png"
    data, _ = _image_file_to_png_bytes(p, f"{key.lower()}_logo.png")
    return data, f"{key.lower()}_logo.png"


def bookmaker_keyboard_banner_url() -> str | None:
    raw = (BOOKMAKER_MENU_IMAGE_URL or "").strip()
    if raw and _is_logo_url(raw):
        return raw
    return None


def bookmaker_logo_url(canonical_name: str) -> str | None:
    key = (canonical_name or "").strip().upper()
    raw = _BOOKMAKER_ICONS.get(key)
    if raw and _is_logo_url(raw):
        return raw.strip()
    return None


def bookmaker_display(canonical_name: str) -> str:
    key = (canonical_name or "").strip().upper()
    if not key:
        return "🎲 —"
    icon_val = _BOOKMAKER_ICONS.get(key, "🎲")
    if _is_logo_url(icon_val) or _is_local_file_logo(icon_val):
        emoji = _BOOKMAKER_TEXT_EMOJI.get(key, "🎲")
        return f"{emoji} {key}"
    return f"{icon_val} {key}"
