from __future__ import annotations

import io
from pathlib import Path

import qrcode

from config import settings


def make_qr_png_bytes(data: str) -> bytes:
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def get_payment_qr_bytes() -> tuple[bytes, str]:
    """QR для оплаты: локальный файл из настроек или генерация из PAYMENT_QR_PAYLOAD."""
    path: Path | None = settings.payment_qr_path
    if path is not None and path.is_file():
        return path.read_bytes(), path.name
    payload = settings.payment_qr_payload
    return make_qr_png_bytes(payload), "payment.png"
