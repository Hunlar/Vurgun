"""Centralized Google Cloud Translation helper for KVP GAME."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

log = logging.getLogger(__name__)
_API_KEY = os.environ.get("GOOGLE_TRANSLATE_API_KEY", "").strip()
_CACHE: dict[tuple[str, str, str], str] = {}
_CACHE_LOCK = asyncio.Lock()
_CLIENT: Optional[httpx.AsyncClient] = None

LANGUAGE_ALIASES = {
    "az": "az", "az-az": "az", "fa": "fa", "fa-ir": "fa",
    "pt-br": "pt", "zh": "zh-CN", "zh-cn": "zh-CN", "zh-hans": "zh-CN",
    "zh-tw": "zh-TW", "he": "iw", "fil": "tl", "nb": "no",
}

def normalize_language(code: str) -> str:
    code = (code or "tr").strip().replace("_", "-").lower()
    if code in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[code]
    return code.split("-", 1)[0] if code else "tr"

async def google_translate(text: str, target_lang: str, source_lang: str = "tr") -> str:
    if not text or not target_lang or target_lang == source_lang or not _API_KEY:
        return text
    key = (source_lang, target_lang, text)
    async with _CACHE_LOCK:
        cached = _CACHE.get(key)
    if cached is not None:
        return cached
    global _CLIENT
    try:
        if _CLIENT is None or _CLIENT.is_closed:
            _CLIENT = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
        response = await _CLIENT.post(
            "https://translation.googleapis.com/language/translate/v2",
            params={"key": _API_KEY},
            json={"q": text, "source": source_lang, "target": target_lang, "format": "html"},
        )
        response.raise_for_status()
        translated = response.json()["data"]["translations"][0]["translatedText"]
        async with _CACHE_LOCK:
            if len(_CACHE) > 5000:
                _CACHE.clear()
            _CACHE[key] = translated
        return translated
    except Exception as exc:
        log.warning("Google Translate failed (%s): %s", target_lang, exc)
        return text

async def translate_markup(markup, target_lang: str):
    if markup is None or not _API_KEY or target_lang == "tr":
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
