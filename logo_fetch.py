from __future__ import annotations

import asyncio
from collections import OrderedDict

import aiohttp

_MAX_BYTES = 5 * 1024 * 1024
_CACHE_MAX = 96
_cache_lock = asyncio.Lock()
_url_cache: OrderedDict[str, bytes] = OrderedDict()

_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()


async def _get_http_session() -> aiohttp.ClientSession:
    """Один ClientSession на процесс — меньше TCP-handshake при массовых запросах к CDN."""
    global _session
    async with _session_lock:
        if _session is None or _session.closed:
            connector = aiohttp.TCPConnector(
                limit=80,
                limit_per_host=16,
                ttl_dns_cache=600,
                enable_cleanup_closed=True,
            )
            _session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=25, connect=10),
                connector=connector,
            )
        return _session


async def close_http() -> None:
    global _session
    async with _session_lock:
        if _session is not None and not _session.closed:
            await _session.close()
        _session = None


async def fetch_image_bytes(url: str) -> bytes | None:
    """Скачивает изображение по HTTPS. Кэш URL → bytes (ограничен по размеру)."""
    async with _cache_lock:
        if url in _url_cache:
            _url_cache.move_to_end(url)
            return _url_cache[url]
    data = await _fetch_image_bytes_impl(url)
    if data:
        async with _cache_lock:
            _url_cache[url] = data
            _url_cache.move_to_end(url)
            while len(_url_cache) > _CACHE_MAX:
                _url_cache.popitem(last=False)
    return data


async def _fetch_image_bytes_impl(url: str) -> bytes | None:
    try:
        session = await _get_http_session()
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status != 200:
                return None
            raw = await resp.read()
            if not raw or len(raw) > _MAX_BYTES:
                return None
            return raw
    except Exception:
        return None


async def get_bookmaker_logo_bytes(canonical_name: str) -> tuple[bytes | None, str]:
    """Локальный файл из bookmakers.py или скачивание по URL из _BOOKMAKER_ICONS."""
    from bookmakers import bookmaker_local_logo_png_bytes, bookmaker_logo_url

    key = (canonical_name or "").strip().upper()
    # Чтение с диска + PIL блокируют event loop — выносим в поток (результат кэшируется внутри).
    data, fname = await asyncio.to_thread(bookmaker_local_logo_png_bytes, key)
    if data:
        return data, fname
    url = bookmaker_logo_url(canonical_name)
    if url:
        b = await fetch_image_bytes(url)
        if b:
            return b, guess_logo_filename(url)
    return None, "logo.png"


def guess_logo_filename(url: str) -> str:
    u = url.lower().split("?", 1)[0]
    if u.endswith(".png"):
        return "logo.png"
    if u.endswith(".webp"):
        return "logo.webp"
    return "logo.jpg"


_welcome_logo: tuple[bytes | None, str] | None = None


async def get_welcome_logo_bytes() -> tuple[bytes | None, str]:
    """Лого при /start: сначала локальный файл, затем URL из настроек.

    Файл читается один раз за процесс: он статичный, а /start — самый частый
    хендлер, и синхронное чтение мегабайтного PNG тормозило весь event loop.
    """
    global _welcome_logo
    if _welcome_logo is not None:
        return _welcome_logo

    from config import settings

    path = settings.brand_logo_path
    if path is not None and path.is_file():
        data = await asyncio.to_thread(path.read_bytes)
        _welcome_logo = (data, path.name)
        return _welcome_logo

    url = settings.brand_logo_url
    if url:
        data = await fetch_image_bytes(url)
        if data:
            _welcome_logo = (data, guess_logo_filename(url))
            return _welcome_logo
        return None, "logo.png"  # сеть могла моргнуть — попробуем ещё раз позже

    _welcome_logo = (None, "logo.png")
    return _welcome_logo
