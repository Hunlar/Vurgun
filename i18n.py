"""Free, keyless translation helper for KVP GAME.

Uses Google's public web-translation endpoint (unofficial; no Cloud API key).
Requests are serialized and throttled to reduce HTTP 429 rate-limit responses.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

log = logging.getLogger(__name__)

_CACHE: dict[tuple[str, str, str], str] = {}
_CACHE_LOCK = asyncio.Lock()
_REQUEST_LOCK = asyncio.Lock()
_CLIENT: Optional[httpx.AsyncClient] = None
_LAST_REQUEST_AT = 0.0
_COOLDOWN_UNTIL = 0.0
_MIN_REQUEST_INTERVAL = 1.1
_BASE_LANGS = {"tr", "en", "ru", "uz", "ar", "de"}

LANGUAGE_ALIASES = {
    "az": "az", "az-az": "az", "fa": "fa", "fa-ir": "fa",
    "pt-br": "pt", "zh": "zh-CN", "zh-cn": "zh-CN", "zh-hans": "zh-CN",
    "zh-tw": "zh-TW", "he": "he", "fil": "fil", "nb": "no",
}

def normalize_language(code: str) -> str:
    code = (code or "tr").strip().replace("_", "-").lower()
    if code in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[code]
    return code.split("-", 1)[0] if code else "tr"

async def _get_client() -> httpx.AsyncClient:
    global _CLIENT
    if _CLIENT is None or _CLIENT.is_closed:
        _CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0),
            headers={"User-Agent": "Mozilla/5.0 (compatible; KVPGameBot/1.0)"},
            follow_redirects=True,
        )
    return _CLIENT

async def google_translate(text: str, target_lang: str, source_lang: str = "tr", force: bool = False) -> str:
    """Translate text without a paid API key; return original text on failure."""
    if not text or not target_lang or target_lang == source_lang:
        return text
    if target_lang in _BASE_LANGS and not force:
        return text

    key = (source_lang, target_lang, text)
    async with _CACHE_LOCK:
        cached = _CACHE.get(key)
    if cached is not None:
        return cached

    global _LAST_REQUEST_AT, _COOLDOWN_UNTIL
    async with _REQUEST_LOCK:
        now = time.monotonic()
        if now < _COOLDOWN_UNTIL:
            return text

        wait_for = _MIN_REQUEST_INTERVAL - (now - _LAST_REQUEST_AT)
        if wait_for > 0:
            await asyncio.sleep(wait_for)

        client = await _get_client()
        for attempt in range(3):
            try:
                response = await client.get(
                    "https://translate.googleapis.com/translate_a/single",
                    params={
                        "client": "gtx",
                        "sl": source_lang,
                        "tl": target_lang,
                        "dt": "t",
                        "format": "html",
                        "q": text,
                    },
                )
                _LAST_REQUEST_AT = time.monotonic()

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        delay = float(retry_after) if retry_after else (3.0 * (2 ** attempt))
                    except ValueError:
                        delay = 3.0 * (2 ** attempt)
                    if attempt < 2:
                        log.warning("Translation rate limited; retrying in %.1fs", delay)
                        await asyncio.sleep(min(delay, 20.0))
                        continue
                    _COOLDOWN_UNTIL = time.monotonic() + 60.0
                    log.warning("Translation endpoint rate-limited; pausing requests for 60 seconds")
                    return text

                response.raise_for_status()
                payload = response.json()
                translated = "".join(
                    part[0] for part in (payload[0] or [])
                    if isinstance(part, list) and part and isinstance(part[0], str)
                )
                if not translated:
                    return text

                async with _CACHE_LOCK:
                    if len(_CACHE) >= 5000:
                        _CACHE.clear()
                    _CACHE[key] = translated
                return translated

            except httpx.HTTPStatusError as exc:
                log.warning("Translation HTTP error (%s): %s", target_lang, exc.response.status_code)
                return text
            except Exception as exc:
                log.warning("Free translation failed (%s): %s", target_lang, exc)
                return text

    return text

async def translate_markup(markup, target_lang: str):
    if markup is None or target_lang in _BASE_LANGS:
        return markup
    rows = []
    for row in markup.inline_keyboard:
        translated_row = []
        for button in row:
            label = await google_translate(button.text, target_lang) if button.text else button.text
            translated_row.append(InlineKeyboardButton(
                text=label, url=button.url, callback_data=button.callback_data,
                web_app=button.web_app, login_url=button.login_url,
                switch_inline_query=button.switch_inline_query,
                switch_inline_query_current_chat=button.switch_inline_query_current_chat,
                callback_game=button.callback_game, pay=button.pay,
            ))
        rows.append(translated_row)
    return InlineKeyboardMarkup(rows)

async def close_translation_client() -> None:
    global _CLIENT
    if _CLIENT is not None and not _CLIENT.is_closed:
        await _CLIENT.aclose()
    _CLIENT = None
