"""Free, keyless translation helper for KVP GAME.

Uses Google's public web-translation endpoint (unofficial; no Cloud API key).
Keep the public function names stable so bot.py does not need call-site changes.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from typing import Optional

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

log = logging.getLogger(__name__)

_CACHE: dict[tuple[str, str, str], str] = {}
_CACHE_LOCK = asyncio.Lock()
_CLIENT: Optional[httpx.AsyncClient] = None
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
            timeout=httpx.Timeout(15.0, connect=5.0),
            headers={"User-Agent": "Mozilla/5.0"},
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

    try:
        client = await _get_client()
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
    except Exception as exc:
        log.warning("Free translation failed (%s): %s", target_lang, exc)
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
