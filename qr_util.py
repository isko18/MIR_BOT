from __future__ import annotations

import asyncio
import io
from pathlib import Path

import qrcode

from config import settings
from database import get_payment_qr, get_payment_qr_version

_DEFAULT_VERSION = "__default__"

# (версия, байты, имя файла). Версия — updated_at загруженного админом QR либо
# _DEFAULT_VERSION. Сравнение версий дешевле, чем тянуть BLOB на каждую заявку.
_cached: tuple[str, bytes, str] | None = None
_cache_lock = asyncio.Lock()


def make_qr_png_bytes(data: str) -> bytes:
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _load_default_qr() -> tuple[bytes, str]:
    path: Path | None = settings.payment_qr_path
    if path is not None and path.is_file():
        return path.read_bytes(), path.name
    return make_qr_png_bytes(settings.payment_qr_payload), "payment.png"


async def get_payment_qr_bytes() -> tuple[bytes, str]:
    """QR для оплаты: загруженный админом либо дефолтный (файл/PAYMENT_QR_PAYLOAD)."""
    global _cached
    version = await get_payment_qr_version() or _DEFAULT_VERSION
    cached = _cached
    if cached is not None and cached[0] == version:
        return cached[1], cached[2]

    async with _cache_lock:
        cached = _cached
        if cached is not None and cached[0] == version:
            return cached[1], cached[2]
        if version == _DEFAULT_VERSION:
            data, name = await asyncio.to_thread(_load_default_qr)
        else:
            row = await get_payment_qr()
            if row is None:  # QR сбросили между двумя запросами — берём дефолтный
                version = _DEFAULT_VERSION
                data, name = await asyncio.to_thread(_load_default_qr)
            else:
                data, name = row["data"], row["filename"]
        _cached = (version, data, name)
        return data, name


def invalidate_payment_qr_cache() -> None:
    global _cached
    _cached = None
